"""Restore a verified backup to a new directory, never over an existing library."""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.data_tools import snapshot_database, copy_assets, remap_file_paths, write_json, utc_now
from scripts.verify_backup import verify


def restore(backup: Path, data_dir: Path) -> Path:
    backup, data_dir = backup.expanduser().resolve(), data_dir.expanduser().resolve()
    manifest = verify(backup)
    if data_dir.exists():
        raise FileExistsError('恢复目标已存在；必须选择一个全新的数据目录，不能覆盖已有资料。')
    if data_dir.is_relative_to(backup):
        raise ValueError('恢复目标不能放在备份目录内部。')
    data_dir.mkdir(parents=True, exist_ok=False)
    results = []
    try:
        for name in manifest['workspaces']:
            if name not in {'main', 'demo', 'personal', 'evaluation'}:
                raise ValueError('备份清单含未知资料库。')
            source, target = backup / name, data_dir / name
            db = source / 'investor_agent.db'
            snapshot_database(db, target / 'investor_agent.db')
            copy_assets(source, target, {db})
            paths = remap_file_paths(target / 'investor_agent.db',
                                    str(manifest['source_data_dir']).replace('\\', '/').rstrip('/') + '/' + name,
                                    target, source)
            results.append({'workspace': name, **paths})
        # Legacy backup compatibility: merge the restored backups into main,
        # retaining the restored source directories as migration backups.
        if set(manifest['workspaces']) & {'main', 'demo', 'personal'}:
            from core.storage import workspace_context
            from core.db import init_db
            with workspace_context('main', root=data_dir / 'main', allow_writes=True):
                init_db()
        write_json(data_dir / 'restore_manifest.json', {'status': 'complete', 'created_at': utc_now(), 'workspaces': results})
    except Exception as error:
        write_json(data_dir / 'RESTORE_INCOMPLETE.json', {'status': 'incomplete', 'error': str(error)})
        raise
    return data_dir


def main():
    parser = argparse.ArgumentParser(description='校验并恢复到全新目录；更新来源文件路径，不覆盖已有资料库。')
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(restore(args.backup, args.data_dir))
    except Exception as error:
        parser.exit(1, f'恢复未完成：{error}\n不要启动含 RESTORE_INCOMPLETE.json 的目标目录。\n')


if __name__ == '__main__':
    main()
