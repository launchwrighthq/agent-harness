"""`ah work-status --repo`: the work a repository holds, read from git and the store alone."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"
IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *IDENTITY, *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """main with a draft spec, a settled spec and a plan; a worktree two commits ahead, dirty."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root / "docs/specs/2026-01-01-draft-design.md", "# D\n\nStatus: draft\n")
    write(root / "docs/specs/2026-01-02-done-design.md", "# E\n\nStatus: implemented\n")
    write(root / "docs/specs/archive/2026-01-03-old-design.md", "# O\n\nStatus: draft\n")
    write(root / "docs/superpowers/plans/2026-01-04-plan.md", "# P\n\nStatus: draft\n")
    write(root / ".gitignore", ".claude/\n")
    git(root, "add", "docs", ".gitignore")
    git(root, "commit", "-q", "-m", "specs")
    tree = root / ".claude" / "worktrees" / "feat"
    git(root, "worktree", "add", "-q", "-b", "feat", str(tree))
    for name in ("one", "two"):
        write(tree / f"{name}.txt", f"{name}\n")
        git(tree, "add", f"{name}.txt")
        git(tree, "commit", "-q", "-m", f"add {name}")
    write(tree / "loose.txt", "loose\n")
    return root


def ah(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AH_HANDED_OVER", "AH_ROOT", "CLAUDE_CODE_SESSION_ID", "AGENT_STATE_DIR")
    }
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "work-status", "--repo", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def report(repo: Path) -> dict[str, object]:
    done = ah(repo, "--json")
    assert done.returncode == 0, done.stderr
    data: dict[str, object] = json.loads(done.stdout)
    return data


def test_lists_every_worktree_with_its_drift_dirt_and_last_commit(repo: Path) -> None:
    trees = report(repo)["worktrees"]

    assert trees == [
        {
            "path": str(repo.resolve()),
            "branch": "main",
            "ahead": 0,
            "behind": 0,
            "dirty": 0,
            "subject": "specs",
            "when": trees[0]["when"],  # type: ignore[index]
        },
        {
            "path": str(repo.resolve() / ".claude" / "worktrees" / "feat"),
            "branch": "feat",
            "ahead": 2,
            "behind": 0,
            "dirty": 1,
            "subject": "add two",
            "when": trees[1]["when"],  # type: ignore[index]
        },
    ]


def test_lists_each_unsettled_spec_of_the_spec_folders_with_its_audit(repo: Path) -> None:
    head = git(repo, "rev-parse", "main")
    rows = [{"id": "R1", "requirement": "x", "state": "missing"}]
    audit = {"stem": "2026-01-01-draft-design", "head": head, "base": "main", "rows": rows}
    write(repo / ".claude/state/spec-audit/2026-01-01-draft-design.json", json.dumps(audit))

    specs = report(repo)["specs"]

    assert specs == [
        {
            "path": "docs/specs/2026-01-01-draft-design.md",
            "status": "draft",
            "audit": "1 open, 0 blocked",
        },
        {
            "path": "docs/superpowers/plans/2026-01-04-plan.md",
            "status": "draft",
            "audit": "not audited",
        },
    ]


def test_leaves_out_the_reports_of_a_qa_folder(repo: Path) -> None:
    config = '[specs]\ndirs = ["docs/specs", "docs/qa"]\n\n[specs.kinds."docs/qa"]\nkind = "qa"\n'
    write(repo / ".agents/harness.toml", config)
    write(repo / "docs/qa/2026-01-05-pass.md", "# Q\n\nStatus: untested\n")
    write(repo / "docs/qa/2026-01-06-done.md", "# R\n\nStatus: tested\n")

    specs = report(repo)["specs"]

    assert isinstance(specs, list)
    assert [row["path"] for row in specs] == ["docs/specs/2026-01-01-draft-design.md"]


def test_lists_the_live_goal_of_every_session(repo: Path) -> None:
    goals = repo / ".claude/state/goal-spec"
    live = {
        "specs": ["docs/specs/2026-01-01-draft-design.md"],
        "level": "merged",
        "status": "active",
    }
    write(goals / "s-live.json", json.dumps(live))
    write(goals / "s-met.json", json.dumps({**live, "status": "met"}))

    assert report(repo)["goals"] == [{"session": "s-live", **live}]


def test_prints_the_three_tables_with_no_session_or_transcript(repo: Path) -> None:
    done = ah(repo)

    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[0] == f"# work-status: {repo.resolve()}"
    feat = next(line for line in lines if line.startswith("| feat |"))
    assert feat.startswith("| feat | feat | 2 | 0 | 1 | ")
    assert feat.endswith(" add two |")
    assert "| docs/superpowers/plans/2026-01-04-plan.md | draft | not audited |" in lines
    assert lines[-1] == "Goals: none"
