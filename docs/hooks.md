# Hooks, rules and git hooks

The package ships its hooks, rules, git hooks and skills through `harness.toml`. `ah install`
copies them into a project, and `ah selftest` runs every hook fixture. This page gives each hook,
the config keys it reads, the manifest format and the limits of the guards.

## The hooks

| Hook | Events | What it does |
|---|---|---|
| `workspace-guard` | PreToolUse (Bash) | Refuses a git mutation of another session's checkout, `git stash`, whole-tree staging, a ref-only move and every git command that is not a simple shape. |
| `deny-foreign-branch-moves` | PreToolUse (Bash) | Refuses a ref-only move of a branch that another worktree has checked out. |
| `worktree-path-guard` | PreToolUse (Bash) | Refuses `git worktree add` at a path other than `.claude/worktrees/<name>`. `ah worktree add <name>` makes a worktree there, so every guard lets it pass. |
| `deny-env-reads` | PreToolUse (Bash and the file tools) | Refuses any tool call that names a guarded credential file, and refuses every env file but a `.example` to the file tools. |
| `bash-read-guard` | PreToolUse (Bash) | Refuses a shell reader (`cat`, `grep`, `sed`, ...) of an env, key or credential file. |
| `env-drift` | SessionStart | Says when a local env file holds edits that a secret store has not received. |
| `load-memory` | SessionStart, PreToolUse | Injects the memory index once per session, keyed on the session id. |
| `prompt-directives` | UserPromptSubmit | Turns prompt flags (`-u`, `-e`, `-d`) and an embedded `/lw-goal-spec` into added context. A command after a quote mark or a backtick is a quote and adds nothing. |
| `render-spec` | PostToolUse, Stop | Renders a changed spec to its HTML page (see `docs/spec.md`). |
| `goal-spec` | Stop | Holds a turn open until the saved spec audit shows zero open rows. |
| `local-run-guard` | UserPromptSubmit, PreToolUse | Off by default. Keeps tests, lints and builds on a remote runner unless the user asks for a local run. |
| `scratch-cap` | PreToolUse (Bash) | Off by default. Runs a scratchpad probe in a cgroup with a memory and CPU cap. Needs `systemd-run` and `jq`. |
| `comment-budget` | PreToolUse, PostToolUse (Write, Edit) | Off by default. Refuses a write that makes a comment block longer than its cap, and reports the budget findings of the written file as added context. |

Every hook is Python 3.11 or a short bash script. `deny-foreign-branch-moves` and `scratch-cap`
need `jq`. No hook needs Node.

## How a hook runs

`ah install --project` copies `bin/`, `src/`, `hooks/` and `harness.toml` to
`.claude/agent-harness/`. The client runs each hook through the launcher:

```bash
bash .claude/agent-harness/hooks/run.sh .claude/agent-harness/hooks/<script> <event>
```

`run.sh` takes the script from the checkout that the shell runs in when that checkout has a copy,
so a session in a worktree runs the worktree's hooks. Otherwise it takes the script from the
project that holds the launcher. It picks `python3` for a `.py` file and `bash` for a `.sh` file.
A missing interpreter or script exits 1 with a message, which the client shows as a hook error.

A Python hook puts the folder above its own `hooks/` folder, plus `src`, on `sys.path` and imports
`agent_harness.hookio`. The project of a hook is the checkout that holds the hook file. An
installed hook sits three levels below the project root. Neither the payload nor
`CLAUDE_PROJECT_DIR` can move it. A shell hook reads a config value with
`python3 ../bin/ah config get <key>`, run from its own folder, and only when it needs the value.

A guard prints a PreToolUse `deny` or `ask`, or prints nothing. An `allow` from a PreToolUse hook
would bypass the permission rules, so no guard prints one. Only an env engine step that can put
a value in the context gets an `ask` (see below). Nothing shows that Codex honors an `ask`, so
the Codex adapter turns it into a `deny` that keeps the reason of the hook.

## Config keys

Every key sits in `[harness]` of `.agents/harness.toml`. `ah config get harness.<key>` prints the
value or its default. A config file that is not TOML gives a message on stderr, and each hook then
guards with the defaults.

| Key | Read by | Meaning |
|---|---|---|
| `landCommand` | `workspace-guard`, `deny-foreign-branch-moves` | The command a refusal names for landing. |
| `land.greenToken` | `lw-land` | Default `""`. When set, a gate is green only when it exits 0 and one line of its log equals this text. |
| `land.probeCommand` | `lw-land` | Default `""`. When set and the gate is red, `ah land-results` reads the last `verify: failures <json>` line of the gate log. A failed step that names no file stops the landing. Otherwise the probe command runs in a detached worktree at the merged base, with the failed files appended as arguments and `LAND_ROOT` and `LAND_CHECKOUT` in its environment. It exits 1 and prints its own record. When every failed file fails there too, the landing goes on. |
| `land.afterMerge` | `lw-land` | Default `""`. A command that runs in the branch worktree after the landing merges the base in, for example one that writes an index of the specs again. Its changes to tracked files are amended into that merge commit. It does not run when the merge made no merge commit. A failure stops the landing at exit 3. |
| `land.overlapExclude` | `lw-land` | Default `[]`. Paths, relative to the repository root, that the check for a carried gate does not count. A base move that touches only these paths and paths the branch did not change carries a green gate over. |
| `workspace.worktreesOnly` | `workspace-guard` | Also refuse a git mutation in the main checkout. |
| `workspace.gitConfigAllow` | `workspace-guard` | The keys `git -c` may set in an agent command. |
| `workspace.repos` | `workspace-guard` | Other repositories where git may run, as absolute paths or `~/` paths, for example a client library beside the project. git runs in the main checkout of the project and every worktree of it without this key. |
| `guardedFiles` | `deny-env-reads`, `bash-read-guard` | Names no tool call may carry. An entry is a pattern or `{ pattern, hint }`. |
| `secretFiles` | `bash-read-guard` | More credential names. An entry is a pattern or `{ pattern, hint }`. |
| `envCommand` | `deny-env-reads`, `bash-read-guard` | One more env engine command form, for example `lw env`. A deny names it. |
| `envDrift.envs`, `envDrift.stores` | `env-drift` | The environments, and one `{ name, push }` per store. `{env}` in `push` is the environment. |
| `memoryIndex` | `load-memory` | The index file, relative to the project root. |
| `directives` | `prompt-directives` | Each prompt flag and the text it adds. |
| `gateCommand`, `localRuns.hint`, `localRuns.dispatch` | `local-run-guard` | The gate command that always passes, the hint in a refusal, and the commands that pick their runner. A dispatch command passes, except with `--target local`. |
| `scratchCap.memoryMax`, `scratchCap.cpuMax` | `scratch-cap` | The caps of the cgroup. |
| `comments.maxBlock`, `comments.longBlock`, `comments.maxShare`, `comments.shareFloor`, `comments.minLines`, `comments.maxLineLength`, `comments.include`, `comments.exclude`, `comments.overrides` | `comment-budget`, `ah comments` | The comment budget (below). |

A file pattern matches one whole base name and ignores case. `*` and `?` match inside one name.

## The comment budget

`ah comments [path ...]` and the `comment-budget` hook share one measure, `agent_harness.comments`.
It scans lines and does not parse: `//`, `/* */` and a JSDoc block in JS and TS, `#` lines
and a docstring that opens a line in Python and shell, and `#`, `//` and `/* */` in Terraform. A
shebang is code.

A file is new when the base branch (`harness.baseBranch`) does not hold it. A file that git finds
renamed from a path the base branch holds is not new: it keeps the caps of its old path. The
check is `git diff --find-renames=30% <base>` over the `include` patterns, so it sees a rename
that is staged or committed. The floor is 30% because a move rewrites the import lines.

| Key | Default | Applies to |
|---|---|---|
| `comments.maxBlock` | `12` | The longest comment block of a new file. |
| `comments.longBlock` | `25` | The longest comment block of every file. |
| `comments.maxShare` | `0.35` | The share of comment lines in a new file with `shareFloor` code lines or more. |
| `comments.shareFloor` | `60` | Below this many code lines, the share is not measured. |
| `comments.minLines` | `20` | A file with fewer lines is not measured. |
| `comments.maxLineLength` | `0` | The longest comment line in characters, indent left out. `0` is no cap. |
| `comments.include`, `comments.exclude` | TS, JS, Python and Terraform files; tests, `docs/`, `.claude/`, `dist/` and declarations out | The path patterns (`**`, `*`, `?`) of the files that the budget reads. |
| `comments.overrides` | `{}` | A path pattern and the caps that replace the ones above for the files it matches. The first pattern that matches wins. |

An override sets only the caps: `maxBlock`, `longBlock`, `maxShare`, `shareFloor`, `minLines`
and `maxLineLength`. Any other key fails the run. This override gives Terraform one comment
line per value or block, and 100 characters per line:

```toml
[harness.comments.overrides."**/*.tf"]
maxBlock = 1
longBlock = 1
minLines = 0
maxLineLength = 100
```

A project that moves to the budget with files over a cap names each of them in
`comments.exclude`. That list is the exemption register. It only shrinks: a file leaves the list
when it meets the budget, and no file joins it.

`ah comments` reads the tracked and untracked files of the checkout, or the named files and
folders, and prints one line per finding. The hook denies a Write or an Edit that makes a block
longer than its cap. A block that the file already holds is the floor, so an edit near old prose
passes. After the write, the hook reports each finding as added context, the line length
included. A Codex `apply_patch` gives the hook the path only, so Codex gets the report and not
the deny.

## The credential name guard

`deny-env-reads` replaces `Read(**/.env*)` deny rules of the client. With those rules, the client
resolved every path that a Bash command reads, and prompted when one did not resolve.

The Bash check names no program. A list of readers (`cat`, `grep`, `sed`, ...) misses `source`,
`< file` and `python3 -c open(...)`, so a command that holds a guarded name is refused, whatever
it runs. The cost is that the name cannot appear in shell prose either, a commit message and a
heredoc body included. The one exemption is the words of an env engine step that the guard
clears (see The env engine exemption). In Bash, every other env file is the job of
`bash-read-guard`, which refuses it to a reader. A credential backup in the tree is a copy
outside the secret store that nothing rotates.

## The env engine exemption

`deny-env-reads` and `bash-read-guard` read a Bash command as the steps that bash runs:
`agent_harness.shell_steps` splits it, and `agent_harness.env_guard` gives the answer for each
engine step. A step runs the env engine when its first words, after leading `VAR=value`
assignments and a keyword such as `then` or `do`, are one of these forms:

- `python3 .claude/agent-harness/bin/ah env`
- `python3 ~/.claude/agent-harness/bin/ah env`
- `python3 <project root>/.claude/agent-harness/bin/ah env`, with the absolute path of the project
  that holds the hook
- `ah env`
- the value of `harness.envCommand`

A step starts at the start of the line, and after a newline, `;`, `&&`, `||`, `|`, `(` or `{`.
So `cd app && ah env status` runs the engine. Text in single or double quotes is data, and so is
a heredoc body that goes to a program other than a shell. So `grep -n "ah env" docs/env.md` and
a commit message that names `ah env get` run no engine. A heredoc body or a here-string that goes
to `sh`, `bash`, `zsh` or another shell is read as steps, and so is each `$(...)`, backtick and
`<(...)` substitution, also inside double quotes and in an unquoted heredoc body.

Some steps can hide an engine run, so they ask. A step whose command word holds a `$` or a
backtick asks when the line holds the word `env` (`$AH env status`). A wrapper (`env`, `eval`,
`exec`, `sudo`, `timeout`, `xargs` and the like), a package runner (`npx`, `pnpm`, `uv` and the
like) or a shell whose arguments hold the engine words asks (`bash -c 'ah env status'`). A command that the parser cannot read, such as one with an
unclosed quote, gets no exemption, and it asks when its words hold the engine words anywhere.

An `ah`, `./bin/ah` or `python3 <path>/ah` followed by `env` is an engine step that is no form of
the list. It gets the same asks, but no exemption.

Each engine step gets one of these answers:

| Step | Answer |
|---|---|
| Any expansion in the subcommand, or an unquoted `$`, backtick, glob or brace in a global option before it | Ask: the guard cannot see which subcommand runs. |
| A quiet subcommand: `keys`, `status`, `check`, `where`, `layout`, `diff`, `template`, `init`, `set`, `unset`, `copy`, `move`, `rename`, `merge`, `slice`, `push`, `pull`, `help` | Pass, whatever its later arguments expand to. |
| `get` with no `--reveal` | Pass. It prints the masked value. |
| `get ... --reveal` of a key that `[env] publicKeys` names, every word literal | Pass, for any file, `.env.production` included. |
| `get ... --reveal` of any other key | Ask, with the key name. |
| `get` with an unquoted expansion, or a double-quoted one with no literal `/` beside it | Ask: the word can turn into `--reveal`. `"$SCRATCH/x.env"` cannot. |
| `run` on a file outside the project | Deny. |
| `run` on a held file (a guarded or `secretFiles` name) or the `.env.<env>` of an environment in `[env] protected` | Ask, with the file name. |
| `run` after a `cd` that the guard cannot follow, inside a substitution, or on a file that an expansion names | Ask. |
| `run` on any other file | Pass, whatever program it starts. The engine redacts the output (docs/env.md). |

The credential name scan of both hooks skips the words of each step that passes or asks about a
reveal: the engine reads a file that its arguments name, and it is no reader. For `run`, the scan
skips the words up to `--` and reads the command after it as any command. The scan reads every
other step, every redirection and every heredoc body in full. A step skips nothing when the guard
cannot check its global options, when it is no form of the list, and when it sits in a
substitution or a shell body. Each other step of the line
gets the checks of any command.

The global options `--root`, `--master` and `--declarations` may come before the subcommand. The
guard checks each value: `--root` comes once and resolves to the project root or a folder inside
it, and `--master` and `--declarations` resolve inside the project and name no credential file by
the rules of that guard. A value that holds `$`, a backtick, a quote or a backslash fails. A step
with a value that fails still gets its answer, but the scan reads its words. So
`ah env --root /other/project status` passes, and `ah env --master .env.production status` is
refused.

`[env] publicKeys` lists key name patterns of config: `*_DRIVER`, `*_DRIVERS`, `*_REGION`,
`*_BUCKET`, `*_HOST`, `*_PORT`, `*_MODE`, `*_LEVEL`, `*_ENDPOINT_URL`, `NODE_ENV` and
`APP_ENV` by default. A `*` matches any text, and case counts. The engine is the safety net. When
it reveals a public key whose value looks like a credential (a URL with a password, a PEM block,
or a whole value of 32 or more base64 or hex characters), it prints the value masked and says
why.

A reveal of a key that is not public gets an `ask`, and Claude Code asks the user to confirm it.
The `secrets` rule allows that reveal only when the user asked to see the value. Under Codex, the
adapter turns the `ask` into a `deny`. Its reason says that the user runs the reveal themselves,
or confirms it in Claude Code. A deny of the scan wins over an ask.

A credential backup (`.env.production.bak`, `.local/secrets/`) is refused even to the
engine.

## Worktrees

A worktree goes in `<main checkout>/.claude/worktrees/<name>`, on a branch named `<name>`. The
engine makes and removes one without a path, so no guard needs to read the command:

```bash
python3 .claude/agent-harness/bin/ah worktree add fix-login            # from harness.baseBranch
python3 .claude/agent-harness/bin/ah worktree add fix-login --from main
python3 .claude/agent-harness/bin/ah worktree remove fix-login
```

`add` refuses a name with a `/`, a leading `.` or `-`, or `..`, a folder that exists and a branch
that exists. It prints the path of the new worktree. `remove` refuses a folder that is no
worktree of this repository, a worktree with a changed or untracked file, and the worktree that
the command runs in. It keeps the branch: the `lw-branch-audit` skill decides about the branch.

## Git configuration in an agent command

`workspace-guard` lets `git -c` set only a key that `workspace.gitConfigAllow` names. A `*`
matches any rest of the key, and the test ignores case. The default list is `user.name`,
`user.email`, `color.*`, `core.pager`, `core.quotepath`, `advice.*`, `log.*`,
`merge.conflictstyle`, `pull.rebase`, `init.defaultbranch`, the `diff` keys `algorithm`,
`colormoved`, `colormovedws`, `context`, `indentheuristic`, `interhunkcontext`, `mnemonicprefix`,
`noprefix`, `relative`, `renamelimit`, `renames` and `statgraphwidth`, and the `format` keys
`pretty`, `numbered`, `subjectprefix` and `signoff`. No key on the list names a path, so
`diff.orderFile` and `format.signatureFile` are refused.

The guard refuses every other key, for example `include.*`, `includeIf.*`, `alias.*`,
`core.hooksPath`, `core.sshCommand`, `core.fsmonitor` and `receive.*`. It also refuses a key that
runs a program, even when a pattern matches it: `diff.external`, a key that ends in `.command`,
`.textconv` or `.cmd`, and `core.pager` with a value other than `cat`, `more` or `less`.

`git config` that only reads passes in any directory of the repository: one key alone
(`git config user.name`, a word with a `.`), the subcommands `get` and `list`, `--get`, `--get-all`, `--get-regexp`, `--get-urlmatch`,
`-l` and `--list`. A write option refuses the command, also beside a read option and in an
abbreviated form: `--add`, `--unset`, `--unset-all`, `--replace-all`, `--rename-section`,
`--remove-section`, `-e` and `--edit`. A key with a value and every other subcommand, such as
`set`, `unset`, `edit`, `rename-section` and `remove-section`, are writes too.

Git reads configuration from more places, so the guard also refuses:

- `--config-env`, and any `GIT_CONFIG*` variable in any form, `echo $GIT_CONFIG_COUNT` too.
- In front of git, any variable other than `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`,
  `GIT_AUTHOR_DATE`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL`, `GIT_COMMITTER_DATE`, `LANG`,
  `LC_*`, `TZ`, `NO_COLOR`, `TERM` and `GIT_TERMINAL_PROMPT`.
- The git flags `--exec-path`, `--git-dir`, `--work-tree`, `--bare` and `--namespace`.
- Git in a directory outside the repository of the project. The guard follows each `cd` and `-C`
  to the effective directory of each git step. A nested repository or a submodule is refused.

## Git command shapes

A command that involves git may only be steps joined by `&&`, `;` or a newline:

- `cd <literal path>`, with no option, no `-`, no `~` and no variable.
- One `git` command with literal arguments. Allowlisted variables may stand in front of it, and
  `-C <literal path>` is allowed. It may take a heredoc whose body bash does not expand
  (`git commit -F - <<'EOF'`), `2>&1` and output to `/dev/null`.
- That git command piped into one `head`, `tail`, `grep`, `rg`, `wc`, `sort`, `uniq`, `cat`,
  `less`, `jq`, `sed -n` (with a `N,Mp` or `/re/p` script) or `awk` (with a literal program that
  holds no `system`, `|`, `>` or `@load`).
- A reader step such as `echo`, `ls`, `grep`, `diff` or `md5sum`, with literal arguments and `$?`.

Every other form is refused with one message that names these shapes: a runner (`env`, `xargs`,
`timeout`, `sudo`, `eval`, `bash -c`, ...), a compound command, a subshell, a function, a
substitution, a variable, a glob, `||`, a background `&` and redirection to a file.

`git worktree remove`, `move`, `lock`, `unlock` and `repair` may name only the worktree of the
effective directory, and that worktree must be the session's own. Each path must be the exact
path of that worktree, absolute or from the effective directory, with no `..` and no link in any
part. git also takes a short name such as `own` for `.claude/worktrees/own`, and the guard
refuses it: the refusal names the full path to write. A session may name its own worktree,
`remove` too. git 2.43 removes the worktree that the command runs in, and the removal harms no
other session.

`move` puts the worktree only in a new folder right in `.claude/worktrees` of the shared
checkout, with a name of letters, digits, `.`, `_` and `-`. The target is written with no `..`,
it must not exist, and neither `.claude` nor `.claude/worktrees` may be a link. The move must
come before any step that is not git in its command, because such a step could create the
target first.

`deny-foreign-branch-moves` refuses `git branch` with a flag that moves, renames, copies or
deletes a ref when the command names a branch that another worktree has checked out. It reads
each word after `branch` up to `--` or the end of the step: a short bundle with `f`, `m`, `M`,
`c`, `C`, `d` or `D`, or any prefix of `--force`, `--move`, `--copy` or `--delete`. It reads `-C
<path>` only between `git` and the subcommand of each git step, so `git branch -C a b` is a copy.
More than one `-C` over all git steps is refused, and so is one `-C` on a git step that moves no
ref. git counts as the base name of a word (`/usr/bin/git`, `command git`). The hook removes a
backslash and the newline after it, as bash does. Bash keeps both in a comment and in single
quotes, so the hook also reads the text with each such pair as a newline. A command that joins
lines and also holds a `#` or a single quote can mix both forms, so a git ref command in it is
refused. The hook removes quotes and backslashes from each word before it reads the word as a
flag.

## Heredocs and key names in the read guard

`bash-read-guard` scans the whole command. It exempts a heredoc body in two exact shapes only:

- `cat <<DELIM` or `cat <<'DELIM'`, to the terminal, to `> /dev/null`, or to one `> file` whose
  path is a plain relative path with no segment that starts with `.` and no script extension.
- `git commit -F - <<DELIM` or `git tag <name> -F - <<DELIM`, with the heredoc as stdin, no
  redirection and only the flags `-a`, `--all`, `--amend`, `--allow-empty`, `-q`, `--quiet`, `-s`
  and `--signoff` (commit) or `-a`, `--annotate`, `-s`, `--sign`, `-f` and `--force` (tag).

Anything else on the line cancels the exemption. An unquoted body with a `$` or a backtick is
scanned too, because bash expands it.

Bash expands no glob in the body of a heredoc with a quoted delimiter (`<<'EOF'`, `<<"EOF"`,
`<<\EOF`). The literal name scan still reads every body: a script that names `.env.production`
is refused. The glob scan skips the body of a data heredoc: the first line is `cat <<'D'`,
`cat > file <<'D'` or `tee [file] <<'D'`, with at most one more `> file` and a pipe into `head`,
`tail`, `grep`, `wc` or `cat`. Each command after the closing delimiter must be a bare `echo`,
`printf`, `true`, `pwd` or a reader of the listing list below, with no substitution, heredoc,
group or loop. So `cat <<'EOF' | wc -l` with a `/**` line passes, and so does
`cat >> notes.md <<'EOF'` followed by `ls docs | head`. `cat <<'EOF' > x.sh && ./x.sh`,
`bash x.sh` after the body and `git ls-files --pathspec-from-file=- <<'EOF'` get the body scanned,
because a later step can run the file or read the body as file names.

A program that `python3 -`, `python -` or `node -` reads from a quoted heredoc expands a glob only
through a glob or match function, or through a program or shell that it starts. So the glob
scan skips the body when the first line ends in `<<'D'` and the body holds none of these words:
`glob`, `fnmatch`, `match(`, `system`, `popen`, `exec`, `spawn`, `subprocess`, `shell`, `pty`,
`ctypes` and the dynamic imports for Python, and `glob`, `match`, `exec`, `spawn`,
`child_process`, `shell`, `fork` and a dynamic `require` or `import(` for Node. A `/**` comment or
a regex class in such a body passes. The literal name scan still reads the body, and the lines
after the delimiter are commands like any other. perl, ruby and the other interpreters keep the
full scan, because a backtick or a pipe `open` there runs a shell. A line that opens the heredoc
and holds a `#`, a backtick, `$(` or `((` keeps the whole command scanned.

Some words name no file that the command reads, so the scans skip them:

- The script of grep, rg and `git grep` (the `-e` value, else the first operand) skips both
  scans: the tool matches it against lines. A script file (`-f`), a path operand and an unknown
  option are still read. So `grep -rn '\.env' src` passes and `grep -f .env.local x` is refused.
- A sed script of only `s`, `y`, `p`, `d`, `q`, `=`, `n` and `N` commands, with plain addresses
  and the `s` flags `g`, `p`, `i`, `I`, `m`, `M` or a number, skips both scans. An `e` or `w`
  command or flag, `r`, a block, a label, `-f` and an unknown option keep the script scanned.
- The operands of `git branch`, `git tag`, `git for-each-ref` and `git show-ref` are ref
  patterns, unless the stage has `-F` or `--file`.
- A name filter of grep or rg (`--include`, `--exclude`, `--exclude-dir`, `-g`, `--glob`,
  `--iglob`) only drops files from a recursive search, which reads every file without it. So
  `--include=*` and `--include=*.ts*` pass. A filter that names a credential family by its
  literal part (`*.pem`, `.env*`) or by the start of a guarded name (`vault*` for `vault.json`),
  or that holds a bracket class, still counts.
- `ls` prints names, and `find` with no `-exec`, `-ok`, `-delete`, `-fprint` or `-fls` prints
  names. Their words skip both scans when their output, and the output of each `head`, `tail`,
  `grep`, `wc` or `cat` that a pipe feeds, reaches only the terminal or `/dev/null`. A pipe into
  any other program, a redirection to a file, `|&`, `&>`, a substitution, a group, a loop,
  `exec`, `eval`, `coproc`, `$_` and `--files0-from` anywhere in the command void this. An
  unquoted glob of `find` keeps the scan. So `ls /srv/x/*/* | head` and
  `find . -path '*ref*' | head` pass, and `ls *.pem | xargs cat` is refused.
- A `$(...)` inside double quotes is a command line of its own, so a grep script in it is a
  script too.

A sentence can end in a `.example` name, so `app/.env.example.` is the committed reference.

The guard reads a command only when it runs a reader, such as `cat`, `hexdump`, `zcat` or `ed`,
or feeds a file with `<`. A `$(<file)` or `$( < file)` counts as a reader, also inside double
quotes. `ln` is no reader: a link prints nothing, and a later read through the link name is a
read that the guard does not follow.

An env name, a `*.secret` name, `.netrc`, `.git-credentials` and each `secretFiles` name count
wherever the command holds them. A key or certificate name (`*.pem`, `*.key`, `*.p12`, `*.pfx`,
`id_rsa*`, `id_ed25519*`) counts only when it holds a `/` or names a file in the command's
directory. It counts by name alone when the command can read in another directory: `cd`, `find`,
`xargs`, a `-C` flag, `--git-dir`, `--work-tree` or `chdir`. A glob that can expand to one of the
names is refused, and the reason says that the word could match a credential file. The guard
expands braces first, and a range such as `{j..l}` reads as `*`. A bracket expression stands for
one character. A bracket that the guard cannot read as a set, such as the reversed range `e-a`
of the TOML list `["instance-agnostic"]`, reads as any one character, so such a word passes when
no credential name has its length and its other characters.
Program text puts a comma, a bracket, a brace, a parenthesis or a quote next to a glob word, as in
`subprocess.run(['cat *.pem'], shell=True)`. A shell that the program starts expands the word
alone, so the glob scan also reads each word without that punctuation.

A glob such as `*` can expand to a key name, so the name test alone refuses it. The guard then
lists the folder, as bash does, and lets the glob pass when it matches real files and none of
them is a credential file. A link counts by its own name and by the name of its target. The
guard lists the folder only for an unquoted glob in the last part of the word, with no
variable, substitution, brace or `~`. A relative glob uses the directory of its step: the guard
follows each `cd <literal path>` that stands alone between `;`, `&&` and newlines. Any other
`cd`, `pushd` or `popd`, for example in a subshell, keeps the name test for every relative glob.
With `dotglob` on, the name test stays too.

The guard lists the folder when the hook runs, so the listing counts only when no step can
change the folder or run a program before the glob expands. Every step between `;`, `&&` and
newlines must be `cd <literal path>`, a sed run with a plain script (see above) and no `-i`, or
one of `ls`, `wc`, `cat`, `head`, `tail`, `grep`, `egrep`, `fgrep`, `stat`, `du`, `md5sum`,
`sha1sum`, `sha256sum`, `diff`, `cmp` and `nl`, as a bare name. A pipe may feed only `head`, `tail`, `grep`, `wc` or `cat`. `rg`, `sort`, `uniq`,
`less` and `file` are not in the lists, because an option of each can run a program or write a
file (`rg --pre`, `sort -o`, `uniq IN OUT`, `less`, `file -C`). A `$(`, a backtick, `<(`, `>(`,
a heredoc, a background `&`, `||`, a group, an assignment and any redirection but `>/dev/null`
and `2>/dev/null` anywhere in the command void the listing, quoted patterns included. Otherwise
the name test decides.

A glob that matches no file keeps the name test. Bash then passes the word as written, but a
step before it in the same command can create a file that the glob matches when it runs. So
`cat /srv/empty/*` stays refused, and `wc -l /srv/skills/lw-land/*` passes when the folder
holds only `SKILL.md` and `land.sh`.

git matches a quoted pathspec itself. An exclude pathspec (`':!x'`, `':^x'`, `':(exclude)x'`)
drops names, so the glob scan skips it. A long magic list counts as an exclude only when one
entry is exactly `exclude`: `':(top,exclude)x'` drops names, `':(top,exclude-x)x'` picks them.
Any other quoted pathspec picks names, and git matches a name that starts with `.` to it, so
`git grep x -- '?env'` is refused. Without `--`, each word after the subcommand that is no
option is a pathspec, the pattern of `git grep` included. A pattern that is no glob of a
credential name passes. Under the `icase` magic, the name test reads the pathspec in lower case.

A listing hands a reader file names that the line never shows. `ls`, `find`, `fd`, `locate` and
`git ls-files` list names, and a listing that no pattern of its own bounds reads as the glob
`*`. The guard refuses one whose names reach a reader: through a `$(...)` or backtick
substitution, a pipe into `xargs`, `parallel` or a `while read` loop, or a `find -exec` or `-ok`
action that runs a reader. So `cat $(ls -a | grep env)` and `find . -type f | xargs cat` are
refused. A glob operand of `ls`, an operand that is a file, a `find -name` test and a `git
ls-files` pathspec bound the names, and the name test reads them. A substitution whose last
stage is `wc` or a counting `grep` (`-c`, `-q`) keeps no name, so `n=$(ls | wc -l)` passes, and
a pipe into `grep` or `head` reads the names as lines, not as files.

## Limits of the guards

The guards stop accidents by cooperating agents. They are not a sandbox. Each one reads the
command text before it runs, so a script file written earlier, or a program that reads a
credential file itself, still gets through. In `bash-read-guard`, a sed `e` command or `s///e`
flag, or any interpreter, can build a file name at run time that the guard cannot see; the guard
is an accident guard, not a sandbox. Keep real secrets out of the agent's reach with file
permissions, separate users or a container.

## Git hooks

`ah install --project` writes the git hooks to `.githooks/harness/` and sets `core.hooksPath` to
the absolute path of `.githooks/` in the main checkout. Git runs a push hook in the git
directory, where a relative path does not resolve, so `push-to-checkout` needs the absolute path.

With `core.hooksPath` set in the repository, git runs no hook of a global `core.hooksPath` (from
`git config --global`) and no hook of `.git/hooks/`. So `.githooks/<name>` is a dispatcher, one
copy for each client-side hook name: `applypatch-msg`, `pre-applypatch`, `post-applypatch`,
`pre-commit`, `pre-merge-commit`, `prepare-commit-msg`, `commit-msg`, `post-commit`, `pre-rebase`,
`post-checkout`, `post-merge`, `pre-push`, `post-rewrite`, `pre-auto-gc` and `push-to-checkout`.
A harness git hook with another name gets a dispatcher too. The dispatcher is POSIX sh and runs,
in this order:

1. The hook of the same name in the global `core.hooksPath`, when that path is set and is not
   this `.githooks/` folder.
2. The harness hook of the same name in `.githooks/harness/`.
3. `$(git rev-parse --git-common-dir)/hooks/<name>`, only when no global path is set. A global
   dispatcher runs that file itself, so it never runs twice.

Each hook gets the same arguments and the same stdin. The dispatcher keeps stdin in a temporary
file, because `pre-push` and `post-rewrite` read it. A nonzero exit stops the chain with that
code, so a global `pre-push` that exits 1 refuses the push. A missing or not executable hook is
skipped. When no hook of the chain handles `push-to-checkout`, the dispatcher does what git does
without the hook: it refuses a checkout with staged or unstaged changes, else it updates the
checkout with `git read-tree -u -m`.

| Git hook | Id | What it does |
|---|---|---|
| `push-to-checkout` | `push-to-checkout` | Lands `git push . HEAD:<branch>` into the checkout that holds the branch. It refuses, and changes nothing, when a file the landing writes is uncommitted there, or when an untracked or ignored path is in the way of a file the landing adds (the same path, a file where a folder comes, or a folder where a file comes). Git alone writes over an ignored path. |
| `commit-msg` | `commit-msg-strip` | Runs `strip_commit_trailers.py`, which takes agent attribution trailers and credit lines out of the message. It never blocks a commit. |
| `post-commit` | `noreply-identity` | Off by default. Rewrites the author and committer of a new, unpublished commit to the `users.noreply.github.com` address. With it on, `lw-land` stops at exit 5 on a branch commit without that address, and gives its own merge commit the address. |

`strip_commit_trailers.py` also gives `strip(message)` and `carries(message)` to a test that
checks the history, so the hook and the check apply the same rules.

### The commit-msg rules

The rules of `strip_commit_trailers.py` key on the shape of an attribution, so a new wording of a
client default still matches. An attribution is a trailer whose key or value names Claude, a
credit sentence, a session link or an `@anthropic.com` address.

The `commit-msg` hook runs `strip` over the message file and edits it. It never rejects a commit.
A reject costs the author a retry and removes nothing, and a generator that writes the trailer
from a template fails again on each retry. A project test runs `carries` over the history,
because `git commit --no-verify` skips every hook. Both read `RULES`. A check stricter than the
hook fails a commit that the hook already cleaned. A check looser than the hook lets through what
the hook takes out.

A rule can only be explained with the text that it bans. The module, the rule file, the test and
a commit message about them write `Co-Authored-By:` and `Claude-Session:` in prose, and `strip`
must keep each of them byte for byte. A commit body can also cite a `.claude/...` path. So:

- Every inline rule needs a value after the key. A bare key does not match. An inline rule on
  `Claude-Session[ \t]*:[ \t]*\S+` would take a backtick out of a message whose token after the
  colon is a backtick and a comma, not a URL.
- No line rule reads the subject, so a rule never deletes the summary line.
- A trailer value that only names a path (`.claude/rules`) is no attribution. `IDENTITY` is the
  shape of an attribution: an address, a product link or a model name.
- The rule on the value reads only the trailer block, as git defines it: the last paragraph, with
  every line `Key: value`. Without that limit, a body line such as `Default: claude-opus-5` is
  deleted, and no history check sees the loss, because the line names a model.

To find a rule that takes too much, run the rules over a commit history. Assert that every line
they take holds `claude`, `anthropic`, `co-authored` or the robot emoji. This check is coarser
than the rules on purpose, so a regex that takes prose fails it.

## Rules

| Rule | File | Covers |
|---|---|---|
| `anti-slop` | `rules/anti-slop.md` | Every file |
| `git-workflow` | `rules/git-workflow.md` | Every file |
| `secrets` | `rules/secrets.md` | Every file |
| `spec-format` | `rules/spec-format.md` | `docs/specs/**`, `docs/superpowers/plans/**` |
| `operator-report` | `rules/operator-report.md` | Every file |
| `codex` | `rules/codex.md` | Every file |
| `core-principles` | `rules/core-principles.md` | Every file |
| `instance-agnostic` | `rules/instance-agnostic.md` | Every file |
| `code-limits` | `rules/code-limits.md` | Every file |

The rules install to `.claude/rules/lw/`. They hold no project fact: a rule names the config key
and the `ah config get` command instead, for example `harness.baseBranch`.

## Manifest format

`harness.toml` keeps one table per item. Paths are relative to the folder of the manifest.

```toml
[[hook]]
id = "bash-read-guard"
summary = "Refuse a shell reader of a credential file"
events = ["PreToolUse"]          # SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop
matcher = "Bash"                 # tool events only
script = "hooks/bash-read-guard.py"   # .py or .sh; the event name is argument 1
files = ["hooks/inject-memory.py"]    # more files the hook loads from beside itself
timeout = 60
codex = true                     # false: Claude Code only
default = "on"                   # or "off"

[[hook.fixture]]                 # read by ah selftest
tool = "Bash"                    # or Read, Edit, Write; or prompt = "..." for UserPromptSubmit
command = "cat .env.local"
expect = "deny"                  # deny, ask, block, context or silent
contains = "credential file"
decoy = true                     # the payload names a foreign checkout; its hook copy must not run

[[rule]]
id = "spec-format"
summary = "Spec format and rendering"
file = "rules/spec-format.md"
paths = ["docs/specs/**"]        # empty means every file

[[skill]]
id = "lw-spec-write"
dir = "skills/lw-spec-write"

[[githook]]
id = "commit-msg-strip"
summary = "Take agent attribution trailers out of a commit message"
name = "commit-msg"
script = "githooks/commit-msg"
files = ["githooks/strip_commit_trailers.py"]
```

A fixture runs on UserPromptSubmit when it holds `prompt`. A tool fixture runs on PreToolUse, or
on PostToolUse for a hook that has no PreToolUse event. A `deny` fixture passes on a PreToolUse
deny or on exit 2 with the reason on stderr.
