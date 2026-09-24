"""Read-only original-document viewers tied to the active data library."""
from pathlib import PureWindowsPath
from hashlib import sha256
import streamlit as st

from core.storage import resolve_stored_file
from app.messages import NO_SOURCE, public_message


@st.cache_data(max_entries=32, show_spinner=False)
def _pdf_page(path: str, modified_ns: int, page: int):
    import fitz
    with fitz.open(path) as document:
        page = max(1, min(page, len(document)))
        return document[page - 1].get_pixmap(matrix=fitz.Matrix(1.6, 1.6)).tobytes('png'), len(document)


def render_source(source: dict, key: str, *, default_page: int = 1):
    if not source or not source.get('file_path'):
        st.caption(NO_SOURCE)
        return
    path = resolve_stored_file(str(source.get('file_path') or ''))
    if path is None:
        st.caption('当前资料库无法访问对应原文件，请前往数据中心核验来源记录或重新接入原文件。')
        return
    key = key + '_' + sha256((str(path) + str(source.get('page'))).encode()).hexdigest()[:12]
    try:
        stat = path.stat()
        if stat.st_size > 30 * 1024 * 1024:
            st.caption('原文件超过在线预览与下载的大小限制，请前往数据中心核验文件。')
            return
        original = path.read_bytes()
    except OSError as exc:
        st.warning(public_message(exc, fallback='原文件读取失败，请前往数据中心核验文件状态。'))
        return
    # A stored file name may originate from either operating system. Only the
    # basename belongs in captions and download names; the path remains private.
    name = PureWindowsPath(str(source.get('file_name') or path.name)).name
    mime = 'application/pdf' if path.suffix.lower() == '.pdf' else 'application/octet-stream'
    st.download_button('下载原始文件', original, name, mime, key=f'source_download_{key}')
    if path.suffix.lower() != '.pdf':
        if source.get('cell'):
            st.caption(f'对应位置：{public_message(source["cell"], fallback="未记录")}')
        return
    try:
        page = max(1, int(source.get('page') or default_page))
    except (ValueError, TypeError):
        page = max(1, int(default_page))
    opened_key = f'source_open_{key}'
    if st.button(f'查看原文第 {page} 页', key=f'source_button_{key}'):
        st.session_state[opened_key] = True
    if st.session_state.get(opened_key):
        try:
            image, count = _pdf_page(str(path), stat.st_mtime_ns, page)
            selected = st.number_input('原文页码', min_value=1, max_value=count, value=min(page, count), key=f'source_page_{key}')
            if int(selected) != page:
                image, _ = _pdf_page(str(path), stat.st_mtime_ns, int(selected))
            st.image(image, caption=f'{name} · PDF第 {selected} 页', width='stretch')
        except Exception as exc:
            reason = public_message(exc, fallback='文件无法解析').rstrip('。')
            st.warning(f'原文预览失败：{reason}。请下载原始文件核验。')
