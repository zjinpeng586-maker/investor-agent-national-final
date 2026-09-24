"""Real widgets, mocked network, actual import transaction and fresh selectors."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from streamlit.testing.v1 import AppTest

from core import db, online_disclosure, service
from core.online_disclosure import DisclosureItem
from core.storage import workspace_context

APP = Path(__file__).resolve().parents[1] / 'app/main.py'
COMPANY = '测试科技股份有限公司'
URL = 'https://static.cninfo.com.cn/finalpage/2025-04-01/unified-test.PDF'


def pdf_bytes(*, document_only=False):
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(800, 700))
    pdf.setFont('STSong-Light', 12)
    lines = [COMPANY, '证券代码：688999', '2024年年度报告', '主要会计数据和财务指标', '单位：亿元']
    lines += ['指标      2024年      2023年', '营业收入      100      90',
              '归母净利润      10      8', '经营现金流      20      15'] if not document_only else ['公司业务主要围绕科技服务开展。']
    for i, line in enumerate(lines):
        pdf.drawString(30, 670 - i * 28, line)
    pdf.save()
    return buffer.getvalue()


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'local')
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('FINANCIAL_WORKSPACE', 'main')
    # Unexpected network calls must fail the test, not reach a real exchange.
    def offline(*args, **kwargs):
        raise AssertionError('Live network is forbidden in UI tests')
    monkeypatch.setattr('requests.sessions.Session.request', offline)
    return tmp_path


def app():
    instance = AppTest.from_file(APP, default_timeout=40).run()
    assert not instance.exception
    return instance


def data_center(instance):
    instance.radio(key='navigation').set_value('数据中心').run()
    assert not instance.exception
    return instance


def search_result(status='success'):
    items = [DisclosureItem('测试科技：2024年年度报告', '688999', COMPANY, '2025-04-01', '上交所', URL)] if status == 'success' else []
    return {'status': status, 'candidates': items, 'candidate_count': len(items),
            'message': '接口未响应' if status == 'failed' else '', 'query': '测试科技',
            'exchange': '上交所', 'report_type': '年度报告', 'year': 2024,
            'request_success_count': 0 if status == 'failed' else 1,
            'request_failure_count': 1 if status == 'failed' else 0, 'elapsed_sec': 0.01,
            'diagnostics': {'requests': [{'status': status}], 'steps': ['mock response']}}


def table_records(instance):
    return next(frame.value for frame in instance.dataframe if '接入状态' in frame.value.columns)


def companies(root):
    with workspace_context('main', root / 'main'):
        return [row['name'] for row in db.fetch_companies()]


def query(instance, question):
    instance.text_area(key='qa_input').set_value(question)
    next(button for button in instance.button if button.label == '开始分析').click().run()
    assert not instance.exception
    return instance.session_state.chat_messages[-1]['result']


@pytest.mark.parametrize('status,element,message', [
    ('success', 'success', '检索完成：共获取 1 条'),
    ('empty', 'info', '检索完成：未获取到'),
    ('failed', 'error', '检索失败：'),
])
def test_search_feedback_persists_across_reruns(local, monkeypatch, status, element, message):
    monkeypatch.setattr(online_disclosure, 'search_disclosures_with_details', lambda *a, **kw: search_result(status))
    instance = data_center(app())
    instance.button(key='search_disclosures').click().run()
    for _ in range(2):
        assert not instance.exception
        assert any(message in item.value for item in getattr(instance, element))
        assert any(expander.label == '检索详情' for expander in instance.expander)
        assert len(instance.session_state.online_candidates) == (1 if status == 'success' else 0)
        assert COMPANY not in companies(local)
        instance.run()


def test_failed_search_and_changed_scope_clear_previous_preview(local, monkeypatch):
    outcomes = iter([search_result(), search_result('failed')])
    monkeypatch.setattr(online_disclosure, 'search_disclosures_with_details', lambda *a, **kw: next(outcomes))
    monkeypatch.setattr(online_disclosure, 'download_pdf', lambda *a, **kw: ('年度报告.pdf', pdf_bytes()))
    instance = data_center(app())
    instance.button(key='search_disclosures').click().run()
    instance.button(key='prepare_disclosure').click().run()
    assert not instance.exception
    assert instance.session_state.pending_import['can_commit']
    instance.button(key='search_disclosures').click().run()
    assert not instance.exception
    assert not instance.session_state.online_candidates
    assert 'pending_import' not in instance.session_state
    assert any('检索失败' in item.value for item in instance.error)
    assert not any('检索完成：共获取' in item.value or '解析完成' in item.value for item in instance.success)
    instance.selectbox(key='disclosure_year').set_value('2023').run()
    assert instance.session_state.online_search_result is None


def test_real_pdf_commit_refreshes_every_selector_query_and_source(local, monkeypatch):
    monkeypatch.setattr(online_disclosure, 'search_disclosures_with_details', lambda *a, **kw: search_result())
    monkeypatch.setattr(online_disclosure, 'download_pdf', lambda *a, **kw: ('年度报告.pdf', pdf_bytes()))
    instance = data_center(app())
    instance.button(key='search_disclosures').click().run()
    assert COMPANY not in companies(local)
    instance.button(key='prepare_disclosure').click().run()
    assert not instance.exception
    assert any('解析完成' in item.value for item in instance.success)
    assert not any('入库成功' in item.value for item in instance.success)
    assert COMPANY not in companies(local)
    batch = instance.session_state.pending_import
    assert batch['can_commit'], batch['errors']
    instance.checkbox(key=f'import_confirm_{batch["import_id"]}').set_value(True).run()
    instance.button(key=f'import_commit_{batch["import_id"]}').click().run()
    assert not instance.exception
    assert instance.radio(key='navigation').value == '数据中心'
    assert any(f'入库成功：{COMPANY}' in item.value for item in instance.success)
    row = table_records(instance).loc[lambda frame: frame['企业名称'] == COMPANY].iloc[0]
    assert row['接入状态'] == '已入库' and row['数据来源'] == '交易所检索'
    assert row['来源 URL'] == URL and row['新增/更新指标数量'] >= 6
    monkeypatch.setattr(online_disclosure, 'search_disclosures_with_details', lambda *a, **kw: search_result('failed'))
    instance.button(key='search_disclosures').click().run()
    assert not instance.exception
    assert any('检索失败' in item.value for item in instance.error)
    assert not any('入库成功' in item.value for item in instance.success)
    instance.radio(key='navigation').set_value('企业分析').run()
    assert COMPANY in instance.selectbox(key='enterprise_main').options
    assert COMPANY in instance.selectbox(key='enterprise_cmp').options
    instance.selectbox(key='enterprise_main').set_value(COMPANY).run()
    assert COMPANY not in instance.selectbox(key='enterprise_cmp').options
    assert instance.session_state.conversation_context['primary_company'] == COMPANY
    instance.selectbox(key='enterprise_main').set_value('比亚迪股份有限公司').run()
    assert COMPANY in instance.selectbox(key='enterprise_cmp').options
    instance.radio(key='navigation').set_value('财报问数').run()
    assert COMPANY in instance.selectbox(key='qa_main').options
    result = query(instance, '测试科技2024年的营业收入是多少？')
    assert result['status'] == 'success' and '100.00' in result['answer']
    follow = query(instance, '那上一年呢？')
    assert follow['parsed']['companies'] == [COMPANY]
    assert follow['parsed']['years'] == [2023] and follow['parsed']['metrics'] == ['revenue']
    assert '90.00' in follow['answer']
    instance.selectbox(key='qa_main').set_value(COMPANY).run()
    assert instance.session_state.conversation_context['primary_company'] == COMPANY
    code = query(instance, '688999的2024年营业收入是多少？')
    assert code['parsed']['companies'] == [COMPANY] and code['status'] == 'success'


@pytest.mark.parametrize('document_only', [False, True])
def test_direct_url_failure_or_document_only_never_claims_added(local, monkeypatch, document_only):
    def download(*args, **kwargs):
        if not document_only:
            raise ValueError('请求超时')
        return '年度报告.pdf', pdf_bytes(document_only=True)
    monkeypatch.setattr(online_disclosure, 'download_pdf', download)
    instance = data_center(app())
    instance.text_input(key='direct_pdf_url').set_value(URL).run()
    instance.button(key='prepare_pdf_url').click().run()
    assert not instance.exception
    if not document_only:
        assert any('文件下载失败' in error.value for error in instance.error)
        assert 'pending_import' not in instance.session_state
        return
    batch = instance.session_state.pending_import
    assert batch['can_save_document'], batch
    instance.checkbox(key=f'import_confirm_{batch["import_id"]}').set_value(True).run()
    instance.button(key=f'import_document_{batch["import_id"]}').click().run()
    assert not instance.exception
    assert not any('入库成功' in item.value for item in instance.success)
    row = table_records(instance).loc[lambda frame: frame['企业名称'] == COMPANY].iloc[0]
    assert row['接入状态'] == '待补充指标' and row['数据来源'] == '官方链接接入'
    instance.radio(key='navigation').set_value('企业分析').run()
    assert COMPANY not in instance.selectbox(key='enterprise_main').options
    assert COMPANY not in instance.selectbox(key='enterprise_cmp').options


def test_failed_database_write_rolls_back_and_has_visible_error(local, monkeypatch):
    monkeypatch.setattr(online_disclosure, 'download_pdf', lambda *a, **kw: ('年度报告.pdf', pdf_bytes()))
    instance = data_center(app())
    instance.text_input(key='direct_pdf_url').set_value(URL).run()
    instance.button(key='prepare_pdf_url').click().run()
    batch = instance.session_state.pending_import
    instance.checkbox(key=f'import_confirm_{batch["import_id"]}').set_value(True).run()
    def fail(*args, **kwargs):
        raise RuntimeError('模拟磁盘写入失败')
    monkeypatch.setattr(service, 'insert_report_file', fail)
    instance.button(key=f'import_commit_{batch["import_id"]}').click().run()
    assert not instance.exception
    assert any('入库失败' in item.value and '模拟磁盘写入失败' in item.value for item in instance.error)
    assert COMPANY not in companies(local)
    assert COMPANY not in set(table_records(instance)['企业名称'])
    instance.radio(key='navigation').set_value('企业分析').run()
    assert COMPANY not in instance.selectbox(key='enterprise_main').options


def test_deleted_imported_company_no_longer_selectable(local):
    with workspace_context('main', local / 'main', allow_writes=True):
        db.init_db()
        result = service.commit_import(service.prepare_import('测试.csv',
            f'公司,年份,营业收入（亿元）\n{COMPANY},2024,100'.encode()), confirmed=True)
        assert result['ok']
    instance = app()
    instance.selectbox(key='qa_main').set_value(COMPANY).run()
    original = query(instance, '测试科技2024年营业收入是多少？')
    assert original['status'] == 'success'
    data_center(instance)
    instance.selectbox(key='delete_company').set_value(COMPANY).run()
    with workspace_context('main', local / 'main'):
        cid = next(row['id'] for row in db.fetch_companies() if row['name'] == COMPANY)
    instance.checkbox(key=f'confirm_delete_company_{cid}').set_value(True).run()
    next(button for button in instance.button if button.label == '删除所选企业').click().run()
    assert not instance.exception
    assert COMPANY not in companies(local)
    assert COMPANY not in set(table_records(instance)['企业名称'])
    instance.radio(key='navigation').set_value('财报问数').run()
    assert COMPANY not in instance.selectbox(key='qa_main').options
    assert instance.session_state.conversation_context['primary_company'] != COMPANY
    assert instance.session_state.chat_messages[-1]['result']['company'] == COMPANY
    assert instance.button(key='qa_enterprise_0').disabled


def test_new_url_and_failed_download_clear_old_commit_feedback(local, monkeypatch):
    monkeypatch.setattr(online_disclosure, 'download_pdf', lambda *a, **kw: ('年度报告.pdf', pdf_bytes()))
    instance = data_center(app())
    instance.text_input(key='direct_pdf_url').set_value(URL).run()
    instance.button(key='prepare_pdf_url').click().run()
    batch = instance.session_state.pending_import
    instance.checkbox(key=f'import_confirm_{batch["import_id"]}').set_value(True).run()
    instance.button(key=f'import_commit_{batch["import_id"]}').click().run()
    assert any('入库成功' in item.value for item in instance.success)
    instance.text_input(key='direct_pdf_url').set_value(URL.replace('unified-test', 'another')).run()
    assert not any('入库成功' in item.value for item in instance.success)
    def fail(*a, **kw):
        raise ValueError('新文件下载失败')
    monkeypatch.setattr(online_disclosure, 'download_pdf', fail)
    instance.button(key='prepare_pdf_url').click().run()
    assert any('新文件下载失败' in item.value for item in instance.error)
    assert not any('入库成功' in item.value for item in instance.success)


def test_deleting_last_report_disables_historical_navigation(local):
    with workspace_context('main', local / 'main', allow_writes=True):
        db.init_db()
        result = service.commit_import(service.prepare_import('测试.csv',
            f'公司,年份,营业收入（亿元）\n{COMPANY},2024,100'.encode()), confirmed=True)
        report_id = result['report_file_id']
    instance = app()
    query(instance, '测试科技2024年营业收入是多少？')
    data_center(instance)
    instance.selectbox(key='delete_report').set_value(report_id).run()
    instance.checkbox(key=f'delete_report_confirm_{report_id}').set_value(True).run()
    instance.button(key='delete_report_button').click().run()
    assert not instance.exception
    assert COMPANY not in set(table_records(instance)['企业名称'])
    instance.radio(key='navigation').set_value('财报问数').run()
    assert COMPANY not in instance.selectbox(key='qa_main').options
    assert instance.button(key='qa_enterprise_0').disabled
    assert instance.session_state.chat_messages[-1]['result']['data_snapshot'][COMPANY][0]['revenue'] == 100
