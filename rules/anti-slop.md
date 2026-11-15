# Anti-slop: prose and code

Every agent follows this rule, Codex included. A user's global agent instructions do not reach
every client, a teammate or a CI agent, so the harness ships them here.
`python3 .claude/agent-harness/bin/ah lint <path>` finds the banned words, the banned fillers and
em dashes in Markdown and in code comments.

## Prose

Write prose in Simplified Technical English (the ASD-STE100 style). Controlled prose is faster to
read and easier to check, and not every reader is a native English speaker.

- One idea per sentence: 20 words or fewer for an instruction, 25 for a description.
- One word per concept. Pick the clear term and repeat it. A handler stays a handler.
- Active voice, present tense. "The hook reads the command."
- One topic per paragraph: three or four sentences, then a break.
- Concrete over abstract: exact field names, endpoints, paths and numbers.
- Facts only. No hedging, no filler sections, no thinking out loud.

Code, identifiers, API and CLI names, file paths, command output, error strings, URLs and commit
subjects are exempt. Reproduce them as they are.

## Banned words

`delve`, `foster`, `leverage`, `utilize`, `facilitate`, `empower`, `streamline`, `robust`,
`cutting-edge`, `paradigm shift`, `game changer`, `transformative`, `elevate`, `embark`,
`supercharge`, `harness` (the verb), `ever-evolving`, `tapestry`, `realm`, `beacon`,
`multifaceted`, `meticulous`, `intricate`, `paramount`.

A term of art keeps its meaning: a test harness is still a test harness.

## Banned fillers

`it's worth noting`, `it's important to note`, `at the end of the day`, `when it comes to`,
`at its core`, `in today's world`, `the reality is`, `let's dive in`, `here's the thing`,
`let me be clear`, `I'll be honest`, `this changes everything`.

## Banned patterns

| Pattern | Example | Write instead |
|---|---|---|
| Binary contrast | "It's not X, it's Y." | State Y. |
| Faux insight | "What most people get wrong" | The claim alone. |
| Colon reveal | "The detail that makes it work: a second agent." | A plain sentence. |
| Superficial analysis | A trailing "highlighting", "underscoring", "showcasing" | The real consequence. |
| Puffery | "Marks a pivotal moment", "plays a vital role" | The fact. |
| Weasel attribution | "Experts agree", "studies show" | The source, or no claim. |
| Summary ending | "In conclusion", a last paragraph that repeats the answer | Stop at the last concrete point. |
| Closing kicker | A metaphor or a mic-drop line at the end | Delete it. |
| Formatting slop | Emoji in headings, bold inside a sentence, a header over two sentences | Plain text. |
| Em dash | U+2014, at any length | A period, a comma, a colon or parentheses. |

The middle dot (U+00B7), the bullet (U+2022) and the en dash (U+2013) are not separators in text a
reader sees either. Use a sentence, commas with "and", or labelled fields.

## Code

- **No adjective names.** `enhanced_`, `improved_`, `smart_`, `_v2`, `_new`, `_final`, `helper`,
  `utils`. Name what the thing does: `renew_lease`, `source_number`.
- **No defensive wrapping that hides.** A `catch` that returns null "to be safe" turns a bug into
  a silent wrong answer. Wrap the error with context, or let it go up.
- **No speculative abstraction.** A base class with one subclass, a config option nothing sets, a
  parameter no caller passes. Build the second case, then extract.
- **No type in the name.** `config_dict`, `items_list_array`.
- **No emoji** in code, log lines, docstrings or commit messages.
- **No commented-out code and no `TODO: implement`.**
- **No reformat or rename outside the change.** A diff of 40 files for one fix cannot be reviewed.
- **No Markdown bold and no Markdown heading inside a source comment.**

## The test for a diff

Read the diff as if someone else wrote it. For each line, ask: does deleting it lose
information? A line that stays only because it "adds context" is slop. Delete it.
