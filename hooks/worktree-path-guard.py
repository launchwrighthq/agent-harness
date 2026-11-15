#!/usr/bin/env python3
"""Refuse `git worktree add` at a path outside `<repo>/.claude/worktrees/`.

The rule is stated in `.claude/rules/lw/git-workflow.md`. An agent still puts a sibling
directory beside the checkout when nothing refuses it.

Silent for every other command. An `allow` from a PreToolUse hook would bypass the permission
rules rather than defer to them, so this only ever denies or says nothing.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import hookio  # noqa: E402

HOOK = "worktree-path-guard"

# Flags of `git worktree add` that take a value, so the word after them is not the path.
VALUED = {"-b", "-B", "--reason", "--orphan"}

# Git's own global options, which sit between `git` and the subcommand.
GLOBAL_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

WORD = re.compile(r"(?:[^\s'\"]|'[^']*'|\"[^\"]*\")+")


def strip(word: str) -> str:
    found = re.fullmatch(r"(['\"])(.*)\1", word, re.DOTALL)
    return found.group(2) if found else word


def words(text: str) -> list[str]:
    """The words of a command, quotes removed, with an attached `-C<path>` split into two."""
    out: list[str] = []
    for raw in WORD.findall(text):
        word = strip(raw)
        attached = re.fullmatch(r"(-[Cc])(.+)", word)
        if attached:
            out.extend([attached.group(1), strip(attached.group(2))])
        else:
            out.append(word)
    return out


def target(text: str, base: str) -> tuple[str, str] | None:
    """The path `git worktree add` would create, and the directory it resolves against."""
    parts = words(text)
    at: str | None = None
    i = 0
    if not parts or (parts[0] != "git" and not parts[0].endswith("/git")):
        return None
    i += 1
    while i < len(parts):
        word = parts[i]
        if not word.startswith("-"):
            break
        name, inline = word.split("=", 1) if "=" in word else (word, None)
        if name == "-C":
            if inline is not None:
                at = inline
            else:
                i += 1
                at = parts[i] if i < len(parts) else None
        elif name in GLOBAL_VALUED and inline is None:
            i += 1
        i += 1
    if parts[i : i + 2] != ["worktree", "add"]:
        return None
    i += 2
    while i < len(parts):
        word = parts[i]
        if word in VALUED:
            i += 1
        elif not word.startswith("-"):
            return word, os.path.normpath(os.path.join(base, at or "."))
        i += 1
    return None


def worktrees_of(at: str) -> str:
    """The worktree folder of the checkout `git worktree add` would attach to."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=at,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return os.path.normpath(os.path.join(common, "..", ".claude", "worktrees"))


def main() -> None:
    payload = hookio.read_payload(HOOK)
    if payload is None or payload.get("tool_name") != "Bash":
        return
    fields = payload.get("tool_input")
    fields = fields if isinstance(fields, dict) else {}
    # The tool's directory comes from the payload: the Codex adapter runs hooks from the
    # checkout root.
    base = os.path.abspath(str(fields.get("workdir") or payload.get("cwd") or os.getcwd()))
    found = target(str(fields.get("command") or ""), base)
    if found is None:
        return
    path_word, at = found
    try:
        home = worktrees_of(at)
    except (OSError, subprocess.CalledProcessError) as error:
        # No repository at that path: git itself refuses the command, so nothing needs a guard.
        print(f"{HOOK}: no repository at {at} ({str(error).splitlines()[0]})", file=sys.stderr)
        return
    path = os.path.normpath(path_word if os.path.isabs(path_word) else os.path.join(at, path_word))
    # A DIRECT child, because the rule is `.claude/worktrees/<name>`: a nested path is as hard to
    # find as a sibling, and `../..` from a worktree lands back inside this directory.
    inside = os.path.relpath(path, home)
    if (
        inside != "."
        and not inside.startswith("..")
        and not os.path.isabs(inside)
        and "/" not in inside
    ):
        return
    hookio.deny(
        f"A worktree goes in {home}/<name> and nowhere else. This command would put one at "
        f"{path}, beside or outside the checkout, where a sweep reads it as stale scratch and "
        "where nothing ties it to this repository for the next session. Re-run it with a path "
        "under .claude/worktrees/, or use EnterWorktree, which puts it there. "
        ".claude/rules/lw/git-workflow.md carries the rule."
    )


if __name__ == "__main__":
    main()
