# agent-harness

agent-harness installs agent skills, hooks, rules and git hooks into a git repository. Claude Code
and Codex read them. Every script runs on Python 3.11 or newer with the standard library only. No
script needs Node.

## Install

Two install modes exist. A per-project install is the default. A global install is optional.

| Command | Writes |
|---|---|
| `python3 <package>/bin/ah install --project [--manifest <harness.toml> ...]` | The engine in `.claude/agent-harness/`, the skills in `.claude/skills/lw-*`, the rules in `.claude/rules/lw/`, the harness entries of the `hooks` key of `.claude/settings.json` and `.codex/hooks.json`, the links in `.agents/skills/`, the marked block in `AGENTS.md`, `.githooks/` and the lockfile `.claude/agent-harness/lock.json`. |
| `python3 <package>/bin/ah install --global` | `~/.claude/agent-harness/` and `~/.claude/skills/lw-*`. No hook and no rule. |

`<package>` is this folder. `--manifest` adds an extra manifest in the format of `harness.toml`,
for example the hooks and skills of one project. `harness.manifests` in the config names the
extra manifests that every install adds. A second install with no change changes
nothing, so Codex keeps its hook trust. A project hook in either hook file stays after the harness
entries. An install over an old project copy of a skill or a rule names each file of the copy and
writes nothing. Move the files you still need, or pass `--replace` (docs/install.md).

A global `ah` that runs inside a repository with a project install hands over to the project
engine. `ah install` never hands over: it installs the package of the engine that you start.
Claude Code prefers a personal skill to a project skill with the same name. Keep the global
install no older than the newest project install.

The project root is the top folder of the git checkout. For a project in a subfolder of a larger
checkout, set `AH_ROOT` to the project folder.

Details: [docs/install.md](docs/install.md).

## Config

Every project fact sits in `.agents/harness.toml`. Every key has a default, so a repository with
no config file works. A project install writes a commented starter file when none exists. It
never rewrites an existing file.

| Section | Holds | Details |
|---|---|---|
| `[harness]` | The base branch, the gate, test, land and env commands, guarded files, workspace rules, prompt directives, resource caps, extra banned words, the comment budget. | [docs/hooks.md](docs/hooks.md) |
| `[specs]` | The spec folders, the archive folder, the page folder and the audit store. | [docs/spec.md](docs/spec.md) |
| `[env]` | The master env file, the other env files, the declarations, the layout check and the secret store. | [docs/env.md](docs/env.md) |
| `[deploy]` | The CI workflows and jobs, the environments, the phases and the serving targets. | [docs/deploy.md](docs/deploy.md) |
| `[branches]` | The remote base, the protected refs, the worktree folder, the rebuildable folder names and the age gates of `ah branch-audit`. `[branches.autoApply]` turns the worktree sweep on. | [docs/work.md](docs/work.md) |

`ah config get <key>` prints one value. A string prints as it is. Any other value prints as JSON.
Skill text names this command where it needs a project fact.

```bash
python3 .claude/agent-harness/bin/ah config get harness.baseBranch
python3 .claude/agent-harness/bin/ah config get specs.dirs
```

`harness.baseBranch` defaults to the branch of `refs/remotes/origin/HEAD`, else `main`. An empty
`gateCommand` or `testCommand` means that the repository has none.

## Commands

Run each command as `python3 .claude/agent-harness/bin/ah <command>` in a project install, or as
`python3 ~/.claude/agent-harness/bin/ah <command>` with only a global install.

| Command | Does | Details |
|---|---|---|
| `ah install` | Installs the harness in a project or globally. | [docs/install.md](docs/install.md) |
| `ah check [--global]` | Compares the installed files and registries with the lockfile and the manifests. Exits 1 and names each file that differs. Prints the size of the always-loaded rules, and exits 1 when it is over `harness.rules.maxChars`. | [docs/install.md](docs/install.md) |
| `ah rules` | Prints each installed rule and each project rule of `.claude/rules/` with its line count and character count, a subtotal per group, the total, then the size of the rules that load in every session. `[harness.rules.paths]` scopes a rule to globs. A rule adapter in `.claude/adapters/rules/<rule id>.md` adds project notes to a rule or replaces its body. | [docs/install.md](docs/install.md) |
| `ah selftest` | Runs the fixture of every installed hook for Claude Code and Codex, then the portability checks. | [docs/install.md](docs/install.md) |
| `ah lint <path ...>` | Finds banned words, banned fillers and em dashes in Markdown, code comments and Python docstrings. `--changed` adds the spec references in comments, on the lines new since the base branch. `--refs <path>` lists the spec references of every line. | [docs/install.md](docs/install.md) |
| `ah comments [path ...]` | Checks the comment budget: the longest comment block and the comment share of each source file. Exits 1 on a finding. | [docs/hooks.md](docs/hooks.md) |
| `ah config get <key>` | Prints one config value. | this file |
| `ah spec render\|audit\|codex\|goal\|restate\|repoint` | Renders a spec page and runs the audit store, the Codex review, the goal and the reconcile steps. | [docs/spec.md](docs/spec.md) |
| `ah env <subcommand>` | Reads and changes `.env*` files. It shows no value unless `get --reveal` is passed. | [docs/env.md](docs/env.md) |
| `ah deploy-status` | Prints the verdict of the last deploy, its phases and what each target serves. It only reads. | [docs/deploy.md](docs/deploy.md) |
| `ah work-status [--repo]` | Reports the work in progress from transcripts, git and saved audits. `--repo` reads git and the store alone. | [docs/work.md](docs/work.md) |
| `ah branch-audit` | Judges what each worktree, branch and stray folder holds that the base lacks, and prints a prune plan. `--apply --only <ids>` runs the rows the user names. | [docs/work.md](docs/work.md) |
| `ah worktree add <name> [--from <ref>]`, `ah worktree remove <name>` and `ah worktree claim <name>` | Adds `.claude/worktrees/<name>` of the main checkout on the new branch `<name>` (from `harness.baseBranch`, or `<ref>`), records the agent session that made it, and prints its path. Removes a clean worktree of this repository and keeps its branch. Claims a worktree that has no owner for this session. None takes a path. | [docs/hooks.md](docs/hooks.md) |
| `ah land-results files\|unaccounted\|steps <log>` | Prints the failed files, the unaccounted steps or one line per failed step of the `verify: failures <json>` record in a gate log, for `lw-land`. `steps` exits 3 for a log with no valid record. | [docs/hooks.md](docs/hooks.md) |

The hooks, the rules and the git hooks are in [docs/hooks.md](docs/hooks.md).

## Layout

| Path | Holds |
|---|---|
| `bin/ah` | The launcher. It puts `src/` on the import path. |
| `src/agent_harness/` | The engine: `cli.py`, `config.py` and one package per area. |
| `harness.toml` | The manifest of the hooks, rules, skills, git hooks and tools. |
| `hooks/`, `skills/`, `rules/`, `githooks/`, `codex/` | The installed content and the Codex hook adapter. |
| `docs/` | One document per area. |
| `tests/unit/`, `tests/integration/` | The tests. |

## Changes in 0.4.2

This release has one change, and no behavior differs from 0.4.1.

| Change | Keys and commands | Details |
|---|---|---|
| A shorter module docstring | No key. The module docstring of `hooks/local-run-guard.py` has 12 lines at most, so `ah comments` passes on the package itself. | [docs/hooks.md](docs/hooks.md) |

## Changes in 0.4.1

This release has thirteen changes. Each default keeps the behavior of 0.4.0.

| Change | Keys and commands | Details |
|---|---|---|
| A value on standard input | No key. The env guards clear the value of `ah env set <KEY> <file> --stdin <<'EOF'`, a quoted heredoc on the env command. The `secrets` rule and the `lw-env` skill name this form. | [docs/hooks.md](docs/hooks.md) |
| A heredoc body that is text | No key. The glob scan of `bash-read-guard` skips the quoted body of `git commit -F -` anywhere in a command, and of a program that is no reader, shell or runner. The literal name scan still reads the body. | [docs/hooks.md](docs/hooks.md) |
| A new worktree of a listed repository | No key. `workspace-guard` reads the missing folder `<main checkout>/.claude/worktrees/<name>` of a repository of `harness.workspace.repos` as that repository, so one command can add a worktree and run git in it. | [docs/hooks.md](docs/hooks.md) |
| The skills of the `AGENTS.md` block | `harness.agentsSkills = "harness"` lists only the skills of the manifests. The default `all` also lists the project skills. | [docs/install.md](docs/install.md) |
| A removed hook script | No key. `ah install` prints one warning line when it removes a hook script that a running agent session still calls. | [docs/install.md](docs/install.md) |
| The texts of a spec page | `specs.wordmark` and `specs.footer` replace the mark above the contents list and the footer note. | [docs/spec.md](docs/spec.md) |
| More board pieces | No key. `boards.py` of `lw-spec-mockup` has `tabbar`, `link` and `tokens(sets=)`, and `embed_font` reads `.woff`, `.ttf` and `.otf` files too. | [docs/spec.md](docs/spec.md) |
| A project board library | `specs.mockupLibrary` names a project folder that a board script imports from. | [docs/spec.md](docs/spec.md) |
| The worktree sweep | `[branches.autoApply] staleHours` above 0 turns it on, and `everyMinutes` spaces the sweeps. The new `worktree-sweep` hook starts it. It removes a merged and clean worktree that no live session owns, and the branch of that worktree. | [docs/work.md](docs/work.md) |
| A landing that removes its worktree | `harness.land.removeWorktree = true` removes the clean branch worktree and the landed branch as the last step of a landing. | [docs/hooks.md](docs/hooks.md) |
| The certificate bundle of a package | No key. `ah branch-audit` reads an ignored `cacert.pem` as `cache`. Every other `.pem` file stays a `credential`. | [docs/work.md](docs/work.md) |
| Env keys from a command | `env.declarationsCommand` is a shell command that prints one key name per line. `ah env check` and `ah env where` take each name as a declared key. | [docs/env.md](docs/env.md) |
| The comment budget of config files | A pattern of `comments.overrides` or `comments.include` puts a YAML file, a Dockerfile or an env template in the budget. `comments.headBlock` caps the block that opens a file. | [docs/hooks.md](docs/hooks.md) |

Six behaviors differ from 0.4.0:

- The two env guards and `bash-read-guard` pass the three command forms of the first two rows,
  and `workspace-guard` passes git in the new worktree folder of the third row. Each one was
  refused before.
- An upgrade adds the `worktree-sweep` hook to `.claude/settings.json` and `.codex/hooks.json`.
  The hook does nothing while `branches.autoApply.staleHours` is 0.
- `ah install` can print the warning line for a removed hook script.
- `ah branch-audit` gives a worktree whose only ignored credential file is a `cacert.pem` the
  verdict of its other files. Before, that file was a `credential` row.
- `tokens()` of `boards.py` raises `ValueError` for a `theme` other than `light`, `dark` or a
  name of `sets`. Before, it gave the light values. `embed_font` raises for a file that is no
  `.woff2`, `.woff`, `.ttf` or `.otf`.
- The `lw-env` skill tells an agent to give a value in the quoted heredoc, not with `--value`.

## Changes in 0.4.0

Code comments stop pointing at specs. The spec points at the code.

| Change | Keys | Details |
|---|---|---|
| `ah lint --changed` reports each spec reference in a comment or a Python docstring on the lines new since the base branch: row and decision IDs, spec sections, dates and "ported from". `ah lint --refs <path>` sweeps every line. A comment `lw-gap <ID>: <removal condition>` passes. | `lint.commentPatterns`, `lint.commentExclude` | [docs/install.md](docs/install.md) |
| `ah lint` reads Python docstrings, through `ast`, and Python comments through `tokenize`. | none | [docs/install.md](docs/install.md) |
| The comment budget caps a Python module docstring at `maxBlock` (12) lines, in a new file and an old one. | `comments.maxBlock` | [docs/hooks.md](docs/hooks.md) |
| The rule `code-limits` no longer allows a spec row in a comment. The skills `lw-spec-write` and `lw-goal-spec` never ask for an ID in a code comment. | none | [rules/code-limits.md](rules/code-limits.md) |

Two defaults differ from 0.3.3. Set the keys to keep the old behavior:

- `specs.dirs` is `["docs/specs"]`. A project with plans in `docs/superpowers/plans` adds that folder.
- `ah comments` and the `comment-budget` hook report a module docstring over 12 lines in an old file.
  Shorten it, or raise `comments.maxBlock` for that path in `comments.overrides`.

## Changes in 0.3.3

Two env lists move into the config. The examples and fixtures use an invented app.

| Change | Keys | Details |
|---|---|---|
| The files that always take their own `.example` as master | `env.paired`, default `[".env.ci"]` | [docs/env.md](docs/env.md) |
| The values that `ah env push` reads as no value for a declared secret | `env.placeholders`, default `["changeme", "xxx"]` | [docs/env.md](docs/env.md) |

Two defaults differ from 0.3.2. Set the keys to keep the old behavior:

- `.env.seed` and `.env.exp` no longer pair by default. Add them to `env.paired`.
- `tobemodified` is no longer a placeholder by default. Add it to `env.placeholders`.

## Changes in 0.3.2

`local-run-guard` refuses only a runner whose folder is in its own project. No key changes.

| Command, from a session in the project | 0.3.1 | 0.3.2 |
|---|---|---|
| `cd ../other-repo && pytest` | refused | passes |
| `npm --prefix ../other-repo test` | refused | passes |
| `uv --directory . run pytest` | passes | refused |
| `npx --prefix . vitest` | passes | refused |

A `cd` or `pushd` moves the folder for the commands after it. So does a folder option:
`--prefix`, `--directory`, `--project`, `--dir`, `--cwd` or `-C`. A linked worktree counts as
its main checkout. A folder that the hook cannot know, such as `cd "$DIR"`, counts as the
project, so the hook still refuses there. Details: [docs/hooks.md](docs/hooks.md).

## Changes in 0.3.1

This release has five changes. Each default keeps the behavior of 0.3.0.

| Change | Keys and commands | Details |
|---|---|---|
| The comment budget at the end of a turn | No key. With the `comment-budget` hook on, the Stop event measures each file that changed since the base, also a file that a shell command wrote. A finding holds the turn once. | [docs/hooks.md](docs/hooks.md) |
| Pictures that a board links | No key. `ah spec render` writes each file that a board links into the board of the page as a `data:` URI. A link that it cannot write prints a warning, and `ah spec render --strict` refuses it. | [docs/spec.md](docs/spec.md) |
| The commit of a spec | `specs.commit = "auto"` lets the `lw-spec-write` skill commit a spec with no question. The default `ask` asks first. | [docs/spec.md](docs/spec.md) |
| An install from the installed copy | `python3 .claude/agent-harness/bin/ah install --project` installs again with no source package. The project copy now also holds `rules/`, `skills/` and `githooks/`. | [docs/install.md](docs/install.md) |
| A green gate line with a part that changes | `harness.land.greenPattern` is an extended regular expression that one line of the gate log must match. | [docs/hooks.md](docs/hooks.md) |

Two behaviors differ from 0.3.0:

- A project with the `comment-budget` hook on can get one hold at the end of a turn, for the
  findings of the files that changed since the base.
- `.claude/agent-harness/` and its `lock.json` hold three more folders: `rules/`, `skills/` and
  `githooks/`. An upgrade writes them once.

## Changes in 0.3.0

This release closes six gaps. Each one has a config key, and
each default keeps the behavior of 0.2.11.

| Gap | Keys and commands | Details |
|---|---|---|
| The cost of the land gate | `[harness.land.gates]` names more gate modes, `harness.land.defaultGate` picks the mode of a landing, and `LAND_GATE=<mode>` picks it for one landing. | [docs/hooks.md](docs/hooks.md) |
| The report of a red gate | The land command prints the failed steps of the `verify: failures` record, then the output of `harness.land.explainCommand`. | [docs/hooks.md](docs/hooks.md) |
| A spec image with no file | `specs.missingAssets = "placeholder"` renders a box and a warning. `ah spec render --strict` refuses the page. | [docs/spec.md](docs/spec.md) |
| An env file with no secret | `harness.plainEnvFiles` lists exact file names that an agent may read. | [docs/hooks.md](docs/hooks.md) |
| Global git hooks | `harness.githooks.global = "skip"` stops the dispatcher from running them. The selftest case `global-hooks` checks both values. | [docs/hooks.md](docs/hooks.md) |
| The size of the rules | `ah rules`, `ah check` and `ah install` print the always-loaded size. `harness.rules.maxChars` caps it, and `[harness.rules.paths]` scopes a rule. | [docs/install.md](docs/install.md) |

An entry of `harness.plainEnvFiles` is one exact file name. `ah install --project` and
`ah check` fail for an entry with `*` or `?`, and for an entry that a pattern of `guardedFiles`
or `secretFiles` matches. `ah selftest` skips a deny fixture that names a plain env file of the
project, and prints one `SKIP` line for it.

A manifest rule has the new optional key `agentsCovers`: the text of the Covers column of the
`AGENTS.md` block, in place of the `paths` of the rule.

After a landing, the land command prints each warning of the page render as
`land: page: warning: ...`, so a placeholder image shows in the landing.

Three behaviors differ from 0.2.11:

- A landing with `LAND_GATE=0` leaves no green gate mark, also when the base moves during the
  landing.
- The `codex` rule has `paths`, so Claude Code loads it only for `AGENTS.md` and `.codex/**`.
  The `AGENTS.md` block still says `every file` for it. An upgrade writes
  `.claude/rules/lw/codex.md` and the `AGENTS.md` block again once.
- The line for a tree with a green gate names the mode:
  `land: gate: already green on this tree (<tree id>) in mode <mode>, not running it again`.

## Changes in 0.2.11

An entry of `harness.workspace.repos` is relative to the main checkout of the project, such as
`../client` or `../*`. Every worktree reads it from the main checkout, so every worktree gets the
same answer. The new default is `../*`: every sibling repository of the project, at any depth. A
repository inside another checkout below the fixed part still does not match. An absolute or
`~/` entry still works. `ah install` and `ah check` print a warning line for it, because it names
a folder of one machine. Details: [docs/hooks.md](docs/hooks.md) and
[docs/install.md](docs/install.md).

The `push-to-checkout` git hook lands on a base checkout whose index already holds the pushed
commit. Before, it read that staged content as the work of a peer and refused every landing. Now
a file is the work of a peer only when it differs from HEAD and from the pushed commit. An index
entry staged to the pushed commit over a file that still holds HEAD goes back to HEAD first, so
the landing writes the file too. Details: [docs/hooks.md](docs/hooks.md).

## Changes in 0.2.10

`workspace-guard` lets git run in every repository that a pattern of `harness.workspace.repos`
matches. 0.2.11 changes the default of the key. An entry with `*`, `?` or `[` matches
the main checkout of a repository, but never a vendored clone or a submodule inside another
checkout. In a listed repository git may change the main checkout too. A `cd` or `-C` path may
start with `~/`.

`workspace-guard` also refuses git that a comment, a `case` pattern or a quoted heredoc delimiter
hides, a fetch or pull into a local ref, a push or remote that names this repository,
`git reflog expire` and `delete`, and `ext::` URLs. `bash-read-guard` refuses a file name built
from a variable in a folder that holds a guarded file.

The project files of the harness stay in `.claude/`: the default of `harness.adapterDir` is
`.claude/adapters`, and `harness.manifests` defaults to `.claude/githooks/harness.toml` when that
file exists. Move a `harness/` folder there and drop the two keys. Details:
[docs/hooks.md](docs/hooks.md) and [docs/install.md](docs/install.md).

## Changes in 0.2.9

The env engine reads three more key types: `float`, `decimal` and `path`. Before, `ah env get`
refused an `env.toml` that declared one of them. Details:
[docs/env.md](docs/env.md).

The env guard judges the env command inside a plain wrapper, such as
`timeout 300 npx lw env check`, like the same command alone. Before, every wrapped env command
asked. Details: [docs/hooks.md](docs/hooks.md).

## Changes in 0.2.8

`ah worktree add` records the agent session that made a worktree, and `ah worktree claim <name>`
records it for a worktree that has no owner. `workspace-guard` lets that session change its own
worktree from any checkout. A peer worktree and the main checkout of the project stay refused. In
a repository of `harness.workspace.repos` the main checkout is open too. A hook of a
worktree install runs the main checkout's copy when
that install is newer, so an old branch never keeps old guards. `ah env` may write into the
folders of `[env] writeRoots`, by default the agent scratch folders. Details:
[docs/hooks.md](docs/hooks.md) and [docs/env.md](docs/env.md).

## Changes in 0.2.7

`workspace-guard` takes the main checkout of the repository as the project, so a session that
starts in a worktree can run git in every sibling worktree, where its agents work.
`harness.workspace.repos` lists other repositories where git may run, such as a client library
beside the project. Details: [docs/hooks.md](docs/hooks.md).

## Changes in 0.2.6

The env guards ask only when a value can reach the agent context. They read a command line step
by step, so the words of the env command in a quote, a commit message or a heredoc body are data.
A quiet env command passes in any line. `[env] publicKeys` lists config keys that `get --reveal`
shows without an ask, and the engine masks a public value that has a credential shape. `ah env
run` redacts the file values from the output of the command, so a run of an interpreter on a file
that is not held asks no more. `[env] runCommands` is no longer read. Details:
[docs/hooks.md](docs/hooks.md) and [docs/env.md](docs/env.md).

## Changes in 0.2.5

`ah work-status` reads the transcript records of the current Claude Code. A finish notification
in a queued attachment or a queue record now ends its agent, an agent whose own file ends its turn
counts as finished, and a peer message filed as a meta user record reaches the Peers section. A
peer message no longer takes the reply from the request before it, an interrupted request takes
the answer of the next message, and the body of an Edit or Write call names no spec. Details:
[docs/work.md](docs/work.md).

## Changes in 0.2.4

`[specs] keep` lists settled documents that stay in their active folder as a living reference.
`ah spec check` does not ask to archive them, and `lw-spec-reconcile` never moves them. The
spec-audit skill reports unknown amendments when no session history exists, and gives a missed
requirement the row ID M1, M2. The work-status skill lists a request that got an answer but no
action. The core principles add two rules: fix the failures your change causes, and start a new
subsystem or a contract change with a spec.

## Changes in 0.2.3

A bare `/lw-goal-spec` builds in a git worktree, and in Claude Code with an agent workflow. The
user does not need to ask for either. The `prompt-directives` hook takes a `/lw-goal-spec` or a
`/goal` after a quote mark or a backtick as a quote, and asks for nothing.

## Changes in 0.2.2

A skill folder in `.claude/skills/` that git ignores is no project skill. `ah install` gives it no
link in `.agents/skills/` and no line in `AGENTS.md`, so a local skill, such as a symlink that a
session hook writes, never reaches a tracked file. Details: [docs/install.md](docs/install.md).

## Changes in 0.2.1

0.2.1 merges the changes of 0.2.0 with the changes of 0.1.4 and 0.1.5. It adds no behavior of
its own.

| Release | Change | Details |
|---|---|---|
| 0.1.4 | A fenced code block or a blockquote indented under a list item renders inside that item. The long module docstrings of the hooks and the engine moved to the docs. | [docs/spec.md](docs/spec.md), [docs/hooks.md](docs/hooks.md) |
| 0.1.5 | Each deploy target has `timeout`, the seconds that one read may take. The default is 20. | [docs/deploy.md](docs/deploy.md) |
| 0.2.0 | The six changes below. | |

## Changes in 0.2.0

| Gap | Change | Details |
|---|---|---|
| G1 | A spec folder of kind `qa` holds QA reports with the statuses `untested` and `tested`. A QA report renders with its own badge and never moves to an archive. | [docs/spec.md](docs/spec.md) |
| G2 | `ah deploy-status` reads a workstation deploy with Kamal nodes from the existing `[deploy]` keys. The deploy document has a worked example. | [docs/deploy.md](docs/deploy.md) |
| G3 | The read guard refuses a file listing whose names reach a reader: through a substitution, a pipe into `xargs`, `parallel` or a `while read` loop, or a `find -exec` action. Tests pin the harmless patterns that the guard passes: `jq` paths, parameter expansions, `--include` globs and bracket classes. | [docs/hooks.md](docs/hooks.md) |
| G4 | 0.1.2 keeps the project hook entries of `.claude/settings.json` and `.codex/hooks.json` after the harness entries, and runs git hooks through a dispatcher. 0.2.0 adds no second mechanism. The install builds both hook files before its first write, and a project git hook comes from a `[[githook]]` of a manifest that `harness.manifests` names. | [docs/install.md](docs/install.md) |
| G5 | The comment budget keeps the caps of the old path for a renamed file, measures Terraform files, and takes per-pattern caps in `comments.overrides` and a line length cap in `comments.maxLineLength`. | [docs/hooks.md](docs/hooks.md) |
| G6 | The new rule `core-principles` holds truth over optimism and asking before an uncertain change. `code-limits` adds error handling. | [docs/hooks.md](docs/hooks.md) |
