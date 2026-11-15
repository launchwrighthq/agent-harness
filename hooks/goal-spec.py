#!/usr/bin/env python3
"""The `/lw-goal-spec` Stop hook: hold a turn until the saved spec audit allows it to end.

    goal-spec.py Stop                      the hook; the payload arrives on stdin
    goal-spec.py start|status|clear|check  the same commands as `ah spec goal`

The logic lives in `agent_harness.spec.goal`. A crash in the Stop hook prints its error and lets
the turn end, because a hook that cannot finish must not trap the session in its turn.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness.spec import goal  # noqa: E402


def run(argv: list[str]) -> int:
    if argv[:1] != ["Stop"]:
        return goal.main(argv)
    try:
        return goal.main(argv)
    # Before a goal is read there is no state to record the error in, and the turn must end.
    except (Exception, SystemExit) as error:  # noqa: BLE001 - printed to stderr, then exit 0
        print(f"goal-spec: the Stop hook failed and let the turn end: {error}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
