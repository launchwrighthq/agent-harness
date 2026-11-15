# The spec family

`ah spec` renders a spec to its review page and keeps the audit store that the spec skills share.
`ah work-status` reports the work of a session from its transcript, git and the saved audits. All
of them work in any git repository.

```bash
python3 .claude/agent-harness/bin/ah spec render docs/specs/2026-01-01-orders-design.md
```

## Config

The `[specs]` section of `.agents/harness.toml` holds the spec facts. Every key has a default.

| Key | Default | Meaning |
|---|---|---|
| `dirs` | `["docs/specs", "docs/superpowers/plans"]` | The spec folders. A folder whose last part is `plans` holds plans, and its pages carry the PLAN badge. Every other folder holds specifications. |
| `archive` | `archive` | The folder name, inside each spec folder, for settled documents. |
| `out` | `dist` | The folder name, inside each spec folder, for rendered pages. Keep it out of git. |
| `auditDir` | `.claude/state` | The audit store, relative to the main checkout, so every worktree shares one store. An absolute path is used as it is. |
| `kinds` | `{}` | Per spec folder, a table with `kind` (`spec`, `plan` or `qa`), `label` (the badge word) and `statuses` (the vocabulary the renderer accepts). For example `[specs.kinds."docs/qa"]` with `kind = "qa"`, `label = "QA REPORT"`, `statuses = ["untested", "tested"]`. The key names a folder of `dirs` or a folder under one, else the config fails and names the key. A folder under a spec folder is a folder of its own, with its own `archive` and `out` folders: the longest folder that holds a file decides its kind and its page for `render`, `check` and `census`. |
| `checks` | `["archive", "superseded", "pointers", "assets"]` | The checks that `spec check` runs. |
| `pointerFiles` | `["*.md"]` | The git pathspecs of the files whose spec paths `spec check` resolves. |

The audit, goal and Codex commands read the base branch from `harness.baseBranch`. `land.sh`
also reads `harness.gateCommand`. `AGENT_STATE_DIR` overrides `auditDir` for one process.

## Commands

| Command | Behavior |
|---|---|
| `spec render <file.md> [--out <dir>]` | Writes the page to the `out` folder of the source's spec folder, mirroring any `archive/` below it. `--out` names another folder, which must sit inside an `out` folder. It refuses a page that git tracks and an image whose file is missing. |
| `spec render --all` | Renders every source of every spec folder and its archive. A `README.md` describes its folder, so it is no source, as in `census` and `check`. A refused source does not stop the rest: it prints one line that starts with the source and gives the reason, and the command then exits 1. |
| `spec check` | Checks the tracked tree: a settled document outside the archive or an unsettled one inside it, a `superseded-by` path, every spec path in a file of `pointerFiles` outside an archive, and an asset that no document names. Exits 1 on a finding. |
| `spec census [--json]` | Counts the tracked documents of each folder by status. Lists a name in an active folder and its archive, a settled status with no evidence, and a document with no status. |
| `spec verify <old.html> <new.md>` | Renders the Markdown and lists each word of the old page that the new page lost. Exits 1 on a loss. |
| `spec audit digest [<spec> ...] [--open] [--session [<id>]] [--base <branch>] [--json]` | Lists the requirement rows, the amendment candidates and the related branches of each spec, for a fresh reviewer. |
| `spec audit record <spec> --rows <file> [--level <rung>] [--base <branch>] [--auditor <file>]` | Saves the reviewer's rows in the store and prints the table. It refuses an empty rows file, a row with empty evidence, and a Done means ID with no row. |
| `spec audit table <spec>` | Prints a saved audit, and says when the base branch moved since. |
| `spec codex run <spec>` and `spec codex task <spec> ...` | Run Codex as the second reviewer and save its verdict. |
| `spec goal start\|status\|clear\|check` | Start, read or end a goal that the `goal-spec` Stop hook enforces. |
| `spec restate <file> '<status>' [--archive]` | Rewrites the first `Status:` that starts in the first 4000 characters, up to the earliest closer. With `--archive` it refuses an unsettled status and a file in the archive, stages the new status, and moves the file into the archive folder with `git mv`. |
| `spec repoint <old> <new> [<old> <new> ...] [--apply]` | Lists, or with `--apply` fixes, each tracked file that names an old path. |
| `work-status [--session <id>] [--since 12h] [--all] [--spec <path>] [--json]` | The digest of one Claude Code session. [work.md](work.md) has the design. |

A spec argument is a path, a file name or a stem. A name resolves in each spec folder and its
archive, in this checkout first and then in the main checkout.

## The renderer

The renderer reads the Markdown constructs that the specs use and refuses every other one with
the file and the line. `tests/integration/spec/test_render_parity.py` compares the page, byte for
byte, with the golden pages in `tests/fixtures/spec-render/`. The template is
`src/agent_harness/spec/spec-template.html`.

The TypeScript renderer that this port copies is its reference, and it refuses a blockquote or a
fenced code block inside a list item. So this renderer refuses them too, and the refusal says how
to rewrite the source: end the list with a blank line and start the block at the left margin
below it.

## The audit store

| File | Writer | Reader |
|---|---|---|
| `<auditDir>/spec-audit/<stem>.json` | `spec audit record` | the `goal-spec` hook, `spec audit table`, `work-status` |
| `<auditDir>/goal-spec/<session>.json` | `spec goal start` and the `goal-spec` hook | `spec goal status` |
| `<auditDir>/codex-review/<stem>.json` | `spec codex run` | the `goal-spec` hook |
| `<auditDir>/codex-review/passes/<stem>/pass-<n>/` | `spec codex run` and `task` | the next pass |

## Hooks

| Hook | Events | Behavior |
|---|---|---|
| `render-spec` | PostToolUse (Write, Edit), Stop | Renders a changed spec. At Stop it also renders a spec that a shell command wrote, and holds a turn whose reply links a page that does not exist. It renders only inside `CLAUDE_PROJECT_DIR` and its `.claude/worktrees/`. |
| `goal-spec` | Stop | Holds a turn until the saved audit of each goal spec shows zero open rows on the current base commit. |
