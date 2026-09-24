from io import BytesIO

import pandas as pd
import pytest
from pypdf import PdfReader

from core.analysis import (calc_growth, compare_companies, compute_alerts, generate_report,
                           get_source_for_metric, growth_details, risk_dashboard,
                           score_company, trend_text)
from core.charts import make_metric_chart
from core.report_pdf import (_mini_bar_drawing, _radar_drawing, _score_table,
                             report_text_to_pdf_bytes)


def full_data():
    return pd.DataFrame([
        dict(year=2023, revenue=100, net_profit=10, operating_cashflow=20,
             roe=15, debt_ratio=45, audit_opinion='无保留意见', raw_source='annual-2023.pdf'),
        dict(year=2024, revenue=110, net_profit=12, operating_cashflow=24,
             roe=16, debt_ratio=42, audit_opinion='无保留意见', raw_source='annual-2024.pdf'),
    ])


def test_empty_or_single_metric_data_never_get_default_score_or_stable_risk():
    for df in [pd.DataFrame(), pd.DataFrame([dict(year=2024)]), pd.DataFrame([dict(year=2024, revenue=100)])]:
        score = score_company(df)
        assert score['score'] is None
        assert score['available'] is False
        assert score['detail'] == {}
        alerts = compute_alerts(df)
        assert any(alert['level'] == 'unknown' for alert in alerts)
        dashboard = risk_dashboard(alerts)
        assert all(value == '数据不足，无法评估' for value in dashboard.values())
        assert '相对平稳' not in str(dashboard)


@pytest.mark.parametrize('missing', ['revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio'])
def test_every_score_dimension_requires_its_inputs(missing):
    df = full_data()
    df.loc[1, missing] = None
    score = score_company(df)
    assert not score['available']
    assert score['score'] is None
    assert score['missing']


def test_full_data_scores_and_unqualified_audit_opinion_is_not_qualified():
    df = full_data()
    assert score_company(df)['available']
    alerts = compute_alerts(df)
    assert not any(alert['title'] == '审计意见关注' for alert in alerts)
    assert alerts[0]['checked_rules'] == ['R01', 'R02', 'R03', 'R04', 'R05', 'R06']
    assert alerts[0]['coverage_complete'] is True
    df.loc[1, 'audit_opinion'] = '保留意见'
    assert any(alert['title'] == '审计意见关注' for alert in compute_alerts(df))


def test_known_risk_is_retained_but_missing_data_is_disclosed():
    df = pd.DataFrame([dict(year=2024, debt_ratio=80)])
    alerts = compute_alerts(df)
    assert any(alert['title'] == '资产负债率偏高' for alert in alerts)
    assert any(alert['level'] == 'unknown' for alert in alerts)
    assert risk_dashboard(alerts)['综合风险'] == '中等关注（数据不全）'


def test_interval_growth_is_not_mislabeled_yoy_and_cagr_uses_elapsed_years():
    df = pd.DataFrame([dict(year=2023, revenue=100), dict(year=2025, revenue=144)])
    assert calc_growth(df, 'revenue') is None
    details = growth_details(df, 'revenue')
    assert details['interval_growth'] == pytest.approx(44)
    assert details['cagr'] == pytest.approx(20)
    assert details['missing_years'] == [2024]
    text = trend_text('测试公司', df, 'revenue')
    assert '区间累计增长 44.00%' in text
    assert '年化增长率（CAGR）20.00%' in text
    assert '缺少2024年指标' in text
    assert '同比变化 44.00%' not in text


def test_adjacent_yoy_and_interval_growth_are_separate_facts():
    df = pd.DataFrame([dict(year=2023, revenue=100), dict(year=2024, revenue=120), dict(year=2025, revenue=144)])
    result = growth_details(df, 'revenue')
    assert result['yoy'] == pytest.approx(20)
    assert result['interval_growth'] == pytest.approx(44)
    assert result['cagr'] == pytest.approx(20)
    assert result['missing_years'] == []


@pytest.mark.parametrize('base', [0, -100])
def test_zero_and_negative_base_do_not_create_growth_rates(base):
    df = pd.DataFrame([dict(year=2023, revenue=base), dict(year=2024, revenue=100)])
    result = growth_details(df, 'revenue')
    assert all(result[key] is None for key in ['yoy', 'interval_growth', 'cagr'])
    assert '基期为零或负值' in result['reason']


def test_missing_latest_value_is_not_replaced_by_earlier_valid_year():
    df = pd.DataFrame([dict(year=2023, revenue=100), dict(year=2024, revenue=120), dict(year=2025, revenue=None)])
    assert calc_growth(df, 'revenue') is None
    assert growth_details(df, 'revenue')['interval_growth'] is None
    assert '2025年暂无数据' in trend_text('测试公司', df, 'revenue')


def test_percentage_metric_changes_are_percentage_points():
    df = pd.DataFrame([dict(year=2023, roe=10), dict(year=2024, roe=12)])
    text = trend_text('测试公司', df, 'roe')
    assert '2.00 个百分点' in text
    assert '20.00%' not in text


def test_single_metric_comparison_has_no_score_recommendation():
    a, b = full_data(), full_data()
    b['revenue'] *= 2
    result = compare_companies('甲公司', a, '乙公司', b, metrics=['revenue'])
    assert result['recommended'] is None
    assert result['score_table'].empty
    assert result['score_a']['score'] is None
    assert result['table']['年度'].tolist() == [2023, 2024]
    assert result['table']['乙公司'].tolist() == [200, 220]


def test_comprehensive_comparison_has_no_default_38_or_cross_year_preference():
    a = pd.DataFrame([dict(year=2024, revenue=100)])
    result = compare_companies('甲公司', a, '乙公司', a)
    assert result['recommended'] is None
    assert result['score_a']['score'] is None
    assert '38' not in result['reasoning']
    b = full_data()
    b['year'] += 5
    no_common = compare_companies('甲公司', full_data(), '乙公司', b)
    assert no_common['recommended'] is None
    assert '没有共同可用年度' in no_common['reasoning']


def test_mixed_unit_charts_have_separate_axes_and_missing_year_gap():
    df = pd.DataFrame([dict(year=2023, revenue=100, net_profit=10, roe=12, eps=1),
                       dict(year=2025, revenue=130, net_profit=None, roe=14, eps=1.4)])
    fig = make_metric_chart(df, '折线图', ['revenue', 'net_profit', 'roe', 'eps'])
    assert len({trace.yaxis for trace in fig.data}) == 3
    assert [fig.layout[key].title.text for key in ['yaxis', 'yaxis2', 'yaxis3']] == ['亿元', '%', '元/股']
    revenue = fig.data[0]
    assert list(revenue.x) == [2023, 2024, 2025]
    assert list(revenue.y) == [100, None, 130]
    assert revenue.connectgaps is False
    assert list(fig.layout.xaxis.ticktext) == ['2023', '2024', '2025']


def test_financial_metrics_never_form_a_fake_structure_pie():
    fig = make_metric_chart(full_data(), '饼图', ['revenue', 'net_profit', 'operating_cashflow', 'roe'])
    assert all(trace.type == 'bar' for trace in fig.data)
    assert any('不构成整体' in annotation.text for annotation in fig.layout.annotations)


def test_report_with_missing_data_has_no_score_graph_or_safe_risk_claim():
    df = pd.DataFrame([dict(year=2024, revenue=100)])
    score = score_company(df)
    text = generate_report('测试公司', df, compute_alerts(df), None)
    assert '数据不足，暂不评分' in text
    assert 'None分' not in text
    assert '38' not in text
    assert _radar_drawing(score) is None
    assert _mini_bar_drawing(score) is None
    assert _score_table(score) is None
    pdf = report_text_to_pdf_bytes(text, '测试公司', df=df, score=score)
    assert pdf.startswith(b'%PDF')
    parsed = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    assert 'None' not in parsed
    assert '数据不足' in parsed


def test_each_metric_source_uses_its_own_location_and_missing_source_does_not_fallback():
    df = full_data()
    df['metric_provenance'] = [{}, {'revenue': {'file_name': 'new.csv', 'cell': 'B2', 'raw_unit': '元', 'import_id': 'v2'},
                                    'net_profit': {'file_name': 'old.pdf', 'page': 10, 'raw_unit': '亿元', 'import_id': 'v1'}}]
    assert 'new.csv' in get_source_for_metric(df, 2024, 'revenue')
    assert 'B2' in get_source_for_metric(df, 2024, 'revenue')
    assert 'old.pdf' in get_source_for_metric(df, 2024, 'net_profit')
    assert 'new.csv' not in get_source_for_metric(df, 2024, 'net_profit')
    assert '不使用同年其他指标' in get_source_for_metric(df, 2024, 'roe')
    assert '不使用其他年度' in get_source_for_metric(df, 1999, 'revenue')
