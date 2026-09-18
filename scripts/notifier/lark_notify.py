#!/usr/bin/env python3
"""CLI entrypoint for Coding Agent Notifier Feishu/Lark webhook notifications."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    # Allow hook setups that run this script with an interpreter that has
    # requests available but the package itself not installed.
    sys.path.insert(0, str(_SRC))

from coding_agent_notifier.notifier import lark_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(lark_main())
