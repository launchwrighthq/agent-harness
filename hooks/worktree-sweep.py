#!/usr/bin/env python3
"""Start the worktree sweep at the start and the end of a session, when the project turns it on.

`[branches.autoApply] staleHours` above 0 turns the sweep on (`agent_harness.work.sweep`). The
hook starts the sweep as a process of its own, at most once per `everyMinutes`, and does not wait
for it. The sweep writes its lines to `worktree-sweep/log` in the state folder.

The hook never fails a session: an error prints one line on stderr, and the exit code is 0.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import HarnessError, hookio  # noqa: E402
from agent_harness.work import sweep  # noqa: E402

HOOK = "worktree-sweep"


def main() -> None:
    try:
        if sys.argv[1:2] == [sweep.RUN]:
            sweep.run(Path(sys.argv[2]))
        else:
            sweep.start(hookio.project_root(__file__), os.path.abspath(__file__))
    except (HarnessError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"{HOOK}: {' '.join(str(error).split())}", file=sys.stderr)


if __name__ == "__main__":
    main()
