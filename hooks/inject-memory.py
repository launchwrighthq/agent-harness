#!/usr/bin/env python3
"""Inject the project memory index into the context of the model, once per session.

`load-memory.sh` runs it on SessionStart and PreToolUse. A flag file keyed on the session id
makes it fire once per session. The index is `harness.memoryIndex` of the config (default
`.claude/memory/memory.md`). No index means no output. The key is never a PID: the client runs
each hook in a new shell, so the parent PID changes on every call. The env var
CLAUDE_CODE_SESSION_ID comes first, because the shell fast path of `load-memory.sh` sees only
that one, and the two must agree. Then comes `session_id` of the payload. A session with
neither gets a per-process key, which injects too often but never silences another session.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import hookio  # noqa: E402

MAX_LINES = 200  # bound the startup token cost


def session_key(payload: str) -> str:
    """The stable per-session flag key. Env first, then stdin, then a PID fallback."""
    env = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if env:
        return env
    try:
        sid = json.loads(payload).get("session_id")
    except (ValueError, AttributeError):
        sid = None
    return sid or f"nosession-{os.getppid()}"


def index_path(project: Path) -> Path:
    rel = hookio.harness_value(hookio.hook_config("inject-memory", __file__), "memoryIndex")
    return project / (rel if isinstance(rel, str) and rel else ".claude/memory/memory.md")


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "PreToolUse"
    payload = sys.stdin.read() if not sys.stdin.isatty() else ""
    tmp = os.environ.get("TMPDIR", "/tmp")
    flag = Path(tmp) / f"claude-memory-loaded-{session_key(payload)}"
    if flag.exists():
        return
    project = Path(
        os.environ.get("LW_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    )
    index = index_path(project)
    if not index.exists():
        return
    flag.touch()
    lines = index.read_text(encoding="utf-8").splitlines()[:MAX_LINES]
    context = (
        f"=== Project memory index ({index}) ===\n"
        "Load a topic file beside it only when the task needs it.\n\n" + "\n".join(lines)
    )
    print(
        json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}})
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        # A context hook must not break the session; the client shows this line as a hook error.
        print(f"inject-memory: {error}", file=sys.stderr)
        sys.exit(1)
