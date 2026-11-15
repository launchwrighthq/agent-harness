#!/usr/bin/env python3
"""Run a harness hook with Codex's event and session context.

`ah install --project` installs this file at `.claude/agent-harness/codex/run-hook.py` and points
every `.codex/hooks.json` entry at it. A project at the top of its checkout gets:

    python3 "$(git rev-parse --show-toplevel)/.claude/agent-harness/codex/run-hook.py" \
        <script> <event>

A project in the subfolder `<folder>` of the checkout names its own adapter:

    python3 "$(git rev-parse --show-toplevel)/<folder>/.claude/agent-harness/codex/run-hook.py" \
        <script> <event>

It turns the Codex payload into the Claude Code shape that the hooks read, runs the hook with
the interpreter of its suffix, and merges the answers:

- the hook scripts come from the project that holds this file, never from a path in the
  payload. The project is a checkout root, or a project folder inside a checkout (see
  `own_root`). A payload `workdir` or `cwd` inside `<root>/.claude/worktrees/<name>/` selects that
  worktree only when git reports the same common directory for it as for the root. That
  worktree's copy of the script runs when it has one. Any other directory is data that the hooks
  read from the payload;
- `CLAUDE_PROJECT_DIR` is that checkout, and `CLAUDE_CODE_SESSION_ID` comes from `session_id`;
- `exec_command` and `shell_command` become `Bash`, with `cmd` copied to `command`;
- an `apply_patch` becomes one `Edit` event per `*** Add/Update/Delete File` path;
- a `deny` or a `block` passes through at once. Else the first `ask` becomes a deny, and else
  every `additionalContext` is joined. Nothing shows that Codex honors the `ask` decision, so the
  adapter fails closed: the deny keeps the reason of the hook and says how the user goes on.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# The engine root: the folder above codex/. Its src/ holds the engine.
ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE / "src"))

from agent_harness import HarnessError  # noqa: E402
from agent_harness.config import CONFIG_FILE, ROOT_ENV  # noqa: E402
from agent_harness.install.plan import ENGINE_DIR, LOCK_FILE, interpreter_for  # noqa: E402

# What the adapter adds to the reason of an ask that it turns into a deny.
ASK_UNDER_CODEX = (
    " Codex does not confirm a hook ask, so the Codex adapter denies the command. To see the"
    " value, the user runs the reveal themselves, or confirms it in Claude Code."
)

PATCH_PATH = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)


def git(cwd: Path, *args: str) -> Path:
    out = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    return Path(out).resolve()


def own_root() -> Path:
    """The project that holds this file. It is the only source of hook scripts it trusts.

    The project is the root of a git checkout, or a folder inside one that is a project of its
    own: the folder that `AH_ROOT` names, or a folder with `.agents/harness.toml` or the lockfile
    of an install. A copy of this file anywhere else is no installed adapter, so it refuses.
    """
    root = ENGINE.parent.parent
    if root / ENGINE_DIR != ENGINE:
        raise SystemExit(f"run-hook.py: expected this file at <project>/{ENGINE_DIR}/codex/")
    try:
        top = git(root, "--show-toplevel")
    except (OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"run-hook.py: {root} is not a git checkout ({error})") from error
    if top == root:
        return root
    named = os.environ.get(ROOT_ENV, "")
    if named:
        if Path(named).resolve() == root:
            return root
        raise SystemExit(
            f"run-hook.py: {ROOT_ENV} names {named}, but this adapter belongs to {root}"
        )
    if (root / CONFIG_FILE).is_file() or (root / LOCK_FILE).is_file():
        return root
    raise SystemExit(
        f"run-hook.py: expected {root}/{ENGINE_DIR}/codex/run-hook.py at the root of a checkout"
        f" or of a project in it, not in {top}: {root} has no {CONFIG_FILE} or {LOCK_FILE}"
    )


def checkout_for(root: Path, cwd: str) -> Path:
    """`root`, or the worktree of the same repository under `root/.claude/worktrees/` with `cwd`.

    `cwd` comes from the payload, so it only selects between these trusted checkouts. A path
    anywhere else, or a directory there that is not a worktree of this repository, gives `root`.
    """
    worktrees = root / ".claude" / "worktrees"
    try:
        inside = Path(cwd).resolve().relative_to(worktrees)
    except (OSError, RuntimeError, ValueError):
        return root
    if not inside.parts:
        return root
    tree = worktrees / inside.parts[0]
    try:
        if not tree.is_dir() or git(tree, "--show-toplevel") != tree:
            return root
        if git(tree, "--git-common-dir") != git(root, "--git-common-dir"):
            return root
    except (OSError, subprocess.SubprocessError) as error:
        print(
            f"run-hook.py: {tree} is not a worktree of {root} ({error}); using {root}",
            file=sys.stderr,
        )
        return root
    return tree


def events_for(payload: dict[str, Any], cwd: str) -> list[dict[str, Any]]:
    """The Claude Code shaped events of one Codex payload."""
    tool = payload.get("tool_name")
    fields = payload.setdefault("tool_input", {})
    if tool in ("exec_command", "shell_command"):
        payload["tool_name"] = "Bash"
        fields["command"] = fields.get("command", fields.get("cmd", ""))
    if tool != "apply_patch":
        return [payload]
    patch = fields.get("command", fields.get("input", fields.get("patch", "")))
    return [
        {**payload, "tool_name": "Edit", "tool_input": {"file_path": str(Path(cwd) / path)}}
        for path in PATCH_PATH.findall(patch)
    ]


def ask_as_deny(result: dict[str, Any]) -> dict[str, Any]:
    """The answer `result` with its `ask` decision turned into a deny that keeps the reason."""
    output = result.get("hookSpecificOutput", {})
    reason = str(output.get("permissionDecisionReason") or "")
    denied = {**output, "permissionDecision": "deny"}
    denied["permissionDecisionReason"] = (reason + ASK_UNDER_CODEX).strip()
    return {**result, "hookSpecificOutput": denied}


def main() -> int:
    if len(sys.argv) < 2:
        print("run-hook.py: usage: run-hook.py <script> [event]", file=sys.stderr)
        return 1
    script, *args = sys.argv[1:]
    if script.startswith("/") or ".." in Path(script).parts or not script.startswith(".claude/"):
        print(f"run-hook.py: {script} is not a hook path under .claude/", file=sys.stderr)
        return 1
    try:
        interpreter = interpreter_for(script)
    except HarnessError as error:
        print(f"run-hook.py: {error}", file=sys.stderr)
        return 1
    if shutil.which(interpreter) is None:
        print(
            f"run-hook.py: {interpreter} is not installed, so the hook {script} did not run",
            file=sys.stderr,
        )
        return 1
    root = own_root()
    payload = json.load(sys.stdin)
    fields = payload.get("tool_input") or {}
    cwd = str(fields.get("workdir") or payload.get("cwd") or root)
    checkout = checkout_for(root, cwd)
    file = checkout / script if (checkout / script).is_file() else root / script
    if not file.is_file():
        print(f"run-hook.py: {file} is missing; run ah install --project", file=sys.stderr)
        return 1
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(checkout)}
    if session := payload.get("session_id"):
        env["CLAUDE_CODE_SESSION_ID"] = session
    event_name = payload.get("hook_event_name") or (args[0] if args else "PreToolUse")
    contexts = []
    asked: dict[str, Any] | None = None
    for event in events_for(payload, cwd):
        done = subprocess.run(
            [interpreter, str(file), *args],
            input=json.dumps(event),
            text=True,
            cwd=checkout,
            env=env,
            check=False,
            capture_output=True,
        )
        if done.stderr:
            sys.stderr.write(done.stderr)
        if done.returncode:
            sys.stdout.write(done.stdout)
            return done.returncode
        if not done.stdout.strip():
            continue
        try:
            result = json.loads(done.stdout)
        except json.JSONDecodeError:
            contexts.append(done.stdout.strip())
            continue
        output = result.get("hookSpecificOutput", {})
        if output.get("permissionDecision") == "deny" or result.get("decision") == "block":
            print(json.dumps(result))
            return 0
        # An ask waits for the other events, because a deny of a later file wins over it.
        if output.get("permissionDecision") == "ask":
            asked = asked or result
        elif output.get("additionalContext"):
            contexts.append(output["additionalContext"])
    if asked is not None:
        print(json.dumps(ask_as_deny(asked)))
    elif contexts:
        answer = {"hookEventName": event_name, "additionalContext": "\n\n".join(contexts)}
        print(json.dumps({"hookSpecificOutput": answer}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
