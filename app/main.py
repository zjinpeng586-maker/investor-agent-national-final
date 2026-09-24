from __future__ import annotations

import sys
import re
import json
import hashlib
from copy import deepcopy
from html import escape
from uuid import uuid4
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.ui_state import (save_current_session, start_new_session, restore_session, open_enterprise,
                          return_to_query, toggle_details, migrate_legacy_session_state,
                          on_company_change, on_compare_change)
from app.branding import PAGE_TITLE, PRODUCT_NAME, REPORT_SUBTITLE, brand_html, page_icon
from app.messages import NO_REPORTS, NO_METRICS, NO_SOURCE, PARSE_COMPLETE, public_message, public_details
from app.source_viewer import render_source
from app.attribution_view import render_attribution

from core.analysis import (
    METRIC_LABELS,
    build_summary,
    compare_companies,
    compute_alerts,
    generate_report,
    rows_to_df,
    safe_num,
    score_company,
)
from core.charts import make_metric_chart
from core.conversation import confirm_clarification, new_conversation_context, resolve_turn
from core.db import (
    delete_uploaded_company,
    fetch_companies,
    fetch_company_metrics,
    fetch_report_files,
    init_db,
    fetch_metric_sources,
)
from core.evaluation import CATEGORY_LABELS, run_benchmark
from core.llm import PROVIDER_PRESETS, build_llm_config, enhance_report_with_llm, llm_enabled
from core.online_disclosure import guess_exchange, resolve_stock_code, search_disclosures_with_details
from core.qa_engine import answer_question
from core.retrieval import get_index_status
from core.report_pdf import REPORT_LAYOUT_VERSION, report_text_to_pdf_bytes
from core.seed import seed_sample_data
from core.service import ingest_online_pdf_bytes, ingest_online_pdf_url, ingest_pdf_file, ingest_tabular_file
from core.service import prepare_import, commit_import
from core.online_disclosure import download_pdf
from core.storage import configure_workspace, configure_public_session, public_session_enabled, is_public_deployment, can_write


PAGES = ['财报问数', '企业分析', '研究资料库', '数据中心', '评测中心']
DEFAULT_COMPANY = '比亚迪股份有限公司'
DEFAULT_COMPARE = '宁德时代新能源科技股份有限公司'
YEAR_PATTERN = re.compile(r'20\d{2}')


def display_value(value, digits: int = 2) -> str:
    val = safe_num(value)
    return '-' if val is None else f'{val:.{digits}f}'


def metric_rows_to_df(rows) -> pd.DataFrame:
    df = rows_to_df(rows)
    if df.empty or 'year' not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df['year'] = pd.to_numeric(df['year'], errors='coerce')
    return df.dropna(subset=['year']).sort_values('year').reset_index(drop=True)


def has_annual_metrics(frame):
    return (not frame.empty and 'year' in frame.columns
            and any(pd.to_numeric(frame[key], errors='coerce').replace(
                    [float('inf'), float('-inf')], float('nan')).notna().any()
                    for key in METRIC_LABELS if key in frame))


def trace_rows_to_df(rows) -> pd.DataFrame:
    """Show structured trace values without Arrow coercing mixed SQL parameters."""
    return pd.DataFrame([
        {
            key: json.dumps(value, ensure_ascii=False, default=str)
            if isinstance(value, (list, tuple, dict)) else value
            for key, value in row.items()
        }
        for row in public_details(rows or [])
    ])


def normalize_score_detail(score_detail: dict[str, float]) -> dict[str, float]:
    max_map = {'盈利能力': 30, '现金流质量': 25, '偿债稳健性': 25, '成长能力': 20}
    return {
        dim: round(max(0, min((safe_num(score_detail.get(dim)) or 0) / maximum * 100, 100)), 1)
        for dim, maximum in max_map.items()
    }


def make_radar_chart(score_a: dict, name_a: str, score_b: dict | None = None, name_b: str | None = None):
    if score_a.get('available') is False or (score_b and score_b.get('available') is False):
        return None
    dims = ['盈利能力', '现金流质量', '偿债稳健性', '成长能力']
    values_a = [normalize_score_detail(score_a.get('detail', {})).get(dim, 0) for dim in dims]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=values_a + values_a[:1], theta=dims + dims[:1], fill='toself', name=name_a))
    if score_b and name_b:
        values_b = [normalize_score_detail(score_b.get('detail', {})).get(dim, 0) for dim in dims]
        fig.add_trace(go.Scatterpolar(r=values_b + values_b[:1], theta=dims + dims[:1], fill='toself', name=name_b))
    fig.update_layout(
        polar={'radialaxis': {'visible': True, 'range': [0, 100]}},
        height=410,
        margin={'l': 35, 'r': 35, 't': 35, 'b': 35},
        showlegend=True,
    )
    return fig


def make_line_chart(df: pd.DataFrame, metrics: list[str], title: str):
    return make_metric_chart(df, '折线图', metrics, title)


def apply_selected_period(question: str, selected_period: str) -> tuple[str, int | None]:
    """Apply the UI period only when the user did not state a year explicitly."""
    explicit_years = YEAR_PATTERN.findall(question)
    if explicit_years:
        return question, int(explicit_years[0])
    if selected_period != '自动识别':
        year = int(selected_period)
        return f'{question.rstrip("？?。 ")}（{year}年）', year
    return question, None


def filter_result_data(df: pd.DataFrame, parsed: dict) -> pd.DataFrame:
    """Limit result data to the years actually understood by the QA engine."""
    if df.empty or 'year' not in df.columns:
        return pd.DataFrame()
    years = [int(year) for year in parsed.get('years') or []]
    if years:
        return df[df['year'].astype(int).isin(years)].copy()
    if parsed.get('intent') == 'trend_analysis':
        return df.sort_values('year').tail(3).copy()
    return df.copy()


def comparison_result_data(
    primary: str,
    secondary: str,
    data_map: dict[str, pd.DataFrame],
    parsed: dict,
) -> pd.DataFrame:
    """Build a factual two-company table using only data already in data_map."""
    rows = []
    metrics = parsed.get('metrics') or ['revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio']
    for company in [primary, secondary]:
        company_df = filter_result_data(data_map.get(company, pd.DataFrame()), parsed)
        if company_df.empty:
            continue
        selected_rows = company_df if parsed.get('years') else company_df.sort_values('year').tail(1)
        for _, row in selected_rows.iterrows():
            item = {'企业': company, '年度': int(row['year'])}
            for metric in metrics:
                if metric in row and pd.notna(row.get(metric)):
                    item[METRIC_LABELS.get(metric, metric)] = row.get(metric)
            rows.append(item)
    return pd.DataFrame(rows)


def init_state() -> None:
    migrate_legacy_session_state()
    defaults = {
        'page': '财报问数',
        'navigation': '财报问数',
        'chat_messages': [],
        'recent_sessions': [],
        'session_title': '新会话',
        'saved_sessions': [],
        'active_session_id': uuid4().hex,
        'engine_mode': '本地分析模式',
        'investor_profile': '平衡型',
        'online_candidates': [],
        'online_diag': None,
        'online_search_result': None,
        'online_parse_result': None,
        'import_result': None,
        'conversation_context': new_conversation_context(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def apply_styles() -> None:
    css = (ROOT / 'app' / 'styles.css').read_text(encoding='utf-8')
    st.markdown(f'<style>{css}</style>', unsafe_allow_html=True)



def load_workspace():
    companies = fetch_companies()
    name_to_id = {row['name']: row['id'] for row in companies}
    data_map = {name: metric_rows_to_df(fetch_company_metrics(cid)) for name, cid in name_to_id.items()}
    for name, df in data_map.items():
        if not df.empty:
            sources = [dict(row) for row in fetch_metric_sources(name_to_id[name])]
            df['metric_provenance'] = [
                {row['metric']: row for row in sources if row['year'] == int(year)} for year in df['year']
            ]
    analysis_names = [name for name, df in data_map.items() if has_annual_metrics(df)]
    return companies, name_to_id, data_map, analysis_names


def reconcile_company_selectors(analysis_names, companies=None):
    """Reconcile widgets against freshly read facts before creating widgets."""
    default = DEFAULT_COMPANY if DEFAULT_COMPANY in analysis_names else next(iter(analysis_names), None)
    main = st.session_state.get('selected_main')
    if main not in analysis_names:
        main = default
    st.session_state.selected_main = main
    options = [name for name in analysis_names if name != main]
    compare = st.session_state.get('selected_cmp')
    if compare not in options:
        compare = DEFAULT_COMPARE if DEFAULT_COMPARE in options else next(iter(options), None)
    st.session_state.selected_cmp = compare
    # Reassert the authoritative values before registration on every run.
    # A hidden selector can retain old widget metadata; only a fresh state
    # assignment tells the browser to restore its value when it reappears.
    for key in ('qa_main', 'enterprise_main'):
        st.session_state[key] = main
    st.session_state.enterprise_cmp = compare
    context = dict(st.session_state.get('conversation_context') or new_conversation_context())
    removed = False
    for key in ('primary_company', 'compare_company'):
        if context.get(key) and context[key] not in analysis_names:
            context[key] = None
            removed = True
    if removed:
        context.update(awaiting_clarification=False, clarification=None)
    st.session_state.conversation_context = context
    if companies is not None:
        all_names = [row['name'] for row in companies]
        for key in ('source_company', 'delete_company'):
            if key in st.session_state and st.session_state[key] not in all_names:
                st.session_state.pop(key, None)
    st.session_state.pop('post_import_company', None)


def render_sidebar(analysis_names: list[str]):
    with st.sidebar:
        st.markdown(brand_html(), unsafe_allow_html=True)
        st.button('＋ 新建会话', key='new_session', width='stretch', on_click=start_new_session)
        selected_page = st.radio('主导航', PAGES, key='navigation', label_visibility='collapsed')
        st.session_state.page = selected_page
        with st.container(key='sidebar_history'):
            st.caption('最近会话')
            sessions = st.session_state.get('saved_sessions', [])
            if not sessions:
                st.caption('暂无历史会话')
            for session in sessions[:10]:
                st.button(session['title'], key=f"session_{session['id']}", width='stretch',
                          on_click=restore_session, args=(session['id'],))
        with st.container(key='sidebar_settings'):
            with st.expander('系统设置'):
                engine_mode = st.selectbox('分析模式', ['本地分析模式', '云端增强模式'], key='engine_mode')
                llm_config = None
                if engine_mode == '云端增强模式':
                    provider = st.selectbox('模型服务商', list(PROVIDER_PRESETS), key='llm_provider')
                    preset = PROVIDER_PRESETS[provider]
                    model = st.selectbox('模型 / Endpoint', preset['models'], key=f'llm_model_{provider}')
                    if '自定义' in model:
                        model = st.text_input('自定义模型名称或 Endpoint ID', key=f'llm_custom_{provider}')
                    base_url = st.text_input('API Base URL', value=preset['base_url'], key=f'llm_base_{provider}', disabled=is_public_deployment())
                    api_key = st.text_input('API Key', type='password', key=f'llm_key_{provider}')
                    llm_config = build_llm_config(provider, api_key, model, base_url)
                st.selectbox('分析偏好', ['稳健型', '平衡型', '成长型'], key='investor_profile')
    if not analysis_names:
        st.session_state.selected_main = None
        st.session_state.selected_cmp = None
        return llm_config
    return llm_config



def render_header(page: str, selected_main: str, main_df: pd.DataFrame) -> None:
    mode = '云端增强' if st.session_state.engine_mode == '云端增强模式' else '本地分析'
    data_status = '结构化数据已连接' if not main_df.empty else '尚未接入年度指标'
    with st.container(key='app_header'):
        left, right = st.columns([2, 3])
        left.markdown(f'## {page}')
        right.markdown(
            f'<div class="context-bar"><span class="fu-badge"><span class="fu-dot"></span>'
            f'{mode} · {data_status}</span><span class="fu-current-company">'
            f'当前企业：{escape(selected_main)}</span></div>', unsafe_allow_html=True,
        )



def run_question(
    question: str,
    selected_main: str,
    selected_cmp: str,
    selected_period: str,
    all_names: list[str],
    data_map,
    llm_config,
):
    page_year = int(selected_period) if selected_period != '自动识别' else None
    turn = resolve_turn(
        st.session_state.conversation_context,
        question,
        all_names,
        page_company=selected_main,
        page_compare=selected_cmp,
        page_year=page_year,
        profile=st.session_state.investor_profile,
    )
    st.session_state.conversation_context = turn['context']
    if turn['status'] == 'needs_clarification':
        return
    execute_resolved_question(question, turn, selected_main, selected_cmp, all_names, data_map, llm_config)


def execute_resolved_question(
    question: str,
    turn: dict,
    selected_main: str,
    selected_cmp: str,
    all_names: list[str],
    data_map,
    llm_config,
):
    resolved = turn['resolved']
    result = answer_question(
        question,
        selected_main,
        selected_cmp,
        all_names,
        data_map,
        st.session_state.investor_profile,
        llm_config if llm_enabled(llm_config) else None,
        resolved_context=resolved,
    )
    # An answer is an immutable view of the facts verified at execution time.
    # Later imports must not rewrite charts, sources or downloads in its history.
    answer_companies = (result.get('parsed') or {}).get('companies') or []
    result['data_snapshot'] = {
        name: deepcopy(filter_result_data(data_map.get(name, pd.DataFrame()), result.get('parsed') or {}).to_dict('records'))
        for name in answer_companies
    }
    result['report_files_snapshot'] = [dict(row) for row in fetch_report_files() if row['company_name'] in answer_companies]
    st.session_state.chat_messages.append({
        'question': question,
        'effective_question': question,
        'applied_year': (resolved.get('years') or [None])[0],
        'selected_compare': selected_cmp,
        'result': result,
    })
    if st.session_state.session_title == '新会话':
        st.session_state.session_title = question[:22]
    save_current_session()


def render_answer_card(item: dict, data_map: dict, name_to_id: dict, index: int):
    live_analysis_names = {name for name, frame in data_map.items() if has_annual_metrics(frame)}
    result = item['result']
    if 'data_snapshot' in result:
        data_map = {name: pd.DataFrame(deepcopy(rows)) for name, rows in result['data_snapshot'].items()}
    parsed = result.get('parsed') or {}
    companies = [name for name in parsed.get('companies') or [result.get('company')] if name]
    company = result.get('company') or (companies[0] if companies else None)
    metrics = parsed.get('metrics') or ['revenue', 'net_profit', 'operating_cashflow']
    with st.chat_message('user'):
        st.write(item['question'])
    with st.chat_message('assistant'):
        st.markdown('#### 结论摘要')
        st.write(result.get('answer') or result.get('reason') or '当前条件下暂无可核验的回答。')
        if result.get('status') in {'unknown_company', 'unsupported_period', 'missing_scope'}:
            st.warning(result.get('reason') or '请求的企业、期间或指标未得到完整匹配。')
            render_query_trace(result)
            return
        frames = {name: filter_result_data(data_map.get(name, pd.DataFrame()), parsed) for name in companies}
        current_df = frames.get(company, pd.DataFrame())
        if result.get('status') == 'no_evidence' and not (result.get('sql_result') or {}).get('rows'):
            render_query_trace(result)
            return
        render_attribution(result.get('attribution'),
                           key=f'attribution_{st.session_state.active_session_id}_{index}')
        actions = st.columns([1.35, 1.35, 3, 1.25])
        second = next((name for name in companies if name != company), None)
        actions[0].button('进入企业分析', key=f'qa_enterprise_{index}', on_click=open_enterprise,
                          args=(company, second if second in live_analysis_names else None),
                          disabled=company not in live_analysis_names, width='stretch',
                          help='该企业已无可分析的年度指标，历史回答仍保留。' if company not in live_analysis_names else None)
        report_key = f'qa_report_open_{st.session_state.active_session_id}_{index}'
        if actions[1].button('生成分析报告', key=f'qa_report_{index}', disabled=current_df.empty, width='stretch'):
            st.session_state[report_key] = True
        detail_key = f'qa_details_open_{st.session_state.active_session_id}_{index}'
        detail_open = st.session_state.get(detail_key, True)
        actions[3].button('收起详情' if detail_open else '展开详情', key=f'qa_details_{index}',
                          on_click=toggle_details, args=(detail_key,), width='stretch')
        if st.session_state.get(report_key) and not current_df.empty:
            with st.expander('分析报告下载', expanded=True):
                years = '、'.join(str(int(y)) for y in current_df['year'])
                st.caption(f'报告范围：{company}｜{years}年；只使用当前范围内已有的指标。')
                report_downloads(company, current_df, compute_alerts(current_df), None,
                                 f'qa_download_{st.session_state.active_session_id}_{index}', None)
        if not detail_open:
            return
        tab_chart, tab_data, tab_evidence = st.tabs(['图表', '数据', '依据'], key=f'answer_tabs_{st.session_state.active_session_id}_{index}', on_change='rerun')
        table_rows = []
        for name, frame in frames.items():
            for _, row in frame.iterrows():
                entry = {'企业': name, '年度': int(row['year'])}
                for metric in metrics:
                    entry[metric] = row.get(metric)
                table_rows.append(entry)
        values = pd.DataFrame(table_rows)
        with tab_chart:
            if len(companies) <= 1:
                chart = make_line_chart(current_df, metrics, f'{company or "当前企业"} 财务指标趋势')
                if chart is not None:
                    st.plotly_chart(chart, width='stretch', key=f'qa_chart_{index}')
                else:
                    st.info('没有可绘制的指标，缺失数据不会填成零。')
            elif not values.empty:
                from core.analysis import METRIC_UNITS
                units = dict.fromkeys(METRIC_UNITS.get(metric, '') for metric in metrics if metric in METRIC_LABELS)
                for unit_index, unit in enumerate(units):
                    selected = [metric for metric in metrics if METRIC_UNITS.get(metric) == unit and metric in values]
                    if not selected:
                        continue
                    long = values.melt(id_vars=['企业', '年度'], value_vars=selected, var_name='指标', value_name='数值').dropna(subset=['数值'])
                    long['指标'] = long['指标'].map(METRIC_LABELS)
                    long['企业 / 年度'] = long['企业'].str.replace('股份有限公司', '', regex=False) + ' / ' + long['年度'].astype(str)
                    if not long.empty:
                        fig = px.bar(long, x='企业 / 年度', y='数值', color='指标', barmode='group',
                                     title=f'企业指标对比（{unit}）', labels={'数值': unit})
                        fig.update_xaxes(type='category')
                        st.plotly_chart(fig, width='stretch', key=f'qa_compare_chart_{index}_{unit_index}')
        with tab_data:
            if values.empty:
                st.info('当前条件下没有可展示的数据。')
            else:
                from core.analysis import METRIC_UNITS
                labels = {metric: f'{METRIC_LABELS.get(metric, metric)}（{METRIC_UNITS.get(metric, "文本")}）' for metric in metrics}
                st.dataframe(values.rename(columns=labels), width='stretch', hide_index=True)
        with tab_evidence:
            st.text(public_message(result.get('evidence'), NO_SOURCE))
            source_rows = []
            for name, frame in frames.items():
                for _, snapshot_row in frame.iterrows():
                    for metric, source in (snapshot_row.get('metric_provenance') or {}).items():
                        if metric in metrics:
                            source_rows.append({'企业': name, '年度': int(snapshot_row['year']), '指标': METRIC_LABELS.get(metric, metric),
                                                '数值': str(json.loads(source['value_json'])), '单位': source['normalized_unit'],
                                                '文件': source['file_name'] or '旧版来源待复核', '页码': source['page'], '单元格': source['cell'],
                                                '原始数值': source['raw_value'], '原始单位': source['raw_unit'], '导入版本': source['import_id']})
            if source_rows:
                st.dataframe(trace_rows_to_df(source_rows), width='stretch', hide_index=True)
            citations = (result.get('retrieval_result') or {}).get('citations') or []
            for number, citation in enumerate(citations):
                st.markdown(f'**来源 {citation.get("number", number + 1)}：{citation.get("file_name", "原始文档")}**')
                st.caption(f'报告年份：{citation.get("report_year", "未标注")}｜PDF第{citation.get("page", "-")}页')
                st.write(citation.get('snippet', ''))
                source = dict(citation)
                if not source.get('file_path'):
                    report = next((dict(row) for row in result.get('report_files_snapshot', []) if row['file_name'] == source.get('file_name') and row['company_name'] in companies and row['report_year'] in (parsed.get('years') or [row['report_year']])), None)
                    if report:
                        source['file_path'] = report['file_path']
                render_source(source, f'qa_{index}_{number}')
        years = '、'.join(map(str, parsed.get('years') or [])) or '已明确说明的可用期间'
        period = parsed.get('period') or 'annual'
        st.caption(f'当前条件：{"、".join(companies)}｜{years}｜' + ('年度报告' if period == 'annual' else str(period)))
        render_query_trace(result)



def render_qa_page(selected_main, selected_cmp, all_names, data_map, name_to_id, llm_config):
    with st.container(key='qa_scope'):
        c1, c2, c3 = st.columns([2.5, 1.45, 3])
        with c1:
            if st.session_state.get('qa_main') not in all_names:
                st.session_state.qa_main = selected_main
            selected_main = st.selectbox('当前理解企业', all_names, key='qa_main',
                                         on_change=on_company_change, args=('qa_main',))
            st.session_state.selected_main = selected_main
        with c2:
            years = data_map[selected_main]['year'].astype(int).tolist()
            period_options = ['自动识别'] + [str(y) for y in reversed(years)]
            if st.session_state.get('qa_period') not in period_options:
                st.session_state.qa_period = '自动识别'
            selected_period = st.selectbox('当前理解期间', period_options, key='qa_period')
        c3.markdown('<div class="fu-scope-note">合并口径 · 人民币</div>', unsafe_allow_html=True)
    context = st.session_state.conversation_context
    pending = None
    messages = st.session_state.chat_messages
    if not messages:
        with st.container(key='qa_welcome'):
            st.markdown('<div class="fu-welcome"><div class="fu-eyebrow">自然语言财报分析</div>'
                        '<h2>查询财务数据，核验分析依据</h2>'
                        '<p>支持指标查询、趋势分析、企业对比与连续问答。</p></div>', unsafe_allow_html=True)
        recommended = [
            ('**营收趋势分析**\n\n查看所选期间的营业收入变化', f'{selected_main}近三年营业收入变化如何？'),
            ('**盈利表现分析**\n\n查看归母净利润及同比变化', f'{selected_main}净利润变化如何？'),
            ('**企业指标对比**\n\n核心指标对比，了解经营差异', f'{selected_main}和{selected_cmp}核心指标对比'),
        ]
        with st.container(key='qa_suggestions'):
            cards = st.columns(3)
            for idx, (label, prompt) in enumerate(recommended):
                if cards[idx].button(label, width='stretch', key=f'recommended_{idx}', help=prompt):
                    pending = prompt

    if context.get('awaiting_clarification') and context.get('clarification'):
        with st.container(border=True, key='qa_clarification'):
            clarification = context['clarification']
            st.info(clarification['question'])
            option_map = {option['label']: option['value'] for option in clarification['options']}
            selected_option = st.selectbox('请选择', list(option_map), key='clarification_choice')
            if st.button('确认并继续', type='primary', key='confirm_clarification'):
                turn = confirm_clarification(context, option_map[selected_option], all_names, st.session_state.investor_profile)
                st.session_state.conversation_context = turn['context']
                execute_resolved_question(turn['question'], turn, selected_main, selected_cmp, all_names, data_map, llm_config)
                st.rerun()

    if messages:
        with st.container(key='qa_results'):
            if len(messages) > 1:
                with st.expander(f'查看此前 {len(messages) - 1} 轮问答', expanded=False):
                    for idx, item in enumerate(messages[:-1]):
                        with st.expander(item['question'], expanded=False):
                            render_answer_card(item, data_map, name_to_id, idx)
            render_answer_card(messages[-1], data_map, name_to_id, len(messages)-1)

    with st.container(key='qa_composer'):
        with st.form('question_form', clear_on_submit=True):
            question = st.text_area('自然语言问题', placeholder='例如：这家公司近三年营收变化如何？',
                                    height=100, max_chars=5000, key='qa_input', label_visibility='collapsed')
            note, submit = st.columns([5, 1.25], vertical_alignment='bottom')
            note.markdown('<div class="fu-composer-note">支持七项核心财务指标总览、趋势、风险和连续问答；新入库资料即时参与分析</div>', unsafe_allow_html=True)
            with submit:
                submitted = st.form_submit_button('开始分析', type='primary', width='stretch')
        if submitted:
            if question.strip():
                pending = question.strip()
            else:
                st.warning('请输入需要分析的财务问题。')
    if pending:
        with st.spinner('正在理解问题并查询财报数据…'):
            run_question(pending, selected_main, selected_cmp, selected_period, all_names, data_map, llm_config)
        st.rerun()
    st.markdown('<p class="fu-footnote">回答依据已接入数据与来源，支持连续问答和原文核验。</p>', unsafe_allow_html=True)
    with st.expander('无需云端模型的问答示例'):
        st.write('查询总览：比亚迪2024年核心财务指标是多少？')
        st.write('连续追问：那2023年呢？')
        st.write('指标别称：比亚迪2024年净资产回报率、每股盈利是多少？')
        st.write('趋势与归因：比亚迪2024年净利润为什么变化？')
        st.caption('可将企业替换为新入库企业或其股票代码。缺少的指标不会补造；报告解释仍需真实原文。')



def report_downloads(company: str, df: pd.DataFrame, alerts: list[dict], compare_payload, key_prefix: str, llm_config):
    report_text = generate_report(company, df, alerts, compare_payload, st.session_state.investor_profile)
    signature = hashlib.sha256((report_text + st.session_state.investor_profile).encode()).hexdigest()
    report_state_key = f'{key_prefix}_enhanced_text'
    stored = st.session_state.get(report_state_key, {})
    if stored.get('signature') == signature:
        report_text = stored['text']
    if llm_enabled(llm_config) and st.button('使用云端模型增强报告文字', key=f'{key_prefix}_enhance'):
        try:
            report_text = enhance_report_with_llm(llm_config, report_text, company, st.session_state.investor_profile)
            st.session_state[report_state_key] = {'signature': signature, 'text': report_text}
            st.success('报告事实保持不变，已处理云端阅读提示。')
        except Exception:
            st.info('云端服务暂未返回有效结果，继续使用可信本地报告。')
    score = score_company(df)
    txt_col, pdf_col = st.columns(2)
    txt_col.download_button(
        '下载 TXT 报告', report_text.encode('utf-8'), f'{PRODUCT_NAME}_{company}_{REPORT_SUBTITLE}.txt', 'text/plain',
        width='stretch', key=f'{key_prefix}_txt',
    )
    try:
        pdf_signature = hashlib.sha256((REPORT_LAYOUT_VERSION + report_text + company + df.to_json(force_ascii=False, default_handler=str)
                                        + json.dumps(score, ensure_ascii=False, default=str)).encode()).hexdigest()
        cache = st.session_state.setdefault('pdf_report_cache', {})
        if pdf_signature not in cache:
            cache[pdf_signature] = report_text_to_pdf_bytes(report_text, company, df=df, score=score)
            while len(cache) > 6:
                cache.pop(next(iter(cache)))
        pdf_bytes = cache[pdf_signature]
        pdf_col.download_button(
            '下载图文 PDF 报告', pdf_bytes, f'{PRODUCT_NAME}_{company}_{REPORT_SUBTITLE}.pdf', 'application/pdf',
            width='stretch', key=f'{key_prefix}_pdf',
        )
    except Exception as exc:
        pdf_col.warning(f'PDF 报告生成失败：{public_message(exc)}')


def render_enterprise_page(analysis_names, data_map, llm_config):
    pick1, pick2 = st.columns(2)
    if st.session_state.get('enterprise_main') not in analysis_names:
        st.session_state.enterprise_main = st.session_state.selected_main
    selected_main = pick1.selectbox('分析企业', analysis_names, key='enterprise_main',
                                    on_change=on_company_change, args=('enterprise_main',))
    compare_options = [name for name in analysis_names if name != selected_main]
    if compare_options and st.session_state.get('enterprise_cmp') not in compare_options:
        st.session_state.enterprise_cmp = next(
            (name for name in compare_options if name == st.session_state.selected_cmp), compare_options[0]
        )
    selected_cmp = pick2.selectbox('对比企业', compare_options, key='enterprise_cmp',
                                   on_change=on_compare_change) if compare_options else None
    st.session_state.selected_main, st.session_state.selected_cmp = selected_main, selected_cmp
    df, cmp_df = data_map[selected_main], data_map.get(selected_cmp, pd.DataFrame())
    alerts, score = compute_alerts(df), score_company(df)
    compare_payload = compare_companies(
        selected_main, df, selected_cmp, cmp_df, st.session_state.investor_profile
    ) if selected_cmp != selected_main and not cmp_df.empty else None

    latest = df.iloc[-1]
    metrics = st.columns(6)
    values = [
        ('最新年度', str(int(latest['year']))), ('营业收入(亿元)', display_value(latest.get('revenue'))),
        ('归母净利润(亿元)', display_value(latest.get('net_profit'))),
        ('经营现金流(亿元)', display_value(latest.get('operating_cashflow'))),
        ('ROE(%)', display_value(latest.get('roe'))), ('综合评分', str(score['score']) if score.get('available') else '数据不足'),
    ]
    for col, (label, value) in zip(metrics, values):
        col.metric(label, value)
    st.info(build_summary(selected_main, df, alerts, st.session_state.investor_profile))

    tab_overview, tab_compare, tab_report = st.tabs(['经营概览与风险', '企业对比', '分析报告'], key='enterprise_tabs', on_change='rerun')
    with tab_overview:
        left, right = st.columns([1.35, 1])
        fig = make_line_chart(df, ['revenue', 'net_profit', 'operating_cashflow'], f'{selected_main} 核心指标趋势')
        if fig is not None:
            left.plotly_chart(fig, width='stretch', key='enterprise_trend')
        radar = make_radar_chart(score, selected_main)
        if radar is not None:
            right.plotly_chart(radar, width='stretch', key='enterprise_radar')
        else:
            right.info(score.get('reason') or '综合评分所需数据不足。')
        st.dataframe(df[[c for c in ['year'] + list(METRIC_LABELS) if c in df.columns]], width='stretch', hide_index=True)
        st.markdown('#### 自定义图表分析')
        chart_col, metric_col = st.columns([1, 2])
        if st.session_state.get('enterprise_chart_type') == '饼图':
            st.session_state.enterprise_chart_type = '柱形图'
        chart_type = chart_col.selectbox('图表类型', ['折线图', '柱形图'], key='enterprise_chart_type')
        chart_metrics = metric_col.multiselect(
            '分析指标', list(METRIC_LABELS), default=['revenue', 'net_profit', 'operating_cashflow'],
            format_func=lambda value: METRIC_LABELS.get(value, value), key='enterprise_chart_metrics',
        )
        custom_chart = make_metric_chart(df, chart_type, chart_metrics, f'{selected_main} {chart_type}分析')
        if custom_chart is not None:
            st.plotly_chart(custom_chart, width='stretch', key='enterprise_custom_chart')
        else:
            st.info('当前选择的指标暂无足够数据，请更换指标或图表类型。')
        st.markdown('#### 风险预警与依据')
        for idx, alert in enumerate(alerts):
            icon = {'red': '🔴', 'orange': '🟠', 'yellow': '🟡', 'green': '🟢'}.get(alert['level'], '🔵')
            with st.expander(f'{icon} {alert["title"]}', expanded=idx == 0):
                st.write(alert['message'])
                st.write(f'规则依据：{alert.get("rule", "-")}')
                st.write(f'数据依据：{alert.get("basis", "-")}')
                st.write(f'数据来源：{alert.get("source", "-")}')
    with tab_compare:
        if compare_payload is None:
            st.info('当前没有可用于对比的第二家企业。')
        else:
            cols = st.columns(3)
            cols[0].metric(f'{selected_main} 综合评分', compare_payload['score_a']['score'] if compare_payload['score_a'].get('available') else '数据不足')
            cols[1].metric(f'{selected_cmp} 综合评分', compare_payload['score_b']['score'] if compare_payload['score_b'].get('available') else '数据不足')
            cols[2].metric('规则评分比较', compare_payload['recommended'] or '无综合倾向')
            st.write(compare_payload['reasoning'])
            radar = make_radar_chart(compare_payload['score_a'], selected_main, compare_payload['score_b'], selected_cmp)
            if radar is not None:
                st.plotly_chart(radar, width='stretch', key='enterprise_compare_radar')
            st.dataframe(compare_payload['score_table'], width='stretch', hide_index=True)
            st.dataframe(compare_payload['table'], width='stretch', hide_index=True)
    with tab_report:
        st.markdown('#### 生成当前企业分析报告')
        st.caption('报告包含年度指标、趋势、风险与来源；数据完整时才展示综合评分。')
        report_downloads(selected_main, df, alerts, compare_payload, 'enterprise_report', llm_config)
    st.button('返回连续问答', width='stretch', on_click=return_to_query)


def render_library_page(companies, name_to_id):
    reports = fetch_report_files()
    st.markdown('### 已接入研究资料')
    st.caption('当前展示真实接入文档及页码级 RAG 索引状态；索引可在首次问答检索时自动建立，并按文件变化自动刷新。')
    if not reports:
        st.info(NO_REPORTS)
        return
    rows = []
    for report in reports:
        rows.append({
            '文档名称': report['file_name'],
            '类型': report['file_type'] or '未分类',
            '企业': report['company_name'] or '未关联',
            '报告年份': str(report['report_year']) if report['report_year'] else '-',
            '解析状态': report['parse_status'] or '未知',
            'RAG索引状态': get_index_status(dict(report)),
            '接入时间': report['uploaded_at'],
        })
    library_df = pd.DataFrame(rows)
    f1, f2 = st.columns(2)
    keyword = f1.text_input('搜索文档或企业', placeholder='输入文档名、公司名')
    types = ['全部'] + sorted(library_df['类型'].dropna().unique().tolist())
    selected_type = f2.selectbox('资料类型', types)
    filtered = library_df
    if keyword:
        mask = filtered.astype(str).apply(lambda col: col.str.contains(keyword, case=False, na=False, regex=False)).any(axis=1)
        filtered = filtered[mask]
    if selected_type != '全部':
        filtered = filtered[filtered['类型'] == selected_type]
    st.dataframe(filtered, width='stretch', hide_index=True)
    with st.expander('查看资料来源详情'):
        choices = [f'{r["file_name"]}｜{r["company_name"] or "未关联"}｜{r["report_year"] or "未标年"}｜记录{r["id"]}' for r in reports]
        picked = st.selectbox('选择资料', choices)
        report = reports[choices.index(picked)]
        st.write(f'文件名称：{report["file_name"]}')
        st.write(f'解析状态：{report["parse_status"]}')
        st.write(public_message(report['note'], NO_SOURCE))
        st.info(f'RAG 索引状态：{get_index_status(dict(report))}。引用仅在真实页级文本检索命中后展示。')
        render_source(dict(report), f'library_original_{report["id"]}')


def clear_online_attempt(*, clear_search=True):
    if clear_search:
        st.session_state.online_candidates = []
        st.session_state.online_diag = None
        st.session_state.online_search_result = None
        st.session_state.pop('disclosure_candidate', None)
    st.session_state.online_parse_result = None
    st.session_state.import_result = None
    if (st.session_state.get('pending_import') or {}).get('_ui_channel') == 'online':
        st.session_state.pop('pending_import', None)


def render_import_result():
    result = st.session_state.get('import_result')
    if not result:
        return
    names = '、'.join(result.get('company_names') or result.get('companies') or [])
    status = result.get('status')
    if status == 'success' and result.get('ok'):
        st.success(f'入库成功：{names} 已接入资料库，本次新增 {result.get("inserted_metrics", 0)} 项、更新 {result.get("updated_metrics", 0)} 项财务指标。')
        st.caption('该企业现已支持财报问数、连续问答、企业分析及对比分析。')
    elif status == 'document_only':
        st.warning('文件已保存，但未识别到可用于结构化分析的年度财务指标。该记录已标记为“待补充指标”，暂不可用于企业选择与问答分析。')
    elif status == 'unchanged':
        st.info('本次未新增或更新指标，已有指标已保留。如需修改，请重新复核并选择覆盖。')
    elif result.get('committed'):
        st.warning('数据已提交，但入库结果复核未完成。请刷新数据资产与来源核验已写入记录，避免重复提交。')
        if result.get('error_message'):
            st.caption(public_message(result['error_message']))
    else:
        reason = public_message(result.get('error_message') or '；'.join(result.get('errors') or []), '未完成数据库提交').rstrip('。')
        st.error(f'入库失败：{reason}。本次操作未写入可用分析数据。')
    if result.get('warnings'):
        with st.expander('本次接入详情'):
            for message in result['warnings']:
                st.write(public_message(message))



def prepare_online_preview(url, *, origin, expected_company=None, stock_code=None,
                           report_title=None, report_type=None, year_hint=None):
    clear_online_attempt(clear_search=False)
    try:
        with st.spinner('正在下载披露文件……'):
            file_name, content = download_pdf(url)
    except Exception as exc:
        st.session_state.online_parse_result = {'status': 'failed', 'origin': origin,
                                                'message': f'文件下载失败：{public_message(exc)}'}
        return
    try:
        with st.spinner('正在解析披露文件并检查企业、年份和单位……'):
            batch = prepare_import(file_name, content, expected_company=expected_company,
                                   stock_code=stock_code, source_label='online_disclosure' if origin == 'candidate' else 'online_pdf_url',
                                   year_hint=year_hint, report_title=report_title, source_url=url,
                                   report_type=report_type)
        batch.update(_ui_channel='online', _ui_origin=origin, _ui_url=url)
        st.session_state.pending_import = batch
        if batch.get('can_commit'):
            status, message = 'success', PARSE_COMPLETE
        elif batch.get('can_save_document'):
            status, message = 'document_only', '文件可读取，但未提取到有效年度指标；可复核后仅保存文档，暂不能用于结构化分析。'
        else:
            status, message = 'failed', '下载完成，但解析失败：' + public_message('；'.join(batch.get('errors') or ['未提取到有效年度指标']))
        st.session_state.online_parse_result = {'status': status, 'message': message, 'origin': origin}
    except Exception as exc:
        st.session_state.online_parse_result = {'status': 'failed', 'origin': origin, 'message': f'PDF 解析失败：{public_message(exc)}'}


def refresh_imported_data(result):
    st.session_state.post_import_company = result.get('company_names') or result.get('companies') or []
    _, _, _, names = load_workspace()
    reconcile_company_selectors(names)
    st.session_state.pop('pending_import', None)
    st.session_state.online_parse_result = None
    st.rerun()


def render_ingested_reports():
    st.markdown('#### 已接入企业 / 已入库公开披露')
    reports = [dict(row) for row in fetch_report_files()]
    source_labels = {'exchange': '交易所检索', 'direct_url': '官方链接接入', 'upload': '本地文件接入', 'builtin': '内置数据'}
    status_labels = {'success': '已入库', 'ingested': '已入库', 'imported': '已入库', 'document_only': '待补充指标',
                     'pending_metrics': '待补充指标', 'failed': '失败'}
    if not reports:
        st.info('暂无已确认接入的报告。检索候选和解析预览不会自动入库。')
        return
    rows = []
    for row in reports:
        status = row.get('ingest_status')
        rows.append({'企业名称': row.get('company_name') or '未关联', '股票代码': row.get('stock_code') or '',
                     '披露标题或文件名': row.get('report_title') or row['file_name'],
                     '报告类型': row.get('report_type') or '未标注', '报告年份': str(row.get('report_year') or '未识别'),
                     '数据来源': source_labels.get(row.get('source_type'), row.get('source_type') or '待复核'),
                     '来源 URL': row.get('source_url') or '',
                     '接入状态': status_labels.get(status, '待补充指标'),
                     '新增/更新指标数量': row.get('metric_count') or 0, '入库时间': row['uploaded_at']})
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    if can_write():
        with st.expander('报告记录管理'):
            options = {row['id']: f'{row["company_name"] or "未关联"}｜{row["file_name"]}｜记录{row["id"]}'
                       for row in reports if row.get('source_type') != 'builtin' and row.get('file_type') != 'builtin'}
            if options:
                if st.session_state.get('delete_report') not in options:
                    st.session_state.pop('delete_report', None)
                identifier = st.selectbox('选择报告记录', list(options), format_func=options.get, key='delete_report')
                confirmed = st.checkbox('确认删除该报告记录及其独有指标来源', key=f'delete_report_confirm_{identifier}')
                if st.button('删除所选报告记录', disabled=not confirmed, key='delete_report_button'):
                    from core.db import delete_report_file
                    try:
                        if delete_report_file(identifier):
                            st.session_state.import_result = None
                            st.session_state.pop('pending_import', None)
                            st.rerun()
                    except Exception as exc:
                        st.error(f'删除失败：{public_message(exc)}')


def render_data_center(companies, name_to_id, data_map):
    st.markdown('### 数据接入与复核')
    import_feedback = st.empty()
    writable = can_write()
    if not writable:
        st.info('当前部署为只读模式，无法执行数据导入或删除操作。')
    elif is_public_deployment():
        st.info('在线会话资料库：支持公开披露检索、下载复核和真实入库。导入后可在财报问数、企业分析中使用，仅当前浏览器会话可见。刷新、断开连接或服务重启后不保证保留，请自行保存原始文件。本会话限20个文件、合计100 MB。')
    tab_upload, tab_online, tab_sources = st.tabs(['本地文件接入', '公开披露接入', '数据资产与来源'], key='data_center_tabs', on_change='rerun')
    with tab_upload:
        if writable:
            st.markdown('#### 01 选择文件并解析')
            upload = st.file_uploader('选择 CSV、Excel 或年度 PDF', type=['csv', 'xlsx', 'xls', 'pdf'], key='review_upload')
            manual = st.text_input('企业名称（文件未标注时填写）', key='import_company')
            c1, c2 = st.columns(2)
            money = c1.selectbox('金额单位（仅补充未标注单位）', ['自动识别', '元', '千元', '万元', '百万元', '亿元'], key='import_money')
            percent = c2.selectbox('百分比口径（仅补充未标注单位）', ['自动识别', '百分数', '比例'], key='import_percent')
            if upload:
                content = upload.getvalue()
                signature = hashlib.sha256(content + f'{upload.name}|{manual}|{money}|{percent}'.encode()).hexdigest()
                current = st.session_state.get('pending_import')
                if current and current.get('_ui_channel') == 'upload' and current.get('_ui_signature') != signature:
                    st.session_state.pop('pending_import', None)
                    st.session_state.import_result = None
                if st.button('解析并预览', type='primary', key='prepare_upload'):
                    st.session_state.pop('pending_import', None)
                    st.session_state.import_result = None
                    try:
                        with st.spinner('正在按表格行列识别指标并检查单位…'):
                            batch = prepare_import(upload.name, content, manual_company_name=manual.strip() or None,
                                                   money_unit=None if money == '自动识别' else money,
                                                   percent_unit=None if percent == '自动识别' else percent)
                        batch.update(_ui_channel='upload', _ui_signature=signature)
                        st.session_state.pending_import = batch
                    except Exception as exc:
                        st.error(f'解析失败：{public_message(exc)}')
            elif (st.session_state.get('pending_import') or {}).get('_ui_channel') == 'upload':
                st.session_state.pop('pending_import', None)
            batch = st.session_state.get('pending_import')
            if batch and batch.get('_ui_channel') == 'upload':
                render_import_review(batch)
    with tab_online:
        query = st.text_input('公司名称或股票代码', value='比亚迪', key='disclosure_query')
        c1, c2, c3 = st.columns(3)
        source = c1.selectbox('公开披露来源', ['自动判断', '上交所', '深交所'], key='disclosure_exchange')
        report_type = c2.selectbox('报告类型', ['年度报告', '半年度报告', '季度报告', '全部公告'], key='disclosure_type')
        year = c3.selectbox('年份', ['2025', '2024', '2023', '2022', '2021'], index=1, key='disclosure_year')
        search_signature = (query, source, report_type, year)
        if st.session_state.get('disclosure_search_signature') != search_signature:
            clear_online_attempt()
            st.session_state.disclosure_search_signature = search_signature
        if st.button('检索公开披露', width='stretch', key='search_disclosures'):
            clear_online_attempt()
            try:
                with st.spinner('正在检索公开披露……'):
                    result = search_disclosures_with_details(query, source, report_type, int(year), limit=20)
                st.session_state.online_search_result = result
                st.session_state.online_candidates = [item.as_dict() if hasattr(item, 'as_dict') else dict(item)
                                                      for item in result.get('candidates', [])]
                st.session_state.online_diag = result.get('diagnostics')
            except Exception as exc:
                st.session_state.online_search_result = {'status': 'failed', 'message': str(exc)}
                st.session_state.online_diag = {'error': str(exc), 'exception': type(exc).__name__}
        search_result = st.session_state.get('online_search_result')
        if search_result:
            status = search_result.get('status')
            if status == 'success':
                st.success(f'检索完成：共获取 {search_result["candidate_count"]} 条符合条件的公开披露。')
                if search_result.get('request_failure_count'):
                    st.warning('部分检索请求未成功，已展示其他请求返回的有效候选，可展开“检索详情”查看详情。')
            elif status == 'empty':
                st.info('检索完成：未获取到符合当前条件的公开披露，请核验企业名称、证券代码、交易所、报告类型及报告期。')
            else:
                reason = public_message(search_result.get('message'), '未获得有效响应').removeprefix('检索失败：').split('。请查看检索详情')[0].rstrip('。')
                st.error(f'检索失败：{reason}。请查看检索详情后重试。')
        candidates = st.session_state.get('online_candidates') or []
        if candidates:
            st.dataframe(pd.DataFrame(candidates), width='stretch', hide_index=True)
            labels = [f'{i+1}. {row.get("公告标题", "公告")}' for i, row in enumerate(candidates)]
            if st.session_state.get('disclosure_candidate') not in labels:
                st.session_state.pop('disclosure_candidate', None)
            picked = st.selectbox('选择要预览的公告', labels, key='disclosure_candidate')
            candidate = candidates[labels.index(picked)]
            old = st.session_state.get('pending_import') or {}
            if old.get('_ui_origin') == 'candidate' and old.get('_ui_url') != candidate['PDF链接']:
                clear_online_attempt(clear_search=False)
            if writable and st.button('下载并解析所选公告', key='prepare_disclosure'):
                title_year = re.search(r'((?:19|20)\d{2})\s*年', candidate.get('公告标题') or '')
                prepare_online_preview(candidate['PDF链接'], origin='candidate', expected_company=candidate.get('公司名称') or None,
                                       stock_code=candidate.get('股票代码') or None, report_title=candidate.get('公告标题'),
                                       report_type=report_type, year_hint=int(title_year.group(1)) if title_year else None)
        if writable:
            url = st.text_input('官方年报 PDF 链接', key='direct_pdf_url', placeholder='https://static.cninfo.com.cn/...PDF')
            if st.session_state.get('_direct_url_signature') != url.strip():
                st.session_state._direct_url_signature = url.strip()
                st.session_state.import_result = None
                if (st.session_state.get('online_parse_result') or {}).get('origin') == 'url':
                    clear_online_attempt(clear_search=False)
            st.caption('仅支持允许的官方 HTTPS 年报来源；下载会检查重定向、文件大小和耗时。')
            if st.button('下载并解析链接', key='prepare_pdf_url'):
                prepare_online_preview(url.strip(), origin='url')
            parsed = st.session_state.get('online_parse_result')
            if parsed:
                {'success': st.success, 'document_only': st.warning}.get(parsed['status'], st.error)(parsed['message'])
            batch = st.session_state.get('pending_import')
            if batch and batch.get('_ui_channel') == 'online':
                render_import_review(batch)
        with st.expander('检索详情'):
            st.json(public_details(st.session_state.get('online_diag') or {'状态': '尚未检索'}))
    with tab_sources:
        render_ingested_reports()
        if companies:
            selected_name = st.selectbox('查看企业指标来源', [row['name'] for row in companies], key='source_company')
            history = st.checkbox('显示历次导入来源', key='source_history')
            sources = fetch_metric_sources(name_to_id[selected_name], include_history=history)
            rows = [{'年度': row['year'], '指标': METRIC_LABELS.get(row['metric'], row['metric']), '数值': str(json.loads(row['value_json'])),
                     '单位': row['normalized_unit'], '文件': row['file_name'], '页码': row['page'], '单元格': row['cell'],
                     '原值': row['raw_value'], '原单位': row['raw_unit'], '导入版本': row['import_id'], '当前有效': bool(row['is_current'])} for row in sources]
            if rows:
                st.dataframe(trace_rows_to_df(rows), width='stretch', hide_index=True)
            else:
                st.info(NO_SOURCE)
            if sources:
                options = [f'{i+1}. {row["year"]} {METRIC_LABELS.get(row["metric"], row["metric"])} / {row["file_name"] or "旧版来源"}' for i,row in enumerate(sources)]
                chosen = st.selectbox('打开指标对应的原始文件', options)
                render_source(dict(sources[options.index(chosen)]), 'source_detail')
        uploaded = [row for row in companies if row['source'] != 'builtin']
        if writable and uploaded:
            st.markdown('#### 企业资料管理')
            delete_name = st.selectbox('选择待管理企业', [row['name'] for row in uploaded], key='delete_company')
            confirm = st.checkbox('确认删除该企业的财务指标、报告文件及来源记录。此操作不可撤销。', key=f'confirm_delete_company_{name_to_id[delete_name]}')
            if st.button('删除所选企业', disabled=not confirm):
                try:
                    if delete_uploaded_company(name_to_id[delete_name]):
                        st.session_state.import_result = None
                        st.session_state.pop('pending_import', None)
                        st.rerun()
                except Exception as exc:
                    st.error(f'删除失败：{public_message(exc)}')
    # Render after this run's actions have cleared/replaced stale feedback.
    with import_feedback.container():
        render_import_result()


def render_evaluation_page():
    st.markdown('### 本地确定性回归 Benchmark')
    st.caption('结果只在点击后于当前环境真实执行并计算；未运行时不展示准确率。')
    st.info(
        '本评测覆盖多轮上下文、Planner 路由、Text-to-SQL 结果、SQL 安全和页级 RAG。'
        'RAG 使用运行时生成的多页测试 PDF 验证 page → chunk → retrieval → citation。\n\n'
        '不代表真实市场全部公司、所有 PDF 排版、OCR 扫描件、公网稳定性、大模型能力或生产级并发性能。'
    )
    if st.button('运行本地 Benchmark', type='primary'):
        with st.spinner('正在执行本地 Benchmark…'):
            st.session_state.benchmark_result = run_benchmark()
    result = st.session_state.get('benchmark_result')
    if not result:
        st.warning('尚未运行当前环境 Benchmark。')
        st.dataframe(pd.DataFrame([{
            '评测维度': label, '当前状态': '尚未运行', '依据': '点击“运行本地 Benchmark”'
        } for label in CATEGORY_LABELS.values()]), width='stretch', hide_index=True)
        return

    st.caption(result['disclaimer'])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Benchmark 版本', result['suite_version'])
    c2.metric('总样本数', result['total'])
    c3.metric('通过 / 失败', f'{result["passed"]} / {result["failed"]}')
    c4.metric('总体通过率', f'{result["pass_rate"] * 100:.2f}%')
    st.write(f'运行时间：{result["started_at"]}')
    st.write(f'总耗时：{result["duration_ms"]:.3f} ms')
    st.caption('这是当前环境下本地 Benchmark 的运行耗时，不代表生产服务 SLA。')
    category_rows = [{
        '评测维度': item['label'], '通过 / 总数': f'{item["passed"]} / {item["total"]}',
        '通过率': f'{item["pass_rate"] * 100:.2f}%', '样本量': f'n={item["total"]}',
    } for item in result['categories'].values()]
    st.dataframe(pd.DataFrame(category_rows), width='stretch', hide_index=True)
    detail_rows = [{
        'Case ID': case['id'], '类别': CATEGORY_LABELS.get(case['category'], case['category']),
        '问题/场景': case['scenario'], 'Expected': json.dumps(case['expected'], ensure_ascii=False),
        'Actual': json.dumps(case['actual'], ensure_ascii=False),
        '状态': '通过' if case['passed'] else '失败', '耗时(ms)': case['duration_ms'],
        '错误': case['error'] or '',
    } for case in result['cases']]
    st.markdown('#### Case 明细')
    st.dataframe(pd.DataFrame(detail_rows), width='stretch', hide_index=True)
    st.download_button(
        '下载 Benchmark JSON', json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'),
        file_name=f'benchmark_{result["suite_version"]}.json', mime='application/json',
    )


def main():
    st.set_page_config(page_title=PAGE_TITLE, page_icon=page_icon(), layout='wide', initial_sidebar_state='auto')
    init_state()
    configure_workspace('main')
    if public_session_enabled():
        if '_public_database_session' not in st.session_state:
            st.session_state['_public_database_session'] = uuid4().hex
        configure_public_session(st.session_state['_public_database_session'])
    init_db()
    seed_sample_data()
    apply_styles()
    companies, name_to_id, data_map, analysis_names = load_workspace()
    reconcile_company_selectors(analysis_names, companies)
    llm_config = render_sidebar(analysis_names)
    selected_main = st.session_state.selected_main
    selected_cmp = st.session_state.get('selected_cmp')
    active_selector = 'qa_main' if st.session_state.page == '财报问数' else 'enterprise_main' if st.session_state.page == '企业分析' else None
    if active_selector and st.session_state.get(active_selector) in analysis_names:
        selected_main = st.session_state.selected_main = st.session_state[active_selector]
    if selected_cmp not in analysis_names or selected_cmp == selected_main:
        selected_cmp = next((name for name in analysis_names if name != selected_main), None)
        st.session_state.selected_cmp = selected_cmp
    render_header(st.session_state.page, selected_main or '暂无可分析企业', data_map.get(selected_main, pd.DataFrame()))
    if st.session_state.page in {'财报问数', '企业分析'} and not analysis_names:
        st.info(NO_METRICS)
        st.button('前往数据中心', on_click=go_to_data_center, type='primary')
    elif st.session_state.page == '财报问数':
        render_qa_page(selected_main, selected_cmp, analysis_names, data_map, name_to_id, llm_config)
    elif st.session_state.page == '企业分析':
        render_enterprise_page(analysis_names, data_map, llm_config)
    elif st.session_state.page == '研究资料库':
        render_library_page(companies, name_to_id)
    elif st.session_state.page == '数据中心':
        render_data_center(companies, name_to_id, data_map)
    else:
        render_evaluation_page()



def render_query_trace(result):
    with st.expander('查询过程'):
        parsed = result.get('parsed') or {}
        task = result.get('analysis_intent') or parsed.get('intent')
        if task:
            st.write(f'已识别任务：{task}')
        plan = result.get('query_plan') or {}
        if plan.get('structured_intent') and plan['structured_intent'] != parsed.get('intent'):
            st.caption(f'原始解析：{parsed.get("intent") or "unknown"}｜结构化子任务：{plan["structured_intent"]}')
        if plan:
            st.write(f'查询规划：{str(plan.get("route", "未规划")).upper()}')
            st.caption(plan.get('reason', ''))
        trace = result.get('agent_trace') or result.get('trace') or []
        if trace:
            st.dataframe(trace_rows_to_df(trace), width='stretch', hide_index=True)
        sql = result.get('sql_result') or {}
        if sql.get('sql'):
            st.code(sql['sql'], language='sql')
            st.write(f'参数：{sql.get("params", [])}')
            st.write(f'安全校验：{"通过" if sql.get("safety_status") == "passed" else sql.get("safety_status", "未执行")}')
            execution = sql.get('execution_status') or sql.get('status') or 'not_executed'
            st.write('执行状态：' + {'success': '成功', 'failed': '失败', 'not_executed': '未执行', 'not_applicable': '不适用'}.get(execution, execution))
            st.write(f'返回行数：{sql.get("row_count", len(sql.get("rows") or []))}')
        if sql.get('attempts'):
            st.markdown('**执行尝试**')
            st.dataframe(trace_rows_to_df(sql['attempts']), width='stretch', hide_index=True)
        if sql.get('error') or sql.get('fallback_reason'):
            st.warning(public_message(sql.get('error') or sql.get('fallback_reason')))
        retrieval = result.get('retrieval_result') or {}
        if retrieval:
            st.write(f'文档检索状态：{retrieval.get("status", "未执行")}')
            st.write(f'符合企业和年份条件的文档：{retrieval.get("candidate_documents", 0)}')
            st.write(f'支持答案的引用数量：{len(retrieval.get("citations") or [])}')
            if retrieval.get('reason'):
                st.caption(retrieval['reason'])
            if retrieval.get('document_checks'):
                st.markdown('**原文范围核查**')
                st.dataframe(trace_rows_to_df(retrieval['document_checks']), width='stretch', hide_index=True)
        if result.get('reason'):
            st.caption(result['reason'])



def go_to_data_center():
    st.session_state.page = '数据中心'
    st.session_state.navigation = '数据中心'



def render_import_review(batch):
    st.markdown('#### 02 数据复核')
    st.caption(f'文件：{batch["file_name"]}｜本次导入：{batch["import_id"]}')
    for warning in batch.get('warnings') or []:
        st.warning(public_message(warning))
    for error in batch.get('errors') or []:
        st.error(public_message(error))
    from core.analysis import METRIC_UNITS
    preview = []
    for row in batch.get('records') or []:
        item = {'企业': row.get('company_name'), '年度': row.get('year')}
        for metric, label in METRIC_LABELS.items():
            item[f'{label}（{METRIC_UNITS[metric]}）'] = row.get(metric)
        item['审计意见'] = row.get('audit_opinion')
        preview.append(item)
    if preview:
        st.dataframe(pd.DataFrame(preview), width='stretch', hide_index=True)
    else:
        st.info('没有可确认的结构化指标；请检查文件是否为可提取文本的年度财报，或上传核对后的表格。')
    with st.expander('查看原值与单位换算依据'):
        details = []
        for row in batch.get('records') or []:
            for metric, source in (row.get('_provenance') or {}).items():
                details.append({'企业': row.get('company_name'), '年度': row.get('year'), '指标': METRIC_LABELS.get(metric, metric),
                                '原值': str(source.get('raw_value')), '原单位': source.get('raw_unit'),
                                '入库值': str(row.get(metric)), '入库单位': source.get('normalized_unit'),
                                '页码': source.get('page'), '单元格': source.get('cell')})
        if details:
            st.dataframe(pd.DataFrame(details), width='stretch', hide_index=True)
    verified = st.checkbox('我已核对企业、年度、单位和预览数值', key=f'import_confirm_{batch["import_id"]}')
    overwrite = st.checkbox('覆盖已有的同企业同年度指标（保留原来源历史）', key=f'import_overwrite_{batch["import_id"]}')
    st.caption('未选择覆盖时，只补充缺失指标；不替换已有数值。异常必须先修正，不能通过勾选确认绕过。')
    commit_clicked = st.button('确认入库', type='primary',
                               disabled=not verified or not batch.get('can_commit') or not can_write(),
                               key=f'import_commit_{batch["import_id"]}')
    document_clicked = False
    if batch.get('can_save_document') and not batch.get('can_commit'):
        st.caption('仅保存原文不会将企业加入结构化分析名单；后续可补充核验过的年度指标。')
        document_clicked = st.button('仅保存文档（不加入分析名单）',
                                      disabled=not verified or not can_write(), key=f'import_document_{batch["import_id"]}')
    if commit_clicked or document_clicked:
        st.session_state.import_result = None
        try:
            with st.spinner('正在提交并核验入库结果……'):
                result = commit_import(batch, confirmed=True, overwrite_existing=overwrite,
                                       document_only=document_clicked)
            st.session_state.import_result = result
            if result.get('status') in {'success', 'document_only', 'unchanged'} or result.get('committed'):
                refresh_imported_data(result)
        except Exception as exc:
            st.session_state.import_result = {'status': 'failed', 'error_message': str(exc)}



if __name__ == '__main__':
    main()
