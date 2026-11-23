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
| `missingAssets` | `refuse` | What `spec render` does with an image under `assets/` that has no file. `refuse` refuses the render. `placeholder` writes the page with a box in place of the image and prints a warning. Another value is a config error. See [Missing images](#missing-images). |
| `auditDir` | `.claude/state` | The audit store, relative to the main checkout, so every worktree shares one store. An absolute path is used as it is. |
| `kinds` | `{}` | Per spec folder, a table with `kind` (`spec`, `plan` or `qa`), `label` (the badge word) and `statuses` (the vocabulary the renderer accepts). `label` and `statuses` default to those of the kind. The key names a folder of `dirs` or a folder under one, else the config fails and names the key. A folder under a spec folder is a folder of its own, with its own `out` folder and, unless its kind is `qa`, its own `archive` folder: the longest folder that holds a file decides its kind and its page for `render`, `check` and `census`. See [QA reports](#qa-reports). |
| `checks` | `["archive", "superseded", "pointers", "assets"]` | The checks that `spec check` runs. |
| `pointerFiles` | `["*.md"]` | The git pathspecs of the files whose spec paths `spec check` resolves. |
| `keep` | `[]` | Paths of settled documents that stay in their active folder, such as the living reference spec of an application. `spec check` does not ask to archive them and reports a path that names no document of an active folder. `lw-spec-reconcile` never moves them. |

The audit, goal and Codex commands read the base branch from `harness.baseBranch`. `land.sh`
also reads `harness.gateCommand`. `AGENT_STATE_DIR` overrides `auditDir` for one process.

### QA reports

A folder of kind `qa` holds disposable QA reports. Add it to `dirs`, or name a folder under a
spec folder, and give it the kind:

```toml
[specs]
dirs = ["docs/specs", "docs/plans", "docs/qa"]

[specs.kinds."docs/qa"]
kind = "qa"
```

| Kind | Badge | Statuses | Archive |
|---|---|---|---|
| `spec` | SPECIFICATION | the spec-format vocabulary | yes |
| `plan` | PLAN | the spec-format vocabulary | yes |
| `qa` | QA REPORT, in purple | `untested`, `tested` | none |

A `tested` chip reads as live, like `approved`. The renderer and the `render-spec` hook treat a
QA report like any spec. At the end of a turn the hook also finds a report in a gitignored QA
folder, which `git status` lists only with `--ignored`. A QA folder has no archive, so
`spec restate --archive` refuses a report, and `spec check` and `spec census` look for no archive
there. `spec audit digest` and `spec audit record` refuse a named report, and `--open` and
`--session` leave reports out. A project with no `qa` folder sees no change.

## Commands

| Command | Behavior |
|---|---|
| `spec render [--strict] <file.md> [--out <dir>]` | Writes the page to the `out` folder of the source's spec folder, mirroring any `archive/` below it. `--out` names another folder, which must sit inside an `out` folder. It refuses a page that git tracks. It refuses an image whose file is missing, unless `specs.missingAssets` is `placeholder`. `--strict` refuses such an image in each mode. |
| `spec render [--strict] --all` | Renders every source of every spec folder and its archive. It follows `specs.missingAssets` and `--strict` like the render of one file. A `README.md` describes its folder, so it is no source, as in `census` and `check`. A refused source does not stop the rest: it prints one line that starts with the source and gives the reason, and the command then exits 1. |
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
the file and the line. The constructs are a header block of H1, `Date:` and `Status:`, the
optional `Subject:` and `Range:` fields and a standfirst, headings h2 to h4, paragraphs, ordered
and unordered lists with nesting, fenced code blocks, pipe tables, blockquotes, images and videos
under `assets/`, inline code, bold, italic, strikethrough and links. A literal Markdown mark in a
finished page is a fact that a reader believes, so the renderer never passes one through.
`tests/integration/spec/test_render_parity.py` compares the page, byte for
byte, with the golden pages in `tests/fixtures/spec-render/`. The template is
`src/agent_harness/spec/spec-template.html`.

A fenced code block or a blockquote indented under a list item renders inside that `<li>`, as in
CommonMark. The block starts on the item text line below, with or without a blank line between.
A code block loses the indent of its opening fence, so a line in it that looks like a list
marker stays code. A paragraph indented under the item after the block stays in the item too.
A fenced code block in an item refuses a line at or left of the item marker, and the refusal says
to indent the line under the item text.

### Missing images

An image or a video names a file as `assets/<file>`. `specs.missingAssets` decides what the
renderer does when that file does not exist:

| Mode | Page | Output | Exit |
|---|---|---|---|
| `refuse` (default) | None. | On stderr: `<file>:<line>: the image assets/<file> has no file at <folder>/assets/<file>` | 1 |
| `placeholder` | Written. Each such image is `<span class="missing-asset">image missing: assets/<file></span>`, a box with a dashed frame. | On stderr, one line per image: `warning: <file>:<line>: the image assets/<file> has no file at <folder>/assets/<file>; the page shows a placeholder`. On stdout: `wrote <page> (<n> sections)` | 0 |

```toml
[specs]
missingAssets = "placeholder"
```

Use `placeholder` while a spec waits for its boards, so the text has a review page before the
images exist. A missing `.mp4` or `.webm` gets the same box with the same text.

`ah spec render --strict` refuses a missing image in each mode, with the message and the exit
code of the mode `refuse`. Run it before a spec lands, so no page with a box reaches the base
branch. `--strict` can stand at any position, and it works with `--all`. With `--all` in the
mode `placeholder`, a spec with a box counts as rendered.

Two refusals stay in each mode, with or without `--strict`: an inline `data:` image, and an
image path outside `assets/`.

A value other than `refuse` and `placeholder` stops the command at exit 1 with no page:
`ah spec: specs.missingAssets must be one of refuse, placeholder, got '<value>'`.

The template changed by the one CSS rule `.missing-asset`, so each page differs from a page of
0.2.11 by that rule.

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
| `render-spec` | PostToolUse (Write, Edit), Stop | Renders a changed spec. At Stop it also renders a spec that a shell command wrote, and holds a turn whose reply links a page that does not exist. It renders only inside `CLAUDE_PROJECT_DIR` and its `.claude/worktrees/`. It follows `specs.missingAssets` of the checkout that it renders in: in the mode `placeholder` it adds each `warning:` line after the page link. It has no strict mode. |
| `goal-spec` | Stop | Holds a turn until the saved audit of each goal spec shows zero open rows on the current base commit. |
