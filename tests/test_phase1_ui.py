from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from app.main import apply_selected_period, comparison_result_data, filter_result_data


PAGES = ['财报问数', '企业分析', '研究资料库', '数据中心', '评测中心']
APP_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'


def test_default_page_and_five_page_navigation():
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()

    assert not app.exception
    assert app.radio[0].options == PAGES
    assert app.radio[0].value == '财报问数'

    for page in PAGES:
        app.radio[0].set_value(page).run()
        assert not app.exception
        assert any(item.value == f'## {page}' for item in app.markdown)


def test_evaluation_page_does_not_claim_unrun_metrics():
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    app.radio[0].set_value('评测中心').run()

    page_text = '\n'.join(item.value for item in [*app.markdown, *app.caption])
    assert '不展示百分比' in page_text
    evaluation = app.dataframe[0].value.set_index('评测维度')
    assert evaluation.loc['多轮上下文', '当前状态'] == '本阶段可检查'
    assert evaluation.loc['多轮上下文', '依据'] == 'Phase 2 自动化多轮测试'
    assert evaluation.loc['条件澄清', '当前状态'] == '本阶段可检查'
    assert evaluation.loc['条件澄清', '依据'] == 'Phase 2 澄清与恢复测试'
    assert evaluation.loc['Text-to-SQL 与纠错', '当前状态'] == '本阶段可检查'
    assert evaluation.loc['Text-to-SQL 与纠错', '依据'] == 'Phase 3 SQL 生成、安全校验、执行与纠错自动化测试'
    captions = '\n'.join(item.value for item in app.caption)
    assert 'Phase 3 Text-to-SQL 安全执行与纠错均已有自动化检查' in captions
    assert '正式准确率与性能 Benchmark 留待 Phase 5' in captions


def _widget_by_label(widgets, label):
    return next(widget for widget in widgets if widget.label == label)


def _submit_question(app, question):
    app.text_area[0].set_value(question)
    _widget_by_label(app.button, '开始分析').click().run()
    assert not app.exception
    return app.session_state.chat_messages[-1]


def test_selected_period_is_applied_but_explicit_question_year_wins():
    augmented, applied_year = apply_selected_period('比亚迪营业收入是多少？', '2024')
    assert applied_year == 2024
    assert '2024年' in augmented

    explicit, applied_year = apply_selected_period('比亚迪2023年营业收入是多少？', '2024')
    assert explicit == '比亚迪2023年营业收入是多少？'
    assert applied_year == 2023


def test_result_data_filters_years_and_comparison_uses_both_companies():
    byd = pd.DataFrame([{'year': 2023, 'revenue': 1}, {'year': 2024, 'revenue': 2}])
    catl = pd.DataFrame([{'year': 2023, 'revenue': 3}, {'year': 2024, 'revenue': 4}])
    parsed = {'intent': 'finance_query', 'years': [2024], 'metrics': ['revenue']}

    filtered = filter_result_data(byd, parsed)
    assert filtered['year'].astype(int).tolist() == [2024]
    comparison = comparison_result_data('比亚迪', '宁德时代', {'比亚迪': byd, '宁德时代': catl}, parsed)
    assert comparison['企业'].tolist() == ['比亚迪', '宁德时代']
    assert comparison['年度'].tolist() == [2024, 2024]
    assert comparison['营业收入'].tolist() == [2, 4]


def test_qa_period_answer_data_compare_and_header_stay_in_sync():
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    period = _widget_by_label(app.selectbox, '当前理解期间')
    period.set_value('2024').run()

    item = _submit_question(app, '比亚迪营业收入是多少？')
    assert item['result']['parsed']['years'] == [2024]
    assert '2024年营业收入' in item['result']['answer']
    result_tables = [table.value for table in app.dataframe if 'year' in table.value.columns]
    assert result_tables and result_tables[0]['year'].astype(int).tolist() == [2024]

    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    _widget_by_label(app.selectbox, '当前理解期间').set_value('2025').run()
    item = _submit_question(app, '比亚迪2024年营业收入是多少？')
    assert item['result']['parsed']['years'] == [2024]

    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    compare_button = next(button for button in app.button if '核心指标对比' in button.label)
    compare_button.click().run()
    compare_tables = [table.value for table in app.dataframe if '企业' in table.value.columns]
    assert compare_tables
    assert set(compare_tables[0]['企业']) == {'比亚迪股份有限公司', '宁德时代新能源科技股份有限公司'}

    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    assert app.session_state.selected_main == '比亚迪股份有限公司'
    assert app.session_state.selected_cmp == '宁德时代新能源科技股份有限公司'
    _widget_by_label(app.selectbox, '当前理解企业').set_value('宁德时代新能源科技股份有限公司').run()
    header_html = '\n'.join(item.value for item in app.markdown if 'context-bar' in item.value)
    assert '当前企业：宁德时代新能源科技股份有限公司' in header_html
    assert app.session_state.selected_cmp != app.session_state.selected_main
    compare_button = next(button for button in app.button if '核心指标对比' in button.label)
    assert '宁德时代新能源科技股份有限公司' in compare_button.label
    assert app.session_state.selected_cmp in compare_button.label
    compare_button.click().run()
    compare_tables = [table.value for table in app.dataframe if '企业' in table.value.columns]
    assert compare_tables
    assert len(set(compare_tables[0]['企业'])) == 2

    app.radio[0].set_value('企业分析').run()
    _widget_by_label(app.selectbox, '分析企业').set_value('比亚迪股份有限公司').run()
    header_html = '\n'.join(item.value for item in app.markdown if 'context-bar' in item.value)
    assert '当前企业：比亚迪股份有限公司' in header_html


def test_new_session_clears_phase2_conversation_context():
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    _submit_question(app, '比亚迪2024年营业收入是多少？')
    assert app.session_state.conversation_context['metrics'] == ['revenue']

    _widget_by_label(app.button, '＋ 新建会话').click().run()
    context = app.session_state.conversation_context
    assert context['primary_company'] is None
    assert context['years'] == []
    assert context['metrics'] == []
    assert context['intent'] is None
    assert context['awaiting_clarification'] is False


def test_multiturn_ui_updates_context_and_clarification_resumes_answer():
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    _submit_question(app, '比亚迪2024年营收多少？')
    _submit_question(app, '那2023年呢？')
    _submit_question(app, '宁德时代呢？')
    item = _submit_question(app, '净利润呢？')
    assert item['result']['parsed']['companies'] == ['宁德时代新能源科技股份有限公司']
    assert item['result']['parsed']['years'] == [2023]
    assert item['result']['parsed']['metrics'] == ['net_profit']

    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    app.text_area[0].set_value('比亚迪财务数据是多少？')
    _widget_by_label(app.button, '开始分析').click().run()
    assert app.session_state.conversation_context['awaiting_clarification'] is True
    _widget_by_label(app.selectbox, '请选择').set_value('归母净利润')
    _widget_by_label(app.button, '确认并继续').click().run()
    assert app.session_state.conversation_context['awaiting_clarification'] is False
    assert app.session_state.chat_messages[-1]['result']['parsed']['metrics'] == ['net_profit']
