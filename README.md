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
| `[branches]` | The remote base, the protected refs, the worktree folder, the rebuildable folder names and the age gates of `ah branch-audit`. | [docs/work.md](docs/work.md) |

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
| `ah check [--global]` | Compares the installed files and registries with the lockfile and the manifests. Exits 1 and names each file that differs. | [docs/install.md](docs/install.md) |
| `ah rules` | Prints each installed rule and each project rule of `.claude/rules/` with its line count and character count, a subtotal per group, then the total. A rule adapter in `harness/adapters/rules/<rule id>.md` adds project notes to a rule or replaces its body. | [docs/install.md](docs/install.md) |
| `ah selftest` | Runs the fixture of every installed hook for Claude Code and Codex, then the portability checks. | [docs/install.md](docs/install.md) |
| `ah lint <path ...>` | Finds banned words, banned fillers and em dashes in Markdown and code comments. | [docs/install.md](docs/install.md) |
| `ah comments [path ...]` | Checks the comment budget: the longest comment block and the comment share of each source file. Exits 1 on a finding. | [docs/hooks.md](docs/hooks.md) |
| `ah config get <key>` | Prints one config value. | this file |
| `ah spec render\|audit\|codex\|goal\|restate\|repoint` | Renders a spec page and runs the audit store, the Codex review, the goal and the reconcile steps. | [docs/spec.md](docs/spec.md) |
| `ah env <subcommand>` | Reads and changes `.env*` files. It shows no value unless `get --reveal` is passed. | [docs/env.md](docs/env.md) |
| `ah deploy-status` | Prints the verdict of the last deploy, its phases and what each target serves. It only reads. | [docs/deploy.md](docs/deploy.md) |
| `ah work-status [--repo]` | Reports the work in progress from transcripts, git and saved audits. `--repo` reads git and the store alone. | [docs/work.md](docs/work.md) |
| `ah branch-audit` | Judges what each worktree, branch and stray folder holds that the base lacks, and prints a prune plan. `--apply --only <ids>` runs the rows the user names. | [docs/work.md](docs/work.md) |
| `ah worktree add <name> [--from <ref>]` and `ah worktree remove <name>` | Adds `.claude/worktrees/<name>` of the main checkout on the new branch `<name>` (from `harness.baseBranch`, or `<ref>`) and prints its path. Removes a clean worktree of this repository and keeps its branch. Neither takes a path. | [docs/hooks.md](docs/hooks.md) |
| `ah land-results files\|unaccounted <log>` | Prints the failed files or the unaccounted steps of the `verify: failures <json>` record in a gate log, for `lw-land`. | [docs/hooks.md](docs/hooks.md) |

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
| G3 | The read guard refuses a file listing whose names reach a reader: through a substitution, a pipe into `xargs`, `parallel` or a `while read` loop, or a `find -exec` action. Tests pin the harmless patterns from real sessions that the guard passes: `jq` paths, parameter expansions, `--include` globs and bracket classes. | [docs/hooks.md](docs/hooks.md) |
| G4 | 0.1.2 keeps the project hook entries of `.claude/settings.json` and `.codex/hooks.json` after the harness entries, and runs git hooks through a dispatcher. 0.2.0 adds no second mechanism. The install builds both hook files before its first write, and a project git hook comes from a `[[githook]]` of a manifest that `harness.manifests` names. | [docs/install.md](docs/install.md) |
| G5 | The comment budget keeps the caps of the old path for a renamed file, measures Terraform files, and takes per-pattern caps in `comments.overrides` and a line length cap in `comments.maxLineLength`. | [docs/hooks.md](docs/hooks.md) |
| G6 | The new rule `core-principles` holds truth over optimism and asking before an uncertain change. `code-limits` adds error handling. | [docs/hooks.md](docs/hooks.md) |
