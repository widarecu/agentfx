#!/usr/bin/env python3
"""Prepare the dev virtualenv in Claude Code cloud sessions."""

import json
import os
import subprocess
from pathlib import Path


def warn(message: str) -> None:
    print(
        json.dumps(
            {
                "systemMessage": message,
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": message,
                },
            }
        )
    )


def main() -> None:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    ruff = root / ".venv" / "bin" / "ruff"
    if ruff.is_file():
        return
    if os.environ.get("CLAUDE_CODE_REMOTE") == "true":
        try:
            result = subprocess.run(
                ["make", "dev"],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=540,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0 and ruff.is_file():
            return
        warn(
            "Cloud setup could not finish `make dev`. Python edit checks are unavailable; run `make dev` from the agentfx folder to see the error."
        )
    else:
        warn(
            "Python edit checks are unavailable without .venv/bin/ruff. Run `make dev` from the agentfx folder to enable formatting and lint."
        )


if __name__ == "__main__":
    main()
