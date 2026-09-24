"""Attribution cases use isolated SQLite data and explicitly synthetic PDFs."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

from core import attribution
from core.db import get_db_path, init_db
from core.retrieval import retrieve_documents
from core.storage import workspace_context


COMPANY = '归因测试股份有限公司'
OTHER = '其他测试股份有限公司'
QUESTION = '归因测试2024年净利润为什么变化？'


@pytest.fixture
def database(tmp_path, monkeypatch):
    with workspace_context('evaluation', tmp_path / 'library', allow_writes=True):
        init_db()
        with sqlite3.connect(get_db_path()) as connection:
            connection.execute('INSERT INTO companies(id, name) VALUES(1, ?)', [COMPANY])
            connection.execute('INSERT INTO companies(id, name) VALUES(2, ?)', [OTHER])
            connection.executemany(
                'INSERT INTO financial_metrics(company_id,year,revenue,net_profit,gross_margin,operating_cashflow,raw_source) '
                'VALUES(1,?,?,?,?,?,?)',
                [(2023, 100, 10, 20, 20, 'synthetic-2023.csv'),
                 (2024, 120, 12, 18, 30, 'synthetic-2024.csv')],
            )
        monkeypatch.setattr(attribution, 'retrieve_documents', lambda *args, **kwargs: {
            'status': 'no_evidence', 'citations': [], 'document_checks': [], 'reason': '无测试文档'})
        yield tmp_path


def _update(year=2024, **metrics):
    # Column names are internal test constants, never application input.
    assert set(metrics).issubset(attribution.METRICS)
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute('UPDATE financial_metrics SET ' + ','.join(f'{key}=?' for key in metrics)
                           + ' WHERE company_id=1 AND year=?', [*metrics.values(), year])


def _build(years=None, **kwargs):
    return attribution.build_net_profit_attribution(COMPANY, [2024] if years is None else years, QUESTION, **kwargs)


def _driver(result, metric):
    return next(node for node in result['drivers'] if node['metric'] == metric)


def _pdf(tmp_path, *, company=COMPANY, year=2024, filename='synthetic-report.pdf', sentences=None):
    path = tmp_path / filename
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    canvas = Canvas(str(path))
    for sentence in sentences or [f'{year}年营业收入增长主要由于产品销售增加。']:
        canvas.setFont('STSong-Light', 12)
        canvas.drawString(50, 770, company)
        canvas.drawString(50, 745, f'{year}年年度报告（自动化测试合成材料）')
        canvas.drawString(50, 690, sentence)
        canvas.showPage()
    canvas.save()
    return {'id': 301, 'company_name': company, 'report_year': year,
            'file_name': path.name, 'file_path': str(path), 'file_type': 'PDF'}


def _use_real_retrieval(monkeypatch, reports, tmp_path):
    monkeypatch.setattr(attribution, 'retrieve_documents', lambda question, context, **kwargs:
                        retrieve_documents(question, context, reports=reports,
                                           index_root=tmp_path / 'index', **kwargs))


def test_root_arithmetic_and_dedicated_two_year_readonly_query(database):
    result = _build()
    assert result['status'] == 'success'
    root = result['root']
    assert (root['previous_value'], root['current_value'], root['delta'], root['growth_rate']) == (10, 12, 2, 20)
    assert root['direction'] == 'up'
    assert root['growth_label'] == '年度同比'
    assert result['sql_trace'][0]['years'] == [2023, 2024]
    assert result['sql_trace'][0]['metrics'] == list(attribution.METRICS)
    assert result['sql_trace'][0]['attempts'][0]['safety_status'] == 'passed'
    assert result['sql_trace'][0]['attempts'][0]['status'] == 'success'
    assert result['duration_ms'] >= 0
    assert len(result['drivers']) == 3
    assert '不代表严格因果贡献率' in result['disclaimer']


@pytest.mark.parametrize(('previous', 'current', 'delta', 'growth', 'direction'), [
    (0, 12, 12, None, 'up'), (0, 0, 0, None, 'flat'), (-10, -5, 5, 50, 'up'),
    (-10, 5, 15, 150, 'up'), (-10, -20, -10, -100, 'down'), (10, 10, 0, 0, 'flat'),
])
def test_zero_negative_and_flat_profit_bases(database, previous, current, delta, growth, direction):
    _update(2023, net_profit=previous)
    _update(2024, net_profit=current)
    root = _build()['root']
    assert root['delta'] == delta
    assert root['growth_rate'] == growth
    assert root['direction'] == direction
    if previous == 0:
        assert '不适用' in root['growth_label']
    if previous < 0:
        assert '绝对值' in root['growth_label']


def test_revenue_growth_and_margin_percentage_points_are_distinct(database):
    result = _build()
    revenue = _driver(result, 'revenue')
    margin = _driver(result, 'gross_margin')
    assert revenue['delta'] == 20
    assert revenue['growth_rate'] == 20
    assert revenue['interpretation'] == '收入端正向线索'
    assert margin['delta'] == -2
    assert margin['unit'] == '%'
    assert margin['delta_unit'] == '个百分点'
    assert margin['growth_rate'] is None
    assert '同比' not in margin['growth_label']


def test_cashflow_is_supporting_signal_even_when_opposite_to_profit(database):
    _update(2024, operating_cashflow=5)
    cash = _driver(_build(), 'operating_cashflow')
    assert cash['role'] == 'supporting_signal'
    assert '经营质量辅助线索' in cash['interpretation']
    assert '不同步' in cash['interpretation']
    assert '导致' not in cash['interpretation']


def test_no_pdf_means_quantitative_only_without_invented_cause(database):
    result = _build()
    assert all(node['evidence_status'] == 'quantitative_only' for node in [result['root'], *result['drivers']])
    assert all(not node['evidence'] for node in [result['root'], *result['drivers']])
    assert result['root']['reason'] == attribution.NO_DOCUMENT_EVIDENCE
    assert all(item['status'] == 'no_evidence' and item['accepted_citations'] == 0 for item in result['document_checks'])


def test_optional_metric_missing_uses_existing_safe_per_metric_path(database):
    _update(2024, gross_margin=None)
    result = _build()
    assert result['status'] == 'success'
    assert result['root']['delta'] == 2
    assert _driver(result, 'gross_margin')['evidence_status'] == 'insufficient'
    assert _driver(result, 'gross_margin')['delta'] is None
    assert _driver(result, 'revenue')['growth_rate'] == 20
    assert _driver(result, 'operating_cashflow')['delta'] == 10
    assert result['sql_trace'][0]['status'] == 'failed'
    assert len(result['sql_trace']) == 5
    assert len(result['document_checks']) == 3
    assert all(len(item['attempts']) <= 2 for item in result['sql_trace'])


def test_root_missing_never_substitutes_another_metric_or_year(database):
    _update(2024, net_profit=None)
    result = _build()
    assert result['status'] == 'insufficient'
    assert result['root']['evidence_status'] == 'insufficient'
    assert result['root']['previous_value'] is None
    assert result['current_year'] == 2024
    assert result['drivers'] == []
    assert result['document_checks'] == []


@pytest.mark.parametrize('missing_year', [2023, 2024])
def test_missing_requested_pair_does_not_fall_back(database, missing_year):
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute('DELETE FROM financial_metrics WHERE year=?', [missing_year])
    result = _build()
    assert result['status'] == 'insufficient'
    assert (result['previous_year'], result['current_year']) == (2023, 2024)
    assert not result['document_checks']


@pytest.mark.parametrize('years', [[2022, 2024], [2022, 2023, 2024], [True], ['2024年']])
def test_ambiguous_or_invalid_explicit_years_are_not_silently_reduced(database, years):
    result = _build(years)
    assert result['status'] == 'insufficient'
    assert not result['sql_trace']


def test_two_explicit_consecutive_years_preserved(database):
    result = _build([2024, 2023])
    assert result['status'] == 'success'
    assert (result['previous_year'], result['current_year']) == (2023, 2024)


def test_no_year_finds_and_discloses_latest_continuous_usable_pair(database):
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute(
            'INSERT INTO financial_metrics(company_id,year,revenue,net_profit,gross_margin,operating_cashflow) '
            'VALUES(1,2025,125,NULL,19,31)')
    result = _build([], available_years=[2023, 2024, 2025])
    assert result['status'] == 'success'
    assert (result['previous_year'], result['current_year']) == (2023, 2024)
    assert '2023 → 2024' in result['scope_note']
    assert '净利润数据可用' in result['scope_note']
    assert result['sql_trace'][0]['metrics'] == []
    assert result['sql_trace'][0]['params'][0] == COMPANY
    assert 'IS NOT NULL' in result['sql_trace'][0]['sql']


def test_no_year_gap_is_not_computed_as_yoy(database):
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute('UPDATE financial_metrics SET year=2022 WHERE year=2023')
    result = _build([])
    assert result['status'] == 'insufficient'
    assert result['root'] is None
    assert '连续两年' in result['reason']


def test_no_year_single_record_is_insufficient(database):
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute('DELETE FROM financial_metrics WHERE year=2023')
    result = _build([])
    assert result['status'] == 'insufficient'
    assert result['root'] is None


def test_unknown_company_is_never_replaced_with_current_company(database):
    result = attribution.build_net_profit_attribution('不存在股份有限公司', [2024], QUESTION)
    assert result['status'] == 'insufficient'
    assert all(item['companies'] == ['不存在股份有限公司'] for item in result['sql_trace'])
    assert result['root']['current_value'] is None


def test_real_same_company_year_pdf_quotes_keep_original_pages(database, monkeypatch):
    report = _pdf(database, sentences=[
        '2024年净利润增长主要由于产品销量增加。',
        '2024年营业收入增长主要由于海外市场销售增加。',
        '2024年毛利率下降主要由于原材料价格上涨。',
        '2024年经营现金流增加主要由于货款回收增加。',
    ])
    _use_real_retrieval(monkeypatch, [report], database)
    result = _build()
    for number, node in enumerate([result['root'], *result['drivers']], 1):
        assert node['evidence_status'] == 'quantitative_and_document'
        citation = node['evidence'][0]
        assert citation['company'] == COMPANY
        assert citation['report_year'] == 2024
        assert citation['page'] == number
        assert citation['file_name'] == report['file_name']
        assert Path(citation['file_path']).samefile(report['file_path'])
    assert all(item['accepted_citations'] == 1 for item in result['document_checks'])


@pytest.mark.parametrize(('company', 'year'), [(COMPANY, 2023), (OTHER, 2024)])
def test_existing_retrieval_strict_scope_cannot_reuse_other_year_or_company(database, monkeypatch, company, year):
    report = _pdf(database, company=company, year=year)
    _use_real_retrieval(monkeypatch, [report], database)
    result = _build()
    assert all(node['evidence_status'] == 'quantitative_only' for node in [result['root'], *result['drivers']])
    assert all(not node['evidence'] for node in [result['root'], *result['drivers']])


@pytest.mark.parametrize('sentence', [
    '2023年营业收入增长主要由于产品销售增加。',
    '2024年报告指出2023年营业收入增长主要由于产品销售增加。',
    '2024年营业收入下降主要由于产品销量减少。',
    '2024年上半年营业收入增长主要由于产品销售增加。',
    '2024年营业收入可能增长主要由于产品销售增加。',
    '2024年净利润增长主要由于原材料成本降低。营业收入受到市场影响。',
    '由于产品需求减少，2024年营业收入下降。',
    '由于市场需求增长，2023年营业收入增加；2024年尚未披露。',
    '2024年营业收入未增长主要由于需求减少。',
    '2024年营业收入未发生明显变化，主要由于市场需求稳定。',
])
def test_report_snippet_must_support_this_annual_metric_direction(database, monkeypatch, sentence):
    report = _pdf(database, sentences=[sentence])
    _use_real_retrieval(monkeypatch, [report], database)
    revenue = _driver(_build(), 'revenue')
    assert revenue['evidence_status'] == 'quantitative_only'
    assert revenue['evidence'] == []


@pytest.mark.parametrize('filename', ['2024年半年度报告.pdf', '2024年第一季度报告.pdf', '2024-Q1.pdf'])
def test_annual_attribution_rejects_quarter_or_half_year_file(database, monkeypatch, filename):
    report = _pdf(database, filename=filename)
    _use_real_retrieval(monkeypatch, [report], database)
    assert _driver(_build(), 'revenue')['evidence_status'] == 'quantitative_only'


def test_causal_clause_direction_does_not_override_metric_direction(database, monkeypatch):
    report = _pdf(database, sentences=['2024年净利润增长主要由于原材料成本下降。'])
    _use_real_retrieval(monkeypatch, [report], database)
    assert _build()['root']['evidence_status'] == 'quantitative_and_document'


def test_cause_first_matching_metric_direction_is_accepted(database, monkeypatch):
    report = _pdf(database, sentences=['由于原材料成本下降，2024年净利润增长。'])
    _use_real_retrieval(monkeypatch, [report], database)
    assert _build()['root']['evidence_status'] == 'quantitative_and_document'


@pytest.mark.parametrize(('field', 'value'), [
    ('company', OTHER), ('report_year', 2023), ('page', 0), ('page', True),
    ('file_name', ''), ('file_path', 'missing.pdf'), ('snippet', '营业收入受到市场影响。'),
])
def test_returned_citation_is_rechecked_at_component_boundary(database, monkeypatch, field, value):
    report = _pdf(database)
    citation = {'company': COMPANY, 'report_year': 2024, 'page': 1,
                'file_name': report['file_name'], 'file_path': report['file_path'],
                'snippet': '2024年营业收入增长主要由于销量增加。'}
    citation[field] = value
    monkeypatch.setattr(attribution, 'retrieve_documents', lambda *args, **kwargs:
                        {'citations': [deepcopy(citation)], 'status': 'success', 'document_checks': []})
    result = _build()
    revenue = _driver(result, 'revenue')
    assert revenue['evidence_status'] == 'quantitative_only'
    assert revenue['evidence'] == []
    check = next(item for item in result['document_checks'] if item['metric'] == 'revenue')
    assert check['rejected_citations']


def test_retrieval_failure_keeps_verified_numbers_and_real_failure_status(database, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic retrieval failure')
    monkeypatch.setattr(attribution, 'retrieve_documents', fail)
    result = _build()
    assert result['status'] == 'success'
    assert result['root']['current_value'] == 12
    assert all(item['status'] == 'failed' for item in result['document_checks'])
    assert all('synthetic retrieval failure' in item['reason'] for item in result['document_checks'])


def test_sql_exception_does_not_create_numbers(database, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic SQL failure')
    monkeypatch.setattr(attribution, 'run_text_to_sql', fail)
    result = _build()
    assert result['status'] == 'insufficient'
    assert result['root']['current_value'] is None
    assert all(item['status'] == 'failed' for item in result['sql_trace'])
    assert not result['document_checks']


@pytest.mark.parametrize('value', [True, float('inf'), float('-inf'), float('nan')])
def test_boolean_and_nonfinite_values_cannot_form_nodes(database, monkeypatch, value):
    rows = [{'company': COMPANY, 'year': year, 'net_profit': value, 'revenue': 100,
             'gross_margin': 20, 'operating_cashflow': 30} for year in [2023, 2024]]
    monkeypatch.setattr(attribution, 'run_text_to_sql', lambda *args, **kwargs:
                        {'status': 'success', 'rows': deepcopy(rows), 'attempts': []})
    result = _build()
    assert result['status'] == 'insufficient'
    assert result['root']['current_value'] is None


def test_sql_success_with_wrong_company_rows_is_rejected(database, monkeypatch):
    rows = [{'company': OTHER, 'year': year, 'net_profit': 10, 'revenue': 100,
             'gross_margin': 20, 'operating_cashflow': 30} for year in [2023, 2024]]
    monkeypatch.setattr(attribution, 'run_text_to_sql', lambda *args, **kwargs:
                        {'status': 'success', 'rows': rows, 'attempts': []})
    assert _build()['status'] == 'insufficient'
