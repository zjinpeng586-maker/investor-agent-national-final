"""Public writes must be real and confined to the current visitor library."""
from uuid import uuid4
import pytest
from core import db, service
from core.storage import (configure_workspace, configure_public_session, get_data_root,
    workspace_context, can_write, resolve_stored_file, check_session_upload_capacity)
from core.qa_engine import answer_question, KEYWORDS
from core.conversation import resolve_turn, new_conversation_context
from core.analysis import rows_to_df


@pytest.fixture
def public(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'public')
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    configure_workspace('main')
    yield tmp_path
    configure_workspace('main')


def test_sessions_are_isolated_and_real_import_feeds_query(public):
    first, second = uuid4().hex, uuid4().hex
    root = configure_public_session(first)
    db.init_db()
    batch = service.prepare_import('财务.csv', ('公司,年份,营业收入（亿元）,归母净利润（亿元）\n'
        '测试科技股份有限公司,2024,123,12\n测试科技股份有限公司,2023,100,10').encode())
    assert service.commit_import(batch, confirmed=True)['ok']
    report = db.fetch_report_files()[0]
    path = report['file_path']
    assert resolve_stored_file(path)
    rows = db.fetch_companies()
    data = {r['name']: rows_to_df(db.fetch_company_metrics(r['id'])) for r in rows}
    result = answer_question('测试科技2024年净利润为什么变化？', rows[0]['name'], None, list(data), data)
    assert result['status'] == 'success' and result['attribution']['root']['current_value'] == 12
    configure_public_session(second)
    db.init_db()
    assert not db.fetch_companies() and resolve_stored_file(path) is None
    configure_public_session(first)
    assert get_data_root() == root and db.fetch_companies()
    with workspace_context('evaluation', public / 'evaluation', allow_writes=True):
        db.init_db()
        assert not db.fetch_companies()
    assert get_data_root() == root and can_write()
    configure_workspace('main')
    assert not can_write() and not (public / 'main/investor_agent.db').exists()


@pytest.mark.parametrize('identifier', ['../main', '', 'main', 'a'*31, 'G'*32])
def test_session_identifier_rejects_user_paths(public, identifier):
    with pytest.raises(ValueError):
        configure_public_session(identifier)
    assert not can_write()


def test_public_session_never_uses_legacy_overrides(public, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', public / 'secret.db')
    monkeypatch.setattr(service, 'UPLOAD_DIR', public / 'secret_uploads')
    root = configure_public_session(uuid4().hex)
    assert db.get_db_path() == root / 'investor_agent.db'
    assert service._upload_dir() == root / 'uploads'
    with pytest.raises(ValueError, match='100 MB'):
        check_session_upload_capacity(101 * 1024 * 1024)


@pytest.mark.parametrize('phrase,metrics', [
    ('核心财务指标', list(KEYWORDS)), ('关键财务指标', list(KEYWORDS)),
    ('全部财务指标', list(KEYWORDS)), ('财务数据一览', list(KEYWORDS)),
    ('财务指标总览', list(KEYWORDS)), ('营业收入', ['revenue']),
    ('净资产回报率', ['roe']), ('经营净现金', ['operating_cashflow']),
    ('每股盈利', ['eps']),
])
def test_expanded_offline_questions_and_followups(public, phrase, metrics):
    from core.seed import seed_sample_data
    configure_public_session(uuid4().hex)
    db.init_db()
    seed_sample_data()
    data = {r['name']: rows_to_df(db.fetch_company_metrics(r['id'])) for r in db.fetch_companies()}
    company = '比亚迪股份有限公司'
    q = f'比亚迪2024年{phrase}是多少？'
    turn = resolve_turn(new_conversation_context(), q, list(data), page_company=company)
    result = answer_question(q, company, None, list(data), data, resolved_context=turn['resolved'])
    assert result['status'] == 'success'
    assert result['parsed']['metrics'] == metrics
    assert result['sql_result']['status'] == 'success'
    followup = resolve_turn(turn['context'], '那2023年呢？', list(data), page_company=company)
    assert followup['resolved']['metrics'] == metrics
    assert followup['resolved']['years'] == [2023]


def test_public_disclosure_widgets_commit_then_query_and_isolate(public, monkeypatch):
    from core import online_disclosure
    from test_unified_ui import app, data_center, search_result, pdf_bytes, query, COMPANY
    monkeypatch.setattr(online_disclosure, 'search_disclosures_with_details', lambda *a, **k: search_result())
    monkeypatch.setattr(online_disclosure, 'download_pdf', lambda *a, **k: ('年度报告.pdf', pdf_bytes()))
    first = data_center(app())
    session = first.session_state['_public_database_session']
    first.button(key='search_disclosures').click().run()
    first.button(key='prepare_disclosure').click().run()
    batch = first.session_state.pending_import
    assert batch['can_commit']
    first.checkbox(key=f'import_confirm_{batch["import_id"]}').set_value(True).run()
    first.button(key=f'import_commit_{batch["import_id"]}').click().run()
    assert not first.exception
    first.radio(key='navigation').set_value('企业分析').run()
    assert COMPANY in first.selectbox(key='enterprise_main').options
    first.radio(key='navigation').set_value('财报问数').run()
    result = query(first, '测试科技2024年营业收入是多少？')
    assert result['status'] == 'success' and '100.00' in result['answer']
    result = query(first, '测试科技2024年净利润为什么变化？')
    assert result['attribution']['status'] == 'success'
    assert first.session_state['_public_database_session'] == session
    second = app()
    assert second.session_state['_public_database_session'] != session
    assert COMPANY not in second.selectbox(key='qa_main').options
    assert not (public / 'main/investor_agent.db').exists()


def test_explicit_readonly_mode_cannot_bind_writable_session(public, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'readonly')
    with pytest.raises(ValueError):
        configure_public_session(uuid4().hex)
    assert not can_write()
