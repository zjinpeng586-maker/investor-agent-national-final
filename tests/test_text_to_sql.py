from pathlib import Path
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from core.conversation import new_conversation_context, resolve_turn
from core.analysis import rows_to_df
from core.db import DB_PATH, fetch_companies, fetch_company_metrics, init_db
from core.qa_engine import answer_question
from core.seed import seed_sample_data
from core.text_to_sql import (
    SQLPlan,
    execute_readonly_sql,
    generate_sql,
    get_readonly_connection,
    run_text_to_sql,
    validate_sql,
)


BYD = '比亚迪股份有限公司'
CATL = '宁德时代新能源科技股份有限公司'
APP_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'


@pytest.fixture(scope='module', autouse=True)
def seeded_database():
    init_db()
    seed_sample_data()


def _context(intent='finance_query', companies=None, years=None, metrics=None):
    return {
        'intent': intent,
        'companies': companies or [BYD],
        'years': years or [],
        'metrics': metrics or [],
    }


def test_single_year_revenue_and_profit_match_database():
    companies = {row['name']: row['id'] for row in fetch_companies()}
    expected = {row['year']: dict(row) for row in fetch_company_metrics(companies[BYD])}[2024]
    for metric in ['revenue', 'net_profit']:
        result = run_text_to_sql(_context(years=[2024], metrics=[metric]))
        assert result['status'] == 'success'
        assert result['params'] == [BYD, 2024]
        assert result['rows'][0][metric] == expected[metric]


def test_multi_metric_trend_and_company_comparison_queries():
    multi = run_text_to_sql(_context(
        years=[2024], metrics=['revenue', 'net_profit', 'operating_cashflow']
    ))
    assert multi['status'] == 'success'
    assert {'revenue', 'net_profit', 'operating_cashflow'}.issubset(multi['columns'])

    trend = run_text_to_sql(_context(intent='trend_analysis', metrics=['revenue']))
    assert trend['status'] == 'success'
    assert trend['row_count'] == 3
    assert sorted(row['year'] for row in trend['rows']) == [2023, 2024, 2025]

    comparison = run_text_to_sql(_context(
        intent='company_compare', companies=[BYD, CATL], years=[2024], metrics=[]
    ))
    assert comparison['status'] == 'success'
    assert {row['company'] for row in comparison['rows']} == {BYD, CATL}
    assert {row['year'] for row in comparison['rows']} == {2024}


def test_multi_metric_answer_is_grounded_in_sql_rows():
    company_rows = fetch_companies()
    data_map = {row['name']: rows_to_df(fetch_company_metrics(row['id'])) for row in company_rows}
    resolved = _context(years=[2024], metrics=['revenue', 'net_profit', 'operating_cashflow'])
    result = answer_question(
        '比亚迪2024年的营业收入、净利润和经营现金流是多少？',
        BYD, CATL, list(data_map), data_map, resolved_context=resolved,
    )
    assert result['sql_status'] == 'success'
    assert '营业收入' in result['answer']
    assert '归母净利润' in result['answer']
    assert '经营现金流' in result['answer']


def test_phase2_resolved_context_drives_sql():
    companies = [BYD, CATL]
    first = resolve_turn(
        new_conversation_context(), '比亚迪2024年营业收入是多少？', companies,
        page_company=BYD, page_compare=CATL,
    )
    second = resolve_turn(first['context'], '那2023年呢？', companies, page_company=BYD, page_compare=CATL)
    third = resolve_turn(second['context'], '宁德时代呢？', companies, page_company=BYD, page_compare=CATL)
    result = run_text_to_sql(third['resolved'])
    assert result['status'] == 'success'
    assert result['params'] == [CATL, 2023]
    assert result['rows'][0]['company'] == CATL
    assert result['rows'][0]['year'] == 2023
    assert 'revenue' in result['columns']


@pytest.mark.parametrize('statement', [
    'INSERT INTO companies(name) VALUES (?)',
    'UPDATE companies SET name=?',
    'DELETE FROM companies',
    'DROP TABLE companies',
    'PRAGMA table_info(companies)',
    "ATTACH DATABASE 'other.db' AS other",
])
def test_write_and_admin_statements_are_rejected(statement):
    result = validate_sql(statement, ['x'] if '?' in statement else [])
    assert result['valid'] is False


def test_multiple_statements_system_and_non_whitelist_tables_are_rejected():
    assert validate_sql('SELECT * FROM companies; DELETE FROM companies', [])['valid'] is False
    assert validate_sql('SELECT * FROM sqlite_master', [])['valid'] is False
    assert validate_sql('SELECT * FROM secrets', [])['valid'] is False


def test_parameter_count_and_safe_with_select_validation():
    assert validate_sql('SELECT * FROM companies WHERE name=?', [])['valid'] is False
    cte = 'WITH picked AS (SELECT id, name FROM companies WHERE name=?) SELECT name FROM picked;'
    assert validate_sql(cte, [BYD])['valid'] is True
    assert execute_readonly_sql(cte, [BYD])['rows'][0]['name'] == BYD


def test_readonly_connection_cannot_write():
    connection = get_readonly_connection()
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("INSERT INTO companies(name) VALUES ('禁止写入')")
    finally:
        connection.close()
    assert DB_PATH.exists()


def test_invalid_sql_is_repaired_once_and_revalidated():
    broken = SQLPlan(
        'SELECT fm.missing_column FROM financial_metrics fm JOIN companies c ON c.id=fm.company_id '
        'WHERE c.name=? AND fm.year=?',
        [BYD, 2024],
        'test_invalid',
    )
    result = run_text_to_sql(_context(years=[2024], metrics=['revenue']), broken)
    assert result['status'] == 'success'
    assert result['corrected'] is True
    assert len(result['attempts']) == 2
    assert result['attempts'][0]['status'] == 'failed'
    assert result['attempts'][1]['status'] == 'success'
    assert result['attempts'][1]['safety_status'] == 'passed'


def test_rejected_sql_is_rebuilt_safely_and_empty_result_is_detected():
    unsafe = SQLPlan('DROP TABLE companies', [], 'test_unsafe')
    repaired = run_text_to_sql(_context(years=[2024], metrics=['revenue']), unsafe)
    assert repaired['status'] == 'success'
    assert repaired['attempts'][0]['safety_status'] == 'rejected'
    assert repaired['attempts'][1]['safety_status'] == 'passed'

    empty = run_text_to_sql(_context(companies=['不存在股份有限公司'], years=[2024], metrics=['revenue']))
    assert empty['status'] == 'failed'
    assert empty['sql_status'] == 'fallback'
    assert len(empty['attempts']) == 2
    assert all('查询结果为空' in attempt.get('error', '') for attempt in empty['attempts'])


def test_ui_query_process_contains_real_sql_safety_and_execution_status():
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    app.text_area[0].set_value('比亚迪2024年营业收入是多少？')
    next(button for button in app.button if button.label == '开始分析').click().run()
    assert not app.exception
    result = app.session_state.chat_messages[-1]['result']['sql_result']
    assert result['status'] == 'success'
    assert result['safety_status'] == 'passed'
    assert result['execution_status'] == 'success'
    assert result['params'] == [BYD, 2024]
    assert any('SELECT' in code.value for code in app.code)
    visible_text = '\n'.join(str(item.value) for item in [*app.markdown, *app.text])
    assert '安全校验：通过' in visible_text
    assert '执行状态：成功' in visible_text
