from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.analysis import METRIC_LABELS, METRIC_UNITS, clean_financial_df, safe_num


def _clean_metric_df(df: pd.DataFrame, metrics: list[str]) -> pd.DataFrame:
    if df.empty or 'year' not in df.columns:
        return pd.DataFrame()
    available = [m for m in dict.fromkeys(metrics) if m in df.columns]
    if not available:
        return pd.DataFrame()
    work = clean_financial_df(df[['year'] + available])
    if work.empty:
        return work
    for metric in available:
        work[metric] = work[metric].map(safe_num)
    # Insert missing years as gaps, never as zeroes or a bridge over a gap.
    work = work.drop_duplicates('year', keep='last').set_index('year')
    return work.reindex(range(int(work.index.min()), int(work.index.max()) + 1)).rename_axis('year').reset_index()


def make_metric_chart(df: pd.DataFrame, chart_type: str, metrics: list[str], title: str = '财务指标图表'):
    """One Figure with independent subplot axes for each unit.

    Revenue, profit and cash flow overlap rather than partition a whole, so a
    legacy pie request renders bars and explains the correction.
    """
    work = _clean_metric_df(df, metrics)
    if work.empty:
        return None
    available = [m for m in dict.fromkeys(metrics) if m in work.columns and work[m].notna().any()]
    if not available:
        return None

    units: dict[str, list[str]] = {}
    for metric in available:
        units.setdefault(METRIC_UNITS.get(metric, '未标明单位'), []).append(metric)
    is_bar = chart_type in {'柱形图', '饼图'}
    corrected_pie = chart_type == '饼图'
    fig = make_subplots(rows=len(units), cols=1, shared_xaxes=False,
                        vertical_spacing=0.12 if len(units) > 1 else 0.0,
                        subplot_titles=[f'{" / ".join(METRIC_LABELS.get(m, m) for m in ms)}（{unit}）'
                                        for unit, ms in units.items()])
    palette = ['#315ce3', '#12a6a6', '#e7a33e', '#7c5ccb', '#df6276', '#579cc5', '#7aa15b']
    has_missing = work[available].isna().any().any()
    for row, (unit, keys) in enumerate(units.items(), 1):
        for metric in keys:
            values = [None if pd.isna(value) else float(value) for value in work[metric]]
            common = dict(x=[int(y) for y in work['year']], y=values,
                          name=f'{METRIC_LABELS.get(metric, metric)}（{unit}）',
                          marker_color=palette[available.index(metric) % len(palette)],
                          hovertemplate=f'%{{x:.0f}}年<br>{METRIC_LABELS.get(metric, metric)}：%{{y:.2f}} {unit}<extra></extra>')
            trace = go.Bar(**common) if is_bar else go.Scatter(**common, mode='lines+markers', connectgaps=False)
            fig.add_trace(trace, row=row, col=1)
        fig.update_yaxes(title_text=unit, row=row, col=1)
        fig.update_xaxes(title_text='年度', tickmode='array', tickvals=[int(y) for y in work['year']],
                         ticktext=[str(int(y)) for y in work['year']], tickformat='d', row=row, col=1)
    notes = []
    if corrected_pie:
        notes.append('这些指标不构成整体，已改用按单位分组的柱形图。')
    if has_missing:
        notes.append('缺失年度或指标保留为空，不补零；折线断开表示数据缺失。')
    if notes:
        fig.add_annotation(x=0, y=-0.18 / len(units), xref='paper', yref='paper',
                           text='<br>'.join(notes), showarrow=False, xanchor='left', font_size=11)
    fig.update_layout(title=title, height=310 * len(units) + (65 if notes else 20),
                      barmode='group', template='plotly_white', hovermode='x unified',
                      margin=dict(t=80, b=90 if notes else 55, l=65, r=25),
                      legend=dict(orientation='h', y=1.16, x=0))
    return fig
