---
name: lw-env
description: Use when you need to know or change what an env file holds, such as which keys a `.env*` file has, whether a key is set, how two env files differ, where a key is used, or to set, copy, move, rename or remove a key. Also use to push an env file to the secret store or pull it back. Never read an env file with cat, grep, head or an editor tool.
---

# Env files

The env engine answers every question about an env file without showing a value. Use it for
each read or write of a `.env*` file. Never open an env file with `cat`, `grep`, `head`, `sed`,
`less`, the Read tool or any other reader. The `deny-env-reads` hook refuses them, and a value in
the context can leak into a log, a commit or a reply.

Run the engine from the repository root:

```bash
python3 .claude/agent-harness/bin/ah env <subcommand> [args]
```

With only a global install, run `python3 ~/.claude/agent-harness/bin/ah env` instead. When
`python3 .claude/agent-harness/bin/ah config get harness.envCommand` names another command, that
command calls the same engine, and you can use it instead.

## Subcommands

| Need | Command |
|---|---|
| The keys of a file | `ah env keys <file>` |
| The keys that an app reads | `ah env keys <app>` (needs `[env] declarations`) |
| Key counts, empty keys and missing keys per file | `ah env status [<file> ...]` |
| Which file sets which key, as a table with the apps | `ah env status --matrix` |
| Undeclared keys, empty required keys, stray and git-tracked env files | `ah env check` |
| Where a key is set, declared, used and read in code | `ah env where <KEY>` |
| Whether a key is set, without its value | `ah env get <KEY> <file>` |
| Set or add a key | `ah env set <KEY> <file> --stdin` (value on standard input) or `--value <v>` |
| Remove a key | `ah env unset <KEY> <file>` |
| Copy a key to another file | `ah env copy <KEY> <from> <to> [--as <NEW>]` |
| Move a key to another file | `ah env move <KEY> <from> <to>` |
| Rename a key everywhere | `ah env rename <OLD> <NEW> [<file> ...]` |
| Compare two files | `ah env diff <a> <b>` |
| Add the keys that one file lacks | `ah env merge <from> <to>` |
| Copy only the declared secrets into a new environment file | `ah env merge <from> <to> --secrets-only` |
| Write the keys of one app to a file | `ah env slice <app> <file> --to <dest>` |
| Check the layout rules L1 to L7 | `ah env layout [DIR ...]`, `--fix --dry-run` to preview, `--fix` to reorder |
| Write the committed template of a live file from the master | `ah env template [<file> ...]` |
| Write the master and the environment templates from the declarations | `ah env template --master`, `--check` in a gate |
| Create a file with the keys of the master | `ah env init <file>` |
| Compare a file with the secret store | `ah env diff <env> --store [<name>]` |
| Send a file to the secret store | `ah env push <env> [--store <name>] [--apps a,b] [--all] [--dry-run] [--drop <KEY> ...] [--yes]` |
| Write the store values into the file | `ah env pull <env> [--store <name>] [--apps a,b]` |
| Start one command with the keys of a file | `ah env run <file> [--require <KEY> ...] -- <command>` |

A masked value prints as `empty`, or as `set, <length> chars, sha256:<8 hex>`. A connection string
(`postgres`, `mysql`, `redis`, `mongodb`, `amqp` and their variants) prints as
`scheme://***:***@host:port/database?<n> params`. `true`, `false` and a number of up to 6 digits
print as they are only for a key declared `secret = false`. Two files hold the same value when
the digests are equal.

Run each engine command alone: no pipe, no `;`, no `&&`, no redirection, no double quote and no
backslash. The `deny-env-reads` hook allows only the lone command on a guarded file. When the user
gives you a value to set, pass it with `--value '<v>'` in single quotes. A person or a script that
holds the value pipes it to `--stdin`, so the value stays out of `ps` and the shell history.

## Run a command with an env file

`ah env run <file> [--require <KEY> ...] -- <command> [args]` starts one command with the keys
of the file in its environment. The command can print them, so the guards ask the user unless
`[env] runCommands` lists the program, the file is a plain env file of the project, and no
argument can run code or expand (`sh -c`, `python3 -c`, `node -e`, `$X`, `/proc`). Never use
`run` to see a value: `env`, `printenv` and an interpreter always ask.

## The reveal rule

`ah env get <KEY> <file> --reveal` prints the value itself. Use it only when the user asks to see
that one value. Never use it to compare values (use `diff`), to check that a key is set (use
`get` without `--reveal`) or to copy a value (use `copy` or `move`). After a reveal, do not repeat
the value in a commit, a file, a log or a later reply.

## Rules

- A file that ends in `.example` is a committed template. `set`, `copy`, `move`, `merge`, `unset`
  and `init` refuse to write a value into one. Edit a template in an editor, or write it with
  `ah env template`.
- `set` puts a new key in its master block, after the keys that the master puts before it. A key
  that the master does not name goes into a last `# Other` block. Add the key to the master too.
- From a worktree, a live env file that only the main checkout holds resolves there. The output
  marks it `(main checkout)`.
- `push` to a store that holds the whole file refuses to drop a key that only the store holds.
  Name each such key with `--drop <KEY>` only when nothing reads it any more.
- Ask before `push` and `pull`. They change what an environment runs on, or overwrite local values.
- A push asks on the terminal. In an agent shell there is none, so pass `--yes` only after the
  user agreed to that push. A protected environment also needs `--confirm <env>`; the refusal
  names the exact command. Never add those flags on your own.
- A push refuses a key that the master does not name and a declared secret that is empty or a
  placeholder (`tobemodified`, `changeme`, `xxx`). Fix the file; do not work around it.
- With declarations, a push sends only the secret keys. `--all` sends the plain keys too.
- With several stores in `[[env.stores]]`, name one with `--store <name>`.
- A push to the `github` store stops when its GitHub Environment does not exist. Tell the user to
  create it in GitHub (Settings > Environments) with its protection rules. Pass
  `--create-environment` only when the user asks for an environment with no protection rule. The
  engine refuses it for a protected environment.

## Facts

Read a project fact with `python3 .claude/agent-harness/bin/ah config get <key>`, never from
memory:

| Fact | Key |
|---|---|
| The master env file | `env.master` |
| The env files that `status`, `rename` and `layout` cover | `env.files` (empty: every `.env*` file beside the master) |
| The key declarations | `env.declarations` |
| More folders of env files with their own master | `env.dirs` |
| The code files that `where` and `check` read | `env.readers`, `env.skip`, `env.ignore` |
| The secret store, or the named stores | `env.store`, `env.stores` |
| The environments whose push needs `--confirm` | `env.protected` |
| The programs that `run` may start with no question | `env.runCommands` |
