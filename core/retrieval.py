from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from core.db import ROOT, fetch_report_files


INDEX_ROOT = ROOT / 'data' / 'rag_index'
NO_EVIDENCE = '当前未检索到足以支持该问题的可核验文档证据。'
METRIC_ALIASES = {
    'revenue': ['营业收入', '营收', '收入'],
    'net_profit': ['净利润', '归母净利润', '归属于上市公司股东的净利润'],
    'operating_cashflow': ['经营现金流', '经营活动产生的现金流量净额'],
    'roe': ['ROE', '净资产收益率'],
    'debt_ratio': ['资产负债率', '负债率'],
    'gross_margin': ['毛利率', '销售毛利率'],
    'eps': ['每股收益', 'EPS'],
}


def is_rag_eligible_report(report: dict[str, Any]) -> bool:
    suffix = Path(str(report.get('file_path') or report.get('file_name') or '')).suffix.lower()
    file_type = str(report.get('file_type') or '').strip().lower()
    return suffix == '.pdf' or file_type in {'pdf', 'application/pdf'}


def build_retrieval_query(question: str, resolved_context: dict[str, Any]) -> str:
    parts = [question]
    parts.extend(resolved_context.get('companies') or [])
    parts.extend(str(year) for year in resolved_context.get('years') or [])
    for metric in resolved_context.get('metrics') or []:
        parts.extend(METRIC_ALIASES.get(metric, [metric]))
    return ' '.join(part for part in parts if part)


def extract_pdf_pages(file_path: str | Path) -> list[dict[str, Any]]:
    path = Path(file_path)
    if not path.is_file():
        return []
    try:
        import fitz

        with fitz.open(path) as document:
            return [
                {'page': number + 1, 'text': page.get_text('text').strip()}
                for number, page in enumerate(document)
                if page.get_text('text').strip()
            ]
    except Exception:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return [
                {'page': number + 1, 'text': (page.extract_text() or '').strip()}
                for number, page in enumerate(reader.pages)
                if (page.extract_text() or '').strip()
            ]
        except Exception:
            return []


def chunk_pages(
    pages: list[dict[str, Any]], metadata: dict[str, Any], chunk_size: int = 800, overlap: int = 120,
) -> list[dict[str, Any]]:
    chunks = []
    step = max(1, chunk_size - overlap)
    for page in pages:
        text = re.sub(r'\s+', ' ', page.get('text', '')).strip()
        for offset in range(0, len(text), step):
            content = text[offset:offset + chunk_size].strip()
            if not content:
                continue
            chunks.append({
                'document_id': metadata.get('id'),
                'company': metadata.get('company_name') or metadata.get('company'),
                'report_year': metadata.get('report_year'),
                'file_name': metadata.get('file_name'),
                'file_path': str(metadata.get('file_path') or ''),
                'page': int(page['page']),
                'chunk_id': f'{metadata.get("id", "document")}-p{page["page"]}-{offset}',
                'text': content,
            })
            if offset + chunk_size >= len(text):
                break
    return chunks


def _local_path(file_path: str | Path) -> Path:
    path = Path(str(file_path))
    return path if path.is_absolute() else ROOT / path


def _index_path(report: dict[str, Any], index_root: Path) -> Path:
    key = report.get('id') or re.sub(r'[^A-Za-z0-9_-]+', '_', report.get('file_name', 'document'))
    return index_root / f'report_{key}.json'


def build_document_index(report: dict[str, Any], index_root: Path = INDEX_ROOT) -> dict[str, Any]:
    if not is_rag_eligible_report(report):
        return {'status': 'not_applicable', 'page_count': 0, 'chunks': []}
    path = _local_path(report.get('file_path', ''))
    if not path.is_file():
        return {'status': 'missing', 'page_count': 0, 'chunks': [], 'error': '文件不存在。'}
    stat = path.stat()
    index_path = _index_path(report, index_root)
    if index_path.is_file():
        try:
            cached = json.loads(index_path.read_text(encoding='utf-8'))
            if cached.get('file_size') == stat.st_size and cached.get('mtime_ns') == stat.st_mtime_ns:
                return cached
        except Exception:
            pass
    pages = extract_pdf_pages(path)
    chunks = chunk_pages(pages, {**report, 'file_path': str(path)})
    payload = {
        'status': 'ready' if chunks else 'no_text',
        'report_file_id': report.get('id'), 'file_path': str(path),
        'file_size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
        'page_count': max((page['page'] for page in pages), default=0), 'chunks': chunks,
    }
    try:
        index_root.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass
    return payload


def get_index_status(report: dict[str, Any], index_root: Path = INDEX_ROOT) -> str:
    if not is_rag_eligible_report(report):
        return '不适用'
    path = _local_path(report.get('file_path', ''))
    if not path.is_file():
        return '文件缺失'
    index_path = _index_path(report, index_root)
    if not index_path.is_file():
        return '待首次查询建立'
    try:
        cached = json.loads(index_path.read_text(encoding='utf-8'))
        stat = path.stat()
        if cached.get('file_size') != stat.st_size or cached.get('mtime_ns') != stat.st_mtime_ns:
            return '待刷新'
        return '无可检索文本' if cached.get('status') == 'no_text' else '已建立'
    except Exception:
        return '待刷新'


def _terms(text: str) -> list[str]:
    normalized = re.sub(r'\s+', '', text.lower())
    chinese = ''.join(re.findall(r'[\u4e00-\u9fff]', normalized))
    grams = [chinese[i:i + size] for size in (2, 3) for i in range(max(0, len(chinese) - size + 1))]
    words = re.findall(r'[a-z]+|\d+(?:\.\d+)?', normalized)
    finance = [term for term in ['研发', '海外', '风险', '利润', '营收', '毛利率', '现金流', '产品结构', '政策', '汇率'] if term in normalized]
    return grams + words + finance


def rank_chunks(query: str, chunks: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    query_counts = Counter(_terms(query))
    if not query_counts:
        return []
    document_frequency = Counter()
    chunk_terms = []
    for chunk in chunks:
        terms = Counter(_terms(chunk['text']))
        chunk_terms.append(terms)
        document_frequency.update(terms.keys())
    scored = []
    total = max(1, len(chunks))
    for chunk, terms in zip(chunks, chunk_terms):
        score = sum(
            min(count, terms.get(term, 0)) * (math.log((total + 1) / (document_frequency[term] + 0.5)) + 1)
            for term, count in query_counts.items()
        )
        if score > 0:
            scored.append({**chunk, 'score': round(score, 4)})
    scored.sort(key=lambda item: item['score'], reverse=True)
    results, seen_pages = [], set()
    for item in scored:
        page_key = (item['document_id'], item['page'])
        if page_key in seen_pages:
            continue
        results.append(item)
        seen_pages.add(page_key)
        if len(results) >= top_k:
            break
    return results


def _extract_snippet(query: str, text: str, limit: int = 220) -> str:
    sentences = [part.strip() for part in re.split(r'(?<=[。！？；])|\n+', text) if part.strip()]
    ranked = rank_chunks(
        query,
        [{'text': sentence, 'document_id': 0, 'page': 0} for sentence in sentences],
        top_k=1,
    )
    snippet = ranked[0]['text'] if ranked else (sentences[0] if sentences else text)
    return snippet[:limit]


def retrieve_documents(
    question: str,
    resolved_context: dict[str, Any],
    reports: list[dict[str, Any]] | None = None,
    index_root: Path = INDEX_ROOT,
    top_k: int = 5,
) -> dict[str, Any]:
    report_rows = [
        dict(row) for row in (reports if reports is not None else fetch_report_files())
        if is_rag_eligible_report(dict(row))
    ]
    companies = set(resolved_context.get('companies') or [])
    years = {int(year) for year in resolved_context.get('years') or []}
    company_reports = [row for row in report_rows if row.get('company_name') in companies]
    exact = [row for row in company_reports if row.get('report_year') in years] if years else company_reports
    selected = exact or company_reports
    all_chunks, indexed_documents = [], 0
    for report in selected:
        try:
            index = build_document_index(report, index_root)
            if index.get('chunks'):
                indexed_documents += 1
                all_chunks.extend(index['chunks'])
        except Exception:
            continue
    retrieval_query = build_retrieval_query(question, resolved_context)
    metric_aliases = [
        alias.lower()
        for metric in resolved_context.get('metrics') or []
        for alias in METRIC_ALIASES.get(metric, [metric])
    ]
    if metric_aliases:
        all_chunks = [
            chunk for chunk in all_chunks
            if any(alias in chunk.get('text', '').lower() for alias in metric_aliases)
        ]
    else:
        raw_matches = rank_chunks(question, all_chunks, top_k=max(1, len(all_chunks)))
        matched_chunks = {item.get('chunk_id') for item in raw_matches}
        all_chunks = [chunk for chunk in all_chunks if chunk.get('chunk_id') in matched_chunks]
    hits = rank_chunks(retrieval_query, all_chunks, top_k)
    citations = []
    for number, hit in enumerate(hits, 1):
        citations.append({
            'number': number, 'document_id': hit.get('document_id'),
            'company': hit.get('company'), 'report_year': hit.get('report_year'),
            'file_name': hit.get('file_name'), 'file_path': hit.get('file_path'),
            'page': hit.get('page'), 'snippet': _extract_snippet(retrieval_query, hit.get('text', '')),
            'chunk_id': hit.get('chunk_id'), 'score': hit.get('score'),
        })
    answer = NO_EVIDENCE
    if citations:
        answer = '年报原文要点：\n' + '\n'.join(
            f'[{item["number"]}] {item["snippet"]}' for item in citations
        )
    return {
        'status': 'success' if citations else 'no_evidence', 'answer': answer,
        'hits': hits, 'citations': citations, 'candidate_documents': len(selected),
        'indexed_documents': indexed_documents, 'chunk_count': len(all_chunks),
        'hit_count': len(hits), 'exact_year_match': bool(exact),
        'retrieval_query': retrieval_query,
    }
