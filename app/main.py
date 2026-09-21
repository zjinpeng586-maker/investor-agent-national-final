from __future__ import annotations

import sys
import re
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

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
)
from core.evaluation import CATEGORY_LABELS, run_benchmark
from core.llm import PROVIDER_PRESETS, build_llm_config, enhance_report_with_llm, llm_enabled
from core.online_disclosure import guess_exchange, resolve_stock_code, search_disclosures_with_details
from core.qa_engine import answer_question
from core.retrieval import get_index_status
from core.report_pdf import report_text_to_pdf_bytes
from core.seed import seed_sample_data
from core.service import ingest_online_pdf_bytes, ingest_online_pdf_url, ingest_pdf_file, ingest_tabular_file


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


def normalize_score_detail(score_detail: dict[str, float]) -> dict[str, float]:
    max_map = {'盈利能力': 30, '现金流质量': 25, '偿债稳健性': 25, '成长能力': 20}
    return {
        dim: round(max(0, min((safe_num(score_detail.get(dim)) or 0) / maximum * 100, 100)), 1)
        for dim, maximum in max_map.items()
    }


def make_radar_chart(score_a: dict, name_a: str, score_b: dict | None = None, name_b: str | None = None):
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
    defaults = {
        'page': '财报问数',
        'navigation': '财报问数',
        'chat_messages': [],
        'recent_sessions': [],
        'session_title': '新会话',
        'engine_mode': '本地分析模式',
        'investor_profile': '平衡型',
        'online_candidates': [],
        'online_diag': None,
        'conversation_context': new_conversation_context(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def apply_styles() -> None:
    st.markdown(
        '''
        <style>
        [data-testid="stSidebar"] {background: #102a43;}
        [data-testid="stSidebar"] * {color: #f4f7fb;}
        [data-testid="stSidebar"] .stRadio label {padding: .25rem 0;}
        .v2-hero {padding: 2.1rem 1.8rem; border: 1px solid #dce5ef; border-radius: 18px;
                  background: linear-gradient(135deg, #f7faff 0%, #edf4fb 100%); margin-bottom: 1rem;}
        .v2-hero h1 {color: #102a43; font-size: 2rem; margin: 0 0 .4rem 0;}
        .v2-hero p {color: #52667a; margin: 0;}
        .context-bar {padding: .65rem .9rem; background: #f6f8fb; border-radius: 10px; color: #486581;}
        .status-note {padding: .8rem 1rem; border-left: 4px solid #2b6cb0; background: #f3f7fb;}
        </style>
        ''',
        unsafe_allow_html=True,
    )


def load_workspace():
    companies = fetch_companies()
    name_to_id = {row['name']: row['id'] for row in companies}
    data_map = {name: metric_rows_to_df(fetch_company_metrics(cid)) for name, cid in name_to_id.items()}
    analysis_names = [name for name, df in data_map.items() if not df.empty and 'year' in df.columns]
    return companies, name_to_id, data_map, analysis_names


def render_sidebar(analysis_names: list[str]):
    with st.sidebar:
        st.markdown('## 财报智问')
        st.caption('上市公司财报智能问数系统 · V2.0')
        if st.button('＋ 新建会话', width='stretch', type='primary'):
            current = st.session_state.get('session_title', '新会话')
            if current != '新会话' and current not in st.session_state.recent_sessions:
                st.session_state.recent_sessions.insert(0, current)
                st.session_state.recent_sessions = st.session_state.recent_sessions[:5]
            st.session_state.chat_messages = []
            st.session_state.session_title = '新会话'
            st.session_state.conversation_context = new_conversation_context()
            st.session_state.page = '财报问数'
            st.session_state.navigation = '财报问数'
            st.rerun()

        selected_page = st.radio('主导航', PAGES, key='navigation', label_visibility='collapsed')
        st.session_state.page = selected_page

        st.divider()
        st.markdown('**最近会话**')
        recent = st.session_state.recent_sessions
        if recent:
            for idx, title in enumerate(recent[:5]):
                st.caption(f'{idx + 1}. {title}')
        else:
            st.caption('暂无历史会话')

        st.divider()
        with st.expander('设置'):
            engine_mode = st.selectbox(
                '分析模式', ['本地分析模式', '云端增强模式'],
                index=0 if st.session_state.engine_mode == '本地分析模式' else 1,
            )
            st.session_state.engine_mode = engine_mode
            llm_config = None
            if engine_mode == '云端增强模式':
                provider = st.selectbox('模型服务商', list(PROVIDER_PRESETS))
                preset = PROVIDER_PRESETS[provider]
                model = st.selectbox('模型 / Endpoint', preset['models'])
                if '自定义' in model:
                    model = st.text_input('自定义模型名称或 Endpoint ID')
                base_url = st.text_input('API Base URL', value=preset['base_url'])
                api_key = st.text_input('API Key', type='password')
                llm_config = build_llm_config(provider, api_key, model, base_url)
            st.session_state.investor_profile = st.selectbox(
                '分析偏好', ['稳健型', '平衡型', '成长型'],
                index=['稳健型', '平衡型', '成长型'].index(st.session_state.investor_profile),
            )
        st.caption('● 本地数据服务正常')
        st.caption('游客 · 演示空间')

    default_main = DEFAULT_COMPANY if DEFAULT_COMPANY in analysis_names else analysis_names[0]
    default_cmp = DEFAULT_COMPARE if DEFAULT_COMPARE in analysis_names else next(
        (name for name in analysis_names if name != default_main), default_main
    )
    if st.session_state.get('selected_main') not in analysis_names:
        st.session_state.selected_main = default_main
    if st.session_state.get('selected_cmp') not in analysis_names or st.session_state.selected_cmp == st.session_state.selected_main:
        st.session_state.selected_cmp = default_cmp
    return llm_config


def render_header(page: str, selected_main: str, main_df: pd.DataFrame) -> None:
    years = '-'
    if not main_df.empty:
        years = f"{int(main_df['year'].min())}–{int(main_df['year'].max())}"
    left, right = st.columns([3, 2])
    left.markdown(f'## {page}')
    right.markdown(
        f'<div class="context-bar">当前企业：{selected_main}<br>可用报告期：{years}</div>',
        unsafe_allow_html=True,
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
    st.session_state.chat_messages.append({
        'question': question,
        'effective_question': question,
        'applied_year': (resolved.get('years') or [None])[0],
        'selected_compare': selected_cmp,
        'result': result,
    })
    if st.session_state.session_title == '新会话':
        st.session_state.session_title = question[:22]


def render_answer_card(item: dict, data_map: dict[str, pd.DataFrame], name_to_id: dict[str, int], index: int) -> None:
    question, result = item['question'], item['result']
    company = result.get('company')
    parsed = result.get('parsed') or {}
    analysis_intent = result.get('analysis_intent') or parsed.get('intent')
    sql_result = result.get('sql_result') or {}
    sql_rows = pd.DataFrame(sql_result.get('rows') or [])
    if sql_result.get('status') == 'success' and not sql_rows.empty:
        df = sql_rows[sql_rows['company'] == company].drop(columns=['company'], errors='ignore')
    else:
        df = filter_result_data(data_map.get(company, pd.DataFrame()), parsed)
    metrics = parsed.get('metrics') or ['revenue', 'net_profit', 'operating_cashflow']
    compare_company = None
    if analysis_intent == 'company_compare':
        compare_company = next(
            (name for name in parsed.get('companies') or [] if name != company and name in data_map),
            item.get('selected_compare'),
        )
        if compare_company == company or compare_company not in data_map:
            compare_company = None
    with st.chat_message('user'):
        st.write(question)
    with st.chat_message('assistant'):
        st.markdown('#### 结论摘要')
        st.write(result['answer'])
        tab_chart, tab_data, tab_evidence = st.tabs(['图表', '数据', '依据'])
        with tab_chart:
            if compare_company:
                if sql_result.get('status') == 'success' and not sql_rows.empty:
                    compare_df = sql_rows.rename(columns={'company': '企业', 'year': '年度', **METRIC_LABELS})
                    compare_df = compare_df.drop(columns=['raw_source'], errors='ignore')
                else:
                    compare_df = comparison_result_data(company, compare_company, data_map, parsed)
                value_columns = [column for column in compare_df.columns if column not in ['企业', '年度']]
                long_df = compare_df.melt(
                    id_vars=['企业', '年度'], value_vars=value_columns, var_name='指标', value_name='数值'
                ).dropna(subset=['数值'])
                if not long_df.empty:
                    fig = px.bar(long_df, x='指标', y='数值', color='企业', barmode='group',
                                 title=f'{company} vs {compare_company} 核心指标对比')
                    st.plotly_chart(fig, width='stretch', key=f'qa_compare_chart_{index}')
                else:
                    st.info('两家企业当前没有可用于绘图的共同指标。')
            else:
                fig = make_line_chart(df, metrics, f'{company} 财务指标趋势')
                if fig is not None:
                    st.plotly_chart(fig, width='stretch', key=f'qa_chart_{index}')
                else:
                    st.info('当前问题对应的数据不足以生成趋势图。')
        with tab_data:
            if compare_company:
                if sql_result.get('status') == 'success' and not sql_rows.empty:
                    compare_df = sql_rows.rename(columns={'company': '企业', 'year': '年度', **METRIC_LABELS})
                    compare_df = compare_df.drop(columns=['raw_source'], errors='ignore')
                else:
                    compare_df = comparison_result_data(company, compare_company, data_map, parsed)
                if compare_df.empty:
                    st.info('两家企业当前没有可展示的结构化指标。')
                else:
                    st.dataframe(compare_df, width='stretch', hide_index=True)
            elif df.empty:
                st.info('当前企业暂无结构化年度指标。')
            else:
                cols = [col for col in ['year'] + metrics if col in df.columns]
                st.dataframe(df[cols], width='stretch', hide_index=True)
        with tab_evidence:
            st.text(result.get('evidence') or '暂无来源依据。')
            retrieval = result.get('retrieval_result') or {}
            for citation in retrieval.get('citations') or []:
                st.markdown(f'**来源 {citation["number"]}**')
                st.write(citation.get('file_name') or '未命名文档')
                st.caption(f'报告年份：{citation.get("report_year") or "未标注"}｜PDF第{citation["page"]}页')
                st.write(f'“{citation.get("snippet", "")}”')
            reports = fetch_report_files(name_to_id.get(company)) if company in name_to_id else []
            if reports:
                st.caption(f'关联来源文件：{reports[0]["file_name"]}')
        years = '、'.join(map(str, parsed.get('years') or [])) or '当前可用期间'
        metrics = '、'.join(METRIC_LABELS.get(x, x) for x in (parsed.get('metrics') or [])) or '综合财务指标'
        companies = f'{company}、{compare_company}' if compare_company else company
        st.caption(f'当前理解：{companies}｜{years}｜{metrics}｜{result.get("model_used", "可信数据分析")}')
        with st.expander('查询过程'):
            query_plan = result.get('query_plan') or {'route': 'sql', 'reason': '既有可信查询链路'}
            retrieval = result.get('retrieval_result') or {}
            st.write(f'查询规划：{query_plan.get("route", "sql").upper()}')
            st.caption(query_plan.get('reason', ''))
            st.write(f'1. 已识别任务：{analysis_intent}')
            if analysis_intent != parsed.get('intent'):
                st.caption(f'原始解析：{parsed.get("intent", "unknown")}｜结构化子任务：{analysis_intent}')
            st.write(f'2. 已识别企业与期间：{company} / {years}')
            if sql_result.get('sql_status') in {'success', 'fallback'}:
                source_label = {
                    'local': '本地规则', 'local_repair': '本地规则自动修复', 'llm': '云端模型'
                }.get(sql_result.get('source'), sql_result.get('source', '未知'))
                st.write(f'3. SQL 来源：{source_label}')
                st.code(sql_result.get('sql', ''), language='sql')
                st.write(f'参数：{sql_result.get("params", [])}')
                st.write(f'安全校验：{"通过" if sql_result.get("safety_status") == "passed" else "未通过"}')
                st.write(f'执行状态：{"成功" if sql_result.get("execution_status") == "success" else "回退"}')
                st.write(f'返回行数：{sql_result.get("row_count", 0)}')
                st.write(f'自动纠错：{"是" if sql_result.get("corrected") else "否"}')
                st.markdown('**执行尝试**')
                st.dataframe(pd.DataFrame(sql_result.get('attempts') or []), width='stretch', hide_index=True)
            else:
                st.write('3. 当前任务沿用 V1.0 可信分析链路，不适用结构化 SQL 查询。')
            st.write('4. 已核对查询结果、规则依据与来源记录')
            if result.get('agent_trace'):
                st.dataframe(pd.DataFrame(result['agent_trace']), width='stretch', hide_index=True)
            if query_plan.get('route') in {'rag', 'hybrid'}:
                st.write(f'RAG 候选文档数：{retrieval.get("candidate_documents", 0)}')
                st.write(f'索引 chunk 数：{retrieval.get("chunk_count", 0)}')
                st.write(f'检索命中数：{retrieval.get("hit_count", 0)}')
                pages = sorted({item['page'] for item in retrieval.get('citations', [])})
                st.write('实际引用页码：' + ('、'.join(f'PDF第{page}页' for page in pages) if pages else '无'))
            if sql_result.get('sql_status') == 'fallback':
                st.warning('Text-to-SQL 未完成有效查询，本次已明确回退到 V1.0 可信数据路径。')


def render_qa_page(selected_main, selected_cmp, all_names, data_map, name_to_id, llm_config):
    st.markdown(
        '''<div class="v2-hero"><h1>今天想分析哪家公司？</h1>
        <p>直接用自然语言询问财务指标、趋势、风险或企业对比，回答以已接入的结构化数据和来源记录为依据。</p></div>''',
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.session_state.get('qa_main') not in all_names:
            st.session_state.qa_main = selected_main
        selected_main = st.selectbox('当前理解企业', all_names, key='qa_main')
        st.session_state.selected_main = selected_main
    with c2:
        years = data_map[selected_main]['year'].astype(int).tolist()
        period_options = ['自动识别'] + [str(y) for y in reversed(years)]
        if st.session_state.get('qa_period') not in period_options:
            st.session_state.qa_period = '自动识别'
        selected_period = st.selectbox('当前理解期间', period_options, key='qa_period')

    context = st.session_state.conversation_context
    context_company = context.get('primary_company') or selected_main
    context_years = '、'.join(map(str, context.get('years') or [])) or '自动识别'
    context_metrics = '、'.join(METRIC_LABELS.get(key, key) for key in context.get('metrics') or []) or '待问题识别'
    st.caption(f'当前上下文：{context_company}｜{context_years}｜{context_metrics}')

    if context.get('awaiting_clarification') and context.get('clarification'):
        clarification = context['clarification']
        st.info(clarification['question'])
        option_map = {option['label']: option['value'] for option in clarification['options']}
        selected_option = st.selectbox('请选择', list(option_map), key='clarification_choice')
        if st.button('确认并继续', type='primary', key='confirm_clarification'):
            turn = confirm_clarification(
                context, option_map[selected_option], all_names, st.session_state.investor_profile
            )
            st.session_state.conversation_context = turn['context']
            execute_resolved_question(
                turn['question'], turn, selected_main, selected_cmp, all_names, data_map, llm_config
            )
            st.rerun()

    recommended = [
        f'{selected_main}近三年营业收入变化如何？',
        f'{selected_main}净利润变化如何？',
        f'{selected_main}和{selected_cmp}核心指标对比',
        f'{selected_main}有哪些财务风险？',
    ]
    cards = st.columns(4)
    pending = None
    for idx, prompt in enumerate(recommended):
        if cards[idx].button(prompt, width='stretch', key=f'recommended_{idx}'):
            pending = prompt

    with st.form('question_form', clear_on_submit=True):
        question = st.text_area(
            '自然语言问题',
            placeholder='例如：比亚迪近三年营业收入变化如何？',
            height=105,
            label_visibility='collapsed',
        )
        submitted = st.form_submit_button('开始分析', type='primary', width='stretch')
    if submitted and question.strip():
        pending = question.strip()
    if pending:
        run_question(pending, selected_main, selected_cmp, selected_period, all_names, data_map, llm_config)
        st.rerun()

    if not st.session_state.chat_messages:
        st.info('选择一个推荐问题，或输入你关心的财务问题开始分析。')
    for idx, item in enumerate(st.session_state.chat_messages[-8:]):
        render_answer_card(item, data_map, name_to_id, idx)


def report_downloads(company: str, df: pd.DataFrame, alerts: list[dict], compare_payload, key_prefix: str, llm_config):
    report_text = generate_report(company, df, alerts, compare_payload, st.session_state.investor_profile)
    if llm_enabled(llm_config) and st.button('使用云端模型增强报告文字', key=f'{key_prefix}_enhance'):
        try:
            report_text = enhance_report_with_llm(llm_config, report_text, company, st.session_state.investor_profile)
            st.success('报告文字已完成云端增强。')
        except Exception:
            st.info('云端服务暂未返回有效结果，继续使用可信本地报告。')
    score = score_company(df)
    txt_col, pdf_col = st.columns(2)
    txt_col.download_button(
        '下载 TXT 报告', report_text.encode('utf-8'), f'{company}_财务分析报告.txt', 'text/plain',
        width='stretch', key=f'{key_prefix}_txt',
    )
    try:
        pdf_bytes = report_text_to_pdf_bytes(report_text, company, df=df, score=score)
        pdf_col.download_button(
            '下载图文 PDF 报告', pdf_bytes, f'{company}_财务分析报告.pdf', 'application/pdf',
            width='stretch', key=f'{key_prefix}_pdf',
        )
    except Exception as exc:
        pdf_col.warning(f'PDF 报告生成暂未完成：{exc}')


def render_enterprise_page(analysis_names, data_map, llm_config):
    pick1, pick2 = st.columns(2)
    if st.session_state.get('enterprise_main') not in analysis_names:
        st.session_state.enterprise_main = st.session_state.selected_main
    selected_main = pick1.selectbox('分析企业', analysis_names, key='enterprise_main')
    compare_options = [name for name in analysis_names if name != selected_main]
    if compare_options and st.session_state.get('enterprise_cmp') not in compare_options:
        st.session_state.enterprise_cmp = next(
            (name for name in compare_options if name == st.session_state.selected_cmp), compare_options[0]
        )
    selected_cmp = pick2.selectbox('对比企业', compare_options, key='enterprise_cmp') if compare_options else selected_main
    st.session_state.selected_main, st.session_state.selected_cmp = selected_main, selected_cmp
    df, cmp_df = data_map[selected_main], data_map[selected_cmp]
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
        ('ROE(%)', display_value(latest.get('roe'))), ('综合评分', str(score['score'])),
    ]
    for col, (label, value) in zip(metrics, values):
        col.metric(label, value)
    st.info(build_summary(selected_main, df, alerts, st.session_state.investor_profile))

    tab_overview, tab_compare, tab_report = st.tabs(['经营概览与风险', '企业对比', '分析报告'])
    with tab_overview:
        left, right = st.columns([1.35, 1])
        fig = make_line_chart(df, ['revenue', 'net_profit', 'operating_cashflow'], f'{selected_main} 核心指标趋势')
        if fig is not None:
            left.plotly_chart(fig, width='stretch', key='enterprise_trend')
        right.plotly_chart(make_radar_chart(score, selected_main), width='stretch', key='enterprise_radar')
        st.dataframe(df[[c for c in ['year'] + list(METRIC_LABELS) if c in df.columns]], width='stretch', hide_index=True)
        st.markdown('#### 自定义图表分析')
        chart_col, metric_col = st.columns([1, 2])
        chart_type = chart_col.selectbox('图表类型', ['折线图', '柱形图', '饼图'], key='enterprise_chart_type')
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
            cols[0].metric(f'{selected_main} 综合评分', compare_payload['score_a']['score'])
            cols[1].metric(f'{selected_cmp} 综合评分', compare_payload['score_b']['score'])
            cols[2].metric('研究辅助倾向', compare_payload['recommended'])
            st.write(compare_payload['reasoning'])
            st.plotly_chart(
                make_radar_chart(compare_payload['score_a'], selected_main, compare_payload['score_b'], selected_cmp),
                width='stretch', key='enterprise_compare_radar',
            )
            st.dataframe(compare_payload['score_table'], width='stretch', hide_index=True)
            st.dataframe(compare_payload['table'], width='stretch', hide_index=True)
    with tab_report:
        st.markdown('#### 生成当前企业分析报告')
        st.caption('报告包含年度指标、趋势、四维评分、风险关注点和来源说明。')
        report_downloads(selected_main, df, alerts, compare_payload, 'enterprise_report', llm_config)
    if st.button('带着当前企业继续问数', width='stretch'):
        st.session_state.page = '财报问数'
        st.session_state.navigation = '财报问数'
        st.rerun()


def render_library_page(companies, name_to_id):
    reports = fetch_report_files()
    st.markdown('### 已接入研究资料')
    st.caption('当前展示真实接入文档及页码级 RAG 索引状态；索引可在首次问答检索时自动建立，并按文件变化自动刷新。')
    if not reports:
        st.info('当前资料库暂无文件，可前往“数据中心”接入资料。')
        return
    rows = []
    for report in reports:
        rows.append({
            '文档名称': report['file_name'],
            '类型': report['file_type'] or '未分类',
            '企业': report['company_name'] or '未关联',
            '报告年份': report['report_year'] or '-',
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
        mask = filtered.astype(str).apply(lambda col: col.str.contains(keyword, case=False, na=False)).any(axis=1)
        filtered = filtered[mask]
    if selected_type != '全部':
        filtered = filtered[filtered['类型'] == selected_type]
    st.dataframe(filtered, width='stretch', hide_index=True)
    with st.expander('查看资料来源详情'):
        choices = [f'{r["file_name"]}｜{r["company_name"] or "未关联"}' for r in reports]
        picked = st.selectbox('选择资料', choices)
        report = reports[choices.index(picked)]
        st.write(f'文件路径 / 来源：{report["file_path"]}')
        st.write(f'解析状态：{report["parse_status"]}')
        st.write(f'来源说明：{report["note"] or "暂无补充说明"}')
        st.info(f'RAG 索引状态：{get_index_status(dict(report))}。引用仅在真实页级文本检索命中后展示。')


def show_import_messages(title: str, warnings: list[str] | None = None):
    st.success(title)
    if warnings:
        with st.expander('查看导入详情'):
            for warning in warnings:
                st.write('· ' + str(warning))


def render_data_center(companies, name_to_id, data_map):
    st.markdown('### 01 接入 → 02 解析 → 03 复核 → 04 入库 → 05 来源追踪')
    tab_upload, tab_disclosure, tab_sources = st.tabs(['文件接入', '公开披露', '来源追踪'])
    with tab_upload:
        c1, c2, c3 = st.columns(3)
        manual_name = c1.text_input('企业名称（可选）')
        stock_code = c2.text_input('股票代码（可选）')
        year_text = c3.text_input('报告年份（可选）')
        tabular = st.file_uploader('上传结构化财务表（CSV / Excel）', type=['csv', 'xlsx'])
        pdfs = st.file_uploader('上传企业年报 PDF（可多选）', type=['pdf'], accept_multiple_files=True)
        if st.button('解析并入库', type='primary', width='stretch'):
            imported = False
            year_hint = int(year_text) if year_text.isdigit() else None
            if tabular is not None:
                try:
                    name, warnings = ingest_tabular_file(tabular.name, tabular.getvalue(), manual_name or None)
                    show_import_messages(f'结构化财务表导入完成：{name}', warnings)
                    imported = True
                except Exception as exc:
                    st.error(f'结构化财务表导入失败：{exc}')
            for pdf in pdfs or []:
                try:
                    if stock_code or year_hint:
                        name, warnings, _ = ingest_online_pdf_bytes(
                            pdf.name, pdf.getvalue(), manual_company_name=manual_name or None,
                            stock_code=stock_code or None, source_label='uploaded_disclosure_pdf',
                            year_hint=year_hint, report_title=pdf.name,
                        )
                    else:
                        name, warnings = ingest_pdf_file(pdf.name, pdf.getvalue(), manual_name or None)
                    show_import_messages(f'PDF 导入完成：{name}', warnings)
                    imported = True
                except Exception as exc:
                    st.error(f'{pdf.name} 导入失败：{exc}')
            if not tabular and not pdfs:
                st.warning('请至少选择一个 CSV、Excel 或 PDF 文件。')
            if imported:
                st.info('数据已写入本地来源库，刷新后可在企业分析与来源追踪中查看。')

        st.markdown('#### 公开 PDF 链接导入')
        url = st.text_input('公开 PDF 链接', placeholder='https://.../report.pdf')
        if st.button('导入 PDF 链接', width='stretch'):
            if not url.strip():
                st.warning('请填写有效的公开 PDF 链接。')
            else:
                try:
                    name, warnings, _ = ingest_online_pdf_url(
                        url.strip(), manual_company_name=manual_name or None, stock_code=stock_code or None,
                        year_hint=int(year_text) if year_text.isdigit() else None,
                    )
                    show_import_messages(f'在线 PDF 导入完成：{name}', warnings)
                except Exception as exc:
                    st.warning(f'在线 PDF 导入暂未完成：{exc}')

    with tab_disclosure:
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        query = c1.text_input('公司名称 / 股票代码', placeholder='比亚迪 / 002594')
        source = c2.selectbox('披露来源', ['自动判断', '上交所', '深交所'])
        report_type = c3.selectbox('报告类型', ['年度报告', '半年度报告', '季度报告', '全部公告'])
        year_option = c4.selectbox('年份', ['不限', '2025', '2024', '2023', '2022', '2021'], index=2)
        if query:
            exchange = guess_exchange(query)
            st.caption(f'识别股票代码：{resolve_stock_code(query) or "未识别"}；交易所倾向：{exchange or "待判断"}')
        if st.button('搜索公开披露文件', type='primary', width='stretch'):
            if not query.strip():
                st.warning('请输入公司名称或股票代码。')
            else:
                with st.spinner('正在检索公开披露文件，外部站点响应可能需要数秒...'):
                    items, diag = search_disclosures_with_details(
                        query, source, report_type, None if year_option == '不限' else int(year_option), limit=20
                    )
                st.session_state.online_candidates = [item.as_dict() for item in items]
                st.session_state.online_diag = diag
                if items:
                    st.success(f'检索到 {len(items)} 条候选披露文件。')
                else:
                    st.warning('暂未检索到候选公告，可查看运行详情。')
        if st.session_state.online_diag:
            with st.expander('运行详情'):
                for step in st.session_state.online_diag.get('steps', []):
                    st.write('· ' + str(step))
        if st.session_state.online_candidates:
            candidate_df = pd.DataFrame(st.session_state.online_candidates)
            st.dataframe(candidate_df, width='stretch', hide_index=True)
            labels = [f'{i + 1}. {row.get("公告标题", "")}' for i, row in enumerate(st.session_state.online_candidates)]
            selected = st.selectbox('选择要导入的公告', labels)
            row = st.session_state.online_candidates[labels.index(selected)]
            manual_pdf = st.file_uploader('如交易所拦截服务器下载，可在浏览器下载后上传原始 PDF', type=['pdf'], key='disclosure_pdf')
            b1, b2 = st.columns(2)
            if b1.button('直接导入所选公告', width='stretch'):
                try:
                    name, warnings, _ = ingest_online_pdf_url(
                        row['PDF链接'], manual_company_name=row.get('公司名称') or query,
                        stock_code=row.get('股票代码') or resolve_stock_code(query),
                        source_label='online_disclosure', year_hint=None if year_option == '不限' else int(year_option),
                        report_title=row.get('公告标题'),
                    )
                    show_import_messages(f'公开披露文件导入完成：{name}', warnings)
                except Exception as exc:
                    st.warning(f'服务器直接导入未完成：{exc}')
                    st.info('可通过上方浏览器上传备用路径继续导入。')
            if b2.button('导入浏览器上传 PDF', width='stretch'):
                if manual_pdf is None:
                    st.warning('请先上传原始公告 PDF。')
                else:
                    try:
                        name, warnings, _ = ingest_online_pdf_bytes(
                            manual_pdf.name, manual_pdf.getvalue(), manual_company_name=row.get('公司名称') or query,
                            stock_code=row.get('股票代码') or resolve_stock_code(query), source_label='browser_disclosure_pdf',
                            year_hint=None if year_option == '不限' else int(year_option),
                            report_title=row.get('公告标题'), source_url=row.get('PDF链接'),
                        )
                        show_import_messages(f'公开披露 PDF 导入完成：{name}', warnings)
                    except Exception as exc:
                        st.error(f'公开披露 PDF 导入失败：{exc}')

    with tab_sources:
        rows = []
        for company in companies:
            df = data_map.get(company['name'], pd.DataFrame())
            rows.append({
                '企业名称': company['name'], '股票代码': company['stock_code'], '来源': company['source'],
                '数据年度': '-' if df.empty else f"{int(df['year'].min())}–{int(df['year'].max())}", '指标记录数': len(df),
            })
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        reports = fetch_report_files()
        if reports:
            st.markdown('#### 来源文件')
            st.dataframe(pd.DataFrame([{
                '企业': r['company_name'], '文件': r['file_name'], '类型': r['file_type'],
                '年份': r['report_year'], '解析状态': r['parse_status'], '接入时间': r['uploaded_at'],
            } for r in reports]), width='stretch', hide_index=True)
        uploaded = [row for row in companies if row['source'] != 'builtin']
        if uploaded:
            delete_name = st.selectbox('上传企业管理', [row['name'] for row in uploaded])
            if st.button('删除所选上传企业'):
                if delete_uploaded_company(name_to_id[delete_name]):
                    st.success(f'已删除：{delete_name}')
                    st.rerun()


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


def main() -> None:
    st.set_page_config(page_title='财报智问 V2.0', page_icon='📊', layout='wide', initial_sidebar_state='expanded')
    init_db()
    seed_sample_data()
    init_state()
    apply_styles()
    companies, name_to_id, data_map, analysis_names = load_workspace()
    if not analysis_names:
        st.error('系统暂无可分析的结构化企业指标，请先导入 CSV 或 Excel。')
        st.stop()
    llm_config = render_sidebar(analysis_names)
    active_selector = 'qa_main' if st.session_state.page == '财报问数' else (
        'enterprise_main' if st.session_state.page == '企业分析' else None
    )
    if active_selector and st.session_state.get(active_selector) in analysis_names:
        st.session_state.selected_main = st.session_state[active_selector]
    selected_main = st.session_state.selected_main
    selected_cmp = st.session_state.get('selected_cmp')
    if selected_cmp not in analysis_names or (selected_cmp == selected_main and len(analysis_names) > 1):
        selected_cmp = next((name for name in analysis_names if name != selected_main), selected_main)
        st.session_state.selected_cmp = selected_cmp
    main_df = data_map[selected_main]
    render_header(st.session_state.page, selected_main, main_df)

    if st.session_state.page == '财报问数':
        render_qa_page(selected_main, selected_cmp, analysis_names, data_map, name_to_id, llm_config)
    elif st.session_state.page == '企业分析':
        render_enterprise_page(analysis_names, data_map, llm_config)
    elif st.session_state.page == '研究资料库':
        render_library_page(companies, name_to_id)
    elif st.session_state.page == '数据中心':
        render_data_center(companies, name_to_id, data_map)
    else:
        render_evaluation_page()


if __name__ == '__main__':
    main()
