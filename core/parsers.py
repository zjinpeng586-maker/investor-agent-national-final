from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pandas as pd
from pypdf import PdfReader

try:
    from pdfminer.high_level import extract_text as pdfminer_extract_text
except Exception:  # pragma: no cover - optional dependency alternate_path
    pdfminer_extract_text = None

try:
    import fitz  # PyMuPDF, more tolerant for some exchange annual reports
except Exception:  # pragma: no cover - optional dependency alternate_path
    fitz = None

try:
    import pdfplumber  # table-aware extraction for annual-report financial tables
except Exception:  # pragma: no cover - optional dependency alternate_path
    pdfplumber = None

CH_ALIASES = {
    'year': ['年份', '年度', '报告年度', 'year', 'report_year'],
    'company': ['企业名称', '公司名称', '公司', '企业', 'company', '证券简称'],
    'revenue': ['营业收入', '营业总收入', '营收', '收入合计', 'revenue', '营业收入(亿)', '营业收入(亿元)', '营业收入（亿元）'],
    'net_profit': ['归母净利润', '归属于上市公司股东的净利润', '归属于母公司股东的净利润', '净利润', 'net_profit', '归母净利润(亿)', '归母净利润(亿元)'],
    'operating_cashflow': ['经营现金流', '经营活动产生的现金流量净额', '经营活动现金流量净额', '经营现金流净额', 'operating_cashflow', '经营活动现金流'],
    'roe': ['ROE', '净资产收益率', '加权平均净资产收益率', 'roe'],
    'debt_ratio': ['资产负债率', 'debt_ratio'],
    'gross_margin': ['毛利率', '整体毛利率', 'gross_margin'],
    'eps': ['每股收益', '基本每股收益', 'eps'],
    'audit_opinion': ['审计意见', '审计意见类型', 'audit_opinion'],
}


def _norm_col(s: str) -> str:
    return str(s).strip().replace('（', '(').replace('）', ')').replace('％', '%').replace('\n', '')


def _find_col(cols: list[str], aliases: list[str]) -> str | None:
    normed = {_norm_col(c): c for c in cols}
    for alias in aliases:
        alias_n = _norm_col(alias).lower()
        for c_norm, original in normed.items():
            low = c_norm.lower()
            if alias_n == low or alias_n in low or low in alias_n:
                return original
    return None


def _to_float(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace(',', '').replace('，', '').replace('%', '').replace('％', '')
    s = s.replace('约', '').replace('人民币', '').replace('元', '')
    if not s or s.lower() == 'nan' or s in ['-', '—', '--']:
        return None
    try:
        return float(s)
    except Exception:
        m = re.search(r'-?\d+(?:\.\d+)?', s)
        if m:
            try:
                return float(m.group(0))
            except Exception:
                return None
    return None


def _read_tabular(file_name: str, content: bytes) -> pd.DataFrame:
    if file_name.lower().endswith('.csv'):
        try:
            return pd.read_csv(io.BytesIO(content))
        except UnicodeDecodeError:
            return pd.read_csv(io.BytesIO(content), encoding='gbk')
    # read first sheet; if first rows are notes, try scanning header rows
    raw = pd.read_excel(io.BytesIO(content), header=None)
    for header_idx in range(min(8, len(raw))):
        candidate = pd.read_excel(io.BytesIO(content), header=header_idx)
        cols = [_norm_col(c) for c in candidate.columns]
        if _find_col(cols, CH_ALIASES['year']) and (_find_col(cols, CH_ALIASES['revenue']) or _find_col(cols, CH_ALIASES['net_profit'])):
            return candidate
    return pd.read_excel(io.BytesIO(content))


def parse_tabular(file_name: str, content: bytes) -> tuple[str | None, list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    df = _read_tabular(file_name, content)
    df.columns = [_norm_col(c) for c in df.columns]
    company_col = _find_col(list(df.columns), CH_ALIASES['company'])
    year_col = _find_col(list(df.columns), CH_ALIASES['year'])
    if not year_col:
        raise ValueError('未识别到年份列，请在表中包含“年份”或“year”列。')

    company_name = None
    if company_col and df[company_col].notna().any():
        company_name = str(df[company_col].dropna().iloc[0]).strip()

    mapped: dict[str, str] = {}
    for key, aliases in CH_ALIASES.items():
        col = _find_col(list(df.columns), aliases)
        if col:
            mapped[key] = col

    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        year = _to_float(row.get(mapped.get('year', '')))
        if year is None:
            continue
        record = {'year': int(year), 'raw_source': file_name}
        for key in ['revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio', 'gross_margin', 'eps']:
            col = mapped.get(key)
            if col:
                record[key] = _to_float(row.get(col))
        if mapped.get('audit_opinion'):
            val = row.get(mapped['audit_opinion'])
            record['audit_opinion'] = None if pd.isna(val) else str(val).strip()
        records.append(record)

    needed = ['revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio']
    missing = [k for k in needed if k not in mapped]
    if missing:
        warnings.append('以下字段未识别，将以空值导入：' + '、'.join(missing))
    if not company_name:
        warnings.append('未在表格中识别到企业名称，将使用文件名或手动名称。')
    if not records:
        raise ValueError('未读取到有效年度数据，请检查表格是否为标准行列表。')
    return company_name, records, warnings


COMPANY_PATTERNS = [
    r'([\u4e00-\u9fa5A-Za-z0-9（）()·]{2,40}股份有限公司)',
    r'([\u4e00-\u9fa5A-Za-z0-9（）()·]{2,40}有限公司)',
]
YEAR_PATTERN = re.compile(r'(20\d{2})\s*年')


def extract_pdf_text(content: bytes, page_limit: int = 120) -> str:
    """Extract text from a PDF with stable alternate_paths.

    SSE/SZSE annual reports are sometimes large and contain object streams that
    pypdf may reject with errors such as ``Stream has ended unexpectedly`` even
    though the browser can open the file. For presentation stability we should not
    let such parser-level failures crash the app. Try pypdf first, then fall
    back to pdfminer.six; if both fail, return a clear Chinese error.
    """
    if not content or len(content) < 1024:
        raise ValueError('PDF 文件内容过小，可能未完整下载。请重试或复制浏览器中可打开的最终 PDF 链接导入。')
    header_pos = content[:8192].find(b'%PDF')
    if header_pos < 0:
        preview = content[:180].decode('utf-8', errors='ignore').replace('\n', ' ')
        raise ValueError('下载内容暂未识别为有效 PDF 文件。可能收到的是网页提示内容而不是年报 PDF。内容预览：' + preview)
    if header_pos > 0:
        content = content[header_pos:]

    errors: list[str] = []
    # pypdf fast path. strict=False is important for many exchange PDFs.
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        texts = []
        for p in reader.pages[:page_limit]:
            try:
                texts.append(p.extract_text() or '')
            except Exception as e:
                errors.append(f'pypdf页面解析未完成：{e}')
                continue
        text = '\n'.join(texts).strip()
        if text:
            return text
        errors.append('pypdf 未提取到有效文本')
    except Exception as e:
        errors.append(f'pypdf整体解析未完成：{e}')

    # AlternatePath 1: PyMuPDF is often more tolerant for object streams.
    if fitz is not None:
        try:
            doc = fitz.open(stream=content, filetype='pdf')
            texts = []
            for i in range(min(page_limit, doc.page_count)):
                try:
                    texts.append(doc.load_page(i).get_text('text') or '')
                except Exception as e:
                    errors.append(f'PyMuPDF页面解析未完成：{e}')
            text = '\n'.join(texts).strip()
            if text:
                return text
            errors.append('PyMuPDF 未提取到有效文本')
        except Exception as e:
            errors.append(f'PyMuPDF解析未完成：{e}')

    # AlternatePath 2: pdfplumber can recover row/column tables that plain text
    # extraction sometimes misses, especially in bank/insurance annual reports.
    if pdfplumber is not None:
        try:
            texts = []
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages[:page_limit]:
                    try:
                        t = page.extract_text() or ''
                        if t:
                            texts.append(t)
                        for table in page.extract_tables() or []:
                            for row in table:
                                line = ' '.join(str(cell or '').replace('\n', ' ') for cell in row)
                                if line.strip():
                                    texts.append(line)
                    except Exception as e:
                        errors.append(f'pdfplumber页面解析未完成：{e}')
            text = '\n'.join(texts).strip()
            if text:
                return text
            errors.append('pdfplumber 未提取到有效文本')
        except Exception as e:
            errors.append(f'pdfplumber解析未完成：{e}')

    # AlternatePath 3 for PDFs that the browser can open but pypdf cannot parse.
    if pdfminer_extract_text is not None:
        try:
            text = pdfminer_extract_text(io.BytesIO(content), maxpages=page_limit) or ''
            if text.strip():
                return text
            errors.append('pdfminer 未提取到有效文本')
        except Exception as e:
            errors.append(f'pdfminer解析未完成：{e}')

    # Last resort: keep the app alive and produce a user-understandable failure.
    joined = '；'.join(errors[-3:])
    raise ValueError('PDF 已下载，但文本解析未完成。可能原因：该年报 PDF 对象流较特殊、文本层异常或文件下载内容不完整。技术细节：' + joined)


def _extract_company(text: str, file_name: str = '') -> str | None:
    # Prefer title area
    head = text[:3000]
    for pat in COMPANY_PATTERNS:
        m = re.search(pat, head)
        if m:
            name = m.group(1)
            for bad in ['本公司', '公司法', '股份有限公司法']:
                if bad in name:
                    continue
            return name
    stem = Path(file_name).stem
    for pat in COMPANY_PATTERNS:
        m = re.search(pat, stem)
        if m:
            return m.group(1)
    return None


def _extract_year(text: str, file_name: str = '') -> int | None:
    # prefer annual report patterns
    for src in [file_name, text[:3000]]:
        m = re.search(r'(20\d{2})\s*年\s*(?:年度报告|年度财务报告|年报)', src)
        if m:
            return int(m.group(1))
    years = YEAR_PATTERN.findall(text[:5000]) or re.findall(r'20\d{2}', file_name)
    if years:
        return int(years[0])
    return None


def _unit_factor(window: str) -> float:
    if '亿元' in window:
        return 1.0
    if '百万元' in window or '百万' in window:
        return 0.01
    if '万元' in window:
        return 1e-4
    if '千元' in window:
        return 1e-5
    if '元' in window:
        return 1e-8
    return 1.0


def _numbers_after_label(text: str, label: str, width: int = 520) -> list[float]:
    idx = text.find(label)
    if idx == -1:
        return []
    window = text[idx: idx + width].replace(' ', '')
    nums = []
    for m in re.finditer(r'[-+]?\d[\d,]*(?:\.\d+)?%?', window):
        raw = m.group(0)
        if '%' in raw:
            try:
                nums.append(float(raw.replace('%', '').replace(',', '')))
            except Exception:
                pass
        else:
            try:
                nums.append(float(raw.replace(',', '')) * _unit_factor(window))
            except Exception:
                pass
    return nums




def _iter_contexts(text: str, labels: list[str], width: int = 900) -> list[tuple[str, str, int]]:
    """Return candidate contexts around all label occurrences.

    Annual-report PDFs often contain the same label many times. The first hit may
    be a narrative paragraph, business segment table, or quarterly table. We scan
    every occurrence and later rank candidate contexts instead of only using
    ``text.find``.
    """
    norm = text.replace('\u3000', ' ')
    contexts: list[tuple[str, str, int]] = []
    for label in labels:
        start = 0
        while True:
            idx = norm.find(label, start)
            if idx == -1:
                break
            left = max(0, idx - 350)
            right = min(len(norm), idx + width)
            contexts.append((label, norm[left:right], idx))
            start = idx + max(1, len(label))
    return contexts


def _score_metric_context(label: str, ctx: str, metric: str) -> int:
    score = 0
    good_words = ['主要会计数据', '主要财务指标', '会计数据和财务指标', '合并利润表', '合并现金流量表', '合并资产负债表']
    bad_words = ['分季度', '季度', '主营业务分', '按产品', '按行业', '构成', '占营业收入', '变动原因', '募集资金', '非经常性']
    for w in good_words:
        if w in ctx:
            score += 8
    for w in bad_words:
        if w in ctx:
            score -= 8
    if metric == 'revenue' and '营业收入' == label:
        score += 3
    if metric == 'net_profit' and '归属于上市公司股东的净利润' in label:
        score += 5
    if metric == 'operating_cashflow' and '经营活动产生的现金流量净额' in label:
        score += 5
    if metric == 'roe' and '加权平均' in label:
        score += 5
    return score


def _extract_numbers_with_units(ctx: str, want_percent: bool = False) -> list[float]:
    # Normalize common OCR/text-extraction spacing while preserving % symbols.
    compact = ctx.replace(',', '').replace('，', '').replace('\u3000', ' ')
    compact = re.sub(r'(?<=\d)\s+(?=\d)', '', compact)
    if want_percent:
        vals: list[float] = []
        for m in re.finditer(r'[-+]?\d+(?:\.\d+)?\s*%?', compact):
            raw = m.group(0).strip()
            try:
                val = float(raw.replace('%', ''))
            except Exception:
                continue
            if -100 <= val <= 100 and not (1900 <= abs(val) <= 2100):
                vals.append(val)
        return vals
    factor = _unit_factor(compact)
    vals = []
    for m in re.finditer(r'[-+]?\d+(?:\.\d+)?', compact):
        raw = m.group(0)
        try:
            val = float(raw)
        except Exception:
            continue
        # Skip years, page numbers and percentage fragments.
        if 1900 <= abs(val) <= 2100:
            continue
        around = compact[max(0, m.start()-2): m.end()+2]
        if '%' in around:
            continue
        # A-share annual reports usually present monetary amounts in yuan, but
        # some extracted text loses the nearby unit. If the number is huge, infer
        # yuan and convert to 亿元.
        if factor == 1.0 and abs(val) > 1_000_000:
            val = val * 1e-8
        else:
            val = val * factor
        if abs(val) >= 0.0001:
            vals.append(val)
    return vals


def _metric_from_all_contexts(text: str, labels: list[str], metric: str, prefer_percent: bool = False) -> float | None:
    candidates: list[tuple[int, float]] = []
    for label, ctx, _idx in _iter_contexts(text, labels):
        nums = _extract_numbers_with_units(ctx, want_percent=prefer_percent)
        if not nums:
            continue
        score = _score_metric_context(label, ctx, metric)
        # Prefer first column value in a financial-data table, but ignore values
        # obviously outside normal financial ratio ranges.
        for pos, val in enumerate(nums[:4]):
            if prefer_percent:
                if not (-100 <= val <= 100):
                    continue
            else:
                # Most target monetary metrics in our competition data are in 亿元.
                if abs(val) > 1_000_000:  # still not converted correctly
                    continue
            candidates.append((score - pos, round(val, 2)))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None


def _debt_ratio_from_balance_sheet(text: str) -> float | None:
    assets = _metric_from_all_contexts(text, ['资产总计', '总资产'], 'total_assets')
    liabilities = _metric_from_all_contexts(text, ['负债合计', '总负债'], 'total_liabilities')
    if assets and liabilities and assets > 0:
        ratio = liabilities / assets * 100
        if 0 <= ratio <= 150:
            return round(ratio, 2)
    return None

def _num_from_context(text: str, labels: list[str], prefer_percent: bool = False, metric: str = '') -> float | None:
    # First use the strengthened all-context extractor. It is much more stable
    # for exchange-downloaded annual reports than the old first-hit scan.
    val = _metric_from_all_contexts(text, labels, metric or labels[0], prefer_percent=prefer_percent)
    if val is not None:
        return val
    # AlternatePath to old first-occurrence scan for simple PDFs/templates.
    for label in labels:
        idx = text.find(label)
        if idx == -1:
            continue
        window = text[idx: idx + 900].replace(' ', '')
        if prefer_percent:
            m = re.search(r'([-+]?\d+(?:\.\d+)?)\s*%', window)
            if m:
                return float(m.group(1))
        nums = _numbers_after_label(text, label)
        if nums:
            filtered = [x for x in nums if not (1900 <= abs(x) <= 2100 and len(str(int(abs(x)))) == 4)]
            if filtered:
                return round(filtered[0], 2)
    return None


def _str_from_context(text: str, labels: list[str]) -> str | None:
    for label in labels:
        idx = text.find(label)
        if idx == -1:
            continue
        window = text[idx: idx + 180]
        for opinion in ['标准的无保留意见', '保留意见', '否定意见', '无法表示意见', '带强调事项段的无保留意见']:
            if opinion in window:
                return opinion
        m = re.search(label + r'[：:\s]*([^\n]{2,50})', window)
        if m:
            return m.group(1).strip()
    return None


def parse_pdf(file_name: str, content: bytes) -> tuple[str | None, list[dict[str, Any]], list[str], str]:
    warnings: list[str] = []
    text = extract_pdf_text(content)
    company_name = _extract_company(text, file_name)
    year = _extract_year(text, file_name)
    record = {
        'year': year,
        'revenue': _num_from_context(text, ['营业收入', '营业总收入', '一、营业总收入'], metric='revenue'),
        'net_profit': _num_from_context(text, ['归属于上市公司股东的净利润', '归属于母公司股东的净利润', '归母净利润'], metric='net_profit'),
        'operating_cashflow': _num_from_context(text, ['经营活动产生的现金流量净额', '经营活动现金流量净额', '经营现金流'], metric='operating_cashflow'),
        'roe': _num_from_context(text, ['加权平均净资产收益率', '净资产收益率'], prefer_percent=True, metric='roe'),
        'debt_ratio': _num_from_context(text, ['资产负债率'], prefer_percent=True, metric='debt_ratio'),
        'gross_margin': _num_from_context(text, ['毛利率'], prefer_percent=True, metric='gross_margin'),
        'eps': _num_from_context(text, ['基本每股收益', '每股收益'], metric='eps'),
        'audit_opinion': _str_from_context(text, ['审计意见类型', '审计意见']),
        'raw_source': file_name,
    }
    if record.get('debt_ratio') is None:
        record['debt_ratio'] = _debt_ratio_from_balance_sheet(text)

    if not company_name:
        warnings.append('PDF 企业名称建议以页面/手动输入信息为准。')
    if not year:
        warnings.append('PDF 报告年份建议以页面/手动输入信息为准。')
    filled = [k for k in ['revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio'] if record.get(k) is not None]
    snippet = text[:1600]
    if not filled:
        warnings.append('PDF 已完成来源解析与入库；结构化指标将优先采用财务表或校验库。')
        return company_name, [], warnings, snippet
    if len(filled) < 3:
        warnings.append('PDF 已完成来源解析；部分量化字段可由结构化财务表继续增强。')
    return company_name, [record], warnings, snippet
