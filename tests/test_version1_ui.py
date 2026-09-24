"""Exercise real Streamlit controls after the Version1 visual migration."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'


def _app():
    app = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert not app.exception
    return app


def _label(widgets, label):
    return next(widget for widget in widgets if widget.label == label)


def _submit(app, question):
    app.text_area(key='qa_input').set_value(question)
    _label(app.button, '开始分析').click().run()
    assert not app.exception
    return app.session_state.chat_messages[-1]


def _company_and_years(app):
    company = app.session_state.selected_main
    years = [int(year) for year in app.selectbox(key='qa_period').options if str(year).isdigit()]
    assert len(years) >= 2
    return company, years


def test_history_restores_results_context_and_continues_real_query():
    app = _app()
    company, years = _company_and_years(app)
    app.selectbox(key='qa_period').set_value(str(years[0])).run()
    first = _submit(app, f'{company}{years[0]}年营业收入是多少？')
    assert first['result']['sql_result']['status'] == 'success'
    first_id = app.session_state.active_session_id
    original_messages = deepcopy(app.session_state.chat_messages)

    app.button(key='new_session').click().run()
    assert not app.exception
    assert app.session_state.active_session_id != first_id
    assert app.session_state.chat_messages == []
    assert app.session_state.qa_period == '自动识别'
    assert app.session_state.conversation_context['metrics'] == []
    _submit(app, f'{company}{years[1]}年归母净利润是多少？')
    second_id = app.session_state.active_session_id

    app.button(key=f'session_{first_id}').click().run()
    assert not app.exception
    assert app.session_state.active_session_id == first_id
    assert app.session_state.chat_messages == original_messages
    assert app.session_state.qa_period == str(years[0])
    assert app.session_state.conversation_context['metrics'] == ['revenue']
    restored = _submit(app, f'那{years[1]}年呢？')
    assert restored['result']['parsed']['companies'] == [company]
    assert restored['result']['parsed']['years'] == [years[1]]
    assert restored['result']['parsed']['metrics'] == ['revenue']
    assert restored['result']['sql_result']['status'] == 'success'
    assert len(app.session_state.chat_messages) == 2
    other = next(s for s in app.session_state.saved_sessions if s['id'] == second_id)
    assert len(other['messages']) == 1
    assert other['context']['metrics'] == ['net_profit']


def test_enterprise_page_return_keeps_selected_company_and_pdf_downloads():
    app = _app()
    app.radio(key='navigation').set_value('企业分析').run()
    assert not app.exception
    choices = app.selectbox(key='enterprise_main').options
    picked = choices[-1]
    app.selectbox(key='enterprise_main').set_value(picked).run()
    assert not app.exception
    assert {button.label for button in app.download_button} >= {'下载 TXT 报告', '下载图文 PDF 报告'}
    _label(app.button, '返回连续问答').click().run()
    assert not app.exception
    assert app.radio(key='navigation').value == '财报问数'
    assert app.selectbox(key='qa_main').value == picked
    assert app.session_state.selected_main == picked


def test_cloud_settings_keep_native_password_field_and_local_fallback():
    app = _app()
    app.selectbox(key='engine_mode').set_value('云端增强模式').run()
    assert not app.exception
    assert _label(app.selectbox, '模型服务商').options
    assert _label(app.selectbox, '模型 / Endpoint').options
    api_key = _label(app.text_input, 'API Key')
    assert api_key.proto.type == 1  # Streamlit TextInput.PASSWORD
    assert api_key.value == ''
    assert _label(app.text_input, 'API Base URL').value.startswith('https://')
    company, years = _company_and_years(app)
    result = _submit(app, f'{company}{years[0]}年营业收入是多少？')
    assert result['result']['sql_result']['status'] == 'success'
    app.selectbox(key='engine_mode').set_value('本地分析模式').run()
    assert not app.exception
    assert app.session_state.engine_mode == '本地分析模式'
    assert not any(widget.label == 'API Key' for widget in app.text_input)
    assert app.session_state.chat_messages[-1]['result'] == result['result']


def test_library_search_treats_brackets_as_literal_text():
    app = _app()
    app.radio(key='navigation').set_value('研究资料库').run()
    assert not app.exception
    assert app.dataframe
    before = app.dataframe[0].value.copy()
    _label(app.text_input, '搜索文档或企业').set_value('[').run()
    assert not app.exception
    actual = app.dataframe[0].value
    expected = before[before.astype(str).apply(lambda column: column.str.contains('[', regex=False)).any(axis=1)]
    assert actual.reset_index(drop=True).equals(expected.reset_index(drop=True))


@pytest.mark.parametrize(('chart_type', 'trace_type'), [('折线图', 'scatter'), ('柱形图', 'bar')])
def test_enterprise_custom_chart_controls_generate_requested_plot(chart_type, trace_type):
    app = _app()
    app.radio(key='navigation').set_value('企业分析').run()
    assert '饼图' not in app.selectbox(key='enterprise_chart_type').options
    app.selectbox(key='enterprise_chart_type').set_value(chart_type).run()
    assert not app.exception
    charts = [json.loads(chart.proto.spec) for chart in app.get('plotly_chart')]
    custom = next(chart for chart in charts if f'{chart_type}分析' in chart.get('layout', {}).get('title', {}).get('text', ''))
    assert custom['data']
    assert all(trace['type'] == trace_type for trace in custom['data'])


def test_empty_question_has_no_result_or_lost_session():
    app = _app()
    active_id = app.session_state.active_session_id
    app.text_area(key='qa_input').set_value('   ')
    _label(app.button, '开始分析').click().run()
    assert not app.exception
    assert app.session_state.chat_messages == []
    assert app.session_state.active_session_id == active_id
    assert app.session_state.conversation_context['awaiting_clarification'] is False
    assert any('请输入需要分析的财务问题。' in warning.value for warning in app.warning)


def test_answer_enterprise_action_uses_answer_company():
    app = _app()
    company, years = _company_and_years(app)
    result = _submit(app, f'{company}{years[0]}年营业收入是多少？')['result']
    app.button(key='qa_enterprise_0').click().run()
    assert not app.exception
    assert app.radio(key='navigation').value == '企业分析'
    assert app.selectbox(key='enterprise_main').value == result['company']


def test_answer_report_action_exposes_real_report_downloads():
    app = _app()
    company, years = _company_and_years(app)
    _submit(app, f'{company}{years[0]}年营业收入是多少？')
    app.button(key='qa_report_0').click().run()
    assert not app.exception
    assert {button.label for button in app.download_button} >= {'下载 TXT 报告', '下载图文 PDF 报告'}


def test_answer_details_toggle_preserves_query_result():
    app = _app()
    company, years = _company_and_years(app)
    result = deepcopy(_submit(app, f'{company}{years[0]}年营业收入是多少？')['result'])
    initial_frames = len(app.dataframe)
    assert initial_frames > 0
    app.button(key='qa_details_0').click().run()
    assert not app.exception
    assert len(app.dataframe) < initial_frames
    assert app.session_state.chat_messages[-1]['result'] == result
    app.button(key='qa_details_0').click().run()
    assert not app.exception
    assert len(app.dataframe) == initial_frames


def test_cloud_enhanced_report_survives_reruns_in_actual_download_payloads(monkeypatch):
    import core.llm as llm
    import core.report_pdf as report_pdf
    from streamlit.delta_generator import DeltaGenerator

    enhancement_calls = []
    downloads = {}
    pdf_inputs = []
    original_download = DeltaGenerator.download_button
    original_pdf = report_pdf.report_text_to_pdf_bytes
    marker = '回归测试增强稿：'

    def enhance(_config, report_text, company, profile):
        enhancement_calls.append((company, profile))
        return marker + report_text

    def capture_download(self, label, data, *args, **kwargs):
        downloads[kwargs.get('key', label)] = data
        return original_download(self, label, data, *args, **kwargs)

    def capture_pdf(report_text, *args, **kwargs):
        pdf_inputs.append(report_text)
        return original_pdf(report_text, *args, **kwargs)

    monkeypatch.setattr(llm, 'enhance_report_with_llm', enhance)
    monkeypatch.setattr(report_pdf, 'report_text_to_pdf_bytes', capture_pdf)
    monkeypatch.setattr(DeltaGenerator, 'download_button', capture_download)
    app = _app()
    app.selectbox(key='engine_mode').set_value('云端增强模式').run()
    # The cloud call is replaced above; this fixture value never leaves the test.
    _label(app.text_input, 'API Key').set_value('regression-fixture-not-a-real-key').run()
    app.radio(key='navigation').set_value('企业分析').run()
    assert not app.exception
    assert not downloads['enterprise_report_txt'].decode('utf-8').startswith(marker)

    app.button(key='enterprise_report_enhance').click().run()
    assert not app.exception
    assert len(enhancement_calls) == 1
    enhanced = downloads['enterprise_report_txt']
    assert enhanced.decode('utf-8').startswith(marker)
    assert downloads['enterprise_report_pdf'].startswith(b'%PDF')
    assert pdf_inputs[-1] == enhanced.decode('utf-8')
    assert app.session_state.enterprise_report_enhanced_text['text'] == enhanced.decode('utf-8')

    # AppTest does not transfer files; inspect the bytes passed to the real native
    # download controls after reruns, which is the payload the browser receives.
    for _ in range(2):
        app.run()
        assert not app.exception
        assert downloads['enterprise_report_txt'] == enhanced
        assert downloads['enterprise_report_pdf'].startswith(b'%PDF')
        assert pdf_inputs[-1] == enhanced.decode('utf-8')
        assert len(enhancement_calls) == 1

    # A changed report must not reuse an enhancement for the previous conditions.
    next_profile = next(value for value in app.selectbox(key='investor_profile').options
                        if value != app.session_state.investor_profile)
    app.selectbox(key='investor_profile').set_value(next_profile).run()
    assert not app.exception
    assert not downloads['enterprise_report_txt'].decode('utf-8').startswith(marker)
    assert len(enhancement_calls) == 1


def test_sql_attempts_serialize_to_arrow_without_mutating_backend_params(monkeypatch):
    import pyarrow as pa
    import streamlit as st

    attempt_frames = []
    original_dataframe = st.dataframe

    def capture_dataframe(data=None, *args, **kwargs):
        if hasattr(data, 'columns') and 'params' in data.columns:
            attempt_frames.append(data.copy(deep=True))
        return original_dataframe(data, *args, **kwargs)

    monkeypatch.setattr(st, 'dataframe', capture_dataframe)
    app = _app()
    company, years = _company_and_years(app)
    result = _submit(app, f'{company}{years[0]}年营业收入是多少？')['result']
    assert result['sql_result']['status'] == 'success'
    backend_attempts = result['sql_result']['attempts']
    assert backend_attempts
    assert any(isinstance(value, str) for value in backend_attempts[-1]['params'])
    assert any(isinstance(value, int) for value in backend_attempts[-1]['params'])
    assert attempt_frames
    frame = attempt_frames[-1]
    assert all(isinstance(value, str) for value in frame['params'])
    assert frame['params'].map(json.loads).tolist() == [row['params'] for row in backend_attempts]
    arrow_table = pa.Table.from_pandas(frame, preserve_index=False)
    assert arrow_table.num_rows == len(backend_attempts)
    visible = next(table.value for table in app.dataframe if 'params' in table.value.columns)
    assert company in visible['params'].iloc[-1]
    assert str(years[0]) in visible['params'].iloc[-1]
