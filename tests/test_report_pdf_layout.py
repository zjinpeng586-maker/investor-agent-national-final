"""Synthetic PDF acceptance cases: fit A4, paginate, and preserve full content."""

from io import BytesIO
import re

import pandas as pd
import pymupdf
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate

from core import report_pdf
from app.branding import PAGE_TITLE, REPORT_SUBTITLE


PAGE_MARGIN = 1.8 * cm
# SimpleDocTemplate's Frame additionally reserves 6 pt on each side.
FRAME_WIDTH = A4[0] - 2 * PAGE_MARGIN - 12
SCORE = {
    'available': True, 'score': 82,
    'detail': {'盈利能力': 24, '现金流质量': 20, '偿债稳健性': 21, '成长能力': 17},
}
SECTION_TITLES = [
    '一、报告摘要', '二、核心财务数据概览', '三、趋势解读',
    '四、风险预警与规则依据', '五、四维评分解释', '六、风险仪表盘',
    '七、投资者画像匹配', '八、企业对比参考', '九、数据来源与可追溯性',
    '十、研究结论',
]


def _financial_data():
    return pd.DataFrame([
        dict(year=year, revenue=100 + i * 11, net_profit=10 + i,
             operating_cashflow=20 + i * 2, roe=15 + i / 10,
             debt_ratio=45 - i / 10, gross_margin=20 + i / 5,
             eps=1.2 + i / 10)
        for i, year in enumerate(range(2017, 2025))
    ])


def _compact_text(pdf):
    with pymupdf.open(stream=pdf, filetype='pdf') as document:
        # Running folios must not interrupt a source identifier continued on the next page.
        body = ''.join(page.get_text(clip=pymupdf.Rect(
            0, 0, page.rect.width, page.rect.height - cm,
        )) for page in document)
        return re.sub(r'\s+', '', body)


def _assert_page_geometry(pdf):
    """Inspect actual emitted PDF text and vector geometry, not flowable declarations."""
    with pymupdf.open(stream=pdf, filetype='pdf') as document:
        assert len(document) > 0
        for page_index, page in enumerate(document):
            assert page.rect.width == pytest.approx(A4[0], abs=0.1)
            assert page.rect.height == pytest.approx(A4[1], abs=0.1)
            # Default extraction clips off-paper glyphs and would hide precisely
            # the regression this check is intended to catch.
            for block in page.get_text('dict', clip=pymupdf.INFINITE_RECT())['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        if not span['text'].strip():
                            continue
                        x0, y0, x1, y1 = span['bbox']
                        context = (page_index + 1, span['text'], span['bbox'])
                        assert x0 >= PAGE_MARGIN - 1, context
                        assert x1 <= page.rect.width - PAGE_MARGIN + 1, context
                        assert y0 >= 0, context
                        assert y1 <= page.rect.height, context
                        if not (span['text'] == PAGE_TITLE or re.fullmatch(r'第\s*\d+\s*页', span['text'])):
                            assert y0 >= 1.65 * cm - 1, context
                            assert y1 <= page.rect.height - 1.45 * cm + 1, context
            for drawing in page.get_drawings():
                rect = drawing['rect']
                context = (page_index + 1, tuple(rect))
                assert rect.x0 >= PAGE_MARGIN - 1, context
                assert rect.x1 <= page.rect.width - PAGE_MARGIN + 1, context
                assert rect.y0 >= 0, context
                assert rect.y1 <= page.rect.height, context


@pytest.fixture(scope='module')
def full_score_pdf():
    report = '\n'.join(title + '\n合成测试：指标变化须结合原始报告核验。' for title in SECTION_TITLES)
    return report_pdf.report_text_to_pdf_bytes(
        report, '合成财务测试股份有限公司', df=_financial_data(), score=SCORE,
    )


@pytest.fixture(scope='module')
def long_report_pdf():
    # All marker strings and sources are synthetic, never confidential user data.
    lines = []
    for section, title in enumerate(SECTION_TITLES):
        lines.append(title)
        for item in range(19):
            prefix = '- ' if section in (2, 3, 4, 5) else ''
            lines.append(prefix + '跨年度经营与现金流变化需结合报告证据核验，缺失数据不应作为低风险依据。'
                         + f'S{section:02d}ROW{item:02d}END')
    lines.extend(['SOURCETAILSTART' + 'Abcd0123456789' * 150 + 'SOURCETAILEND'])
    company = '合成超长企业名称' * 10 + '<A&B>COMPANYTAILEND'
    subtitle = '合成副标题：<不可隐去> & 附加说明 ' + 'LONGSUBTITLE' * 15 + 'SUBTITLETAILEND'
    return report_pdf.report_text_to_pdf_bytes(
        '\n'.join(lines), company, subtitle=subtitle, df=_financial_data(), score=SCORE,
    )


def test_complete_score_report_text_and_charts_fit_a4(full_score_pdf):
    # In particular, the prior side-by-side 230 + 470 pt score charts overflowed.
    _assert_page_geometry(full_score_pdf)
    text = _compact_text(full_score_pdf)
    assert '四维能力雷达图' in text
    assert '评分维度条形图' in text
    for value in ('24/30', '20/25', '21/25', '17/20'):
        assert value in text


def test_long_title_source_and_risk_text_do_not_overflow(long_report_pdf):
    _assert_page_geometry(long_report_pdf)


def test_every_report_section_keeps_its_tail_instead_of_truncation(long_report_pdf):
    text = _compact_text(long_report_pdf)
    for section in range(len(SECTION_TITLES)):
        for item in range(19):
            assert f'S{section:02d}ROW{item:02d}END' in text
    assert 'COMPANYTAILEND' in text
    assert '<A&B>' in text
    assert 'SUBTITLETAILEND' in text
    assert '<不可隐去>&附加说明' in text
    assert 'SOURCETAILSTART' + 'Abcd0123456789' * 150 + 'SOURCETAILEND' in text


def test_export_keeps_every_supplied_financial_year():
    # Render the table on its own so trend-axis labels cannot conceal missing rows.
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=PAGE_MARGIN, leftMargin=PAGE_MARGIN,
    )
    document.build([report_pdf._metric_snapshot_table(_financial_data())])
    text = _compact_text(buffer.getvalue())
    # Interior years 2018-2020 would be silently omitted by the old tail(4).
    for year in range(2017, 2025):
        assert str(year) in text


def test_full_compliance_notice_is_readable_and_retained(full_score_pdf):
    text = _compact_text(full_score_pdf)
    assert (f'本报告由{PAGE_TITLE}基于已接入数据和规则引擎自动生成，'
            '仅供学习研究与辅助分析，不构成任何形式的投资建议或买卖指令。') in text


def test_report_brand_metadata_and_cover_match_application(full_score_pdf):
    with pymupdf.open(stream=full_score_pdf, filetype='pdf') as document:
        cover = re.sub(r'\s+', '', document[0].get_text())
        assert PAGE_TITLE in cover and REPORT_SUBTITLE in cover
        assert PAGE_TITLE in document.metadata['title']
        assert document.metadata['author'] == PAGE_TITLE
        assert document.metadata['subject'] == REPORT_SUBTITLE
        assert all(PAGE_TITLE in page.get_text() for page in document)
    text = _compact_text(full_score_pdf)
    for legacy in ('V2.0', '星月', '赛场展示', '内置结构化样例库', '演示样例'):
        assert legacy not in text


def test_report_without_facts_has_explicit_empty_states_and_no_claim_of_saved_files():
    pdf = report_pdf.report_text_to_pdf_bytes('', '空指标企业', df=pd.DataFrame())
    text = _compact_text(pdf)
    assert '当前未提供可核验的分析摘要' in text
    assert '当前资料库暂无可用于分析的年度财务指标' in text
    assert '当前结果暂无可核验的来源信息' in text
    assert '系统仍保留来源文件' not in text
    assert '四维能力雷达图' not in text
    _assert_page_geometry(pdf)


def test_text_report_uses_shared_brand_and_preserves_metric_specific_sources():
    from core.analysis import generate_report

    data = _financial_data()
    data['metric_provenance'] = [
        {'revenue': {'file_name': 'revenue.csv', 'cell': 'B2', 'raw_unit': '亿元', 'import_id': 'version-1'},
         'net_profit': {'file_name': 'profit.pdf', 'page': 7, 'raw_unit': '亿元', 'import_id': 'version-2'}}
        for _ in range(len(data))
    ]
    report = generate_report('核验企业', data, [], None)
    assert report.startswith(PAGE_TITLE + '\n' + REPORT_SUBTITLE + '\n分析企业：核验企业')
    assert '2017年营业收入 100.00亿元：revenue.csv，单元格B2' in report
    assert '2017年归母净利润 10.00亿元：profit.pdf，第7页' in report
    assert 'version-1' in report and 'version-2' in report
    assert '答辩展示' not in report


@pytest.mark.parametrize('table_name', ['information', 'snapshot', 'kpi', 'score'])
def test_tables_fit_actual_doc_frame_width(table_name):
    tables = {
        'information': lambda: report_pdf._info_table([('说明', '需要保留完整的说明文字' * 5)]),
        'snapshot': lambda: report_pdf._metric_snapshot_table(_financial_data()),
        'kpi': lambda: report_pdf._kpi_cards(_financial_data(), SCORE),
        'score': lambda: report_pdf._score_table(SCORE),
    }
    table = tables[table_name]()
    width, height = table.wrap(FRAME_WIDTH, A4[1])
    assert 0 < width <= FRAME_WIDTH + 0.1
    assert height > 0


def test_oversized_source_table_row_wraps_and_continues_on_next_page():
    source = 'https://example.invalid/annual/' + 'NoSpaceIdentifier0123456789' * 230 + '/SOURCEENDMARK'
    table = report_pdf._info_table([('来源文件', source), ('末行', 'LASTTABLEROWEND')])
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=PAGE_MARGIN, leftMargin=PAGE_MARGIN,
        topMargin=1.65 * cm, bottomMargin=1.45 * cm,
    )
    document.build([table])
    pdf = buffer.getvalue()
    with pymupdf.open(stream=pdf, filetype='pdf') as result:
        assert len(result) >= 2
    _assert_page_geometry(pdf)
    text = _compact_text(pdf)
    assert source in text
    assert 'LASTTABLEROWEND' in text


def test_layout_revision_invalidates_existing_session_pdf_cache(tmp_path, monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from streamlit.delta_generator import DeltaGenerator
    import core.analysis as analysis

    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'public')
    monkeypatch.setattr(analysis, 'generate_report', lambda *args, **kwargs: '一、报告摘要\n缓存回归固定文本。')
    revision = report_pdf.REPORT_LAYOUT_VERSION
    monkeypatch.setattr(report_pdf, 'REPORT_LAYOUT_VERSION', 'previous-layout-test')
    original_pdf = report_pdf.report_text_to_pdf_bytes
    original_download = DeltaGenerator.download_button
    calls, downloads = [], {}

    def build(*args, **kwargs):
        calls.append(True)
        return original_pdf(*args, **kwargs)

    def capture(self, label, data, *args, **kwargs):
        downloads[kwargs.get('key')] = data
        return original_download(self, label, data, *args, **kwargs)

    monkeypatch.setattr(report_pdf, 'report_text_to_pdf_bytes', build)
    monkeypatch.setattr(DeltaGenerator, 'download_button', capture)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app/main.py', default_timeout=40).run()
    app.radio(key='navigation').set_value('企业分析').run()
    assert not app.exception and len(calls) == 1
    old_key = next(iter(app.session_state.pdf_report_cache))
    old_bytes = b'%PDF-previous-overflow-cache'
    app.session_state.pdf_report_cache[old_key] = old_bytes
    app.run()
    assert not app.exception and len(calls) == 1
    assert downloads['enterprise_report_pdf'] == old_bytes

    monkeypatch.setattr(report_pdf, 'REPORT_LAYOUT_VERSION', revision)
    app.run()
    assert not app.exception and len(calls) == 2
    assert downloads['enterprise_report_pdf'] != old_bytes
    _assert_page_geometry(downloads['enterprise_report_pdf'])
