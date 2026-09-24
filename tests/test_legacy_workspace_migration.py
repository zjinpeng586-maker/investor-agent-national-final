"""Compatibility coverage for legacy demo/personal libraries and backups."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from core import db
from core.storage import workspace_context
from core.unified_migration import MIGRATION_KEY, migrate_legacy_workspaces


def _legacy(root, *, company_id, name, revenue, profit=None, content=b'%PDF legacy', import_id=None):
    root.mkdir(parents=True)
    report = root / 'uploads' / (root.name + '.pdf')
    report.parent.mkdir()
    report.write_bytes(content)
    path = root / 'investor_agent.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE companies(id INTEGER PRIMARY KEY,name TEXT UNIQUE,stock_code TEXT,industry TEXT,source TEXT);
            CREATE TABLE financial_metrics(id INTEGER PRIMARY KEY,company_id INTEGER,year INTEGER,
                revenue REAL,net_profit REAL,operating_cashflow REAL,roe REAL,debt_ratio REAL,gross_margin REAL,
                eps REAL,audit_opinion TEXT,raw_source TEXT,UNIQUE(company_id,year));
            CREATE TABLE report_files(id INTEGER PRIMARY KEY,company_id INTEGER,file_name TEXT,
                report_year INTEGER,file_type TEXT,file_path TEXT,parse_status TEXT,note TEXT,uploaded_at TEXT);
            CREATE TABLE metric_sources(id INTEGER PRIMARY KEY,company_id INTEGER,year INTEGER,metric TEXT,
                value_json TEXT,normalized_unit TEXT,file_name TEXT,file_path TEXT,page INTEGER,cell TEXT,
                raw_value TEXT,raw_unit TEXT,source_url TEXT,import_id TEXT,source_note TEXT,imported_at TEXT,is_current INTEGER);
            CREATE TABLE app_meta(key TEXT PRIMARY KEY,value TEXT);
            PRAGMA user_version=2;
        ''')
        conn.execute('INSERT INTO companies VALUES(?,?,?,?,?)', (company_id, name, '123456', '测试行业', 'upload'))
        conn.execute('INSERT INTO financial_metrics(id,company_id,year,revenue,net_profit,raw_source) VALUES(1,?,2024,?,?,?)',
                     (company_id, revenue, profit, root.name))
        for index, (metric, value) in enumerate([('revenue', revenue), ('net_profit', profit)], 1):
            if value is None:
                continue
            conn.execute('INSERT INTO metric_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (index, company_id, 2024, metric, json.dumps(value), '亿元', report.name, str(report), 7, None,
                          str(value), '亿元', 'https://example.com/report.pdf', import_id or root.name,
                          '历史来源', '2024-01-01T00:00:00Z', 1))
        conn.execute('INSERT INTO report_files VALUES(1,?,?,?,?,?,?,?,?)',
                     (company_id, report.name, 2024, 'pdf', str(report), 'reviewed', '旧报告', '2024-01-01'))
    return path, report


def test_legacy_merge_personal_metric_priority_foreign_keys_and_idempotence(tmp_path):
    demo, demo_pdf = _legacy(tmp_path / 'demo', company_id=80, name='测试科技股份有限公司', revenue=100, profit=12)
    personal, personal_pdf = _legacy(tmp_path / 'personal', company_id=6, name='测试科技', revenue=250)
    before = {path: path.read_bytes() for path in (demo, personal, demo_pdf, personal_pdf)}
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db()
        companies = db.fetch_companies()
        assert len(companies) == 1
        cid = companies[0]['id']
        assert cid not in (80, 6)
        rows = db.fetch_company_metrics(cid)
        assert len(rows) == 1 and rows[0]['revenue'] == 250 and rows[0]['net_profit'] == 12
        sources = {row['metric']: dict(row) for row in db.fetch_metric_sources(cid)}
        assert sources['revenue']['import_id'] == 'personal'
        assert sources['net_profit']['import_id'] == 'demo'
        assert sources['revenue']['page'] == 7
        reports = db.fetch_report_files()
        assert len(reports) == 1  # identical content + company + report year
        assert reports[0]['company_id'] == cid
        assert reports[0]['stock_code'] == '123456'
        assert reports[0]['content_hash'] == hashlib.sha256(demo_pdf.read_bytes()).hexdigest()
        assert all(Path(row['file_path']).is_relative_to(tmp_path / 'main') for row in [*reports, *sources.values()])
        with sqlite3.connect(db.get_db_path()) as conn:
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()
            assert conn.execute('SELECT value FROM app_meta WHERE key=?', (MIGRATION_KEY,)).fetchone()[0] == 'done'
        snapshot = db.get_db_path().read_bytes()
        assert migrate_legacy_workspaces()['status'] == 'already_done'
        db.init_db()
        assert db.get_db_path().read_bytes() == snapshot
        assert len(db.fetch_metric_sources(cid, include_history=True)) == 3
    assert all(path.read_bytes() == content for path, content in before.items())
    assert len(list((tmp_path / 'main' / 'uploads').rglob('*.pdf'))) == 1


def test_migration_preserves_existing_main_corrections(tmp_path):
    _legacy(tmp_path / 'personal', company_id=4, name='测试科技', revenue=250, profit=12)
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db(migrate_legacy=False)
        cid = db.upsert_company('测试科技', '123456')
        db.upsert_metric(cid, {'year': 2024, 'revenue': 999, 'import_id': 'main-user'})
        migrate_legacy_workspaces()
        row = db.fetch_company_metrics(cid)[0]
        assert row['revenue'] == 999 and row['net_profit'] == 12
        assert next(row for row in db.fetch_metric_sources(cid) if row['metric'] == 'revenue')['import_id'] == 'main-user'


def test_failed_migration_rolls_back_rows_files_and_marker(tmp_path, monkeypatch):
    _legacy(tmp_path / 'demo', company_id=4, name='测试科技', revenue=100)
    import core.unified_migration as migration
    original = migration._merge_source
    def fail_after_merge(*args):
        original(*args)
        raise RuntimeError('模拟迁移中断')
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db(migrate_legacy=False)
        monkeypatch.setattr(migration, '_merge_source', fail_after_merge)
        with pytest.raises(RuntimeError, match='模拟迁移中断'):
            migrate_legacy_workspaces()
        assert db.fetch_companies() == []
        assert db.fetch_report_files() == []
        conn = db.get_conn()
        assert conn.execute('SELECT value FROM app_meta WHERE key=?', (MIGRATION_KEY,)).fetchone() is None
        conn.close()
        assert not list((tmp_path / 'main').rglob('*.pdf'))
        monkeypatch.setattr(migration, '_merge_source', original)
        assert migrate_legacy_workspaces()['status'] == 'done'
        assert len(db.fetch_companies()) == 1


def test_evaluation_never_reads_or_migrates_legacy_siblings(tmp_path):
    _legacy(tmp_path / 'personal', company_id=2, name='测试科技', revenue=100)
    with workspace_context('evaluation', tmp_path / 'evaluation', allow_writes=True):
        db.init_db()
        assert migrate_legacy_workspaces()['status'] == 'skipped'
        assert db.fetch_companies() == []
    assert not (tmp_path / 'main').exists()


def test_schema_upgrade_of_existing_version_two_is_idempotent(tmp_path):
    old_db, _ = _legacy(tmp_path / 'main', company_id=41, name='测试科技', revenue=100)
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db()
        conn = db.get_conn()
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(report_files)')}
        assert {'source_type', 'source_url', 'report_title', 'report_type', 'import_id', 'content_hash', 'ingest_status', 'metric_count'} <= columns
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 3
        conn.close()
        assert db.fetch_company_metrics(41)[0]['revenue'] == 100
        report = db.fetch_report_files()[0]
        assert report['ingest_status'] == 'imported' and report['metric_count'] == 1
        assert report['source_type'] == 'direct_url'
        assert report['source_url'] == 'https://example.com/report.pdf'
        assert report['report_title'] == 'main.pdf'
        assert report['import_id'] == 'main' and report['content_hash']
        before = old_db.read_bytes()
        db.init_db()
        assert old_db.read_bytes() == before


@pytest.mark.parametrize('source_state', ['missing_table', 'unverified'])
def test_auto_migration_uniquely_links_legacy_raw_source_and_report_deletion(tmp_path, source_state):
    old_db, pdf = _legacy(tmp_path / 'personal', company_id=41, name='旧版企业', revenue=250)
    with sqlite3.connect(old_db) as conn:
        conn.execute('UPDATE financial_metrics SET raw_source=?', (pdf.name,))
        if source_state == 'missing_table':
            conn.execute('DROP TABLE metric_sources')
        else:
            conn.execute("UPDATE metric_sources SET file_path=NULL,file_name=NULL,import_id='legacy-unverified'")
    before = old_db.read_bytes()
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db()
        cid = db.fetch_companies()[0]['id']
        report = db.fetch_report_files()[0]
        source = db.fetch_metric_sources(cid)[0]
        assert source['file_path'] == report['file_path']
        assert source['import_id'] == report['import_id']
        assert report['ingest_status'] == 'imported' and report['metric_count'] == 1
        assert db.delete_report_file(report['id'])
        assert db.fetch_company_metrics(cid) == []
    assert old_db.read_bytes() == before


@pytest.mark.parametrize('entrypoint', ['schema_upgrade', 'explicit_migration'])
@pytest.mark.parametrize('reference', ['filename', 'absolute_path'])
def test_old_main_and_explicit_copy_backfill_missing_sources_without_notes(tmp_path, entrypoint, reference):
    root = tmp_path / ('main' if entrypoint == 'schema_upgrade' else 'old')
    old_db, pdf = _legacy(root, company_id=41, name='旧版企业', revenue=100)
    with sqlite3.connect(old_db) as conn:
        conn.execute('DROP TABLE metric_sources')
        conn.execute('UPDATE financial_metrics SET raw_source=?', (pdf.name if reference == 'filename' else str(pdf),))
    if entrypoint == 'explicit_migration':
        from scripts.migrate_legacy_data import migrate
        root = migrate(old_db, tmp_path / 'new', source_files=root)
    with workspace_context('main', root, allow_writes=True):
        db.init_db()
        report = db.fetch_report_files()[0]
        assert report['ingest_status'] == 'imported' and report['metric_count'] == 1
        assert report['source_type'] == 'upload'
        source = db.fetch_metric_sources(41)[0]
        assert source['file_path'] == report['file_path'] and source['file_name'] == pdf.name
        before = db.get_db_path().read_bytes()
        db.init_db()
        assert db.get_db_path().read_bytes() == before
        db.delete_report_file(report['id'])
        assert db.fetch_company_metrics(41) == []


def test_ambiguous_legacy_source_stays_pending_and_does_not_guess_file(tmp_path):
    old_db, pdf = _legacy(tmp_path / 'personal', company_id=41, name='旧版企业', revenue=100)
    another = pdf.parent / 'another.pdf'
    another.write_bytes(b'%PDF different report')
    with sqlite3.connect(old_db) as conn:
        conn.execute('DROP TABLE metric_sources')
        conn.execute('UPDATE financial_metrics SET raw_source=?', (pdf.name,))
        conn.execute('INSERT INTO report_files VALUES(2,?,?,?,?,?,?,?,?)',
                     (41, pdf.name, 2024, 'pdf', str(another), 'reviewed', '不用于推断来源', '2024-01-02'))
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db()
        cid = db.fetch_companies()[0]['id']
        assert db.fetch_metric_sources(cid)[0]['file_path'] is None
        assert all(report['metric_count'] == 0 and report['ingest_status'] == 'pending_metrics'
                   for report in db.fetch_report_files())


@pytest.mark.parametrize('kind', ['main', 'personal'])
@pytest.mark.parametrize('source_case', ['audit_only', 'numeric_history_without_numeric_metric'])
def test_legacy_report_imported_status_requires_actual_numeric_annual_metrics(tmp_path, kind, source_case):
    old_db, _ = _legacy(tmp_path / kind, company_id=41, name='旧版企业', revenue=100)
    with sqlite3.connect(old_db) as conn:
        conn.execute("UPDATE financial_metrics SET revenue=NULL,audit_opinion='标准无保留意见'")
        if source_case == 'audit_only':
            conn.execute("UPDATE metric_sources SET metric='audit_opinion',value_json=?,normalized_unit='文本'",
                         (json.dumps('标准无保留意见'),))
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        db.init_db()
        report = db.fetch_report_files()[0]
        assert report['ingest_status'] == 'pending_metrics' and report['metric_count'] == 0
        cid = db.fetch_companies()[0]['id']
        assert all(row['revenue'] is None for row in db.fetch_company_metrics(cid))


def test_explicit_legacy_file_migration_targets_main_and_preserves_source(tmp_path):
    from scripts.migrate_legacy_data import migrate
    old_db, old_pdf = _legacy(tmp_path / 'old', company_id=41, name='旧版企业', revenue=100)
    original = old_db.read_bytes()
    target = migrate(old_db, tmp_path / 'new', source_files=old_db.parent)
    assert target == tmp_path / 'new' / 'main'
    assert old_db.read_bytes() == original and old_pdf.is_file()
    with workspace_context('main', target, allow_writes=True):
        assert db.fetch_companies()[0]['name'] == '旧版企业'
        assert Path(db.fetch_report_files()[0]['file_path']).read_bytes() == old_pdf.read_bytes()
    with pytest.raises(FileExistsError):
        migrate(old_db, tmp_path / 'new')


def test_restoring_old_backup_automatically_merges_main(tmp_path):
    from scripts.data_tools import digest
    from scripts.restore_backup import restore
    backup_root = tmp_path / 'backup'
    _legacy(backup_root / 'demo', company_id=40, name='测试科技', revenue=100)
    _legacy(backup_root / 'personal', company_id=3, name='测试科技', revenue=200)
    entries = [{'path': path.relative_to(backup_root).as_posix(), 'size': path.stat().st_size, 'sha256': digest(path)}
               for path in backup_root.rglob('*') if path.is_file()]
    (backup_root / 'manifest.json').write_text(json.dumps({
        'status': 'complete', 'format_version': 1, 'source_data_dir': str(backup_root),
        'workspaces': ['demo', 'personal'], 'files': entries}), encoding='utf-8')
    restored = restore(backup_root, tmp_path / 'restored')
    with workspace_context('main', restored / 'main', allow_writes=True):
        cid = db.fetch_companies()[0]['id']
        assert db.fetch_company_metrics(cid)[0]['revenue'] == 200
        assert Path(db.fetch_report_files()[0]['file_path']).is_relative_to(restored / 'main')
    assert (restored / 'demo' / 'investor_agent.db').is_file()
    assert (restored / 'personal' / 'investor_agent.db').is_file()


@pytest.mark.parametrize('legacy_default', ['demo', 'personal', 'evaluation'])
def test_old_environment_defaults_cannot_change_business_library(tmp_path, monkeypatch, legacy_default):
    from core import storage
    monkeypatch.setenv('FINANCIAL_WORKSPACE', legacy_default)
    monkeypatch.setenv('FINANCIAL_DATA_DIR', str(tmp_path))
    token = storage._workspace.set(None)
    try:
        assert storage.get_workspace() == 'main'
        assert storage.get_data_root() == tmp_path / 'main'
    finally:
        storage._workspace.reset(token)
