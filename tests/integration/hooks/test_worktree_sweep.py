"""worktree-sweep starts the sweep of `[branches.autoApply]` and never fails a session."""

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import hook_project

HOOK = "worktree-sweep.py"
ON = "staleHours = 1\neveryMinutes = 30\n"
IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}
OLD_HOURS = 240


def git(root: Path, *args: str, hours_ago: int = OLD_HOURS, check: bool = True) -> str:
    stamp = f"{int(time.time()) - hours_ago * 3600} +0000"
    env = {**os.environ, **IDENTITY, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, env=env, check=check
    )
    return done.stdout.strip()


def make_project(tmp_path: Path, auto: str = ON) -> Path:
    """A repository on `trunk` with the hooks installed and `auto` as `[branches.autoApply]`."""
    config = f'[harness]\nbaseBranch = "trunk"\n\n[branches.autoApply]\n{auto}'
    project = hook_project.make_project(Path(os.path.realpath(tmp_path)) / "project", config)
    git(project, "checkout", "--quiet", "-b", "trunk")
    (project / "a.txt").write_text("base\n", encoding="utf-8")
    git(project, "add", "a.txt")
    git(project, "commit", "--quiet", "-m", "base")
    return project


def add_worktree(project: Path, name: str, hours_ago: int = OLD_HOURS) -> Path:
    tree = project / ".claude" / "worktrees" / name
    git(project, "worktree", "add", "--quiet", "-b", name, str(tree), hours_ago=hours_ago)
    return tree


def has_branch(project: Path, name: str) -> bool:
    ref = f"refs/heads/{name}"
    return git(project, "rev-parse", "--verify", "--quiet", ref, check=False) != ""


def state(project: Path) -> Path:
    return project / ".claude" / "state" / "worktree-sweep"


def run_hook(project: Path, event: str = "SessionStart") -> subprocess.CompletedProcess[str]:
    env = hook_project.hook_env(project, "sweep-test", project.parent)
    env.pop("AGENT_STATE_DIR", None)
    env["HOME"] = str(project.parent / "home")
    return hook_project.run_hook(project, HOOK, event, {"hook_event_name": event}, env)


def sweep_log(project: Path) -> str:
    """The log of the sweep that the hook started. The sweep holds the lock until it ends."""
    with open(state(project) / "lock", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
    return (state(project) / "log").read_text(encoding="utf-8")


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
def test_removes_a_merged_clean_worktree_and_its_branch(tmp_path: Path, event: str) -> None:
    project = make_project(tmp_path)
    tree = add_worktree(project, "done")

    done = run_hook(project, event)
    log = sweep_log(project)

    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    assert log == (
        "worktree-sweep: 2 rows, stale window 1 hours\n"
        f"    ok git worktree remove {tree}\n"
        "    ok git branch -d done\n"
    )
    assert not tree.exists()
    assert not has_branch(project, "done")


def test_starts_no_sweep_with_the_default_stale_hours(tmp_path: Path) -> None:
    project = make_project(tmp_path, auto="")
    tree = add_worktree(project, "done")

    done = run_hook(project)

    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    assert not state(project).exists()
    assert tree.is_dir()


def test_keeps_a_worktree_that_is_unmerged_dirty_or_inside_the_stale_window(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    done_tree = add_worktree(project, "done")
    unmerged = add_worktree(project, "unmerged")
    (unmerged / "new.txt").write_text("new\n", encoding="utf-8")
    git(unmerged, "add", "new.txt")
    git(unmerged, "commit", "--quiet", "-m", "new")
    dirty = add_worktree(project, "dirty")
    (dirty / "notes.txt").write_text("not committed\n", encoding="utf-8")
    old = time.time() - OLD_HOURS * 3600
    os.utime(dirty / "notes.txt", (old, old))
    fresh = add_worktree(project, "fresh", hours_ago=0)

    run_hook(project)
    log = sweep_log(project)

    assert log.startswith("worktree-sweep: 2 rows, stale window 1 hours\n")
    assert not done_tree.exists()
    assert (unmerged.is_dir(), dirty.is_dir(), fresh.is_dir()) == (True, True, True)
    assert [has_branch(project, name) for name in ("unmerged", "dirty", "fresh")] == [True] * 3


def test_keeps_a_worktree_whose_owner_session_wrote_inside_the_stale_window(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    owned = add_worktree(project, "owned")
    git_dir = Path(git(owned, "rev-parse", "--absolute-git-dir"))
    (git_dir / "lw-owner").write_text("owner-session\n", encoding="utf-8")
    transcripts = project.parent / "home" / ".claude" / "projects" / "another-checkout"
    transcripts.mkdir(parents=True)
    (transcripts / "owner-session.jsonl").write_text("{}\n", encoding="utf-8")
    idle = add_worktree(project, "idle")
    idle_dir = Path(git(idle, "rev-parse", "--absolute-git-dir"))
    (idle_dir / "lw-owner").write_text("idle-session\n", encoding="utf-8")
    (transcripts / "idle-session.jsonl").write_text("{}\n", encoding="utf-8")
    old = time.time() - OLD_HOURS * 3600
    os.utime(transcripts / "idle-session.jsonl", (old, old))

    run_hook(project)
    log = sweep_log(project)

    assert log.startswith("worktree-sweep: 2 rows, stale window 1 hours\n")
    assert owned.is_dir()
    assert has_branch(project, "owned")
    assert not idle.exists()


def test_leaves_a_merged_branch_that_no_worktree_holds(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    git(project, "branch", "loose")

    run_hook(project)
    log = sweep_log(project)

    assert log == "worktree-sweep: 0 rows, stale window 1 hours\n"
    assert has_branch(project, "loose")


def test_starts_no_second_sweep_inside_every_minutes(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    tree = add_worktree(project, "done")
    state(project).mkdir(parents=True)
    (state(project) / "stamp").write_text(f"{time.time() - 60}\n", encoding="utf-8")

    done = run_hook(project)

    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    assert not (state(project) / "log").exists()
    assert tree.is_dir()


def test_starts_a_sweep_when_the_last_one_is_older_than_every_minutes(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    tree = add_worktree(project, "done")
    state(project).mkdir(parents=True)
    (state(project) / "stamp").write_text(f"{time.time() - 31 * 60}\n", encoding="utf-8")

    run_hook(project)
    sweep_log(project)

    assert not tree.exists()


def test_starts_no_sweep_while_another_session_holds_the_lock(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    tree = add_worktree(project, "done")
    state(project).mkdir(parents=True)

    with open(state(project) / "lock", "w", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        done = run_hook(project)

    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    assert not (state(project) / "log").exists()
    assert tree.is_dir()


@pytest.mark.parametrize(
    ("auto", "message"),
    [
        (
            "staleHours = -1\n",
            "worktree-sweep: config key branches.autoApply.staleHours must be a number of 0 or"
            " more, not -1\n",
        ),
        (
            'staleHours = 1\neveryMinutes = "often"\n',
            "worktree-sweep: config key branches.autoApply.everyMinutes must be a number of 0 or"
            " more, not 'often'\n",
        ),
        ("staleHours = [\n", None),
    ],
)
def test_prints_one_line_and_exits_0_on_a_config_error(
    tmp_path: Path, auto: str, message: str | None
) -> None:
    project = make_project(tmp_path, auto=auto)
    tree = add_worktree(project, "done")

    done = run_hook(project)

    assert (done.returncode, done.stdout) == (0, "")
    assert done.stderr.count("\n") == 1
    assert done.stderr.startswith("worktree-sweep: ")
    assert message is None or done.stderr == message
    assert tree.is_dir()
