"""The installed `.githooks/` runs the global hooks, the harness hooks and `.git/hooks/` in turn."""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

Ah = Callable[..., subprocess.CompletedProcess[str]]

IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


class Git:
    """Runs git with a global config file of the test, so the user's global hooks stay out."""

    def __init__(self, global_config: Path) -> None:
        self.env = {
            **os.environ,
            **IDENTITY,
            "GIT_CONFIG_GLOBAL": str(global_config),
            "GIT_CONFIG_NOSYSTEM": "1",
        }

    def __call__(
        self, root: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            env=self.env,
            check=check,
        )


def hook(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def installed(repo: Path, package: Path, ah: Ah, tmp_path: Path) -> tuple[Path, Path]:
    """The repository with the harness installed, one commit and a bare remote `origin`."""
    hook(package / "githooks/commit-msg", f'echo harness >>"{tmp_path}/log"')
    done = ah("install", "--project", "--from", str(package))
    assert done.returncode == 0, done.stderr
    git = Git(tmp_path / "empty.gitconfig")
    (tmp_path / "empty.gitconfig").write_text("", encoding="utf-8")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--quiet", "--bare", str(remote))
    git(repo, "checkout", "--quiet", "-b", "main")
    git(repo, "remote", "add", "origin", str(remote))
    return repo, remote


def commit(git: Git, repo: Path, name: str) -> str:
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "--quiet", "-m", f"add {name}")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def test_runs_the_global_pre_push_with_its_stdin_and_stops_on_its_refusal(
    installed: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, remote = installed
    global_hooks = tmp_path / "global-hooks"
    hook(global_hooks / "pre-push", f'echo "$@" >"{tmp_path}/args"\ncat >"{tmp_path}/stdin"')
    hook(repo / ".git/hooks/pre-push", f'echo local >>"{tmp_path}/log"')
    config = tmp_path / "global.gitconfig"
    config.write_text(f"[core]\n\thooksPath = {global_hooks}\n", encoding="utf-8")
    git = Git(config)
    first = commit(git, repo, "a.txt")

    pushed = git(repo, "push", "--quiet", "origin", "main", check=False)
    hook(global_hooks / "pre-push", "cat >/dev/null\nexit 1")
    commit(git, repo, "b.txt")
    refused = git(repo, "push", "--quiet", "origin", "main", check=False)

    assert pushed.returncode == 0, pushed.stderr
    assert (tmp_path / "args").read_text() == f"origin {remote}\n"
    assert (tmp_path / "stdin").read_text() == (
        f"refs/heads/main {first} refs/heads/main {'0' * 40}\n"
    )
    assert refused.returncode != 0
    assert git(remote, "rev-parse", "main").stdout.strip() == first
    assert "local" not in (tmp_path / "log").read_text()


def test_runs_the_global_hook_before_the_harness_hook(
    installed: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, _ = installed
    global_hooks = tmp_path / "global-hooks"
    hook(global_hooks / "commit-msg", f'echo global >>"{tmp_path}/log"')
    config = tmp_path / "global.gitconfig"
    config.write_text(f"[core]\n\thooksPath = {global_hooks}\n", encoding="utf-8")

    commit(Git(config), repo, "a.txt")

    assert (tmp_path / "log").read_text() == "global\nharness\n"


def skip_global_hooks(repo: Path, package: Path, ah: Ah) -> None:
    (repo / ".agents").mkdir(exist_ok=True)
    (repo / ".agents/harness.toml").write_text('[harness.githooks]\nglobal = "skip"\n')
    done = ah("install", "--project", "--from", str(package))
    assert done.returncode == 0, done.stderr


def test_runs_no_global_hook_and_the_local_git_hook_when_the_config_skips_them(
    installed: tuple[Path, Path], package: Path, ah: Ah, tmp_path: Path
) -> None:
    repo, _ = installed
    skip_global_hooks(repo, package, ah)
    global_hooks = tmp_path / "global-hooks"
    hook(global_hooks / "commit-msg", f'echo global >>"{tmp_path}/log"')
    hook(repo / ".git/hooks/commit-msg", f'echo local >>"{tmp_path}/log"')
    config = tmp_path / "global.gitconfig"
    config.write_text(f"[core]\n\thooksPath = {global_hooks}\n", encoding="utf-8")

    commit(Git(config), repo, "a.txt")

    assert (tmp_path / "log").read_text() == "harness\nlocal\n"


def test_skips_a_global_pre_push_that_would_refuse_the_push(
    installed: tuple[Path, Path], package: Path, ah: Ah, tmp_path: Path
) -> None:
    repo, remote = installed
    skip_global_hooks(repo, package, ah)
    global_hooks = tmp_path / "global-hooks"
    hook(global_hooks / "pre-push", "cat >/dev/null\nexit 1")
    hook(repo / ".git/hooks/pre-push", f'cat >"{tmp_path}/stdin"')
    config = tmp_path / "global.gitconfig"
    config.write_text(f"[core]\n\thooksPath = {global_hooks}\n", encoding="utf-8")
    git = Git(config)
    first = commit(git, repo, "a.txt")

    pushed = git(repo, "push", "--quiet", "origin", "main", check=False)

    assert pushed.returncode == 0, pushed.stderr
    assert git(remote, "rev-parse", "main").stdout.strip() == first
    assert (tmp_path / "stdin").read_text() == (
        f"refs/heads/main {first} refs/heads/main {'0' * 40}\n"
    )


def test_runs_the_global_hook_again_when_the_config_goes_back_to_run(
    installed: tuple[Path, Path], package: Path, ah: Ah, tmp_path: Path
) -> None:
    repo, _ = installed
    skip_global_hooks(repo, package, ah)
    (repo / ".agents/harness.toml").write_text('[harness.githooks]\nglobal = "run"\n')
    assert ah("install", "--project", "--from", str(package)).returncode == 0
    global_hooks = tmp_path / "global-hooks"
    hook(global_hooks / "commit-msg", f'echo global >>"{tmp_path}/log"')
    config = tmp_path / "global.gitconfig"
    config.write_text(f"[core]\n\thooksPath = {global_hooks}\n", encoding="utf-8")

    commit(Git(config), repo, "a.txt")

    assert (tmp_path / "log").read_text() == "global\nharness\n"


def test_runs_the_local_git_hook_once_when_no_global_path_is_set(
    installed: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, _ = installed
    hook(repo / ".git/hooks/pre-push", f'cat >/dev/null\necho local >>"{tmp_path}/pushes"')
    git = Git(tmp_path / "empty.gitconfig")
    commit(git, repo, "a.txt")

    pushed = git(repo, "push", "--quiet", "origin", "main", check=False)

    assert pushed.returncode == 0, pushed.stderr
    assert (tmp_path / "pushes").read_text() == "local\n"


def test_updates_the_checkout_on_a_push_when_no_hook_handles_it(
    installed: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, _ = installed
    git = Git(tmp_path / "empty.gitconfig")
    commit(git, repo, "a.txt")
    tree = tmp_path / "wt"
    git(repo, "worktree", "add", "--quiet", "-b", "feat", str(tree))
    commit(git, tree, "b.txt")

    pushed = git(tree, "push", "--quiet", ".", "HEAD:refs/heads/main", check=False)

    assert pushed.returncode == 0, pushed.stderr
    assert (repo / "b.txt").read_text() == "b.txt\n"
    assert git(repo, "status", "--porcelain", "--untracked-files=no").stdout == ""


def test_runs_the_harness_push_to_checkout_of_the_package_through_the_dispatcher(
    repo: Path, ah: Ah, tmp_path: Path
) -> None:
    assert ah("install", "--project").returncode == 0
    git = Git(tmp_path / "empty.gitconfig")
    (tmp_path / "empty.gitconfig").write_text("", encoding="utf-8")
    git(repo, "checkout", "--quiet", "-b", "main")
    commit(git, repo, "a.txt")
    tree = tmp_path / "wt"
    git(repo, "worktree", "add", "--quiet", "-b", "feat", str(tree))
    commit(git, tree, "b.txt")
    # Git alone refuses a push over any staged file. The harness hook refuses only an overlap.
    (repo / "peer.txt").write_text("peer\n", encoding="utf-8")
    git(repo, "add", "peer.txt")

    pushed = git(tree, "push", "--quiet", ".", "HEAD:refs/heads/main", check=False)

    assert pushed.returncode == 0, pushed.stderr
    assert (repo / "b.txt").read_text() == "b.txt\n"
    assert git(repo, "diff", "--cached", "--name-only").stdout == "peer.txt\n"
