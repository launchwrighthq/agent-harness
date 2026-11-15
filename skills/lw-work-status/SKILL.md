---
name: lw-work-status
description: Use when the user types /lw-work-status or asks what is in progress, what is unfinished, which agents ran and what they did, whether something typed in the chat or a section of the spec was dropped, or where a session stands. Reports every work item from evidence (transcripts, git, saved audits) without starting, stopping or merging anything.
---

# Work status

`ah` below means `python3 .claude/agent-harness/bin/ah`. With only a global install, it is
`python3 ~/.claude/agent-harness/bin/ah`.

In Claude Code, run the digest first:

```bash
ah work-status [--session <session-id>]
```

| Flag | Effect |
|---|---|
| `--session <id>` | Another session by id. The default is `$CLAUDE_CODE_SESSION_ID`. |
| `--since 12h` | Move the recency line. The default is the first mention of the newest spec. |
| `--all` | Show finished rows of any age, with no cap. |
| `--spec <path>` | Read this spec instead of the newest one the session named. |
| `--json` | Print the digest as data. |
| `--projects-root <dir>` | Read the transcripts under another folder. The default is `~/.claude/projects`. |
| `--repo` | Print the repository view instead. It reads no transcript and needs no session. |

The digest reads Claude Code transcripts under `~/.claude/projects`, the spec folders of
`specs.dirs` and their archives, the saved audits, and the commits of each agent worktree against
`harness.baseBranch`.

In Codex, or for a peer whose history you cannot read, run `ah work-status --repo`. It lists every
worktree with its commits ahead of and behind the base branch, its dirty file count and its last
commit. Then it lists every unsettled spec with its audit state, and the live goal of each session.
Follow `.claude/rules/lw/codex.md` for the rest: an absent transcript is not an empty task list.

Then check live agents and background jobs with the tools the client has. A `reported` row that
the live list still shows as running is `running`: a finish notification can arrive long before
the agent stops writing. Read `git status` of each relevant worktree without opening an env or
account file.

## Report

1. One table, one row per agent or background job, never one per spec section:

   | Task | Solution | Who | Status | Last seen |
   |---|---|---|---|---|

   Finished rows first, open rows last: `merged`, `landed`, `reported`, `stopped`, `failed`, then
   `running`, then the items the user owes. Oldest first within a state. `Solution` is one clause
   from the spec or the agent's report, or `unknown`. Sections that no agent covers share one
   `not started` row. `Who` is the agent description and short id, `me`, `peer <name>`, or `you`.
2. One line per unanswered chat item that is not in the table, quoted.
3. The Hidden line of the digest.
4. One line per spec the session named that is unsettled or has an open audit: file name, status,
   audit state. Offer `/lw-spec-audit` for a spec that reads `not audited` or `stale`.

A finish message is no proof of a merge. Report the history of a peer session as unknown, never as
empty.

## States

| State | Means |
|---|---|
| `running` | Spawned, with no finish notification, or the live list shows it. |
| `reported` | Finished, and nothing landed in its window. |
| `landed` | Its worktree gained a commit during its run. A shared worktree does not land every agent in it. |
| `merged` | The tip of its worktree is on the base branch. |
| `stopped` | `TaskStop` was called. |
| `failed` | A failed notification or a non-zero exit. |
| `unanswered` | A chat row that no assistant text followed. |

## Mistakes

| Mistake | Instead |
|---|---|
| Reading only the end of the transcript | An open request of any age is in the digest. |
| Reading prompts only | A `typed-while-busy` chat row is a prompt too. |
| Reading only some of the subagent transcripts | One row per spawn, with its report. The digest reads every subagent file. |
| Printing a UTC stamp, so a live session reads hours stale | Print local time, as the digest does. Transcripts are stamped UTC. |
| Rebuilding "unfinished" from status prose | Rows come from records. Prose feeds `Solution`. |
| Reporting from memory after a compaction | Run the digest. The file has what the context lost. |
| Hiding an unfinished item because it is old | Unfinished never hides. `--all` and `--since` move the line. |
| Pasting a tool result or a command line | The digest carries neither, and neither does the table. |

Do not start, stop, delete, merge or publish work while you report its status.
