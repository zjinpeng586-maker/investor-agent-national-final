import json
import sqlite3

import pytest

from core.db import (atomic_write, fetch_companies, fetch_company_metrics, fetch_metric_sources,
                     get_db_path, init_db, upsert_company, upsert_metric, delete_uploaded_company)
from core.seed import seed_sample_data
from core.storage import workspace_context, require_write_access, get_data_root


def test_rerun_seed_never_overwrites_user_metric_or_sources(tmp_path):
    with workspace_context('evaluation', tmp_path, allow_writes=True):
        init_db()
        seed_sample_data()
        company = next(row for row in fetch_companies() if row['stock_code'] == '002594')
        upsert_metric(company['id'], {'year': 2024, 'revenue': 100, '_provenance': {
            'revenue': {'file_name': 'user.csv', 'raw_unit': '亿元', 'raw_value': '100', 'cell': 'B2', 'import_id': 'user-1'}}})
        original_sources = [dict(row) for row in fetch_metric_sources(company['id'], 2024)]
        before = get_db_path().read_bytes()
        seed_sample_data()
        assert get_db_path().read_bytes() == before
        row = next(row for row in fetch_company_metrics(company['id']) if row['year'] == 2024)
        assert row['revenue'] == 100
        assert [dict(row) for row in fetch_metric_sources(company['id'], 2024)] == original_sources


def test_partial_update_preserves_other_metrics_provenance_and_version_history(tmp_path):
    with workspace_context('evaluation', tmp_path, allow_writes=True):
        init_db()
        cid = upsert_company('来源测试企业')
        upsert_metric(cid, {'year': 2024, 'revenue': 100, 'net_profit': 10, '_provenance': {
            'revenue': {'file_name': 'A.csv', 'cell': 'B2', 'raw_unit': '亿元', 'import_id': 'A'},
            'net_profit': {'file_name': 'A.csv', 'cell': 'C2', 'raw_unit': '亿元', 'import_id': 'A'}}})
        upsert_metric(cid, {'year': 2024, 'net_profit': 12, '_provenance': {
            'net_profit': {'file_name': 'B.pdf', 'page': 11, 'raw_value': 120000, 'raw_unit': '万元', 'import_id': 'B'}}})
        sources = {row['metric']: dict(row) for row in fetch_metric_sources(cid, 2024)}
        assert sources['revenue']['file_name'] == 'A.csv'
        assert sources['net_profit']['file_name'] == 'B.pdf'
        assert sources['net_profit']['page'] == 11
        assert sources['net_profit']['raw_unit'] == '万元'
        history = [dict(row) for row in fetch_metric_sources(cid, 2024, 'net_profit', include_history=True)]
        assert [(json.loads(row['value_json']), row['is_current']) for row in history] == [(12, 1), (10, 0)]


def test_main_and_evaluation_workspaces_are_separate(tmp_path):
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        init_db()
        seed_sample_data()
        assert len(fetch_companies()) == 10
        cid = upsert_company('真实资料企业')
        upsert_metric(cid, {'year': 2024, 'revenue': 999})
        path = get_db_path()
        before = path.read_bytes()
        with workspace_context('evaluation', tmp_path / 'evaluation', allow_writes=True):
            init_db()
            seed_sample_data()
            assert get_db_path() != path
            assert len(fetch_companies()) == 10
        assert get_db_path() == path
        assert path.read_bytes() == before
        assert any(row['name'] == '真实资料企业' for row in fetch_companies())


def test_public_main_rejects_all_mutation_apis(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'public')
    with workspace_context('main', tmp_path):
        init_db()
        seed_sample_data()
        for action in [require_write_access, lambda: upsert_company('访客写入'),
                       lambda: upsert_metric(1, {'year': 2024, 'revenue': 1}),
                       lambda: delete_uploaded_company(1)]:
            with pytest.raises(PermissionError):
                action()


def test_batch_rolls_back_all_company_and_metric_changes(tmp_path):
    with workspace_context('evaluation', tmp_path, allow_writes=True):
        init_db()
        with pytest.raises(ValueError):
            with atomic_write():
                cid = upsert_company('事务测试企业')
                upsert_metric(cid, {'year': 2024, 'revenue': 100})
                upsert_metric(cid, {'year': 2025})
        assert fetch_companies() == []


@pytest.mark.parametrize('record', [{'year': 2024}, {'year': 2024, 'period': 'H1', 'revenue': 100}, {'year': 2024, 'revenue': float('inf')}])
def test_empty_nonannual_or_nonfinite_records_are_rejected(tmp_path, record):
    with workspace_context('evaluation', tmp_path, allow_writes=True):
        init_db()
        cid = upsert_company('记录校验企业')
        with pytest.raises(ValueError):
            upsert_metric(cid, record)
        assert fetch_company_metrics(cid) == []


def test_nested_transaction_cannot_write_another_workspace(tmp_path):
    with workspace_context('main', tmp_path / 'p', allow_writes=True):
        init_db()
        with atomic_write():
            upsert_company('本库企业')
            with workspace_context('evaluation', tmp_path / 'e', allow_writes=True):
                with pytest.raises(RuntimeError, match='事务内不能切换'):
                    upsert_company('跨库企业')
        assert [r['name'] for r in fetch_companies()] == ['本库企业']
        before = get_db_path().read_bytes()
        init_db()
        assert get_db_path().read_bytes() == before


def test_original_reader_rejects_other_workspace_with_external_data_root(tmp_path, monkeypatch):
    import core.storage as storage
    packaged = tmp_path / 'packaged'
    other = packaged / 'data' / 'workspaces' / 'evaluation' / 'private.pdf'
    other.parent.mkdir(parents=True)
    other.write_bytes(b'private')
    sample = packaged / 'data' / 'sample_byd.csv'
    sample.write_text('sample')
    monkeypatch.setattr(storage, 'ROOT', packaged)
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path / 'external'))
    with workspace_context('main', tmp_path / 'external' / 'main'):
        assert storage.resolve_stored_file(str(other)) is None
        assert storage.resolve_stored_file(str(sample)) == sample


def test_public_environment_ignores_internal_default(monkeypatch):
    import core.storage as storage
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'public')
    monkeypatch.setenv('FINANCIAL_WORKSPACE', 'evaluation')
    token = storage._workspace.set(None)
    try:
        assert storage.get_workspace() == 'main'
    finally:
        storage._workspace.reset(token)
