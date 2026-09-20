from __future__ import annotations

import re
from typing import Any

import pandas as pd

from core.analysis import (
    METRIC_LABELS,
    build_summary,
    compare_companies,
    compute_alerts,
    explain_metric,
    generate_report,
    metric_query,
    score_company,
    trend_text,
)
from core.llm import llm_enabled, parse_question_with_llm, polish_answer_with_llm
from core.agents import build_agent_trace, agent_trace_text
from core.text_to_sql import run_text_to_sql

KEYWORDS = {
    'revenue': ['营业收入', '营收', '收入', 'revenue'],
    'net_profit': ['归母净利润', '净利润', '利润', 'net profit'],
    'operating_cashflow': ['经营现金流', '现金流', '经营活动现金流'],
    'roe': ['roe', '净资产收益率'],
    'debt_ratio': ['资产负债率', '负债率', '杠杆'],
    'gross_margin': ['毛利率'],
    'eps': ['每股收益', 'eps'],
}

METRIC_FROM_CN = {v: k for k, arr in KEYWORDS.items() for v in arr}


def _contains_any(text: str, words: list[str]) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def _extract_years(question: str) -> list[int]:
    return [int(x) for x in re.findall(r'20\d{2}', question)]


def _detect_metrics(question: str) -> list[str]:
    hits = []
    for key, words in KEYWORDS.items():
        if _contains_any(question, words):
            hits.append(key)
    return hits


def _detect_companies(question: str, companies: list[str], default_company: str) -> list[str]:
    hits = []
    for name in companies:
        short = name.replace('股份有限公司', '').replace('新能源科技', '').replace('有限公司', '')
        if name in question or short in question:
            hits.append(name)
    if not hits and default_company:
        hits = [default_company]
    return hits


def _detect_profile(question: str, selected_profile: str) -> str:
    if '稳健' in question:
        return '稳健型'
    if '成长' in question:
        return '成长型'
    if '平衡' in question or '均衡' in question:
        return '平衡型'
    return selected_profile or '平衡型'


def _local_parse(question: str, companies: list[str], default_company: str, selected_profile: str) -> dict[str, Any]:
    q = question.lower()
    years = _extract_years(question)
    metrics = _detect_metrics(question)
    companies_found = _detect_companies(question, companies, default_company)
    profile = _detect_profile(question, selected_profile)

    if _contains_any(question, ['天气', '吃什么', '电影', '旅游', '笑话', '翻译一下']) and not metrics:
        intent = 'out_of_scope'
    elif _contains_any(question, ['会不会涨', '明天涨', '该不该买', '能买吗', '买入', '卖出', '收益保证', '稳赚']):
        intent = 'refusal'
    elif _contains_any(question, ['报告', '生成报告', '分析报告']):
        intent = 'report_generate'
    elif _contains_any(question, ['什么意思', '是什么', '解释']) and metrics:
        intent = 'metric_explain'
    elif _contains_any(question, ['对比', '相比', '谁更', '哪个更', '更适合', '更值得关注']):
        intent = 'company_compare'
    elif _contains_any(question, ['风险', '预警', '问题', '隐患']):
        intent = 'risk_warning'
    elif _contains_any(question, ['趋势', '近三年', '变化', '下降原因', '为什么', '增收不增利']):
        intent = 'trend_analysis'
    elif metrics:
        intent = 'finance_query'
    elif _contains_any(question, ['摘要', '总结', '结论', '分析一下']):
        intent = 'investment_summary'
    else:
        intent = 'unknown'
    return {
        'intent': intent,
        'companies': companies_found,
        'years': years,
        'metrics': metrics,
        'investor_profile': profile,
        'reason': '可信数据层解析',
    }


def parse_question(question: str, companies: list[str], default_company: str, selected_profile: str, llm_config: dict | str | None = None) -> dict[str, Any]:
    local = _local_parse(question, companies, default_company, selected_profile)
    if not llm_enabled(llm_config):
        return local
    try:
        parsed = parse_question_with_llm(llm_config or {}, question, companies)
        if parsed.get('intent'):
            for key in ['companies', 'years', 'metrics']:
                if not isinstance(parsed.get(key), list):
                    parsed[key] = []
            if not parsed['companies']:
                parsed['companies'] = local['companies']
            if not parsed.get('investor_profile') or parsed.get('investor_profile') == '未知':
                parsed['investor_profile'] = local['investor_profile']
            return parsed
    except Exception:
        return local
    return local


def _company_data(name: str, data_map: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return data_map.get(name, pd.DataFrame())


def _evidence_from(company: str, df: pd.DataFrame, alerts: list[dict[str, str]], extra: str = '') -> str:
    parts = [f'企业：{company}']
    if not df.empty:
        latest = df.sort_values('year').iloc[-1]
        parts.append(f'最新年度：{int(latest["year"])}')
        for key, label in METRIC_LABELS.items():
            if key in latest and pd.notna(latest.get(key)):
                parts.append(f'{label}：{latest.get(key)}')
    if alerts:
        parts.append('风险预警：' + '；'.join([f'{a["title"]}（{a["basis"]}；{a["rule"]}）' for a in alerts]))
    if extra:
        parts.append(extra)
    return '\n'.join(parts)


def answer_question(
    question: str,
    selected_company: str,
    selected_compare: str,
    all_company_names: list[str],
    data_map: dict[str, pd.DataFrame],
    profile: str = '平衡型',
    llm_config: dict | str | None = None,
    resolved_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = parse_question(question, all_company_names, selected_company, profile, llm_config if llm_config else None)
    if resolved_context:
        parsed.update({
            key: resolved_context[key]
            for key in ['intent', 'companies', 'years', 'metrics', 'investor_profile', 'reason']
            if key in resolved_context
        })
    intent = parsed.get('intent') or 'unknown'
    agent_trace = build_agent_trace(intent, parsed)
    companies = parsed.get('companies') or [selected_company]
    years = parsed.get('years') or []
    metrics = parsed.get('metrics') or []
    investor_profile = parsed.get('investor_profile') or profile

    # keep at most two companies for comparison
    primary = companies[0] if companies else selected_company
    if primary not in data_map:
        primary = selected_company
    secondary = None
    for c in companies[1:]:
        if c != primary and c in data_map:
            secondary = c
            break
    if secondary is None and selected_compare in data_map and selected_compare != primary:
        secondary = selected_compare

    query_data_map = data_map
    sql_result: dict[str, Any] = {
        'status': 'not_applicable', 'sql_status': 'not_applicable', 'attempts': [], 'rows': [],
    }
    if intent in {'finance_query', 'trend_analysis', 'company_compare'}:
        sql_result = run_text_to_sql(parsed)
        if sql_result['status'] == 'success':
            sql_df = pd.DataFrame(sql_result['rows'])
            query_data_map = {}
            for company_name in companies[:2]:
                company_rows = sql_df[sql_df['company'] == company_name].drop(columns=['company'], errors='ignore')
                query_data_map[company_name] = company_rows.reset_index(drop=True)
        else:
            sql_result['sql_status'] = 'fallback'

    df = _company_data(primary, query_data_map)
    if df.empty and sql_result.get('sql_status') == 'fallback':
        df = _company_data(primary, data_map)
    alerts = compute_alerts(df)
    draft = ''
    evidence_extra = ''
    chart = None
    report_text = None

    if intent == 'out_of_scope':
        draft = '本系统聚焦上市公司财务分析、风险预警、企业对比和投资者参考结论，暂不提供天气、生活服务或通用闲聊回答。你可以询问企业财务、风险或对比相关问题。'
    elif intent == 'refusal':
        draft = '本系统不预测短期股价涨跌，也不提供买入、卖出或保证收益类建议。可以基于已接入的财务指标、风险预警和企业对比结果，生成投资者参考结论。'
    elif intent == 'metric_explain':
        metric = metrics[0] if metrics else 'roe'
        draft = explain_metric(metric)
        if df is not None and not df.empty and metrics:
            draft += '\n\n' + metric_query(primary, df, metric, years[0] if years else None)
    elif intent == 'finance_query':
        query_metrics = metrics or ['net_profit']
        draft = '\n'.join(metric_query(primary, df, metric, years[0] if years else None) for metric in query_metrics)
    elif intent == 'trend_analysis':
        metric = metrics[0] if metrics else 'revenue'
        draft = trend_text(primary, df, metric)
        if '为什么' in question or '原因' in question or '增收不增利' in question:
            draft += '\n原因分析：系统会结合营收、归母净利润、经营现金流、ROE和毛利率变化判断。如果收入增长但利润或现金流下降，通常意味着盈利转化效率、成本压力或现金回款质量需要重点跟踪。'
        chart = 'trend'
    elif intent == 'risk_warning':
        draft = '；'.join([f'{a["title"]}：{a["message"]}\n规则依据：{a.get("rule", "-")}\n数据依据：{a.get("basis", "-")}\n来源：{a.get("source", "-")}' for a in alerts])
    elif intent == 'company_compare':
        if secondary is None or secondary == primary:
            draft = '当前没有可用于对比的第二家企业，请在左侧选择对比企业，或上传新的企业结构化财务数据。'
        else:
            secondary_df = _company_data(secondary, query_data_map)
            if secondary_df.empty and sql_result.get('sql_status') == 'fallback':
                secondary_df = _company_data(secondary, data_map)
            cmp = compare_companies(primary, df, secondary, secondary_df, investor_profile)
            draft = cmp['reasoning'] + '\n评分拆解：\n' + cmp['score_table'].to_string(index=False)
            evidence_extra = '企业对比评分依据：\n' + '\n'.join([f'{k}：' + '；'.join(v) for k, v in cmp['basis'].items()])
    elif intent == 'report_generate':
        cmp = None
        if secondary and secondary in data_map and secondary != primary:
            cmp = compare_companies(primary, df, secondary, _company_data(secondary, data_map), investor_profile)
        report_text = generate_report(primary, df, alerts, cmp, investor_profile)
        draft = '已生成投资者分析报告，可在“分析报告”页查看并下载。\n\n' + report_text[:800] + ('...' if len(report_text) > 800 else '')
    elif intent == 'investment_summary' or intent == 'unknown':
        if intent == 'unknown':
            draft = '我暂时没有识别出非常具体的分析意图。以下先给出企业摘要，你也可以继续询问“净利润是多少”“有哪些风险”“和宁德时代谁更适合稳健型投资者”等问题。\n\n'
        draft += build_summary(primary, df, alerts, investor_profile)
    else:
        draft = build_summary(primary, df, alerts, investor_profile)

    evidence = _evidence_from(primary, df, alerts, evidence_extra)
    evidence = evidence + '\n\n智能体协同链路：\n' + agent_trace_text(agent_trace)
    final_answer = draft
    model_used = '可信数据分析'
    if llm_enabled(llm_config) and intent not in ['out_of_scope', 'refusal']:
        try:
            final_answer = polish_answer_with_llm(llm_config or {}, question, evidence, draft, investor_profile)
            model_used = '云端大模型增强'
        except Exception as e:
            final_answer = draft + '\n\n云端增强服务暂未返回有效结果，当前展示可信数据分析结果。'
            model_used = '可信数据分析'
    return {
        'answer': final_answer,
        'draft': draft,
        'parsed': parsed,
        'evidence': evidence,
        'chart': chart,
        'report_text': report_text,
        'model_used': model_used,
        'company': primary,
        'agent_trace': agent_trace,
        'sql_result': sql_result,
        'sql_status': sql_result.get('sql_status', 'not_applicable'),
    }
