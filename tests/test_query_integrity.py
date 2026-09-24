from __future__ import annotations

import json
from pathlib import Path
import pandas as pd
import pytest

from core.analysis import rows_to_df
from core.conversation import new_conversation_context, resolve_turn
from core.db import fetch_companies, fetch_company_metrics, init_db
from core.llm import EXPLANATION_NOTES, ENHANCEMENT_MARKER, enhance_report_with_llm, polish_answer_with_llm, validate_enhancement
from core.llm import validate_api_endpoint
from core.qa_engine import answer_question, parse_question
from core.retrieval import INDEX_SCHEMA_VERSION, build_document_index, extract_pdf_pages, get_index_status, retrieve_documents
from core.seed import seed_sample_data
from core.storage import workspace_context
from core.text_to_sql import validate_query_result, generate_sql, run_text_to_sql

BYD = '比亚迪股份有限公司'
CATL = '宁德时代新能源科技股份有限公司'
CONFIG = {'api_key': 'mock-only', 'base_url': 'https://invalid.local/v1', 'model': 'mock'}


@pytest.fixture
def data(tmp_path):
    with workspace_context('evaluation', tmp_path / 'library', allow_writes=True):
        init_db()
        seed_sample_data()
        rows = fetch_companies()
        yield {row['name']: rows_to_df(fetch_company_metrics(row['id'])) for row in rows}


def ask(data, question, **kwargs):
    return answer_question(question, BYD, CATL, list(data), data, **kwargs)


@pytest.mark.parametrize('question', ['贵州茅台', '贵州茅台2024年营业收入是多少？', '请问贵州茅台的净利润是多少？', '比亚迪和贵州茅台2024年的营收分别是多少？', '贵州茅台的利润是什么', '比亚迪2024年和贵州茅台2023年营业收入是多少？'])
def test_unknown_named_company_never_uses_selected_company(data, question):
    turn = resolve_turn(new_conversation_context(), question, list(data), page_company=BYD)
    result = ask(data, question, resolved_context=turn['resolved'])
    assert result['status'] == 'unknown_company'
    assert '贵州茅台' in result['answer']
    assert result['sql_result']['status'] == 'not_applicable'
    assert '7771.02' not in result['answer']


@pytest.mark.parametrize('period', ['上半年', '下半年', '第一季度', '第三季度', 'Q4'])
def test_nonannual_period_is_not_replaced_by_annual_data(data, period):
    result = ask(data, f'比亚迪2024年{period}营收是多少？')
    assert result['status'] == 'unsupported_period'
    assert result['sql_result']['status'] == 'not_applicable'
    assert '7771.02' not in result['answer']


@pytest.mark.parametrize('task', ['风险', '分析一下', '生成分析报告', '净利润是多少', '净利润为什么下降'])
def test_absent_explicit_year_never_falls_back_to_latest(data, task):
    result = ask(data, f'比亚迪2099年{task}')
    assert result['status'] == 'missing_scope'
    assert result['missing_scope'] == [{'company': BYD, 'year': 2099}]
    assert '相对平稳' not in result['answer']
    assert result['report_text'] is None


def test_year_range_and_three_companies_are_complete(data):
    q = '比亚迪、宁德时代和长安汽车2023年至2025年营业收入分别是多少？'
    turn = resolve_turn(new_conversation_context(), q, list(data), page_company=BYD)
    assert turn['resolved']['years'] == [2023, 2024, 2025]
    assert len(turn['resolved']['companies']) == 3
    result = ask(data, q, resolved_context=turn['resolved'])
    assert result['sql_result']['status'] == 'success'
    assert len(result['sql_result']['rows']) == 9
    for row in result['sql_result']['rows']:
        assert row['company'] in result['answer']
        assert f'{row["year"]}年' in result['answer']
        assert f'{row["revenue"]:.2f}' in result['answer']


def test_sql_failure_scope_filter_applies_to_every_company(data, monkeypatch):
    monkeypatch.setattr('core.qa_engine.run_text_to_sql', lambda _ctx: {'status': 'failed', 'sql_status': 'fallback', 'attempts': [{'error': 'test unavailable'}], 'rows': []})
    result = ask(data, '比亚迪和宁德时代2023年营收分别是多少？')
    assert result['sql_status'] == 'fallback'
    assert '2024年' not in result['answer'] and '2025年' not in result['answer']
    assert '7771.02' not in result['answer']
    assert BYD in result['answer'] and CATL in result['answer']


def test_sql_fallback_still_provides_scoped_facts_to_citation_validator(data, monkeypatch):
    captured = {}
    monkeypatch.setattr('core.qa_engine.run_text_to_sql', lambda _ctx: {'status': 'failed', 'sql_status': 'fallback', 'attempts': [{'error': 'test unavailable'}], 'rows': []})
    def retrieve(_q, context):
        captured.update(context)
        return {'status': 'no_evidence', 'answer': '未检索到证据', 'citations': []}
    monkeypatch.setattr('core.qa_engine.retrieve_documents', retrieve)
    result = ask(data, '比亚迪2024年年报中的净利润是多少')
    assert result['sql_status'] == 'fallback'
    assert [(row['company'], row['year']) for row in captured['structured_facts']] == [(BYD, 2024)]
    assert captured['structured_facts'][0]['net_profit'] == 402.54


def test_single_metric_compare_does_not_run_comprehensive_score(data, monkeypatch):
    def reject(*_args, **_kwargs):
        raise AssertionError('single-metric comparison must not score')
    monkeypatch.setattr('core.qa_engine.compare_companies', reject)
    result = ask(data, '对比比亚迪和宁德时代2024年营业收入')
    assert '7771.02' in result['answer']
    assert '38' not in result['answer']
    assert '不据此进行综合评分' in result['answer']


def test_default_full_name_comparison_recommendation_is_not_an_unknown_company(data):
    question = BYD + '和' + CATL + '核心指标对比'
    turn = resolve_turn(new_conversation_context(), question, list(data), page_company=BYD, page_compare=CATL)
    assert turn['status'] == 'ready'
    result = ask(data, question, resolved_context=turn['resolved'])
    assert result['status'] == 'success'
    assert not result['parsed']['unknown_companies']
    assert result['analysis_intent'] == 'company_compare'
    assert {row['company'] for row in result['sql_result']['rows']} == {BYD, CATL}
    rejected = ask(data, '贵州茅台和' + CATL + '核心指标对比')
    assert rejected['status'] == 'unknown_company'
    assert '贵州茅台' in rejected['answer']


def test_sql_result_validates_company_year_cartesian_scope():
    context = {'companies': [BYD, CATL], 'years': [2023, 2024], 'metrics': ['revenue']}
    result = {'columns': ['company', 'year', 'revenue'], 'rows': [
        {'company': BYD, 'year': 2023, 'revenue': 1}, {'company': CATL, 'year': 2024, 'revenue': 2}]}
    assert validate_query_result(result, context)['valid'] is False
    result['rows'].extend([{'company': BYD, 'year': 2024, 'revenue': 3}, {'company': CATL, 'year': 2023, 'revenue': 4}])
    assert validate_query_result(result, context)['valid'] is True
    result['rows'].append({'company': '其他公司', 'year': 2024, 'revenue': 5})
    assert validate_query_result(result, context)['valid'] is False
    with pytest.raises(ValueError, match='半年或季度'):
        generate_sql({**context, 'period': 'H1'})


def test_retrieval_missing_year_does_not_consider_old_report(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr('core.retrieval.build_document_index', lambda *args: calls.append(args))
    result = retrieve_documents('2024年净利润下降原因', {'companies': [BYD], 'years': [2024], 'metrics': ['net_profit']}, [
        {'company_name': BYD, 'report_year': 2023, 'file_name': 'old.pdf', 'file_path': 'old.pdf'}], tmp_path)
    assert result['status'] == 'no_evidence'
    assert result['candidate_documents'] == 0
    assert not calls
    assert result['missing_scope'] == [{'company': BYD, 'year': 2024}]


@pytest.mark.parametrize('metadata_year', [2022, 2023])
def test_pdf_body_year_rejects_old_metadata_and_invalidates_legacy_cache(tmp_path, monkeypatch, metadata_year):
    path = Path(__file__).parent / 'fixtures' / 'byd_2024_key_financials.pdf'
    report = {'id': 702, 'company_name': BYD, 'report_year': metadata_year,
              'file_name': path.name, 'file_path': str(path)}
    stat = path.stat()
    index_root = tmp_path / 'index'
    index_root.mkdir()
    # Simulate a previously valid cache whose metadata hid the wrong PDF year.
    legacy = {'status': 'ready', 'schema_version': INDEX_SCHEMA_VERSION - 1,
              'file_path': str(path), 'file_size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
              'scope': [BYD, metadata_year], 'chunks': [{'text': '净利润100亿元'}]}
    (index_root / 'report_702.json').write_text(json.dumps(legacy), encoding='utf-8')
    assert get_index_status(report, index_root) == '待刷新'
    reads = []
    def observed_extract(pdf_path):
        reads.append(pdf_path)
        return extract_pdf_pages(pdf_path)
    monkeypatch.setattr('core.retrieval.extract_pdf_pages', observed_extract)
    index = build_document_index(report, index_root)
    assert index['schema_version'] == INDEX_SCHEMA_VERSION
    assert index['status'] == 'scope_mismatch' and index['chunks'] == []
    assert index['scope_verification']['document_year'] == 2024
    assert index['scope_verification']['document_company'] == BYD
    assert '2024' in get_index_status(report, index_root)
    assert str(metadata_year) in get_index_status(report, index_root)
    result = retrieve_documents(f'比亚迪{metadata_year}年年报净利润是多少',
        {'companies': [BYD], 'years': [metadata_year], 'metrics': ['net_profit']}, [report], index_root)
    assert result['status'] == 'no_evidence' and result['citations'] == []
    assert result['candidate_documents'] == 0 and result['exact_year_match'] is False
    assert result['missing_scope'] == [{'company': BYD, 'year': metadata_year}]
    assert result['rejected_documents'][0]['status'] == 'scope_mismatch'
    assert '正文标题/页头报告年度为2024年' in result['reason']
    assert len(reads) == 1  # Reuse this extraction for scope verification and indexing.


def test_pdf_body_issuer_rejects_other_company_metadata(tmp_path):
    path = Path(__file__).parent / 'fixtures' / 'byd_2024_key_financials.pdf'
    report = {'id': 703, 'company_name': CATL, 'report_year': 2024,
              'file_name': '宁德时代2024年年度报告.pdf', 'file_path': str(path)}
    result = retrieve_documents('宁德时代2024年年报净利润是多少',
        {'companies': [CATL], 'years': [2024], 'metrics': ['net_profit']}, [report], tmp_path / 'index')
    assert result['status'] == 'no_evidence' and result['citations'] == []
    assert result['rejected_documents'][0]['status'] == 'scope_mismatch'
    assert BYD in result['reason'] and CATL in result['reason']


def test_scope_without_a_clear_report_title_never_guesses_comparative_year(tmp_path, monkeypatch):
    path = tmp_path / '2024年年度报告.pdf'
    path.write_bytes(b'placeholder; extraction is controlled by this test')
    monkeypatch.setattr('core.retrieval.extract_pdf_pages', lambda _: [
        {'page': 1, 'text': '2024年营业收入100亿元，2023年营业收入90亿元。'}])
    report = {'id': 704, 'company_name': BYD, 'report_year': 2023,
              'file_name': path.name, 'file_path': str(path)}
    index = build_document_index(report, tmp_path / 'index')
    assert index['status'] == 'ready'
    assert index['scope_verification']['status'] == 'metadata_only'
    assert index['scope_verification']['document_year'] is None
    assert '仍依赖导入元数据' in index['scope_verification']['note']


def test_standalone_issuer_header_is_checked_without_inventing_year(tmp_path, monkeypatch):
    path = tmp_path / 'issuer.pdf'
    path.write_bytes(b'placeholder')
    monkeypatch.setattr('core.retrieval.extract_pdf_pages', lambda _: [
        {'page': 1, 'text': BYD + '\n财务数据摘录\n2024年营业收入100亿元。'}])
    report = {'id': 705, 'company_name': CATL, 'report_year': 2024,
              'file_name': path.name, 'file_path': str(path)}
    index = build_document_index(report, tmp_path / 'index')
    assert index['status'] == 'scope_mismatch'
    assert index['scope_verification']['document_year'] is None
    assert index['scope_verification']['document_company'] == BYD


@pytest.mark.parametrize('text', [
    '归母净利润为10亿元，对公司影响如下。',
    '归母净利润下降。海外业务受到汇率影响。',
    '归母净利润下降可能由于市场波动。',
    '归母净利润为10亿元；营业收入下降主要系销量减少。',
])
def test_cause_requires_metric_and_causal_fact_same_sentence(tmp_path, monkeypatch, text):
    report = {'id': 1, 'company_name': BYD, 'report_year': 2024, 'file_name': 'a.pdf', 'file_path': 'a.pdf'}
    monkeypatch.setattr('core.retrieval.build_document_index', lambda *_: {'chunks': [
        {'text': text, 'document_id': 1, 'page': 2, 'company': BYD, 'report_year': 2024, 'chunk_id': '1-p2', 'file_name': 'a.pdf', 'file_path': 'a.pdf'}]})
    result = retrieve_documents('2024年净利润下降原因', {'companies': [BYD], 'years': [2024], 'metrics': ['net_profit']}, [report], tmp_path)
    assert result['status'] == 'no_evidence'


def test_causal_citation_keeps_supporting_sentence_and_original_location(tmp_path, monkeypatch):
    report = {'id': 1, 'company_name': BYD, 'report_year': 2024, 'file_name': 'a.pdf', 'file_path': 'a.pdf'}
    sentence = '归母净利润下降主要系原材料成本增加。'
    monkeypatch.setattr('core.retrieval.build_document_index', lambda *_: {'chunks': [
        {'text': '其他信息。' + sentence, 'document_id': 1, 'page': 7, 'company': BYD, 'report_year': 2024, 'chunk_id': '1-p7', 'file_name': 'a.pdf', 'file_path': '/library/a.pdf'}]})
    result = retrieve_documents('2024年净利润下降原因', {'companies': [BYD], 'years': [2024], 'metrics': ['net_profit']}, [report], tmp_path)
    citation = result['citations'][0]
    assert citation['snippet'] == sentence
    assert citation['page'] == 7 and citation['file_path'] == '/library/a.pdf'


def test_report_header_company_year_alone_are_not_relevance_evidence(tmp_path, monkeypatch):
    report = {'id': 1, 'company_name': BYD, 'report_year': 2024, 'file_name': 'a.pdf', 'file_path': 'a.pdf'}
    monkeypatch.setattr('core.retrieval.build_document_index', lambda *_: {'chunks': [
        {'text': '比亚迪股份有限公司2024年年度报告。公司持续推进新能源汽车研发。', 'document_id': 1, 'page': 7, 'company': BYD, 'report_year': 2024, 'chunk_id': '1-p7', 'file_name': 'a.pdf', 'file_path': 'a.pdf'}]})
    result = retrieve_documents('比亚迪2024年年报如何描述员工食堂菜谱？', {'companies': [BYD], 'years': [2024], 'metrics': []}, [report], tmp_path)
    assert result['status'] == 'no_evidence'


def test_annual_numeric_answer_cannot_cite_quarterly_numbers(tmp_path, monkeypatch):
    report = {'id': 1, 'company_name': BYD, 'report_year': 2024, 'file_name': 'a.pdf', 'file_path': 'a.pdf'}
    chunks = [{'text': text, 'document_id': 1, 'page': page, 'company': BYD, 'report_year': 2024,
               'chunk_id': f'1-p{page}', 'file_name': 'a.pdf', 'file_path': 'a.pdf'}
              for page, text in [(1, '第一季度净利润10亿元，第二季度净利润20亿元，第三季度净利润30亿元。'),
                                 (2, '2024年年度报告：归母净利润（元）40,254,346,000.00，2023年30,040,811,000.00。')]]
    monkeypatch.setattr('core.retrieval.build_document_index', lambda *_: {'chunks': chunks})
    result = retrieve_documents('2024年年报净利润是多少', {'companies': [BYD], 'years': [2024], 'metrics': ['net_profit'],
        'structured_facts': [{'company': BYD, 'year': 2024, 'net_profit': 402.54}]}, [report], tmp_path)
    assert result['numeric_fact_checked']
    assert [citation['page'] for citation in result['citations']] == [2]
    assert '40,254,346,000.00' in result['citations'][0]['snippet']


def test_cloud_explicit_conditions_are_not_overwritten(data, monkeypatch):
    monkeypatch.setattr('core.qa_engine.parse_question_with_llm', lambda *_: {'intent': 'finance_query', 'companies': [CATL], 'years': [2025], 'metrics': ['net_profit']})
    parsed = parse_question('比亚迪2023年至2024年营业收入多少', list(data), CATL, '平衡型', CONFIG)
    assert parsed['companies'] == [BYD]
    assert parsed['years'] == [2023, 2024]
    assert parsed['metrics'] == ['revenue']


def test_cloud_semantics_fill_unresolved_intent_without_losing_to_context(data, monkeypatch):
    monkeypatch.setattr('core.qa_engine.parse_question_with_llm', lambda *_: {'intent': 'risk_warning', 'companies': [], 'years': [], 'metrics': []})
    monkeypatch.setattr('core.qa_engine.polish_answer_with_llm', lambda _cfg, _q, _e, draft, _p: draft)
    result = ask(data, '比亚迪2024年怎么样', llm_config=CONFIG, resolved_context={'intent': 'unknown', 'companies': [BYD], 'years': [2024], 'metrics': []})
    assert result['analysis_intent'] == 'risk_warning'


def test_cloud_changed_numbers_and_company_are_rejected(data, monkeypatch):
    monkeypatch.setattr('core.qa_engine.parse_question_with_llm', lambda *_: {})
    monkeypatch.setattr('core.qa_engine.polish_answer_with_llm', lambda *_: '宁德时代2025年营业收入为999亿元。')
    result = ask(data, '比亚迪2024年营收多少', llm_config=CONFIG)
    assert result['answer'] == result['draft']
    assert '7771.02' in result['answer'] and '999' not in result['answer']
    assert result['agent_trace'][-1]['状态'] == '已降级'
    assert '改写原始事实' in result['agent_trace'][-1]['原因']


def test_report_and_answer_cloud_use_program_rendered_notes(monkeypatch):
    monkeypatch.setattr('core.llm._post_chat', lambda *_args, **_kwargs: json.dumps({'explanation_ids': ['revenue', 'profit']}))
    draft = '公司2024年营收100亿元，利润10亿元。'
    result = polish_answer_with_llm(CONFIG, '收入和利润', '', draft)
    assert result == draft + ENHANCEMENT_MARKER + EXPLANATION_NOTES['revenue'] + '\n' + EXPLANATION_NOTES['profit']
    assert validate_enhancement(draft, result)['valid']
    assert enhance_report_with_llm(CONFIG, draft, BYD).startswith(draft)
    monkeypatch.setattr('core.llm._post_chat', lambda *_args, **_kwargs: '{"explanation_ids":["profit"],"answer":"利润100亿元"}')
    with pytest.raises(ValueError):
        enhance_report_with_llm(CONFIG, draft, BYD)


def test_trace_contains_only_observed_steps_with_durations(data):
    result = ask(data, '比亚迪2024年营收多少')
    assert result['agent_trace']
    assert all('耗时ms' in item and item['耗时ms'] >= 0 for item in result['agent_trace'])
    assert all(item['状态'] != '已调用' for item in result['agent_trace'])
    assert not any(item['智能体'] in {'风险预警智能体', '在线信息披露检索智能体', '文档检索'} for item in result['agent_trace'])
    assert result['sql_result']['attempts'][0]['duration_ms'] >= 0


def test_risk_scope_uses_only_explicitly_disclosed_auxiliary_base(data):
    result = ask(data, '比亚迪2024年有哪些风险')
    assert result['status'] == 'success'
    assert result['parsed']['years'] == [2024]
    assert result['analysis_basis_years'] == {BYD: [2023]}
    assert '仅用于同比/评分' in result['answer']
    assert '2025年' not in result['answer']
    assert result['sql_result']['status'] == 'not_applicable'


def test_comprehensive_compare_keeps_result_year_separate_from_auxiliary_base(data):
    result = ask(data, '对比比亚迪和宁德时代2024年')
    assert {row['year'] for row in result['sql_result']['rows']} == {2024}
    assert result['analysis_basis_years'] == {BYD: [2023], CATL: [2023]}
    assert '比较年度为2024年' in result['answer']


def test_metric_provenance_keeps_distinct_files(data):
    data[BYD]['metric_provenance'] = [
        {'revenue': {'file_name': 'revenue.csv', 'cell': 'B2', 'raw_unit': '元', 'import_id': 'rev-v1'},
         'net_profit': {'file_name': 'profit.pdf', 'page': 5, 'raw_unit': '万元', 'import_id': 'profit-v2'}}
        for _ in data[BYD].index]
    result = ask(data, '比亚迪2024年营收和净利润是多少')
    assert 'revenue.csv，单元格B2' in result['evidence']
    assert 'profit.pdf，PDF第5页' in result['evidence']
    assert '原始单位：元' in result['evidence'] and '原始单位：万元' in result['evidence']
    assert 'revenue.csv' in result['answer'] and 'profit.pdf' in result['answer']
    assert '旧版行级来源' not in result['answer']
    revenue_only = ask(data, '比亚迪2024年营业收入是多少')
    assert 'revenue.csv' in revenue_only['answer']
    assert 'profit.pdf' not in revenue_only['answer']
    assert '旧版行级来源' not in revenue_only['answer']


@pytest.mark.parametrize('url', ['http://127.0.0.1:8080/v1', 'https://api.deepseek.com.evil.example/v1', 'https://api.deepseek.com/v1/other', 'https://api.deepseek.com:443/v1', 'https://user:pass@api.deepseek.com/v1'])
def test_public_cloud_endpoint_rejects_custom_and_private_targets(monkeypatch, url):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'public')
    with pytest.raises(ValueError):
        validate_api_endpoint({'provider': 'DeepSeek', 'api_key': 'mock', 'base_url': url})


def test_official_endpoint_and_local_model_configuration(monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'public')
    validate_api_endpoint({'provider': 'DeepSeek', 'api_key': 'mock'})
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'local')
    validate_api_endpoint({'api_key': 'mock', 'base_url': 'http://127.0.0.1:11434/v1'})
    with pytest.raises(ValueError):
        validate_api_endpoint({'api_key': 'mock', 'base_url': 'file:///etc/passwd'})
