---
name: lw-spec-reconcile
description: Use when the user asks to tidy the specs, to bring spec statuses in line with what is built, to find stale or abandoned specs, to archive settled specs and plans, or to repair an archived status that names no evidence. Sets each status from evidence, moves settled documents only when allowed, and repoints every reference.
---

# Reconcile spec statuses

`ah` below means `python3 .claude/agent-harness/bin/ah`. With only a global install, it is
`python3 ~/.claude/agent-harness/bin/ah`.

Read `.claude/rules/lw/spec-format.md`. Then read the facts:

```bash
ah config get specs.dirs
ah config get specs.archive
ah config get harness.baseBranch
```

## 1. The archive holds settled documents

Age alone is no reason to archive a document. The archive folder holds the settled statuses, and
the active folder holds the others. So a document waits to be restated, and the move follows in
the same change.

A document of `specs.keep` (`ah config get specs.keep`) is a living reference, such as the main
spec of an application. It stays in its active folder when it is settled. Never move it.

| Settled status | When it applies |
|---|---|
| `implemented` | What the document describes is on the base branch. A recon whose client shipped counts. |
| `superseded-by <path>` | Another document replaced it. The path resolves. |
| `abandoned` | It was built and reverted by decision, or a candidate was rejected on its verdict. |
| `answered` | An analysis whose question is closed and which produced no code. An analysis a reader still consults stays active. |

## 2. The evidence sentence

A settled status names its evidence on the same line:

```
Status: implemented - `src/orders/evict.py` holds the cache, and part 4 landed as `feature-a`.
```

`Status: implemented` alone is a claim that nobody checked. A document with one unbuilt part is
not settled. It stays `in-progress`, stays active, and names the missing part and a branch or a
date. Leaving a document active is a correct outcome.

When the code went wider or narrower than the text, add a `Status note:` paragraph under the
header that says where. When you change a status, keep the earlier account in that paragraph.

## 3. Take the census

```bash
ah spec census
```

It counts the tracked documents of each spec folder and its archive by status, from the first
`Status:` in the first 4000 characters. It also lists a name in both an active folder and its
archive, a settled status with no evidence, and a document with no status. Judge tracked files
only. An untracked draft and active work of another session stay as they are.

## 4. Check each document against the base branch

Read what the document asked for, then check each part on the base branch. Use `/lw-spec-audit`
for an independent check of a spec with a Done means table. Four traps:

- An unchecked plan box does not prove that the work is missing. Git is the evidence.
- A deliverable often ships under another name. Search for the concept, and record the new name
  in the status sentence.
- A path that reads as missing may only have moved. Check the current shape of the tree.
- A document can settle in the negative: a decision not to build something closes an analysis.

Use `git grep`, never a recursive `grep -r`. A recursive grep also reads every worktree under
`.claude/worktrees/` and large untracked data, and can run for minutes.

## 5. Parallel checkers, one writer

For more than about ten documents, start read-only agents in batches of eight. Give each batch the
vocabulary, the traps above and the `git grep` rule. Each agent returns one block per file:

```
FILE: <file name>
VERDICT: implemented | superseded-by <path> | abandoned | answered | STAY-ACTIVE
EVIDENCE: <one sentence that names what it saw, or what is missing>
```

The agents write nothing. You apply the verdicts yourself, one at a time: agents that write to one
git index race for no gain. A verdict an agent cannot verify is a finding, not a gap to fill.

## 6. Apply, then repoint

Apply only the status changes the user allowed. Move a document to the archive only when the user
allowed it. Read its relative links first, because they change depth.

```bash
ah spec restate <file> '<status text without "Status:">' [--archive]
ah spec repoint <old path> <new path> [<old path> <new path> ...]
ah spec repoint <old path> <new path> [<old path> <new path> ...] --apply
```

`restate` rewrites the first `Status:` in the first 4000 characters, and keeps the markup after the
earliest closer of a hand-written HTML page. With `--archive` it refuses an unsettled status and a
file already in the archive. It stages the new status, runs `git mv` into the archive folder, and
prints the `repoint` command for the old path. `repoint` takes one or more path pairs. Without
`--apply` it lists each tracked file that names an old path. With `--apply` it writes the new path
into each of them. Repoint in the same change as the move. A move without a repoint breaks the
references in source comments, memory files and migrations. The `spec` field of each
`lab/<slug>/meta.json` is such a reference: `repoint` rewrites it, and `lw lab check` fails on a
`spec` path that does not exist.

A reference inside the archive is history. Repoint it to the new location of the same file, but do
not change what it claims.

## 7. Two findings of the census

`ah spec census` lists both. No check fails on them, so read each one.

- A name in the active folder and in the archive. A merge can restore a settled document. Read
  both copies before you delete either: the archived copy can be the newer one.
- An archived status that names no evidence. Its place is right, so nothing complains, and the
  claim cannot be checked without doing the work again. Report each one.

## 8. Finish

Render each changed source with `ah spec render <path>`, or every source with
`ah spec render --all`. Run `ah spec check`: it fails on a settled document outside the archive,
an unsettled one inside it, a `superseded-by` path or a spec pointer that does not resolve, and
an asset that no document names. Then run `git diff --check`. Do not move a document while a test suite
runs, because a suite that reads the tree then sees neither the old state nor the new one.

Report what moved, what stays active, and the evidence. Commit nothing and delete nothing that the
user did not allow.
