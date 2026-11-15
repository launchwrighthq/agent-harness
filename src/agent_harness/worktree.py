"""`ah worktree add <name> [--from <ref>]` and `ah worktree remove <name>`.

A worktree of a repository goes in `<main checkout>/.claude/worktrees/<name>`, on a new branch
with the same name (`.claude/rules/lw/git-workflow.md`). `add` makes one from the base branch, or
from `--from <ref>`, and prints its path. `remove` removes a clean worktree of this repository
and keeps its branch. Neither takes a path, so neither can put a worktree anywhere else.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

from agent_harness import HarnessError, config

WORKTREES = Path(".claude") / "worktrees"
# One folder name, which is also the branch name: no `/`, and no leading `.` or `-`.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise HarnessError("git is not installed; ah worktree needs it") from error


def _must(cwd: Path, *args: str) -> str:
    done = _git(cwd, *args)
    if done.returncode != 0:
        raise HarnessError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def main_checkout(cwd: Path) -> Path:
    """The main checkout of the repository that holds `cwd`, from a worktree too."""
    common = Path(_must(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if common.name != ".git":
        raise HarnessError(f"{common} is a bare repository; ah worktree needs a main checkout")
    return common.parent


def check_name(name: str) -> None:
    if not NAME.fullmatch(name) or name.endswith(".lock") or ".." in name:
        raise HarnessError(
            f"{name!r} is one folder name, not a path. The worktree goes in "
            f"{WORKTREES}/<name> of the main checkout, on a branch of the same name. Use "
            "letters, digits, `.`, `_` and `-`, and start with a letter or a digit."
        )


def add(cwd: Path, name: str, ref: str | None) -> Path:
    """Create `<main>/.claude/worktrees/<name>` on the new branch `<name>` at `ref`."""
    check_name(name)
    root = main_checkout(cwd)
    folder = root / WORKTREES / name
    if folder.exists():
        raise HarnessError(f"{folder} exists. Pick another name.")
    if _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}").returncode == 0:
        raise HarnessError(f"the branch {name} exists. Pick another name.")
    start = ref or str(config.load(root).get("harness.baseBranch"))
    _must(root, "worktree", "add", "--quiet", "-b", name, str(folder), start)
    return folder


def _worktrees(root: Path) -> list[Path]:
    listing = _must(root, "worktree", "list", "--porcelain")
    prefix = "worktree "
    return [Path(line[len(prefix) :]) for line in listing.splitlines() if line.startswith(prefix)]


def remove(cwd: Path, name: str) -> tuple[Path, str]:
    """Remove the clean worktree `<main>/.claude/worktrees/<name>`. Returns it and its branch."""
    check_name(name)
    root = main_checkout(cwd)
    folder = root / WORKTREES / name
    if folder.resolve() not in [path.resolve() for path in _worktrees(root)]:
        raise HarnessError(f"{folder} is no worktree of this repository ({root}).")
    if Path(os.path.realpath(cwd)).is_relative_to(folder.resolve()):
        raise HarnessError(
            f"this command runs inside it ({cwd}). Run it from the main checkout or another "
            "worktree."
        )
    changed = _must(folder, "status", "--porcelain", "--untracked-files=all").splitlines()
    if changed:
        raise HarnessError(
            f"{folder} holds {len(changed)} changed or untracked files. Commit or remove them "
            "first; ah worktree remove never discards work."
        )
    branch = _must(folder, "branch", "--show-current") or "(detached)"
    _must(root, "worktree", "remove", str(folder))
    return folder, branch


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ah worktree",
        description="Add or remove a worktree in .claude/worktrees/ of this repository.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    made = sub.add_parser("add", help="add <name> on a new branch <name>, and print its path")
    made.add_argument("name")
    made.add_argument("--from", dest="ref", help="the start point (default: harness.baseBranch)")
    gone = sub.add_parser("remove", help="remove the clean worktree <name>; its branch stays")
    gone.add_argument("name")
    args = parser.parse_args(argv)
    cwd = Path.cwd()
    if args.action == "add":
        print(add(cwd, args.name, args.ref))
        return 0
    folder, branch = remove(cwd, args.name)
    print(f"removed {folder}; the branch {branch} stays")
    return 0
