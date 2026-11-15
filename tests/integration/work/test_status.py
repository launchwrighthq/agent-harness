"""`apply_git_evidence` runs git only in a worktree of the repository it reports on."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_harness.work import status

IDENTITY = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *IDENTITY, *args], check=True, capture_output=True)


def reported_row(worktree: Path) -> status.TaskRow:
    return status.TaskRow(
        task_id="a0000000000000001",
        kind="agent",
        description="x",
        model=None,
        agent_type=None,
        spec=None,
        section=None,
        worktree=str(worktree),
        spawned_at=AT,
        last_seen=AT,
        state="reported",
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "commit", "-q", "--allow-empty", "-m", "init")
    return root


def test_runs_no_git_in_a_worktree_path_of_another_repository(home: Path, tmp_path: Path) -> None:
    foreign = tmp_path / "foreign" / ".claude" / "worktrees" / "w"
    foreign.mkdir(parents=True)
    git(foreign, "init", "-q", "-b", "main")
    (foreign / "a.txt").write_text("x\n", encoding="utf-8")
    git(foreign, "add", "a.txt")
    git(foreign, "commit", "-q", "-m", "init")
    marker = tmp_path / "marker"
    monitor = tmp_path / "monitor.sh"
    monitor.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    monitor.chmod(0o755)
    git(foreign, "config", "core.fsmonitor", str(monitor))
    row = reported_row(foreign)

    status.apply_git_evidence([row], home)

    assert not marker.exists()
    assert (row.state, row.dirty) == ("reported", False)


def test_reads_a_worktree_of_the_repository_it_reports_on(home: Path) -> None:
    own = home / ".claude" / "worktrees" / "own"
    git(home, "worktree", "add", "-q", "-b", "own", str(own))
    row = reported_row(own)

    status.apply_git_evidence([row], home)

    assert row.state == "merged"
