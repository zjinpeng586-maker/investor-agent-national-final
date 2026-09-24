"""Release review: report intent, annual evidence and installer compatibility."""
import pytest
from core.qa_engine import _local_parse
from core.retrieval import retrieve_documents

COMPANY = '比亚迪股份有限公司'

@pytest.mark.parametrize('question', [
    '比亚迪2024年报告中的净利润为什么变化？',
    '比亚迪2024年年报中净利润变化的原因是什么？',
])
def test_report_explanation_is_not_report_generation(question):
    result = _local_parse(question, [COMPANY], COMPANY, '平衡型')
    assert result['intent'] == 'trend_analysis'

@pytest.mark.parametrize('metadata,title', [
    ({'report_type': '半年度报告'}, '2024年半年度报告'),
    ({'report_type': '季度报告'}, '2024年第三季度报告'),
    ({}, '2024年半年度报告'),
])
def test_annual_retrieval_excludes_interim_reports(tmp_path, monkeypatch, metadata, title):
    from core import retrieval
    path = tmp_path / 'report.pdf'
    path.write_bytes(b'%PDF-test')
    monkeypatch.setattr(retrieval, 'extract_pdf_pages', lambda _: [
        {'page': 1, 'text': COMPANY + '\n' + title + '\n净利润增长主要由于销售规模扩大。'}])
    report = {'id': 1, 'company_name': COMPANY, 'report_year': 2024,
              'file_path': str(path), 'file_name': 'report.pdf', **metadata}
    result = retrieve_documents('比亚迪2024年净利润为什么增长？',
        {'companies': [COMPANY], 'years': [2024], 'metrics': ['net_profit'], 'period': 'annual'},
        reports=[report], index_root=tmp_path / 'index')
    assert result['status'] == 'no_evidence'
    assert not result['citations']
