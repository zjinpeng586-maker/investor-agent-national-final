"""Shared public identity and local, optional brand asset loading."""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path

BRAND_NAME = "擎梦数智"
PRODUCT_NAME = "财报智问"
PRODUCT_DESCRIPTION = "上市公司财务智能分析平台"
PAGE_TITLE = f"{BRAND_NAME}｜{PRODUCT_NAME}"
REPORT_SUBTITLE = "上市公司财务智能分析报告"
BRAND_LOGO_PATH = Path(__file__).resolve().parent / "assets/brand/qingmeng-shuzhi-logo.png"


def logo_data_uri(logo_path: Path | None = None) -> str | None:
    """Embed only a readable local image; missing assets render text only."""
    path = Path(logo_path) if logo_path is not None else BRAND_LOGO_PATH
    mime = {".png": "image/png", ".svg": "image/svg+xml", ".webp": "image/webp"}.get(path.suffix.lower())
    if not mime:
        return None
    try:
        content = path.read_bytes()
    except OSError:
        return None
    if not content:
        return None
    return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"


def brand_html(logo_path: Path | None = None) -> str:
    uri = logo_data_uri(logo_path)
    logo = (f'<img class="fu-brand-logo" src="{uri}" alt="{escape(BRAND_NAME)} Logo">'
            if uri else '')
    return (f'<div class="fu-brand">{logo}<div class="fu-brand-text">'
            f'<div class="fu-brand-name">{escape(BRAND_NAME)}</div>'
            f'<div class="fu-brand-sub">{escape(PRODUCT_NAME)} · {escape(PRODUCT_DESCRIPTION)}</div>'
            '</div></div>')


def page_icon(logo_path: Path | None = None) -> str | None:
    return logo_data_uri(logo_path)
