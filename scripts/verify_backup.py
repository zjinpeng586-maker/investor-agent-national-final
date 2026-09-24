"""Verify a complete manifest before relying on or restoring a backup."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.data_tools import digest


def verify(folder: Path) -> dict:
    folder = folder.expanduser().resolve()
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'complete' or manifest.get('format_version') != 1 or not manifest.get('files'):
        raise ValueError('不是已完成的受支持备份。')
    names = manifest.get('workspaces') or []
    # Old backup names remain readable solely for compatibility restoration.
    if not names or len(names) != len(set(names)) or not set(names) <= {'main', 'demo', 'personal', 'evaluation'}:
        raise ValueError('清单的资料库列表无效。')
    seen = set()
    for entry in manifest['files']:
        relative = entry['path']
        path = (folder / relative).resolve()
        if path in seen or not path.is_relative_to(folder) or not path.is_file() or Path(relative).parts[0] not in names:
            raise ValueError('清单含重复、越界或缺失路径。')
        seen.add(path)
        if path.stat().st_size != entry['size'] or digest(path) != entry['sha256']:
            raise ValueError(f'备份内容与清单不一致：{relative}')
    actual = set()
    for name in names:
        for path in (folder / name).rglob('*'):
            if path.is_symlink():
                raise ValueError('备份中存在符号链接，停止恢复。')
            if path.is_file():
                actual.add(path.resolve())
    if actual != seen:
        raise ValueError('备份目录含未列入清单的额外文件，或清单遗漏资料。')
    return manifest


def main():
    parser = argparse.ArgumentParser(description='逐文件校验数据库和附件备份的 SHA-256 与大小，不改动文件。')
    parser.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = verify(args.backup)
        print(f'OK: {len(manifest["files"])} files verified; workspaces: {", ".join(manifest["workspaces"])}')
    except Exception as error:
        parser.exit(1, f'备份校验未通过：{error}\n')


if __name__ == '__main__':
    main()
