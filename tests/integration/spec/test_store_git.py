import os
import subprocess
from pathlib import Path

import pytest

from agent_harness.spec import store


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("AGENT_STATE_DIR", raising=False)
    root = Path(os.path.realpath(tmp_path)) / "repo"
    root.mkdir()
    git(root, "init", "--quiet", "--initial-branch=trunk")
    (root / ".agents").mkdir()
    (root / ".agents" / "harness.toml").write_text(
        '[harness]\nbaseBranch = "trunk"\n\n[specs]\ndirs = ["design"]\nauditDir = "audits"\n',
        encoding="utf-8",
    )
    (root / "design" / "archive").mkdir(parents=True)
    (root / "design" / "archive" / "old.md").write_text("# Old\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "init")
    return root


def test_keeps_the_audit_folder_of_the_main_checkout_for_a_worktree(repo: Path) -> None:
    tree = repo / ".claude" / "worktrees" / "wt"
    git(repo, "worktree", "add", "--quiet", "-b", "wt", str(tree))

    assert store.state_dir(repo) == repo / "audits"
    assert store.state_dir(tree) == repo / "audits"


def test_finds_a_spec_by_stem_in_the_archive_of_a_configured_folder(repo: Path) -> None:
    assert store.resolve_spec("old", repo) == repo / "design" / "archive" / "old.md"
    assert store.resolve_spec("missing", repo) is None


def test_reads_the_default_base_branch_from_the_config(repo: Path) -> None:
    assert store.default_base(repo) == "trunk"
    assert store.branch_commit("trunk", repo) is not None
