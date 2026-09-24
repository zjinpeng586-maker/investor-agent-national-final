"""Source controls preserve original files without exposing machine details."""
from io import BytesIO

import pymupdf
from reportlab.pdfgen.canvas import Canvas
import streamlit as st
from streamlit.testing.v1 import AppTest

from app.messages import NO_SOURCE


def _app(source):
    app = AppTest.from_string(
        'import streamlit as st\n'
        'from app.source_viewer import render_source\n'
        'render_source(st.session_state.source, "source_acceptance")\n'
    )
    app.session_state.source = source
    app.run()
    assert not app.exception
    return app


def test_missing_source_has_formal_empty_state_and_no_download():
    app = _app({})
    assert [caption.value for caption in app.caption] == [NO_SOURCE]
    assert not app.button and not app.get('download_button')


def test_unavailable_source_does_not_disclose_saved_path(monkeypatch):
    monkeypatch.setattr('app.source_viewer.resolve_stored_file', lambda value: None)
    app = _app({'file_path': 'C:/Users/private/database/uploads/report.pdf'})
    assert '当前资料库无法访问对应原文件' in app.caption[0].value
    assert 'C:/' not in app.caption[0].value
    assert not app.get('download_button')


def test_original_download_and_exact_pdf_page_remain_available(tmp_path, monkeypatch):
    buffer = BytesIO()
    canvas = Canvas(buffer)
    for number in (1, 2):
        canvas.drawString(60, 700, f'ORIGINAL PAGE {number}')
        canvas.showPage()
    canvas.save()
    original = buffer.getvalue()
    path = tmp_path / 'annual-report.pdf'
    path.write_bytes(original)
    monkeypatch.setattr('app.source_viewer.resolve_stored_file', lambda value: path)
    downloads = []
    real_download = st.download_button

    def capture(label, data, file_name=None, *args, **kwargs):
        downloads.append((data, file_name))
        return real_download(label, data, file_name, *args, **kwargs)

    monkeypatch.setattr(st, 'download_button', capture)
    app = _app({'file_path': str(path), 'file_name': r'C:\private\annual-report.pdf', 'page': 2})
    assert downloads[-1] == (original, 'annual-report.pdf')
    app.button[0].click().run()
    assert not app.exception
    assert app.number_input[0].value == 2
    assert app.get('image') and app.get('image')[0].proto.imgs[0].url
    assert app.get('image')[0].proto.imgs[0].caption == 'annual-report.pdf · PDF第 2 页'
    with pymupdf.open(stream=downloads[-1][0], filetype='pdf') as document:
        assert len(document) == 2 and 'ORIGINAL PAGE 2' in document[1].get_text()


def test_preview_failure_has_safe_actionable_message_and_retains_download(tmp_path, monkeypatch):
    path = tmp_path / 'unreadable.pdf'
    path.write_bytes(b'%PDF-test-content')
    monkeypatch.setattr('app.source_viewer.resolve_stored_file', lambda value: path)

    def unavailable(*args, **kwargs):
        raise RuntimeError('Traceback (most recent call last):\nC:/Users/private/main/investor_agent.db')

    monkeypatch.setattr('app.source_viewer._pdf_page', unavailable)
    app = _app({'file_path': str(path), 'page': 1})
    app.button[0].click().run()
    assert not app.exception
    assert app.warning[0].value == '原文预览失败：文件无法解析。请下载原始文件核验。'
    assert app.get('download_button')
    assert 'Traceback' not in app.warning[0].value
    assert 'investor_agent.db' not in app.warning[0].value


def test_invalid_page_metadata_falls_back_without_losing_original_file(tmp_path, monkeypatch):
    path = tmp_path / 'metadata.pdf'
    path.write_bytes(b'%PDF-test-content')
    monkeypatch.setattr('app.source_viewer.resolve_stored_file', lambda value: path)
    app = _app({'file_path': str(path), 'page': 'invalid'})
    assert app.button[0].label == '查看原文第 1 页'
    assert app.get('download_button')
