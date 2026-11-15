# The env engine

`ah env <subcommand>` reads and changes `.env*` files and moves them to and from a secret store.
It works in any git repository. No subcommand prints a value, except `get --reveal`.

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
| `layout` | `true` | `false`: a new key goes at the end of a file, and no master order applies. |
| `dirs` | `[]` | More folders of env files, each with its own master `<dir>/.env.example`. `layout` with no DIR checks them, and `check` accepts env files in them. |
| `scan` | `.github/workflows`, `infra`, `deploy`, `terraform` and the compose files | The files and folders that `where` searches for a key name. |
| `readers` | `*.py`, `*.js`, `*.jsx`, `*.mjs`, `*.cjs`, `*.ts`, `*.tsx`, `*.mts`, `*.cts`, `*.vue`, `*.svelte` | The globs of the code files whose env reads `where` and `check` find. See Code readers. |
| `skip` | `node_modules`, `.venv`, `venv`, `dist`, `build`, `.claude`, `__pycache__` and build caches | Folders that the reader scan and the stray file check leave out. An entry without a `/` is a folder name at any depth. An entry with a `/` is a path from the root. A value replaces the default list, so keep the defaults you need: `skip = ["node_modules", ".venv", "third_party", "app/vendor"]` leaves out vendored code. |
| `ignore` | `CI`, `HOME`, `LANG`, `NODE_ENV`, `PATH`, `PWD`, `SHELL`, `TERM`, `TMPDIR`, `TZ` | Keys that code reads and that `check` does not ask to declare. |
| `store` | `{ kind = "" }` | The one secret store. See Stores. |
| `stores` | `[]` | Named secret stores. See Stores. |
| `protected` | `[]` | Environments whose push needs `--yes --confirm <env>`, whose GitHub Environment a push never creates, and whose `run` always asks. |
| `runCommands` | `[]` | Bare program names that `ah env run` may start with no question from the read guards. |

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
| `type`, `enum` | `string`, `int`, `bool`, `url`, `enum` or `json`. `set` checks a value against it. |
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

## Subcommands

| Subcommand | Behavior |
|---|---|
| `keys <file\|app>` | The keys of a file in file order, or the keys an app reads from the declarations. |
| `status [<file> ...] [--matrix]` | Per file: key count, empty keys, keys missing against its master, keys not in its master. The master of a file is its own `<file>.example` when `check` and `layout` take that template as its master, else the root master. `--matrix` prints one row per key, one column per live file with `set`, `empty` or `-`, and an APP column: the declared apps, `all`, or `-` for an undeclared key. |
| `check` | Each key that code reads and no master or declaration names, each key of a live file that none names, each empty or missing `required` key, each env file outside the master folder and `dirs`, and each live env file that git tracks. Exit 1 on a finding. |
| `where <KEY>` | Each env file that sets the key, the declaration that reads it, each line of a scanned file that names it, and each code line that reads it (`path:line`, never the line text). |
| `get <KEY> <file> [--reveal]` | The masked value (see Masks). `--reveal` prints the value. |
| `set <KEY> <file> --value <v>` or `--stdin` | Sets the key in place, or adds it in its master block and order. |
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
  declared secret that is empty or a placeholder (`tobemodified`, `changeme`, `xxx`).
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
runs. The file must be inside the repository. The engine prints no value, and the exit code is
the command's.

The command can print what it gets, so the read guards let a run pass with no question only for
a bare program of `[env] runCommands` (default empty) on a plain project file, with plain
arguments. A shell, an interpreter, a wrapper, a guarded file or a protected environment asks
the user. The rules are in `docs/hooks.md`, The env engine exemption.

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
always for `.env.ci`, `.env.seed` and `.env.exp`. Without a master file, the declarations give
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
each key that nothing reads any more.
