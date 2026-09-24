from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any
import math

import pandas as pd
from app.branding import PAGE_TITLE, REPORT_SUBTITLE
from app.messages import NO_METRICS, NO_SOURCE, public_message
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.graphics.shapes import Drawing, Line, Rect, String, Circle, Polygon

_FONT_NAME = 'STSong-Light'
# SimpleDocTemplate's frame also reserves 6 pt of padding on each side.
# Tables and drawings share the actual available frame width, not page width.
_SIDE_MARGIN = 1.8 * cm
_CONTENT_WIDTH = A4[0] - 2 * _SIDE_MARGIN - 12
REPORT_LAYOUT_VERSION = '2026-09-24-branding-wrapped-tables-v3'
try:
    pdfmetrics.registerFont(UnicodeCIDFont(_FONT_NAME))
except Exception:
    pass

_METRIC_LABELS = {
    'revenue': '营业收入',
    'net_profit': '归母净利润',
    'operating_cashflow': '经营现金流',
    'roe': 'ROE',
    'debt_ratio': '资产负债率',
    'gross_margin': '毛利率',
    'eps': '每股收益',
}
_METRIC_UNITS = {
    'revenue': '亿元',
    'net_profit': '亿元',
    'operating_cashflow': '亿元',
    'roe': '%',
    'debt_ratio': '%',
    'gross_margin': '%',
    'eps': '元/股',
}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    styles = {
        'title': ParagraphStyle('CNTitle', parent=base['Title'], fontName=_FONT_NAME, fontSize=20, leading=28, alignment=TA_CENTER, spaceAfter=10),
        'subtitle': ParagraphStyle('CNSubtitle', parent=base['Normal'], fontName=_FONT_NAME, fontSize=9, leading=14, textColor=colors.HexColor('#666666'), alignment=TA_CENTER, spaceAfter=10),
        'h1': ParagraphStyle('CNHeading1', parent=base['Heading1'], fontName=_FONT_NAME, fontSize=14, leading=20, textColor=colors.HexColor('#16324F'), spaceBefore=8, spaceAfter=5),
        'body': ParagraphStyle('CNBody', parent=base['BodyText'], fontName=_FONT_NAME, fontSize=10.2, leading=16, alignment=TA_LEFT, spaceAfter=3),
        'body_big': ParagraphStyle('CNBodyBig', parent=base['BodyText'], fontName=_FONT_NAME, fontSize=11, leading=18, alignment=TA_LEFT, spaceAfter=5),
        'small': ParagraphStyle('CNSmall', parent=base['BodyText'], fontName=_FONT_NAME, fontSize=8.5, leading=13, textColor=colors.HexColor('#666666'), spaceAfter=3),
        'card': ParagraphStyle('CNCard', parent=base['BodyText'], fontName=_FONT_NAME, fontSize=9.5, leading=14, textColor=colors.HexColor('#16324F'), alignment=TA_CENTER),
        'cover_title': ParagraphStyle('CoverTitle', parent=base['Title'], fontName=_FONT_NAME, fontSize=24, leading=34, textColor=colors.HexColor('#102A43'), alignment=TA_CENTER, spaceAfter=12),
        'cover_subtitle': ParagraphStyle('CoverSubtitle', parent=base['Normal'], fontName=_FONT_NAME, fontSize=11, leading=18, textColor=colors.HexColor('#486581'), alignment=TA_CENTER, spaceAfter=8),
    }
    for style in styles.values():
        style.wordWrap = 'CJK'
        style.splitLongWords = True
        # ReportLab's CJK breaker deliberately hangs one closing punctuation
        # glyph outside its line box. Keep that glyph inside our frame/cell.
        style.rightIndent = style.fontSize
        if style.alignment == TA_CENTER:
            style.leftIndent = style.fontSize
        style.allowWidows = 0
        style.allowOrphans = 0
    return styles


def _text_table(data: list[list[Any]], weights: list[float], *, header: bool = False,
                centered: bool = False, font_size: float = 9.2,
                width: float = _CONTENT_WIDTH) -> Table:
    """Use wrapped flowables, including numeric cells and long source paths.

    A plain string cell in ReportLab does not wrap to colWidths. Paragraphs
    measure their real height, and splitInRow allows even a single long note
    to continue on the following page instead of escaping the printable area.
    """
    width = min(float(width), _CONTENT_WIDTH)
    style = ParagraphStyle('TableCell', parent=_styles()['body'], fontSize=font_size,
                           leading=font_size * 1.5, spaceAfter=0, spaceBefore=0,
                           rightIndent=font_size, leftIndent=font_size if centered else 0,
                           alignment=TA_CENTER if centered else TA_LEFT)
    heading = ParagraphStyle('TableHeader', parent=style, textColor=colors.white)
    cells = [[_para(str(cell), heading if header and index == 0 else style) for cell in row]
             for index, row in enumerate(data)]
    return Table(cells, colWidths=[width * weight / sum(weights) for weight in weights],
                 repeatRows=1 if header else 0, splitByRow=1, splitInRow=1, hAlign='LEFT')


def _num(v: Any) -> float | None:
    try:
        if v is None or pd.isna(v):
            return None
        value = float(v)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _fmt(key: str, v: Any) -> str:
    val = _num(v)
    if val is None:
        return '-'
    if key == 'eps':
        return f'{val:.2f}{_METRIC_UNITS.get(key, "")}'
    return f'{val:.2f}{_METRIC_UNITS.get(key, "")}'


def _split_report_lines(report_text: str) -> list[str]:
    return [raw.strip() for raw in (report_text or '').splitlines() if raw.strip()]


def _is_section_title(line: str) -> bool:
    return line.startswith(('一、', '二、', '三、', '四、', '五、', '六、', '七、', '八、', '九、', '十、'))


def _metric_snapshot_table(df: pd.DataFrame) -> Table | None:
    if df is None or df.empty or 'year' not in df.columns:
        return None
    work = df.sort_values('year')
    headers = ['年度', '营业收入', '归母净利润', '经营现金流', 'ROE', '资产负债率']
    data = [headers]
    for _, row in work.iterrows():
        data.append([
            str(int(row.get('year'))),
            _fmt('revenue', row.get('revenue')),
            _fmt('net_profit', row.get('net_profit')),
            _fmt('operating_cashflow', row.get('operating_cashflow')),
            _fmt('roe', row.get('roe')),
            _fmt('debt_ratio', row.get('debt_ratio')),
        ])
    table = _text_table(data, [1.6, 2.7, 2.7, 2.9, 2.0, 2.4], header=True, centered=True, font_size=8.5)
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), _FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#16324F')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F6F8FB')),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#D6DEE8')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def _kpi_cards(df: pd.DataFrame, score: dict[str, Any] | None = None) -> Table | None:
    if df is None or df.empty or 'year' not in df.columns:
        return None
    row = df.sort_values('year').iloc[-1]
    cards = [
        ['最新年度', str(int(row.get('year')))],
        ['营业收入', _fmt('revenue', row.get('revenue'))],
        ['归母净利润', _fmt('net_profit', row.get('net_profit'))],
        ['经营现金流', _fmt('operating_cashflow', row.get('operating_cashflow'))],
        ['ROE', _fmt('roe', row.get('roe'))],
        ['综合评分', f"{score['score']}分" if _has_complete_score(score) else '数据不足，暂不评分'],
    ]
    data = []
    for i in range(0, len(cards), 3):
        data.append([f'{cards[i][0]}\n{cards[i][1]}', f'{cards[i+1][0]}\n{cards[i+1][1]}', f'{cards[i+2][0]}\n{cards[i+2][1]}'])
    table = _text_table(data, [1, 1, 1], centered=True, font_size=10)
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), _FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#16324F')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#EAF2F8')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#BFC9D4')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    return table


def _trend_drawing(df: pd.DataFrame) -> Drawing | None:
    if df is None or df.empty or 'year' not in df.columns:
        return None
    metrics = ['revenue', 'net_profit', 'operating_cashflow']
    work = df.sort_values('year')
    years = [int(y) for y in work['year'].tolist()]
    if len(years) < 2:
        return None
    series = []
    palette = [colors.HexColor('#1F77B4'), colors.HexColor('#D62728'), colors.HexColor('#2CA02C')]
    for m, c in zip(metrics, palette):
        vals = [_num(v) for v in work[m].tolist()] if m in work.columns else []
        pts = [(y, v) for y, v in zip(years, vals) if v is not None]
        if len(pts) >= 2 and pts[0][1] > 0:
            base = abs(pts[0][1]) or 1
            series.append((m, [(y, v / base * 100) for y, v in pts], c))
    if not series:
        return None
    W, H = 470, 170
    left, bottom, right, top = 42, 34, 18, 24
    d = Drawing(W, H)
    d.add(String(6, H-12, '核心指标趋势图（首年=100）', fontName=_FONT_NAME, fontSize=10, fillColor=colors.HexColor('#16324F')))
    d.add(Line(left, bottom, W-right, bottom, strokeColor=colors.HexColor('#C8D0DA')))
    d.add(Line(left, bottom, left, H-top, strokeColor=colors.HexColor('#C8D0DA')))
    all_vals = [v for _, pts, _ in series for _, v in pts]
    minv, maxv = min(all_vals), max(all_vals)
    if minv == maxv:
        minv, maxv = minv-10, maxv+10
    pad = (maxv-minv)*0.15
    minv, maxv = minv-pad, maxv+pad
    min_year, max_year = min(years), max(years)
    def xmap(y):
        return left + (y-min_year)/(max_year-min_year or 1)*(W-left-right)
    def ymap(v):
        return bottom + (v-minv)/(maxv-minv or 1)*(H-bottom-top)
    for y in years:
        x = xmap(y)
        d.add(String(x-10, bottom-18, str(y), fontName=_FONT_NAME, fontSize=7, fillColor=colors.HexColor('#555555')))
        d.add(Line(x, bottom-2, x, bottom+2, strokeColor=colors.HexColor('#C8D0DA')))
    legend_x = left
    for m, pts, c in series:
        for (y1, v1), (y2, national) in zip(pts[:-1], pts[1:]):
            if y2 - y1 == 1:
                d.add(Line(xmap(y1), ymap(v1), xmap(y2), ymap(national), strokeColor=c, strokeWidth=1.8))
        for y, v in pts:
            d.add(Circle(xmap(y), ymap(v), 2.5, fillColor=c, strokeColor=c))
        d.add(Rect(legend_x, H-28, 8, 8, fillColor=c, strokeColor=c))
        d.add(String(legend_x+11, H-27, _METRIC_LABELS[m], fontName=_FONT_NAME, fontSize=7.5, fillColor=colors.HexColor('#333333')))
        legend_x += 90
    return d


def _has_complete_score(score: dict[str, Any] | None) -> bool:
    if not score or score.get('available') is False or _num(score.get('score')) is None:
        return False
    detail = score.get('detail') or {}
    return all(_num(detail.get(key)) is not None for key in ['盈利能力', '现金流质量', '偿债稳健性', '成长能力'])


def _radar_drawing(score: dict[str, Any] | None) -> Drawing | None:
    detail = (score or {}).get('detail') or {}
    if not _has_complete_score(score):
        return None
    max_map = {'盈利能力': 30, '现金流质量': 25, '偿债稳健性': 25, '成长能力': 20}
    dims = list(max_map.keys())
    vals = [max(0, min(float(detail.get(k, 0))/max_map[k], 1)) for k in dims]
    W, H = 230, 190
    cx, cy, r = 115, 88, 58
    import math
    d = Drawing(W, H)
    d.add(String(6, H-12, '四维能力雷达图', fontName=_FONT_NAME, fontSize=10, fillColor=colors.HexColor('#16324F')))
    axes = []
    for i, dim in enumerate(dims):
        ang = math.pi/2 + i*2*math.pi/len(dims)
        x, y = cx + math.cos(ang)*r, cy + math.sin(ang)*r
        axes.append((x, y, ang))
        d.add(Line(cx, cy, x, y, strokeColor=colors.HexColor('#C8D0DA')))
        if abs(x - cx) > r / 2:
            label_x, label_y = x + (6 if x > cx else -6), y - 2
            anchor = 'start' if x > cx else 'end'
        else:
            label_x, label_y, anchor = x, y + (6 if y > cy else -12), 'middle'
        d.add(String(label_x, label_y, dim, textAnchor=anchor, fontName=_FONT_NAME, fontSize=7.2, fillColor=colors.HexColor('#333333')))
    for rr in [0.33, 0.66, 1.0]:
        pts=[]
        for _, _, ang in axes:
            pts += [cx+math.cos(ang)*r*rr, cy+math.sin(ang)*r*rr]
        d.add(Polygon(pts, strokeColor=colors.HexColor('#D6DEE8'), fillColor=None, strokeWidth=0.4))
    pts=[]
    for v, (_, _, ang) in zip(vals, axes):
        pts += [cx+math.cos(ang)*r*v, cy+math.sin(ang)*r*v]
    d.add(Polygon(pts, strokeColor=colors.HexColor('#D62728'), fillColor=colors.Color(0.84,0.15,0.16, alpha=0.18), strokeWidth=1.3))
    return d



def _latest_year(df: pd.DataFrame | None) -> str:
    if df is None or df.empty or 'year' not in df.columns:
        return '暂无'
    try:
        return str(int(df.sort_values('year').iloc[-1].get('year')))
    except Exception:
        return '暂无'


def _year_range(df: pd.DataFrame | None) -> str:
    if df is None or df.empty or 'year' not in df.columns:
        return '暂无结构化年度指标'
    years = [int(y) for y in sorted(df['year'].dropna().unique().tolist())]
    if not years:
        return '暂无结构化年度指标'
    return f'{years[0]}-{years[-1]}' if len(years) > 1 else str(years[0])


def _extract_section(report_text: str, start_title: str, end_titles: tuple[str, ...] = ()) -> list[str]:
    lines = _split_report_lines(report_text)
    out, active = [], False
    for line in lines:
        if line.startswith(start_title):
            active = True
            continue
        if active and end_titles and any(line.startswith(t) for t in end_titles):
            break
        if active:
            out.append(line)
    return out


def _para(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_escape(text), style)


def _bullet(text: str, style: ParagraphStyle) -> Paragraph:
    t = text[2:] if text.startswith('- ') else text
    return Paragraph('• ' + _escape(t), style)


def _info_table(rows: list[tuple[str, str]], widths: tuple[float, float] = (4.0, 11.6)) -> Table:
    data = [[k, v] for k, v in rows]
    table = _text_table(data, list(widths))
    table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), _FONT_NAME), ('FONTSIZE', (0,0), (-1,-1), 9.2),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#EAF2F8')),
        ('TEXTCOLOR', (0,0), (0,-1), colors.HexColor('#16324F')),
        ('BACKGROUND', (1,0), (1,-1), colors.HexColor('#FFFFFF')),
        ('GRID', (0,0), (-1,-1), 0.25, colors.HexColor('#D6DEE8')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'), ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6), ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    return table


def _score_table(score: dict[str, Any] | None) -> Table | None:
    detail = (score or {}).get('detail') or {}
    if not _has_complete_score(score):
        return None
    data = [['维度', '得分', '解读口径']]
    explain = {
        '盈利能力': '观察利润规模、ROE与股东回报效率。',
        '现金流质量': '观察经营现金流对利润与经营规模的支撑。',
        '偿债稳健性': '观察资产负债率和财务杠杆压力。',
        '成长能力': '观察收入与利润的阶段性增长表现。',
    }
    for k, v in detail.items():
        data.append([k, str(v), explain.get(k, '基于结构化年度指标自动测算。')])
    table = _text_table(data, [3.4, 2.2, 9.6], header=True, font_size=9)
    table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), _FONT_NAME), ('FONTSIZE', (0,0), (-1,-1), 9),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#16324F')), ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#F6F8FB')), ('GRID', (0,0), (-1,-1), 0.25, colors.HexColor('#D6DEE8')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'), ('ALIGN', (1,1), (1,-1), 'CENTER'),
        ('LEFTPADDING', (0,0), (-1,-1), 6), ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    return table


def _mini_bar_drawing(score: dict[str, Any] | None, width: float = 470) -> Drawing | None:
    detail = (score or {}).get('detail') or {}
    if not _has_complete_score(score):
        return None
    max_map = {'盈利能力': 30, '现金流质量': 25, '偿债稳健性': 25, '成长能力': 20}
    W, H = min(width, _CONTENT_WIDTH), 130
    bar_x = 78 if W >= 360 else 58
    bar_width = W - bar_x - 64
    value_x = W - 50
    d = Drawing(W, H)
    d.add(String(6, H-12, '评分维度条形图', fontName=_FONT_NAME, fontSize=10, fillColor=colors.HexColor('#16324F')))
    y = H - 34
    for dim, maxv in max_map.items():
        val = max(0, min(float(detail.get(dim, 0)), maxv))
        ratio = val / maxv if maxv else 0
        d.add(String(6, y+2, dim, fontName=_FONT_NAME, fontSize=8, fillColor=colors.HexColor('#333333')))
        d.add(Rect(bar_x, y, bar_width, 10, fillColor=colors.HexColor('#EDF2F7'), strokeColor=colors.HexColor('#CBD5E0')))
        d.add(Rect(bar_x, y, bar_width*ratio, 10, fillColor=colors.HexColor('#2B6CB0'), strokeColor=colors.HexColor('#2B6CB0')))
        d.add(String(value_x, y+1, f'{val:.0f}/{maxv}', fontName=_FONT_NAME, fontSize=8, fillColor=colors.HexColor('#333333')))
        y -= 24
    return d

def _escape(text: str) -> str:
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\r\n', '\n').replace('\n', '<br/>')


def _page_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor('#D6DEE8'))
    canvas.line(_SIDE_MARGIN, 1.05 * cm, A4[0] - _SIDE_MARGIN, 1.05 * cm)
    canvas.setFont(_FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor('#667788'))
    canvas.drawString(_SIDE_MARGIN, 0.65 * cm, PAGE_TITLE)
    canvas.drawRightString(A4[0] - _SIDE_MARGIN, 0.65 * cm, f'第 {doc.page} 页')
    canvas.restoreState()


def report_text_to_pdf_bytes(report_text: str, company_name: str = '企业', subtitle: str | None = None,
                             df: pd.DataFrame | None = None, score: dict[str, Any] | None = None) -> bytes:
    """Render supplied financial facts and explicit data gaps in a paginated report."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, rightMargin=_SIDE_MARGIN, leftMargin=_SIDE_MARGIN,
        topMargin=1.65*cm, bottomMargin=1.45*cm,
        title=f'{PAGE_TITLE}｜{company_name}｜{REPORT_SUBTITLE}', author=PAGE_TITLE,
        subject=REPORT_SUBTITLE,
    )
    styles = _styles()
    story: list[Any] = []
    sub = subtitle or f'生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}｜本报告仅供研究参考，不构成投资建议'
    score_value = (score or {}).get('score') if _has_complete_score(score) else None

    # Page 1: cover and executive summary
    story.append(Spacer(1, 0.6*cm))
    story.append(_para(PAGE_TITLE, styles['cover_title']))
    story.append(_para(REPORT_SUBTITLE, styles['cover_subtitle']))
    story.append(_para(company_name, styles['title']))
    story.append(_para(sub, styles['cover_subtitle']))
    story.append(Spacer(1, 0.55*cm))
    story.append(_info_table([
        ('覆盖年度', _year_range(df)),
        ('最新年度', _latest_year(df)),
        ('综合评分', f'{score_value}分' if score_value is not None else '数据不足，暂不评分'),
        ('报告定位', '面向投资者的信息整理、风险识别、趋势研判和来源追溯辅助报告。'),
    ]))
    story.append(Spacer(1, 0.55*cm))
    summary_lines = _extract_section(report_text, '一、报告摘要', ('二、',))
    if summary_lines:
        story.append(Paragraph('报告摘要', styles['h1']))
        for line in summary_lines:
            story.append(_para(line, styles['body_big']))
    else:
        story.append(_para('当前未提供可核验的分析摘要，请核验企业、报告期及年度指标后重新生成报告。', styles['body_big']))
    story.append(Spacer(1, 0.35*cm))
    story.append(_info_table([
        ('分析维度', '盈利能力、现金流质量、偿债稳健性、成长能力、风险预警、投资者画像匹配。'),
        ('输出内容', '依据本次可用数据提供年度指标、趋势图、风险提示与来源说明；四维指标完整时提供评分及能力雷达图。'),
    ]))
    story.append(PageBreak())

    # Page 2: financial overview
    story.append(Paragraph('一、核心财务指标总览', styles['title']))
    if df is not None and not df.empty:
        cards = _kpi_cards(df, score)
        if cards:
            story.append(cards)
            story.append(Spacer(1, 0.35*cm))
        snapshot = _metric_snapshot_table(df)
        if snapshot:
            story.append(Paragraph('年度指标明细表', styles['h1']))
            story.append(snapshot)
            story.append(Spacer(1, 0.35*cm))
        story.append(Paragraph('指标观察', styles['h1']))
        for line in _extract_section(report_text, '二、核心财务数据概览', ('三、',)):
            if line.startswith('- '):
                story.append(_bullet(line, styles['body']))
            else:
                story.append(_para(line, styles['body']))
    else:
        story.append(_para(NO_METRICS, styles['body_big']))
    story.append(PageBreak())

    # Page 3: trends
    story.append(Paragraph('二、趋势图与经营变化解读', styles['title']))
    trend = _trend_drawing(df) if df is not None else None
    if trend:
        story.append(trend)
        story.append(Spacer(1, 0.35*cm))
    else:
        story.append(_para('当前可用年度不足或关键数值不足，趋势图暂不展示；报告仍保留年度数据和来源追溯。', styles['body_big']))
    trend_lines = _extract_section(report_text, '三、趋势解读', ('四、',))
    if trend_lines:
        story.append(Paragraph('趋势解读', styles['h1']))
        for line in trend_lines:
            story.append(_bullet(line, styles['body']) if line.startswith('- ') else _para(line, styles['body']))
    story.append(_info_table([
        ('阅读提示', '趋势图以各指标首个有效正值年度为100；不同指标基期可能不同。缺失年份断线，不按零处理。不用于预测股价。'),
        ('投资者关注', '重点结合收入增长、利润质量、经营现金流与ROE变化，判断企业经营质量是否同步改善。'),
    ]))
    story.append(PageBreak())

    # Page 4: score and radar
    story.append(Paragraph('三、能力画像与评分拆解', styles['title']))
    radar = _radar_drawing(score)
    chart_left = 242
    bars = _mini_bar_drawing(score, width=_CONTENT_WIDTH - chart_left)
    # Recompute the bar geometry for its actual column instead of squeezing
    # a fixed 470 pt Drawing into a narrower cell. Keep text at readable size.
    if radar and bars:
        charts = Table([[radar, bars]], colWidths=[chart_left, _CONTENT_WIDTH-chart_left], hAlign='LEFT')
        charts.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(charts)
        story.append(Spacer(1, 0.3*cm))
    score_tbl = _score_table(score)
    if score_tbl:
        story.append(score_tbl)
        story.append(Spacer(1, 0.3*cm))
    else:
        story.append(_para('数据不足，暂不评分。' + str((score or {}).get('reason') or '缺少完整四维评分依据，暂不绘制能力雷达图或评分条形图。'), styles['body_big']))
    score_lines = _extract_section(report_text, '五、四维评分解释', ('六、',))
    for line in score_lines:
        story.append(_bullet(line, styles['body']) if line.startswith('- ') else _para(line, styles['body']))
    story.append(PageBreak())

    # Page 5: risk alerts
    story.append(Paragraph('四、风险预警与投资者关注点', styles['title']))
    risk_lines = _extract_section(report_text, '四、风险预警与规则依据', ('五、',))
    dashboard_lines = _extract_section(report_text, '六、风险仪表盘', ('七、',))
    if risk_lines:
        for line in risk_lines:
            if line.startswith('- '):
                story.append(_bullet(line, styles['body']))
            elif line.startswith(('规则依据：', '数据依据：', '  规则依据：', '  数据依据：')):
                story.append(_para(line.strip(), styles['small']))
            else:
                story.append(_para(line, styles['body']))
    else:
        story.append(_para('本报告未提供完整风险规则执行记录，不能据此判断低风险或相对平稳。', styles['body_big']))
    if dashboard_lines:
        story.append(Spacer(1, 0.25*cm))
        story.append(Paragraph('风险仪表盘', styles['h1']))
        for line in dashboard_lines:
            story.append(_bullet(line, styles['body']) if line.startswith('- ') else _para(line, styles['body']))
    story.append(_info_table([
        ('风险提示', '系统以公开数据和规则阈值提示关注事项，不直接输出投资买卖指令。'),
        ('使用方式', '建议将风险预警作为进一步查阅年报、公告和行业资料的线索。'),
    ]))
    story.append(PageBreak())

    # Page 6: investor profile and decision support
    story.append(Paragraph('五、投资者画像匹配与辅助决策说明', styles['title']))
    profile_lines = _extract_section(report_text, '七、投资者画像匹配', ('八、', '九、'))
    if profile_lines:
        for line in profile_lines:
            story.append(_para(line, styles['body_big'] if not line.startswith('- ') else styles['body']))
    else:
        story.append(_para('系统可按照稳健型、平衡型和成长型投资者视角，对盈利、现金流、负债和成长性进行差异化解释。', styles['body_big']))
    story.append(Spacer(1, 0.3*cm))
    story.append(_info_table([
        ('稳健型关注', '经营现金流、资产负债率、利润稳定性和重大风险预警。'),
        ('平衡型关注', '盈利能力、现金流质量、偿债稳健性与成长能力的综合表现。'),
        ('成长型关注', '收入增长、利润弹性、长期成长空间及产业链竞争位置。'),
        ('合规边界', '报告用于辅助理解企业经营质量，不预测股价，不给出买入、卖出或持有建议。'),
    ]))
    compare_lines = _extract_section(report_text, '八、企业对比参考', ('九、',))
    if compare_lines:
        story.append(Spacer(1, 0.25*cm))
        story.append(Paragraph('企业对比参考', styles['h1']))
        for line in compare_lines:
            story.append(_para(line, styles['body']))
    story.append(PageBreak())

    # Page 7: traceability and methodology
    story.append(Paragraph('六、数据来源、智能体链路与方法说明', styles['title']))
    source_lines = _extract_section(report_text, '九、数据来源与可追溯性', ('十、',))
    if not source_lines:
        story.append(_para(NO_SOURCE, styles['body_big']))
    for line in source_lines:
        story.append(_para(public_message(line, fallback=NO_SOURCE), styles['body_big']))
    story.append(Spacer(1, 0.25*cm))
    story.append(_info_table([
        ('数据接入', '支持 Excel/CSV 本地文件接入、PDF 财报解析与公开披露检索；接入结果经数据复核后入库。'),
        ('功能分工', '系统具有披露检索、文件解析、规则分析和报告输出模块；本页介绍功能，不代表本次已执行全部模块。实际执行以问数过程记录为准。'),
        ('来源追溯', '已入库指标保留相应文件、页码或单元格、原始单位和导入版本；缺失来源明确标记，便于逐项复核。'),
        ('缺失处理', '缺少目标企业、报告期、指标或来源时，明确提示不足，不更换请求条件或给出默认评分。'),
    ]))
    story.append(Spacer(1, 0.35*cm))
    conclusion_lines = _extract_section(report_text, '十、研究结论')
    if conclusion_lines:
        story.append(Paragraph('研究结论', styles['h1']))
        for line in conclusion_lines:
            story.append(_para(line, styles['body_big']))
    story.append(Spacer(1, 0.4*cm))
    footer = [['合规提示', f'本报告由{PAGE_TITLE}基于已接入数据和规则引擎自动生成，仅供学习研究与辅助分析，不构成任何形式的投资建议或买卖指令。']]
    table = _text_table(footer, [2.2, 13.8], font_size=8.5)
    table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), _FONT_NAME), ('FONTSIZE', (0,0), (-1,-1), 8.5),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#EAF2F8')),
        ('TEXTCOLOR', (0,0), (0,-1), colors.HexColor('#16324F')),
        ('GRID', (0,0), (-1,-1), 0.25, colors.HexColor('#BFC9D4')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'), ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6), ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(table)

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buf.getvalue()
