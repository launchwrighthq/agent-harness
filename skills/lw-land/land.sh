#!/usr/bin/env bash
# Lands the current branch on the local base branch, from the branch's own worktree.
#
#   bash .claude/skills/lw-land/land.sh
#
# The base branch and the gate are `harness.baseBranch` and `harness.gateCommand` of
# `.agents/harness.toml`, read with `ah config get`. The `ah` engine is the project install
# (`.claude/agent-harness/bin/ah`), else the package beside this script, else the global install.
# Dials: LAND_ATTEMPTS (3), LAND_GATE (1 is the mode of `harness.land.defaultGate`; a mode name
# runs that mode; 0 skips the gate and the report must say so), LAND_PEER_WAIT (60 s),
# LAND_PEER_TRIES (10), LAND_TARGET (the base branch to land on).
# The gate modes: `full` is `harness.gateCommand`, each other one is a key of `harness.land.gates`.
# The optional `harness.land` keys are in the docs/hooks.md table of the package.
#
# Exit codes: 0 landed, 2 the gate is red, 3 a merge conflict, 4 the push did not land,
# 5 this checkout cannot land (detached, on the base branch, uncommitted work, no setup).
#
# It needs the `push-to-checkout` git hook in `core.hooksPath` and
# `receive.denyCurrentBranch = updateInstead`, so the push writes the ref, the index and the
# files of the checkout that holds the base branch in one operation.
# Written for bash 3.2 and 5: declare every array `=()` and check its length before use.
set -uo pipefail

ATTEMPTS=${LAND_ATTEMPTS:-3}
GATE=${LAND_GATE:-1}
WAIT_FOR_PEER=${LAND_PEER_WAIT:-60}
PEER_TRIES=${LAND_PEER_TRIES:-10}

root=$(git rev-parse --show-toplevel) || exit 5
common=$(git rev-parse --path-format=absolute --git-common-dir) || exit 5
gate_marks="$common/land-gates"
gate_log=""

say() { printf 'land: %s\n' "$1"; }
die() {
  printf 'land: %s\n' "$1" >&2
  exit "$2"
}

[[ $ATTEMPTS =~ ^[1-9][0-9]?$ ]] || die "LAND_ATTEMPTS must be 1 to 99." 5
[[ $PEER_TRIES =~ ^[1-9][0-9]?$ ]] || die "LAND_PEER_TRIES must be 1 to 99." 5
[[ $WAIT_FOR_PEER =~ ^[0-9]+$ ]] || die "LAND_PEER_WAIT must be a number of seconds." 5

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ah=""
for candidate in "$root/.claude/agent-harness/bin/ah" "$here/../../bin/ah" "$HOME/.claude/agent-harness/bin/ah"; do
  if [[ -f $candidate ]]; then
    ah=$candidate
    break
  fi
done
[[ -n $ah ]] || die "no agent-harness engine found. Run: ah install --project" 5
setting() {
  (cd "$root" && python3 "$ah" config get "$1")
}
base=${LAND_TARGET:-$(setting harness.baseBranch)} || die "cannot read harness.baseBranch with $ah" 5
gate_command=$(setting harness.gateCommand) || die "cannot read harness.gateCommand with $ah" 5
green_token=$(setting harness.land.greenToken) || die "cannot read harness.land.greenToken with $ah" 5
probe_command=$(setting harness.land.probeCommand) || die "cannot read harness.land.probeCommand with $ah" 5
after_merge=$(setting harness.land.afterMerge) || die "cannot read harness.land.afterMerge with $ah" 5
explain_command=$(setting harness.land.explainCommand) || die "cannot read harness.land.explainCommand with $ah" 5
default_gate=$(setting harness.land.defaultGate) || die "cannot read harness.land.defaultGate with $ah" 5
gate_names=$(setting harness.land.gates | python3 -c 'import json, sys
gates = json.load(sys.stdin)
if not isinstance(gates, dict) or not all(isinstance(c, str) for c in gates.values()):
    sys.exit(1)
print("\n".join(gates))') ||
  die "cannot read harness.land.gates with $ah. It is a table of mode names and commands." 5
excluded=$(setting harness.land.overlapExclude |
  python3 -c 'import json, sys; print("\n".join(json.load(sys.stdin)))') ||
  die "cannot read harness.land.overlapExclude with $ah. It is a list of paths." 5
exclude=()
while IFS= read -r path; do
  [[ -z $path ]] || exclude+=(":(exclude,top)$path")
done <<<"$excluded"
[[ -n $base ]] || die "harness.baseBranch is empty. Set it in .agents/harness.toml." 5

# The mode name is a part of the gate mark's file name.
mode_name='^[A-Za-z][A-Za-z0-9_-]*$'
known_modes="full, none"
while IFS= read -r name; do
  [[ -n $name ]] || continue
  [[ $name != full && $name != none ]] ||
    die "harness.land.gates holds the mode $name. harness.gateCommand is the mode full, and LAND_GATE=0 is the mode none. Remove the key." 5
  [[ $name =~ $mode_name ]] ||
    die "harness.land.gates holds the mode $name. A mode name starts with a letter and holds letters, digits, - and _." 5
  known_modes="$known_modes, $name"
done <<<"$gate_names"
case $GATE in
0 | none)
  gate_mode=none
  mode_source="LAND_GATE=$GATE"
  ;;
1)
  gate_mode=$default_gate
  mode_source="harness.land.defaultGate"
  [[ $gate_mode != none ]] ||
    die "harness.land.defaultGate is none. Only LAND_GATE=0 skips the gate. Set a mode that runs a command." 5
  ;;
*)
  gate_mode=$GATE
  mode_source="LAND_GATE=$GATE"
  ;;
esac
if [[ $gate_mode == full ]]; then
  [[ -n $gate_command ]] ||
    die "harness.gateCommand is empty. Set it in .agents/harness.toml, or ask the user for LAND_GATE=0." 5
elif [[ $gate_mode != none ]]; then
  grep -qxF -- "$gate_mode" <<<"$gate_names" ||
    die "the gate mode $gate_mode ($mode_source) is unknown. The modes are: $known_modes." 5
  gate_command=$(setting "harness.land.gates.$gate_mode") ||
    die "cannot read harness.land.gates.$gate_mode with $ah" 5
  [[ -n $gate_command ]] || die "harness.land.gates.$gate_mode is empty. Set the command of the mode." 5
fi

# The checkout that holds the base branch, which is the one a push updates.
base_checkout() {
  git worktree list --porcelain |
    awk -v ref="refs/heads/$base" '/^worktree /{w=substr($0,10)} $1=="branch" && $2==ref {print w; exit}'
}

# The mark of a green gate: `<tree>` for the mode `full`, `<tree>.<mode>` for each other mode.
gate_mark() {
  if [[ $2 == full ]]; then
    printf '%s\n' "$gate_marks/$1"
  else
    printf '%s\n' "$gate_marks/$1.$2"
  fi
}

# The mode whose green gate covers the tree of the last `gate` call. Empty when no gate ran.
green_mode=""

# The gate per tree and mode, so a retry after a peer collision does not pay for the gate twice.
# A `full` mark covers every mode. Another mark covers its own mode only.
# A redirect and never a pipe: a pipeline reports the last command's status, not the gate's.
gate() {
  local tree marker log
  tree=$(git rev-parse 'HEAD^{tree}')
  marker=$(gate_mark "$tree" "$gate_mode")
  green_mode=""
  say "gate: mode $gate_mode"
  if [[ -f $(gate_mark "$tree" full) ]]; then
    green_mode=full
  elif [[ $gate_mode != none && -f $marker ]]; then
    green_mode=$gate_mode
  fi
  if [[ -n $green_mode ]]; then
    say "gate: already green on this tree ($tree) in mode $green_mode, not running it again"
    return 0
  fi
  if [[ $gate_mode == none ]]; then
    say "gate: SKIPPED by LAND_GATE=$GATE. The report must say the gate was not run."
    return 0
  fi
  mkdir -p "$gate_marks"
  find "$gate_marks" -maxdepth 1 -type f -mtime +7 -delete 2>/dev/null || say "gate: could not prune old gate marks"
  # Per process too: two landings of one tree would otherwise write one log.
  log="$gate_marks/$tree-$$.log"
  say "gate: $gate_command, logging to $log"
  # A gate that can end early with exit 0, such as a cancelled run, proves green with a line.
  if (cd "$root" && LAND_MERGED_BASE=$merged LAND_ROOT=$root bash -c "$gate_command") >"$log" 2>&1; then
    if [[ -z $green_token ]] || grep -qxF -- "$green_token" "$log"; then
      : >"$marker"
      green_mode=$gate_mode
      say "gate: green"
      return 0
    fi
    say "gate: exit 0, but no line of the gate log is $green_token" >&2
  fi
  gate_log=$log
  return 1
}

results() {
  python3 "$ah" land-results "$1" "$2"
}

# One line per failed step of the result record: the step, its exit code and its failed files.
failed_steps() {
  local log=$1 lines verdict line
  lines=$(results steps "$log" 2>&1)
  verdict=$?
  if [[ $verdict -eq 3 ]]; then
    say "gate: the log has no result record" >&2
  elif [[ $verdict -ne 0 ]]; then
    say "gate: the result record of $log could not be read. $lines" >&2
  elif [[ -z $lines ]]; then
    say "gate: the result record names no failed step" >&2
  else
    say "gate: red at these steps of the result record:" >&2
    while IFS= read -r line; do printf 'land:   %s\n' "$line" >&2; done <<<"$lines"
  fi
}

# A gate such as a remote runner keeps the log of a failed step in its own store. The
# `harness.land.explainCommand` command prints it. Its exit code never changes the landing's.
explain() {
  local log=$1 verdict
  [[ -n $explain_command ]] || return 0
  say "explain: $explain_command" >&2
  (cd "$root" && LAND_GATE_LOG=$log LAND_ROOT=$root bash -c "$explain_command") >&2
  verdict=$?
  [[ $verdict -eq 0 ]] || say "explain: the command failed (exit $verdict). The gate verdict stays." >&2
}

# The base can be red, and its failures are not the branch's to fix. Run the failed files again
# on the base this MERGED, in a detached worktree, so a fix that landed later is not read as ours.
# The probe command gets the files as arguments, and prints its own `verify: failures` record.
already_red_on_base() {
  local log=$1 at=$2 probe verdict file
  local -a files=()
  while IFS= read -r file; do [[ -z $file ]] || files+=("$file"); done < <(results files "$log")
  [[ ${#files[@]} -gt 0 ]] || return 1
  probe="$checkout/.claude/worktrees/land-base-check-$$"
  if ! git worktree add -q --detach "$probe" "$at" >"$log.base" 2>&1; then
    say "gate: the probe worktree could not be added. $(tail -1 "$log.base")" >&2
    return 1
  fi
  say "probe: $probe_command, on $base at $(git rev-parse --short "$at"), logging to $log.base"
  # shellcheck disable=SC2016
  (cd "$probe" && LAND_ROOT=$root LAND_CHECKOUT=$checkout bash -c "$probe_command"' "$@"' _ "${files[@]}") >"$log.base" 2>&1
  verdict=$?
  git worktree remove --force "$probe" >/dev/null 2>&1 || say "gate: remove the probe worktree $probe by hand" >&2
  if [[ $verdict -eq 0 ]]; then
    say "gate: these files PASS on the $base this merged, so the failures are the branch's:" >&2
    printf 'land:   %s\n' "${files[@]}" >&2
    return 1
  fi
  [[ $verdict -eq 1 ]] || { say "gate: the probe did not finish with a test verdict (exit $verdict)." >&2; return 1; }
  for file in "${files[@]}"; do
    if ! results files "$log.base" | grep -qxF -- "$file"; then
      say "gate: $file does not fail on the $base this merged, so its failure is the branch's." >&2
      return 1
    fi
  done
  printf "land:   %s (red on the merged base too)\n" "${files[@]}"
  grep '^verify: failures ' "$log.base" | tail -1
  return 0
}

# A path the landing adds that already sits untracked or ignored in the base checkout blocks the
# write. Delete it only when it is a file byte-identical to the copy this branch committed.
clear_own_leftovers() {
  local report=$1 checkout=$2 path cleared=0
  checkout=${checkout%/}
  while read -r path; do
    [[ -n $path ]] || continue
    if [[ ! -e "$checkout/$path" && ! -L "$checkout/$path" ]]; then
      say "leftover: $path is already gone from the checkout"
      continue
    fi
    if [[ -L "$checkout/$path" || ! -f "$checkout/$path" ]]; then
      say "leftover: $path in the checkout is a folder or a link, so it is not mine to delete" >&2
      continue
    fi
    if git show "HEAD:$path" 2>/dev/null | cmp -s - "$checkout/$path"; then
      rm -- "$checkout/$path"
      say "leftover: removed $path from the checkout; it was identical to the committed copy"
      cleared=1
    else
      say "leftover: $path in the checkout DIFFERS from this branch's copy, so it is not mine to delete" >&2
    fi
  done <<<"$(sed -n -e "s/.*Untracked working tree file '\(.*\)' would be overwritten.*/\1/p" \
    -e '/in the way of files the landing adds:$/,/^land: that is/s/^land:   //p' "$report")"
  return $((cleared ? 0 : 1))
}

# The report lines of an untracked file in the way of a path the landing writes.
in_the_way='in the way of files the landing adds\|Untracked working tree file\|would lose untracked files'

# A peer can stage and commit in that checkout at any time, and their window is short.
# Waiting costs nothing and runs no gate again, because nothing about this tree changed.
# An untracked file in the way is no commit window, so it is not waited for.
push_waiting_for_peers() {
  local left=$PEER_TRIES
  while true; do
    git push --quiet --receive-pack="$receive_pack" . "HEAD:refs/heads/$base" >"$report" 2>&1 && return 0
    grep -q 'land: REFUSED' "$report" || return 1
    grep -q "$in_the_way" "$report" && return 1
    sed -n 's/^remote: //; /^land:/p' "$report" >&2
    left=$((left - 1))
    [[ $left -gt 0 ]] || return 1
    say "waiting ${WAIT_FOR_PEER}s for that session, $left tries left"
    sleep "$WAIT_FOR_PEER"
  done
}

# The base moves faster than a gate runs, so gating every move again never converges. A GREEN
# gate carries over unless the base touched a file this branch also changed, outside the paths
# of `harness.land.overlapExclude`.
carry_the_gate_over() {
  local from=$1 to=$2 merge_base
  merge_base=$(git merge-base "$from" HEAD) || return 1
  [[ -z $(comm -12 <(git diff --name-only "$merge_base" HEAD -- ${exclude[@]+"${exclude[@]}"} | sort) \
    <(git diff --name-only "$from" "$to" -- ${exclude[@]+"${exclude[@]}"} | sort)) ]]
}

# A derived file, such as an index of the specs, goes stale when two branches merge cleanly.
# The `harness.land.afterMerge` command writes it again, into the merge commit the gate then marks.
# Only a merge commit made by this landing is amended.
run_after_merge() {
  local before=$1
  [[ -n $after_merge ]] || return 0
  [[ $(git rev-parse HEAD) != "$before" && $(git rev-parse HEAD) != "$merged" ]] || return 0
  if ! (cd "$root" && bash -c "$after_merge") >"$report" 2>&1; then
    cat "$report" >&2
    die "the after-merge command failed. The output is above. The merge is left in place." 3
  fi
  [[ -n $(git status --porcelain=v1 --untracked-files=no) ]] || return 0
  say "after merge: amending these files into the merge commit:"
  git diff --name-only HEAD -- | sed 's/^/land:   /'
  # --no-verify: a pre-commit hook would judge every file the merge brought. The gate is next.
  git commit --quiet --no-verify --amend --no-edit --all ||
    die "the after-merge files could not be amended into the merge commit." 3
}

# A base with no commit takes the branch's root commit only when that commit matches the files
# of its checkout. A temporary index compares contents and modes without staging a peer's file.
start_unborn_base() {
  local initial index_dir
  initial=$(git rev-list --max-parents=0 HEAD)
  [[ $(wc -w <<<"$initial") == 1 ]] || die "$base has no commit, and this branch has more than one root commit." 5
  [[ -z $(git -C "$checkout" ls-files) ]] || die "$base has no commit, and its checkout has staged files." 4
  index_dir=$(mktemp -d)
  if ! GIT_INDEX_FILE="$index_dir/index" git -C "$checkout" read-tree "$initial" ||
    ! GIT_INDEX_FILE="$index_dir/index" git -C "$checkout" diff --quiet; then
    rm -rf "$index_dir"
    die "the root commit of $branch does not match the files of the $base checkout. Keep those files first." 4
  fi
  rm -rf "$index_dir"
  git -C "$checkout" read-tree "$initial" || die "the root commit could not be staged in $checkout." 4
  if ! git -C "$checkout" merge --quiet --ff-only "$initial"; then
    git -C "$checkout" read-tree --empty
    die "$base could not start at the root commit of $branch." 4
  fi
  say "$base had no commit. It starts at the root commit $(git rev-parse --short "$initial"), which matches its checkout."
}

branch=$(git rev-parse --abbrev-ref HEAD) || exit 5
[[ $branch != HEAD ]] || die "HEAD is detached, so there is no branch to land." 5
[[ $branch != "$base" ]] || die "this is $base. Land a branch, from the worktree that holds it." 5

# A landing carries commits, so uncommitted work would be left behind silently.
mine=$(git status --porcelain=v1 --untracked-files=no)
if [[ -n $mine ]]; then
  printf 'land:   %s\n' "$mine" >&2
  die "commit these first: a landing carries commits, and these would stay behind." 5
fi

checkout=$(base_checkout)
[[ -n $checkout ]] || die "no worktree holds $base, so there is nothing to land into." 5
git show-ref --verify --quiet "refs/heads/$base" || start_unborn_base

# With the `noreply-identity` git hook on, every commit carries `<name>@users.noreply.github.com`,
# GitHub's `<id>+` prefix allowed. A merge runs no post-commit hook, so the landing's own commits
# take that address here, and a branch commit that lacks it stops the landing before any merge.
enabled=$(setting harness.enable) || die "cannot read harness.enable with $ah" 5
if [[ $enabled == *'"noreply-identity"'* ]]; then
  author=$(git config user.name) || die "the noreply-identity git hook is on, and git has no user.name here." 5
  export GIT_AUTHOR_NAME=$author GIT_COMMITTER_NAME=$author
  noreply_domain=users.noreply.github.com
  export GIT_AUTHOR_EMAIL="$author@$noreply_domain" GIT_COMMITTER_EMAIL="$author@$noreply_domain"
  stray=$(git log --format='%h|%an|%ae|%cn|%ce' "$base..HEAD" |
    awk -F'|' -v domain="@$noreply_domain" '{ a = $3; c = $5; sub(/^[0-9]+\+/, "", a); sub(/^[0-9]+\+/, "", c) }
      a != $2 domain || c != $4 domain { print $1 "  " $2 " <" $3 "> / " $4 " <" $5 ">" }')
  if [[ -n $stray ]]; then
    printf 'land:   %s\n' "$stray" >&2
    die "these commits lack the noreply address. Rebuild each with <name>@users.noreply.github.com, then land again." 5
  fi
fi
# The push opens the repository by its git directory, so without `core.bare = false` git takes
# it as bare: it moves the branch, runs no hook and leaves the checkout's files behind.
[[ $(git config --get core.bare) == false ]] ||
  die "the repository config lacks core.bare = false. Run: git -C $checkout config core.bare false" 5
[[ $(git config --get receive.denyCurrentBranch) == updateInstead ]] ||
  die "receive.denyCurrentBranch is not updateInstead. Run: git -C $checkout config receive.denyCurrentBranch updateInstead" 5
hooks_path=$(git config --get core.hooksPath || true)
hooks_path=${hooks_path%/}
[[ -n $hooks_path ]] || die "core.hooksPath is unset, so no push-to-checkout git hook is active. Run: ah install --project" 5
# Git runs a push hook in the git directory, where a relative path does not resolve.
[[ $hooks_path == /* ]] ||
  die "core.hooksPath is $hooks_path, a relative path that a push hook does not find. Run: git -C $checkout config core.hooksPath $checkout/${hooks_path#./}" 5
# The landing that brings the hooks folder into the checkout runs a copy of the hook from HEAD.
landing_hooks=$hooks_path
if [[ ! -x $hooks_path/push-to-checkout ]]; then
  landing_hooks="$common/land-hooks"
  mkdir -p "$landing_hooks"
  # The dispatcher at `<hooks>/push-to-checkout` runs the harness hook in `<hooks>/harness/`, which
  # a lone copy cannot find, so the harness hook comes first.
  rel_hooks=${hooks_path#"$checkout"/}
  [[ $hooks_path == "$checkout"/* ]] &&
    { git show "HEAD:$rel_hooks/harness/push-to-checkout" || git show "HEAD:$rel_hooks/push-to-checkout"; } \
      >"$landing_hooks/push-to-checkout" 2>/dev/null ||
    die "no push-to-checkout git hook is in $hooks_path or in HEAD. Run: ah install --project" 5
  chmod +x "$landing_hooks/push-to-checkout"
  say "hook: $hooks_path has no push-to-checkout yet, so this landing runs the copy in HEAD"
fi
# The receiving side starts with the hooks folder on its command line, so no other config can
# drop the hook. `git -c ... push` does not reach it: git clears GIT_CONFIG_PARAMETERS for a
# local transport.
receive_pack="git -c core.hooksPath=$(printf '%q' "$landing_hooks") receive-pack"

report=$(mktemp)
trap 'rm -f "$report"' EXIT
landed=0
carry=0

for attempt in $(seq 1 "$ATTEMPTS"); do
  say "attempt $attempt of $ATTEMPTS: branch $branch, $base at $(git rev-parse --short "$base")"

  # Resolved BEFORE the merge, and merged by SHA: a peer moving the base between the two would
  # otherwise pin the overlap check to a commit HEAD never merged.
  merged=$(git rev-parse "$base") || die "$base cannot be read." 5
  before=$(git rev-parse HEAD)
  if ! git merge --no-edit -m "Merge branch '$base' into $branch" "$merged" >"$report" 2>&1; then
    conflicts=$(git diff --name-only --diff-filter=U)
    cat "$report" >&2
    [[ -z $conflicts ]] && die "merging $base into $branch failed. The output is above." 3
    say "these files conflict with $base:" >&2
    printf 'land:   %s\n' $conflicts >&2
    die "resolve them, commit, and land again. The merge is left in place for you." 3
  fi
  run_after_merge "$before"

  if [[ $carry == 1 ]]; then
    # The mark keeps the mode of the gate it carries. A skipped gate leaves no mark.
    if [[ -n $green_mode ]]; then
      mkdir -p "$gate_marks"
      : >"$(gate_mark "$(git rev-parse 'HEAD^{tree}')" "$green_mode")"
    fi
    carry=0
  fi

  gate_green=1
  if ! gate; then
    gate_green=0
    failed_steps "$gate_log"
    unaccounted=""
    [[ -z $probe_command ]] || unaccounted=$(results unaccounted "$gate_log") ||
      die "the gate is red, and its log $gate_log could not be read." 2
    if [[ -n $probe_command && -z $unaccounted ]] && already_red_on_base "$gate_log" "$merged"; then
      say "gate: red, and every failure is red on the merged $base too. Landing adds none."
    elif [[ $(git rev-parse "$base") != "$merged" ]]; then
      say "gate: red, and $base moved. Merging it again, since it may carry the fix."
      continue
    else
      explain "$gate_log"
      tail -30 "$gate_log" >&2
      if [[ -n $unaccounted ]]; then
        printf 'land:   %s\n' $unaccounted >&2
        die "the gate failed at the steps above, which no probe can attribute to $base." 2
      fi
      die "the gate is red. The last 30 lines are above; the whole log is $gate_log." 2
    fi
  fi

  was=$(git rev-parse "$base")
  if push_waiting_for_peers; then
    landed=1
    break
  fi

  if grep -q 'non-fast-forward' "$report"; then
    # From the base that was MERGED, not from `was`: the base may have moved during the gate.
    # Only a green gate carries over. A red one that the probe let through gates again.
    if [[ $gate_green == 1 ]] && carry_the_gate_over "$merged" "$(git rev-parse "$base")"; then
      say "$base moved, and touched no file this branch changed, so the gate carries over."
      carry=1
    else
      say "$base moved, so it gates again."
    fi
    continue
  fi

  if grep -q "$in_the_way" "$report"; then
    if clear_own_leftovers "$report" "$checkout"; then continue; fi
    cat "$report" >&2
    die "an untracked file in the checkout blocks the landing and it is not this branch's." 4
  fi

  if grep -q 'land: REFUSED' "$report"; then
    die "a peer's uncommitted work held those paths for every wait. Land again when they commit." 4
  fi

  cat "$report" >&2
  die "the push failed for a reason this script does not know. The output is above." 4
done

[[ $landed == 1 ]] || die "gave up after $ATTEMPTS attempts. Nothing was changed." 4

new=$(git rev-parse "$base")
say "landed $branch on $base at $(git rev-parse --short "$base")"

# Prove the checkout took the landing, rather than assume it. Only the paths the landing wrote:
# a peer's dirty file elsewhere is theirs, and the hook leaves it alone by design.
written=$(git diff --name-only "$was" "$new" --)
if [[ -n $written ]]; then
  # shellcheck disable=SC2086
  if ! stale=$(git -C "$checkout" diff --name-only "$new" -- $written 2>"$report"); then
    cat "$report" >&2
    die "$base is at $(git rev-parse --short "$base"), and the proof that $checkout took it could not run. Check its files by hand." 4
  fi
  if [[ -n $stale ]]; then
    printf 'land:   %s\n' $stale >&2
    die "the checkout's files disagree with $base on paths this landed. The hook did not run." 4
  fi
fi
at=$(git -C "$checkout" rev-parse --short HEAD) ||
  die "$base is at $(git rev-parse --short "$base"), and $checkout could not be read to confirm it took the landing." 4
say "$checkout is consistent at $at"

# The page folder stays out of git, so a landed spec has no page in the base checkout until it
# renders there. The worktree page goes away with the worktree, so the report links this page.
spec_dirs=$(cd "$checkout" && python3 "$ah" config get specs.dirs |
  python3 -c 'import json, sys; print("\n".join(json.load(sys.stdin)))') ||
  die "$base landed, and specs.dirs could not be read, so no page was rendered." 4
in_spec_dir() {
  local dir
  while read -r dir; do
    [[ -n $dir && $1 == "$dir"/* ]] && return 0
  done <<<"$spec_dirs"
  return 1
}
while read -r path; do
  [[ $path == *.md && ${path##*/} != README.md && -f $checkout/$path ]] || continue
  in_spec_dir "$path" || continue
  if (cd "$checkout" && python3 "$ah" spec render "$checkout/$path") >"$report" 2>&1; then
    page=$(sed -n 's/^wrote \(.*\) ([0-9]* sections)$/\1/p' "$report")
    say "page: file://$page"
    while IFS= read -r line; do
      [[ $line != warning:* ]] || say "page: $line"
    done <"$report"
  else
    say "page: $path did not render in $checkout: $(tail -1 "$report")"
  fi
done <<<"$written"
