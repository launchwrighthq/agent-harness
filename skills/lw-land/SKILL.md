---
name: lw-land
description: Use when the user asks to land, merge locally, or put a finished branch on the local base branch. Runs the land command from the branch's worktree, which merges the base in, runs the gate and updates the base checkout. Never pushes to origin.
---

# Land on the local base branch

`ah` below means `python3 .claude/agent-harness/bin/ah`. With only a global install, it is
`python3 ~/.claude/agent-harness/bin/ah`.

Read `.claude/rules/lw/git-workflow.md`. A request to land allows the task commits and the local
merge. It does not allow a push to origin, a deploy or an account action.

1. Read the facts:

   ```bash
   ah config get harness.baseBranch
   ah config get harness.gateCommand
   ah config get harness.testCommand
   ah config get harness.landCommand
   ah config get harness.land
   ```

   An empty gate command stops the land command. Ask the user for the gate, or for a landing
   without it.
2. Work from the task branch's own worktree. Read `git status` and the staged diff, then commit
   only the paths of the task. Never sweep up a peer's change.
3. Run the land command (`harness.landCommand`, by default
   `bash .claude/skills/lw-land/land.sh`). It merges the base branch into the branch, runs the
   gate, and pushes HEAD into the checkout of the base branch through the `push-to-checkout` git
   hook. It waits for a peer's dirty file. It does not wait for an untracked or ignored file in
   the way, and it deletes only one that is identical to the committed copy.
4. Read the exit code and the `is consistent` line, which proves the checkout took the landing.
   Each `page:` line links the page of a landed spec in the base checkout. Report that link, not
   the worktree link, because the worktree page goes away with the worktree.
5. When a `(red on the merged base too)` line names files, report them: the gate was red, and
   the landing added none of its failures.
6. After a landing, the branch and its worktree stay. Run the `lw-branch-audit` skill to see
   whether they are safe to delete. Delete only what the user names.

| Exit | Meaning | What to do |
|---|---|---|
| 0 | landed | Report level `merged`. |
| 2 | the gate is red | Reproduce the failing check with `harness.testCommand` on the files the log names, or with the gate command when it is empty. Fix it, land again. |
| 3 | a merge conflict, or the after-merge command failed | Resolve it in the worktree, commit, land again. |
| 4 | the push did not land | Report the paths it names and wait for that session. |
| 5 | this checkout cannot land | Do what the message says (commit first, git config, gate command, commit address). |

## Options

Each key sits in `[harness.land]` of `.agents/harness.toml`. An empty value turns it off.

| Key | Default | Effect |
|---|---|---|
| `greenToken` | `""` | The gate is green only when it exits 0 and one line of its log equals this text. |
| `probeCommand` | `""` | On a red gate, the land command reads the `verify: failures <json>` record of the log with `ah land-results`. It runs this command on the failed files in a worktree at the merged base. When every file fails there too, the landing goes on. |
| `afterMerge` | `""` | A command that runs after the merge. Its changes to tracked files go into the merge commit. |
| `overlapExclude` | `[]` | Paths that do not count when a base move decides whether a green gate carries over. |

The dials are environment variables: `LAND_ATTEMPTS` (3), `LAND_PEER_TRIES` (10),
`LAND_PEER_WAIT` (60 seconds), `LAND_GATE` (1) and `LAND_TARGET` (the base branch).

The land command needs `receive.denyCurrentBranch = updateInstead`, `core.bare = false` and an
absolute `core.hooksPath` that holds the `push-to-checkout` hook (the dispatcher, with the
harness hook in `harness/`). `ah install --project` sets the first and the last. The message of the land command names the command for each missing one.
With the `noreply-identity` git hook on, every branch commit must carry the noreply address.

Never stash, restore or delete a peer's file, never move the base branch with `update-ref` or
`branch -f`, and never switch the base checkout to another branch. Set `LAND_GATE=0` only when the
user asks for a landing without the gate, and say in the report that the gate did not run.
