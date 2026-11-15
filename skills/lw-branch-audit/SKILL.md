---
name: lw-branch-audit
description: Use when the user asks which branches or worktrees hold unmerged or uncommitted work, whether a branch is safe to delete, what a worktree is for, or to clean up stale branches, worktrees or stray folders under .claude/worktrees. Also when the disk fills with worktrees, or a branch says "ahead 3" and nobody knows if that is real. Inspects first and deletes nothing unless the user names what to delete.
---

# Audit branches and worktrees

`ah` below means `python3 .claude/agent-harness/bin/ah`. With only a global install, it is
`python3 ~/.claude/agent-harness/bin/ah`.

Read `.claude/rules/lw/git-workflow.md`. The question is never "how many commits ahead". It is: if
this ref disappeared now, what content would be gone? `ah branch-audit` answers that by bytes. You
answer what it cannot: whether that content is still wanted. Then the user decides, and only then
does anything run.

The procedure has five steps, in this order. Never skip to step 5.

## 1. Report

```bash
ah branch-audit --fetch --summary   # one line per verdict, and the megabytes the plan frees per risk
ah branch-audit --prune             # the evidence under every row, then the plan
ah branch-audit --json              # the same report as data
```

Start from `--summary`. When every row is MERGED, ABSORBED or STALLED, no stray folder is listed
and no route disagrees, say so and stop.

Run it from any worktree. It finds the main checkout through `git rev-parse --git-common-dir`,
audits the same set from every worktree, and marks the one it runs in `[here]`. That worktree is
never offered.

### What the verdicts mean

A worktree takes the first verdict that applies. A branch uses MERGED, ABSORBED, UNMERGED and
IN PROGRESS.

| Verdict | Means | Offered |
|---|---|---|
| IN USE | A process works inside it, runs a program from it or holds a file in it, or a git lock names an owner process that is alive. | never |
| STALE SESSION | A live lock, and the transcript of its owner is idle past `branches.sessionIdleDays` (2). | to close, by the user |
| UNCOMMITTED | A tracked change, or an untracked file whose bytes exist nowhere else. | after step 2 |
| HOLDS DATA | Ignored bytes found nowhere else, or a credential-shaped file. | after step 2 |
| UNMERGED | A commit carries a file that the base lacks. | after step 2 |
| IN PROGRESS | A commit, a reflog entry, a loose file or the session transcript of the worktree is newer than `branches.staleHours` (24). | never |
| STALLED | Nothing new, and every loose file is a stray or a copy. | yes |
| ABSORBED | Landed by rebase or cherry-pick: the bytes are in the base under other SHAs. | yes |
| MERGED | Every commit is in the base. | yes |

A loose file is `tracked_change` (an edit hidden by `assume-unchanged` or `skip-worktree`
included), `unsaved`, `stray` (its bytes are committed at that path in the history of the base),
`cache` (under a folder that `branches.rebuildable` names), `copy` (byte-equal to the file of the
main checkout at that path, or an untracked link to that file), `data` (a folder the command
cannot read included), or `credential`. A credential is never opened, even under a rebuildable
folder. A symbolic link is hashed as the link, so its target is never opened either. An unsaved
file marked `base_newer` has a newer commit of the same path on the base: a hint, never proof.

A folder under `branches.worktreeRoot` that git does not list is a STRAY FOLDER, with its size,
owner and newest file. Its printed removal appears only when its newest file is older than
`branches.staleHours` and no process works inside it. A ref in `branches.protect` is never
offered: a deployment ref such as `env/production` is the deployment.

### How a ref is judged

1. Ancestry: `git merge-base --is-ancestor`. True ends it.
2. Patch id: `git cherry` splits the commits into ones already in the base and candidates.
3. Content: each file of each candidate, blob against the base: `identical`, `superseded`,
   `obsolete-draft` (a draft in a spec folder that the base archived, differing only in
   `Status:`), or new.
4. Subject: a candidate that is still new is looked up by its subject. `(a commit with this
   subject is in <base>)` is a rebase with a conflict resolution. Check it by hand.

A second route classifies the diff of the tip from the merge base. A ref whose routes DISAGREE is
never acted on, by the command or by you. It has no row in the plan at any risk, and neither has
its worktree.

## 2. Judge

For each UNCOMMITTED, HOLDS DATA and UNMERGED row, and each stray folder, read what it holds and
decide VALUABLE or GARBAGE. Give one line of evidence, and the evidence is a comparison:

- the base has a newer version (`git diff <base> -- <path>`, or the `base_newer` hint checked);
- the work landed under another commit (`git log <base> --oneline -S '<distinctive line>'`);
- the data is a copy (the `copy` state, or a hash beside the file of the main checkout);
- or nothing else holds it, which makes it VALUABLE.

For an UNMERGED branch, read `git log --oneline <base>..<ref>`, and check in the spec folders
(`ah config get specs.dirs`) whether what it builds is still the plan. Before you trust an
ABSORBED verdict at risk low, spot-check one file: `git rev-parse <ref>:<path>` against
`git log --format=%H <base> -- <path>`. Never open a `credential` file to judge it.

A folder of the worktree that a tool rebuilds, such as a virtual env or a test cache, is GARBAGE.
The report counts it as data until `branches.rebuildable` names it.

## 3. Propose

One table, ranked by megabytes: the row, its verdict, the action, the risk, the evidence. The
`id` column is the `id` of the plan, which step 5 takes.

- A VALUABLE row's action is to save it first: commit it on its branch and land it from that
  worktree (`ah config get harness.landCommand`). Remove it on the next audit.
- A STALE SESSION row's action is for the user to close that session. Nothing here stops a
  process.
- A remote branch, a stray folder and duplicate data inside a kept worktree are printed commands
  (`#` in the plan). Propose them as commands the user runs.

| Risk | Covers | Runs with |
|---|---|---|
| none | Git proves the merge, both routes agree, nothing is forced; a prune. | `--apply` |
| low | A `-D` that only the content check proves; the unlock of a dead lock. | `--apply --only` |
| medium | A `--force` over strays, or a worktree that holds copies. | `--apply --only` |
| high | A remote branch, a stray folder, data, anything that holds content. | never, printed only |

A ref whose routes disagree has no risk level: the plan holds no row for it.

## 4. Wait

Stop. The user approves rows by `id`. Nothing is deleted unless the user names it. An approval of
one plan does not carry to the next run, and a row the user did not name does not run.

## 5. Execute

```bash
ah branch-audit --apply --only remove:foo,branch:foo,unlock:bar,remove:bar
```

`--apply --only` runs exactly the named rows up to risk medium, in plan order: prunes, unlocks,
worktree removals, then branch deletes. A high row named there is refused. An id that the plan
does not hold makes the run exit 1 with nothing run. `--apply` alone runs risk none and stops, and
`--risk` is refused. Each row is audited again just before it runs. A worktree whose files, tip or
lock changed since the report, or a branch whose tip moved, is refused, and the run exits 1. Audit
again and ask again.

A branch landed by rebase or cherry-pick has no ancestry, so `git branch -d` refuses it. The plan
deletes it with `git branch -D` at risk low, because the content check proves each file is on the
base. Run that row only with the spot check of step 2 and the user's word.

Then hand over the printed commands that the user approved and the command never runs:
`git push origin --delete <branch>` for a remote branch, and `rm -rf <literal path>` for a stray
folder or duplicate data. Never delete a remote branch while the base has unpushed commits: the
server is then the only copy. Run such a command yourself only when the user approved that exact
list in the same turn.

Never delete peer work, never stash and never reset by force. Report the megabytes freed and the
free disk space (`df -h .`).

## When the command is wrong

A fix starts as a case. Write the failing scenario into
`agent-harness/tests/fixtures/branch-audit/cases.json` of the harness package first, then fix
`src/agent_harness/work/`. `tests/integration/work/test_branches.py` runs every case.

The repository facts live in the `[branches]` section of `.agents/harness.toml`: `remoteBase`,
`protect`, `worktreeRoot`, `rebuildable`, `staleHours` and `sessionIdleDays`. The base branch, the
spec folders and the land command come from `harness.baseBranch`, `specs.dirs` and
`harness.landCommand`. `--projects-root` names the transcript folder, `~/.claude/projects` by
default.
