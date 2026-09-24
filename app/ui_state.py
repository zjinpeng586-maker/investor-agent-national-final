"""Session-only UI state. Financial results remain owned by the existing engine."""
from copy import deepcopy
from uuid import uuid4

import streamlit as st

from core.conversation import new_conversation_context


def migrate_legacy_session_state():
    """One-time compatibility for old demo/personal session containers.

    Financial snapshots remain history, while library selectors are discarded.
    No legacy field is copied into newly saved session snapshots.
    """
    legacy = st.session_state.pop('workspace_sessions', {}) or {}
    sessions = list(st.session_state.get('saved_sessions', []))
    known = {item.get('id') for item in sessions}
    for snapshot in legacy.values():
        for session in snapshot.get('saved_sessions', []) or []:
            if session.get('id') and session['id'] not in known:
                sessions.append(deepcopy(session))
                known.add(session['id'])
        if snapshot.get('chat_messages') and snapshot.get('active_session_id') not in known:
            identifier = snapshot.get('active_session_id') or uuid4().hex
            sessions.append({'id': identifier, 'title': snapshot.get('session_title', '历史会话'),
                             'messages': deepcopy(snapshot['chat_messages']),
                             'context': deepcopy(snapshot.get('conversation_context') or new_conversation_context()),
                             'selected_main': snapshot.get('selected_main'),
                             'selected_cmp': snapshot.get('selected_cmp'),
                             'period': snapshot.get('qa_period', '自动识别')})
            known.add(identifier)
    if legacy:
        st.session_state.saved_sessions = sessions
        st.session_state.recent_sessions = [item.get('title', '历史会话') for item in sessions]
    obsolete = ('data_workspace', 'active_workspace')
    if legacy or any(key in st.session_state for key in obsolete):
        for key in ('pending_import', 'online_candidates', 'online_diag', 'import_success'):
            st.session_state.pop(key, None)
    for key in obsolete:
        st.session_state.pop(key, None)


def on_company_change(key):
    """A deliberate selector change updates context before the next question."""
    company = st.session_state.get(key)
    st.session_state.selected_main = company
    st.session_state.qa_main = company
    st.session_state.enterprise_main = company
    context = dict(st.session_state.get('conversation_context') or new_conversation_context())
    context.update(primary_company=company, awaiting_clarification=False, clarification=None)
    if st.session_state.get('selected_cmp') == company:
        st.session_state.selected_cmp = None
        st.session_state.enterprise_cmp = None
    if context.get('compare_company') == company:
        context['compare_company'] = None
    st.session_state.conversation_context = context


def on_compare_change():
    compare = st.session_state.get('enterprise_cmp')
    st.session_state.selected_cmp = compare
    context = dict(st.session_state.get('conversation_context') or new_conversation_context())
    context['compare_company'] = compare
    st.session_state.conversation_context = context


def save_current_session():
    messages = st.session_state.get('chat_messages', [])
    if not messages:
        return
    session_id = st.session_state.setdefault('active_session_id', uuid4().hex)
    snapshot = {
        'id': session_id,
        'title': st.session_state.get('session_title', '新会话'),
        'messages': deepcopy(messages),
        'context': deepcopy(st.session_state.conversation_context),
        'selected_main': st.session_state.get('selected_main'),
        'selected_cmp': st.session_state.get('selected_cmp'),
        'period': st.session_state.get('qa_period', '自动识别'),
    }
    sessions = [s for s in st.session_state.get('saved_sessions', []) if s['id'] != session_id]
    st.session_state.saved_sessions = [snapshot, *sessions][:10]
    st.session_state.recent_sessions = [s['title'] for s in st.session_state.saved_sessions]


def start_new_session():
    save_current_session()
    st.session_state.active_session_id = uuid4().hex
    st.session_state.chat_messages = []
    st.session_state.session_title = '新会话'
    st.session_state.conversation_context = new_conversation_context()
    st.session_state.qa_period = '自动识别'
    st.session_state.page = '财报问数'
    st.session_state.navigation = '财报问数'
    st.session_state.pop('qa_input', None)


def restore_session(session_id):
    save_current_session()
    snapshot = next((s for s in st.session_state.get('saved_sessions', []) if s['id'] == session_id), None)
    if snapshot is None:
        return
    st.session_state.active_session_id = snapshot['id']
    st.session_state.session_title = snapshot['title']
    st.session_state.chat_messages = deepcopy(snapshot['messages'])
    st.session_state.conversation_context = deepcopy(snapshot.get('context') or new_conversation_context())
    st.session_state.selected_main = snapshot.get('selected_main')
    st.session_state.selected_cmp = snapshot.get('selected_cmp')
    st.session_state.qa_main = snapshot.get('selected_main')
    st.session_state.enterprise_main = snapshot.get('selected_main')
    st.session_state.enterprise_cmp = snapshot.get('selected_cmp')
    st.session_state.qa_period = snapshot.get('period', '自动识别')
    st.session_state.page = '财报问数'
    st.session_state.navigation = '财报问数'
    st.session_state.pop('qa_input', None)


def open_enterprise(company, compare=None):
    st.session_state.selected_main = company
    st.session_state.enterprise_main = company
    on_company_change('enterprise_main')
    if compare and compare != company:
        st.session_state.selected_cmp = compare
        st.session_state.enterprise_cmp = compare
        on_compare_change()
    st.session_state.page = '企业分析'
    st.session_state.navigation = '企业分析'


def return_to_query():
    st.session_state.qa_main = st.session_state.selected_main
    st.session_state.page = '财报问数'
    st.session_state.navigation = '财报问数'


def toggle_details(key):
    st.session_state[key] = not st.session_state.get(key, True)
