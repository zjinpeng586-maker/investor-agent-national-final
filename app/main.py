from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.analysis import (
    build_summary,
    compare_companies,
    compute_alerts,
    generate_report,
    risk_dashboard,
    rows_to_df,
    score_company,
    safe_num,
    METRIC_LABELS,
)
from core.db import delete_uploaded_company, fetch_companies, fetch_company_metrics, fetch_report_files, init_db
from core.agents import catalog_as_rows
from core.llm import PROVIDER_PRESETS, build_llm_config, llm_enabled, enhance_report_with_llm
from core.qa_engine import answer_question
from core.seed import seed_sample_data
from core.service import ingest_pdf_file, ingest_tabular_file, ingest_online_pdf_url, ingest_online_pdf_bytes
from core.report_pdf import report_text_to_pdf_bytes
from core.charts import make_metric_chart
from core.online_disclosure import search_disclosures, search_disclosures_with_details, resolve_stock_code, guess_exchange



def _show_import_messages(title: str, warnings: list[str] | None = None):
    """Competition-facing import feedback: emphasize workflow stability while keeping details available."""
    st.success(title)
    if warnings:
        with st.expander('查看导入详情', expanded=False):
            for w in warnings:
                st.write('· ' + str(w))
            st.caption('说明：PDF 年报用于公开来源留痕和资料解析；结构化指标库用于量化评分与图表建模。')



def display_value(value, digits: int = 2) -> str:
    val = safe_num(value)
    return '-' if val is None else f'{val:.{digits}f}'



def normalize_score_detail(score_detail: dict[str, float]) -> dict[str, float]:
    """Normalize different score dimensions to 0-100 for radar display."""
    max_map = {'盈利能力': 30, '现金流质量': 25, '偿债稳健性': 25, '成长能力': 20}
    out: dict[str, float] = {}
    for dim, max_val in max_map.items():
        val = safe_num(score_detail.get(dim)) or 0
        out[dim] = round(max(0, min(val / max_val * 100, 100)), 1)
    return out


def make_radar_chart(score_a: dict, name_a: str, score_b: dict | None = None, name_b: str | None = None, title: str = '四维能力雷达图'):
    dims = ['盈利能力', '现金流质量', '偿债稳健性', '成长能力']
    norm_a = normalize_score_detail(score_a.get('detail', {}))
    values_a = [norm_a.get(d, 0) for d in dims]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=values_a + values_a[:1], theta=dims + dims[:1], fill='toself', name=name_a))
    if score_b and name_b:
        norm_b = normalize_score_detail(score_b.get('detail', {}))
        values_b = [norm_b.get(d, 0) for d in dims]
        fig.add_trace(go.Scatterpolar(r=values_b + values_b[:1], theta=dims + dims[:1], fill='toself', name=name_b))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=True,
        title=title,
        height=430,
        margin=dict(l=40, r=40, t=70, b=40),
    )
    return fig

def make_line_chart(df: pd.DataFrame, metrics: list[str], title: str):
    """Build a Plotly line chart using long-form numeric data.

    Plotly Express can fail when wide-form y columns contain mixed object and
    numeric dtypes. This helper coerces each selected metric to numeric and
    drops empty metrics, so switching among uploaded companies will not crash
    the page.
    """
    if df.empty or 'year' not in df.columns:
        return None
    available = [c for c in metrics if c in df.columns]
    if not available:
        return None
    work = df[['year'] + available].copy()
    work['year'] = pd.to_numeric(work['year'], errors='coerce')
    for c in available:
        work[c] = pd.to_numeric(work[c], errors='coerce')
    work = work.dropna(subset=['year'])
    long_df = work.melt(id_vars='year', value_vars=available, var_name='指标', value_name='数值')
    long_df = long_df.dropna(subset=['数值'])
    if long_df.empty:
        return None
    long_df['指标'] = long_df['指标'].map(lambda x: METRIC_LABELS.get(x, x))
    return px.line(long_df, x='year', y='数值', color='指标', markers=True, title=title)



def metric_rows_to_df(rows) -> pd.DataFrame:
    """Convert DB metric rows to a DataFrame that is safe for sorting/charting.

    Online PDF imports may cache a PDF and create a company/source record even
    when no structured financial indicators are parsed from the PDF. In that
    case fetch_company_metrics returns an empty list; blindly sorting by
    ``year`` would raise KeyError. This helper keeps the UI stable and lets the
    user delete/re-import or fall back to structured Excel/CSV data.
    """
    df = rows_to_df(rows)
    if df.empty or 'year' not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df['year'] = pd.to_numeric(df['year'], errors='coerce')
    df = df.dropna(subset=['year'])
    if df.empty:
        return pd.DataFrame()
    return df.sort_values('year').reset_index(drop=True)


def has_structured_metrics(company_id: int) -> bool:
    df = metric_rows_to_df(fetch_company_metrics(company_id))
    return not df.empty and 'year' in df.columns

st.set_page_config(page_title='投资者智能分析助手', layout='wide')
init_db()
seed_sample_data()

st.title('投资者智能分析助手')
st.caption('面向上市公司财务研究场景，提供企业画像、风险识别、图表分析、交互问答与图文报告生成能力。')

with st.sidebar:
    st.header('分析引擎配置')
    engine_mode = st.selectbox(
        '运行模式',
        ['本地分析模式', '云端增强模式'],
        index=0,
        help='本地分析模式优先使用结构化数据与专业分析规则；云端增强模式在系统数据基础上提升问题理解、表达润色和多角度解释能力。',
    )
    use_llm = engine_mode == '云端增强模式'
    llm_config = None
    api_key = ''
    if use_llm:
        provider = st.selectbox('模型服务商', list(PROVIDER_PRESETS.keys()), index=0)
        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS['DeepSeek'])
        model_options = preset.get('models', [])
        model_choice = st.selectbox('模型 / Endpoint', model_options, index=0) if model_options else ''
        custom_model = ''
        if '自定义' in str(model_choice):
            custom_model = st.text_input('自定义模型名称或 Endpoint ID', placeholder='例如：deepseek-chat / qwen-plus / 火山引擎 Endpoint ID')
        base_url = preset.get('base_url', '')
        with st.expander('高级连接参数', expanded=False):
            base_url = st.text_input('API Base URL', value=base_url, help='用于连接所选国产模型服务的对话生成接口。')
        api_key = st.text_input('API Key', type='password', help='仅在当前会话中用于云端增强调用，请勿提交到公开代码仓库。')
        llm_config = build_llm_config(provider, api_key, custom_model or model_choice, base_url)
        if llm_enabled(llm_config):
            st.success(f'{provider} 云端增强已就绪。')
        else:
            st.info('请输入完整的云端模型连接信息。')
    else:
        st.caption('当前使用结构化数据层与专业分析规则，可完成企业画像、风险识别、图表分析、问答和报告生成。')

    st.divider()
    st.header('投资者画像')
    investor_profile = st.radio('请选择分析偏好', ['稳健型', '平衡型', '成长型'], index=1)

    st.divider()
    st.header('数据接入')
    manual_company_name = st.text_input('手动指定企业名称（可选）', placeholder='例如：招商银行股份有限公司')
    manual_stock_code = st.text_input('股票代码（可选）', placeholder='例如：600036')
    manual_year_hint = st.text_input('报告年份（可选）', placeholder='例如：2024')
    tabular_file = st.file_uploader('上传结构化财务表（xlsx/csv）', type=['xlsx', 'csv'])
    pdf_files = st.file_uploader('上传企业年报 PDF（可多选）', type=['pdf'], accept_multiple_files=True)
    st.caption('说明：结构化表用于量化指标建模；年报 PDF 用于公开来源留痕、资料解析与报告证据。')

    if st.button('导入文件', use_container_width=True):
        msgs: list[tuple[str, str, list[str]]] = []
        year_hint_sidebar = int(manual_year_hint) if str(manual_year_hint).strip().isdigit() else None
        if tabular_file is not None:
            try:
                company_name, warns = ingest_tabular_file(tabular_file.name, tabular_file.read(), manual_company_name or None)
                msgs.append(('success', f'结构化财务表导入完成：{company_name}', warns))
            except Exception as e:
                msgs.append(('error', f'结构化财务表导入暂未完成：{e}', []))
        if pdf_files:
            for f in pdf_files:
                try:
                    if manual_stock_code or year_hint_sidebar:
                        company_name, warns, _cache_path = ingest_online_pdf_bytes(
                            f.name,
                            f.getvalue(),
                            manual_company_name=manual_company_name or None,
                            stock_code=manual_stock_code or None,
                            source_label='uploaded_disclosure_pdf',
                            overwrite_existing=False,
                            year_hint=year_hint_sidebar,
                            report_title=f.name,
                            source_url=f'用户上传：{f.name}',
                        )
                    else:
                        company_name, warns = ingest_pdf_file(f.name, f.read(), manual_company_name or None)
                    msgs.append(('success', f'企业年报 PDF 导入完成：{company_name}', warns))
                except Exception as e:
                    msgs.append(('error', f'企业年报 PDF 导入暂未完成：{f.name}', [str(e)]))
        if msgs:
            for level, m, warns in msgs:
                if level == 'error':
                    st.warning(m)
                    if warns:
                        with st.expander('查看导入详情', expanded=False):
                            for w in warns:
                                st.write('· ' + str(w))
                else:
                    _show_import_messages(m, warns)
        else:
            st.info('请至少上传一个 xlsx/csv 或一个 PDF。')
        st.rerun()

    companies = fetch_companies()
    if not companies:
        st.error('系统暂无企业数据，请导入结构化财务表。')
        st.stop()

    name_to_id = {r['name']: r['id'] for r in companies}
    # Only companies with structured year-based metrics can be used as main
    # analysis targets. Online PDFs that are downloaded but not parsed into
    # structured metrics remain visible in 数据与来源, but are excluded here to
    # avoid KeyError: 'year'.
    names_with_metrics = [r['name'] for r in companies if has_structured_metrics(r['id'])]
    if not names_with_metrics:
        st.error('系统暂无可分析的结构化企业指标。请先导入 xlsx/csv，或导入可成功解析出指标的 PDF。')
        st.stop()
    names = names_with_metrics
    default_main = '比亚迪股份有限公司' if '比亚迪股份有限公司' in names else names[0]
    default_cmp = '宁德时代新能源科技股份有限公司' if '宁德时代新能源科技股份有限公司' in names else (names[1] if len(names) > 1 else default_main)
    selected_main = st.selectbox('主分析企业', names, index=names.index(default_main) if default_main in names else 0)
    compare_candidates = [n for n in names if n != selected_main]
    selected_cmp = st.selectbox('对比企业', compare_candidates, index=compare_candidates.index(default_cmp) if default_cmp in compare_candidates else 0) if compare_candidates else selected_main

# Prepare data maps
companies = fetch_companies()
name_to_id = {r['name']: r['id'] for r in companies}
all_names = list(name_to_id.keys())
data_map: dict[str, pd.DataFrame] = {}
for name, cid in name_to_id.items():
    data_map[name] = metric_rows_to_df(fetch_company_metrics(cid))

analysis_names = [n for n, df in data_map.items() if not df.empty and 'year' in df.columns]
if selected_main not in analysis_names:
    alternate_path = '比亚迪股份有限公司' if '比亚迪股份有限公司' in analysis_names else (analysis_names[0] if analysis_names else None)
    if alternate_path is None:
        st.error('当前数据库中暂无可分析的结构化年度指标。请上传结构化 Excel/CSV，或在“数据与来源”中管理企业数据后重新导入。')
        st.stop()
    selected_main = alternate_path
if selected_cmp not in analysis_names or selected_cmp == selected_main:
    selected_cmp = next((n for n in analysis_names if n != selected_main), selected_main)

main_id = name_to_id[selected_main]
cmp_id = name_to_id[selected_cmp] if selected_cmp in name_to_id else None
main_df = data_map.get(selected_main, pd.DataFrame())
cmp_df = data_map.get(selected_cmp, pd.DataFrame()) if cmp_id else pd.DataFrame()
main_reports = fetch_report_files(main_id)

if main_df.empty or 'year' not in main_df.columns:
    st.error('当前主分析企业暂无可用于量化建模的结构化年度指标。请上传结构化 Excel/CSV，或在“数据与来源”中管理该企业后重新导入。')
    st.stop()

main_alerts = compute_alerts(main_df)
main_summary = build_summary(selected_main, main_df, main_alerts, investor_profile)
main_score = score_company(main_df)
compare_payload = compare_companies(selected_main, main_df, selected_cmp, cmp_df, investor_profile) if not cmp_df.empty and selected_cmp != selected_main else None
latest = main_df.sort_values('year').iloc[-1]

st.subheader(f'当前分析企业：{selected_main}')
metric_cols = st.columns(6)
metric_cols[0].metric('最新年度', int(latest['year']))
metric_cols[1].metric('营业收入(亿元)', display_value(latest.get('revenue')))
metric_cols[2].metric('归母净利润(亿元)', display_value(latest.get('net_profit')))
metric_cols[3].metric('经营现金流(亿元)', display_value(latest.get('operating_cashflow')))
metric_cols[4].metric('ROE(%)', display_value(latest.get('roe')))
metric_cols[5].metric('综合评分', main_score['score'])

st.markdown('### 投资者参考结论')
st.info(main_summary)

risk = risk_dashboard(main_alerts)
risk_cols = st.columns(4)
for i, (k, v) in enumerate(risk.items()):
    icon = '🟢'
    if '重点' in v or '高' in v:
        icon = '🔴'
    elif '关注' in v or '中' in v:
        icon = '🟡'
    risk_cols[i].metric(k, f'{icon} {v}')

tab_online, tab_overview, tab_compare, tab_charts, tab_qa, tab_report, tab_sources = st.tabs(['公开披露检索', '企业总览', '企业对比', '图表分析', '智能问答', '分析报告', '数据与来源'])

with tab_online:
    st.markdown('#### 公开披露检索')
    st.caption('输入公司名称或股票代码，系统可检索上交所、深交所公开披露公告，定位年度报告并形成来源记录。')
    st.info('提示：系统支持结构化数据、公开披露检索、PDF 链接导入与文件上传，便于形成完整的数据来源链路。')

    c0, c1, c2, c3 = st.columns([2.2, 1.2, 1.2, 1.1])
    with c0:
        online_query = st.text_input('公司名称 / 股票代码', value='', placeholder='例如：比亚迪 / 002594 / 上汽集团 / 600104')
    with c1:
        online_source = st.selectbox('披露来源', ['自动判断', '上交所', '深交所'])
    with c2:
        online_report_type = st.selectbox('报告类型', ['年度报告', '半年度报告', '季度报告', '全部公告'])
    with c3:
        online_year = st.selectbox('年份', ['不限', '2025', '2024', '2023', '2022', '2021'], index=2)

    resolved_code = resolve_stock_code(online_query)
    guessed = guess_exchange(online_query) if online_query else None
    if online_query:
        st.caption(f'系统识别：股票代码 {resolved_code or "未识别"}；交易所倾向 {"上交所" if guessed == "sse" else ("深交所" if guessed == "szse" else "待自动尝试") }。')

    if 'online_candidates' not in st.session_state:
        st.session_state.online_candidates = []

    if st.button('搜索公开披露文件', type='primary', use_container_width=True):
        if not online_query.strip():
            st.warning('请输入公司名称或股票代码。')
        else:
            with st.spinner('正在检索公开披露页面（最长约 20 秒，超时会给出原因，不会一直卡住）...'):
                try:
                    year_value = None if online_year == '不限' else int(online_year)
                    items, diag = search_disclosures_with_details(online_query, online_source, online_report_type, year_value, limit=20)
                    st.session_state.online_candidates = [x.as_dict() for x in items]
                    st.session_state.online_diag = diag
                    if items:
                        st.success(f'检索到 {len(items)} 条候选披露文件。')
                    else:
                        st.warning('暂未检索到候选公告。下方“运行详情”可查看本次公开披露检索过程。')
                except Exception as e:
                    st.session_state.online_candidates = []
                    st.session_state.online_diag = {'steps': [f'在线检索暂未完成：{e}'], 'requests': [], 'elapsed_sec': 0}
                    st.error(f'在线检索暂未完成：{e}')

    if st.session_state.get('online_diag'):
        diag = st.session_state.online_diag
        with st.expander('运行详情', expanded=not bool(st.session_state.online_candidates)):
            st.write(f"耗时：{diag.get('elapsed_sec', 0)} 秒")
            for step in diag.get('steps', []):
                st.write('• ' + str(step))
            reqs = diag.get('requests', [])
            if reqs:
                st.caption('检索过程记录：status=200 表示接口可访问；parsed 表示该次检索解析出的候选数量。')
                st.dataframe(pd.DataFrame(reqs), use_container_width=True)

    if st.session_state.online_candidates:
        st.markdown('##### 候选公告列表')
        cand_df = pd.DataFrame(st.session_state.online_candidates)
        st.dataframe(cand_df, use_container_width=True)
        titles = [f"{i+1}. {r.get('公告标题','')} | {r.get('来源','')} | {r.get('披露日期','')}" for i, r in enumerate(st.session_state.online_candidates)]
        picked = st.selectbox('选择要导入解析的公告', titles)
        picked_idx = titles.index(picked)
        picked_row = st.session_state.online_candidates[picked_idx]
        overwrite_online = st.checkbox('允许覆盖已有同年度指标（谨慎使用）', value=False)
        manual_online_name = st.text_input('在线导入企业名称修正（可选）', value=online_query if online_query and not resolved_code else '')
        if st.button('导入所选公告 PDF', use_container_width=True):
            with st.spinner('正在接入公开披露文件、解析字段并写入本地来源库...'):
                try:
                    label = 'online_sse' if picked_row.get('来源') == '上交所' else 'online_szse'
                    year_hint_value = None if online_year == '不限' else int(online_year)
                    preferred_company_name = manual_online_name or picked_row.get('公司名称') or None
                    company_name, warns, cache_path = ingest_online_pdf_url(
                        picked_row['PDF链接'],
                        manual_company_name=preferred_company_name,
                        stock_code=picked_row.get('股票代码') or resolved_code,
                        source_label=label,
                        overwrite_existing=overwrite_online,
                        year_hint=year_hint_value,
                        report_title=picked_row.get('公告标题'),
                    )
                    _show_import_messages(f'在线披露文件导入完成：{company_name}', warns)
                    st.info('导入后可在“数据与来源”查看来源文件；若该企业已有结构化指标，可进入企业总览、图表分析、智能问答或分析报告继续查看。')
                except Exception as e:
                    st.warning('系统已定位到公开披露文件，可通过公开 PDF 上传导入继续完成资料接入。')
                    msg = str(e)
                    if ('上交所网页/访问校验页面' in msg) or ('返回内容不是 PDF' in msg) or ('text/html' in msg):
                        st.info('提示：请点击候选表格中的 PDF 链接，用浏览器下载原始年报，再通过下方“公开披露 PDF 上传导入”继续入库分析。')
                    st.info('该流程已纳入公开披露资料接入方案：系统负责检索定位公告，上传后系统继续完成来源留痕与结构化处理。')

        st.markdown('##### 公开披露 PDF 上传导入')
        st.caption('适用于已通过公开披露检索定位到年报 PDF 的场景。先点击候选表格中的 PDF 链接下载原始文件，再在这里上传，系统会延续当前公告元数据完成来源入库。')
        uploaded_exchange_pdf = st.file_uploader('上传从公开披露页面下载的公告 PDF', type=['pdf'], key='browser_exchange_pdf')
        if uploaded_exchange_pdf is not None:
            st.caption(f'将按当前候选公告元数据导入：{picked_row.get("公告标题", "")}｜{picked_row.get("股票代码", "")}｜{online_year}')
        if st.button('解析上传的公开披露 PDF 并写入数据库', use_container_width=True):
            if uploaded_exchange_pdf is None:
                st.warning('请先上传公告 PDF 文件。')
            else:
                with st.spinner('正在解析公开披露 PDF 并写入本地来源库...'):
                    try:
                        year_hint_value = None if online_year == '不限' else int(online_year)
                        label = 'browser_sse_pdf' if picked_row.get('来源') == '上交所' else 'browser_szse_pdf'
                        preferred_company_name = manual_online_name or picked_row.get('公司名称') or None
                        company_name, warns, cache_path = ingest_online_pdf_bytes(
                            uploaded_exchange_pdf.name,
                            uploaded_exchange_pdf.getvalue(),
                            manual_company_name=preferred_company_name,
                            stock_code=picked_row.get('股票代码') or resolved_code,
                            source_label=label,
                            overwrite_existing=overwrite_online,
                            year_hint=year_hint_value,
                            report_title=picked_row.get('公告标题'),
                            source_url=picked_row.get('PDF链接'),
                        )
                        _show_import_messages(f'公开披露 PDF 导入完成：{company_name}', warns)
                        st.info('导入后可在“数据与来源”查看来源文件；若该企业已有结构化指标，可继续进入企业总览、图表分析、智能问答或分析报告。')
                    except Exception as e:
                        st.warning('公开披露 PDF 导入暂未完成：' + str(e))
                        st.info('请确认上传的是原始公告 PDF。系统核心演示仍可通过内置结构化样例库与已导入表格稳定运行。')

    st.markdown('---')
    st.markdown('#### 公开 PDF 链接导入')
    st.caption('适用于已经拿到公开年报 PDF 链接的场景，可直接粘贴链接导入来源库。')
    url_col1, url_col2 = st.columns([3, 1])
    with url_col1:
        direct_pdf_url = st.text_input('公开 PDF 链接', placeholder='https://.../xxx.pdf')
    with url_col2:
        direct_year_hint = st.text_input('年份提示（可选）', placeholder='2024')
    direct_name = st.text_input('企业名称（建议填写）', placeholder='例如：某某股份有限公司')
    direct_code = st.text_input('股票代码（可选）', placeholder='例如：002594')
    direct_overwrite = st.checkbox('PDF 链接导入时允许覆盖已有同年度指标', value=False)
    if st.button('导入 PDF 链接并解析', use_container_width=True):
        if not direct_pdf_url.strip():
            st.warning('请填写公开 PDF 链接。')
        else:
            with st.spinner('正在接入公开 PDF 并解析...'):
                try:
                    company_name, warns, cache_path = ingest_online_pdf_url(
                        direct_pdf_url.strip(),
                        manual_company_name=direct_name or None,
                        stock_code=direct_code or None,
                        source_label='online_pdf_url',
                        overwrite_existing=direct_overwrite,
                        year_hint=int(direct_year_hint) if str(direct_year_hint).strip().isdigit() else None,
                        report_title=None,
                    )
                    _show_import_messages(f'PDF 链接导入完成：{company_name}', warns)
                    st.info('导入后可在“数据与来源”查看来源文件；若该企业已有结构化指标，可进入企业总览、图表分析或分析报告继续查看。')
                except Exception as e:
                    st.warning('PDF 链接导入暂未完成：' + str(e))
                    st.info('请确认链接对应公开年报 PDF；如浏览器能打开，可下载后使用上方“公开披露 PDF 上传导入”。')

with tab_overview:
    st.markdown(f'#### {selected_main} 指标看板')
    show_cols = [c for c in ['year', 'revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio', 'gross_margin', 'eps', 'audit_opinion', 'raw_source'] if c in main_df.columns]
    st.dataframe(main_df[show_cols], use_container_width=True)
    st.markdown('#### 四维能力雷达图')
    st.plotly_chart(make_radar_chart(main_score, selected_main, title=f'{selected_main} 四维能力画像'), use_container_width=True, key='overview_radar')

    c1, c2 = st.columns(2)
    with c1:
        fig = make_line_chart(main_df, ['revenue', 'net_profit', 'operating_cashflow'], f'{selected_main} 规模与现金流趋势')
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True, key='overview_scale')
        else:
            st.info('暂无可用于绘制规模与现金流趋势的数值数据。')
    with c2:
        fig = make_line_chart(main_df, ['roe', 'debt_ratio', 'gross_margin'], f'{selected_main} 盈利能力与杠杆趋势')
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True, key='overview_ratio')
        else:
            st.info('暂无可用于绘制盈利能力与杠杆趋势的数值数据。')

    st.markdown('#### 风险预警与规则依据')
    for idx, alert in enumerate(main_alerts):
        icon = {'red': '🔴', 'orange': '🟠', 'yellow': '🟡', 'green': '🟢'}.get(alert['level'], '🔵')
        with st.expander(f'{icon} {alert["title"]}', expanded=idx == 0):
            st.write(alert['message'])
            st.markdown(f'**规则依据：** {alert.get("rule", "-")}')
            st.markdown(f'**数据依据：** {alert.get("basis", "-")}')
            st.markdown(f'**数据来源：** {alert.get("source", "-")}')

with tab_compare:
    st.markdown(f'#### {selected_main} vs {selected_cmp}')
    if compare_payload is None:
        st.info('当前对比企业暂无可用数据。可在左侧上传新的企业结构化财务表。')
    else:
        cc = st.columns(3)
        cc[0].metric(f'{selected_main} 综合评分', compare_payload['score_a']['score'])
        cc[1].metric(f'{selected_cmp} 综合评分', compare_payload['score_b']['score'])
        cc[2].metric('研究辅助倾向', compare_payload['recommended'])
        st.write(compare_payload['reasoning'])

        st.markdown('##### 评分拆解')
        st.dataframe(compare_payload['score_table'], use_container_width=True)
        with st.expander('查看评分依据'):
            for name, basis in compare_payload['basis'].items():
                st.markdown(f'**{name}**')
                for item in basis:
                    st.write('- ' + item)

        st.markdown('##### 双企业四维评分雷达图')
        st.plotly_chart(make_radar_chart(compare_payload['score_a'], selected_main, compare_payload['score_b'], selected_cmp, title=f'{selected_main} vs {selected_cmp} 四维能力对比'), use_container_width=True, key='cmp_radar')

        st.markdown('##### 核心指标对比')
        st.dataframe(compare_payload['table'], use_container_width=True)
        cmp_latest_a = main_df.iloc[-1]
        cmp_latest_b = cmp_df.iloc[-1]
        chart_df = pd.DataFrame([
            {'企业': selected_main, 'ROE': safe_num(cmp_latest_a.get('roe')), '归母净利润': safe_num(cmp_latest_a.get('net_profit')), '资产负债率': safe_num(cmp_latest_a.get('debt_ratio'))},
            {'企业': selected_cmp, 'ROE': safe_num(cmp_latest_b.get('roe')), '归母净利润': safe_num(cmp_latest_b.get('net_profit')), '资产负债率': safe_num(cmp_latest_b.get('debt_ratio'))},
        ])
        d1, d2 = st.columns(2)
        with d1:
            st.plotly_chart(px.bar(chart_df, x='企业', y='ROE', title='ROE 对比'), use_container_width=True, key='cmp_bar_roe')
        with d2:
            st.plotly_chart(px.bar(chart_df, x='企业', y='归母净利润', title='归母净利润对比'), use_container_width=True, key='cmp_bar_profit')


with tab_charts:
    st.markdown('#### 多图表可视化分析')
    st.caption('用户可自主选择折线图、柱形图、饼图、雷达图，用于观察企业趋势、结构和能力画像。')
    chart_company = st.selectbox('选择图表企业', analysis_names, index=analysis_names.index(selected_main) if selected_main in analysis_names else 0, key='chart_company')
    chart_df = data_map.get(chart_company, pd.DataFrame())
    chart_type = st.selectbox('图表类型', ['折线图', '柱形图', '饼图', '雷达图'], key='chart_type')
    metric_options = ['revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio', 'gross_margin', 'eps']
    default_metrics = ['revenue', 'net_profit', 'operating_cashflow']
    selected_metrics = st.multiselect(
        '选择指标',
        metric_options,
        default=[m for m in default_metrics if m in metric_options],
        format_func=lambda x: METRIC_LABELS.get(x, x),
        key='chart_metrics',
    )
    if chart_df.empty:
        st.info('该企业暂无可视化数据。')
    elif chart_type == '雷达图':
        st.plotly_chart(make_radar_chart(score_company(chart_df), chart_company, title=f'{chart_company} 四维能力雷达图'), use_container_width=True, key='chart_radar')
        st.caption('雷达图来自系统四维评分：盈利能力、现金流质量、偿债稳健性、成长能力。')
    else:
        fig = make_metric_chart(chart_df, chart_type, selected_metrics, f'{chart_company} {chart_type}分析')
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True, key='chart_general')
        else:
            st.info('当前选择的指标暂无足够数值数据，建议更换指标或图表类型。')

with tab_qa:
    st.markdown('#### 交互式分析')
    st.caption('支持财务查询、趋势分析、风险预警、企业对比、指标解释和报告生成。非投资分析问题会被引导，不会强行套用财务答案。')
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []

    quick_questions = [
        f'{selected_main}最新财务摘要',
        f'{selected_main}有哪些风险预警',
        f'{selected_main}近三年营收趋势如何',
        f'{selected_main}和{selected_cmp}谁更适合{investor_profile}投资者',
        'ROE是什么意思',
        f'生成{selected_main}投资者分析报告',
    ]
    quick = st.columns(3)
    for idx, qq in enumerate(quick_questions):
        if quick[idx % 3].button(qq, use_container_width=True, key=f'quick_{idx}'):
            st.session_state.chat_history.append(qq)

    question = st.chat_input(f'例如：{selected_main} 2025年净利润是多少？{selected_main} 为什么利润下降？')
    if question:
        st.session_state.chat_history.append(question)

    if st.button('清空对话记录'):
        st.session_state.chat_history = []
        st.rerun()

    for i, q in enumerate(reversed(st.session_state.chat_history[-12:])):
        with st.chat_message('user'):
            st.write(q)
        with st.chat_message('assistant'):
            result = answer_question(
                q,
                selected_main,
                selected_cmp,
                all_names,
                data_map,
                investor_profile,
                llm_config if use_llm and llm_enabled(llm_config) else None,
            )
            st.write(result['answer'])
            with st.expander('查看系统识别与依据'):
                st.json(result['parsed'])
                if result.get('agent_trace'):
                    st.markdown('**智能体协同链路**')
                    st.dataframe(pd.DataFrame(result['agent_trace']), use_container_width=True)
                st.text(result['evidence'])
                st.caption(f'分析模式：{result["model_used"]}')
            if result.get('chart') == 'trend':
                fig = make_line_chart(main_df, ['revenue', 'net_profit', 'operating_cashflow'], f'{selected_main} 核心趋势')
                if fig is not None:
                    st.plotly_chart(
                        fig,
                        use_container_width=True,
                        key=f'chat_trend_{i}',
                    )
                else:
                    st.info('暂无可用于绘制趋势图的数值数据。')

with tab_report:
    st.markdown('#### 一键生成投资者分析报告')
    st.caption('报告包含关键指标卡、趋势图、雷达图、年度指标表、风险关注点和完整文字解读。')
    report_text = generate_report(selected_main, main_df, main_alerts, compare_payload, investor_profile)
    if use_llm and llm_enabled(llm_config):
        st.markdown('##### 云端增强')
        st.caption('云端模型将在不改变原始数据的前提下，增强报告表达、结构衔接和投资者视角。')
        if st.button('使用云端模型增强报告文字', use_container_width=True):
            with st.spinner('正在进行云端报告增强...'):
                try:
                    st.session_state.enhanced_report_text = enhance_report_with_llm(llm_config, report_text, selected_main, investor_profile)
                    st.success('报告文字已完成云端增强。')
                except Exception as e:
                    st.info('云端增强服务暂未返回有效结果，当前展示可信数据分析报告。')
        if st.session_state.get('enhanced_report_text'):
            report_text = st.session_state.enhanced_report_text
    report_score = score_company(main_df)
    latest_report_row = main_df.sort_values('year').iloc[-1] if not main_df.empty else None

    if latest_report_row is not None:
        kpi_cols = st.columns(5)
        kpi_cols[0].metric('最新年度', int(latest_report_row.get('year')))
        kpi_cols[1].metric('营业收入(亿元)', display_value(latest_report_row.get('revenue')))
        kpi_cols[2].metric('归母净利润(亿元)', display_value(latest_report_row.get('net_profit')))
        kpi_cols[3].metric('经营现金流(亿元)', display_value(latest_report_row.get('operating_cashflow')))
        kpi_cols[4].metric('综合评分', f'{report_score.get("score", 0)}')

    vcol1, vcol2 = st.columns([1.25, 1])
    with vcol1:
        fig_report_trend = make_line_chart(main_df, ['revenue', 'net_profit', 'operating_cashflow'], f'{selected_main} 核心指标趋势')
        if fig_report_trend is not None:
            st.plotly_chart(fig_report_trend, use_container_width=True, key='report_trend_chart')
    with vcol2:
        st.plotly_chart(make_radar_chart(report_score, selected_main, title='四维能力雷达图'), use_container_width=True, key='report_radar_chart')

    if not main_df.empty:
        show_cols = [c for c in ['year', 'revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio', 'gross_margin', 'eps'] if c in main_df.columns]
        display_df = main_df[show_cols].sort_values('year').copy()
        display_df = display_df.rename(columns={'year': '年度', **{k: METRIC_LABELS.get(k, k) for k in show_cols}})
        st.markdown('##### 年度指标明细')
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.markdown('##### 报告文字预览')
    st.text_area('报告预览', report_text, height=520)
    rcol1, rcol2 = st.columns(2)
    with rcol1:
        st.download_button(
            '下载 TXT 报告',
            data=report_text.encode('utf-8'),
            file_name=f'{selected_main}_投资者分析报告.txt',
            mime='text/plain',
            use_container_width=True,
        )
    with rcol2:
        try:
            pdf_bytes = report_text_to_pdf_bytes(report_text, selected_main, df=main_df, score=report_score)
            st.download_button(
                '下载 图文PDF报告',
                data=pdf_bytes,
                file_name=f'{selected_main}_图文投资者分析报告.pdf',
                mime='application/pdf',
                use_container_width=True,
            )
        except Exception as e:
            st.warning(f'PDF 报告生成暂未完成：{e}')

with tab_sources:
    st.markdown('#### 数据库中的企业')
    company_rows = []
    for r in companies:
        df = data_map.get(r['name'], pd.DataFrame())
        years = '' if df.empty or 'year' not in df.columns else f"{int(df['year'].min())}-{int(df['year'].max())}"
        company_rows.append({'企业名称': r['name'], '股票代码': r['stock_code'], '来源': r['source'], '数据年度': years, '记录数': len(df)})
    st.dataframe(pd.DataFrame(company_rows), use_container_width=True)

    st.markdown('#### 来源信息')
    st.write('每条指标和预警均保留 raw_source 或上传文件记录，用于解释结论来源。')
    if main_reports:
        for rp in main_reports[:20]:
            with st.expander(f"{rp['file_name']} | {rp['file_type']} | {rp['parse_status']}"):
                st.write(f"文件路径：{rp['file_path']}")
                if rp['report_year']:
                    st.write(f"报告年份：{rp['report_year']}")
                if rp['note']:
                    st.write(rp['note'])
    else:
        st.caption('当前企业暂无额外上传来源，正在使用内置演示数据。')

    uploaded = [r for r in companies if r['source'] != 'builtin']
    if uploaded:
        st.markdown('#### 上传企业管理')
        del_name = st.selectbox('选择要删除的上传企业', [r['name'] for r in uploaded])
        if st.button('删除所选上传企业'):
            ok = delete_uploaded_company(name_to_id[del_name])
            if ok:
                st.success(f'已删除：{del_name}')
                st.rerun()
            else:
                st.warning('内置企业不能删除。')
