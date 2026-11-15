# Report at the end of a task

An end-of-task report opens with three lines: 60 words at most, no path, no identifier.

```
Done: <one plain sentence>
Not done: <one plain sentence, or "nothing">
Needs you: <one plain sentence, or "nothing">
```

- **Then the spec audit table**, when a spec is involved, then the detail.
- **An offer such as "say go" goes into `Needs you`.**
- **A model or session limit never goes there.** It is one clause in the detail.
- **Every "done" names its level.** `built` is green on a branch, `merged` is on the local base
  branch (`ah config get harness.baseBranch`), `pushed` is on origin, `deployed` is on an
  environment, `proven` is a reading from production.
- **A one-line answer to a question is exempt.**

## How an explanation reads

An agent brief, a commit message, a spec, a code comment and a test name stay technical, with
every identifier spelled in full. To the user, an explanation opens in words and then gives the
identifiers. Its first sentence names the thing in the user's words and has no identifier. The
detail stays, below the plain part.

- **Ten backticked identifiers at most** in a reply that ends a turn. Past that, the detail goes
  in a table, a spec or a document that the reply names.
- **A repository noun gets a gloss of five words or fewer** the first time a message uses it.
- **A number carries its unit and what it compares to.** "4.6 seconds, about one download",
  never "4.6".
