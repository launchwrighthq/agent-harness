# Install, check, selftest and lint

The installer copies one pinned version of agent-harness into a repository, or into `~/.claude`.
A lockfile records a sha256 per installed file, so `ah check` finds every edit. A project changes
a fact in `.agents/harness.toml`, never in an installed file.

## Commands

| Command | Does |
|---|---|
| `ah install [--project] [--manifest <path> ...] [--from <package root>] [--replace]` | Installs the package and each extra manifest into the git repository of the current folder. `--project` is the default. `--replace` replaces a project copy (see below). |
| `ah install --global [--from <package root>]` | Installs the engine and the skills into `~/.claude`. No hook, no rule, no registry and no git hook. |
| `ah check [--global]` | Exits 1 and prints `<path> <kind>` for each part that differs. Exits 0 when every part matches. |
| `ah rules` | Prints each installed rule with its line count and character count, then the total. |
| `ah selftest` | Runs every fixture of every enabled hook of the installed manifests, for Claude Code and through the Codex adapter. |
| `ah lint <path ...>` | Prints `file:line:col rule message` for each banned word, banned filler or em dash in Markdown and in code comments. Exits 1 on a finding. |

Run the launcher with `python3`: `python3 agent-harness/bin/ah install` from a source tree, or
`python3 .claude/agent-harness/bin/ah check` in an installed project. `--from` installs from
another package root, for example a newer global copy at `~/.claude/agent-harness`.

The project root is the top folder of the repository of the current folder. `AH_ROOT` names
another root, for a project in a subfolder of a larger repository. `install`, `check` and
`selftest` then work on that folder.

## What a project install writes

| Part | Content |
|---|---|
| `.claude/agent-harness/` | The engine: `bin/`, `src/`, `hooks/`, `tools/`, `codex/` and `harness.toml` of the package. |
| `.claude/agent-harness/manifests/<name>/` | The copy of each extra manifest and of its hook and tool files. |
| `.claude/agent-harness/lock.json` | The package version, the installed manifests, a sha256 per file, the executable files, the skills and the skill links. |
| `.claude/skills/<id>/` | Every file of each enabled skill. |
| `.claude/rules/lw/<file>` | Each enabled rule. A rule with `paths` gets a `paths:` front matter block. |
| `.githooks/<name>` | The git hook dispatcher, one copy per client-side hook name (see "Git hooks" in `docs/hooks.md`). `.githooks/harness/` holds each enabled git hook and its files. `core.hooksPath` of the repository config becomes the absolute path of `.githooks/` in the main checkout when it is unset or the relative `.githooks`. `receive.denyCurrentBranch` becomes `updateInstead` when it is unset, so the `lw-land` push updates the base checkout. |
| `.claude/settings.json` | Only the harness entries of the `hooks` key. Every other byte and every project hook stay as the project wrote them. |
| `.codex/hooks.json` | Only the harness entries of the `hooks` key. Each one runs the hook through the Codex adapter. Every project hook stays. |
| `.agents/skills/<id>` | A link to `../../.claude/skills/<id>` for each skill, project skills too. A project skill is a folder in `.claude/skills/` with a `SKILL.md` that no manifest installs. The install never writes into it. |
| `AGENTS.md` | The block between `<!-- lw-harness:start -->` and `<!-- lw-harness:end -->`. It lists the rules and the skills for Codex. |
| `.gitignore` | The lines `/.claude/state/` and `/.claude/agent-harness/**/__pycache__/`, when no line ignores those files. |
| `.agents/harness.toml` | A commented starter file, only when none exists. The installer never rewrites it. |

A second install with no change writes nothing and prints `nothing changed`. An install removes
each file and link that the previous lockfile lists and the new one does not. It then removes
each folder that it leaves empty.

The installer reads every source file before it writes, so an install from the installed copy
(`--from .claude/agent-harness`) works.

A project copy is a file that the previous lockfile does not list and that the install would
replace or leave behind. It is a planned file with other bytes (`differs`), or a file in
`.claude/agent-harness/`, `.claude/rules/lw/` or a planned skill folder that the plan does not
hold (`extra`). An install over a project copy exits 1, names each file and writes nothing. Move
each file you still need, then install again. A git hook of the project in `.githooks/<name>` is a
project copy too, because the dispatcher takes that path. Move it to `.git/hooks/<name>`, which
the dispatcher runs, or declare it as a `[[githook]]` of a project manifest. `--replace` replaces the copy, removes its extra
files and names each one.

## Rule adapters

A project changes an installed rule with a rule adapter: `<adapterDir>/rules/<rule id>.md`, by
default `harness/adapters/rules/<rule id>.md`. Without front matter, the install appends the
adapter text to the rule under a `## Project notes` heading. With `replace: true` in its front
matter, the adapter text replaces the body of the rule. The `paths:` front matter of the rule
stays in both cases. Front matter takes no other key.

```markdown
---
replace: true
---
# Code limits

Keep each file under 300 lines.
```

The lockfile records a sha256 per adapter. `ah check` names an adapter that changed, came or went
since the install as `stale`, so an adapter edit needs `ah install`. Use `replace: true` to keep a
long rule short when the project has its own version of it, and `disable` to drop a rule.

`ah rules` prints the size of each installed rule, so a project sees what its rules cost:

```
.claude/rules/lw/anti-slop.md 82 lines 4790 chars
total 82 lines 4790 chars
```

## Hook commands

A Claude Code entry runs the installed script with the interpreter of its suffix:

```
python3 "$CLAUDE_PROJECT_DIR/.claude/agent-harness/hooks/workspace-guard.py" PreToolUse
bash "$CLAUDE_PROJECT_DIR/.claude/agent-harness/hooks/load-memory.sh" SessionStart
```

A `.py` script runs with `python3` and a `.sh` script with `bash`. A `.mjs` or `.js` script runs
with `node`, and only an extra manifest can hold one. The package itself needs no Node. The event
name is argument 1.

A Codex entry runs the adapter `.claude/agent-harness/codex/run-hook.py` with the script and the
event. The adapter turns the Codex payload into the Claude Code shape: `exec_command` becomes
`Bash`, and each `apply_patch` file becomes one `Edit` event. It takes the hook scripts only from
its own checkout, or from a worktree of the same repository under `.claude/worktrees/<name>/`. A
directory in the payload is data. A project in a subfolder of its checkout names its own adapter,
`<folder>/.claude/agent-harness/codex/run-hook.py`. The command names the folder inside double
quotes, so the install refuses a folder with a character outside `A-Z`, `a-z`, `0-9`, `.`, `_`,
`/` and `-`.

Nothing shows that Codex honors the PreToolUse `ask` decision. So the adapter turns an `ask` into
a `deny` that keeps the reason of the hook. The reason adds that the user runs the reveal
themselves, or confirms it in Claude Code.

Each hook has one entry per event in both registries. The package manifest comes first, then the
extra manifests by name, each in the order it declares. The order does not depend on the order of
`--manifest` options. Codex keeps its trust per entry position, so a reinstall with no manifest
change keeps every entry trusted. A disabled item has no entry in either registry.

## Project hooks

The installer owns only the entries whose command has one of the two forms above: a script under
`$CLAUDE_PROJECT_DIR/.claude/agent-harness/`, or the Codex adapter. Every other entry is a project
hook. An install keeps it, with its matcher and its other keys.

In each event, the harness entries come first and the project entries follow in their old order.
So a project hook never moves a harness entry, and Codex keeps the trust of each harness entry. A
project group that also holds a harness command keeps only its project commands. A second install
with no change writes nothing.

A project hook that must survive every sync can also come from a project manifest (see "Extra
manifests"). Then `ah selftest` runs its fixtures too.

## Extra manifests

`--manifest <path>` adds a manifest in the format below, for example the manifest of a framework
module. Its name is its folder for a file named `harness.toml`, else its file stem:
`modules/testing/harness.toml` is `testing`. Two extra manifests with one name fail.

`harness.manifests` in `.agents/harness.toml` names the extra manifests of the project, as paths
relative to the project root. `ah install` installs each of them and each `--manifest`, so the
hooks, rules and skills of the project survive every sync, also a sync that passes no
`--manifest`. A file named in both places installs once. A path that is no file fails and names
the key.

```toml
[harness]
manifests = ["tools/agents/harness.toml"]
```

The check reads the manifests that the lockfile names. After a change of `harness.manifests`, run
`ah install`.

The paths of an extra manifest are relative to its own folder. Its hook and tool files go to
`.claude/agent-harness/manifests/<name>/`, so the project does not depend on the source folder,
for example a `node_modules` path. Its rules and skills go to the same folders as the package's.

## The check

`ah check` reads the lockfile and the installed manifests. It needs no source package. A kind is
one of these:

| Kind | Means |
|---|---|
| `missing` | The lockfile lists the file, or a registry or link should exist, and it does not. |
| `edited` | The file differs from its lockfile hash. |
| `mode` | The file is no longer executable. |
| `extra` | A file in `.claude/agent-harness/`, `.claude/rules/lw/` or an installed skill folder that the lockfile does not list, or a stale skill link. |
| `differs` | `.claude/settings.json`, `.codex/hooks.json`, `AGENTS.md`, `.gitignore` or a skill link differs from what the installed manifests and the config give. |
| `stale` | A rule adapter changed, came or went after the install. The installed rule still holds the old text until the next install. |

The check reads `.claude/settings.json` and `.codex/hooks.json` as JSON, so the layout of a
hand-written hook is no difference. It prints one line for each project hook in the two files,
an entry that no installed manifest declares:

```
.claude/settings.json PreToolUse Bash: a project hook that no manifest declares: bash scripts/x.sh
```

These lines are information. They do not change the exit code.

A changed `disable` or `enable` in `.agents/harness.toml` makes the registries differ until the
next install. The check takes the project skills from the links of the lockfile, so a project
skill added after the install changes nothing. The next install links it and lists it in
`AGENTS.md`. Python caches and names that start with a dot are never `extra`.

## The selftest

`ah selftest` first runs the check and refuses to run over an install that differs. Then each
fixture runs as Claude Code runs the hook, and again through the Codex adapter when the hook has
`codex = true`. The environment holds `CLAUDE_PROJECT_DIR` (the project root) and
`CLAUDE_CODE_SESSION_ID`.

| `expect` | Passes when |
|---|---|
| `deny` | The hook prints `hookSpecificOutput.permissionDecision = "deny"`, or exits 2. `contains` must be in the reason. |
| `ask` | The hook prints `hookSpecificOutput.permissionDecision = "ask"`. `contains` must be in the reason. An exit 2 is a deny. Through the Codex adapter, the fixture passes on a `deny` instead, because the adapter turns an `ask` into a `deny`. |
| `block` | A PostToolUse hook prints `{"decision": "block", "reason": ...}`. |
| `context` | The hook prints `hookSpecificOutput.additionalContext` under the fixture event. A `{"prompt": ...}` answer fails, because the client ignores it. |
| `silent` | The hook exits 0 and prints nothing. |

A prompt fixture runs on UserPromptSubmit. A tool fixture runs on PreToolUse, or on PostToolUse
for a hook with no PreToolUse event. A `decoy` fixture names a separate git checkout as the
payload directory. That checkout holds its own copy of the hook, and the run fails when that copy
answers.

The portability checks follow. Each result line names `portability`.

- Each installed skill resolves through `.agents/skills/<id>/SKILL.md` to its file in
  `.claude/skills/<id>/`, and that file has a `description:` line.
- A scratch repository gets the installed `.claude/agent-harness/` in a commit, and a worktree at
  `.claude/worktrees/own` with a subfolder `sub`. From that subfolder, each enabled guard of
  `PORTABILITY_CASES` runs as Claude Code runs it and through the Codex adapter.
  `workspace-guard` denies `git -C <main checkout> reset --hard HEAD` and `git stash` and stays
  silent for `git status --short`. `worktree-path-guard` denies `git worktree add ../outside`.
  `bash-read-guard` denies a read of a local env file.

## The global install

`ah install --global` writes `~/.claude/agent-harness/` (`bin/`, `src/`, `tools/`,
`harness.toml` and `lock.json`) and `~/.claude/skills/lw-*`. `HOME` decides the folder. A global
`ah` inside a repository with a project install hands over to the project engine. `ah install`
never hands over: it installs the package of the engine that you start.

Claude Code prefers a personal skill to a project skill with the same name, so a global skill can
hide a newer project skill. Keep the global install no older than the newest project install.

## Manifest format

```toml
[[hook]]
id = "bash-read-guard"           # lower case letters, digits and -
summary = "Refuse a shell reader of a credential file"
events = ["PreToolUse"]          # SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop,
                                 # SubagentStop, PreCompact, Notification, SessionEnd
matcher = "Bash"                 # tool events only; Codex adds apply_patch to an Edit or Write matcher
script = "hooks/bash-read-guard.py"
files = ["hooks/lib.py"]         # more files the hook loads from beside itself
timeout = 5                      # seconds, 1 to 600
codex = true                     # false: Claude Code only
default = "on"                   # "off", or a boolean

[[hook.fixture]]
tool = "Bash"                    # Bash, Read, Edit or Write; or prompt = "..." for UserPromptSubmit
command = "cat .env.local"       # a Bash fixture; a file tool fixture has file = "..."
expect = "deny"                  # deny, ask, block, context or silent
contains = "credential file"
decoy = true                     # optional
# content = "..."                # the selftest writes `file` with this text for the run

[[rule]]
id = "spec-format"
summary = "Spec format and rendering"
file = "rules/spec-format.md"
paths = ["docs/specs/**"]        # empty means every file

[[skill]]
id = "lw-spec-write"             # a skill of the package needs the lw- prefix
dir = "skills/lw-spec-write"

[[githook]]
id = "commit-msg-strip"
summary = "Take agent attribution trailers out of a commit message"
name = "commit-msg"
script = "githooks/commit-msg"
files = ["githooks/strip_commit_trailers.py"]

[[tool]]
id = "spec-template"
summary = "The template of the spec page"
files = ["tools/spec-template.html"]
```

A key that the format does not name fails, and the message names the table and the key.

## Config keys

The installer reads these keys of `[harness]` in `.agents/harness.toml`:

| Key | Default | Meaning |
|---|---|---|
| `disable` | `[]` | Ids of hooks, rules, skills, git hooks and tools to leave out. A project skill can be named too. An unknown id fails. |
| `enable` | `[]` | Ids of items that are off by default. |
| `manifests` | `[]` | Extra manifests of the project, relative to the root. `ah install` adds each `--manifest` to them. |
| `adapterDir` | `harness/adapters` | The folder of free-text notes per skill that the `AGENTS.md` block names, and of the rule adapters in `rules/`. |
| `lint.words`, `lint.allow` | `[]` | More banned words for `ah lint`, and banned words that this project allows. |

A line that holds `lw-lint-ok` is not linted, for a line that must quote a banned form.
