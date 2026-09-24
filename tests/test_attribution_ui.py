"""UI acceptance for saved attribution values, honest units, and source controls."""
from copy import deepcopy

from streamlit.testing.v1 import AppTest

from app.attribution_view import DISCLAIMER, NO_EVIDENCE, attribution_markup


def _result():
    root = {'metric': 'net_profit', 'label': '归母净利润变化', 'previous_value': 10,
            'current_value': 12, 'delta': 2, 'growth_rate': 20, 'direction': 'up',
            'unit': '亿元', 'delta_unit': '亿元', 'growth_label': '年度同比',
            'evidence_status': 'quantitative_only', 'evidence': []}
    revenue = {**root, 'metric': 'revenue', 'id': 'revenue', 'label': '营业收入变化',
               'role': 'quantitative_driver', 'interpretation': '收入端正向线索'}
    margin = {**root, 'metric': 'gross_margin', 'id': 'gross_margin', 'label': '毛利率变化',
              'role': 'quantitative_driver', 'unit': '%', 'delta_unit': '个百分点',
              'previous_value': 20, 'current_value': 18, 'delta': -2,
              'growth_rate': None, 'direction': 'down', 'interpretation': '盈利能力压力线索'}
    cash = {**root, 'metric': 'operating_cashflow', 'id': 'operating_cashflow',
            'label': '经营现金流变化', 'role': 'supporting_signal',
            'interpretation': '经营质量辅助线索'}
    return {'status': 'success', 'company': '测试公司', 'previous_year': 2023, 'current_year': 2024,
            'root': root, 'drivers': [revenue, margin, cash], 'disclaimer': DISCLAIMER,
            'scope_note': '以 2023 年和 2024 年年度数据比较。'}


def _app(payload):
    app = AppTest.from_string('import streamlit as st\n'
                             'from app.attribution_view import render_attribution\n'
                             'render_attribution(st.session_state.payload, "saved_answer_0")\n')
    app.session_state.payload = payload
    app.run()
    assert not app.exception
    return app


def test_saved_tree_renders_hierarchy_no_evidence_and_disclaimer():
    payload = _result()
    frozen = deepcopy(payload)
    app = _app(payload)
    assert app.expander[0].label == '数因融合财务归因'
    assert app.expander[0].proto.expanded is True
    content = next(markdown.value for markdown in app.markdown if 'fu-attribution-tree' in markdown.value)
    assert '2023 年' in content and '2024 年' in content
    assert '+20.00%' in content and '经营质量辅助线索' in content
    assert 'fu-attribution-auxiliary' in content
    assert {caption.value for caption in app.caption} >= {NO_EVIDENCE, DISCLAIMER}
    assert payload == frozen


def test_margin_delta_is_percentage_points_never_normal_growth():
    payload = _result()
    payload['root'] = payload['drivers'][1]
    payload['drivers'] = []
    html = attribution_markup(payload)
    assert '-2.00 个百分点' in html
    assert '20.00' in html and '18.00' in html
    assert '同比' not in html and 'fu-attribution-growth' not in html


def test_negative_base_label_and_zero_base_remain_honest():
    payload = _result()
    payload['root'].update(previous_value=-10, current_value=5, delta=15, growth_rate=150,
                           growth_label='较上年变动（以上年绝对值为基数）')
    html = attribution_markup(payload)
    assert '较上年变动（以上年绝对值为基数）' in html
    assert '+150.00%' in html
    payload['root'].update(previous_value=0, growth_rate=None, growth_label='上年为零，变动比例不适用')
    html = attribution_markup(payload)
    assert '上年为零，变动比例不适用' in html
    assert '+150.00%' not in html


def test_untrusted_company_labels_and_interpretations_are_escaped():
    payload = _result()
    hostile = '<img src=x onerror="alert(1)">'
    payload['company'] = hostile
    payload['root']['label'] = hostile
    payload['drivers'][0]['interpretation'] = hostile
    html = attribution_markup(payload)
    assert '<img ' not in html
    assert html.count('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;') == 4


def test_insufficient_root_displays_reason_and_no_invented_values():
    app = _app({'status': 'insufficient', 'reason': '缺少 2023 年净利润。'})
    assert app.info[0].value == '缺少 2023 年净利润。'
    assert not app.markdown
    assert app.caption[-1].value == DISCLAIMER


def test_optional_missing_node_does_not_present_nan_as_financial_fact():
    payload = _result()
    payload['drivers'][1].update(evidence_status='insufficient', previous_value=None,
                                 current_value=float('nan'), reason='缺少毛利率。')
    html = attribution_markup(payload)
    assert '缺少毛利率。' in html and '数据不足 · 暂不形成线索' in html
    assert 'nan' not in html and 'None' not in html


def test_each_node_opens_its_own_exact_report_page(monkeypatch):
    payload = _result()
    calls = []
    monkeypatch.setattr('app.attribution_view.render_source',
                        lambda citation, key, default_page: calls.append((citation, key, default_page)))
    snippet = '<script>not executable</script> [untrusted](https://example.invalid)'
    for node, page in [(payload['root'], 10), (payload['drivers'][0], 23)]:
        node['evidence_status'] = 'quantitative_and_document'
        node['evidence'] = [{'file_name': '测试公司年报.pdf', 'file_path': '/in-library/report.pdf',
                             'report_year': 2024, 'company': '测试公司', 'page': page, 'snippet': snippet}]
    app = _app(payload)
    assert len(calls) == 2
    assert [call[2] for call in calls] == [10, 23]
    assert calls[0][1] != calls[1][1]
    assert [text.value for text in app.text].count(snippet) == 2
    assert any('PDF 第 10 页' in text.value for text in app.text)
    assert NO_EVIDENCE not in [caption.value for caption in app.caption]


def test_absent_attribution_does_not_add_any_component():
    app = _app(None)
    assert not app.expander and not app.markdown and not app.caption


def test_metric_sources_keep_units_versions_and_distinguish_legacy_row_sources(monkeypatch):
    payload = _result()
    calls = []
    monkeypatch.setattr('app.attribution_view.render_source',
                        lambda citation, key, default_page: calls.append((citation, key, default_page)))
    payload['root']['quantitative_sources'] = [
        {'year': 2023, 'raw_source': '旧来源不能当作每个指标的来源', 'metric_source': None},
        {'year': 2024, 'metric_source': {'file_name': 'profits.csv', 'file_path': '/in-library/profits.csv',
                                       'cell': 'C2', 'raw_unit': '元', 'import_id': 'upload-version-2'}},
    ]
    app = _app(payload)
    assert any('未核验该指标独立出处' in caption.value for caption in app.caption)
    assert any('原始单位：元' in text.value and 'upload-version-2' in text.value for text in app.text)
    assert any('C2' in text.value for text in app.text)
    assert len(calls) == 1 and calls[0][0]['file_name'] == 'profits.csv'


def test_actual_checks_show_failures_and_elapsed_time_without_reexecution():
    payload = _result()
    payload['sql_trace'] = [{'label': 'missing_margin', 'status': 'failed', 'metrics': ['gross_margin'],
                             'years': [2023, 2024], 'duration_ms': 7.25, 'attempts': [{}, {}],
                             'reason': '指标缺失'}]
    payload['document_checks'] = [{'metric': 'revenue', 'status': 'no_evidence', 'years': [2024],
                                   'duration_ms': 8.5, 'accepted_citations': [], 'rejected_citations': [{}],
                                   'reason': '报告年度不符'}]
    app = _app(payload)
    assert any(expander.label == '归因查询与证据核验' for expander in app.expander)
    assert app.dataframe[0].value.iloc[0]['状态'] == 'failed'
    assert app.dataframe[0].value.iloc[0]['耗时 ms'] == 7.25
    assert app.dataframe[1].value.iloc[0]['拒绝引用'] == 1


def test_failure_diagnostics_do_not_expose_local_database_or_stack():
    payload = {'status': 'insufficient',
               'reason': 'Traceback (most recent call last):\nC:/Users/private/data/investor_agent.db',
               'sql_trace': [{'status': 'failed', 'reason': '读取失败：C:/Users/private/data/investor_agent.db',
                              'duration_ms': 3.25, 'attempts': [{}]}]}
    app = _app(payload)
    assert app.info[0].value == '归因分析未完成，请核验数据和报告来源后重试。'
    assert 'Traceback' not in app.info[0].value
    reason = app.dataframe[0].value.iloc[0]['说明']
    assert 'C:/Users' not in reason and 'investor_agent.db' not in reason
    assert app.dataframe[0].value.iloc[0]['耗时 ms'] == 3.25
