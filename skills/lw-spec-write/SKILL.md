---
name: lw-spec-write
description: Use when the user asks to write, draft or revise a specification, a design or an implementation plan, when a spec render is refused, or when an old hand-written HTML spec needs converting to Markdown. Writes the Markdown source with observable acceptance criteria and renders the review page. Not for checking whether a spec is built, which is lw-spec-audit.
---

# Write a spec

`ah` below means `python3 .claude/agent-harness/bin/ah`. With only a global install, it is
`python3 ~/.claude/agent-harness/bin/ah`.

Read `AGENTS.md`, the rules it lists, and `.claude/rules/lw/spec-format.md`. Read the current
implementation before you describe a gap or propose a new subsystem. Read the newest spec of the
same subject, and name the earlier spec that a new design continues or replaces.

The project notes come from the adapter folder (`ah config get harness.adapterDir`). Read
`<adapterDir>/spec-write/` when it exists. It lists the wiring rows of the project, such as
translation files, infrastructure and command versions. When it is missing, use the generic rows
of step 4.

## 1. Where it goes

The spec folders come from the config. Read them first:

```bash
ah config get specs.dirs
```

A folder whose last part is `plans` holds implementation plans. Each other folder holds
specifications. With the default config:

| Document | Path |
|---|---|
| Specification | `docs/specs/YYYY-MM-DD-<slug>.md` |
| Implementation plan | `docs/superpowers/plans/YYYY-MM-DD-<slug>.md` |

Keep a destination and a format the user named. Start a new specification from
[skeleton.md](skeleton.md), and keep its section order. Do not move an existing spec only to give
it the new format.

The source is Markdown and the page is generated. Each spec folder has an `assets/` folder for
images, an `archive/` folder for settled documents (`ah config get specs.archive`) and a page
folder (`ah config get specs.out`, default `dist`). Keep the page folder out of git.

## 2. Procedure

1. State the problem and the proposed behavior first. Cover scope, inputs, outcomes, failure and
   recovery, persistence and user controls where they apply.
2. Keep apart the current implementation, the proposed change, the synthetic checks and the live
   qualification. A mock that passes or a configured driver name does not prove a capability.
3. Give each decision and requirement a stable ID. End with a Done means table:
   `ID | Requirement | How to check it | Level`.
4. Name migrations, env examples, infrastructure, documentation, console and API wiring only
   where they apply. Consider each one and each row of the adapter notes, and add a row for each
   one that applies.
5. Record a scope change from the user as an amendment row (A1, A2) before you build on it. Write
   the date the user asked. Only the user defers a requirement.
6. Quote the words of the user that started the spec in "What the user asked". Put each question
   you cannot answer in the Open questions table, with the default that holds until the user
   answers.
7. Give each decision row its reason and the option it rejects.
8. Date each measurement: "4.2 s on <YYYY-MM-DD>". A number with no date is a claim.
9. Render the page and check it (section 6).

A draft stays `draft` until the user accepts it. An approved design does not allow a commit, a
push, a deploy or a real account action by itself. When the user approves a spec, ask whether to
commit it.

## 3. The header block

```
# <Title>

Date: 2026-08-18
Status: draft
```

- `Date:` and `Status:` are required. A missing `Date:` falls back to the `YYYY-MM-DD-` prefix of
  the file name. A file with neither does not render.
- `Status:` takes one value of the vocabulary in `.claude/rules/lw/spec-format.md`. It may carry
  prose after the value, and it may sit above the title. `superseded-by` keeps its path, and the
  path must resolve.
- `Subject:` and `Range:` are optional. An absent field prints no chip.
- An `in-progress` status names the missing part, and a branch or a date:
  `Status: in-progress - R4 and R7 open, branch feature-a`.
- A `Status note:` paragraph under the header records where the code went wider or narrower than
  the text, and the earlier account of the status when a reconcile changed it.

Write the clean four-line form for a new document. The renderer reads the older shapes too, so a
revision does not need a new header first.

## 4. Done means

The last numbered section is `## N. Done means`, with one table:

```
| ID | Requirement | How to check it | Level |
|---|---|---|---|
| R1 | A full cache evicts its least recently used entry. | A test fills it and adds one. | merged |
```

- One row per requirement. Reuse the decision IDs (`D1`) where they exist. Number other rows
  `R1`, `R2`.
- A row states what must be true, never where the code sits. "`seed --reset` is gone from the
  parser" is a code shape. "`seed` refuses `--reset`" is the behavior, and the behavior is the
  row. A reviewer who holds a shape holds it exactly, and that costs review passes.
- The level is one rung of `.claude/rules/lw/operator-report.md`: `built`, `merged`, `pushed`,
  `deployed` or `proven`.

`/lw-spec-audit` reads this table and checks each row against the base branch. `/lw-goal-spec`
holds the turn until that audit has no open row. A requirement that lives only in the chat
reaches neither.

## 5. Images and diagrams

Put the file in the `assets/` folder of the spec folder, and reference it relatively:

```markdown
![Entity diagram of the order model](assets/order-model.svg)
```

- The renderer rewrites the path for the depth of the page, so a page in `dist/archive/` gets
  `../../assets/`. A link to `assets/<file>` takes the same walk.
- A `.mp4` or `.webm` file renders as a video player with controls.
- A paragraph that holds only one image renders as a wide figure.
- A `data:` URI refuses to render. An image belongs in `assets/`, where a reader can open, reuse
  and diff it.
- A diagram is an `.svg` or `.png` in `assets/`. A `mermaid` block renders as a labelled code
  block, not as a drawing.
- The image file must exist. A typo in the name refuses the render.
- A spec that adds or changes a screen, a state, a dialog or a flow needs a picture. Load
  `/lw-spec-mockup` and draw the board before the spec goes to review.
- A visual that Markdown cannot express (a mockup built from nested elements) stays a hand-written
  `.html` page among the sources. Commit that page as it is. It has no Markdown sibling, and the
  renderer never runs over it.

## 6. Render and hand back the page

The `render-spec` hook renders a spec on Write and Edit, and at the end of the turn for a file that
a shell command wrote. It gives you the `file://` link. To render by hand:

```bash
ah spec render <spec folder>/<file>.md
```

End the turn with the `file://` link on a line of its own, again after every revision. Never type
the link. A spec written in a worktree has its page in that worktree only. After a landing, give
the link that the land command prints.

`ah spec render --all` renders every source of every spec folder and exits 1 when one is refused.
`ah spec check` checks the folder layout: settled documents only in the archive, every
`superseded-by` path and spec pointer resolves, and no asset that no document names.

The page costs about 1.6 to 3.4 times the tokens of its source. To read or search a spec, read the
Markdown.

## 7. When a render is refused

| Message | Cause and fix |
|---|---|
| `not under <folders>, so it has no document kind` | The file is outside every spec folder. Move it, or add its folder to `specs.dirs`. |
| `Status '<x>' is not one of ...` | The status is outside the vocabulary. Use a value of the spec-format rule. |
| `missing 'Date:' below the H1, and ... has no YYYY-MM-DD- prefix` | Add a `Date:` line. |
| `missing a '# H1' at the top of the file` | Add the `# Title` line above the first `##`. |
| `missing 'Status:' below the H1` | Add a `Status:` line. |
| `an inline data: image belongs in the domain's assets/` | Save the image under `assets/` and link it there. |
| `an image must be referenced as assets/<file>` | Move the image under `assets/`. |
| `the image assets/<file> has no file at <folder>/assets/<file>` | Fix the name, or add the file. |
| `a blockquote cannot nest inside a list` | Move the quote out of the list. |
| `a fenced code block cannot nest inside a list` | Move the code block out of the list, or indent it as list text. |
| `a rendered page belongs under its domain's dist/` | The destination is wrong. Pass no destination. |
| `already tracked by git` | A hand-written page has that path. Find out which kind it is. Never remove it only to make the render pass. |

## 8. Convert an old hand-written HTML spec

Convert only a page with no mockup. A page with a mockup stays HTML.

1. Write the Markdown source beside the page, from the page text. Keep every heading, table, list
   and decision ID.
2. Save each inline image and each inline `<svg>` as a file in `assets/`, and link it.
3. Render the Markdown. The page goes to the page folder, so it never overwrites the original.
4. Compare the words: `ah spec verify <old.html> <new.md>`. It renders the Markdown, lists each
   word of the old page that the new page lost, and exits 1 on a loss. A word that only the
   Markdown has is listed and does not fail.
5. Delete the old page only when the user allows it, in a separate change. The old page can come
   back from history: `git show <rev>:<path>`.

A word-for-word match proves the text and nothing else. A lost callout or a diagram that lost its
line breaks still keeps every word, so check the shape on the rendered page.

## 9. Settle a document

A settled status names its evidence on the same line:

```
Status: implemented - `src/orders/evict.py` holds the cache, landed as `feat(orders): evict`.
```

A document with one unbuilt part is not settled. It stays `in-progress` and names the missing part,
and a branch or a date. Settle, archive and repoint in one change with `/lw-spec-reconcile`.

## 10. Report

Give the source path and the `file://` link of the rendered page. Writing a spec does not mark a
requirement done: `/lw-spec-audit` checks the build later.
