from pathlib import Path

from streamlit.testing.v1 import AppTest


PAGES = ['财报问数', '企业分析', '研究资料库', '数据中心', '评测中心']
APP_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'


def test_default_page_and_five_page_navigation():
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()

    assert not app.exception
    assert app.radio[0].options == PAGES
    assert app.radio[0].value == '财报问数'

    for page in PAGES:
        app.radio[0].set_value(page).run()
        assert not app.exception
        assert any(item.value == f'## {page}' for item in app.markdown)


def test_evaluation_page_does_not_claim_unrun_metrics():
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    app.radio[0].set_value('评测中心').run()

    page_text = '\n'.join(item.value for item in [*app.markdown, *app.caption])
    assert '不展示百分比' in page_text
    assert '待后续接入' in str(app.dataframe[0].value.to_dict())
