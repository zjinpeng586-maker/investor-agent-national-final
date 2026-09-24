from __future__ import annotations

import json
import contextlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    with contextlib.redirect_stdout(sys.stderr):
        from core.evaluation import run_benchmark

        result = run_benchmark()
    print(json.dumps(result, ensure_ascii=False, indent=2))
