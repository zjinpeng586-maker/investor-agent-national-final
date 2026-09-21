from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas
from streamlit.testing.v1 import AppTest

from core.analysis import rows_to_df
from core.conversation import new_conversation_context, resolve_turn
from core.db import DB_PATH, fetch_companies, fetch_company_metrics, init_db
from core.planner import plan_query
from core.qa_engine import answer_question
from core.retrieval import (
    NO_EVIDENCE,
    build_document_index,
    build_retrieval_query,
    chunk_pages,
    extract_pdf_pages,
    get_index_status,
    is_explanation_query,
    is_rag_eligible_report,
    retrieve_documents,
)
from core.seed import seed_sample_data


BYD = '比亚迪股份有限公司'
CATL = '宁德时代新能源科技股份有限公司'
APP_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'


@pytest.fixture(scope='module')
def rag_document(tmp_path_factory):
    init_db()
    seed_sample_data()
    root = tmp_path_factory.mktemp('rag')
    pdf_path = root / '比亚迪2024年年度报告测试.pdf'
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    canvas = Canvas(str(pdf_path))
    pages = [
        '公司2024年持续加大新能源汽车研发投入，重点推进电池和智能化技术。',
        '海外市场拓展面临汇率波动及地区政策风险，公司将加强风险管理。',
        '管理层认为毛利率变化主要受到产品结构调整影响。',
        '2024年归属于上市公司股东的净利润为402.54亿元。',
        '公司归母净利润变化主要受到期间费用、产品结构及市场竞争影响。',
    ]
    for text in pages:
        canvas.setFont('STSong-Light', 12)
        canvas.drawString(72, 720, text)
        canvas.showPage()
    canvas.save()
    company_id = next(row['id'] for row in fetch_companies() if row['name'] == BYD)
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.execute(
        'INSERT INTO report_files(company_id, file_name, report_year, file_type, file_path, parse_status, note) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (company_id, pdf_path.name, 2024, 'PDF', str(pdf_path), 'success', 'Phase 4 test'),
    )
    report_id = cursor.lastrowid
    connection.commit()
    connection.close()
    report = {
        'id': report_id, 'company_name': BYD, 'report_year': 2024,
        'file_name': pdf_path.name, 'file_path': str(pdf_path),
    }
    yield {'report': report, 'path': pdf_path, 'index_root': root / 'index', 'texts': pages}
    connection = sqlite3.connect(DB_PATH)
    connection.execute('DELETE FROM report_files WHERE id=?', (report_id,))
    connection.commit()
    connection.close()


def test_page_extraction_and_chunks_keep_real_page_numbers(rag_document):
    pages = extract_pdf_pages(rag_document['path'])
    assert [page['page'] for page in pages] == [1, 2, 3, 4, 5]
    assert '研发投入' in pages[0]['text']
    chunks = chunk_pages(pages, rag_document['report'], chunk_size=24, overlap=5)
    assert chunks
    assert all(chunk['page'] in {1, 2, 3, 4, 5} for chunk in chunks)
    assert all('研发投入' not in chunk['text'] or chunk['page'] == 1 for chunk in chunks)
    assert all('汇率波动' not in chunk['text'] or chunk['page'] == 2 for chunk in chunks)


def test_lazy_sidecar_index_and_page_level_retrieval(rag_document):
    report = rag_document['report']
    assert get_index_status(report, rag_document['index_root']) == '待首次查询建立'
    index = build_document_index(report, rag_document['index_root'])
    assert index['page_count'] == 5
    assert index['chunks']
    assert get_index_status(report, rag_document['index_root']) == '已建立'

    context = {'companies': [BYD], 'years': [2024], 'metrics': []}
    research = retrieve_documents(
        '年报如何描述研发投入？', context, [report], rag_document['index_root'], top_k=2,
    )
    assert research['status'] == 'success'
    assert research['citations'][0]['page'] == 1
    assert research['citations'][0]['file_name'] == report['file_name']
    assert research['citations'][0]['snippet'] in research['hits'][0]['text']

    overseas = retrieve_documents(
        '海外业务有哪些汇率和政策风险？', context, [report], rag_document['index_root'], top_k=2,
    )
    assert overseas['citations'][0]['page'] == 2


def test_missing_irrelevant_company_and_year_filters_degrade_safely(rag_document, tmp_path):
    report = rag_document['report']
    irrelevant = retrieve_documents(
        '员工食堂本周菜谱是什么？', {'companies': [BYD], 'years': [2024]},
        [report], rag_document['index_root'],
    )
    assert irrelevant['status'] == 'no_evidence'
    assert irrelevant['answer'] == NO_EVIDENCE

    missing = {**report, 'id': 99999, 'file_path': str(tmp_path / 'missing.pdf')}
    unavailable = retrieve_documents(
        '研发投入', {'companies': [BYD], 'years': [2024]}, [missing], tmp_path / 'missing-index',
    )
    assert unavailable['status'] == 'no_evidence'

    other_company = {**report, 'id': 99998, 'company_name': CATL}
    filtered = retrieve_documents(
        '研发投入', {'companies': [BYD], 'years': [2024]}, [other_company], rag_document['index_root'],
    )
    assert filtered['candidate_documents'] == 0
    assert filtered['status'] == 'no_evidence'

    old_report = {**report, 'id': 99997, 'report_year': 2023}
    year_result = retrieve_documents(
        '研发投入', {'companies': [BYD], 'years': [2024]}, [old_report, report], rag_document['index_root'],
    )
    assert year_result['exact_year_match'] is True
    assert all(hit['report_year'] == 2024 for hit in year_result['hits'])


def test_non_pdf_is_not_eligible_and_no_text_cache_stays_no_text(tmp_path, monkeypatch):
    csv_report = {'id': 41, 'file_name': 'metrics.csv', 'file_path': str(tmp_path / 'metrics.csv'), 'file_type': 'CSV'}
    Path(csv_report['file_path']).write_text('year,revenue\n2024,1', encoding='utf-8')
    assert is_rag_eligible_report(csv_report) is False
    assert get_index_status(csv_report, tmp_path / 'index') == '不适用'
    assert build_document_index(csv_report, tmp_path / 'index')['status'] == 'not_applicable'

    empty_pdf = tmp_path / 'scan.pdf'
    empty_pdf.write_bytes(b'%PDF-1.4 synthetic scan placeholder')
    pdf_report = {'id': 42, 'file_name': 'scan.pdf', 'file_path': str(empty_pdf), 'file_type': 'PDF'}
    monkeypatch.setattr('core.retrieval.extract_pdf_pages', lambda _path: [])
    first = build_document_index(pdf_report, tmp_path / 'index')
    second = build_document_index(pdf_report, tmp_path / 'index')
    assert first['status'] == 'no_text'
    assert second['status'] == 'no_text'
    assert get_index_status(pdf_report, tmp_path / 'index') == '无可检索文本'


def test_deterministic_planner_routes():
    base = {'intent': 'finance_query', 'companies': [BYD], 'years': [2024], 'metrics': ['revenue']}
    assert plan_query('比亚迪2024年营业收入是多少？', base)['route'] == 'sql'
    assert plan_query('比亚迪2024年年报如何描述研发投入？', {**base, 'intent': 'unknown', 'metrics': []})['route'] == 'rag'
    numeric_report = plan_query(
        '比亚迪2024年年报中的净利润是多少？',
        {**base, 'metrics': ['net_profit']},
    )
    assert numeric_report['route'] == 'hybrid'
    assert numeric_report['structured_intent'] == 'finance_query'
    assert plan_query('比亚迪2024年净利润为什么变化？', {**base, 'intent': 'trend_analysis', 'metrics': ['net_profit']})['route'] == 'hybrid'
    comparison = plan_query(
        '比亚迪和宁德时代财务表现差异可能来自哪些业务因素？',
        {'intent': 'unknown', 'companies': [BYD, CATL], 'years': [], 'metrics': []},
    )
    assert comparison['route'] == 'hybrid'
    assert comparison['structured_intent'] == 'company_compare'


def test_retrieval_query_includes_resolved_metric_aliases():
    query = build_retrieval_query(
        '为什么会出现这种变化？',
        {'companies': [BYD], 'years': [2024], 'metrics': ['net_profit']},
    )
    assert BYD in query
    assert '2024' in query
    assert '净利润' in query
    assert '归属于上市公司股东的净利润' in query
    assert is_explanation_query('为什么会出现这种变化？') is True


def test_report_numeric_question_keeps_sql_authoritative(rag_document):
    company_rows = fetch_companies()
    data_map = {row['name']: rows_to_df(fetch_company_metrics(row['id'])) for row in company_rows}
    resolved = {'intent': 'finance_query', 'companies': [BYD], 'years': [2024], 'metrics': ['net_profit']}
    result = answer_question(
        '比亚迪2024年年报中的净利润是多少？',
        BYD, CATL, list(data_map), data_map, resolved_context=resolved,
    )
    assert result['query_plan']['route'] == 'hybrid'
    assert result['query_plan']['structured_intent'] == 'finance_query'
    assert result['sql_result']['status'] == 'success'
    sql_value = result['sql_result']['rows'][0]['net_profit']
    assert f'{sql_value:.2f}' in result['answer']
    assert result['retrieval_result']['status'] in {'success', 'no_evidence'}


def test_hybrid_keeps_sql_numbers_and_adds_real_document_citation(rag_document):
    company_rows = fetch_companies()
    data_map = {row['name']: rows_to_df(fetch_company_metrics(row['id'])) for row in company_rows}
    companies = list(data_map)
    first = resolve_turn(
        new_conversation_context(), '比亚迪2024年净利润是多少？', companies,
        page_company=BYD, page_compare=CATL,
    )
    follow_up = resolve_turn(
        first['context'], '为什么会出现这种变化？年报里是怎么解释的？', companies,
        page_company=BYD, page_compare=CATL,
    )
    resolved = follow_up['resolved']
    assert resolved['companies'] == [BYD]
    assert resolved['years'] == [2024]
    assert resolved['metrics'] == ['net_profit']
    result = answer_question(
        '为什么会出现这种变化？年报里是怎么解释的？',
        BYD, CATL, list(data_map), data_map, resolved_context=resolved,
    )
    assert result['query_plan']['route'] == 'hybrid'
    assert result['sql_status'] == 'success'
    sql_value = result['sql_result']['rows'][0]['net_profit']
    assert f'{sql_value:.2f}' in result['answer']
    assert result['retrieval_result']['status'] == 'success'
    pages = [citation['page'] for citation in result['retrieval_result']['citations']]
    assert pages[0] == 5
    assert 4 not in pages
    assert 3 not in pages
    assert 'PDF第5页' in result['evidence']


def test_explanation_query_rejects_metric_only_numeric_page(tmp_path):
    pdf_path = tmp_path / 'only-number.pdf'
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    canvas = Canvas(str(pdf_path))
    canvas.setFont('STSong-Light', 12)
    canvas.drawString(72, 720, '2024年归属于上市公司股东的净利润为402.54亿元。')
    canvas.save()
    report = {
        'id': 501, 'company_name': BYD, 'report_year': 2024,
        'file_name': pdf_path.name, 'file_path': str(pdf_path), 'file_type': 'PDF',
    }
    result = retrieve_documents(
        '为什么会出现这种变化？年报里是怎么解释的？',
        {'companies': [BYD], 'years': [2024], 'metrics': ['net_profit']},
        [report], tmp_path / 'index',
    )
    assert result['status'] == 'no_evidence'
    assert result['citations'] == []


def test_multi_company_hybrid_runs_company_compare_sql(rag_document):
    company_rows = fetch_companies()
    data_map = {row['name']: rows_to_df(fetch_company_metrics(row['id'])) for row in company_rows}
    result = answer_question(
        '比亚迪和宁德时代财务表现差异可能来自哪些业务因素？',
        BYD, CATL, list(data_map), data_map,
    )
    assert result['query_plan']['route'] == 'hybrid'
    assert result['query_plan']['structured_intent'] == 'company_compare'
    assert result['analysis_intent'] == 'company_compare'
    assert result['sql_result']['status'] == 'success'
    assert {row['company'] for row in result['sql_result']['rows']} == {BYD, CATL}
    assert result['retrieval_result']['status'] in {'success', 'no_evidence'}


def test_multi_company_hybrid_ui_shows_effective_and_original_intents(rag_document):
    app = AppTest.from_file(APP_PATH, default_timeout=30).run()
    app.text_area[0].set_value('比亚迪和宁德时代财务表现差异可能来自哪些业务因素？')
    next(button for button in app.button if button.label == '开始分析').click().run()
    assert not app.exception
    visible = '\n'.join(str(item.value) for item in [*app.markdown, *app.text, *app.caption])
    assert '已识别任务：company_compare' in visible
    assert '原始解析：unknown｜结构化子任务：company_compare' in visible
    result = app.session_state.chat_messages[-1]['result']
    assert result['sql_result']['status'] == 'success'
    comparison_tables = [
        frame.value for frame in app.dataframe
        if '企业' in frame.value.columns
    ]
    assert comparison_tables
    assert any(set(table['企业']) == {BYD, CATL} for table in comparison_tables)


def test_phase4_ui_citations_process_library_and_evaluation(rag_document):
    app = AppTest.from_file(APP_PATH, default_timeout=30).run()
    app.text_area[0].set_value('比亚迪2024年年报如何描述研发投入？')
    next(button for button in app.button if button.label == '开始分析').click().run()
    assert not app.exception
    result = app.session_state.chat_messages[-1]['result']
    assert result['query_plan']['route'] == 'rag'
    visible = '\n'.join(str(item.value) for item in [*app.markdown, *app.text, *app.caption])
    assert 'PDF第1页' in visible
    assert '查询规划：RAG' in visible

    app.radio[0].set_value('研究资料库').run()
    assert not app.exception
    library_text = '\n'.join(str(item.value) for item in [*app.markdown, *app.text, *app.caption, *app.info])
    assert '将在后续阶段接入' not in library_text
    assert '页码级 RAG 索引状态' in library_text

    app.radio[0].set_value('评测中心').run()
    evaluation = app.dataframe[0].value.set_index('评测维度')
    assert evaluation.loc['研报 RAG', '当前状态'] == '本阶段可检查'
    assert evaluation.loc['研报 RAG', '依据'] == 'Phase 4 页级索引、检索、引用与 SQL/RAG 融合自动化测试'
