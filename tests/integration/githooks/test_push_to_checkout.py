"""push-to-checkout refuses a landing that would write over a peer's path, and only then."""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[3] / "githooks"
IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **IDENTITY},
        check=check,
    )


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_landing(tmp_path: Path, incoming: str) -> tuple[Path, Path]:
    """A checkout on `trunk` that ignores `*.log` and `secret/`, and a worktree that adds
    `incoming`."""
    repo = Path(os.path.realpath(tmp_path)) / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=trunk")
    write(repo, ".gitignore", "*.log\nsecret/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "--quiet", "-m", "init")
    git(repo, "config", "receive.denyCurrentBranch", "updateInstead")
    git(repo, "config", "core.hooksPath", str(HOOKS))
    tree = repo.parent / "wt"
    git(repo, "worktree", "add", "--quiet", "-b", "feat", str(tree))
    write(tree, incoming, "branch\n")
    git(tree, "add", "-f", incoming)
    git(tree, "commit", "--quiet", "-m", "add")
    return repo, tree


def peer_file(rel: str) -> Callable[[Path], None]:
    return lambda repo: write(repo, rel, "peer\n")


@pytest.mark.parametrize(
    ("incoming", "prepare", "in_the_way", "kept"),
    [
        ("x.log", peer_file("x.log"), "x.log", "x.log"),
        ("secret/key", peer_file("secret/key"), "secret/key", "secret/key"),
        ("secret", peer_file("secret/key"), "secret", "secret/key"),
        ("y.log/inner.txt", peer_file("y.log"), "y.log", "y.log"),
        ("notes.txt", peer_file("notes.txt"), "notes.txt", "notes.txt"),
        ("f/inner.txt", peer_file("f"), "f", "f"),
    ],
)
def test_refuses_a_landing_over_an_untracked_or_ignored_path(
    tmp_path: Path,
    incoming: str,
    prepare: Callable[[Path], None],
    in_the_way: str,
    kept: str,
) -> None:
    repo, tree = make_landing(tmp_path, incoming)
    prepare(repo)
    before = git(repo, "rev-parse", "trunk").stdout

    done = git(tree, "push", ".", "HEAD:refs/heads/trunk", check=False)

    assert done.returncode == 1, done.stderr
    assert f"land:   {in_the_way}\n" in done.stderr
    assert (repo / kept).read_text(encoding="utf-8") == "peer\n"
    assert git(repo, "rev-parse", "trunk").stdout == before


def test_lands_a_file_into_a_folder_that_holds_an_ignored_file(tmp_path: Path) -> None:
    repo, tree = make_landing(tmp_path, "secret/readme.md")
    write(repo, "secret/key", "peer\n")

    done = git(tree, "push", ".", "HEAD:refs/heads/trunk", check=False)

    assert done.returncode == 0, done.stderr
    assert (repo / "secret" / "readme.md").read_text(encoding="utf-8") == "branch\n"
    assert (repo / "secret" / "key").read_text(encoding="utf-8") == "peer\n"


def make_change(tmp_path: Path) -> tuple[Path, Path, str]:
    """A checkout on `trunk` with `notes.txt`, a worktree that changes it, and the new commit."""
    repo, tree = make_landing(tmp_path, "added.txt")
    write(repo, "notes.txt", "base\n")
    git(repo, "add", "notes.txt")
    git(repo, "commit", "--quiet", "-m", "notes")
    git(tree, "rebase", "--quiet", "trunk")
    write(tree, "notes.txt", "branch\n")
    git(tree, "commit", "--quiet", "-am", "change")
    return repo, tree, git(tree, "rev-parse", "HEAD").stdout.strip()


@pytest.mark.parametrize(
    "stage",
    [
        ["checkout", "feat", "--", "notes.txt"],
        ["read-tree", "feat"],
    ],
    ids=["index-and-working-tree", "index-only"],
)
def test_lands_on_a_base_that_already_holds_the_landing(tmp_path: Path, stage: list[str]) -> None:
    repo, tree, new = make_change(tmp_path)
    git(repo, *stage)

    done = git(tree, "push", ".", "HEAD:refs/heads/trunk", check=False)

    assert done.returncode == 0, done.stderr
    assert git(repo, "rev-parse", "trunk").stdout.strip() == new
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "branch\n"
    assert git(repo, "status", "--porcelain").stdout == ""


@pytest.mark.parametrize("staged", [False, True], ids=["working-tree", "index"])
def test_refuses_a_peer_change_that_differs_from_both_sides(tmp_path: Path, staged: bool) -> None:
    repo, tree, _ = make_change(tmp_path)
    write(repo, "notes.txt", "peer\n")
    if staged:
        git(repo, "add", "notes.txt")
    before = git(repo, "rev-parse", "trunk").stdout

    done = git(tree, "push", ".", "HEAD:refs/heads/trunk", check=False)

    assert done.returncode == 1, done.stderr
    assert "land:   notes.txt\n" in done.stderr
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "peer\n"
    assert git(repo, "rev-parse", "trunk").stdout == before
