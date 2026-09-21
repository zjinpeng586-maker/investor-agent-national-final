from __future__ import annotations

import json
import contextlib
import sys

from core.evaluation import run_benchmark


if __name__ == '__main__':
    with contextlib.redirect_stdout(sys.stderr):
        result = run_benchmark()
    print(json.dumps(result, ensure_ascii=False, indent=2))
