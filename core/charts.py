from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from core.analysis import METRIC_LABELS, safe_num


def _clean_metric_df(df: pd.DataFrame, metrics: list[str]) -> pd.DataFrame:
    if df.empty or 'year' not in df.columns:
        return pd.DataFrame()
    available = [m for m in metrics if m in df.columns]
    if not available:
        return pd.DataFrame()
    work = df[['year'] + available].copy()
    work['year'] = pd.to_numeric(work['year'], errors='coerce')
    for m in available:
        work[m] = pd.to_numeric(work[m], errors='coerce')
    work = work.dropna(subset=['year'])
    return work


def make_metric_chart(df: pd.DataFrame, chart_type: str, metrics: list[str], title: str = '财务指标图表'):
    """Return a Plotly figure for line/bar/pie charts.

    Radar charts are handled in app/main.py because they depend on the existing
    four-dimensional scoring model.
    """
    work = _clean_metric_df(df, metrics)
    if work.empty:
        return None
    available = [m for m in metrics if m in work.columns and work[m].notna().any()]
    if not available:
        return None

    if chart_type == '饼图':
        latest = work.sort_values('year').iloc[-1]
        rows = []
        for m in available:
            value = safe_num(latest.get(m))
            if value is not None and value > 0:
                rows.append({'指标': METRIC_LABELS.get(m, m), '数值': value})
        if not rows:
            return None
        pie_df = pd.DataFrame(rows)
        return px.pie(pie_df, names='指标', values='数值', title=title + '（最新年度结构）')

    long_df = work.melt(id_vars='year', value_vars=available, var_name='指标', value_name='数值')
    long_df = long_df.dropna(subset=['数值'])
    if long_df.empty:
        return None
    long_df['指标'] = long_df['指标'].map(lambda x: METRIC_LABELS.get(x, x))
    if chart_type == '柱形图':
        return px.bar(long_df, x='year', y='数值', color='指标', barmode='group', title=title)
    return px.line(long_df, x='year', y='数值', color='指标', markers=True, title=title)
