from core.analysis import compare_companies, compute_alerts, rows_to_df, score_company
from core.db import fetch_companies, fetch_company_metrics, init_db
from core.qa_engine import answer_question
from core.report_pdf import report_text_to_pdf_bytes
from core.seed import seed_sample_data


def _workspace():
    init_db()
    seed_sample_data()
    companies = fetch_companies()
    data = {row['name']: rows_to_df(fetch_company_metrics(row['id'])) for row in companies}
    return companies, data


def test_local_qa_compare_risk_and_pdf_report_remain_available():
    companies, data = _workspace()
    names = [row['name'] for row in companies if not data[row['name']].empty]
    primary = '比亚迪股份有限公司'
    secondary = '宁德时代新能源科技股份有限公司'

    result = answer_question(
        '比亚迪近三年营业收入变化如何？', primary, secondary, names, data, '平衡型'
    )
    assert result['company'] == primary
    assert result['chart'] == 'trend'
    assert '营业收入趋势' in result['answer']
    assert result['evidence']

    alerts = compute_alerts(data[primary])
    comparison = compare_companies(primary, data[primary], secondary, data[secondary])
    score = score_company(data[primary])
    assert alerts
    assert comparison['score_table'].shape[0] == 5
    assert score['score'] > 0

    pdf = report_text_to_pdf_bytes('一、报告摘要\n回归测试报告', primary, df=data[primary], score=score)
    assert pdf.startswith(b'%PDF')
    assert len(pdf) > 1_000
