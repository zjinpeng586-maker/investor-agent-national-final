"""Explicit one-time copy of an old database into a new main library."""
from pathlib import Path
import argparse
from contextlib import closing
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.data_tools import (snapshot_database, copy_assets, remap_file_paths, read_only_db,
                                utc_now, write_json, digest)
from core.storage import workspace_context
from core.db import init_db


def migrate(source_db: Path, data_dir: Path, source_files: Path | None = None,
            original_files_root: str | None = None) -> Path:
    source_db, data_dir = source_db.expanduser().resolve(), data_dir.expanduser().resolve()
    with read_only_db(source_db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'companies', 'financial_metrics'} <= tables:
            raise ValueError('源文件不是财报智问数据库，缺少核心表。')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
            raise ValueError('源数据库包含额外触发器或视图；请先人工复核，禁止直接迁移执行。')
    target = data_dir / 'main'
    if target.exists():
        raise FileExistsError('统一资料库目录已存在，拒绝覆盖；请选择一个新的 --data-dir。')
    if source_files:
        source_files = source_files.expanduser().resolve()
        if not source_files.is_dir() or target.is_relative_to(source_files):
            raise ValueError('资料源目录无效，或迁移目标位于源目录内部。')
    target.mkdir(parents=True, exist_ok=False)
    record = {'status': 'incomplete', 'created_at': utc_now(), 'source_db': str(source_db),
              'policy': '复制旧值，不推断缺失单位/页码/单元格；旧版来源标记待复核。', 'files': []}
    try:
        snapshot_database(source_db, target / 'investor_agent.db')
        if source_files:
            copied = target / 'legacy_files'
            excluded = {source_db}
            # Do not copy databases or their journals as ordinary attachments.
            excluded.update(path for path in source_files.rglob('*') if path.is_file() and path.suffix.lower() in {'.db', '.sqlite', '.sqlite3'})
            files, skipped = copy_assets(source_files, copied, excluded)
            record['files'], record['skipped'] = files, skipped
            record.update(remap_file_paths(target / 'investor_agent.db', original_files_root or str(source_files), copied, source_files))
        else:
            record['warning'] = '未提供 --source-files；原始附件未复制，历史文件路径需人工复核。'
        with workspace_context('main', root=target, allow_writes=True):
            init_db()
        with closing(sqlite3.connect(target / 'investor_agent.db')) as conn:
            pending = conn.execute("SELECT COUNT(*) FROM metric_sources WHERE import_id='legacy-unverified'").fetchone()[0]
            conn.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES('legacy_migration_review','pending')")
            record['unverified_metric_sources'] = pending
            record['companies'] = conn.execute('SELECT COUNT(*) FROM companies').fetchone()[0]
            record['financial_records'] = conn.execute('SELECT COUNT(*) FROM financial_metrics').fetchone()[0]
            conn.commit()
        record['status'], record['completed_at'] = 'complete', utc_now()
        record['database_sha256'] = digest(target / 'investor_agent.db')
        write_json(target / 'migration_manifest.json', record)
    except Exception as error:
        record['error'] = str(error)
        write_json(target / 'MIGRATION_INCOMPLETE.json', record)
        raise
    return target


def main():
    parser = argparse.ArgumentParser(description='只读复制旧版 app.db/investor_agent.db 到新统一资料库，绝不覆盖已有资料。')
    parser.add_argument('--source-db', required=True, type=Path, help='必须明确指定旧数据库文件')
    parser.add_argument('--data-dir', required=True, type=Path, help='新持久数据目录；其中不能已经存在 main')
    parser.add_argument('--source-files', type=Path, help='旧 uploads 等附件所在的上级数据目录')
    parser.add_argument('--original-files-root', help='跨电脑迁移时，数据库中原附件路径的旧根目录；默认 source-files')
    args = parser.parse_args()
    try:
        print(migrate(args.source_db, args.data_dir, args.source_files, args.original_files_root))
    except Exception as error:
        parser.exit(1, f'迁移未完成：{error}\n原始文件保持不变；不要启动含 MIGRATION_INCOMPLETE.json 的目标库。\n')


if __name__ == '__main__':
    main()
