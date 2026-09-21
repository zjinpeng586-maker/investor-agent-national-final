from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

from core.conversation import new_conversation_context, resolve_turn
from core.db import ROOT, init_db
from core.planner import plan_query
from core.retrieval import retrieve_documents
from core.seed import seed_sample_data
from core.text_to_sql import execute_readonly_sql, run_text_to_sql, validate_sql


SUITE_PATH = ROOT / 'data' / 'evaluation' / 'benchmark_v1.json'
BYD = '比亚迪股份有限公司'
CATL = '宁德时代新能源科技股份有限公司'
COMPANIES = [BYD, CATL]
CATEGORY_LABELS = {
    'conversation': '多轮上下文', 'planner': 'Planner 路由',
    'text_to_sql': 'Text-to-SQL 结果正确性', 'sql_security': 'SQL 安全',
    'rag_citation': 'RAG 页码与证据',
}


def load_benchmark_suite(path: str | Path = SUITE_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _project_rows(rows: list[dict[str, Any]], expected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = set().union(*(row.keys() for row in expected)) if expected else set()
    projected = [{key: row.get(key) for key in keys} for row in rows]
    return sorted(projected, key=lambda row: (str(row.get('company')), row.get('year') or 0))


def _write_pdf(path: Path, pages: list[str]) -> None:
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    canvas = Canvas(str(path))
    for text in pages:
        canvas.setFont('STSong-Light', 12)
        canvas.drawString(72, 720, text)
        canvas.showPage()
    canvas.save()


def _rag_reports(root: Path) -> dict[str, list[dict[str, Any]]]:
    full_path = root / 'benchmark-report.pdf'
    numeric_path = root / 'numeric-only.pdf'
    _write_pdf(full_path, [
        '公司2024年持续加大新能源汽车研发投入，重点推进电池和智能化技术。',
        '海外市场拓展面临汇率波动及地区政策风险，公司将加强风险管理。',
        '管理层认为毛利率变化主要受到产品结构调整影响。',
        '2024年归属于上市公司股东的净利润为402.54亿元。',
        '公司归母净利润变化主要受到期间费用、产品结构及市场竞争影响。',
    ])
    _write_pdf(numeric_path, ['2024年归属于上市公司股东的净利润为402.54亿元。'])
    base = {'company_name': BYD, 'report_year': 2024, 'file_type': 'PDF'}
    return {
        'default': [{**base, 'id': 'benchmark-full', 'file_name': full_path.name, 'file_path': str(full_path)}],
        'numeric_only': [{**base, 'id': 'benchmark-number', 'file_name': numeric_path.name, 'file_path': str(numeric_path)}],
    }


def _evaluate_case(case: dict[str, Any], rag_root: Path, rag_reports: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    category, expected = case['category'], case['expected']
    if category == 'conversation':
        context = new_conversation_context()
        result = None
        for question in case['turns']:
            result = resolve_turn(context, question, COMPANIES, page_company=BYD, page_compare=CATL)
            context = result['context']
        actual = {'status': result['status']}
        if result['status'] == 'ready':
            actual.update({key: result['resolved'].get(key) for key in ['companies', 'years', 'metrics', 'intent']})
        else:
            actual['field'] = result.get('field')
        return {'actual': actual, 'passed': actual == expected}
    if category == 'planner':
        plan = plan_query(case['question'], case['context'])
        actual = {key: plan.get(key) for key in expected}
        return {'actual': actual, 'passed': actual == expected}
    if category == 'text_to_sql':
        result = run_text_to_sql(case['context'])
        actual = {'status': result['status'], 'rows': _project_rows(result.get('rows', []), expected['rows'])}
        target = {'status': 'success', 'rows': sorted(expected['rows'], key=lambda row: (row['company'], row['year']))}
        return {'actual': actual, 'passed': actual == target}
    if category == 'sql_security':
        validation = validate_sql(case['sql'], [])
        blocked = not validation['valid']
        executable = None
        if not blocked:
            executable = execute_readonly_sql(case['sql'], [])['row_count'] >= 0
        actual = {'blocked': blocked, 'executable': executable}
        return {'actual': actual, 'passed': blocked == expected['blocked'] and (blocked or executable is True)}
    if category == 'rag_citation':
        variant = case.get('variant', 'default')
        result = retrieve_documents(
            case['question'], case['context'], rag_reports[variant], rag_root / f'index-{variant}',
        )
        pages = [item['page'] for item in result['citations']]
        actual = {'status': result['status'], 'pages': pages}
        passed = result['status'] == expected['status']
        if 'first_page' in expected:
            passed = passed and bool(pages) and pages[0] == expected['first_page']
        if expected.get('excluded_pages'):
            passed = passed and not (set(pages) & set(expected['excluded_pages']))
        return {'actual': actual, 'passed': passed}
    raise ValueError(f'未知 Benchmark 类别：{category}')


def summarize_results(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    passed = sum(bool(case.get('passed')) for case in cases)
    categories: dict[str, dict[str, Any]] = {}
    for case in cases:
        item = categories.setdefault(case['category'], {'passed': 0, 'total': 0})
        item['total'] += 1
        item['passed'] += int(bool(case.get('passed')))
    for key, item in categories.items():
        item['failed'] = item['total'] - item['passed']
        item['pass_rate'] = item['passed'] / item['total'] if item['total'] else 0.0
        item['label'] = CATEGORY_LABELS.get(key, key)
    return {
        'total': total, 'passed': passed, 'failed': total - passed,
        'pass_rate': passed / total if total else 0.0, 'categories': categories,
    }


def run_benchmark(suite: dict[str, Any] | None = None) -> dict[str, Any]:
    suite = suite or load_benchmark_suite()
    init_db()
    seed_sample_data()
    started_at = datetime.now(timezone.utc).isoformat()
    overall_start = time.perf_counter()
    results = []
    with tempfile.TemporaryDirectory(prefix='financial-benchmark-') as temp_dir:
        root = Path(temp_dir)
        reports = _rag_reports(root)
        for case in suite.get('cases', []):
            case_start = time.perf_counter()
            result = {
                'id': case.get('id'), 'category': case.get('category'),
                'scenario': case.get('question') or ' → '.join(case.get('turns') or []),
                'expected': case.get('expected'), 'actual': None, 'passed': False, 'error': None,
            }
            try:
                evaluated = _evaluate_case(case, root, reports)
                result.update(evaluated)
            except Exception as exc:
                result['error'] = f'{type(exc).__name__}: {exc}'
            result['duration_ms'] = round((time.perf_counter() - case_start) * 1000, 3)
            results.append(result)
    summary = summarize_results(results)
    return {
        'suite_id': suite.get('suite_id'), 'suite_version': suite.get('version'),
        'description': suite.get('description'), 'started_at': started_at,
        'duration_ms': round((time.perf_counter() - overall_start) * 1000, 3),
        **summary, 'cases': results,
        'disclaimer': '该结果仅代表内置确定性回归 Benchmark，不代表外部真实生产数据集泛化能力。',
    }
