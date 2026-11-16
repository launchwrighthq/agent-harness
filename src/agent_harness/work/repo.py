"""`ah work-status --repo`: what the repository holds in flight, read from git and the store.

It reads no transcript, so it answers in Codex and for a peer session too: every worktree with
its drift from the base branch, every unsettled spec of the spec folders with its saved audit,
and every live goal.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_harness import config
from agent_harness.spec import layout, store
from agent_harness.spec.goal import LIVE
from agent_harness.work import status


@dataclass(frozen=True)
class Worktree:
    path: str
    branch: str
    ahead: int
    behind: int
    dirty: int
    subject: str
    when: str


@dataclass(frozen=True)
class Spec:
    path: str
    status: str | None
    audit: str


def _counts(base: str, branch: str, root: Path) -> tuple[int, int]:
    """Commits behind and ahead of `base`, or zero for each when git cannot say."""
    found = status._git(["rev-list", "--left-right", "--count", f"{base}...{branch}"], root)
    parts = (found or "").split()
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return 0, 0
    return int(parts[0]), int(parts[1])


def _worktree(block: str, base: str, root: Path) -> Worktree | None:
    fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
    path = fields.get("worktree")
    if path is None:
        return None
    branch = fields.get("branch", "").removeprefix("refs/heads/") or "(detached)"
    behind, ahead = _counts(base, fields.get("HEAD", branch), root)
    changed = (status._git(["status", "--porcelain"], Path(path)) or "").splitlines()
    last = (status._git(["log", "-1", "--format=%cI%n%s"], Path(path)) or "\n").split("\n", 1)
    return Worktree(path, branch, ahead, behind, len(changed), last[1], last[0])


def worktrees(root: Path, base: str) -> list[Worktree]:
    """Every worktree that `git worktree list` names, the main checkout first."""
    listed = status._git(["worktree", "list", "--porcelain"], root) or ""
    found = [_worktree(block, base, root) for block in listed.split("\n\n")]
    return [tree for tree in found if tree is not None]


def unsettled_specs(root: Path) -> list[Spec]:
    """Every spec and plan in the spec folders, the archives left out, that is not settled."""
    rows: list[Spec] = []
    for folder in layout.for_root(root).settling():
        for file in sorted((root / folder).glob("*.md")):
            state = store.status_of(file)
            if state in store.SETTLED:
                continue
            audit, _, _ = status.audit_state(store.stem_of(file), root)
            rows.append(Spec(file.relative_to(root).as_posix(), state, audit))
    return rows


def live_goals(root: Path) -> list[dict[str, Any]]:
    """The goal of each session that the `goal-spec` Stop hook still enforces."""
    folder = store.goal_path("x", root).parent
    goals: list[dict[str, Any]] = []
    for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        goal = store.read_json(file) or {}
        if goal.get("status") in LIVE:
            fields = {key: goal.get(key) for key in ("specs", "level", "status")}
            goals.append({"session": file.stem, **fields})
    return goals


def collect(root: Path) -> dict[str, Any]:
    base = str(config.load(root).get("harness.baseBranch"))
    return {
        "checkout": str(root),
        "base": base,
        "worktrees": [asdict(tree) for tree in worktrees(root, base)],
        "specs": [asdict(spec) for spec in unsettled_specs(root)],
        "goals": live_goals(root),
    }


def render(state: dict[str, Any]) -> str:
    lines = [f"# work-status: {state['checkout']}", f"base {state['base']}", ""]
    lines += ["| Worktree | Branch | Ahead | Behind | Dirty files | Last commit |"]
    lines += ["|---|---|---|---|---|---|"]
    for tree in state["worktrees"]:
        name = Path(tree["path"]).name
        lines.append(
            f"| {name} | {tree['branch']} | {tree['ahead']} | {tree['behind']} | "
            f"{tree['dirty']} | {tree['when'][:16]} {tree['subject']} |"
        )
    lines += ["", "| Spec | Status | Audit |", "|---|---|---|"]
    lines += [f"| {s['path']} | {s['status'] or 'none'} | {s['audit']} |" for s in state["specs"]]
    if not state["specs"]:
        lines.append("| no unsettled spec or plan | | |")
    lines.append("")
    goals = state["goals"]
    lines.append("Goals:" if goals else "Goals: none")
    lines += [
        f"- session {goal['session']}: {', '.join(goal['specs'] or [])}, level {goal['level']}"
        for goal in goals
    ]
    return "\n".join(lines)


def main(root: Path, as_json: bool) -> int:
    state = collect(root)
    print(json.dumps(state, indent=2) if as_json else render(state))
    return 0
