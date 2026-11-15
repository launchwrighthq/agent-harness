"""work-status and branch-audit: the work in progress and what each branch holds.

`[branches]` holds the facts of `ah branch-audit`: the remote base (empty means
`origin/<harness.baseBranch>`), the refs never offered, the worktree folder, the folder names that
hold rebuildable files, and the two age gates.
"""

from __future__ import annotations

from agent_harness import config

config.register_defaults(
    "branches",
    {
        "remoteBase": "",
        "protect": [],
        "worktreeRoot": ".claude/worktrees",
        "rebuildable": [
            "node_modules",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        ],
        # A worktree, a branch or a stray folder touched inside this many hours is in progress,
        # and a stray folder is offered only past it.
        "staleHours": 24,
        # A live lock whose session transcript is idle this many days is a stale session.
        "sessionIdleDays": 2,
    },
)


def main(argv: list[str]) -> int:
    from agent_harness.work import status

    return status.main(argv)


def branch_audit(argv: list[str]) -> int:
    from agent_harness.work import branches

    return branches.main(argv)
