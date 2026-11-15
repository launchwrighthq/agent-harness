#!/usr/bin/env bash
# PreToolUse(Bash) hook: run a throwaway scratchpad script inside a capped cgroup, so a runaway
# probe starves itself instead of the machine. Off by default; `harness.enable` turns it on.
#
# A runaway probe can fill the memory of the machine, and the global OOM killer can then pick
# another process. Under a scope the runaway dies alone. A probe can also hold every core with
# little memory, so the cap covers CPU as well as memory. The caps are `scratchCap.memoryMax`
# and `scratchCap.cpuMax` of the harness config.
#
# NARROW ON PURPOSE. It fires only when the command names a scratchpad path AND runs node, npx,
# bun, deno or python as the first word of a segment: the shape of a one-off probe. A command
# that names a test runner or the gate is honest work and keeps the machine's memory.
#
# The wrapped command runs in a subshell, so a `cd` in it does not persist to the next call.
set -euo pipefail

command -v jq >/dev/null 2>&1 || {
  echo "scratch-cap: jq is not installed, so this hook did not run" >&2
  exit 1
}
DELIM=LW_SCRATCH_CAP_HEREDOC

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

# Already capped, or no systemd user manager to cap with: leave it alone.
case "$cmd" in *systemd-run*) exit 0 ;; esac
command -v systemd-run >/dev/null 2>&1 || exit 0

# A heredoc cannot carry a line that is exactly the delimiter. Refuse to rewrite rather than
# corrupt the command.
printf '%s\n' "$cmd" | grep -qx "$DELIM" && exit 0

case "$cmd" in *scratchpad*) ;; *) exit 0 ;; esac
case "$cmd" in *pytest* | *vitest* | *" verify"* | *"verify "*) exit 0 ;; esac

runs_interpreter=0
norm=${cmd//&&/$'\n'}
norm=${norm//||/$'\n'}
norm=${norm//|/$'\n'}
norm=${norm//;/$'\n'}
while IFS= read -r seg; do
  seg="${seg#"${seg%%[![:space:]]*}"}"
  first=${seg%%[[:space:]]*}
  case "${first##*/}" in
  node | npx | bun | deno | python | python3)
    runs_interpreter=1
    break
    ;;
  esac
done <<<"$norm"
[ "$runs_interpreter" -eq 1 ] || exit 0

# The engine sits beside this folder. Run from here, `ah config` reads the project of the hook.
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MEMORY_MAX=$(cd "$here" && python3 ../bin/ah config get harness.scratchCap.memoryMax 2>/dev/null || echo 8G)
CPU_MAX=$(cd "$here" && python3 ../bin/ah config get harness.scratchCap.cpuMax 2>/dev/null || echo 400%)

wrapped="systemd-run --user --scope -q -p MemoryMax=${MEMORY_MAX} -p MemorySwapMax=0 -p CPUQuota=${CPU_MAX} -- bash -c \"\$(cat <<'${DELIM}'
${cmd}
${DELIM}
)\""

jq -n --arg w "$wrapped" --arg m "$MEMORY_MAX" --arg c "$CPU_MAX" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    updatedInput: { command: $w },
    permissionDecisionReason: ("scratchpad script capped at " + $m + " of memory and " + $c + " of CPU")
  }
}'
