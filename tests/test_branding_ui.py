"""Brand identity, graceful asset fallback and factual public status messages."""
import base64
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import branding
from app.messages import NO_METRICS, NO_REPORTS, public_message, public_details

PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'local')
    monkeypatch.setenv('FINANCIAL_WORKSPACE', 'main')
    return tmp_path


def visible(app):
    return '\n'.join(str(item.value) for name in ('markdown', 'caption', 'text', 'info', 'success', 'warning', 'error') for item in getattr(app, name))


def test_supplied_logo_is_embedded_locally_without_transform():
    uri = branding.logo_data_uri()
    assert uri.startswith('data:image/png;base64,')
    assert base64.b64decode(uri.split(',', 1)[1]) == branding.BRAND_LOGO_PATH.read_bytes()
    html = branding.brand_html()
    assert 'alt="擎梦数智 Logo"' in html
    assert '财报智问 · 上市公司财务智能分析平台' in html
    assert 'https://' not in html and 'http://' not in html
    assert branding.page_icon() == uri
    assert branding.PAGE_TITLE == '擎梦数智｜财报智问'


def test_missing_logo_has_text_only_component(tmp_path):
    missing = tmp_path / 'missing.png'
    assert branding.page_icon(missing) is None
    html = branding.brand_html(missing)
    assert '擎梦数智' in html and '上市公司财务智能分析平台' in html
    assert '<img' not in html and '▥' not in html


@pytest.mark.parametrize('missing_logo', [False, True])
def test_all_pages_show_brand_without_old_footer(isolated, monkeypatch, missing_logo):
    if missing_logo:
        monkeypatch.setattr(branding, 'BRAND_LOGO_PATH', isolated / 'missing.png')
    app = AppTest.from_file(PROJECT / 'app/main.py', default_timeout=40).run()
    for page in ['财报问数', '企业分析', '研究资料库', '数据中心', '评测中心']:
        app.radio(key='navigation').set_value(page).run()
        assert not app.exception
        text = visible(app)
        assert '擎梦数智' in text
        assert '财报智问 · 上市公司财务智能分析平台' in text
        for old in ['星月版', '星月界面', '上市公司研究工作台', '演示资料库', '个人资料库', '展示版', '比赛版', 'Demo', '🌙', '▥', 'fu-side-bottom', 'fu-user', 'fu-sky']:
            assert old not in text
        if missing_logo:
            assert 'class="fu-brand-logo"' not in '\n'.join(item.value for item in app.markdown if '<div class="fu-brand">' in item.value)
    assert '暂无历史会话' in text


def test_empty_library_remains_empty_with_formal_guidance(isolated, monkeypatch):
    monkeypatch.setattr('core.seed.seed_sample_data', lambda: None)
    app = AppTest.from_file(PROJECT / 'app/main.py', default_timeout=40).run()
    assert not app.exception and NO_METRICS in visible(app)
    assert not app.session_state.chat_messages and not app.session_state.saved_sessions
    assert app.session_state.selected_main is None
    app.radio(key='navigation').set_value('研究资料库').run()
    assert NO_REPORTS in visible(app)
    assert not app.dataframe
    app.radio(key='navigation').set_value('数据中心').run()
    assert [tab.label for tab in app.tabs] == ['本地文件接入', '公开披露接入', '数据资产与来源']
    assert not app.success


def test_reappearing_company_selectors_explicitly_restore_browser_value(isolated):
    app = AppTest.from_file(PROJECT / 'app/main.py', default_timeout=40).run()
    company = app.selectbox(key='qa_main').value
    assert company != app.selectbox(key='qa_main').options[0]
    for page in ['数据中心', '研究资料库', '评测中心', '企业分析']:
        app.radio(key='navigation').set_value(page).run()
        assert not app.exception
    widget = app.selectbox(key='enterprise_main')
    assert widget.value == company and widget.proto.set_value
    app.radio(key='navigation').set_value('财报问数').run()
    widget = app.selectbox(key='qa_main')
    assert widget.value == company and widget.proto.set_value


@pytest.mark.parametrize('value', [r'数据库写入失败 C:\Users\person\secret\db.sqlite', '无法读取 /Users/person/private/db.sqlite'])
def test_local_paths_are_redacted_from_public_diagnostics(value):
    result = public_message(value)
    assert 'person' not in result and '[本地文件]' in result
    assert public_details({'requests': [{'error': value}]})['requests'][0]['error'] == result


def test_traceback_has_actionable_fallback():
    assert public_message('Traceback (most recent call last):\nsecret code', '解析失败，请检查文件。') == '解析失败，请检查文件。'
    assert public_details({'error': '', 'rows': 0, 'ok': False}) == {'error': '', 'rows': 0, 'ok': False}


@pytest.mark.parametrize('status,ok,committed,expected,not_expected', [
    ('success', True, True, '本次新增 3 项、更新 2 项', '入库失败'),
    ('document_only', False, True, '待补充指标', '入库成功'),
    ('failed', False, False, '本次操作未写入可用分析数据', '入库成功'),
    ('verification_failed', False, True, '数据已提交，但入库结果复核未完成', '本次操作未写入'),
])
def test_commit_messages_follow_transaction_result(isolated, status, ok, committed, expected, not_expected):
    code = '''import streamlit as st
from app.main import render_import_result
render_import_result()
'''
    app = AppTest.from_string(code)
    app.session_state.import_result = dict(status=status, ok=ok, committed=committed,
        company_names=['核验企业'], inserted_metrics=3, updated_metrics=2,
        error_message=r'写入检查失败 C:\Users\person\secret\db.sqlite')
    app.run()
    assert not app.exception
    text = visible(app)
    assert expected in text and not_expected not in text and 'person' not in text
