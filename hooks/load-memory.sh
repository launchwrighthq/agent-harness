#!/usr/bin/env bash
# Fast path of the memory index injector (see inject-memory.py). The flag check in shell avoids
# a Python start on every tool call: only the one call that injects pays it. $1 is the event.
#
# The flag is keyed on the SESSION id, never on a PID: the client runs each hook in a fresh
# short-lived shell, so `$PPID` differs on every call. CLAUDE_CODE_SESSION_ID is stable for the
# life of a session and is set for both events (the Codex adapter sets it from `session_id`).
#
# The Python file is loaded from beside this one (`BASH_SOURCE`), never through
# `$CLAUDE_PROJECT_DIR`, so a worktree runs its own copy.
SID="${CLAUDE_CODE_SESSION_ID:-}"
FLAG="${TMPDIR:-/tmp}/claude-memory-loaded-${SID:-nosession-$$}"
[ -f "$FLAG" ] && exit 0
PY="$(dirname "${BASH_SOURCE[0]}")/inject-memory.py"
[ -f "$PY" ] || {
  echo "load-memory: $PY is missing; run ah install --project" >&2
  exit 1
}
exec python3 "$PY" "${1:-PreToolUse}"
