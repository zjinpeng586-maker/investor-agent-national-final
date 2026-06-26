from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import math

import pandas as pd

METRIC_LABELS = {
    'revenue': '营业收入',
    'net_profit': '归母净利润',
    'operating_cashflow': '经营现金流',
    'roe': 'ROE',
    'debt_ratio': '资产负债率',
    'gross_margin': '毛利率',
    'eps': '每股收益',
}

METRIC_UNITS = {
    'revenue': '亿元',
    'net_profit': '亿元',
    'operating_cashflow': '亿元',
    'roe': '%',
    'debt_ratio': '%',
    'gross_margin': '%',
    'eps': '元/股',
}

PROFILE_RULES = {
    '稳健型': '更重视现金流质量、资产负债率和利润稳定性。',
    '平衡型': '综合关注盈利能力、现金流质量、偿债稳健性与成长能力。',
    '成长型': '更重视营收增长、盈利弹性、长期成长空间和产业链优势。',
}


NUMERIC_COLUMNS = [
    'year', 'revenue', 'net_profit', 'operating_cashflow',
    'roe', 'debt_ratio', 'gross_margin', 'eps'
]


def _normalize_numeric_value(value: Any) -> Any:
    """Convert common financial strings to numbers without breaking text fields.

    Uploaded Excel/PDF extracts may contain values like '24.72%',
    '1,332.20亿元', '--' or empty strings. Plotly and scoring functions need
    pure numeric columns, so we normalize these values at the data-frame edge.
    """
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value).strip()
    if text in {'', '-', '--', '—', '暂无数据', 'nan', 'None'}:
        return None
    for token in [',', '，', '%', '％', '亿元', '亿', '元/股', '元', '倍']:
        text = text.replace(token, '')
    try:
        return float(text)
    except Exception:
        return None


def clean_financial_df(df: pd.DataFrame) -> pd.DataFrame:
    """Return a safe financial DataFrame for charts, scoring and QA.

    This prevents failures after users import third-party spreadsheets whose
    columns are read as object/string types. Missing optional numeric columns
    stay as NaN and will be skipped or displayed as unavailable.
    """
    if df.empty:
        return df
    work = df.copy()
    for col in NUMERIC_COLUMNS:
        if col in work.columns:
            work[col] = work[col].map(_normalize_numeric_value)
            work[col] = pd.to_numeric(work[col], errors='coerce')
    if 'year' in work.columns:
        work = work.dropna(subset=['year'])
        if not work.empty:
            work['year'] = work['year'].astype(int)
            work = work.sort_values('year').reset_index(drop=True)
    return work


def rows_to_df(rows: list[Any]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    if isinstance(rows[0], dict):
        df = pd.DataFrame(rows)
    else:
        df = pd.DataFrame([dict(r) for r in rows])
    return clean_financial_df(df)


def safe_num(v: Any) -> float | None:
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def fmt_value(key: str, value: Any) -> str:
    val = safe_num(value)
    if val is None:
        return '暂无数据'
    unit = METRIC_UNITS.get(key, '')
    if key == 'eps':
        return f'{val:.2f}{unit}'
    return f'{val:.2f}{unit}'


def latest_row(df: pd.DataFrame) -> pd.Series | None:
    if df.empty:
        return None
    return df.sort_values('year').iloc[-1]


def previous_row(df: pd.DataFrame) -> pd.Series | None:
    if df.empty or len(df) < 2:
        return None
    return df.sort_values('year').iloc[-2]


def get_source_for_metric(df: pd.DataFrame, year: int | None = None) -> str:
    if df.empty:
        return '暂无来源记录'
    work = df.sort_values('year')
    if year is not None and 'year' in work.columns:
        one = work[work['year'] == year]
        if not one.empty:
            src = one.iloc[-1].get('raw_source')
            return str(src) if pd.notna(src) else '结构化财务数据表'
    src = work.iloc[-1].get('raw_source')
    return str(src) if pd.notna(src) else '结构化财务数据表'


def compute_alerts(df: pd.DataFrame) -> list[dict[str, str]]:
    if df.empty:
        return []
    df = df.sort_values('year').reset_index(drop=True)
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    year = int(latest.get('year'))
    source = get_source_for_metric(df, year)
    alerts: list[dict[str, str]] = []

    if prev is not None:
        prev_year = int(prev.get('year'))
        if safe_num(latest.get('net_profit')) is not None and safe_num(prev.get('net_profit')) not in (None, 0):
            yoy = (float(latest['net_profit']) - float(prev['net_profit'])) / abs(float(prev['net_profit'])) * 100
            if yoy < -10:
                alerts.append({
                    'level': 'orange',
                    'title': '净利润下滑预警',
                    'message': f'{year}年归母净利润同比下降 {abs(yoy):.2f}%，盈利能力出现阶段性承压。',
                    'rule': 'R01：最近一年归母净利润同比下降超过10%触发预警。',
                    'basis': f'{prev_year}年归母净利润 {float(prev["net_profit"]):.2f}亿元，{year}年归母净利润 {float(latest["net_profit"]):.2f}亿元。',
                    'source': source,
                })
        if all(safe_num(x) is not None for x in [latest.get('revenue'), prev.get('revenue'), latest.get('net_profit'), prev.get('net_profit')]) and safe_num(prev.get('revenue')) not in (None, 0) and safe_num(prev.get('net_profit')) not in (None, 0):
            rev_yoy = (float(latest['revenue']) - float(prev['revenue'])) / abs(float(prev['revenue'])) * 100
            np_yoy = (float(latest['net_profit']) - float(prev['net_profit'])) / abs(float(prev['net_profit'])) * 100
            if rev_yoy > 0 and np_yoy < 0:
                alerts.append({
                    'level': 'yellow',
                    'title': '增收不增利',
                    'message': f'{year}年营业收入同比增长 {rev_yoy:.2f}%，但归母净利润同比下降 {abs(np_yoy):.2f}%。',
                    'rule': 'R02：营业收入增长但归母净利润下降，触发盈利转化效率预警。',
                    'basis': f'{year}年营收 {float(latest["revenue"]):.2f}亿元，归母净利润 {float(latest["net_profit"]):.2f}亿元。',
                    'source': source,
                })
        if safe_num(latest.get('operating_cashflow')) is not None and safe_num(prev.get('operating_cashflow')) not in (None, 0):
            cfo_yoy = (float(latest['operating_cashflow']) - float(prev['operating_cashflow'])) / abs(float(prev['operating_cashflow'])) * 100
            if cfo_yoy < -30:
                alerts.append({
                    'level': 'red',
                    'title': '经营现金流恶化',
                    'message': f'{year}年经营现金流同比下降 {abs(cfo_yoy):.2f}%，主业现金创造能力需要重点跟踪。',
                    'rule': 'R03：最近一年经营现金流同比下降超过30%触发高关注预警。',
                    'basis': f'{prev_year}年经营现金流 {float(prev["operating_cashflow"]):.2f}亿元，{year}年经营现金流 {float(latest["operating_cashflow"]):.2f}亿元。',
                    'source': source,
                })
        if safe_num(latest.get('roe')) is not None and safe_num(prev.get('roe')) is not None:
            drop = float(prev['roe']) - float(latest['roe'])
            if drop > 5:
                alerts.append({
                    'level': 'orange',
                    'title': 'ROE大幅下滑',
                    'message': f'ROE 从 {float(prev["roe"]):.2f}% 降至 {float(latest["roe"]):.2f}%，股东回报效率下降。',
                    'rule': 'R04：最近一年ROE下降超过5个百分点触发预警。',
                    'basis': f'{prev_year}年ROE {float(prev["roe"]):.2f}%，{year}年ROE {float(latest["roe"]):.2f}%。',
                    'source': source,
                })
    debt = safe_num(latest.get('debt_ratio'))
    if debt is not None and debt > 70:
        alerts.append({
            'level': 'yellow',
            'title': '资产负债率偏高',
            'message': f'{year}年资产负债率为 {debt:.2f}%，财务杠杆处于较高水平。',
            'rule': 'R05：资产负债率高于70%触发偿债稳健性预警。',
            'basis': f'{year}年资产负债率 {debt:.2f}%。',
            'source': source,
        })
    audit = str(latest.get('audit_opinion') or '')
    if audit and audit != 'nan' and '标准' not in audit:
        alerts.append({
            'level': 'red',
            'title': '审计意见关注',
            'message': f'{year}年审计意见为“{audit}”，需要关注财务报表可信度。',
            'rule': 'R06：审计意见不是标准无保留意见时触发预警。',
            'basis': f'{year}年审计意见：{audit}。',
            'source': source,
        })
    if not alerts:
        alerts.append({
            'level': 'green',
            'title': '主要规则未触发',
            'message': '当前未触发系统内置的主要财务预警规则。',
            'rule': '系统已检查净利润、营收利润匹配、经营现金流、ROE、资产负债率和审计意见。',
            'basis': f'最新年度为{year}年，核心指标处于规则阈值以内。',
            'source': source,
        })
    return alerts


def calc_growth(df: pd.DataFrame, key: str) -> float | None:
    if df.empty or len(df) < 2 or key not in df.columns:
        return None
    df = df.sort_values('year')
    a = safe_num(df.iloc[-1].get(key))
    b = safe_num(df.iloc[-2].get(key))
    if a is None or b in (None, 0):
        return None
    return (a - b) / abs(b) * 100


def score_breakdown(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {'total': 0, 'dimensions': {}, 'basis': []}
    df = df.sort_values('year').reset_index(drop=True)
    latest = df.iloc[-1]
    source = get_source_for_metric(df, int(latest.get('year')))
    dims: dict[str, float] = {}
    basis: list[str] = []

    roe = safe_num(latest.get('roe'))
    net_margin_hint = None
    revenue = safe_num(latest.get('revenue'))
    net_profit = safe_num(latest.get('net_profit'))
    if revenue not in (None, 0) and net_profit is not None:
        net_margin_hint = net_profit / revenue * 100
    profitability = 12.0
    if roe is not None:
        profitability += min(max(roe / 25 * 14, 0), 14)
        basis.append(f'盈利能力：ROE为{roe:.2f}%。')
    if net_margin_hint is not None:
        profitability += min(max(net_margin_hint / 10 * 4, 0), 4)
        basis.append(f'净利率估算约{net_margin_hint:.2f}%。')
    dims['盈利能力'] = round(min(profitability, 30), 1)

    cfo = safe_num(latest.get('operating_cashflow'))
    cash_quality = 8.0
    if cfo is not None and net_profit not in (None, 0):
        ratio = cfo / abs(net_profit)
        cash_quality += min(max(ratio * 12, 0), 17)
        basis.append(f'现金流质量：经营现金流/归母净利润约为{ratio:.2f}。')
    elif cfo is not None and cfo > 0:
        cash_quality += 8
    dims['现金流质量'] = round(min(cash_quality, 25), 1)

    debt = safe_num(latest.get('debt_ratio'))
    solvency = 10.0
    if debt is not None:
        if debt <= 55:
            solvency += 15
        elif debt <= 65:
            solvency += 12
        elif debt <= 75:
            solvency += 7
        else:
            solvency += 3
        basis.append(f'偿债稳健性：资产负债率为{debt:.2f}%。')
    dims['偿债稳健性'] = round(min(solvency, 25), 1)

    rev_growth = calc_growth(df, 'revenue')
    profit_growth = calc_growth(df, 'net_profit')
    growth = 8.0
    if rev_growth is not None:
        growth += min(max((rev_growth + 5) / 25 * 7, 0), 7)
        basis.append(f'成长能力：营收同比变化{rev_growth:.2f}%。')
    if profit_growth is not None:
        growth += min(max((profit_growth + 10) / 40 * 5, 0), 5)
        basis.append(f'归母净利润同比变化{profit_growth:.2f}%。')
    dims['成长能力'] = round(min(growth, 20), 1)

    total = round(sum(dims.values()), 1)
    return {'total': total, 'dimensions': dims, 'basis': basis, 'source': source}


def score_company(df: pd.DataFrame) -> dict[str, Any]:
    s = score_breakdown(df)
    return {'score': s['total'], 'detail': s['dimensions'], 'basis': s.get('basis', []), 'source': s.get('source', '')}


def risk_dashboard(alerts: list[dict[str, str]]) -> dict[str, str]:
    levels = [a.get('level') for a in alerts]
    if 'red' in levels:
        overall = '高关注'
    elif 'orange' in levels:
        overall = '中高关注'
    elif 'yellow' in levels:
        overall = '中等关注'
    else:
        overall = '相对平稳'
    names = ''.join(a.get('title', '') for a in alerts)
    return {
        '综合风险': overall,
        '盈利能力': '需关注' if '净利润' in names or '增收不增利' in names or 'ROE' in names else '相对平稳',
        '现金流质量': '需重点关注' if '经营现金流' in names else '相对平稳',
        '偿债能力': '需关注' if '资产负债率' in names else '相对平稳',
    }


def build_summary(company_name: str, df: pd.DataFrame, alerts: list[dict[str, str]], profile: str = '平衡型') -> str:
    if df.empty:
        return f'{company_name} 暂无可用财务数据。'
    df = df.sort_values('year').reset_index(drop=True)
    latest = df.iloc[-1]
    year = int(latest['year'])
    parts = [f'{company_name} 最新可用年度为 {year} 年']
    for key in ['revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio']:
        if key in df.columns and pd.notna(latest.get(key)):
            parts.append(f'{METRIC_LABELS[key]} {fmt_value(key, latest.get(key))}')
    top_alert = alerts[0]['title'] if alerts else '暂无重点预警'
    profile_text = PROFILE_RULES.get(profile, PROFILE_RULES['平衡型'])
    return '，'.join(parts) + f'。当前重点关注：{top_alert}。按{profile}投资者视角，{profile_text}本结论仅供研究参考，不构成投资建议。'


def compare_companies(name_a: str, df_a: pd.DataFrame, name_b: str, df_b: pd.DataFrame, profile: str = '平衡型') -> dict[str, Any]:
    df_a = df_a.sort_values('year').reset_index(drop=True)
    df_b = df_b.sort_values('year').reset_index(drop=True)
    la = df_a.iloc[-1]
    lb = df_b.iloc[-1]
    score_a = score_company(df_a)
    score_b = score_company(df_b)
    recommended = name_a if score_a['score'] >= score_b['score'] else name_b
    profile_text = PROFILE_RULES.get(profile, PROFILE_RULES['平衡型'])
    reasoning = (
        f'按{profile}投资者视角，{profile_text}'
        f'系统从盈利能力、现金流质量、偿债稳健性与成长能力四个维度进行评分。'
        f'{name_a} 综合评分为 {score_a["score"]} 分，{name_b} 综合评分为 {score_b["score"]} 分，'
        f'当前研究辅助倾向为：{recommended}。该结论仅供研究参考，不构成投资建议。'
    )
    table = pd.DataFrame([
        {'指标': '营业收入(亿元)', name_a: la.get('revenue'), name_b: lb.get('revenue')},
        {'指标': '归母净利润(亿元)', name_a: la.get('net_profit'), name_b: lb.get('net_profit')},
        {'指标': '经营现金流(亿元)', name_a: la.get('operating_cashflow'), name_b: lb.get('operating_cashflow')},
        {'指标': 'ROE(%)', name_a: la.get('roe'), name_b: lb.get('roe')},
        {'指标': '资产负债率(%)', name_a: la.get('debt_ratio'), name_b: lb.get('debt_ratio')},
    ])
    dimension_rows = []
    for dim in ['盈利能力', '现金流质量', '偿债稳健性', '成长能力']:
        dimension_rows.append({'评分维度': dim, name_a: score_a['detail'].get(dim, 0), name_b: score_b['detail'].get(dim, 0)})
    dimension_rows.append({'评分维度': '综合得分', name_a: score_a['score'], name_b: score_b['score']})
    score_table = pd.DataFrame(dimension_rows)
    basis = {
        name_a: score_a.get('basis', []),
        name_b: score_b.get('basis', []),
    }
    return {
        'recommended': recommended,
        'reasoning': reasoning,
        'table': table,
        'score_table': score_table,
        'score_a': score_a,
        'score_b': score_b,
        'basis': basis,
    }


def metric_query(company_name: str, df: pd.DataFrame, metric_key: str, year: int | None = None) -> str:
    if df.empty:
        return f'{company_name} 暂无可用财务数据。'
    work = df.sort_values('year')
    if year is not None:
        one = work[work['year'] == year]
        if one.empty:
            years = '、'.join(str(int(x)) for x in work['year'].tolist())
            return f'系统当前没有检索到{company_name}{year}年的数据。当前可用年度包括：{years}。'
        row = one.iloc[-1]
    else:
        row = work.iloc[-1]
        year = int(row['year'])
    if metric_key not in row or pd.isna(row.get(metric_key)):
        return f'系统当前没有检索到{company_name}{year}年的{METRIC_LABELS.get(metric_key, metric_key)}数据。'
    source = get_source_for_metric(work, int(year))
    return f'{company_name}{int(year)}年{METRIC_LABELS.get(metric_key, metric_key)}为 {fmt_value(metric_key, row.get(metric_key))}。数据来源：{source}。'


def trend_text(company_name: str, df: pd.DataFrame, metric_key: str = 'revenue') -> str:
    if df.empty:
        return f'{company_name} 暂无可用趋势数据。'
    work = df.sort_values('year')
    points = []
    for _, row in work.iterrows():
        if metric_key in row and pd.notna(row.get(metric_key)):
            points.append(f'{int(row["year"])}年{fmt_value(metric_key, row.get(metric_key))}')
    growth = calc_growth(work, metric_key)
    growth_part = f'最近一年同比变化约 {growth:.2f}%。' if growth is not None else '当前数据不足以计算最近一年同比变化。'
    return f'{company_name}{METRIC_LABELS.get(metric_key, metric_key)}趋势：' + '，'.join(points) + f'。{growth_part}'


def explain_metric(metric_key: str) -> str:
    explanations = {
        'roe': 'ROE即净资产收益率，用于衡量公司利用股东权益创造利润的效率。ROE越高，通常说明股东回报能力越强，但也需要结合杠杆水平和利润质量判断。',
        'debt_ratio': '资产负债率用于衡量公司资产中由负债形成的比例。比例过高可能意味着偿债压力较大，但不同行业的合理区间不同。',
        'operating_cashflow': '经营现金流反映企业主营业务带来的现金流入流出净额，是观察利润含金量和主业造血能力的重要指标。',
        'gross_margin': '毛利率反映营业收入扣除营业成本后的盈利空间，常用于观察产品竞争力、成本压力和价格策略变化。',
        'net_profit': '归母净利润反映归属于上市公司股东的最终利润，是投资者判断盈利能力的重要指标。',
        'revenue': '营业收入反映企业经营规模和业务扩张情况，但收入增长还需要结合利润、现金流和费用变化判断质量。',
    }
    return explanations.get(metric_key, '该指标可作为财务分析的辅助指标，需要结合企业行业特征和其他财务数据综合判断。')


def _yoy_text(df: pd.DataFrame, key: str) -> str:
    if df.empty or key not in df.columns or len(df) < 2:
        return '数据不足，暂不计算同比变化。'
    work = df.sort_values('year').dropna(subset=[key])
    if len(work) < 2:
        return '数据不足，暂不计算同比变化。'
    latest = work.iloc[-1]
    prev = work.iloc[-2]
    a = safe_num(latest.get(key))
    b = safe_num(prev.get(key))
    if a is None or b in (None, 0):
        return '数据不足，暂不计算同比变化。'
    yoy = (a - b) / abs(b) * 100
    direction = '增长' if yoy >= 0 else '下降'
    return f'{int(latest["year"])}年较{int(prev["year"])}年{direction}{abs(yoy):.2f}%。'


def _data_range_text(df: pd.DataFrame) -> str:
    if df.empty or 'year' not in df.columns:
        return '暂无结构化年度指标。'
    years = [int(y) for y in sorted(df['year'].dropna().unique().tolist())]
    return '、'.join(str(y) for y in years) if years else '暂无结构化年度指标。'


def generate_report(company_name: str, df: pd.DataFrame, alerts: list[dict[str, str]], compare_payload: dict[str, Any] | None, profile: str = '平衡型') -> str:
    """Generate a richer competition-facing investor report.

    The text version is intentionally more complete than the dashboard summary:
    it includes data range, multi-year metric table, trend interpretation,
    rule-based risk explanation, score breakdown, investor-profile view and
    source traceability. The PDF renderer can then pair this text with charts.
    """
    summary = build_summary(company_name, df, alerts, profile)
    score = score_company(df)
    risk = risk_dashboard(alerts)
    source = get_source_for_metric(df) if not df.empty else '暂无来源记录'
    years_text = _data_range_text(df)

    lines = [
        f'{company_name}投资者分析报告',
        '',
        '一、报告摘要',
        summary,
        f'本次分析覆盖年度：{years_text}。系统从盈利能力、现金流质量、偿债稳健性和成长能力四个维度形成综合判断。',
        f'综合评分：{score["score"]}分；综合风险状态：{risk.get("综合风险", "-")}。',
        '',
        '二、核心财务数据概览',
    ]

    if not df.empty:
        work = df.sort_values('year')
        for _, row in work.iterrows():
            year = int(row['year'])
            vals = []
            for key in ['revenue', 'net_profit', 'operating_cashflow', 'roe', 'debt_ratio']:
                if key in row and pd.notna(row.get(key)):
                    vals.append(f'{METRIC_LABELS[key]} {fmt_value(key, row.get(key))}')
            if vals:
                lines.append(f'- {year}年：' + '；'.join(vals))
        latest = work.iloc[-1]
        lines += [
            '',
            '三、趋势解读',
            f'- 营业收入趋势：{trend_text(company_name, work, "revenue")} {_yoy_text(work, "revenue")}',
            f'- 归母净利润趋势：{trend_text(company_name, work, "net_profit")} {_yoy_text(work, "net_profit")}',
            f'- 经营现金流趋势：{trend_text(company_name, work, "operating_cashflow")} {_yoy_text(work, "operating_cashflow")}',
            f'- 股东回报观察：最新年度ROE为{fmt_value("roe", latest.get("roe"))}，用于观察权益资本回报效率。',
            f'- 财务稳健观察：最新年度资产负债率为{fmt_value("debt_ratio", latest.get("debt_ratio"))}，用于衡量财务杠杆水平。',
        ]
    else:
        lines.append('当前企业暂无可用于建模的结构化年度指标；可继续导入Excel/CSV或公开披露PDF。')

    lines += ['', '四、风险预警与规则依据']
    for alert in alerts:
        lines.append(f'- {alert["title"]}：{alert["message"]}')
        lines.append(f'  规则依据：{alert.get("rule", "-")}')
        lines.append(f'  数据依据：{alert.get("basis", "-")}')

    lines += ['', '五、四维评分解释']
    lines.append(f'综合评分：{score["score"]}分。评分用于辅助横向比较，不构成投资建议。')
    for dim, val in score['detail'].items():
        lines.append(f'- {dim}：{val}分')
    for b in score.get('basis', [])[:6]:
        lines.append(f'  评分依据：{b}')

    lines += ['', '六、风险仪表盘']
    for k, v in risk.items():
        lines.append(f'- {k}：{v}')

    profile_text = PROFILE_RULES.get(profile, PROFILE_RULES['平衡型'])
    lines += [
        '',
        '七、投资者画像匹配',
        f'当前选择画像：{profile}。{profile_text}',
        '系统建议投资者重点结合利润趋势、现金流含金量、资产负债率和ROE变化进行综合判断，避免只看单一指标。',
    ]

    if compare_payload:
        lines += ['', '八、企业对比参考']
        lines.append(compare_payload['reasoning'])

    lines += [
        '',
        '九、数据来源与可追溯性',
        f'主要结构化数据来源：{source}。系统保留已导入的结构化财务表、公开披露PDF及在线检索记录，用于回溯分析依据。',
        '',
        '十、研究结论',
        '本报告基于系统已接入的结构化财务数据、公开来源材料和规则引擎自动生成，适用于投资研究辅助、企业横向比较和答辩展示。报告仅供学习研究与辅助分析，不构成任何形式的投资建议或买卖指令。',
    ]
    return '\n'.join(lines)
