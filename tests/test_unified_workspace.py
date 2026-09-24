from pathlib import Path
import sqlite3

import pytest

from core import db, storage
from core.seed import seed_sample_data
from scripts.backup_data import backup
from scripts.restore_backup import restore
from scripts.verify_backup import verify


def test_only_main_is_a_business_library_and_local_writes_are_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'local')
    with storage.workspace_context('main', tmp_path / 'main'):
        assert storage.configure_workspace('main') == 'main'
        assert storage.can_write()
        db.init_db()
        cid = db.upsert_company('本地企业')
        db.upsert_metric(cid, {'year': 2024, 'revenue': 1})
        assert db.fetch_company_metrics(cid)[0]['revenue'] == 1
        with pytest.raises(ValueError):
            storage.configure_workspace('evaluation')


def test_main_seed_fills_only_missing_fields(tmp_path):
    with storage.workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db()
        cid = db.upsert_company('比亚迪股份有限公司', '002594')
        db.upsert_metric(cid, {'year': 2024, 'revenue': 999, 'import_id': 'user-reviewed'})
        seed_sample_data()
        row = next(row for row in db.fetch_company_metrics(cid) if row['year'] == 2024)
        assert row['revenue'] == 999 and row['net_profit'] is not None
        assert next(row for row in db.fetch_metric_sources(cid) if row['metric'] == 'revenue' and row['year'] == 2024)['import_id'] == 'user-reviewed'
        assert len(db.fetch_companies()) == 10


def test_evaluation_refuses_main_directory_and_does_not_inherit_local_write_access(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'local')
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    with storage.workspace_context('main', tmp_path / 'main'):
        with pytest.raises(ValueError, match='不能.*共用'):
            with storage.workspace_context('evaluation', tmp_path / 'main', allow_writes=True):
                pass
    with storage.workspace_context('evaluation', tmp_path / 'evaluation'):
        assert not storage.can_write()
        monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'main' / 'investor_agent.db')
        assert db.get_db_path() == tmp_path / 'evaluation' / 'investor_agent.db'


def test_default_backup_verify_restore_preserves_main_and_attachment_paths(tmp_path):
    data = tmp_path / 'source'
    with storage.workspace_context('main', data / 'main', allow_writes=True):
        db.init_db()
        cid = db.upsert_company('备份企业', '123456')
        path = storage.get_data_root() / 'uploads' / 'report.pdf'
        path.parent.mkdir()
        path.write_bytes(b'%PDF test')
        db.upsert_metric(cid, {'year': 2024, 'revenue': 10, '_provenance': {'revenue': {'file_path': str(path), 'import_id': 'backup-1'}}})
        db.insert_report_file(cid, path.name, 2024, 'pdf', str(path), 'reviewed', import_id='backup-1', ingest_status='imported', metric_count=1)
    saved = backup(data, tmp_path / 'backups')
    assert verify(saved)['workspaces'] == ['main']
    restored = restore(saved, tmp_path / 'restored')
    with storage.workspace_context('main', restored / 'main', allow_writes=True):
        assert db.fetch_company_metrics(cid)[0]['revenue'] == 10
        report = db.fetch_report_files()[0]
        assert Path(report['file_path']).read_bytes() == b'%PDF test'
        assert Path(report['file_path']).is_relative_to(restored)
        assert Path(db.fetch_metric_sources(cid)[0]['file_path']).is_relative_to(restored)


def test_delete_report_restores_prior_metric_and_removes_orphan_year(tmp_path):
    with storage.workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db()
        cid = db.upsert_company('报告删除企业')
        db.upsert_metric(cid, {'year': 2024, 'revenue': 10, '_provenance': {'revenue': {'file_path': 'old.pdf', 'import_id': 'old'}}})
        old = db.insert_report_file(cid, 'old.pdf', 2024, 'pdf', 'old.pdf', 'reviewed', import_id='old')
        db.upsert_metric(cid, {'year': 2024, 'revenue': 20, '_provenance': {'revenue': {'file_path': 'new.pdf', 'import_id': 'new'}}})
        db.upsert_metric(cid, {'year': 2023, 'revenue': 15, '_provenance': {'revenue': {'file_path': 'new.pdf', 'import_id': 'new'}}})
        new = db.insert_report_file(cid, 'new.pdf', 2024, 'pdf', 'new.pdf', 'reviewed', import_id='new')
        assert db.delete_report_file(new)
        rows = db.fetch_company_metrics(cid)
        assert len(rows) == 1 and rows[0]['year'] == 2024 and rows[0]['revenue'] == 10
        assert db.fetch_metric_sources(cid)[0]['import_id'] == 'old'
        assert db.delete_report_file(old)
        assert db.fetch_company_metrics(cid) == []
        assert db.fetch_metric_sources(cid, include_history=True) == []
        assert not db.delete_report_file(old)


def test_delete_tabular_report_is_scoped_to_its_year(tmp_path):
    with storage.workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db()
        cid = db.upsert_company('多年份表格企业')
        ids = {}
        for year in (2023, 2024):
            db.upsert_metric(cid, {'year': year, 'revenue': year, '_provenance': {'revenue': {'file_path': 'all.csv', 'import_id': 'all'}}})
            ids[year] = db.insert_report_file(cid, 'all.csv', year, 'csv', 'all.csv', 'reviewed', import_id='all')
        db.delete_report_file(ids[2024])
        assert [row['year'] for row in db.fetch_company_metrics(cid)] == [2023]
        assert [row['report_year'] for row in db.fetch_report_files()] == [2023]


def test_report_delete_is_public_readonly_and_transactional(tmp_path, monkeypatch):
    with storage.workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db()
        cid = db.upsert_company('删除事务企业')
        db.upsert_metric(cid, {'year': 2024, 'revenue': 10, 'import_id': 'old'})
        conn = db.get_conn()
        conn.execute("UPDATE metric_sources SET value_json='invalid-json'")
        conn.commit()
        conn.close()
        db.upsert_metric(cid, {'year': 2024, 'revenue': 20, 'import_id': 'new'})
        rid = db.insert_report_file(cid, 'new.pdf', 2024, 'pdf', 'new.pdf', 'reviewed', import_id='new')
        before = db.get_db_path().read_bytes()
        with pytest.raises(ValueError):
            db.delete_report_file(rid)
        assert db.get_db_path().read_bytes() == before
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'public')
    with storage.workspace_context('main', tmp_path / 'main'):
        with pytest.raises(PermissionError, match='当前部署为只读模式'):
            db.delete_report_file(rid)
