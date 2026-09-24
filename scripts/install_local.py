"""First-time installation only. Launch scripts never install packages."""
from pathlib import Path
import argparse
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='Create .venv and install the locked runtime dependencies with Python 3.11.')
    parser.add_argument('--check-only', action='store_true', help='Validate interpreter and manifest without changing files or downloading.')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        parser.error('Python 3.11 is required. Run py -3.11 or python3.11.')
    requirements = ROOT / 'requirements.txt'
    if not requirements.is_file():
        parser.error('requirements.txt is missing; extract the complete project first.')
    for line in requirements.read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '==' not in line:
            parser.error('A runtime dependency is not pinned: ' + line)
    if args.check_only:
        print('OK: Python 3.11 and exact runtime requirements. No installation performed.')
        return
    destination = ROOT / '.venv'
    python = destination / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not destination.exists():
        venv.EnvBuilder(with_pip=True).create(destination)
    if not python.is_file():
        parser.error('Existing .venv is incomplete. Keep your data; create a fresh project/environment directory.')
    subprocess.run([str(python), '-c', 'import sys; assert sys.version_info[:2] == (3,11), "Existing .venv must use Python 3.11"'], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], cwd=ROOT, check=True)
    subprocess.run([str(python), '-m', 'pip', 'check'], cwd=ROOT, check=True)
    print('Installation complete. Daily use: run_streamlit.bat (Windows) or bash run_streamlit_mac.sh (macOS).')


if __name__ == '__main__':
    main()
