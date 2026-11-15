---
name: lw-deploy-status
description: Use when the question is what our deploys did or what is deployed now. Is production deployed, which commit or release is live, did the last deploy finish, did a green CI run deploy anything, why did the deploy fail, how long did it take, why was it slow, which node runs an old digest, is a node unhealthy, show the deploy history. Runs `ah deploy-status`, which reads the CI runs through `gh`, the workstation deploy records and each serving target, and prints a verdict, the phases with a duration each, and what each target serves. Read-only. Not how to deploy to one target.
---

# What the deploys did, and what is deployed now

A green run does not mean that anything deployed. A red run does not mean that nothing did.
Read the verdict and the serving targets. Never read the conclusion of a run alone.

## Run it

```bash
python3 .claude/agent-harness/bin/ah deploy-status                  # each environment, the last deploy
python3 .claude/agent-harness/bin/ah deploy-status --env production # one environment
python3 .claude/agent-harness/bin/ah deploy-status --steps          # open each phase into jobs or steps
python3 .claude/agent-harness/bin/ah deploy-status --history        # the runs of the last 24 hours
python3 .claude/agent-harness/bin/ah deploy-status --history 7d     # a wider window: <n>h, <n>d or hours
python3 .claude/agent-harness/bin/ah deploy-status --json           # one JSON document
```

With only a global install, run `python3 ~/.claude/agent-harness/bin/ah deploy-status`. A
global engine hands over to the project engine when the repository has one.

Every call is a read: `gh run list`, `gh api` GETs, `git` object lookups, and the read method of
each target. Nothing deploys, cancels or re-runs. So this needs none of the care that a rollout
needs.

## Where the facts come from

The `[deploy]` section of `.agents/harness.toml` holds every fact. Print one value with
`python3 .claude/agent-harness/bin/ah config get <key>`.

| Key | Holds |
|---|---|
| `deploy.ci.workflows` | The deploy workflow files. |
| `deploy.ci.jobPrefix` | The prefix of the jobs of a called workflow, for example `deploy / `. |
| `deploy.ci.repo` | The repository as `<owner>/<name>`. Empty means the repository of the checkout. |
| `deploy.environments` | How a run maps to an environment: `branch`, `runName`, `workflow`, `since`, `event`. |
| `deploy.phases.moves` | Each job and the surface that it changes. |
| `deploy.phases.serving` | Each job whose success means that its surface serves the run. |
| `deploy.phases.noop` | Each job whose success can change nothing. |
| `deploy.version` | `git-count`, `digest` or `command`: the version column. |
| `deploy.records` | The folder of workstation deploy records. |
| `deploy.targets` | Each serving target and its read method: `lw`, `ssh-docker`, `http` or `command`. |

An empty `deploy.phases.moves` gives the verdict `finished, no phase map`. Say that the config
lacks the phase map. Do not guess which jobs deploy.

## Procedure

1. Run `ah deploy-status`. Read the first lines of each environment. A line `N newer run(s)
   moved nothing` counts the runs that the view stepped over, and says why. A line
   `run <id> queued for <age>` names a run that has not finished. A run queued for hours is
   stuck: say so.
2. Read the verdict line under the run header. The table below gives each verdict.
3. Read the `SERVING` table. Each row names the newest run where that serving phase succeeded.
   Two rows can name two runs. A push that touches one surface leaves the others on older runs.
   A `caution:` line names newer runs where that serving job failed. A job can fail after its
   rollout went live, so read the targets before you say the older run serves.
4. Read the serving targets at the end. They say what runs now. When a target and a run
   disagree, the target wins.
5. For a slow deploy, run with `--steps`. A phase of many jobs opens into its jobs. A phase of
   one job opens into its steps.
6. For a trend, run with `--history`. Each row is one run or one record.

## Verdicts

| Verdict | Meaning |
|---|---|
| `deployed` | A serving phase succeeded. Its surface served the image of this run when the job ended. |
| `nothing to deploy` | Green, and every phase that moves something skipped, or only a no-op phase ran. |
| `failed at <phase>` | That phase failed. The phases after it did not run. |
| `cancelled at <phase>` | Someone or a timeout cancelled the run in that phase. |
| `running at <phase>` | The run is in flight in that phase. |
| `moved <surfaces>, no serving phase succeeded` | Something moved, but no surface serves it yet. |
| `served <surfaces> only` | A serving phase succeeded, but none of `deploy.phases.deployed`. The main surface did not move. |
| `failed, no job says where` | The run failed with no job, for example a cancel before the first job. |
| `deployed (rollback to <id>)` | A workstation record of a rollback. |

## Serving targets

| Read method | What it reads |
|---|---|
| `lw` | `lw deploy status <target> --json`: the running containers of each node, and the release log. |
| `ssh-docker` | `docker ps` on each node over SSH, with a role per container name prefix. |
| `http` | A version URL. `field` picks a key of a JSON body, `header` a response header. |
| `command` | A project command that prints one JSON object per line. |

A `problem:` line names a node that did not answer, an unhealthy node, a missing role, or two
versions of one role. A warning under the phases says that the run says `deployed` while a
target reports a problem.

### The `lw` read method

`lw deploy status` runs `docker ps` and `docker inspect` on each node over SSH, or reads the ECS
task list for an `aws` target. It changes nothing.

1. Read the `release log:` line. It names the last applied release in
   `deploy/releases/<target>.jsonl`.
2. Read each node row: the health, the release id that the node reports, and the image digest
   that its app containers run. The containers are the truth. A release log line or a green CI
   run says only that someone started a deploy.
3. Read the `problem:` lines:
   - a node is not healthy, or SSH to it failed (the reason follows);
   - the nodes run more than one digest: a rollout stopped part way;
   - the nodes run another digest than the last applied release: someone deployed outside
     `lw deploy`, or a rollback ran.
4. For a rollout that stopped, read the output of the last `lw deploy apply`, or the release
   log. The rollout stops at the first unhealthy node and puts that node back. The nodes after
   it keep the release that they ran. A digest mismatch stops the rollout before any node
   changes.
5. To go back, the user runs `lw deploy rollback <target>` (the release before the current one)
   or `lw deploy rollback <target> --to <release id>`. Name the command. Do not run it unless the
   user asks for the rollback.

## Traps

- **A skipped phase is no deploy.** A push that touches no deploy input skips every phase that
  moves something. The run is green and no surface moved. The current view steps over it.
- **A green no-op phase is no evidence.** A migrate job can exit 0 with no database to change.
  `deploy.phases.noop` names such jobs, and the surface line marks them `possible no-op`.
- **A phase of many jobs shows wall clock.** A matrix runs side by side. A sum of the job
  durations claims time that nobody waited for.
- **The total of a run includes its queue.** The header says `queued <duration>` for a wait over
  30 seconds, and `attempt <n>` for a re-run.
- **A commit that this checkout does not hold has no version.** It reads `-`. `git fetch` fills
  it in.
- **An empty run list stops the command when `gh` holds no token.** It never reads as "nothing
  deployed". Run `gh auth login`, then run the command again.
- **A repository that the active `gh` account cannot see stops the command.** The message says
  so. Check the gh identity of the project with `gh auth status`, and `gh auth switch` to the
  account that owns it. Do not edit the workflow config for this error.
- **No deploy workflow is a fact, not a failure.** With the default config and no `deploy.yml`,
  the command prints `no deploy workflow configured` and exits 0. Report that line.

## What the command does not know

- A run and a record are history. Only the serving targets say what runs now.
- A node that drifted after a green run still reads as deployed in the CI half. The serving half
  shows it.
- A record is per machine. A workstation deploy from another machine does not show here.
- The run list holds the newest 200 runs of each workflow. The history footer says when a window
  reaches past them.

## Never

- Never say "deployed" from a CI run or a release log line alone. Say what the targets run.
- Never print or copy an env value. The release `.env` on a node has mode 0600 for a reason.
- Never run a command that changes a target to answer a status question.

## Product facts

Free-text facts about one project sit in its adapter folder,
`<adapterDir>/deploy-status/targets/<target>.md`, one file per target.
`python3 .claude/agent-harness/bin/ah config get harness.adapterDir` prints the folder. A file holds the expected nodes, the services of each
node and the known gaps. With no file, no product facts are known. Say that, and do not invent
them.
