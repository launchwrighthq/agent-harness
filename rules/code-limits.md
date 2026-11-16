# Code limits

## Structure

- **One concern per file.** A route file parses, checks access and calls a service. A service
  holds the logic. Infrastructure, such as a client of an outside API, sits in its own file.
- **Small functions with one purpose.** Split a function that does several things, or that needs
  deep nesting to read.
- **Keep the layer boundary.** No SQL and no HTML assembly in a route or a command handler. A
  presentation component takes data and gives markup: no SQL, no network call, no business rule.
- **Respect the package boundary.** An app may import a package. A package never imports an app.
- **Extract on the second copy.** Move duplicated logic or markup into one helper with
  parameters, and call it from both places.

## Comments

Code is the source of truth for what the code does. A comment holds only what the code cannot
say: an invariant, a unit, an owner, a rejected option that looks better, or a fact from outside
the file such as a supplier behavior, a measured number or a spec row.

The caps come from the config. `ah config get harness.comments` prints them, and the defaults
are below.

- **A comment block of a new file has 12 lines at most** (`maxBlock`).
- **A comment block of any file has 25 lines at most** (`longBlock`). A longer explanation is a
  document. Put it in the docs and leave one line that names it.
- **A new file with 60 code lines or more keeps its comment share at 0.35 of all lines at
  most** (`shareFloor`, `maxShare`).
- **Never restate the code below.** When the comment and the signature say the same thing,
  delete the comment.
- **A comment that names a file, a symbol or a spec is a claim.** Keep it true when the target
  moves.
- **No history in a source file.** "This used to be X" and a landing date belong in the commit
  message or the docs.

A file is new when the base branch (`ah config get harness.baseBranch`) does not hold it. A file
that git finds renamed from one the base branch holds keeps the caps of its old path. A path
pattern in `harness.comments.overrides` gives its files their own caps, for example one line per
block and `maxLineLength` 100 for `**/*.tf`. `harness.comments.exclude` is the exemption
register: it only shrinks. `ah comments` checks the budget, and the `comment-budget` hook refuses
a write over a block cap when the project turns it on.

## Errors

- **Fail fast at the boundary.** Validate input and config where they enter. Refuse a bad state
  early, with a message that names the value and the fix.
- **Never swallow an error.** No empty `catch`. Handle the error, or let it go up, and log it
  with its context, such as the host or the id, through the project logger.
- **Throw a specific error type** where it helps the caller decide what to do.
- **A failure that a user sees is localized and escaped,** never a raw stack trace.
