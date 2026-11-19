"""`ah worktree add` and `ah worktree remove` in a temporary repository, and a landing from one."""

import os
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2]
LAUNCHER = PACKAGE / "bin" / "ah"
LAND = PACKAGE / "skills" / "lw-land" / "land.sh"
IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **IDENTITY},
        check=True,
    )
    return done.stdout.strip()


def run_ah(cwd: Path, *args: str, session: str | None = None) -> subprocess.CompletedProcess[str]:
    skip = ("AH_HANDED_OVER", "AH_ROOT", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
    env = {k: v for k, v in os.environ.items() if k not in skip}
    if session is not None:
        env["CLAUDE_CODE_SESSION_ID"] = session
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "worktree", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**env, **IDENTITY},
        check=False,
    )


def owner(folder: Path) -> str | None:
    marker = Path(git(folder, "rev-parse", "--absolute-git-dir")) / "lw-owner"
    return marker.read_text(encoding="utf-8").strip() if marker.exists() else None


def make_repo(tmp_path: Path) -> Path:
    repo = Path(os.path.realpath(tmp_path)) / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=trunk")
    (repo / ".agents").mkdir()
    (repo / ".agents" / "harness.toml").write_text(
        '[harness]\nbaseBranch = "trunk"\ngateCommand = "true"\n', encoding="utf-8"
    )
    (repo / ".gitignore").write_text("/.claude/worktrees/\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "init")
    return repo


def test_adds_a_worktree_on_a_new_branch_from_the_base_branch(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo, "switch", "--quiet", "-c", "elsewhere")
    git(repo, "commit", "--quiet", "--allow-empty", "-m", "not on trunk")

    done = run_ah(repo, "add", "fix-a")

    tree = repo / ".claude" / "worktrees" / "fix-a"
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{tree}\n"
    assert git(tree, "branch", "--show-current") == "fix-a"
    assert git(tree, "rev-parse", "HEAD") == git(repo, "rev-parse", "trunk")


def test_adds_a_worktree_from_another_ref_and_from_inside_a_worktree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    run_ah(repo, "add", "first")
    first = repo / ".claude" / "worktrees" / "first"
    git(first, "commit", "--quiet", "--allow-empty", "-m", "on first")

    done = run_ah(first, "add", "second", "--from", "first")

    second = repo / ".claude" / "worktrees" / "second"
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{second}\n"
    assert git(second, "rev-parse", "HEAD") == git(first, "rev-parse", "HEAD")


def test_refuses_a_name_that_is_a_path_or_that_is_taken(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo, "branch", "taken")

    answers = {name: run_ah(repo, "add", name) for name in ("../out", "a/b", ".hidden", "taken")}

    for name, done in answers.items():
        assert (name, done.returncode) == (name, 1)
    assert "is one folder name, not a path" in answers["../out"].stderr
    assert "is one folder name, not a path" in answers["a/b"].stderr
    assert "is one folder name, not a path" in answers[".hidden"].stderr
    assert "branch taken exists" in answers["taken"].stderr
    assert not (repo / ".claude" / "worktrees").exists()


def test_removes_a_clean_worktree_and_keeps_its_branch(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    run_ah(repo, "add", "done-a")
    tree = repo / ".claude" / "worktrees" / "done-a"

    done = run_ah(repo, "remove", "done-a")

    assert done.returncode == 0, done.stderr
    assert done.stdout == f"removed {tree}; the branch done-a stays\n"
    assert not tree.exists()
    assert git(repo, "branch", "--list", "done-a") == "done-a"


def test_refuses_to_remove_a_dirty_or_unknown_worktree_or_the_current_one(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    run_ah(repo, "add", "dirty")
    tree = repo / ".claude" / "worktrees" / "dirty"
    (tree / "notes.txt").write_text("work\n", encoding="utf-8")
    run_ah(repo, "add", "here")
    here = repo / ".claude" / "worktrees" / "here"

    dirty = run_ah(repo, "remove", "dirty")
    unknown = run_ah(repo, "remove", "none")
    current = run_ah(here, "remove", "here")

    assert dirty.returncode == 1
    assert f"{tree} holds 1 changed or untracked files" in dirty.stderr
    assert tree.is_dir()
    assert unknown.returncode == 1
    assert "is no worktree of this repository" in unknown.stderr
    assert current.returncode == 1
    assert "runs inside it" in current.stderr
    assert here.is_dir()


def test_lands_a_branch_from_a_worktree_that_it_made(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo, "config", "receive.denyCurrentBranch", "updateInstead")
    git(repo, "config", "core.hooksPath", str(PACKAGE / "githooks"))
    made = run_ah(repo, "add", "feature-a")
    # A failed add prints no path, and an empty path is the folder of the test run itself.
    assert made.returncode == 0, made.stderr
    tree = repo / ".claude" / "worktrees" / "feature-a"
    assert made.stdout == f"{tree}\n"
    (tree / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(tree, "add", "feature.txt")
    git(tree, "commit", "--quiet", "-m", "feature")

    done = subprocess.run(
        ["bash", str(LAND)],
        cwd=tree,
        capture_output=True,
        text=True,
        env={**os.environ, **IDENTITY},
        check=False,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "feature\n"


def test_records_the_session_that_adds_a_worktree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    run_ah(repo, "add", "mine", session="session-a")
    run_ah(repo, "add", "plain")

    assert (owner(repo / ".claude/worktrees/mine"), owner(repo / ".claude/worktrees/plain")) == (
        "session-a",
        None,
    )


def test_claims_a_worktree_with_no_owner_and_refuses_one_of_another_session(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    git(repo, "worktree", "add", "--quiet", "-b", "plain", str(repo / ".claude/worktrees/plain"))
    run_ah(repo, "add", "theirs", session="session-b")

    claimed = run_ah(repo, "claim", "plain", session="session-a")
    refused = run_ah(repo, "claim", "theirs", session="session-a")

    assert claimed.returncode == 0, claimed.stderr
    assert owner(repo / ".claude/worktrees/plain") == "session-a"
    assert refused.returncode != 0
    assert "belongs to the session session-b" in refused.stderr
    assert owner(repo / ".claude/worktrees/theirs") == "session-b"
