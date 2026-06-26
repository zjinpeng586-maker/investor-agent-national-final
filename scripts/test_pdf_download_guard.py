from core.parsers import extract_pdf_text

try:
    extract_pdf_text(b'<html><body>not a pdf</body></html>' * 100)
except ValueError as e:
    assert '有效 PDF' in str(e) or '年报 PDF' in str(e) or 'PDF' in str(e)
else:
    raise AssertionError('HTML content must not be parsed as PDF')
print('OK: PDF guard rejects non-PDF bytes before parser')
