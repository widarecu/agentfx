#!/usr/bin/env python3
"""PostToolUse hook: run ruff on a Python file Claude just edited.

Exit code 2 feeds the findings back to Claude so it fixes them before
moving on. Does nothing for non-Python files, files outside the repo,
or when the virtualenv (and so ruff) is not installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    ruff = os.path.join(root, ".venv", "bin", "ruff")
    if not path.endswith(".py") or not os.path.isfile(path) or not os.path.isfile(ruff):
        return 0
    if not os.path.realpath(path).startswith(os.path.realpath(root) + os.sep):
        return 0

    result = subprocess.run(
        [ruff, "check", "--quiet", path],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=root,
    )
    if result.returncode != 0:
        print(f"ruff found problems in {path}:", file=sys.stderr)
        print(result.stdout or result.stderr, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
