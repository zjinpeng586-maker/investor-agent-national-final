"""Compatibility-only tests for historical demo/personal UI snapshots."""
from copy import deepcopy
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / 'app/main.py'


def test_old_workspace_fields_are_ignored_and_history_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'local')
    instance = AppTest.from_file(APP, default_timeout=40).run()
    instance.text_area(key='qa_input').set_value('比亚迪2024年营业收入是多少？')
    next(button for button in instance.button if button.label == '开始分析').click().run()
    assert not instance.exception
    existing = deepcopy(instance.session_state.saved_sessions[0])
    legacy = deepcopy(existing)
    legacy.update(id='legacy-personal-session', title='旧版已保存的问答')
    instance.session_state.workspace_sessions = {'demo': {'saved_sessions': [existing]},
                                                 'personal': {'saved_sessions': [legacy]}}
    instance.session_state.data_workspace = 'personal'
    instance.session_state.active_workspace = 'demo'
    instance.session_state.pending_import = {'_ui_channel': 'online', 'file_name': '旧库待确认.pdf'}
    instance.run()
    assert not instance.exception
    for key in ('data_workspace', 'active_workspace', 'workspace_sessions', 'pending_import'):
        assert key not in instance.session_state
    assert {existing['id'], legacy['id']} <= {entry['id'] for entry in instance.session_state.saved_sessions}
    instance.button(key='session_legacy-personal-session').click().run()
    assert not instance.exception
    assert instance.session_state.chat_messages == legacy['messages']
    instance.text_area(key='qa_input').set_value('那2023年呢？')
    next(button for button in instance.button if button.label == '开始分析').click().run()
    assert not instance.exception
    result = instance.session_state.chat_messages[-1]['result']
    assert result['status'] == 'success'
    assert result['parsed']['companies'] == ['比亚迪股份有限公司']
    assert result['parsed']['years'] == [2023]
    snapshot = next(row for row in instance.session_state.saved_sessions if row['id'] == legacy['id'])
    assert not {'data_workspace', 'active_workspace', 'workspace_sessions'} & snapshot.keys()
