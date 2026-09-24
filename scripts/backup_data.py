"""Create an immutable SQLite snapshot plus hashed attachment manifest."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.data_tools import default_data_dir, snapshot_database, copy_assets, utc_now, write_json


def backup(data_dir: Path, output: Path, workspace: str = 'main') -> Path:
    data_dir, output = data_dir.expanduser().resolve(), output.expanduser().resolve()
    if output.is_relative_to(data_dir):
        raise ValueError('备份输出必须放在数据目录之外。')
    if workspace not in {'main', 'evaluation', 'all'}:
        raise ValueError('备份仅支持统一资料库或内部评测数据库。')
    selected = ['main', 'evaluation'] if workspace == 'all' else [workspace]
    selected = [name for name in selected if (data_dir / name / 'investor_agent.db').is_file()]
    if not selected:
        raise ValueError('未找到指定资料库的 investor_agent.db；请核对 --data-dir 和 --workspace。')
    destination = output / ('backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {'format_version': 1, 'status': 'incomplete', 'created_at': utc_now(),
                'source_data_dir': str(data_dir), 'workspaces': [], 'files': [], 'skipped': []}
    try:
        for name in selected:
            source, target = data_dir / name, destination / name
            db = source / 'investor_agent.db'
            db_entry = snapshot_database(db, target / 'investor_agent.db')
            manifest['files'].append(dict(db_entry, path=f'{name}/investor_agent.db'))
            assets, skipped = copy_assets(source, target, {db})
            manifest['files'].extend(dict(entry, path=f'{name}/{entry["path"]}') for entry in assets)
            manifest['skipped'].extend(f'{name}/{entry}' for entry in skipped)
            manifest['workspaces'].append(name)
        manifest['status'] = 'complete'
        manifest['completed_at'] = utc_now()
        write_json(destination / 'manifest.json', manifest)
    except Exception as error:
        manifest['error'] = str(error)
        write_json(destination / 'INCOMPLETE.json', manifest)
        raise
    return destination


def main():
    parser = argparse.ArgumentParser(description='在线备份 SQLite 与上传资料，输出 SHA-256 清单；不删除或覆盖任何原文件。')
    parser.add_argument('--data-dir', type=Path, default=default_data_dir(), help='FINANCIAL_DATA_DIR 对应的持久数据上级目录')
    parser.add_argument('--workspace', choices=['main', 'evaluation', 'all'], default='main')
    parser.add_argument('--output', type=Path, required=True, help='数据目录之外的备份保存目录')
    args = parser.parse_args()
    try:
        print(backup(args.data_dir, args.output, args.workspace))
    except Exception as error:
        parser.exit(1, f'备份未完成：{error}\n')


if __name__ == '__main__':
    main()
