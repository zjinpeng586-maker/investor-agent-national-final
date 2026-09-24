"""Offline disclosure → review → atomic commit → shared SQL/conversation integration."""
from io import BytesIO
from pathlib import Path

import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

from core import db, service, online_disclosure
from core.conversation import new_conversation_context, resolve_turn
from core.qa_engine import answer_question
from core.service import prepare_import, commit_import
from core.storage import workspace_context

COMPANY = '测试科技股份有限公司'
URL = 'https://static.sse.com.cn/reports/test2024.pdf'


def report_pdf(*, numeric=True, half_year=False):
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    output = BytesIO()
    canvas = Canvas(output)
    canvas.setFont('STSong-Light', 12)
    lines = [COMPANY, '证券代码：600999', '2024年半年度报告' if half_year else '2024年年度报告',
             '主要会计数据和财务指标', '单位：亿元']
    if numeric:
        lines += ['指标      2024年      2023年', '营业收入      100      80',
                  '归母净利润      10      8', '经营现金流      20      16']
    else:
        lines += ['本报告记录公司年度业务概况及研究项目发展情况。', '附注资料尚未包含年度财务数值，请补充经核验的指标表。']
    for index, line in enumerate(lines):
        canvas.drawString(35, 780 - index * 30, line)
    canvas.save()
    return output.getvalue()


@pytest.fixture
def unified(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'local')
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('FINANCIAL_WORKSPACE', 'main')
    def forbidden(*args, **kwargs):
        raise AssertionError('No real network access is allowed')
    monkeypatch.setattr(online_disclosure.requests.Session, 'request', forbidden)
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db()
        yield tmp_path / 'main'


def online_batch(content=None):
    return prepare_import('测试2024年年度报告.pdf', content or report_pdf(), expected_company='测试科技',
        stock_code='600999', source_label='online_disclosure', source_url=URL,
        report_title='测试科技2024年年度报告', year_hint=2024, report_type='年度报告')


def test_search_and_preview_are_not_import_and_commit_feeds_all_readers(unified, monkeypatch):
    candidate = online_disclosure.DisclosureItem('测试科技2024年年度报告', '600999', '测试科技', None, '上交所', URL)
    monkeypatch.setattr(online_disclosure, 'search_sse_with_details', lambda *a, **k:
        ([candidate], {'requests': [{'status': '200', 'error': ''}]}))
    search = online_disclosure.search_disclosures_with_details('600999', '上交所', year=2024)
    assert search['status'] == 'success'
    assert not db.fetch_companies() and not db.fetch_report_files()
    monkeypatch.setattr(online_disclosure, 'safe_download_pdf', lambda *a, **k: ('测试2024年年度报告.pdf', report_pdf()))
    file_name, content = online_disclosure.download_pdf(URL)
    batch = online_batch(content)
    assert batch['can_commit'], batch['errors']
    assert not db.fetch_companies() and not db.fetch_report_files()
    assert not list(unified.rglob('*.pdf'))
    result = commit_import(batch, confirmed=True)
    assert result['ok'] and result['status'] == 'success' and result['committed']
    assert result['company_names'] == [COMPANY]
    assert result['imported_count'] == result['inserted_metrics'] == 6
    assert result['updated_metrics'] == 0 and result['imported_records'] == 2
    assert result['document_added'] and result['report_file_id']
    reports = [dict(row) for row in db.fetch_report_files()]
    assert len(reports) == 1
    report = reports[0]
    assert report['company_name'] == COMPANY and report['stock_code'] == '600999'
    assert report['source_type'] == 'exchange' and report['source_url'] == URL
    assert report['ingest_status'] == 'imported' and report['metric_count'] == 6
    assert report['report_year'] == 2024 and report['report_type'] == '年度报告'
    assert report['content_hash'] == batch['content_sha256']
    sources = db.fetch_metric_sources(report['company_id'])
    assert len(sources) == 6
    assert all(row['source_url'] == URL and row['import_id'] == batch['import_id'] for row in sources)

    from app.main import load_workspace
    _, name_to_id, data_map, names = load_workspace()
    assert COMPANY in names and COMPANY in name_to_id and COMPANY in data_map
    turn = resolve_turn(new_conversation_context(), '测试科技2024年的营业收入是多少？', names)
    first = answer_question(turn['question'], COMPANY, None, names, data_map, resolved_context=turn['resolved'])
    assert first['status'] == 'success' and '100.00' in first['answer']
    followup = resolve_turn(turn['context'], '那2023年呢？', names)
    second = answer_question(followup['question'], COMPANY, None, names, data_map, resolved_context=followup['resolved'])
    assert second['status'] == 'success' and '80.00' in second['answer']
    assert second['parsed']['companies'] == [COMPANY] and second['parsed']['metrics'] == ['revenue']


def test_metric_counts_and_preserved_conflicts_are_distinct(unified):
    first = prepare_import('first.csv', '公司,年份,营业收入（亿元）\n测试科技股份有限公司,2024,100'.encode())
    result = commit_import(first, confirmed=True)
    assert result['inserted_metrics'] == 1 and result['updated_metrics'] == 0
    patch = prepare_import('update.csv', '公司,年份,营业收入（亿元）,归母净利润（亿元）\n测试科技股份有限公司,2024,120,12'.encode())
    updated = commit_import(patch, confirmed=True, overwrite_existing=True)
    assert updated['inserted_metrics'] == updated['updated_metrics'] == 1
    assert updated['imported_count'] == 2
    unchanged = commit_import(prepare_import('same.csv', '公司,年份,营业收入（亿元）\n测试科技股份有限公司,2024,999'.encode()), confirmed=True)
    assert unchanged['status'] == 'unchanged' and not unchanged['ok']
    assert not unchanged['document_added'] and unchanged['imported_count'] == 0
    assert len(db.fetch_report_files()) == 2
    again = commit_import(patch, confirmed=True, overwrite_existing=True)
    assert again['status'] == 'unchanged' and len(db.fetch_report_files()) == 2


@pytest.mark.parametrize('phase', ['metric', 'report', 'verify'])
def test_any_transaction_failure_rolls_back_every_table(unified, monkeypatch, phase):
    batch = online_batch()
    def fail(*args, **kwargs):
        raise RuntimeError('模拟事务中失败')
    if phase == 'metric':
        monkeypatch.setattr(service, 'upsert_metric', fail)
    elif phase == 'report':
        monkeypatch.setattr(service, 'insert_report_file', fail)
    else:
        monkeypatch.setattr(service, '_verify_committed_import', fail)
    result = commit_import(batch, confirmed=True)
    assert result['status'] == 'failed' and not result['ok'] and not result['committed']
    assert result['inserted_metrics'] == result['updated_metrics'] == result['imported_count'] == 0
    assert result['company_names'] == [] and result['report_file_id'] is None
    with db.get_conn() as connection:
        for table in ('companies', 'financial_metrics', 'metric_sources', 'report_files'):
            assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    assert not list(unified.rglob('*.pdf'))


def test_postcommit_verification_failure_does_not_claim_rollback(unified, monkeypatch):
    original = service._verify_committed_import
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('模拟提交后查询异常')
        return original(*args, **kwargs)
    monkeypatch.setattr(service, '_verify_committed_import', fail_second)
    result = commit_import(online_batch(), confirmed=True)
    assert result['status'] == 'verification_failed' and not result['ok']
    assert result['committed'] and result['document_added']
    assert '事务已提交' in result['error_message']
    assert db.fetch_companies() and db.fetch_report_files()
    assert Path(result['file_path']).is_file()


def test_no_numeric_document_is_saved_only_with_explicit_document_confirmation(unified):
    batch = online_batch(report_pdf(numeric=False))
    assert not batch['can_commit']
    assert batch['can_save_document'], batch
    denied = commit_import(batch, confirmed=True)
    assert not denied['ok'] and not db.fetch_companies()
    result = commit_import(batch, confirmed=True, document_only=True)
    assert result['status'] == 'document_only' and not result['ok']
    assert result['document_added'] and result['committed']
    assert result['imported_count'] == 0
    report = dict(db.fetch_report_files()[0])
    assert report['ingest_status'] == 'document_only' and report['metric_count'] == 0
    assert report['source_url'] == URL and report['company_name'] == COMPANY
    assert not db.fetch_company_metrics(report['company_id'])
    from app.main import load_workspace
    companies, _, _, analysis_names = load_workspace()
    assert COMPANY in [row['name'] for row in companies] and COMPANY not in analysis_names


def test_interim_pdf_can_only_be_saved_as_document(unified):
    batch = prepare_import('测试2024年半年度报告.pdf', report_pdf(half_year=True), source_url=URL,
                           expected_company='测试科技', stock_code='600999', report_type='半年度报告')
    assert not batch['can_commit'] and batch['can_save_document'], batch['errors']
    assert not commit_import(batch, confirmed=True)['ok']
    result = commit_import(batch, confirmed=True, document_only=True)
    assert result['status'] == 'document_only'
    report = db.fetch_report_files()[0]
    assert report['report_type'] == '半年度报告' and report['report_year'] == 2024
    assert not db.fetch_company_metrics(report['company_id'])


def test_public_main_stays_readonly_for_structured_and_document_import(unified, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'public')
    with workspace_context('main', unified):
        result = commit_import(online_batch(), confirmed=True)
        assert result['status'] == 'failed' and '只读' in result['error_message']
        assert not db.fetch_companies() and not db.fetch_report_files()
