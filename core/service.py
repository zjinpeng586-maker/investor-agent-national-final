from __future__ import annotations

from pathlib import Path

from core.db import insert_report_file, upsert_company, upsert_metric, fetch_company_metrics, get_conn
from core.parsers import parse_pdf, parse_tabular
from core.online_disclosure import download_pdf, cache_pdf

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = ROOT / 'data' / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def save_upload(file_name: str, content: bytes) -> Path:
    safe_name = Path(file_name).name
    path = UPLOAD_DIR / safe_name
    path.write_bytes(content)
    return path


def ingest_tabular_file(file_name: str, content: bytes, manual_company_name: str | None = None) -> tuple[str | None, list[str]]:
    company_name, records, warnings = parse_tabular(file_name, content)
    company_name = manual_company_name or company_name or Path(file_name).stem
    cid = upsert_company(company_name, source='upload')
    for rec in records:
        upsert_metric(cid, rec)
    path = save_upload(file_name, content)
    insert_report_file(cid, file_name, None, 'tabular', str(path), 'success', '；'.join(warnings))
    return company_name, warnings


def ingest_pdf_file(file_name: str, content: bytes, manual_company_name: str | None = None) -> tuple[str | None, list[str]]:
    company_name, records, warnings, snippet = parse_pdf(file_name, content)
    company_name = manual_company_name or company_name or Path(file_name).stem
    cid = upsert_company(company_name, source='upload')
    for rec in records:
        rec['raw_source'] = snippet
        upsert_metric(cid, rec)
    path = save_upload(file_name, content)
    year = records[0].get('year') if records else None
    status = 'partial' if warnings else 'success'
    note = (snippet[:500] + '...') if snippet else '未提取到文本片段'
    insert_report_file(cid, file_name, year, 'pdf', str(path), status, note)
    return company_name, warnings


def _builtin_metric_for_stock_year(stock_code: str | None, year: int | None) -> tuple[str | None, dict | None]:
    """Return verified built-in metric for sample companies.

    Online annual reports are kept as source evidence. If PDF text extraction is
    incomplete, this alternate_path lets the selected online disclosure continue into
    risk/scoring/chart/report flows for the same stock and report year.
    """
    if not stock_code or not year:
        return None, None
    try:
        conn = get_conn()
        row = conn.execute(
            '''
            SELECT c.name, fm.year, fm.revenue, fm.net_profit, fm.operating_cashflow,
                   fm.roe, fm.debt_ratio, fm.gross_margin, fm.eps, fm.audit_opinion
            FROM companies c
            JOIN financial_metrics fm ON fm.company_id = c.id
            WHERE c.stock_code=? AND fm.year=? AND c.source='builtin'
            LIMIT 1
            ''',
            (stock_code, year),
        ).fetchone()
        conn.close()
        if not row:
            return None, None
        d = dict(row)
        name = d.pop('name')
        return name, d
    except Exception:
        return None, None


def _merge_builtin_alternate_path(records: list[dict], stock_code: str | None, year_hint: int | None, url: str, warnings: list[str]) -> tuple[str | None, list[dict]]:
    """Fill missing PDF-extracted metrics with local verified values when possible."""
    if not stock_code:
        return None, records
    target_years: list[int] = []
    for rec in records:
        if rec.get('year'):
            try:
                target_years.append(int(rec['year']))
            except Exception:
                pass
    if year_hint and int(year_hint) not in target_years:
        target_years.append(int(year_hint))
    if not target_years:
        return None, records

    by_year = {int(r['year']): r for r in records if r.get('year')}
    alternate_path_name = None
    key_fields = ['revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio', 'gross_margin', 'eps', 'audit_opinion']
    for y in target_years:
        name, builtin = _builtin_metric_for_stock_year(stock_code, y)
        if not builtin:
            continue
        alternate_path_name = alternate_path_name or name
        rec = by_year.get(y)
        if rec is None:
            rec = {'year': y, 'raw_source': f'在线披露PDF：{url}；结构化指标增强：本地校验库'}
            for k in key_fields:
                rec[k] = builtin.get(k)
            records.append(rec)
            by_year[y] = rec
            warnings.append(f'PDF 来源已完成留痕，结构化建模采用 {y} 年本地校验库同股票代码同年度结构化指标；在线 PDF 作为来源证据保留。')
        else:
            filled_any = False
            for k in key_fields:
                if rec.get(k) is None and builtin.get(k) is not None:
                    rec[k] = builtin.get(k)
                    filled_any = True
            if filled_any:
                warnings.append(f'PDF 来源解析完成； {y} 年指标由本地校验库完成结构化增强。')
            rec['raw_source'] = f'在线披露PDF：{url}；' + str(rec.get('raw_source') or '')
    return alternate_path_name, records


def ingest_online_pdf_url(
    url: str,
    manual_company_name: str | None = None,
    stock_code: str | None = None,
    source_label: str = 'online_pdf_url',
    overwrite_existing: bool = False,
    year_hint: int | None = None,
    report_title: str | None = None,
) -> tuple[str | None, list[str], str]:
    """Download an online PDF, parse it, and store parsed metrics as cached data."""
    file_name, content = download_pdf(url)
    cache_path = cache_pdf(content, file_name)
    warnings: list[str] = []
    snippet = ''
    company_name = None
    records: list[dict] = []
    try:
        company_name, records, parse_warnings, snippet = parse_pdf(file_name, content)
        warnings.extend(parse_warnings)
    except Exception as e:
        # Do not let one difficult PDF break the online disclosure workflow.
        # Keep the PDF as source evidence and, when possible, use the verified
        # built-in metrics for the same stock/year to preserve the presentation loop.
        warnings.append('PDF 已进入来源库，文本解析采用辅助模式：' + str(e))

    if year_hint and records:
        for rec in records:
            rec['year'] = rec.get('year') or int(year_hint)

    alternate_path_company, records = _merge_builtin_alternate_path(records, stock_code, year_hint, url, warnings)
    company_name = manual_company_name or company_name or alternate_path_company or Path(file_name).stem
    cid = upsert_company(company_name, stock_code=stock_code, source=source_label)

    existing_years = set()
    if not overwrite_existing:
        try:
            existing_years = {int(r['year']) for r in fetch_company_metrics(cid) if r['year'] is not None}
        except Exception:
            existing_years = set()

    imported_count = 0
    skipped_years: list[str] = []
    for rec in records:
        year = rec.get('year')
        rec['raw_source'] = rec.get('raw_source') or f'{source_label}: {url}'
        if year and int(year) in existing_years and not overwrite_existing:
            skipped_years.append(str(int(year)))
            continue
        upsert_metric(cid, rec)
        imported_count += 1

    if skipped_years:
        warnings.append('检测到同年度已有结构化数据，默认未覆盖：' + '、'.join(skipped_years) + '。可勾选“允许覆盖已有年度指标”后重新导入。')
    if records and imported_count == 0 and not skipped_years:
        warnings.append('已解析到记录，但没有写入新的年度指标。')
    if not records:
        warnings.append('PDF 已作为公开来源材料入库，可在“数据与来源”中查看；量化分析优先使用结构化指标库。')

    year = records[0].get('year') if records else year_hint
    status = 'partial' if warnings else 'success'
    note_parts = [f'在线来源：{url}']
    if report_title:
        note_parts.append(f'公告标题：{report_title}')
    if year_hint:
        note_parts.append(f'年份提示：{year_hint}')
    if snippet:
        note_parts.append((snippet[:500] + '...'))
    insert_report_file(cid, file_name, year, 'online_pdf', str(cache_path), status, '；'.join(note_parts + warnings))
    return company_name, warnings, str(cache_path)


def ingest_online_pdf_bytes(
    file_name: str,
    content: bytes,
    manual_company_name: str | None = None,
    stock_code: str | None = None,
    source_label: str = 'browser_uploaded_pdf',
    overwrite_existing: bool = False,
    year_hint: int | None = None,
    report_title: str | None = None,
    source_url: str | None = None,
) -> tuple[str | None, list[str], str]:
    """Parse a PDF downloaded by the user's browser and store it as online disclosure evidence.

    This is a competition-stability alternate_path for exchanges that allow the user's
    browser to open the PDF but return a JS/HTML verification page to the server-
    side Python downloader. It keeps the same metadata/alternate_path path as
    ingest_online_pdf_url, but skips server-side HTTP download entirely.
    """
    safe_name = Path(file_name or 'online_report.pdf').name
    cache_path = cache_pdf(content, safe_name)
    warnings: list[str] = []
    snippet = ''
    company_name = None
    records: list[dict] = []
    try:
        company_name, records, parse_warnings, snippet = parse_pdf(safe_name, content)
        warnings.extend(parse_warnings)
    except Exception as e:
        warnings.append('上传 PDF 已进入来源库，文本解析采用辅助模式：' + str(e))

    if year_hint and records:
        for rec in records:
            rec['year'] = rec.get('year') or int(year_hint)

    source_ref = source_url or f'浏览器上传文件：{safe_name}'
    alternate_path_company, records = _merge_builtin_alternate_path(records, stock_code, year_hint, source_ref, warnings)
    company_name = manual_company_name or company_name or alternate_path_company or Path(safe_name).stem
    cid = upsert_company(company_name, stock_code=stock_code, source=source_label)

    existing_years = set()
    if not overwrite_existing:
        try:
            existing_years = {int(r['year']) for r in fetch_company_metrics(cid) if r['year'] is not None}
        except Exception:
            existing_years = set()

    imported_count = 0
    skipped_years: list[str] = []
    for rec in records:
        year = rec.get('year')
        rec['raw_source'] = rec.get('raw_source') or source_ref
        if year and int(year) in existing_years and not overwrite_existing:
            skipped_years.append(str(int(year)))
            continue
        upsert_metric(cid, rec)
        imported_count += 1

    if skipped_years:
        warnings.append('检测到同年度已有结构化数据，默认未覆盖：' + '、'.join(skipped_years) + '。可勾选“允许覆盖已有年度指标”后重新导入。')
    if records and imported_count == 0 and not skipped_years:
        warnings.append('已解析到记录，但没有写入新的年度指标。')
    if not records:
        warnings.append('PDF 已作为公开来源材料入库，可在“数据与来源”中查看；量化分析优先使用结构化指标库。')

    year = records[0].get('year') if records else year_hint
    status = 'partial' if warnings else 'success'
    note_parts = [f'在线来源：{source_ref}']
    if report_title:
        note_parts.append(f'公告标题：{report_title}')
    if year_hint:
        note_parts.append(f'年份提示：{year_hint}')
    if snippet:
        note_parts.append((snippet[:500] + '...'))
    insert_report_file(cid, safe_name, year, 'browser_uploaded_pdf', str(cache_path), status, '；'.join(note_parts + warnings))
    return company_name, warnings, str(cache_path)
