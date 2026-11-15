#!/usr/bin/env bash
# Runs one harness hook for Claude Code, and for Codex through `.codex/run-hook.py`.
# `ah install --project` installs it at `.claude/agent-harness/hooks/run.sh`. Edit the package
# copy, not this one.
#
#   run.sh <script> <event>    <script> is relative to the checkout root
#
# The script comes from the checkout this shell runs in when that checkout has a copy, so a
# session in a worktree runs the worktree's hooks. Otherwise it comes from the project that
# holds this launcher, found from the launcher's own path, never from the payload.
#
# A missing interpreter or script exits 1 with a message: a non-blocking error that the client
# shows, so a dead guard is reported rather than skipped in silence.
set -uo pipefail

script=${1:-}
[[ -n $script ]] || {
  echo "agent-harness: usage: run.sh <script> <event>" >&2
  exit 1
}
shift
case $script in
/* | *..* | *[[:space:]]*)
  echo "agent-harness: $script is not a plain path inside the checkout" >&2
  exit 1
  ;;
esac

home=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
top=$(git rev-parse --show-toplevel 2>/dev/null || true)
root=$home
[[ -n $top && -f $top/$script ]] && root=$top
file=$root/$script
[[ -f $file ]] || {
  echo "agent-harness: $file is missing; run ah install --project" >&2
  exit 1
}

case $file in
*.py) interpreter=python3 ;;
*.sh | */githooks/*) interpreter=bash ;;
*)
  echo "agent-harness: no interpreter is known for $file" >&2
  exit 1
  ;;
esac
command -v "$interpreter" >/dev/null 2>&1 || {
  echo "agent-harness: $interpreter is not installed, so the hook $script did not run" >&2
  exit 1
}

export LW_PROJECT_DIR=$root
exec "$interpreter" "$file" "$@"
