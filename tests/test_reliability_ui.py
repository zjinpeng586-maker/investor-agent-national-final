"""Safety and integrity flows through real Streamlit widgets in isolated libraries."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import db
from core.service import prepare_import, commit_import
from core.storage import workspace_context

APP_PATH = Path(__file__).resolve().parents[1] / 'app/main.py'


@pytest.fixture
def local_environment(monkeypatch, tmp_path):
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'local')
    monkeypatch.setenv('FINANCIAL_WORKSPACE', 'main')
    return tmp_path


@pytest.fixture
def public_environment(monkeypatch, tmp_path):
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('FINANCIAL_DEPLOYMENT', raising=False)
    monkeypatch.setenv('FINANCIAL_WORKSPACE', 'main')
    return tmp_path


def _app():
    app = AppTest.from_file(APP_PATH, default_timeout=35).run()
    assert not app.exception
    return app


def _label(widgets, label):
    return next(widget for widget in widgets if widget.label == label)


def _text(app):
    return '\n'.join(str(item.value) for item in [*app.markdown, *app.text, *app.caption, *app.info, *app.warning, *app.error])


def _rows(root, workspace='main'):
    with workspace_context(workspace, root / workspace):
        companies = {row['name']: row['id'] for row in db.fetch_companies()}
        return {name: [dict(row) for row in db.fetch_company_metrics(cid)] for name, cid in companies.items()}


def _import(root, content, *, name='测试数据.csv'):
    with workspace_context('main', root / 'main', allow_writes=True):
        db.init_db()
        batch = prepare_import(name, content)
        assert batch['can_commit'], batch['errors']
        result = commit_import(batch, confirmed=True)
        assert result['ok'], result
        return result


def _inject_online_preview(app, batch):
    # Use a prepared in-memory batch; these UI tests deliberately do not download.
    # First establish the search controls; their initial change clears old candidates.
    app.radio(key='navigation').set_value('数据中心').run()
    assert not app.exception
    batch['_ui_channel'] = 'online'
    batch['_ui_origin'] = 'candidate'
    app.session_state.pending_import = batch
    app.run()
    assert not app.exception


def _submit(app, question):
    app.text_area(key='qa_input').set_value(question)
    _label(app.button, '开始分析').click().run()
    assert not app.exception
    assert app.session_state.chat_messages
    return app.session_state.chat_messages[-1]['result']


def test_public_default_import_controls_use_an_isolated_session(public_environment):
    app = _app()
    assert [widget.key for widget in app.radio] == ['navigation']
    app.radio(key='navigation').set_value('数据中心').run()
    assert not app.exception
    assert '在线会话资料库' in _text(app)
    assert app.get('file_uploader')
    assert any(button.key == 'prepare_pdf_url' for button in app.button)
    session = app.session_state['_public_database_session']
    assert (public_environment / 'sessions' / session / 'investor_agent.db').exists()
    assert not (public_environment / 'main/investor_agent.db').exists()
    app.run()
    assert app.session_state['_public_database_session'] == session


def test_public_mode_cannot_select_internal_evaluation_database(public_environment, monkeypatch):
    monkeypatch.setenv('FINANCIAL_WORKSPACE', 'evaluation')
    app = _app()
    app.radio(key='navigation').set_value('数据中心').run()
    assert not app.exception
    assert app.get('file_uploader')
    session = app.session_state['_public_database_session']
    assert (public_environment / 'sessions' / session / 'investor_agent.db').exists()
    assert not (public_environment / 'main/investor_agent.db').exists()
    assert not (public_environment / 'evaluation/investor_agent.db').exists()


def test_local_library_has_builtin_data_and_upload_is_available(local_environment):
    app = _app()
    assert _rows(local_environment)
    assert app.text_area
    assert [widget.key for widget in app.radio] == ['navigation']
    app.radio(key='navigation').set_value('数据中心').run()
    assert not app.exception
    assert app.get('file_uploader')
    assert app.radio(key='navigation').value == '数据中心'


def test_one_library_keeps_session_and_exposes_imported_and_builtin_companies(local_environment):
    app = _app()
    app.radio(key='navigation').set_value('财报问数').run()
    assert not app.exception
    _submit(app, '比亚迪2024年营业收入是多少？')
    session_id = app.session_state.active_session_id
    assert len(app.session_state.chat_messages) == 1
    builtin_options = set(app.selectbox(key='qa_main').options)
    _import(local_environment, '公司,年份,营业收入（亿元）\n独立测试企业,2024,123'.encode())
    app.radio(key='navigation').set_value('财报问数').run()
    assert set(app.selectbox(key='qa_main').options) == builtin_options | {'独立测试企业'}
    assert app.session_state.active_session_id == session_id
    assert len(app.session_state.chat_messages) == 1
    app.selectbox(key='qa_main').set_value('独立测试企业').run()
    assert app.session_state.conversation_context['primary_company'] == '独立测试企业'
    _submit(app, '独立测试企业2024年营业收入是多少？')
    app.radio(key='navigation').set_value('企业分析').run()
    assert not app.exception
    assert '独立测试企业' in app.selectbox(key='enterprise_main').options
    app.radio(key='navigation').set_value('财报问数').run()
    assert app.session_state.active_session_id == session_id
    assert len(app.session_state.chat_messages) == 2
    assert '比亚迪' in app.session_state.chat_messages[0]['question']
    assert '独立测试企业' in app.session_state.chat_messages[1]['question']


def test_empty_library_uses_generic_guidance(local_environment, monkeypatch):
    monkeypatch.setattr('core.seed.seed_sample_data', lambda: None)
    app = _app()
    assert '当前资料库暂无可用于分析的年度财务指标，请前往数据中心完成数据接入与复核。' in _text(app)
    assert _rows(local_environment) == {}
    assert not app.text_area
    _label(app.button, '前往数据中心').click().run()
    assert app.get('file_uploader')


def test_import_preview_writes_nothing_until_confirm_checkbox_and_commit(local_environment):
    app = _app()
    before = _rows(local_environment)
    batch = prepare_import('预览.csv', '公司,年份,营业收入（元）,归母净利润（亿元）\n预览企业,2024,10000000000,10'.encode())
    assert batch['can_commit'], batch['errors']
    _inject_online_preview(app, batch)
    commit_key = f'import_commit_{batch["import_id"]}'
    assert app.button(key=commit_key).disabled
    assert _rows(local_environment) == before
    assert not list((local_environment / 'main').rglob('*.csv'))
    assert any('企业' in table.value.columns and '年度' in table.value.columns for table in app.dataframe)
    app.checkbox(key=f'import_confirm_{batch["import_id"]}').set_value(True).run()
    assert not app.exception
    assert not app.button(key=commit_key).disabled
    assert _rows(local_environment) == before
    app.button(key=commit_key).click().run()
    assert not app.exception
    data = _rows(local_environment)
    assert data['预览企业'][0]['revenue'] == 100
    assert data['预览企业'][0]['net_profit'] == 10
    assert 'pending_import' not in app.session_state
    assert any('入库成功' in success.value for success in app.success)
    assert len(list((local_environment / 'main').rglob('*.csv'))) == 1


def test_invalid_import_stays_disabled_after_user_confirms(local_environment):
    app = _app()
    before = _rows(local_environment)
    batch = prepare_import('未知单位.csv', '公司,年份,营业收入\n甲,2024,100'.encode())
    assert not batch['can_commit']
    _inject_online_preview(app, batch)
    assert any('金额单位未知' in error.value for error in app.error)
    app.checkbox(key=f'import_confirm_{batch["import_id"]}').set_value(True).run()
    assert not app.exception
    assert app.button(key=f'import_commit_{batch["import_id"]}').disabled
    assert _rows(local_environment) == before
    assert not list((local_environment / 'main').rglob('*.csv'))


def test_revenue_only_enterprise_has_no_default_score_or_radar(local_environment):
    _import(local_environment, '公司,年份,营业收入（亿元）\n仅收入企业,2023,80\n仅收入企业,2024,100'.encode())
    app = _app()
    app.radio(key='navigation').set_value('企业分析').run()
    assert not app.exception
    app.selectbox(key='enterprise_main').set_value('仅收入企业').run()
    assert not app.exception
    score = _label(app.metric, '综合评分')
    assert score.value == '数据不足'
    visible = _text(app)
    assert 'None' not in visible
    assert '数据不足，无法完整评估风险' in visible
    assert not any('🟢' in expander.label for expander in app.expander)
    assert '38分' not in visible
    charts = [json.loads(chart.proto.spec) for chart in app.get('plotly_chart')]
    assert charts
    assert not any(trace['type'] == 'scatterpolar' for chart in charts for trace in chart.get('data', []))


@pytest.mark.parametrize(('question', 'status'), [
    ('贵州茅台2024年营业收入是多少？', 'unknown_company'),
    ('比亚迪2024年上半年营业收入是多少？', 'unsupported_period'),
    ('比亚迪2099年有哪些风险？', 'missing_scope'),
    ('比亚迪2099年生成分析报告', 'missing_scope'),
])
def test_invalid_scope_has_no_charts_or_report_actions(public_environment, question, status):
    app = _app()
    result = _submit(app, question)
    assert result['status'] == status
    assert result.get('report_text') is None
    assert result.get('chart') is None
    assert not app.get('plotly_chart')
    assert not any((button.key or '').startswith('qa_report_') for button in app.button)
    assert not app.download_button
    assert result['sql_result']['status'] == 'not_applicable'
    assert '7771.02' not in result['answer']
    assert '相对平稳' not in result['answer']


def test_library_original_pdf_can_be_downloaded_and_paged(local_environment):
    fixture = Path(__file__).parent / 'fixtures/byd_2024_key_financials.pdf'
    _import(local_environment, fixture.read_bytes(), name=fixture.name)
    app = _app()
    app.radio(key='navigation').set_value('研究资料库').run()
    assert not app.exception
    choices = _label(app.selectbox, '选择资料')
    choices.set_value(next(value for value in choices.options if fixture.name in value)).run()
    assert not app.exception
    downloads = [button for button in app.download_button if button.label == '下载原始文件']
    assert downloads and downloads[0].proto.url
    viewer = next(button for button in app.button if button.label == '查看原文第 1 页')
    viewer.click().run()
    assert not app.exception
    page = _label(app.number_input, '原文页码')
    assert page.value == 1
    assert page.max == 2
    assert app.get('image') and app.get('image')[0].proto.imgs[0].url
    page.set_value(2).run()
    assert not app.exception
    assert 'PDF第 2 页' in app.get('image')[0].proto.imgs[0].caption
