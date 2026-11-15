#!/usr/bin/env python3
"""Holds a write to the comment budget when it is written (the `code-limits` rule).

    comment-budget.py PreToolUse    deny a Write or Edit that makes a comment block too long
    comment-budget.py PostToolUse   report each finding of the written file as added context

`agent_harness.comments` measures, and `ah comments` reads the same module, so the hook and the
check agree. A Codex `apply_patch` arrives as an Edit with the path only: it gets the report
after the write, not the deny before it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import comments, hookio  # noqa: E402

HOOK = "comment-budget"


def checkout_of(payload: dict[str, Any]) -> Path:
    """The checkout that holds the written file: the payload folder when it is a git checkout."""
    cwd = payload.get("cwd")
    folder = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    try:
        top = subprocess.run(
            ["git", "-C", folder, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return Path(folder)
    return Path(top.stdout.strip()) if top.returncode == 0 else Path(folder)


def is_new(root: Path, base: str, path: str) -> bool:
    """True when the base branch does not hold `path`. Without git or the branch, a file is old."""
    try:
        held = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{base}:{path}"],
            capture_output=True,
            check=False,
        )
        branch = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", base],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return branch.returncode == 0 and held.returncode != 0


def resulting_text(tool: str, data: dict[str, Any], current: str | None) -> str | None:
    """The text an Edit or a Write leaves on disk, or None when the payload does not say."""
    if tool == "Write":
        content = data.get("content")
        return content if isinstance(content, str) else None
    was, now = data.get("old_string"), data.get("new_string")
    if not isinstance(was, str) or not isinstance(now, str) or current is None:
        return None
    if data.get("replace_all"):
        return current.replace(was, now)
    at = current.find(was)
    return None if at == -1 else current[:at] + now + current[at + len(was) :]


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "PostToolUse"
    payload = hookio.read_payload(HOOK)
    data = payload.get("tool_input") if payload else None
    if payload is None or not isinstance(data, dict) or not isinstance(data.get("file_path"), str):
        return
    tool = payload.get("tool_name")
    if tool not in ("Write", "Edit", "MultiEdit"):
        return
    root = checkout_of(payload)
    file = Path(os.path.join(root, data["file_path"]))
    path = os.path.relpath(file, root).replace("\\", "/")
    cfg = hookio.hook_config(HOOK, __file__)
    budget = comments.budget_of(cfg)
    if path.startswith("..") or not comments.in_scope(path, budget):
        return
    fresh = is_new(root, str(hookio.harness_value(cfg, "baseBranch") or "main"), path)

    if event == "PreToolUse":
        current = file.read_text(encoding="utf-8") if file.exists() else None
        text = resulting_text(str(tool), data, current)
        if text is None:
            return
        after = comments.measure_text(text, path)
        if after.comment + after.code < budget["minLines"]:
            return
        cap = comments.block_cap(fresh, budget)
        # A block that the file already holds is the floor: an edit near old prose passes.
        held = 0 if current is None else comments.measure_text(current, path).longest
        if after.longest > max(cap, held):
            hookio.deny(
                f"{HOOK}: {path} would get a {after.longest}-line comment block, cap {cap}. "
                f"{comments.RULE}"
            )
        return
    if not file.exists():
        return
    found = comments.findings(path, comments.measure_file(file, path), fresh, budget)
    if found:
        hookio.context("PostToolUse", "\n".join([*(f"{HOOK}: {f}" for f in found), comments.RULE]))


if __name__ == "__main__":
    main()
