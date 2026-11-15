#!/bin/sh
# The git hook dispatcher. `ah install --project` writes one copy per client-side hook name into
# `.githooks/`, and `core.hooksPath` names that folder. So git runs no hook of the global
# `core.hooksPath` and none of `.git/hooks/` here, and this file runs them in this order:
#   1. the hook of this name in the global `core.hooksPath`, when one is set and is not this folder
#   2. the harness hook of this name in `harness/`
#   3. `.git/hooks/<name>`, only with no global path: a global dispatcher runs that file itself
# Each hook gets the same arguments and the same stdin. A nonzero exit stops with that code.
set -u
name=$(basename -- "$0")
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || exit 1

input=""
trap '[ -z "$input" ] || rm -f -- "$input"' EXIT
# pre-push and post-rewrite read stdin, so every hook of the chain gets a copy of it.
if [ ! -t 0 ]; then
  input=$(mktemp "${TMPDIR:-/tmp}/githook-stdin.XXXXXX") || exit 1
  cat >"$input" || exit 1
fi

ran=0
run() {
  hook=$1
  shift
  [ -f "$hook" ] && [ -x "$hook" ] || return 0
  ran=1
  if [ -n "$input" ]; then "$hook" "$@" <"$input"; else "$hook" "$@" </dev/null; fi
  code=$?
  [ "$code" -eq 0 ] || exit "$code"
}

global=$(git config --global --type=path --get core.hooksPath 2>/dev/null) || global=""
if [ -n "$global" ]; then
  global_dir=$(CDPATH='' cd -- "$global" 2>/dev/null && pwd -P) || global_dir=""
  if [ "$global_dir" = "$here" ]; then
    global=""
  elif [ -n "$global_dir" ]; then
    run "$global_dir/$name" "$@"
  fi
fi

run "$here/harness/$name" "$@"

if [ -z "$global" ]; then
  common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || common=""
  if [ -n "$common" ] && [ "$(CDPATH='' cd -- "$common/hooks" 2>/dev/null && pwd -P)" != "$here" ]; then
    run "$common/hooks/$name" "$@"
  fi
fi

# With a push-to-checkout hook, git leaves the update of the checkout to the hook. When no hook
# of the chain ran, this does what git does without one: refuse a changed checkout, else update.
if [ "$name" = push-to-checkout ] && [ "$ran" -eq 0 ]; then
  cd "${GIT_WORK_TREE:-$(git rev-parse --show-toplevel)}" || exit 1
  git update-index -q --ignore-submodules --refresh
  git diff-files --quiet --ignore-submodules -- || {
    echo "push-to-checkout: the checkout has unstaged changes" >&2
    exit 1
  }
  git diff-index --quiet --cached --ignore-submodules HEAD -- || {
    echo "push-to-checkout: the checkout has staged changes" >&2
    exit 1
  }
  git read-tree -u -m HEAD "$1" || exit 1
fi
exit 0
