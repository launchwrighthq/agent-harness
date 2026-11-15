# deploy-status

`ah deploy-status` prints the verdict of the last deploy of each environment, its phases with a
duration each, and what each serving target runs now. `--history` prints a table of recent runs.
The command only reads. It changes nothing on GitHub, on a node or on disk.

```
ah deploy-status [--env <env>] [--steps] [--history [<n>h|<n>d]] [--json]
```

| Option | Does |
|---|---|
| `--env <env>` | Prints one environment of `deploy.environments`. |
| `--steps` | Opens each phase: a phase of many jobs into its jobs, a phase of one job into its steps. |
| `--history [window]` | Prints the runs of a window instead of the last deploy. The default is `24h`. A bare number is hours. |
| `--json` | Prints one JSON document with the same content. |

## The two halves

The CI half first reads the repository with `gh api repos/<owner>/<name>`. A 404 there means
that the active `gh` account cannot see the repository, and the command says so and names
`gh auth status` and `gh auth switch`. A workflow error would blame the wrong thing.

The CI half reads the runs of the deploy workflows through `gh`. It folds the jobs of a called
workflow and of a matrix onto the job key of the workflow file. It maps each job key to a phase
with `deploy.phases`. The serving half runs the read method of each target of
`deploy.targets`.

The current view walks back from the newest run until it holds two answers. The first is the
newest run that moved something. The second is, for each serving phase, the newest run where
that phase succeeded. It reads 20 runs at most.

## The verdict

A green run alone never reads as deployed. The verdict of one run comes in this order:

1. A phase that failed or was cancelled gives `failed at <phase>` or `cancelled at <phase>`.
2. A run in flight gives `running at <phase>`, or `queued`.
3. A failed run with no job gives `failed, no job says where`.
4. An empty `phases.moves` gives `finished, no phase map (set deploy.phases.moves)`.
5. No moving phase that ran gives `nothing to deploy`. A no-op phase that succeeded does not
   count.
6. A serving phase that succeeded gives `deployed`. With `phases.deployed` set, only those
   phases give `deployed`, and another serving phase gives `served <surfaces> only`.
7. Else the run gives `moved <surfaces>, no serving phase succeeded`, or `moved <surfaces>`
   without serving phases.

When the last deploy reads `deployed` and a serving target reports a problem, a warning says so.
What the target runs wins. When a serving job failed in a run newer than its `SERVING` row, a
caution line names those runs: a job can fail after its rollout went live.

## Config

Section `[deploy]` of `.agents/harness.toml`. Every key has a default.

| Key | Default | Meaning |
|---|---|---|
| `ci.provider` | `github` | The CI provider. Only `github` (through `gh`) exists. |
| `ci.workflows` | `["deploy.yml"]` | The deploy workflow files. An empty list leaves out the CI half. When the config does not set the key and the repository has no `deploy.yml`, the command prints `no deploy workflow configured` and exits 0. A missing workflow that the config names fails. |
| `ci.jobPrefix` | `""` | The prefix of the jobs of a called workflow, for example `deploy / `. |
| `ci.repo` | `""` | The repository as `<owner>/<name>`. Every `gh` call names it, so the command runs outside a checkout of the repository. Empty means the repository of the checkout. |
| `environments.<env>.match` | none | A table, or a list of tables where any one may hold. A table holds when each of its keys holds: `branch` (a name or a list), `runName` (a glob on the run name), `workflow` (a workflow file), `since` (the earliest creation time), `event` (the trigger, a name or a list, for example `workflow_dispatch`). With no environment, one view covers every run. |
| `phases.moves` | `{}` | Job key to the surface it changes, for example `build = "image"`. |
| `phases.serving` | `{}` | Job key to the surface that serves after the job succeeds. |
| `phases.noop` | `[]` | Job keys whose success can change nothing. |
| `phases.deployed` | `[]` | The serving job keys whose success reads as `deployed`. Empty means every serving job. A run where only another serving job succeeded reads `served <surfaces> only`. |
| `version` | `git-count` | The version column: `git-count` (`git rev-list --count <sha>`), `digest` (the digest of a record) or `command`. |
| `versionCommand` | `[]` | With `version = "command"`, the command that prints the version. It gets the commit as its last argument. A lookup that fails reads `-`, and one line under the table counts the failures and names the first. |
| `records` | `""` | A folder of workstation deploy records, JSON or JSON lines. It is read in this checkout and in the main checkout. An absolute path names one folder. |
| `recordDigest` | `digest` | The dotted key of the digest in a record, for example `digests.origin`. |
| `recordVersion` | `""` | The dotted key of a version in a record, for example `buildNumber`. It fills in the version of a record whose commit gives none, for example a commit that this checkout does not hold. Empty reads none. |
| `targets.<name>` | none | One serving target. See below. |
| `logPhases.<job>` | none | Banner patterns that split the log of a one-job phase into phases under `--steps`, as a list or as a table. See below. |
| `logSummary.<job>` | none | The pattern of the line that sums up the log of a one-job phase, for example `'^== rollout: (.*)$'`. The current view prints its first group under the run header as `<job>: <text>`. It reads the log without `--steps` too. |

### Log phases

`logPhases.<job>` is a list of banner patterns, or a table with these keys:

| Key | Meaning |
|---|---|
| `banners` | The patterns of a line that starts a phase. |
| `final` | The pattern of the line that the script prints last. It closes the last phase. |
| `fatal` | The pattern of a line that says why the script stopped. It counts only when no final line came. |
| `aside` | The pattern of a line that gets a row with no duration, for example a job sent to the background. |

A banner names its phase by the group `name`, else by its first group, else by the whole match.
A group `seconds` gives the duration, for a banner that a check prints when it ends. A group
`status` gives the status: `passed` reads as `success`, a word that starts with `fail` reads as
`failure`. Without these groups, a phase ends at the next banner, the final line or the end of
its step, whichever comes first.

A phase prints under the step whose clock holds its banner. With `final` or `fatal` set, every
phase but the last reads `success`. The last reads `success` after a final line, `failure` after
a fatal line, and `incomplete` without either. A `died: <text>` row then names the fatal line.
With neither key, every phase reads `logged`.

```toml
[deploy.logPhases.rollout]
banners = ['^== (step \d/2: .*)$']
final = '^== rollout: '
fatal = '^xx (.*)$'
aside = '^== (note: .*)$'

[deploy.logPhases]
verify = ['^===== (?P<name>[a-z:]+): (?P<status>passed|FAILED).* in (?P<seconds>[\d.]+) s =====$']
```

### Records

A record file holds one record (`.json`) or one record per line (`.jsonl`).

| Shape | Fields | Verdict |
|---|---|---|
| Job record | `environment`, `sha`, `branch`, `startedAt`, `finishedAt`, `jobs` (job to `{ result, seconds }`) | From the phase map, as for a run. |
| Release record | `id`, `image`, `digest`, `at`, `status` (`applied` or `stopped`), `rollback` | `deployed` for `applied`, else `failed`. |

A record without `environment` belongs to the environment that its file stem names.

### Targets

| `read` | Keys | Reads |
|---|---|---|
| `lw` | `target` (default: the name), `lw` (default: `lw`, for example `npx lw`) | `lw deploy status <target> --json`: the nodes, the release log line and the problems. |
| `ssh-docker` | `nodes`, or `nodesCommand` (a command that prints one host per line), `roles` (role to container name prefix), `optionalRoles` (roles that a node may lack), `user`, `ssh` | `docker ps` and `docker image inspect` on each node over SSH. |
| `http` | `url`, `field` (a dotted key of a JSON body) or `header` (a response header that holds the live version) | One GET with the agent `agent-harness-deploy-status`. |
| `command` | `command` | A command that prints JSON lines with `node`, `role`, `version`, `digest`, `state` and `problem`. |

Each target has `env`, the environment that it serves. The default is the target name. A target
whose `env` names no environment prints under every environment.

## Example

A project whose deploy workflow runs the jobs `gate`, `build`, `migrate`, `apply` and `verify`,
and whose nodes answer `lw deploy status`:

```toml
[deploy]
records = "deploy/releases"

[deploy.ci]
workflows = ["deploy.yml"]

[deploy.environments.production]
match = { runName = "deploy production" }

[deploy.phases]
noop = ["migrate"]

[deploy.phases.moves]
build = "image"
migrate = "database"
apply = "nodes"

[deploy.phases.serving]
apply = "nodes"

[deploy.targets.production]
read = "lw"
lw = "npx lw"
```

A project with a called workflow, a branch rule and Kamal nodes:

```toml
[deploy.ci]
workflows = ["production.yml"]
jobPrefix = "deploy / "

[deploy.environments.production]
match = [{ branch = "env/production" }, { branch = "main", since = "2026-01-01T00:00:00Z" }]

[deploy.phases.moves]
images = "image"
rollout = "fleet"

[deploy.phases.serving]
rollout = "fleet"

[deploy.targets.boxes]
read = "ssh-docker"
env = "production"
nodes = ["box-1", "box-2"]
user = "deploy"
roles = { web = "app-web-", worker = "app-worker-" }
```

## Read-only

The command runs `gh run list`, `gh api` GETs (the repository and the jobs of a run), `gh run view --log` (only for `logPhases`),
`gh auth status`, `git cat-file` and `git rev-list`, and the read method of each target. A
`command` target and `versionCommand` run what the project configures, so keep them read-only.
