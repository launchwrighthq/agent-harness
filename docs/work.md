# Work in progress

`ah work-status` reports what one session is doing, from its transcript, git and the saved audits.
`ah work-status --repo` reports what the repository holds in flight, from git and the store alone.
Both only read. `ah branch-audit` judges what each branch and worktree holds, and deletes only the
rows the user names.

```bash
python3 .claude/agent-harness/bin/ah work-status
python3 .claude/agent-harness/bin/ah work-status --repo
```

## work-status

### The problem

An answer from the memory of a session loses work in three ways.

1. Compaction drops the middle of a long session. Only a summary of it survives.
2. A message typed while the agent is busy arrives as a `queued_command` attachment, not as a
   prompt. Most such messages never come back as a prompt.
3. An agent posts a finish notification that can arrive before its commit exists, and can
   arrive more than once.

The files under `~/.claude/projects` keep all three, so the digest reads them.

The client files one event in more than one record type, and the types change between client
versions. A finish notification can be a user record, a `queued_command` attachment, or only the
`enqueue` record of the queue. The digest reads all three and counts each notification text once.
A peer message is a `queued_command` attachment while the session is busy, and a user record with
`isMeta` and an `origin` of kind `peer` while it is idle. An agent that another agent or a workflow
started notifies its parent only, so its own file decides its state: a file that ends in an
assistant record with `stop_reason` `end_turn` is a finished agent.

### Sources

| Source | What the digest takes from it |
|---|---|
| `~/.claude/projects/*/<session>.jsonl` | Every file of the session, merged by time. A session that enters a worktree writes under a second folder. |
| `~/.claude/projects/*/<session>/subagents/agent-<id>.jsonl` | The report of each agent: its last assistant text. A file that ends its turn marks the agent finished. |
| The spec folders of `specs.dirs` and their archives | The `Status:` and the numbered sections of each spec that the session named. |
| `<auditDir>/spec-audit/<stem>.json` | The audit state of each spec that the session named. |
| `git` in each agent worktree | Whether the agent landed a commit during its run, and whether the worktree is dirty. |

A tool result can echo a token, so the digest reads no tool result body. It reads the human
text, the assistant text, the agent descriptions, the notification summaries and one line of each
report. The user's own shell commands (`<bash-input>`) and their output (`<bash-stdout>`,
`<bash-stderr>`) are no chat: output can echo a token, so the digest drops all three. Git runs
only in a worktree of the repository that the digest reports on, with `core.fsmonitor` off.

### Sections

| Section | Rows |
|---|---|
| Header | The session name and id, its cwd and worktree, the start, the last record, the compactions, the prompt count, and the `/goal` condition. When the cwd is outside the repository that the command runs in and its worktrees, a `repository <path>` line names the checkout of the session. Run the command there for its worktrees and spec files. |
| Spec | The newest spec or plan path named, its `Status:`, and each numbered section with its first sentence and its agents. `--spec` picks another one. |
| Specs named | One row per spec or plan path named: file name, status (`missing` with no file), first and last mention, audit state. |
| Agents | One row per `Agent` spawn: short id, description, model, section, worktree, state, last seen, report. |
| Background | One row per background command or workflow: description, state, exit code. |
| Chat | Each human prompt and each typed-while-busy message, flagged `unanswered` when no assistant text followed it before the next human message. A peer message does not end that window. A request that the user interrupted (`[Request interrupted by user]`) and went on with takes the answer of the next message. |
| Peers | Each message from a peer session. |
| Hidden | How many finished rows the window hides, and the flags that show them. |

### The recency rule

The window opens at the first mention of the newest spec. With no spec, it opens four hours
before the last record. `--since 12h` moves the line and `--all` removes it.

An unfinished row shows at any age. A finished row shows only when its last activity is inside
the window. The Hidden line counts the others. Chat keeps the newest 30 answered rows and every
unanswered row. Specs named folds a settled spec whose audit is `all done` or `not audited` into
one count line, at any age.

### States

| State | Predicate |
|---|---|
| `running` | Spawned, with no finish notification. |
| `reported` | A finish notification arrived, and nothing below applies. |
| `landed` | The worktree that the prompt names gained a commit between the spawn and two minutes after the last sighting. |
| `merged` | The worktree HEAD is the tip of the base branch, or a merge on the base branch lists it as a second parent. |
| `stopped` | `TaskStop` was called on it. |
| `failed` | The notification status is not `completed`, or the exit code is not 0. |

### Decisions

- A peer session is read by id with `--session <id>`. A name needs a session registry that a
  credential guard protects, so the digest does not map names.
- Times print in local time. A UTC stamp makes a live session look stale to a reader outside UTC.
- A spec path inside an archive folder matches too. A settled spec moves there, and a session that
  reads it names that path.

## work-status --repo

The view needs no transcript, so it works in Codex and for a peer session. It prints three tables.

| Table | Rows |
|---|---|
| Worktrees | Every worktree of `git worktree list`: name, branch, commits ahead of and behind `harness.baseBranch`, dirty file count, last commit. |
| Specs | Every spec and plan in the spec folders, the archives left out, whose status is not settled, with its audit state. |
| Goals | The live goal of each session in `<auditDir>/goal-spec/`: its specs and its level. |

`--json` prints the same data.

## branch-audit

`ah branch-audit` answers one question for each worktree, local branch, remote branch and stray
folder: if this ref disappeared now, what content would be gone? It prints a verdict per row and a
prune plan. It deletes only what `--apply` runs, and `--apply --only <ids>` runs only the rows the
user names. The `lw-branch-audit` skill holds the procedure around it.

```bash
python3 .claude/agent-harness/bin/ah branch-audit --summary
python3 .claude/agent-harness/bin/ah branch-audit --prune
python3 .claude/agent-harness/bin/ah branch-audit --apply --only remove:foo,branch:foo
```

### Config

| Key | Default | Meaning |
|---|---|---|
| `branches.remoteBase` | `""` | The remote base. Empty means `origin/<harness.baseBranch>`. |
| `branches.protect` | `[]` | Refs never offered, with or without the remote name. `--protect` replaces the list. |
| `branches.worktreeRoot` | `.claude/worktrees` | The folder whose unlisted subfolders are stray folders. |
| `branches.rebuildable` | `node_modules`, `.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache` | Folder names whose files are `cache`. |
| `branches.staleHours` | `24` | A worktree or a branch touched inside this many hours is IN PROGRESS. A stray folder is offered only past it. `--stale-hours` overrides it. |
| `branches.sessionIdleDays` | `2` | A live lock whose session transcript is idle this many days is a STALE SESSION. |

The base comes from `harness.baseBranch`, the spec folders of the `obsolete-draft` check from
`specs.dirs` and `specs.archive`, and the land command of the plan footer from
`harness.landCommand`. The config of the main checkout is read, whichever worktree runs it.

### Modules

| Module | Holds |
|---|---|
| `work/branches.py` | The options, the audit of each worktree, the stray folders, the run and the entry point. |
| `work/branch_refs.py` | The judge of one ref: ancestry, patch id, content, subject, and the second route. |
| `work/branch_files.py` | The loose files of a worktree, judged by their bytes. |
| `work/branch_procs.py` | The process table, the git lock and the session transcript of a worktree. |
| `work/branch_plan.py` | The plan, its risks, and `--apply` with the audit again before each row. |
| `work/branch_report.py` | `--json` and the report a person reads. |
| `work/branch_git.py` | The git reads, with the blob lookups cached for one pass. |

### Decisions

- A ref is acted on only when two routes agree: the commit walk and the tip diff from the merge
  base. A ref whose routes disagree has no plan row.
- A worktree is never pruned while a process works inside it, a git lock names a live owner, or a
  commit, a reflog entry, a loose file or its session transcript is newer than `staleHours`.
- `-d` deletes a branch that git proves merged. `-D` deletes a branch landed by rebase or
  cherry-pick, at risk low, because the content check proves each file is on the base.
- A remote branch, a stray folder and duplicate data are printed for the user and never run.
- `branches.main` takes `before_apply`, a function that runs after the audit and before
  `--apply`. The cases pass one to stand in for a peer that writes while the plan waits.
- `tests/fixtures/branch-audit/cases.json` holds every behaviour. A fix starts as a case there.
