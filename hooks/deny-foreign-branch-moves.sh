#!/usr/bin/env bash
# Refuses a command that moves a branch ANOTHER checkout has checked out, and points at the one
# that does it properly: `harness.landCommand` of `.agents/harness.toml`, read through `ah config`
# only when the command moves a ref. Nothing here needs the user.
set -uo pipefail

payload=$(cat)

command -v jq >/dev/null 2>&1 || {
  echo "deny-foreign-branch-moves: jq is not installed, so this guard did not run" >&2
  exit 1
}
deny() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$1"
  exit 0
}

[[ $(printf '%s' "$payload" | jq -r '.tool_name // ""') == "Bash" ]] || exit 0
text=$(printf '%s' "$payload" | jq -r '.tool_input.command // ""')
# Bash removes a backslash and the newline after it, with no blank in their place. In a comment
# or in single quotes it keeps both, so the scan below reads both forms of the text.
raw=${text//$'\\\n'/$'\n'}
text=${text//$'\\\n'/}

# The one safe form: pushing HEAD to a branch here runs the `push-to-checkout` git hook, which
# writes that checkout's ref, index and files together. Anchored, so a force flag cannot match.
safe='^[[:space:]]*git[[:space:]]+push([[:space:]]+(-q|--quiet))*[[:space:]]+\.[[:space:]]+HEAD:(refs/heads/)?[A-Za-z0-9._/-]+[[:space:]]*$'
[[ $text =~ $safe ]] && exit 0

# Git's own global options sit between `git` and the subcommand, so every pattern below steps
# over them: `git -C <dir> update-ref` moves the same ref as `git update-ref`.
# An option argument is one shell word, and quoting it is what lets it carry a space.
q='"'
s="'"
arg="(${q}[^${q}]*${q}|${s}[^${s}]*${s}|[^[:space:]]+)"
opts="([[:space:]]+(-[cC][[:space:]]*${arg}"
opts+="|--(git-dir|work-tree|namespace|exec-path|config-env|super-prefix)(=${arg}|[[:space:]]+${arg})"
opts+='|-[pP]|--paginate|--no-pager|--bare|--no-replace-objects|--(literal|glob|noglob|icase)-pathspecs))*'

# True when the words of a `git branch` command, up to `--` or the end of its step, hold a flag
# that moves, renames, copies or deletes a ref: a short bundle with `f`, `m`, `M`, `c`, `C`, `d` or
# `D`, or a prefix of `--force`, `--move`, `--copy` or `--delete`, which git takes for the option.
branch_moves() {
  local word bare name flag
  for word in ${1%%$'\n'*}; do
    # Bash removes quotes and backslashes before git reads the word: `"-f"` and `\-f` are `-f`.
    bare=${word//[\"\'\\]/}
    name=${bare%%[;&|]*}
    [[ $name == -- ]] && return 1
    [[ $name =~ ^-[A-Za-z]+$ && $name =~ [fmMcCdD] ]] && return 0
    if [[ $name == --?* ]]; then
      for flag in --force --move --copy --delete; do
        [[ $flag == "${name%%=*}"* ]] && return 0
      done
    fi
    [[ $name == "$bare" ]] || return 1
  done
  return 1
}

# Each git invocation of the text, with its global options. git is the base name of a word, so
# `/usr/bin/git` and `command git` count. The forms that move a ref alone are
# `update-ref`, `branch` with a flag of `branch_moves`, and a push to the `.` remote. The push is
# matched by its `.` remote rather than by position: `--force` slipped through a pattern that
# wanted `push` and `.` adjacent.
# Sets `moves_a_ref`, `mover` (the global options of the first step that moves a ref) and
# `globals` (the global options of each git step) for the text in $1.
scan_moves() {
  local rest=$1 n prefix sub move
  moves_a_ref=0
  globals=()
  mover=''
  while [[ $rest =~ (^|[^A-Za-z0-9_./-])([^[:space:]]*/)?git(${opts})[[:space:]]+([A-Za-z][A-Za-z-]*)(.*)$ ]]; do
    n=${#BASH_REMATCH[@]}
    prefix=${BASH_REMATCH[3]}
    sub=${BASH_REMATCH[n-2]}
    rest=${BASH_REMATCH[n-1]}
    globals+=("$prefix")
    move=0
    case $sub in
      update-ref) move=1 ;;
      branch) branch_moves "$rest" && move=1 ;;
      push) [[ $1 =~ [[:space:]]\.([[:space:]]|$) ]] && move=1 ;;
    esac
    if [[ $move == 1 && $moves_a_ref == 0 ]]; then
      moves_a_ref=1
      mover=$prefix
    fi
  done
}

set -f
scan_moves "$text"
[[ $moves_a_ref == 1 ]] || scan_moves "$raw"
# Neither form is right when one continuation sits in a comment or in single quotes and another
# does not, so a continuation with `#` or `'` and a git ref command fails closed.
if [[ $moves_a_ref == 0 && $raw != "$text" && ($raw == *"#"* || $raw == *"'"*) ]] &&
  [[ $text =~ git && $text =~ (branch|update-ref|push) ]]; then
  deny "This command joins lines with a backslash and also holds a comment or a single quote, so this guard cannot tell whether it moves a branch. Put the git command on one line."
fi
[[ $moves_a_ref == 1 ]] || exit 0

# The engine sits beside this folder. Run from here, `ah config` reads the project of the hook.
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
land=$(cd "$here" && python3 ../bin/ah config get harness.landCommand 2>/dev/null)
[[ -n $land ]] || land='the land command'

# `git -C <dir>` runs the command in another repository, so the branch list is read from there.
# The path is one shell word: attached, quoted in either style, or bare. A target this hook
# cannot open leaves the question unanswerable, so it refuses instead of reading this checkout.
unreadable="this command carries a -C path that the guard cannot open, so it cannot tell which branches are checked out there, and a ref-only move in another checkout leaves that checkout's index and files behind for a peer to commit in reverse. Run '$land' from the worktree that holds the branch, which writes the ref, the index and the files in one operation, or spell the path so it resolves. Never hand the user a command to run: landing is yours."
# The tool's directory comes from the payload: the Codex adapter runs hooks from the checkout root.
dir=$(printf '%s' "$payload" | jq -r '.tool_input.workdir // .cwd // ""')
[[ -z $dir ]] || cd "$dir" 2>/dev/null || deny "$unreadable"
at=$PWD
# git applies each -C in turn, so the LAST one decides the repository. Reading the first answers
# for a checkout the command never targets, and a guard that resolves the whole chain is a
# second implementation of git's own path handling. One -C is supported; more is refused.
multiple="this command carries more than one -C, and git applies them in turn, so the repository it really targets is not the first one named. The guard reads one -C, so it cannot answer for this command, and a ref-only move in the wrong checkout leaves that checkout's index and files behind for a peer to commit in reverse. Run '$land' from the worktree that holds the branch, which writes the ref, the index and the files in one operation, or name the repository once. Never hand the user a command to run: landing is yours."
# Only the options between `git` and the subcommand are git's: `git branch -C a b` copies a branch.
# The count runs over every git invocation, and the one `-C` must belong to the move.
elsewhere="this command gives -C to a git step that moves no ref, so the guard cannot tell which checkout the ref move targets, and a ref-only move in the wrong checkout leaves that checkout's index and files behind for a peer to commit in reverse. Run '$land' from the worktree that holds the branch, which writes the ref, the index and the files in one operation, or run the move as a command of its own. Never hand the user a command to run: landing is yours."
count=0
for prefix in "${globals[@]}"; do
  # `grep -o`, because `-c` counts matching LINES and a command is one line.
  count=$((count + $(grep -oE '(^|[[:space:]])-C' <<<"$prefix" | wc -l)))
done
[[ $count -le 1 ]] || deny "$multiple"
prefix=$mover
[[ $count == 0 || $prefix =~ (^|[[:space:]])-C ]] || deny "$elsewhere"
if [[ $prefix =~ (^|[[:space:]])-C[[:space:]]*(.) ]]; then
  # The character after `-C` picks the pattern, so the FIRST `-C` is the one that is read.
  case "${BASH_REMATCH[2]}" in
    "$q") named="(^|[[:space:]])-C[[:space:]]*${q}([^${q}]*)${q}" ;;
    "$s") named="(^|[[:space:]])-C[[:space:]]*${s}([^${s}]*)${s}" ;;
    *) named='(^|[[:space:]])-C[[:space:]]*([^[:space:]]+)' ;;
  esac
  at=''
  [[ $prefix =~ $named ]] && at=${BASH_REMATCH[2]}
  [[ -n $at && -d $at ]] || deny "$unreadable"
fi

# Which branches are checked out somewhere OTHER than here, as `git worktree list` reports them.
here=$(git -C "$at" rev-parse --show-toplevel 2>/dev/null) || deny "$unreadable"
elsewhere=$(git -C "$at" worktree list --porcelain 2>/dev/null |
  awk -v here="$here" '/^worktree /{w=substr($0,10)} /^branch /{sub("refs/heads/","",$2); if (w != here) print $2}')
[[ -n $elsewhere ]] || exit 0

while read -r branch; do
  [[ -n $branch ]] || continue
  if [[ $text =~ (^|[^A-Za-z0-9._/-])(refs/heads/)?"$branch"([^A-Za-z0-9._/-]|$) ]]; then
    deny "$branch is checked out in another worktree, and this form moves, renames, copies onto or deletes the ref ALONE: that checkout keeps its old index and files, its git status then shows the move INVERTED, and a peer committing there reverts the work. Run '$land' from this worktree instead. It merges $branch in, runs the gate, and pushes HEAD to $branch, which writes that checkout's ref, index and files in one operation; it retries by itself when $branch moves, and it refuses by naming a peer's file rather than touching it. Never hand the user a command to run: landing is yours. 'git push . HEAD:refs/heads/$branch' is the single call underneath it."
  fi
done <<<"$elsewhere"

exit 0
