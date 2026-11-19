"""Run the newest installed copy of a hook.

A worktree keeps the harness that its branch held when it was made, while the main checkout moves
on. A hook that starts from the install of a worktree runs the main checkout's copy of the same
hook when that install is newer, so a fix reaches every session at once. The main checkout comes
from the `.git` file of the worktree, so the check runs no git command.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

INSTALL = (".claude", "agent-harness")
# Set on the hand-over, so the main checkout's copy never hands over again.
HANDED = "AH_HOOK_FROM_MAIN"


def version(install: Path) -> tuple[int, ...] | None:
    """The harness version that `<install>/lock.json` records, or None."""
    try:
        text = json.loads((install / "lock.json").read_text(encoding="utf-8")).get("version")
        return tuple(int(part) for part in str(text).split("."))
    except (OSError, ValueError, AttributeError):
        return None


def main_checkout(checkout: Path) -> Path | None:
    """The main checkout of the linked worktree `checkout`, or None for a main checkout."""
    marker = checkout / ".git"
    if not marker.is_file():
        return None
    text = marker.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text.removeprefix("gitdir:").strip())
    gitdir = gitdir if gitdir.is_absolute() else (checkout / gitdir).resolve()
    # A linked worktree's Git folder is `<main>/.git/worktrees/<name>`.
    return gitdir.parent.parent.parent if gitdir.parent.name == "worktrees" else None


def newer_hook(script: Path) -> Path | None:
    """The main checkout's copy of the installed hook `script`, when its install is newer."""
    install = script.parent.parent
    if script.parent.name != "hooks" or install.parts[-2:] != INSTALL:
        return None
    main = main_checkout(install.parent.parent)
    if main is None:
        return None
    theirs = main.joinpath(*INSTALL)
    target = theirs / "hooks" / script.name
    mine, newest = version(install), version(theirs)
    if mine is None or newest is None or newest <= mine or not target.is_file():
        return None
    return target


def hand_over() -> None:
    """Replace this process with the newer copy of the running hook, when one exists."""
    if os.environ.get(HANDED):
        return
    target = newer_hook(Path(os.path.abspath(sys.argv[0])))
    if target is None:
        return
    os.environ[HANDED] = "1"
    os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
