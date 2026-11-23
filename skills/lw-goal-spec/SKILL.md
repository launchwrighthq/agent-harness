---
name: lw-goal-spec
description: Use when the user types /lw-goal-spec, or asks to implement a spec until it is fully done, to keep working until the spec is built, or not to stop until it is implemented. Also when the user asks for Codex to review or approve that work. Builds in a git worktree with an agent workflow, also when the user does not say so. Not for writing a spec or only reporting its status.
---

# Build a spec to done

`ah` below means `python3 .claude/agent-harness/bin/ah`. With only a global install, it is
`python3 ~/.claude/agent-harness/bin/ah`.

A goal that ends only when the saved spec audit shows zero open rows. The `goal-spec` Stop hook
reads the audit and git, never the chat. In Codex, read `.claude/rules/lw/codex.md` first.

## 1. Read the facts

```bash
ah config get harness.baseBranch
ah config get harness.gateCommand
ah config get harness.landCommand
```

An empty gate command means the repository has none. Ask the user for the gate before you build.

## 2. Start

```bash
ah spec goal start --session <session-id> [--level merged|pushed] [--codex] [--base <branch>] [<spec> ...]
```

- `--base` defaults to `harness.baseBranch`.
- The level is `merged` by default: that start allows the task commits and the landing on the
  local base branch. `--level pushed` allows a push to origin. No level allows a deploy or a real
  account action.
- A spec with rows at `deployed` or `proven` starts at `pushed`. Those rows wait for the user's
  separate word for the deploy. Audit them at their own level after the user approves that step.
- `--codex` only when the user asks for a Codex review. It exits non-zero when the `codex` CLI is
  absent or not logged in, and names the fix. Tell the user. Never start without it.
- With no spec, the command takes the unsettled spec the session named. Exit 3 prints the
  candidates, numbered: ask the user one question that lists them, then start again with the
  chosen paths. Exit 1 names what is missing.
- Your first reply line is one plain sentence that names the spec, the level and the worktree.

`ah spec goal status`, `check` and `clear` take the same `--session`. `status` prints the goal,
its last verdict and a fresh one. `clear` ends the goal. `/lw-goal-spec status` and
`/lw-goal-spec clear` run the same commands.

## 3. What the Stop hook does

At each turn end the hook judges the goal against the local base branch:

| Verdict | Turn |
|---|---|
| `no-audit`, `stale-audit` | Held: no saved audit, or one of an older base commit. Run `/lw-spec-audit`. |
| `unauthored-audit` | Held: no `spec-audit` reviewer file backs the rows. A fresh reviewer audits again. |
| `open` | Held: rows are `partly`, `missing`, `branch-only` or `uncommitted`. Build, land, audit. |
| `review-owed`, `review-unverified`, `review-rejected` | Held, with `--codex` only. See section 7. |
| `review-capped` | Ends: five Codex passes. Report the open findings. |
| `push-owed` | Held, at level `pushed` only: origin lacks the base branch. Push it. |
| `blocked-unasked` | Held: end the turn with one question that names each blocked ID. |
| `blocked-asked` | Ends: the last message names every blocked ID. |
| `agents-running` | Ends: agents or background jobs still run, and their notifications wake the session. |
| `met` | Ends: every row is closed. The goal is finished. |

The same holding verdict three stops in a row asks for a plain report. The fourth lets the turn
end and marks the goal `stuck`, so a goal never loops a session forever.

## 4. The user's stop wins

The hook reads no chat. When the user says to stop, pause or drop the goal, run
`ah spec goal clear --session <session-id>` at once, then stop. Never use the goal to override an
explicit stop of the user or a higher-priority budget rule.

## 5. Build

A bare `/lw-goal-spec` builds this way too. The user does not need to ask for a worktree or a
workflow.

1. Work in a git worktree at `.claude/worktrees/<name>`, from the local base branch:
   `ah worktree add <name> --from <base branch>`.
2. In Claude Code, build with an agent workflow. Load the `workflow-authoring` skill, then call
   the `Workflow` tool with a script. This skill is the user's opt-in to the workflow. Keep the
   workflow size guideline of the session. In Codex, start one subagent per task.
3. Each agent brief names the worktree path, the requirement IDs it covers and the IDs it
   leaves out. The IDs are for the agent, not for the code: a brief never asks for an ID, a
   spec section or a date in a code comment. A gap that the spec keeps open gets one
   `lw-gap <ID>: <removal condition>` line. Run `ah lint --changed` before each commit.
4. Agents own disjoint files, test files included. Agents commit nothing. The orchestrator
   commits by path.
5. The gate command is the gate. Nothing is done before it is green.
6. Never `git stash`. Never `git add -A`.
7. Consider every wiring row of the spec: env examples, infrastructure, migrations, docs, the CLI
   and the console, and the rows of `<adapterDir>/spec-write/` when it exists. Add a row for each
   one that applies.
8. Land with the land command at level `merged` or higher.

## 6. A requirement you cannot build

Mark it `blocked` with the question for the user. Build the rest. End the turn with a question
that names each blocked ID. Never defer a requirement yourself: only the user defers.

## 7. A Codex goal

With `--codex`, the turn also waits for Codex to approve the base branch, after the audit shows
zero open rows and before a push or a blocked question.

- At `review-owed`, start one review per spec as a background job:
  `ah spec codex run <spec>`. The turn can end while it runs, and its notification wakes the
  session.
- At `review-rejected`, fix each material blocking finding, land, run `/lw-spec-audit`, then run
  the review again. A later pass checks the fixes and their direct regressions only. Optional notes
  need no fix and no further pass.
- At `review-capped`, the spec had five reviews that reached a verdict. The turn ends. Report the
  open blocking findings and let the user decide. The cap approves nothing.
- To dispute a finding, write the reason per finding ID to a file and pass `--reply <file>`.
  Codex decides. Only the user waives a finding.
- Exit 2 means no verdict: read the pass log in the folder it names, then run it again.

## 8. Before the turn that finishes the goal

Run `/lw-spec-audit <spec>`. The Stop hook reads only the saved audit, so a goal with no fresh
audit holds the turn. Do not run the built-in `/goal` at the same time: its checker reads the chat
and refuses a question to the user.

## 9. Final report

Open with the three lines of `.claude/rules/lw/operator-report.md`, then the audit table. A
release by the hook on a stall, a blocked question or the review cap does not mean the work is
done. Report the open rows as they are.
