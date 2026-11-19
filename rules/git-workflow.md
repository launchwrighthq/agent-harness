# Git workflow

- **Commit and push only when the user asks.** On the default branch, create a branch first.
- **Conventional commits:** `type(scope): summary` (`feat`, `fix`, `refactor`, `chore`, `docs`,
  `test`), imperative mood, present tense.
- **One logical change per commit.** Do not mix a refactor with a fix.
- Do not pass `--no-verify` past the git hooks without a reason you state.

## Several sessions share one checkout

Other sessions work in this repository at the same time, and you cannot see their work.

- **Work and commit in your own worktree:** `<repo>/.claude/worktrees/<name>` and nowhere else.
  `python3 .claude/agent-harness/bin/ah worktree add <name> [--from <ref>]` makes one on the new
  branch `<name>` and records your session as its owner, `ah worktree claim <name>` takes one
  that has no owner, and `ah worktree remove <name>` removes a clean one. The
  `worktree-path-guard` hook refuses a `git worktree add` at another path.
- **Land with the `lw-land` skill** from the worktree that holds the branch. It merges the base
  branch in, runs the gate, and pushes into the checkout of the base branch through the
  `push-to-checkout` git hook. No step is the user's.
- **The base branch** is `harness.baseBranch`. Read it with
  `python3 .claude/agent-harness/bin/ah config get harness.baseBranch`.
- **Never move a ref alone** (`git update-ref`, `git branch -f`). The checkout keeps its old index
  and files, and a peer then commits the reverse of the work. A hook refuses it.
- **Never `git stash`.** Every worktree shares one stash stack.
- **Never `git add -A`.** Stage named paths, then confirm that every staged path is yours.
- Never pass a folder to `git restore`, `git checkout` or `git clean` in a shared checkout.
- Run `git fetch` before you conclude anything about a remote branch.

## Levels of done

`built` is green on a branch. `merged` is on the local base branch. `pushed` is on origin.
`deployed` is on an environment. `proven` is a reading from production. A request to merge
locally allows the task commits and the landing. It does not allow a push to origin.
