# Install, check, selftest and lint

The installer copies one pinned version of agent-harness into a repository, or into `~/.claude`.
A lockfile records a sha256 per installed file, so `ah check` finds every edit. A project changes
a fact in `.agents/harness.toml`, never in an installed file.

## Commands

| Command | Does |
|---|---|
| `ah install [--project] [--manifest <path> ...] [--from <package root>] [--replace]` | Installs the package and each extra manifest into the git repository of the current folder. `--project` is the default. `--replace` replaces a project copy (see below). |
| `ah install --global [--from <package root>]` | Installs the engine and the skills into `~/.claude`. No hook, no rule, no registry and no git hook. |
| `ah check [--global]` | Exits 1 and prints `<path> <kind>` for each part that differs. Also exits 1 when the always-loaded rules are over `harness.rules.maxChars`. Exits 0 when every part matches. |
| `ah rules` | Prints each installed rule and each project rule of `.claude/rules/` with its line count and character count, a subtotal per group, the total, then the size of the always-loaded rules. |
| `ah selftest` | Runs every fixture of every enabled hook of the installed manifests, for Claude Code and through the Codex adapter. |
| `ah lint <path ...>` | Prints `file:line:col rule message` for each banned word, banned filler or em dash in Markdown and in code comments. Exits 1 on a finding. |

Run the launcher with `python3`: `python3 agent-harness/bin/ah install` from a source tree, or
`python3 .claude/agent-harness/bin/ah check` in an installed project. `--from` installs from
another package root, for example the project copy of another checkout.

The project root is the top folder of the repository of the current folder. `AH_ROOT` names
another root, for a project in a subfolder of a larger repository. `install`, `check` and
`selftest` then work on that folder.

## What a project install writes

| Part | Content |
|---|---|
| `.claude/agent-harness/` | The engine: `bin/`, `src/`, `hooks/`, `tools/`, `codex/` and `harness.toml` of the package. Also the source of every rule, skill and git hook of the package: `rules/`, `skills/` and `githooks/`. No client loads a file from these three folders. |
| `.claude/agent-harness/manifests/<name>/` | The copy of each extra manifest and of its hook and tool files. |
| `.claude/agent-harness/lock.json` | The package version, the installed manifests, a sha256 per file, the executable files, the skills and the skill links. It also holds a sha256 per rule adapter, and the table `scoped` when the config gives a rule its paths or its generated line. |
| `.claude/skills/<id>/` | Every file of each enabled skill. |
| `.claude/rules/lw/<file>` | Each enabled rule. A rule with `paths` gets a `paths:` front matter block. `[harness.rules.paths]` replaces the paths of a rule. |
| `.githooks/<name>` | The git hook dispatcher, one copy per client-side hook name (see "Git hooks" in `docs/hooks.md`). `.githooks/harness/` holds each enabled git hook and its files. `core.hooksPath` of the repository config becomes the absolute path of `.githooks/` in the main checkout when it is unset or the relative `.githooks`. `receive.denyCurrentBranch` becomes `updateInstead` when it is unset, so the `lw-land` push updates the base checkout. The git config key `harness.globalHooks` gets the value of `harness.githooks.global`. |
| `.claude/settings.json` | Only the harness entries of the `hooks` key. Every other byte and every project hook stay as the project wrote them. |
| `.codex/hooks.json` | Only the harness entries of the `hooks` key. Each one runs the hook through the Codex adapter. Every project hook stays. |
| `.agents/skills/<id>` | A link to `../../.claude/skills/<id>` for each skill, project skills too. A project skill is a folder in `.claude/skills/` with a `SKILL.md` that no manifest installs and git does not ignore. An ignored folder is local to one checkout, so no link and no `AGENTS.md` line names it. The install never writes into a project skill. |
| `AGENTS.md` | The block between `<!-- lw-harness:start -->` and `<!-- lw-harness:end -->`. It lists the rules and the skills for Codex. |
| `.gitignore` | The lines `/.claude/state/` and `/.claude/agent-harness/**/__pycache__/`, when no line ignores those files. |
| `.agents/harness.toml` | A commented starter file, only when none exists. The installer never rewrites it. |

A second install with no change writes nothing and prints `nothing changed`. An install removes
each file and link that the previous lockfile lists and the new one does not. It then removes
each folder that it leaves empty.

## Install from the installed copy

The project copy in `.claude/agent-harness/` is a package root. It holds the engine, the
manifest and the source of every rule, skill and git hook, also of a disabled one. So the
installed launcher installs again with no source package:

```bash
python3 .claude/agent-harness/bin/ah install --project
```

Run it after a change of `.agents/harness.toml`, of a rule adapter or of a project manifest. It
installs the version that the copy holds. A newer version comes from its own package. The
installer reads every source file before it writes, because this install reads and writes the
same files.

A copy that lacks a file of its manifest cannot install. That is a global copy, a project copy
with a deleted source file, and a project copy with no `rules/`, `skills/` and `githooks/`
folder. `ah install` then exits 1, writes nothing and prints:

```
ah install: <copy> is an installed copy that lacks a source file of its manifest (<copy>/harness.toml is invalid: <file> does not exist). Run ah install from the agent-harness package: python3 <package root>/bin/ah install, or pass --from <package root>
```

A project copy is a file that the previous lockfile does not list and that the install would
replace or leave behind. It is a planned file with other bytes (`differs`), or a file in
`.claude/agent-harness/`, `.claude/rules/lw/` or a planned skill folder that the plan does not
hold (`extra`). An install over a project copy exits 1, names each file and writes nothing. Move
each file you still need, then install again. `--replace` replaces the copy, removes its extra
files and names each one.

A git hook of the project in `.githooks/<name>` is a project copy too, because the dispatcher
takes that path. Such a hook can hold a rule of the project, such as a landing rule in
`push-to-checkout`. The refusal lists each project git hook apart, and `--replace` prints one
line for each one it replaces. Keep a project git hook in one of two ways:

- Move it to `.git/hooks/<name>`, which the dispatcher runs after the harness hook. Git does not
  version that folder, so each clone and each machine needs its own copy.
- Declare it as a `[[githook]]` of a project manifest that `harness.manifests` names (see "Extra
  manifests"). The install then puts it in `.githooks/harness/` with the harness hooks. Two git
  hooks with one name fail, so a project `push-to-checkout`, `commit-msg` or `post-commit` needs
  the harness hook of that name in `harness.disable`.

## Rule adapters

A project changes an installed rule with a rule adapter: `<adapterDir>/rules/<rule id>.md`, by
default `.claude/adapters/rules/<rule id>.md`. Without front matter, the install appends the
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

`ah rules` prints the size of each installed rule, so a project sees what its rules cost. Claude
Code also loads each project rule: a `.md` file under `.claude/rules/` outside `lw/`. The command
lists those too, and then gives a subtotal for each group:

```
.claude/rules/lw/anti-slop.md 82 lines 4790 chars
harness rules: 1 files 82 lines 4790 chars
.claude/rules/house.md 12 lines 640 chars
project rules: 1 files 12 lines 640 chars
total 94 lines 5430 chars
```

A project with no rule of its own gets no subtotal lines.

## Rule sizes and scopes

Claude Code loads a rule file with no `paths:` front matter in every session. It loads a rule
file with `paths:` only for a file that matches. The always-loaded size is the cost that every
session pays: the characters of every `.md` file under `.claude/rules/`, at any depth, whose
front matter has no `paths:` key. The count takes the whole file, front matter included.

One line gives that size:

```
always-loaded rules: 7 files 15257 chars
always-loaded rules: 7 files 15257 chars, cap 16000
always-loaded rules: 7 files 15257 chars, over the cap 12000
```

The second and the third form show `harness.rules.maxChars` when the project sets it. A size
equal to the cap is under the cap.

| Command | Where the line prints |
|---|---|
| `ah rules` | The last line. |
| `ah install --project` | The line before the final `ah install:` line. |
| `ah check` | After the information lines, before the lines of the parts that differ. `ah check --global` does not print it. |

`harness.rules.maxChars` is a cap in characters. The default `0` is no cap. With a cap,
`ah check` exits 1 when the size is over it and prints on stderr:

```
ah check: the always-loaded rules hold 15257 characters, over harness.rules.maxChars 12000. Three ways down: leave a rule out with harness.disable, scope a rule with [harness.rules.paths], or replace a rule body with a rule adapter
```

`ah install --project` does not fail over the cap. Its line says `over the cap`. A value that
is not 0 or a positive integer fails `ah install`, `ah check` and `ah rules` at exit 1:
`config key harness.rules.maxChars must be 0 or a positive integer, not <value>`.

`[harness.rules.paths]` scopes a rule to the files it is about. Each key is a rule id, and the
value is a list of globs:

```toml
[harness.rules]
maxChars = 12000

[harness.rules.paths]
instance-agnostic = ["apps/**", "packages/**"]
code-limits = ["**/*.ts", "**/*.py"]
```

The rule installs with these globs as its `paths:` block, in place of the paths of its
manifest. The `AGENTS.md` table shows the globs in its Covers column, or the `agentsCovers` text of a
rule that has one. The key works for a rule
of the package and for a rule of an extra manifest. Each of these errors exits 1 and writes
nothing:

| Case | Message |
|---|---|
| An id that is no rule | `harness.rules.paths names <id>, which no manifest defines as a rule; the rule ids are: <ids>` |
| A value that is no list, an empty list, or a glob with a double quote, a backslash or a line break | `config key harness.rules.paths.<id> must be a list of one or more globs, each with no double quote, backslash or line break` |
| The key is no table | `config key harness.rules.paths must be a table of <rule id> = [globs]` |

An empty list is refused, so the key cannot make a scoped rule load in every session. A change
of the key needs `ah install`. Until then, `ah check` reports the rule file as `stale`.

The package scopes two of its rules. `spec-format` loads for `docs/specs/**` and
`docs/superpowers/plans/**`. `codex` loads for `AGENTS.md` and `.codex/**`, because only Codex
needs it and Codex reads it from the `AGENTS.md` table. Its `agentsCovers` is `every file`, so
the table tells Codex to read it before each change. An upgrade from 0.2.11 writes
`.claude/rules/lw/codex.md` and the `AGENTS.md` block again once. With the default config, the
always-loaded size of the package rules is 15257 characters in 7 files.

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

The adapter maps the payload as follows:

- `CLAUDE_PROJECT_DIR` is the checkout, and `CLAUDE_CODE_SESSION_ID` is the `session_id` of the
  payload.
- `exec_command` and `shell_command` become `Bash`, and `cmd` is copied to `command`.
- An `apply_patch` becomes one `Edit` event for each `*** Add File`, `*** Update File` and
  `*** Delete File` path.
- A payload `workdir` or `cwd` inside `<root>/.claude/worktrees/<name>/` selects that worktree
  only when git reports the same common directory for it and for the root. The copy of the
  script in that worktree runs when it has one. Any other directory is data for the hooks.

A `deny` or a `block` of a hook passes through at once. Else the first `ask` becomes a `deny`,
and else the adapter joins every `additionalContext`. Nothing shows that Codex honors the
PreToolUse `ask` decision. So the adapter turns an `ask` into a `deny` that keeps the reason of
the hook. The reason adds that the user runs the reveal themselves, or confirms it in Claude Code.

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
with no change writes nothing. A project entry above a harness entry makes `ah check` report the
file as `differs`, and the next install moves the project entry below.

A project hook that must survive every sync can also come from a project manifest (see "Extra
manifests"). Then `ah selftest` runs its fixtures too. A project git hook takes the same path: a
`[[githook]]` of a project manifest installs to `.githooks/harness/<name>`, and the dispatcher
runs it. Its name must differ from each enabled harness git hook, so disable the harness hook of
that name to use your own.

## Likely duplicates

A project that had its own agent setup before the harness can hold old copies of harness hooks
and skills. Then both copies run. `ah install` and `ah check` print one line for each likely
duplicate:

- a project hook entry with a command word that is a script with the file name of a harness hook
  script, suffix aside, such as `.claude/hooks/workspace-guard.py`;
- a project skill folder whose name is a harness skill name without `lw-`, such as
  `.claude/skills/land` beside `lw-land`.

```
.claude/settings.json PreToolUse Bash: a likely duplicate of the harness hook workspace-guard (same script name workspace-guard.py): python3 .claude/hooks/workspace-guard.py
.claude/skills/land: a likely duplicate of the harness skill lw-land
```

These lines are information. They do not change the exit code. To remove an old copy:

1. Compare it with the harness copy. Move a project rule that only the old copy holds into a
   rule adapter, a project manifest or `.agents/harness.toml`.
2. For a hook, delete its entry from `.claude/settings.json` and `.codex/hooks.json`, then delete
   its script. For a skill, delete the folder `.claude/skills/<name>`.
3. Run `ah install --project`. It removes the link `.agents/skills/<name>` and the skill line
   of `AGENTS.md`.

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

Without the key, `harness.manifests` is `.claude/githooks/harness.toml` when that file exists. Put
the git hooks of the project and their manifest in `.claude/githooks/`, so no project file of the
harness sits outside `.claude/` and `.agents/`.

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
| `differs` | `.claude/settings.json`, `.codex/hooks.json`, `AGENTS.md`, `.gitignore` or a skill link differs from what the installed manifests and the config give. Also the git config key `harness.globalHooks`, when its value is not `harness.githooks.global`. An unset key counts as `run`. |
| `stale` | A rule adapter changed, came or went after the install. Also a rule file whose `[harness.rules.paths]` entry changed, and the `secrets` rule after a change of `harness.plainEnvFiles`. The installed rule still holds the old text until the next install. |

The check reads `.claude/settings.json` and `.codex/hooks.json` as JSON, so the layout of a
hand-written hook is no difference. It prints one line for each project hook in the two files,
an entry that no installed manifest declares:

```
.claude/settings.json PreToolUse Bash: a project hook that no manifest declares: bash scripts/x.sh
```

These lines are information. They do not change the exit code.

`ah install` and `ah check` also print one warning line for each absolute or `~/` entry of
`harness.workspace.repos`. Such an entry names a folder of one machine. Write the entry relative
to the main checkout instead, such as `../client` or `../*`. The warning does not change the exit
code.

```
warning: .agents/harness.toml harness.workspace.repos: ~/repos/* is a path of this machine; write it relative to the main checkout, such as ../*
```

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

The case `global-hooks` runs last, when the install has a git hook. It adds four result lines
with `git` in the client column, and they count in the last line
`ah selftest: N of N fixture runs pass`. The case proves that the dispatcher follows
`harness.githooks.global` (see "Git hooks" in `docs/hooks.md`).

## The global install

`ah install --global` writes `~/.claude/agent-harness/` (`bin/`, `src/`, `tools/`,
`harness.toml` and `lock.json`) and `~/.claude/skills/lw-*`. `HOME` decides the folder. A global
`ah` inside a repository with a project install hands over to the project engine. `ah install`
never hands over: it installs the package of the engine that you start. The global copy holds no
hook, no rule and no git hook, so it is no package root: `ah install` from the global launcher
needs `--from <package root>` (see "Install from the installed copy").

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
# agentsCovers = "every file"    # optional: the Covers cell of the AGENTS.md block

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

`paths` of a rule scopes the rule for Claude Code. The `AGENTS.md` block prints the same globs in
its Covers column, and Codex reads that column. `agentsCovers` is the text of that column for a
rule that Claude Code loads for some paths and Codex needs for every file. It is one line with
no `|`. It changes only the `AGENTS.md` block: the `paths:` block of the installed rule file
stays. A `[harness.rules.paths]` entry for the rule changes the `paths:` block and keeps the
`agentsCovers` text in the table.

## Config keys

The installer reads these keys of `[harness]` in `.agents/harness.toml`:

| Key | Default | Meaning |
|---|---|---|
| `disable` | `[]` | Ids of hooks, rules, skills, git hooks and tools to leave out. A project skill can be named too. An unknown id fails. |
| `enable` | `[]` | Ids of items that are off by default. |
| `manifests` | `[".claude/githooks/harness.toml"]` when that file exists, else `[]` | Extra manifests of the project, relative to the root. `ah install` adds each `--manifest` to them. |
| `adapterDir` | `.claude/adapters` | The folder of free-text notes per skill that the `AGENTS.md` block names, and of the rule adapters in `rules/`. |
| `rules.maxChars` | `0` | A cap in characters on the always-loaded rules. `0` is no cap. `ah check` exits 1 over the cap. |
| `rules.paths` | `{}` | Per rule id, the globs of the `paths:` block of the installed rule, in place of the paths of the manifest. |
| `githooks.global` | `"run"` | `"run"` or `"skip"`. The install writes it to the git config key `harness.globalHooks`, which the git hook dispatcher reads. |
| `plainEnvFiles` | `[]` | Env files that an agent may read, each as one exact file name. An entry with `*` or `?`, and an entry that a pattern of `guardedFiles` or `secretFiles` matches, fails the install and the check. A list that is not empty adds one line to the installed `secrets` rule. |
| `lint.words`, `lint.allow` | `[]` | More banned words for `ah lint`, and banned words that this project allows. |

A line that holds `lw-lint-ok` is not linted, for a line that must quote a banned form.
