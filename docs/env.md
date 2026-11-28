# The env engine

`ah env <subcommand>` reads and changes `.env*` files and moves them to and from a secret store.
It works in any git repository. No subcommand prints a value, except `get --reveal`. `run`
replaces each value of its file with `***` in the output of the command it starts.

```bash
python3 .claude/agent-harness/bin/ah env status
```

## Config

The `[env]` section of `.agents/harness.toml` holds the env facts. Every key has a default.

| Key | Default | Meaning |
|---|---|---|
| `master` | `.env.example` | The master template. Its blocks and key order are the layout of every other env file. |
| `files` | `[]` | The live env files that `status`, `rename`, `layout` and `template` cover. Empty: every live `.env*` file beside the master. |
| `declarations` | `[]` | TOML files of key declarations, merged in order. See Declarations. |
| `declarationsCommand` | `""` | A shell command that prints one declared key name per line. `where` and `check` take each name as a declared key that the code reads. Empty: no command. See The declarations command. |
| `layout` | `true` | `false`: a new key goes at the end of a file, and no master order applies. |
| `paired` | `[".env.ci"]` | The live files that always take their own `<file>.example` as master, also when the root master holds a key of theirs. |
| `placeholders` | `["changeme", "xxx"]` | The values that `push` reads as no value for a declared secret, in any case. |
| `dirs` | `[]` | More folders of env files, each with its own master `<dir>/.env.example`. `layout` with no DIR checks them, and `check` accepts env files in them. |
| `scan` | `.github/workflows`, `infra`, `deploy`, `terraform` and the compose files | The files and folders that `where` searches for a key name. |
| `readers` | `*.py`, `*.js`, `*.jsx`, `*.mjs`, `*.cjs`, `*.ts`, `*.tsx`, `*.mts`, `*.cts`, `*.vue`, `*.svelte` | The globs of the code files whose env reads `where` and `check` find. See Code readers. |
| `skip` | `node_modules`, `.venv`, `venv`, `dist`, `build`, `.claude`, `__pycache__` and build caches | Folders that the reader scan and the stray file check leave out. An entry without a `/` is a folder name at any depth. An entry with a `/` is a path from the root. A value replaces the default list, so keep the defaults you need: `skip = ["node_modules", ".venv", "third_party", "app/vendor"]` leaves out vendored code. |
| `ignore` | `CI`, `HOME`, `LANG`, `NODE_ENV`, `PATH`, `PWD`, `SHELL`, `TERM`, `TMPDIR`, `TZ` | Keys that code reads and that `check` does not ask to declare. |
| `store` | `{ kind = "" }` | The one secret store. See Stores. |
| `stores` | `[]` | Named secret stores. See Stores. |
| `protected` | `[]` | Environments whose push needs `--yes --confirm <env>`, whose GitHub Environment a push never creates, and whose `run` always asks. |
| `publicKeys` | `*_DRIVER`, `*_DRIVERS`, `*_REGION`, `*_BUCKET`, `*_HOST`, `*_PORT`, `*_MODE`, `*_LEVEL`, `*_ENDPOINT_URL`, `NODE_ENV`, `APP_ENV` | Key name patterns of config, not secrets. `*` matches any text, and case counts. The read guards let `get --reveal` of such a key pass with no question, and the engine masks its value when it looks like a credential (see Masks). A value replaces the default list. |
| `writeRoots` | `<temp>/claude-*` | Folders beside the repository where `copy`, `set` and the other writing subcommands may write an env file, as glob patterns. The default is the agent scratch folders, where a session stages a key for a tool it runs. The repository and its main checkout need no entry. |

Global options come before the subcommand and win over the file: `--root <dir>`,
`--master <file>` and `--declarations <file>` (repeatable). A wrapper command passes them.

## Declarations

A declaration file holds `[keys.<NAME>]` tables, `[families."<PREFIX><PART><SUFFIX>"]` tables
and `[environments.<env>]` tables of one-line values. The key fields:

| Field | Meaning |
|---|---|
| `block` | The block header of the key in the master. Default `Other`. |
| `apps` | The apps that read the key. Absent: every app. |
| `secret` | `true` for a credential. `secret = false` marks a plain key: `get` shows a short literal of it, a push leaves it home unless `--all`, and the `github` store sets it as a variable. |
| `type`, `enum` | `string`, `int`, `bool`, `url`, `enum`, `json`, `float` (a decimal number with an optional exponent), `decimal` (a decimal number, no exponent) or `path` (any text). `set` checks a value against it. |
| `required` | `true`: `check` reports the key when a live file of its environments lacks it or leaves it empty, and `run` refuses to start without it. |
| `summary` | One line. `template --master` writes it as the comment above the key. |
| `default` | One line. `template --master` writes it as the master value. |
| `environments` | The environments whose env file carries the key. Absent: every environment. The environment of `.env.<env>` is `<env>`. |

`keys <app>`, `slice`, `where`, `check`, `template --master`, `push`, `pull`, `merge
--secrets-only` and `run` read them. With declarations, `set` writes only a declared key of the
right type, as one line, into a file that holds only declared keys. A family table declares
every key whose name fills the one `<PART>` placeholder, for example `API_KEY_<NAME>` for
`API_KEY_ALPHA`. It takes the key fields and `members`. The members and each environment key
of the family join the key list after the last key of the family's block. Any other key that
fits the family is declared too. Several declaration files merge in order, and a later file wins
for each field and each value. The engine reads no other field.

### The declarations command

The code readers find a key only where a code line reads the environment by name. A project
that keeps its keys in a schema module has no such line. `declarationsCommand` gives the engine
those keys:

```toml
[env]
declarationsCommand = "node tools/print-env-keys.mjs"
```

The command runs in a shell, in the repository root, and prints one key name per line. `check`
takes each name as a key that a declaration names, so it reports neither a code read of that
key nor that key in a live file. `where <KEY>` prints `declared and read by
env.declarationsCommand` for a name of the output, and `not listed by env.declarationsCommand`
for another key. No other subcommand runs the command.

The run fails with exit 1 and a message when the command exits with a code other than 0, when it
gives no answer in 60 seconds, or when a line of its output is not a key name (letters, digits
and `_`, no digit first). The engine skips an empty line. The message gives the exit code and
the first line of the error output of the command, or the number of the bad line, and never a
line of the output.

## Subcommands

| Subcommand | Behavior |
|---|---|
| `keys <file\|app>` | The keys of a file in file order, or the keys an app reads from the declarations. |
| `status [<file> ...] [--matrix]` | Per file: key count, empty keys, keys missing against its master, keys not in its master. The master of a file is its own `<file>.example` when `check` and `layout` take that template as its master, else the root master. `--matrix` prints one row per key, one column per live file with `set`, `empty` or `-`, and an APP column: the declared apps, `all`, or `-` for an undeclared key. |
| `check` | Each key that code reads and no master or declaration names, each key of a live file that none names, each empty or missing `required` key, each env file outside the master folder and `dirs`, and each live env file that git tracks. A key that `declarationsCommand` prints counts as named. Exit 1 on a finding. |
| `where <KEY>` | Each env file that sets the key, the declaration that reads it, each line of a scanned file that names it, and each code line that reads it (`path:line`, never the line text). With `declarationsCommand`, also whether that command lists the key. |
| `get <KEY> <file> [--reveal]` | The masked value (see Masks). `--reveal` prints the value, except the value of a public key that looks like a credential. |
| `set <KEY> <file> --value <v>` or `--stdin` | Sets the key in place, or adds it in its master block and order. An agent gives the value in a quoted heredoc: `set <KEY> <file> --stdin <<'EOF'`, the value, then `EOF`. The env guards clear that one form (docs/hooks.md, "The env engine exemption"). |
| `unset <KEY> <file>` | Removes the key. A block left with no key goes too. |
| `copy <KEY> <from> <to> [--as <NEW>]` | Copies one key. The entry keeps its lines as written. |
| `move <KEY> <from> <to>` | Copies one key, then removes it from `<from>`. |
| `rename <OLD> <NEW> [<file> ...]` | Renames the key in place in each file and in the master. No file: every env file. It refuses when `<NEW>` exists in a file that holds `<OLD>`. |
| `diff <a> <b>` | Keys only in a, only in b, and keys whose values differ, with the digest of each value. Exit 1 on a difference. |
| `merge <from> <to> [--secrets-only]` | Adds the keys of `<from>` that `<to>` lacks. A key in both stays as it is. `--secrets-only` adds only the keys declared `secret = true`, for a new environment file. |
| `slice <app> <file> [--to <dest>] [--environment <env>]` | The keys that `<app>` reads, in declaration order. `--to` writes them to `<dest>` in the master layout. `--environment` fills a key that the file lacks from the declared value of `<env>`. |
| `layout [DIR ...] [--fix [--dry-run]]` | Checks rules L1 to L7 on the env files of the master folder and of each `dirs` folder, or of each DIR against `DIR/.env.example`. `--fix` first reorders each live file to its master. `--dry-run` prints `would reorder <file>` and writes nothing. Exit 1 on a finding. |
| `template [<file> ...]` | Writes `<file>.example` from the master of `<file>`: its blocks and keys, every value empty. A file whose master is its own `<file>.example` keeps it. No file: every live file. |
| `template --master [--check]` | Writes the master and `.env.<env>.example` of each declared environment from the declarations. See Templates from declarations. |
| `init <file>` | Creates `<file>` from its master, every value empty. It refuses a file that exists. |
| `push <env> [options]` | Sends `.env.<env>` to the store, then writes `.env.<env>.pushed-<marker>`. See Push. |
| `pull <env> [--store <name>] [--apps a,b]` | Writes the store values into `.env.<env>`. A key only the file holds stays. `--apps` takes only the keys of those apps. |
| `diff <env> --store [<name>]` | Keys only in the file, only in the store, and keys that differ. Exit 1 on a difference. |
| `run <file> [--require <KEY> ...] -- <command>` | Starts one command with the keys of the file. See Run a command with an env file. |

Exit codes: 0 on success, 1 on an error or a difference, 2 on a wrong call.

## Masks

Every subcommand prints a value in one masked form. Only `get --reveal` prints it whole.

| Value | Prints as |
|---|---|
| empty | `empty` |
| a connection string of `postgres`, `postgresql`, `mysql`, `mariadb`, `redis`, `rediss`, `mongodb`, `mongodb+srv`, `amqp` or `amqps` | `scheme://***:***@host:port/database?<n> params` (`***:***@` only when it holds a userinfo) |
| `true`, `false` or a number of 1 to 6 digits, of a key declared `secret = false` | the value |
| anything else, any other URL included | `set, <n> chars, sha256:<8 hex>` |

The connection string form prints only when the parser reads the userinfo exactly as the text
between the scheme and the last `@`. A password with `/`, `?` or `#` makes a parser read part of
it as the host or the path, so such a value prints in the digest form. An http or https URL can
carry its token in the path or the query, so it always prints in the digest form.

`get --reveal` of a key that `publicKeys` names prints the value, unless the value looks like a
credential: a URL with a password, a PEM block (`-----BEGIN`), or a whole value of 32 or more
base64 or hex characters. Such a value prints in its masked form, with the reason after it.

## Code readers

`where` and `check` find the code lines that read a key, in the files of `readers` outside the
`skip` folders. In a git checkout they read only the files that git tracks or does not ignore,
so generated and vendored code that `.gitignore` names stays out. The stray file check reads
ignored files, because a live env file is ignored itself, but it leaves out a folder that
`.gitignore` ignores as a whole.

The readers find `process.env.X`, `process.env["X"]` and `import.meta.env.X` in JS and TS, and
`os.environ["X"]`, `os.environ.get("X")` and `os.getenv("X")` in Python. A pydantic
`BaseSettings` class, and a class of the same file built on one, gives one key per field: the
field name with `env_prefix` (from `SettingsConfigDict`, a dict or `class Config`), upper case,
or the field `alias`. A Python file with a settings class that does not parse stops the scan
with its name.

## Templates from declarations

`ah env template --master` writes the master from the declarations: a generated top block, then
each block header, each key with its `summary` as one comment line above it and its `default`
as the value. It also writes `.env.<env>.example` for each environment that a key's
`environments` field or an `[environments.<env>]` table names. That template holds the keys of
the environment, the value of `[environments.<env>]` or an empty one, and no comment line.
`--check` prints `stale: <file>` or `missing: <file>`, writes nothing and exits 1 when a
template differs from the declarations. A gate runs it.

## Push

`push <env> [--store <name>] [--apps a,b] [--all] [--dry-run] [--drop <KEY> ...] [--yes]
[--confirm <env>] [--create-environment]`

- It refuses, and sends nothing, when the file holds a key that its master does not name, or a
  declared secret that is empty or a placeholder (`env.placeholders`, by default `changeme` and
  `xxx`, in any case).
- With declarations, it sends only the secret keys: a key declared `secret = false` stays home
  unless `--all`. `--apps a,b` (or the store setting `apps`) sends only the keys of those apps. A
  whole-file store keeps each key outside the scope as it is.
- It lists the keys it would send, then asks on the terminal. With no terminal on standard
  input, it needs `--yes`. A push to an environment of `protected` needs `--yes --confirm <env>`
  on every run. The refusal names the exact command.
- `--dry-run` lists the keys and writes nothing.
- The marker `.env.<env>.pushed-<marker>` holds the sha256 of the sorted key and value pairs, so
  a reformat is no drift. The `env-drift` hook also accepts a marker of an older push, which
  holds the sha256 of the file bytes.

## Run a command with an env file

`ah env run <file> [--require <KEY> ...] -- <command> [args]` starts one command with the keys
of `<file>` in its environment. An exported value wins over the file. Each declared `required`
key of the file's environment, and each `--require` key, must be set and not empty, or nothing
runs. The file must be inside the repository.

The command gets the standard input of the engine. The engine reads the standard output and the
standard error of the command line by line. In each line it replaces with `***` every value of
the file that has 6 characters or more, each such line of a value that spans lines, and the
password of each URL value at any length. A longer value goes first. The engine then writes the
line to its own standard output or standard error, and it exits with the exit code of the
command. A line reaches the output only when the command ends it with a newline or exits.

The redaction covers the values as the file holds them. A command that encodes, splits or
reverses a value, or writes it to a file, gets past it. So the read guards let a run of any
program pass with no question on a plain project file, and ask for a held file or a file of a
protected environment. The rules are in `docs/hooks.md`, The env engine exemption.

## Files

- A relative path resolves against the repository root.
- From a linked worktree, a live env file that the worktree lacks and the main checkout holds
  resolves in the main checkout. A file that neither holds is created in the worktree. A
  template (`*.example`) is always the worktree's own.
- A file that ends in `.example` is a template. `set`, `copy`, `move`, `merge`, `unset` and
  `init` refuse to write a value into one. `rename` and `template` write templates.
- A live file is written beside itself and renamed over, with mode 0600. Before a write, the
  engine reads the new text back and refuses a text that would not give the expected values.
- A quoted value can span lines. A PEM key folds onto one line, with `\n` for each newline, and
  reads back whole. A value that needs quotes and holds both quote characters is refused.

## Layout

The layout rules:

| Rule | Says |
|---|---|
| L1 | A block opens with a `# Title` header of 1 to 3 words, with no rule characters and no colon. |
| L2 | One empty line between two blocks and nowhere else. The file ends in one newline. |
| L3 | A plain file holds header and key lines only. |
| L4 | A plain file uses the headers and keys of its master, in the master's order. |
| L5 | The master opens with a generated `#` block. |
| L6 | A key comment in the master sits directly above its key. |
| L7 | No comment on a key line. |

`.env.<n>.example` is the master of `.env.<n>` when the root master holds none of its keys, and
always for each file of `env.paired` (by default `.env.ci`). Without a master file, the declarations give
the blocks and the order.

## Stores

`[env.store]` names the one store. A `{env}` in a name is the environment.

```toml
[env.store]
kind = "aws-secrets-manager"
name = "app/{env}/env"
region = "eu-west-1"
```

`[[env.stores]]` names several stores. `push`, `pull` and `diff <env> --store` take
`--store <name>`. With one store no name is needed, and with several the engine asks for one. In
a named entry, `name` names the store and is its marker. A kind that `[env.store]` reads `name`
for reads `secret` there.

```toml
[[env.stores]]
name = "backend"
kind = "aws-secrets-manager"
secret = "app/{env}/backend"

[[env.stores]]
name = "fleet"
kind = "ssh-file"
host = "deploy@fleet.example"
path = "/srv/fleet/.env.{env}"
apps = ["worker"]
```

| Kind | Holds | Keys | CLI |
|---|---|---|---|
| `aws-secrets-manager` | one JSON object per environment | `name`, `region`, `profile` | `aws` |
| `aws-ssm` | one SecureString parameter per key | `prefix`, `region`, `profile` | `aws` |
| `gcp-secret-manager` | one secret per environment, the env file text | `name`, `project` | `gcloud` |
| `github` | one Actions secret per key, or a variable for a key declared `secret = false`; push only | `repo`, `environment` | `gh` |
| `infisical` | one secret per key | `projectId`, `envName` (default `{env}`), `secretPath` | `infisical` |
| `file` | a copy of the env file | `path` | none |
| `digitalocean-app` | the app-level envs of one App Platform app: SECRET, or GENERAL for a plain key; push only, because the API returns a SECRET value encrypted | `app` | `doctl` |
| `ssh-file` | one env file on a host, backed up to `<path>.<unix time>` before a write | `host`, `path` | `ssh` |
| `command` | what two commands of the project write and print; push only without `pull`. See The command store. | `push`, `pull`, `whole` | the program of each list |

Every kind also reads `file` (the live file, default `.env.{env}`), `marker` (default `store`)
and `apps`. The `env-drift` hook compares the marker `.env.<env>.pushed-<marker>` with the live
file, for each store of `harness.envDrift.stores` and of `[[env.stores]]`.

The `digitalocean-app` store reads the app spec with `doctl apps spec get`, sets each key, and
writes it back with `doctl apps update --spec` from a 0600 temp file. The `ssh-file` store runs
`ssh -o BatchMode=yes`. Its write makes a `mktemp` file in the target folder under umask 077,
writes the text from standard input, sets mode 600 and moves it over the path. The backup goes
through a `mktemp` file and `mv` too, so a link on the host is never written through.

A missing CLI fails with its install link. A value never goes into an argument list: the engine
passes it on standard input (`gcloud`, `gh`), or in a 0600 temp file that the CLI reads by path
and that the engine removes after the call (`aws ... file://`, `infisical secrets set --file`).

With `environment` set, the `github` store first checks that the GitHub Environment exists. A
missing one stops the push: create it in GitHub (Settings > Environments) with its protection
rules, then push again. `push <env> --create-environment` creates it instead, with no
protection rule. The engine refuses that flag for an environment of `[env] protected`, because
a person makes a protected environment with its rules.

`aws-secrets-manager`, `gcp-secret-manager` and `file` replace the whole secret on push. A push
to one of them refuses when the store holds a key that the file lacks. Pass `--drop <KEY>` for
each key that nothing reads any more. A `command` store with `whole = true` follows the same rule.

## The command store

The `command` kind is for a store that no other kind fits. The project names one push command
and, when the store can give its values back, one pull command. The engine runs them, and a push
and a pull keep every check of the engine.

```toml
[env.store]
kind = "command"
push = ["node", "scripts/env-store.mjs", "push", "{env}"]
pull = ["node", "scripts/env-store.mjs", "pull", "{env}"]
whole = false
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `push` | list of strings, 1 or more | required | The program and the arguments of a push. |
| `pull` | list of strings, 1 or more | none | The program and the arguments of a pull. Without it the store is push only. |
| `whole` | boolean | `false` | `true` when a push replaces every key of the store. The engine then sends the kept keys too, and applies the `--drop` rule. |

The engine starts the program with no shell, in the repository root. A `{env}` in an element is
the environment name. The first element resolves on `PATH`, or against the repository root when
it holds a `/`. A program that does not exist is an error that names the element. A wrong type,
an empty list or an empty first element is an error that names the key.

A store with no `pull` refuses `pull` and `diff <env> --store`, and a push to it lists every key
of the scope.

### Push

The engine gives the push command one JSON object on standard input. No value is in an argument.

```json
{"version": 1, "env": "production", "values": {"API_KEY": "..."}, "plain": ["PUBLIC_URL"], "current": null}
```

| Field | Holds |
|---|---|
| `version` | `1`. |
| `env` | The environment name. |
| `values` | The keys and values to write. For a `whole` store, every key that the store holds after the push. |
| `plain` | The keys of `values` that the declarations mark `secret = false`, sorted. |
| `current` | What the pull command returned before this push, or `null` when the store is push only or holds nothing. |

| Exit code | Meaning |
|---|---|
| 0 | The store holds the values. The engine writes the marker. |
| any other | The push failed. The engine writes no marker. |

The engine ignores the standard output of a push.

### Pull

The engine gives the pull command `{"version": 1, "env": "<env>"}` on standard input.

| Result | Meaning |
|---|---|
| Exit 0 and one JSON object of string keys and string values on standard output | The values of the store. |
| Exit 3 | The store holds nothing for this environment. |
| Any other exit code | A failure. |
| Exit 0 and output that is no such object | A failure. The message names the fault and no content of the output. |

### Failure text

A failed command gives `the command store failed to <push|pull> for <env> (exit N): <lines>`. The
lines are the first three lines of the error output that are not empty. The engine first
replaces each value it knows with `***`, with the redaction of `ah env run`. It knows the values
of the push, the values of `current` and the values of the live env file.

The redaction knows a value only as the env file holds it, and only a value of 6 characters or
more. A command must print no value, and no encoded form of one.

### Example

```js
// scripts/env-store.mjs
import { readFileSync } from "node:fs";
import { loadStore, saveStore } from "./store-client.mjs";

const [action, env] = process.argv.slice(2);
const input = JSON.parse(readFileSync(0, "utf8"));

if (action === "pull") {
  const values = await loadStore(env);
  if (values === null) process.exit(3);
  process.stdout.write(JSON.stringify(values));
} else {
  await saveStore(env, input.values, { plain: input.plain });
}
```

`loadStore` and `saveStore` stand for the client of the project store. An error that the
command does not catch ends it with exit code 1, which fails the push or the pull.
