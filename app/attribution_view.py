"""Present a frozen attribution result; never query or recalculate financial facts."""
from __future__ import annotations

from html import escape
from math import isfinite

import streamlit as st

from app.source_viewer import render_source
from app.messages import public_message


DISCLAIMER = '以下为基于财务指标变化与报告证据形成的归因线索，用于辅助分析，不代表严格因果贡献率。'
NO_EVIDENCE = '当前仅形成量化归因线索，未检索到足够的同年度报告解释证据。'
EVIDENCE_LABELS = {
    'quantitative_and_document': '量化数据 + 同年报告依据',
    'quantitative_only': '仅量化线索 · 无充分报告依据',
    'insufficient': '数据不足 · 暂不形成线索',
}


def _text(value) -> str:
    return escape(public_message(value, fallback=''), quote=True)


def _number(value, *, signed: bool = False) -> str:
    if isinstance(value, bool):
        return '缺失'
    try:
        number = float(value)
    except (ValueError, TypeError):
        return '缺失'
    if not isfinite(number):
        return '缺失'
    # Display formatting only: all comparisons and growth calculations belong
    # to the validated, saved AttributionResult supplied by the QA engine.
    return f'{number:+,.2f}' if signed else f'{number:,.2f}'


def _growth_markup(node: dict) -> str:
    if node.get('metric') == 'gross_margin':
        return ''
    label = node.get('growth_label') or '年度同比'
    rate = node.get('growth_rate')
    if _number(rate) == '缺失':
        return f'<div class="fu-attribution-growth">{_text(label if node.get("growth_label") else "变动比例不适用")}</div>'
    return f'<div class="fu-attribution-growth">{_text(label)} <strong>{_number(rate, signed=True)}%</strong></div>'


def _node_markup(node: dict, previous_year, current_year, *, root: bool = False) -> str:
    auxiliary = node.get('role') == 'supporting_signal'
    classes = 'fu-attribution-node'
    classes += ' fu-attribution-root' if root else ' fu-attribution-driver'
    if auxiliary:
        classes += ' fu-attribution-auxiliary'
    status = node.get('evidence_status')
    status_label = EVIDENCE_LABELS.get(status, EVIDENCE_LABELS['insufficient'])
    unit = node.get('unit') or ('%' if node.get('metric') == 'gross_margin' else '亿元')
    delta_unit = node.get('delta_unit') or ('个百分点' if node.get('metric') == 'gross_margin' else unit)
    role = '观察结果' if root else ('经营质量辅助线索' if auxiliary else '量化变化线索')
    direction = {'up': '上升', 'down': '下降', 'flat': '持平'}.get(node.get('direction'), '待确认')
    values = (
        '<div class="fu-attribution-values">'
        f'<div><span class="fu-attribution-year">{_text(previous_year)} 年</span>'
        f'<strong>{_number(node.get("previous_value"))}</strong><span class="fu-attribution-unit">{_text(unit)}</span></div>'
        '<span class="fu-attribution-arrow" aria-label="变化至">→</span>'
        f'<div><span class="fu-attribution-year">{_text(current_year)} 年</span>'
        f'<strong>{_number(node.get("current_value"))}</strong><span class="fu-attribution-unit">{_text(unit)}</span></div>'
        '</div>'
    )
    if status == 'insufficient':
        detail = f'<div class="fu-attribution-missing">{_text(node.get("reason") or "缺少连续两个年度的完整指标，暂不计算变化。")}</div>'
    else:
        detail = (
            f'<div class="fu-attribution-change">{direction} · 变化 '
            f'<strong>{_number(node.get("delta"), signed=True)} {_text(delta_unit)}</strong></div>'
            + _growth_markup(node)
        )
    interpretation = node.get('interpretation') or ''
    return (
        f'<section class="{classes}" aria-label="{_text(node.get("label") or "财务指标")}">'
        f'<div class="fu-attribution-role">{role}</div>'
        f'<div class="fu-attribution-title">{_text(node.get("label") or "财务指标")}</div>'
        f'{values}{detail}'
        + (f'<div class="fu-attribution-interpretation">{_text(interpretation)}</div>' if interpretation else '')
        + f'<div class="fu-attribution-status">{status_label}</div></section>'
    )


def attribution_markup(attribution: dict) -> str:
    """Build an escaped, responsive hierarchy from the answer's saved values."""
    root = attribution.get('root') or {}
    years = (attribution.get('previous_year'), attribution.get('current_year'))
    nodes = [node for node in attribution.get('drivers', []) if isinstance(node, dict)][:3]
    return (
        '<div class="fu-attribution-tree">'
        '<div class="fu-attribution-context">'
        f'<span>{_text(attribution.get("company"))}</span><span>年度口径 · 量化变化与报告线索</span></div>'
        + _node_markup(root, *years, root=True)
        + '<div class="fu-attribution-branch-label">分项线索与辅助观察</div>'
        + '<div class="fu-attribution-drivers">'
        + ''.join(_node_markup(node, *years) for node in nodes)
        + '</div></div>'
    )


def _render_evidence(node: dict, *, key: str):
    citations = node.get('evidence') or []
    quantitative_sources = node.get('quantitative_sources') or []
    if not citations and not quantitative_sources:
        return
    with st.expander(f'查看数据与报告依据 · {node.get("label") or "财务指标"}', expanded=False):
        if quantitative_sources:
            st.caption('量化数据来源')
        for index, entry in enumerate(quantitative_sources):
            source = entry.get('metric_source') if isinstance(entry, dict) else None
            if not isinstance(source, dict):
                year = entry.get('year', '—') if isinstance(entry, dict) else '—'
                st.caption(f'{year} 年：历史行级记录，未核验该指标独立出处。')
                continue
            st.text(f'{entry.get("year", "—")} 年 · {source.get("file_name") or "指标来源"}'
                    f' · 原始单位：{source.get("raw_unit") or "未记录"}'
                    f' · 导入版本：{source.get("import_id") or "未记录"}')
            try:
                page = int(source.get('page') or 0)
            except (TypeError, ValueError):
                page = 0
            if source.get('cell'):
                st.text(f'对应单元格：{source["cell"]}')
            if page > 0:
                st.text(f'PDF 第 {page} 页')
            is_pdf = str(source.get('file_path') or source.get('file_name') or '').lower().endswith('.pdf')
            if source.get('file_path') and (not is_pdf or page > 0):
                render_source(source, f'{key}_number_{index}', default_page=page if page > 0 else 1)
            elif is_pdf:
                st.caption('未记录该指标在 PDF 中的页码，暂不提供定位入口。')
        if citations:
            st.caption('报告解释证据')
        for index, citation in enumerate(citations):
            if not isinstance(citation, dict):
                continue
            try:
                page = int(citation.get('page'))
            except (TypeError, ValueError):
                page = 0
            location = f'PDF 第 {page} 页' if page > 0 else '未提供可定位页码'
            # These fields originate in uploaded documents; native text output
            # avoids treating file names and snippets as Markdown or HTML.
            st.text(f'{citation.get("file_name") or "报告原文"} · {citation.get("report_year") or "—"} 年 · {location}')
            st.text(citation.get('snippet') or '未提供原文片段。')
            if page > 0:
                render_source(citation, f'{key}_{index}', default_page=page)


def _render_checks(attribution: dict):
    queries = attribution.get('sql_trace') or []
    documents = attribution.get('document_checks') or []
    if not queries and not documents:
        return
    with st.expander('归因查询与证据核验', expanded=False):
        if queries:
            st.caption('实际执行的只读查询')
            st.dataframe([{
                '查询': row.get('label', ''),
                '年度': '、'.join(str(year) for year in row.get('years', [])),
                '指标': '、'.join(str(metric) for metric in row.get('metrics', [])),
                '状态': row.get('status', ''),
                '耗时 ms': row.get('duration_ms'),
                '尝试次数': len(row['attempts']) if isinstance(row.get('attempts'), list) else row.get('attempts'),
                '说明': public_message(row.get('reason'), fallback=''),
            } for row in queries if isinstance(row, dict)], hide_index=True, width='stretch')
        if documents:
            st.caption('同企业、同年度报告证据检查')
            st.dataframe([{
                '指标': row.get('metric', ''),
                '报告年度': '、'.join(str(year) for year in row.get('years', [])),
                '状态': row.get('status', ''),
                '采纳引用': len(row['accepted_citations']) if isinstance(row.get('accepted_citations'), list) else row.get('accepted_citations'),
                '拒绝引用': len(row['rejected_citations']) if isinstance(row.get('rejected_citations'), list) else row.get('rejected_citations'),
                '拒绝原因': '；'.join(public_message(item.get('reason'), fallback='') for item in row.get('rejected_citations', []) if isinstance(item, dict)),
                '耗时 ms': row.get('duration_ms'),
                '说明': public_message(row.get('retrieval_reason') or row.get('reason'), fallback=''),
            } for row in documents if isinstance(row, dict)], hide_index=True, width='stretch')


def render_attribution(attribution: dict | None, key: str) -> None:
    if not isinstance(attribution, dict):
        return
    with st.expander('数因融合财务归因', expanded=True):
        if attribution.get('scope_note'):
            st.caption(public_message(attribution['scope_note']))
        if attribution.get('status') != 'success' or not attribution.get('root'):
            st.info(public_message(attribution.get('reason'), fallback='归因分析未完成，请核验数据和报告来源后重试。'))
            _render_checks(attribution)
            st.caption(DISCLAIMER)
            return
        st.markdown(attribution_markup(attribution), unsafe_allow_html=True)
        nodes = [attribution['root']] + list(attribution.get('drivers') or [])
        if not any(node.get('evidence') for node in nodes if isinstance(node, dict)):
            st.caption(NO_EVIDENCE)
        for index, node in enumerate(nodes):
            if isinstance(node, dict):
                _render_evidence(node, key=f'{key}_{index}')
        _render_checks(attribution)
        st.caption(DISCLAIMER)
