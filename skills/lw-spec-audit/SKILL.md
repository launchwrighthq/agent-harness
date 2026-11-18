---
name: lw-spec-audit
description: Use when the user types /lw-spec-audit, or asks how much of a spec is built, which requirements are still open, or whether a spec can be marked implemented. Also when the lw-goal-spec Stop hook asks for an audit, and for a sweep of the open specs. An independent reviewer judges each requirement against an exact git commit and saves the rows that lw-goal-spec reads.
---

# Audit a spec

`ah` below means `python3 .claude/agent-harness/bin/ah`. With only a global install, it is
`python3 ~/.claude/agent-harness/bin/ah`.

In Codex, read `.claude/rules/lw/codex.md` first. The levels are in
`.claude/rules/lw/operator-report.md`. Do not run this procedure only because you install or edit
the skill.

The command lists. A fresh reviewer judges. The builder never audits its own work, because it
reads its own intent into the code and misses gaps.

## 1. Digest

Read the base branch, then run the digest from the checkout you audit:

```bash
ah config get harness.baseBranch
ah spec audit digest <spec> [--base <branch>]
```

`--base` defaults to `harness.baseBranch`. A path, a file name or a stem resolves in the spec
folders of `specs.dirs` and their archives. `--open` adds every unsettled spec. `--json` prints
data. `--session <id>` adds every spec the session named and the user's later messages as
amendment candidates. In Codex, pass the paths and the amendments of the current conversation.
When no session history is available, the amendments are unknown. Say so in the report. An empty
list of candidates then does not mean "no amendments".

The digest prints the exact base commit, the status, whether git tracks the file, the requirement
rows, the amendment candidates and the related branches. An explicit Done means row wins over a
numbered decision or a heading. That fallback for an older spec does not replace a read of the
whole document. When the rows come from headings, tell the reviewer to judge the build order or
the phases section first, and the other headings as context. Add a missing requirement to the
audit as a row M1, M2, so that its ID cannot clash with an R ID of the digest. An empty or
partial digest cannot prove that a spec is done.

## 2. One reviewer per spec

Start one fresh reviewer per spec. In Claude Code, the Agent description is
`spec-audit <stem>`: the Stop hook accepts an audit only from an agent whose description starts
with `spec-audit`, and `record` finds that agent's file by the stem. Give the reviewer this prompt,
filled in, and nothing from your own context:

```text
You audit one spec against <base branch>. You did not build it. Trust no report of it.

Spec: <absolute spec path>
Stem: <stem>
Level: <built, merged, pushed, deployed or proven; merged when no goal names one>
Branch: <at level built, the branch that holds the work; else none>
Digest:
<this spec's part of the digest, pasted whole, with its Base line>

Rules:
1. Change no code. Write one file only: .claude/state/rows/spec-audit-<stem>.json in the
   checkout. No edit, commit, stash, checkout or reset.
2. Check each requested behavior on <base branch> by its concept, not only its identifier.
   Use `git grep -n <term> <base branch>` and `git log <base branch>`. Never `grep -r`, which
   also reads every worktree. Try the names a builder would pick instead. At level `built`
   only, read the Branch above in place of <base branch>.
3. Judge supported use, and material feature, logic, integration, UI, layout, interaction,
   localization, text and design failures. A gap names the requirement, a reachable trigger,
   code or reproduction evidence, and a real effect. An explicit requirement stays required
   when it is rare. Do not invent a requirement from a chain of hypothetical failures or a
   corrupted internal state. Credible security, privacy and data-loss failures still count.
   Optional hardening and polish cannot keep a row open. A deliverable may ship under another
   name or as an adequate alternative design. A decision not to build something is done when
   the base branch holds that decision.
4. Work not on the base branch: commits on a branch only are `branch-only`; changes in a
   worktree that nobody committed are `uncommitted`. The digest names branches and worktrees.
5. An amendment candidate that changed the scope and has no row becomes a new row: A1, A2.
   The user's words are its requirement. A requirement that the spec states and the digest
   missed becomes a row M1, M2.
6. `deferred-by-operator` needs the user's words from the digest in `quote`. A deferral by an
   agent, a plan or a brief is `missing`.
7. `blocked` needs `question`: the one question the user must answer.
8. States: done, partly, missing, branch-only, uncommitted, deferred-by-operator, blocked.
   A row is {"id", "requirement", "state", "evidence"}, plus "quote" or "question". Evidence
   names a file and line, a commit, an observed check, or the search that found nothing.
9. Find the material gaps together in the first pass, one finding per root cause. On a later
   pass, check the fixes and their direct regressions only. Do not start a new broad hunt for
   edge cases, and do not raise the bar for acceptance. Before you record, compare
   `git rev-parse <base branch>` with the Base line. If it moved, check again each row that the
   new commits touch.
10. Write one row for each Done means ID. Every row names its evidence. `record` refuses an
    empty rows file, a row with empty evidence, and a Done means ID with no row.
11. Run record yourself, so your own agent file backs the audit:
    python3 .claude/agent-harness/bin/ah spec audit record <stem> --rows .claude/state/rows/spec-audit-<stem>.json --level <level>
12. Return the table that record prints, as it is, and nothing else.
```

A requirement at `proven` needs evidence that already exists. The reviewer takes no account action
and runs no check that changes an external system. Never write reviewer metadata or a transcript
yourself to pass the provenance check. Never run `record` from the main agent.

## 3. The `--open` sweep

Run `ah spec audit digest --open`. Audit at most four specs in parallel: four reviewers in one
message, then the next four. The reviewers write only their own rows files, and each one records
its own audit. Then print one line per spec with open rows, most open rows first.

## 4. Report

Open with the three lines of `.claude/rules/lw/operator-report.md`, then print the table:

```bash
ah spec audit table <spec>
```

The table says "<base> has moved since: re-audit." when the base branch moved after the audit. It
ends with the `Status:` line that the audit supports. Print it as a suggestion. This skill never
edits a `Status:` line, and never settles a spec with an open row.

## 5. Second review by Codex

A `/lw-goal-spec --codex` goal adds Codex as a second reviewer. The audit above still runs first,
and both must approve.

```bash
ah spec codex run <spec> [--base <commit>] [--reply <file>]
ah spec codex task <spec> --worktree <dir> --base <commit> --ids R1,R2 [--reply <file>]
```

`run` checks out the base branch in a detached worktree under `.claude/worktrees/`, links the
`.venv` and `node_modules` folders of each package into it, runs `codex exec --sandbox read-only`
there at reasoning effort `high` with a structured answer schema, and saves the verdict that the
Stop hook reads. `--base` defaults to the commit the goal started from. `task` reviews the
commits of one task in its own worktree, as advice that nothing waits on. Exit 0 is approved, 1 not
approved, 2 no verdict, 3 capped.

A finding has a `kind`: `unmet-requirement`, `defect`, `crossed-rule`, `acceptance-precision`,
`spec-wording` or `style`. Only the first three can block. After five passes that reached a
verdict, `run` refuses and hands the open findings to the user. The cap is never an approval. Only
the user waives a finding. To dispute one, write the reason per finding ID to a file and pass
`--reply <file>`.

## Mistakes

| Mistake | Instead |
|---|---|
| Auditing from the builder's own context | A fresh reviewer gets the digest and nothing else. |
| Accepting a deferral with no quote | Only the user's quoted words make `deferred-by-operator`. |
| Searching only the identifier the spec names | Search for the concept. A deliverable can ship under another name. |
| Recording after the base branch moved | Compare with the Base line, check again, then record. |
| The main agent writes the rows or runs `record` | The reviewer runs `record`, or the Stop hook refuses the audit. |
| Skipping a later chat message because the spec did not change | A message that changed the scope is an `A` row. |
| Waiving a Codex finding yourself | Dispute it with `--reply <file>`. Only the user waives. |
| Auditing at `built` against the base branch | At `built`, the reviewer reads the Branch line of the prompt. |
| Keeping a row open for polish the spec did not ask for | Optional hardening and polish cannot keep a row open. |
