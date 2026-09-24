"""Historical answers must retain the data and source version actually answered."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from streamlit.delta_generator import DeltaGenerator
from streamlit.testing.v1 import AppTest

from core.db import fetch_company_metrics, init_db, upsert_company, upsert_metric
from core.storage import workspace_context

APP = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
COMPANY = '历史快照股份有限公司'


def _query(app, question):
    app.text_area(key='qa_input').set_value(question)
    next(button for button in app.button if button.label == '开始分析').click().run()
    assert not app.exception
    return app.session_state.chat_messages[-1]['result']


def _values_table(app):
    return next(frame.value for frame in app.dataframe if {'企业', '年度', '营业收入（亿元）'} <= set(frame.value.columns))


def _sources_table(app):
    return next(frame.value for frame in app.dataframe if {'文件', '导入版本', '数值'} <= set(frame.value.columns))


def _chart_data(app):
    return [json.loads(chart.proto.spec)['data'] for chart in app.get('plotly_chart')]


def test_history_keeps_value_chart_source_and_download_after_database_update(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'local')
    monkeypatch.setenv('FINANCIAL_WORKSPACE', 'main')
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    library = tmp_path / 'main'
    first_file = library / 'uploads' / 'v1' / 'original.csv'
    first_file.parent.mkdir(parents=True)
    first_file.write_text('年度,营收\n2024,100\n', encoding='utf-8')
    with workspace_context('main', library, allow_writes=True):
        init_db()
        company_id = upsert_company(COMPANY)
        for year, revenue in [(2023, 90), (2024, 100)]:
            row = {'year': year, 'revenue': revenue, 'net_profit': 10, 'operating_cashflow': 20,
                   'roe': 12, 'debt_ratio': 30, 'audit_opinion': '标准无保留意见'}
            row['_provenance'] = {metric: {'file_name': 'original.csv', 'file_path': str(first_file),
                                           'cell': f'{metric}-{year}', 'raw_unit': '亿元' if metric in {'revenue', 'net_profit', 'operating_cashflow'} else '%',
                                           'import_id': 'v1'} for metric in row if metric != 'year'}
            upsert_metric(company_id, row)

    downloads = {}
    original_download = DeltaGenerator.download_button

    def capture_download(self, label, data, *args, **kwargs):
        if label == '下载 TXT 报告':
            downloads[kwargs.get('key', label)] = bytes(data)
        return original_download(self, label, data, *args, **kwargs)

    monkeypatch.setattr(DeltaGenerator, 'download_button', capture_download)
    app = AppTest.from_file(APP, default_timeout=45).run()
    assert not app.exception
    first = _query(app, COMPANY + '2024年营业收入是多少？')
    assert first['status'] == 'success'
    assert first['data_snapshot'][COMPANY][0]['revenue'] == 100
    first_snapshot = deepcopy(first['data_snapshot'])
    first_session = app.session_state.active_session_id
    first_chart = _chart_data(app)
    assert _values_table(app)['营业收入（亿元）'].tolist() == [100]
    assert set(_sources_table(app)['文件']) == {'original.csv'}
    app.button(key='qa_report_0').click().run()
    assert not app.exception
    original_txt = next(iter(downloads.values()))
    assert b'100.00' in original_txt and 'original.csv'.encode() in original_txt

    second_file = library / 'uploads' / 'v2' / 'updated.csv'
    second_file.parent.mkdir(parents=True)
    second_file.write_text('年度,营收\n2024,999\n', encoding='utf-8')
    with workspace_context('main', library, allow_writes=True):
        upsert_metric(company_id, {'year': 2024, 'revenue': 999,
                                   '_provenance': {'revenue': {'file_name': 'updated.csv', 'file_path': str(second_file),
                                                               'cell': 'B2', 'raw_unit': '亿元', 'import_id': 'v2'}}})
        assert next(row for row in fetch_company_metrics(company_id) if row['year'] == 2024)['revenue'] == 999

    app.run()
    assert not app.exception
    assert app.session_state.chat_messages[-1]['result']['data_snapshot'] == first_snapshot
    assert _values_table(app)['营业收入（亿元）'].tolist() == [100]
    assert set(_sources_table(app)['文件']) == {'original.csv'}
    assert set(_sources_table(app)['导入版本']) == {'v1'}
    assert _chart_data(app) == first_chart
    assert all(payload == original_txt for payload in downloads.values())

    # A new question must see the real update; historical isolation is not a
    # stale application-wide cache or a database rollback.
    app.button(key='new_session').click().run()
    current = _query(app, COMPANY + '2024年营业收入是多少？')
    assert current['data_snapshot'][COMPANY][0]['revenue'] == 999
    assert _values_table(app)['营业收入（亿元）'].tolist() == [999]
    assert set(_sources_table(app)['文件']) == {'updated.csv'}
    app.button(key=f'session_{first_session}').click().run()
    assert not app.exception
    assert _values_table(app)['营业收入（亿元）'].tolist() == [100]
    assert set(_sources_table(app)['文件']) == {'original.csv'}
