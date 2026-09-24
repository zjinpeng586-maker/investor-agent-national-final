"""Request-local workspace selection and explicit write permissions."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
_workspace = ContextVar('financial_workspace', default=None)
_data_root = ContextVar('financial_data_root', default=None)
_internal_write = ContextVar('financial_internal_write', default=False)
WORKSPACES = {'main': '统一资料库', 'evaluation': '内部评测数据库'}


def is_public_deployment() -> bool:
    # A bare cloud deployment is read-only until an operator explicitly selects local mode.
    return os.getenv('FINANCIAL_DEPLOYMENT', 'public').lower() != 'local'


def get_workspace() -> str:
    # Evaluation is available only through an explicit internal context. Old
    # environment defaults cannot select a different user-facing database.
    return _workspace.get() or 'main'


def configure_workspace(value: str) -> str:
    if value not in WORKSPACES:
        raise ValueError('未知资料库。')
    if value != 'main':
        raise ValueError('正常业务只能使用统一资料库。')
    _workspace.set(value)
    return value


def get_data_root() -> Path:
    if _data_root.get() is not None:
        return Path(_data_root.get())
    base = Path(os.getenv('FINANCIAL_DATA_DIR', str(ROOT / 'data' / 'workspaces'))).expanduser().resolve()
    return base / get_workspace()


def can_write() -> bool:
    return bool(_internal_write.get()) or (not is_public_deployment() and get_workspace() == 'main')


def require_write_access() -> None:
    if not can_write():
        raise PermissionError('当前部署为只读模式，无法执行数据导入或删除操作。')


@contextmanager
def internal_writes():
    """Trusted bootstrap/migration only; never exposed as a UI option."""
    token = _internal_write.set(True)
    try:
        yield
    finally:
        _internal_write.reset(token)


@contextmanager
def workspace_context(kind: str, root: str | Path | None = None, *, allow_writes: bool = False):
    """Isolate evaluation/backup work without mutating process-wide DB globals."""
    if kind not in WORKSPACES:
        raise ValueError('未知资料库。')
    if kind == 'evaluation':
        target = Path(root).resolve() if root is not None else (
            Path(os.getenv('FINANCIAL_DATA_DIR', str(ROOT / 'data' / 'workspaces'))).expanduser().resolve() / 'evaluation')
        active = get_data_root().resolve()
        main_root = Path(os.getenv('FINANCIAL_DATA_DIR', str(ROOT / 'data' / 'workspaces'))).expanduser().resolve() / 'main'
        if target == main_root or (get_workspace() == 'main' and target == active):
            raise ValueError('评测数据库不能与统一资料库共用目录。')
    ws = _workspace.set(kind)
    data = _data_root.set(Path(root).resolve() if root is not None else None)
    write = _internal_write.set(allow_writes)
    try:
        yield get_data_root()
    finally:
        _internal_write.reset(write)
        _data_root.reset(data)
        _workspace.reset(ws)


def resolve_stored_file(value: str) -> Path | None:
    """Only open files belonging to the current library or packaged samples."""
    raw = Path(value)
    candidates = [raw] if raw.is_absolute() else [get_data_root() / raw, ROOT / raw]
    current = get_data_root().resolve()
    packaged = {(ROOT / 'data' / name).resolve() for name in (
        'sample_byd.csv', 'sample_catl.csv', 'sample_changan.csv', 'sample_eve.csv',
        'sample_ganfeng.csv', 'sample_gotion.csv', 'sample_huayou.csv',
        'sample_new_energy_extended.csv', 'sample_saic.csv', 'sample_sunwoda.csv', 'sample_tianqi.csv')}
    for candidate in candidates:
        path = candidate.resolve()
        if path.is_file() and (path.is_relative_to(current) or path in packaged):
            return path
    return None
