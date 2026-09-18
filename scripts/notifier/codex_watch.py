#!/usr/bin/env python3
"""CLI entrypoint for the Codex failed-turn watcher."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from coding_agent_notifier.codex_watch import watch_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(watch_main())
