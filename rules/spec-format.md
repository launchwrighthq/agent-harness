# Spec format

The spec folders are `specs.dirs` of `.agents/harness.toml`, by default `docs/specs/` for
specifications and `docs/superpowers/plans/` for implementation plans. Read them with
`python3 .claude/agent-harness/bin/ah config get specs.dirs`. Keep an existing document where it is
when you revise it. A new file name starts `YYYY-MM-DD-`. Each document is Markdown with a
descriptive H1, a `Date:` line and a `Status:` line.

Active statuses are `draft`, `approved`, `in-progress`, `analysis` and `blocked`. Settled
statuses are `implemented`, `superseded-by`, `abandoned` and `answered`. A settled status carries
its evidence or its successor path on the status line. The renderer refuses any other status.

`specs.kinds` can give a spec folder the kind `qa`, for example `docs/qa/`. That folder holds
disposable QA reports, rendered by the same renderer with the QA REPORT badge. A QA report takes
`untested` or `tested`, and no other status. It is never archived: delete a report that is done.
The audit and the reconcile commands leave the folder out. Keep the folder out of git when the
reports are scratch notes.

Start a new specification from `.claude/skills/lw-spec-write/skeleton.md` and keep its sections
in order: numbered subsections, an Exclusions list, a Decisions table (`ID | Decision | Why |
Rejected`), Risks, Open questions (`ID | Question | Default until answered`), an Amendments
section after Delivery, then Done means. A subsection details one decision, and
its table row stays short. Name the exact earlier row that a new decision supersedes.

Spec prose follows `.claude/rules/lw/anti-slop.md`:

- One idea per sentence: 20 words or fewer for an instruction, 25 for a description.
- One word per concept. Active voice, present tense. Three or four sentences per paragraph.
- No em dashes, no binary contrast, no puffery, no filler, no summary ending.
- A table for a set of cases, a numbered list for an ordered procedure.
- Code, identifiers, paths, commands and quoted user words stay as they are.

A new specification ends with a Done means table: `ID | Requirement | How to check it | Level`.
Use stable R and D IDs, and A IDs for user amendments. A row describes observable behavior. The
levels come from `.claude/rules/lw/operator-report.md`.

## Render

```bash
python3 .claude/agent-harness/bin/ah spec render <spec folder>/<file>.md
```

The page goes to the page folder of the source's spec folder (`specs.out`, default `dist`), for
example `docs/specs/dist/<name>.html`. Keep the page folder out of git. A page therefore exists
only in the checkout that rendered it.

Link a page by the path that the hook or the land command prints. Never change a worktree path
into the main checkout path by hand. The `render-spec` hook renders a changed source on Write and
Edit and at the end of a turn. At the end of a turn it also checks each page link in the reply. It
renders a missing page whose source is in that checkout, else it holds the turn.

Markdown links, tables, fenced code, and images and videos under the spec folder's `assets/`
render. A `mermaid` block renders as a labelled code block.
