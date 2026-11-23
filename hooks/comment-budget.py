#!/usr/bin/env python3
"""Holds a write to the comment budget when it is written (the `code-limits` rule).

    comment-budget.py PreToolUse    deny a Write or Edit that makes a comment block too long
    comment-budget.py PostToolUse   report each finding of the written file as added context
    comment-budget.py Stop          hold the turn once for the findings of the changed files

A matcher on tool names cannot see a file that a shell command wrote, so Stop measures each file
that changed since the base. `agent_harness.comments` measures, and `ah comments` reads the same
module, so the hook and the check agree. A Codex `apply_patch` arrives as an Edit with the path
only: it gets the report after the write, not the deny before it.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import HarnessError, comments, hookio  # noqa: E402

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


def state_file(payload: dict[str, Any], root: Path) -> Path:
    """The file that holds the findings Stop last held this session for, one per checkout."""
    session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or "none"
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/claude-{os.getuid()}"
    name = re.sub(r"[^\w-]", "_", str(session))
    checkout = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    return Path(base) / HOOK / f"{name}-{checkout}"


def stop(payload: dict[str, Any]) -> int:
    """Exit 2 with the findings of the changed files, once per set of findings.

    The state file is the loop guard: a Stop with the findings of the last held Stop lets the
    turn end. `stop_hook_active` is no guard, because another Stop hook sets it too.
    """
    root = checkout_of(payload)
    cfg = hookio.hook_config(HOOK, __file__)
    base = str(hookio.harness_value(cfg, "baseBranch") or "main")
    state = state_file(payload, root)
    try:
        found = comments.changed_findings(root, comments.budget_of(cfg), base)
        if not found:
            state.unlink(missing_ok=True)
            return 0
        mark = hashlib.sha256("\n".join(found).encode()).hexdigest()
        if state.exists() and state.read_text(encoding="utf-8") == mark:
            return 0
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(mark, encoding="utf-8")
    except (HarnessError, OSError, UnicodeError) as error:
        # Without a verdict or a saved mark the hold could repeat, so the turn ends.
        print(f"{HOOK}: {error}; no verdict", file=sys.stderr)
        return 0
    held = [*(f"{HOOK}: {f}" for f in found), comments.RULE]
    print("\n".join([*held, "Correct each file, then end the turn."]), file=sys.stderr)
    return 2


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "PostToolUse"
    payload = hookio.read_payload(HOOK)
    if payload is not None and event == "Stop":
        sys.exit(stop(payload))
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
    project = comments.budget_of(cfg)
    if path.startswith("..") or not comments.in_scope(path, project):
        return
    base = str(hookio.harness_value(cfg, "baseBranch") or "main")
    try:
        budget = comments.caps_for(path, project)
        fresh = comments.is_new(root, base, path, project)
    except HarnessError as error:
        print(f"{HOOK}: {error}; no verdict", file=sys.stderr)
        return

    if event == "PreToolUse":
        current = file.read_text(encoding="utf-8") if file.exists() else None
        text = resulting_text(str(tool), data, current)
        if text is None:
            return
        after = comments.measure_text(text, path)
        if after.comment + after.code < budget["minLines"]:
            return
        # A block that the file already holds is the floor: an edit near old prose passes.
        before = comments.measure_text(current or "", path)
        floors = comments.block_checks(before, fresh, budget)
        checks = zip(comments.block_checks(after, fresh, budget), floors, strict=True)
        over = [(name, lines, cap) for (name, lines, cap), f in checks if lines > max(cap, f[1])]
        if over:
            name, lines, cap = over[0]
            hookio.deny(
                f"{HOOK}: {path} would get a {lines}-line {name}, cap {cap}. {comments.RULE}"
            )
        return
    if not file.exists():
        return
    found = comments.findings(path, comments.measure_file(file, path), fresh, budget)
    if found:
        hookio.context("PostToolUse", "\n".join([*(f"{HOOK}: {f}" for f in found), comments.RULE]))


if __name__ == "__main__":
    main()
