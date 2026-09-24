"""Standard-library helpers shared by offline administration commands."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def default_data_dir() -> Path:
    if os.getenv('FINANCIAL_DATA_DIR'):
        return Path(os.environ['FINANCIAL_DATA_DIR']).expanduser().resolve()
    if sys.platform == 'win32':
        return Path(os.getenv('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'FinancialReportQA/data'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/FinancialReportQA/data'
    return ROOT / 'data/workspaces'


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


@contextmanager
def read_only_db(path: Path):
    if not path.is_file():
        raise ValueError(f'数据库不存在：{path}')
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    conn.execute('PRAGMA query_only=ON')
    try:
        yield conn
    finally:
        conn.close()


def snapshot_database(source: Path, target: Path, timeout: float = 30) -> dict:
    """SQLite Online Backup API copies committed state, including WAL commits."""
    if target.exists():
        raise FileExistsError(f'拒绝覆盖已有备份：{target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with read_only_db(source) as src:
        check = src.execute('PRAGMA quick_check').fetchone()[0]
        if check != 'ok':
            raise ValueError('源数据库完整性检查未通过：' + str(check))
        with closing(sqlite3.connect(target)) as dst:
            def progress(status, remaining, total):
                if time.monotonic() - started > timeout:
                    raise TimeoutError('数据库快照超过30秒，请停止写入后重试。')
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('备份数据库完整性检查未通过。')
            # The snapshot must be self-contained when moved to another OS;
            # do not carry a WAL-mode header that creates new sidecar files.
            dst.execute('PRAGMA journal_mode=DELETE')
    return {'size': target.stat().st_size, 'sha256': digest(target), 'kind': 'sqlite-online-backup'}


def copy_assets(source: Path, destination: Path, excluded: set[Path] | None = None) -> tuple[list[dict], list[str]]:
    source = source.resolve()
    if destination.resolve().is_relative_to(source):
        raise ValueError('目标目录不得放在源数据目录内，避免递归复制。')
    excluded = {path.resolve() for path in (excluded or set())}
    entries, skipped = [], []
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            skipped.append(str(path.relative_to(source)) + '（符号链接未复制）')
            continue
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(source):
            raise ValueError('资料路径超出源目录。')
        if path.resolve() in excluded or path.name.endswith(('-wal', '-shm', '-journal')):
            continue
        relative = path.relative_to(source)
        target = destination / relative
        if target.exists():
            raise FileExistsError(f'拒绝覆盖资料文件：{target}')
        before = path.stat()
        target.parent.mkdir(parents=True, exist_ok=True)
        with path.open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        after = path.stat()
        copied_hash = digest(target)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or copied_hash != digest(path):
            raise ValueError(f'备份期间文件发生变化，请暂停编辑后重试：{relative}')
        entries.append({'path': relative.as_posix(), 'size': target.stat().st_size,
                        'sha256': copied_hash, 'kind': 'file'})
    return entries, skipped


def write_json(path: Path, content: dict) -> None:
    with path.open('x', encoding='utf-8') as handle:
        json.dump(content, handle, ensure_ascii=False, indent=2)


def remap_file_paths(database: Path, original_root: str, copied_root: Path, actual_source: Path) -> dict:
    """Rebase only existing, explicitly copied attachments; never match basename alone."""
    prefix = original_root.replace('\\', '/').rstrip('/')
    case_insensitive = len(prefix) > 1 and prefix[1] == ':'
    mapped, unresolved = 0, []
    with closing(sqlite3.connect(database)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('report_files', 'metric_sources'):
            if table not in tables:
                continue
            columns = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
            if 'file_path' not in columns:
                continue
            for identifier, value in conn.execute(f'SELECT id,file_path FROM {table} WHERE file_path IS NOT NULL').fetchall():
                normalized = str(value).replace('\\', '/')
                compare, start = (normalized.casefold(), prefix.casefold()) if case_insensitive else (normalized, prefix)
                if compare.startswith(start + '/'):
                    suffix = normalized[len(prefix)+1:]
                elif not normalized.startswith('/') and ':' not in normalized:
                    suffix = normalized
                    root_name = actual_source.name + '/'
                    if suffix.startswith(root_name):
                        suffix = suffix[len(root_name):]
                else:
                    unresolved.append({'table': table, 'id': identifier, 'path': value})
                    continue
                candidate = (copied_root / suffix).resolve()
                if not candidate.is_relative_to(copied_root.resolve()) or not candidate.is_file():
                    unresolved.append({'table': table, 'id': identifier, 'path': value})
                    continue
                conn.execute(f'UPDATE {table} SET file_path=? WHERE id=?', (str(candidate), identifier))
                if table == 'report_files' and 'financial_metrics' in tables and {'company_id', 'report_year'} <= columns:
                    report = conn.execute('SELECT company_id,report_year FROM report_files WHERE id=?', (identifier,)).fetchone()
                    # Preserve exact legacy file provenance across relocation;
                    # free-form notes and mixed source summaries are untouched.
                    conn.execute('''UPDATE financial_metrics SET raw_source=?
                        WHERE company_id=? AND year=? AND raw_source=?''',
                        (str(candidate), report[0], report[1], value))
                mapped += 1
        conn.commit()
    return {'mapped_file_paths': mapped, 'unresolved_file_paths': unresolved}
