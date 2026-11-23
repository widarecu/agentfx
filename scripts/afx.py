#!/usr/bin/env python3
"""Agent-facing Linear commands (AFX-118).

    python scripts/afx.py start AFX-123
    python scripts/afx.py block AFX-123 "Which pair should this use?"

`start` claims a Ready ticket (Ready -> In Progress) and refuses any
other state, so a duplicate start never launches duplicate work.
`block` moves an active ticket to Blocked, adds `needs-wilson` and
posts the question once. Reads LINEAR_API_KEY from the environment.

Exit codes: 0 done, 1 refused (wrong state), 2 error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentfx.integrations import linear_delivery  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afx", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Claim a Ready ticket (Ready -> In Progress)")
    start.add_argument("issue", help="Ticket identifier, e.g. AFX-123")
    block = sub.add_parser("block", help="Block an active ticket on Wilson")
    block.add_argument("issue", help="Ticket identifier, e.g. AFX-123")
    block.add_argument("question", help="The question Wilson needs to answer")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    client: linear_delivery.LinearClient | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        client = client or linear_delivery.LinearClient.from_env()
        if args.command == "start":
            result = linear_delivery.start(client, args.issue)
        else:
            result = linear_delivery.block(client, args.issue, args.question)
    except (linear_delivery.LinearError, ValueError) as exc:
        print(f"afx {args.command}: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not result.ok:
        print(f"afx {args.command}: refused: {result.reason}", file=sys.stderr)
        return EXIT_REFUSED
    if result.changed:
        print(f"{result.identifier}: {result.from_state} -> {result.to_state}")
    else:
        print(f"{result.identifier}: already {result.to_state}; nothing to do")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
