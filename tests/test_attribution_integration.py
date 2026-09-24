"""Attribution must remain additive to the existing safe query pipeline."""
from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.analysis import rows_to_df
from core.db import fetch_companies, fetch_company_metrics, fetch_metric_sources, init_db, upsert_company, upsert_metric
from core.qa_engine import answer_question
from core.seed import seed_sample_data
from core.storage import workspace_context

BYD = '比亚迪股份有限公司'
CATL = '宁德时代新能源科技股份有限公司'
APP = Path(__file__).resolve().parents[1] / 'app' / 'main.py'


def company_frame(company_id):
    frame = rows_to_df(fetch_company_metrics(company_id))
    sources = [dict(row) for row in fetch_metric_sources(company_id)]
    frame['metric_provenance'] = [{source['metric']: source for source in sources if source['year'] == year}
                                 for year in frame['year']]
    return frame


@pytest.fixture
def data(tmp_path):
    with workspace_context('evaluation', tmp_path / 'library', allow_writes=True):
        init_db()
        seed_sample_data()
        yield {row['name']: company_frame(row['id']) for row in fetch_companies()}


def ask(data, question, **kwargs):
    return answer_question(question, BYD, CATL, list(data), data, **kwargs)


@pytest.mark.parametrize('question', [
    '比亚迪2024年净利润为什么变化？',
    '比亚迪2024年净利润增长的主要原因是什么？',
    '比亚迪2024年净利润为何增长？',
    '比亚迪2024年净利润归因分析',
    '比亚迪2024年净利润怎么解释？',
    '比亚迪2024年净利润变化受业务因素影响的原因是什么？',
])
def test_explanation_wording_uses_verified_comparison(data, question):
    result = ask(data, question)
    assert result['attribution']['status'] == 'success'
    assert result['sql_result']['status'] == 'success'
    assert result['parsed']['years'] == [2024]
    assert {row['year'] for row in result['sql_result']['rows']} == {2024}
    assert '300.41' in result['answer'] and '402.54' in result['answer']
    assert '+34.00%' in result['answer']
    assert '仅有一个年度' not in result['answer']
    assert '缺少相邻年度' not in result['answer']
    assert '反映归属于上市公司股东' not in result['answer']


@pytest.mark.parametrize('question', ['净利润是什么？', '净利润是什么意思？'])
def test_plain_definition_stays_definition(data, question):
    result = ask(data, question)
    assert result['parsed']['intent'] == 'metric_explain'
    assert result['attribution'] is None


@pytest.mark.parametrize('previous,current', [(0, 10), (-10, -5), (10, 0), (10, 10)])
def test_summary_matches_tree_for_nonstandard_bases(data, previous, current):
    company_id = next(row['id'] for row in fetch_companies() if row['name'] == BYD)
    upsert_metric(company_id, {'year': 2023, 'net_profit': previous, 'raw_source': 'regression'})
    upsert_metric(company_id, {'year': 2024, 'net_profit': current, 'raw_source': 'regression'})
    data[BYD] = company_frame(company_id)
    result = ask(data, '比亚迪2024年净利润为什么变化？')
    root = result['attribution']['root']
    assert root['delta'] == current - previous
    assert root['growth_label'] in result['answer']
    assert '仅有一个年度' not in result['answer']


@pytest.mark.parametrize('question', [
    '比亚迪2024年净利润为什么变化？', '为什么比亚迪2024年净利润增长？',
    '2024年净利润下降的原因是什么？', '净利润变化的主要原因是什么？',
    '比亚迪2024年报告中的净利润为什么变化？',
])
def test_profit_explanation_attaches_tree_without_changing_main_scope(data, question):
    result = ask(data, question)
    assert result['status'] == 'success'
    assert result['query_plan']['route'] == 'hybrid'
    tree = result['attribution']
    assert tree['status'] == 'success'
    assert tree['company'] == BYD
    assert tree['current_year'] - tree['previous_year'] == 1
    if '2024' in question:
        assert (tree['previous_year'], tree['current_year']) == (2023, 2024)
        assert result['parsed']['years'] == [2024]
        assert tree['root']['current_value'] == pytest.approx(402.54)
        assert tree['root']['previous_value'] == pytest.approx(300.41)
    assert tree['sql_trace']
    assert '不代表严格因果贡献率' in result['answer']
    assert any(step['智能体'] == '财务归因' and step['状态'] == '已完成' for step in result['agent_trace'])


@pytest.mark.parametrize('question', [
    '比亚迪2024年净利润是多少？', '比亚迪2024年营业收入为什么增长？',
    '净利润是什么意思？', '比亚迪和宁德时代2024年净利润为什么变化？',
    '比亚迪2024年利润为什么增长，明天该不该买？',
])
def test_other_queries_do_not_run_attribution(data, monkeypatch, question):
    def forbidden(*args, **kwargs):
        pytest.fail('This query must not invoke the attribution builder')
    monkeypatch.setattr('core.qa_engine.build_net_profit_attribution', forbidden)
    result = ask(data, question)
    assert result['attribution'] is None


@pytest.mark.parametrize('question,status', [
    ('贵州茅台2024年净利润为什么变化？', 'unknown_company'),
    ('比亚迪2024年上半年净利润为什么变化？', 'unsupported_period'),
    ('比亚迪2019年净利润为什么变化？', 'missing_scope'),
])
def test_scope_blocks_precede_all_attribution_queries(data, monkeypatch, question, status):
    def forbidden(*args, **kwargs):
        pytest.fail('A blocked question must not invoke the attribution builder')
    monkeypatch.setattr('core.qa_engine.build_net_profit_attribution', forbidden)
    result = ask(data, question)
    assert result['status'] == status
    assert result['attribution'] is None


def test_component_exception_preserves_main_answer_and_real_failure_trace(data, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic isolated component outage')
    monkeypatch.setattr('core.qa_engine.build_net_profit_attribution', fail)
    result = ask(data, '比亚迪2024年净利润为什么变化？')
    assert result['status'] == 'success' and result['answer']
    assert result['sql_result']['status'] == 'success'
    assert result['attribution'] is None
    step = next(step for step in result['agent_trace'] if step['智能体'] == '财务归因')
    assert step['状态'] == '已降级'
    assert 'RuntimeError' in step['原因']


def test_explicit_range_not_reduced_to_arbitrary_attribution_pair(data):
    result = ask(data, '比亚迪2023年至2025年净利润为什么变化？')
    assert result['status'] == 'success'
    assert result['parsed']['years'] == [2023, 2024, 2025]
    assert {row['year'] for row in result['sql_result']['rows']} == {2023, 2024, 2025}
    assert result['attribution']['status'] == 'insufficient'


def test_cloud_cannot_change_attribution_numbers_or_invent_period(data, monkeypatch):
    question = '比亚迪2024年净利润为什么变化？'
    baseline = ask(data, question)['attribution']
    monkeypatch.setattr('core.qa_engine.llm_enabled', lambda _config: True)
    monkeypatch.setattr('core.qa_engine.parse_question_with_llm', lambda *args: {
        'intent': 'trend_analysis', 'companies': [CATL], 'years': [2025], 'metrics': ['revenue']})
    monkeypatch.setattr('core.qa_engine.polish_answer_with_llm', lambda *args: '净利润增长至999999亿元。')
    result = ask(data, question, llm_config={'api_key': 'test-only'})
    assert result['parsed']['companies'] == [BYD] and result['parsed']['years'] == [2024]
    assert result['attribution']['root'] == baseline['root']
    assert result['attribution']['drivers'] == baseline['drivers']
    assert '999999' not in result['answer']
    assert result['answer'] == result['draft']


def test_attribution_uses_each_metric_source_after_partial_update(data):
    company_id = next(row['id'] for row in fetch_companies() if row['name'] == BYD)
    upsert_metric(company_id, {'year': 2024, 'net_profit': 402.54,
        '_provenance': {'net_profit': {'file_name': 'profit-original.csv', 'cell': 'C3',
                                      'raw_unit': '亿元', 'import_id': 'profit-v1'}}})
    upsert_metric(company_id, {'year': 2024, 'revenue': 7771.02, 'raw_source': 'revenue-update.csv',
        '_provenance': {'revenue': {'file_name': 'revenue-update.csv', 'cell': 'B3',
                                   'raw_unit': '亿元', 'import_id': 'revenue-v2'}}})
    data[BYD] = company_frame(company_id)
    tree = ask(data, '比亚迪2024年净利润为什么变化？')['attribution']
    profit = next(source for source in tree['root']['quantitative_sources'] if source['year'] == 2024)
    revenue_node = next(node for node in tree['drivers'] if node['metric'] == 'revenue')
    revenue = next(source for source in revenue_node['quantitative_sources'] if source['year'] == 2024)
    assert profit['metric_source']['file_name'] == 'profit-original.csv'
    assert profit['metric_source']['import_id'] == 'profit-v1'
    assert revenue['metric_source']['file_name'] == 'revenue-update.csv'


def test_stale_metric_provenance_is_not_attached_to_different_sql_value(data):
    from core.qa_engine import _attach_attribution_provenance
    tree = {'root': {'metric': 'net_profit', 'previous_year': 2023, 'current_year': 2024,
                    'previous_value': 300.41, 'current_value': 999,
                    'quantitative_sources': [{'year': 2024, 'raw_source': 'unverified row'}]}}
    _attach_attribution_provenance(tree, data[BYD])
    assert tree['root']['quantitative_sources'][0]['metric_source'] is None


def test_history_keeps_attribution_snapshot_after_live_database_update(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'local')
    monkeypatch.setenv('FINANCIAL_WORKSPACE', 'main')
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    company = '归因快照股份有限公司'
    library = tmp_path / 'main'
    with workspace_context('main', library, allow_writes=True):
        init_db()
        company_id = upsert_company(company)
        for year, profit in [(2023, 10), (2024, 12)]:
            upsert_metric(company_id, {'year': year, 'net_profit': profit, 'revenue': 100,
                                      'gross_margin': 20, 'operating_cashflow': 25})

    app = AppTest.from_file(APP, default_timeout=45).run()
    assert not app.exception

    def submit():
        app.text_area(key='qa_input').set_value(company + '2024年净利润为什么变化？')
        next(button for button in app.button if button.label == '开始分析').click().run()
        assert not app.exception
        return app.session_state.chat_messages[-1]['result']['attribution']

    first = deepcopy(submit())
    session_id = app.session_state.active_session_id
    assert first['root']['current_value'] == 12
    assert any(expander.label == '数因融合财务归因' for expander in app.expander)
    with workspace_context('main', library, allow_writes=True):
        upsert_metric(company_id, {'year': 2024, 'net_profit': 99})
    app.run()
    assert not app.exception
    assert app.session_state.chat_messages[-1]['result']['attribution'] == first
    app.button(key='new_session').click().run()
    assert submit()['root']['current_value'] == 99
    app.button(key=f'session_{session_id}').click().run()
    assert not app.exception
    assert app.session_state.chat_messages[-1]['result']['attribution'] == first
