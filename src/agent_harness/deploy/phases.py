"""Jobs folded into phases, and the verdict of one run.

A called workflow names its jobs `<caller> / <job>`, and a matrix names them `<job> (<values>)`.
Both fold back onto the job key of the workflow file, which is the name a person asks about.
`phases.moves`, `phases.serving` and `phases.noop` map those keys to what they change.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Any

from agent_harness import HarnessError
from agent_harness.deploy.github import Job, Run, Step, moment

# Conclusions that stop a run. GitHub reports a job past its timeout as `timed_out`.
FAILED = frozenset({"failure", "timed_out", "startup_failure"})
WAITING = frozenset({"", "pending", "queued", "waiting", "requested"})

# Seconds that a step without a `name:` must take under --steps before it gets a row.
STEP_FLOOR = 10

# The runner's own steps. They explain none of a deploy's duration.
RUNNER_STEPS = frozenset({"Set up job", "Set up runner", "Complete runner", "Complete job"})


@dataclass(frozen=True)
class PhaseMap:
    """The phase keys of `[deploy]`: what each job moves, what serves after it, what can no-op."""

    moves: Mapping[str, str]
    serving: Mapping[str, str]
    noop: frozenset[str]
    prefix: str = ""
    # The serving jobs whose success reads as deployed. Empty means every serving job.
    deployed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Row:
    """One row of a phase table. `seconds` is None where no duration can be measured."""

    name: str
    status: str
    seconds: int | None
    indent: int = 0


def phase_map(section: Mapping[str, Any]) -> PhaseMap:
    """The phase map of a `[deploy]` section, with each value checked."""
    phases = _table(section.get("phases"), "deploy.phases")
    ci = _table(section.get("ci"), "deploy.ci")
    serving = _string_table(phases.get("serving"), "deploy.phases.serving")
    deployed = frozenset(_strings(phases.get("deployed"), "deploy.phases.deployed"))
    for name in sorted(deployed - set(serving)):
        raise HarnessError(f"deploy.phases.deployed names {name}, which phases.serving lacks")
    return PhaseMap(
        moves=_string_table(phases.get("moves"), "deploy.phases.moves"),
        serving=serving,
        noop=frozenset(_strings(phases.get("noop"), "deploy.phases.noop")),
        prefix=_string(ci.get("jobPrefix", ""), "deploy.ci.jobPrefix"),
        deployed=deployed,
    )


def group_of(name: str, prefix: str = "") -> str:
    """The job key that a reported job name belongs to."""
    tail = name[len(prefix) :] if prefix and name.startswith(prefix) else name
    return tail.split(" / ", 1)[0].split(" (", 1)[0].strip()


def grouped(jobs: Sequence[Job], prefix: str = "") -> list[tuple[str, list[Job]]]:
    """The job groups in the order they started, so a table reads as the pipeline ran."""
    buckets: dict[str, list[Job]] = {}
    for job in jobs:
        buckets.setdefault(group_of(job.name, prefix), []).append(job)

    def first_start(item: tuple[str, list[Job]]) -> str:
        starts = sorted(j.started.isoformat() for j in item[1] if j.started is not None)
        return starts[0] if starts else "~"

    return sorted(buckets.items(), key=first_start)


def group_status(jobs: Sequence[Job]) -> str:
    """One word for a set of jobs that ran together.

    `skipped` reads as neither success nor failure. A deploy job is skipped when the push touched
    nothing it deploys, and calling that a success would name a commit that never shipped.
    """
    outcomes = {job.conclusion for job in jobs}
    if outcomes & FAILED:
        return "failure"
    if "cancelled" in outcomes:
        return "cancelled"
    if any(job.status != "completed" for job in jobs):
        return "running"
    if outcomes == {"skipped"}:
        return "skipped"
    if outcomes == {"success"}:
        return "success"
    return " ".join(sorted(o for o in outcomes if o)) or "unknown"


def group_span(jobs: Sequence[Job], now: datetime) -> int | None:
    """Wall clock across a group, earliest start to latest finish, over the jobs that ran.

    The jobs of a matrix run side by side, so a sum claims time nobody waited for. A skipped job
    can report a finish before its start, so it stays out. A workstation record gives seconds per
    job and no clock, and its jobs ran one after another, so there the sum is the wall clock.
    """
    ran = [job for job in jobs if job.conclusion != "skipped"]
    if not ran:
        return None
    starts = [job.started for job in ran if job.started is not None]
    if not starts:
        seconds = [job.seconds for job in ran]
        return sum(s for s in seconds if s is not None) if None not in seconds else None
    ends = [job.completed for job in ran]
    last = now if any(end is None for end in ends) else max(e for e in ends if e is not None)
    return between(min(starts), last)


def between(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds())


def statuses(jobs: Sequence[Job], pm: PhaseMap) -> dict[str, str]:
    return {name: group_status(group) for name, group in grouped(jobs, pm.prefix)}


def moved(jobs: Sequence[Job], pm: PhaseMap) -> bool:
    """Whether a phase that moves something ran. A no-op phase that succeeded is no evidence."""
    for name, status in statuses(jobs, pm).items():
        if name not in pm.moves or status == "skipped":
            continue
        if name in pm.noop and status == "success":
            continue
        return True
    return False


def surfaces(jobs: Sequence[Job], pm: PhaseMap) -> list[str]:
    """Each surface that a phase of this run moved, in the order of `phases.moves`."""
    found = statuses(jobs, pm)
    out: list[str] = []
    for job, surface in pm.moves.items():
        if found.get(job) != "success":
            continue
        label = f"{surface} (possible no-op)" if job in pm.noop else surface
        if label not in out:
            out.append(label)
    return out


def verdict(run: Run, jobs: Sequence[Job], pm: PhaseMap) -> str:
    """What one run did, in one phrase.

    A green run alone never reads as deployed. `deployed` needs a phase of `phases.deployed`, or
    of `phases.serving` when that list is empty, that succeeded. A run where only another serving
    phase succeeded reads `served <surfaces> only`. `nothing to deploy` is a green run where
    every phase that moves something skipped.
    A run with no jobs falls back on its own conclusion, so a run cancelled early never reads as
    a quiet push.
    """
    groups = grouped(jobs, pm.prefix)
    for name, group in groups:
        status = group_status(group)
        if status == "failure":
            return f"failed at {name}"
        if status == "cancelled":
            return f"cancelled at {name}"
    if run.status != "completed":
        for name, group in groups:
            if group_status(group) == "running":
                return f"running at {name}"
        return "queued"
    if run.conclusion in FAILED or run.conclusion == "cancelled":
        word = "cancelled" if run.conclusion == "cancelled" else "failed"
        return f"{word}, no job says where"
    if not pm.moves:
        return "finished, no phase map (set deploy.phases.moves)"
    if not moved(jobs, pm):
        return "nothing to deploy"
    found = statuses(jobs, pm)
    deciding = pm.deployed or frozenset(pm.serving)
    if any(found.get(job) == "success" for job in deciding):
        return "deployed"
    served = [s for job, s in pm.serving.items() if found.get(job) == "success"]
    if served:
        return f"served {', '.join(dict.fromkeys(served))} only"
    what = ", ".join(surfaces(jobs, pm)) or "nothing finished"
    if pm.serving:
        return f"moved {what}, no serving phase succeeded"
    return f"moved {what}"


def step_name(name: str) -> str:
    """A step name a person can scan: `Run <owner>/<action>@<ref>` becomes `<action>`.

    A step with no `name:` arrives as its shell body, which is cut to its first line that is not
    blank and not a comment.
    """
    action = re.match(r"^Run\s+[\w.-]+/([\w.-]+)@\S+$", name)
    if action:
        return action.group(1)
    if not name.startswith("Run "):
        return name
    lines = [line.strip() for line in name[4:].splitlines()]
    body = [line for line in lines if line and not line.startswith("#")]
    return body[0] if body else name[4:]


def step_rows(job: Job) -> list[Row]:
    return [row for _, row in step_entries(job)]


def step_entries(job: Job) -> list[tuple[Step, Row]]:
    """The steps of one job worth a row, each with its row: no runner step, no `Post ` teardown.

    A step that the workflow named always gets a row. A step with no name gets one only when it
    takes `STEP_FLOOR` seconds or more.
    """
    rows: list[tuple[Step, Row]] = []
    for step in job.steps:
        if step.name in RUNNER_STEPS or step.name.startswith("Post "):
            continue
        seconds = between(step.started, step.completed)
        if step.name.startswith("Run ") and (seconds or 0) < STEP_FLOOR:
            continue
        status = step.conclusion or step.status
        if status in WAITING:
            continue
        rows.append((step, Row(step_name(step.name), status, seconds, 2)))
    return rows


def open_group(jobs: Sequence[Job], now: datetime) -> list[Row]:
    """A group broken open: a fanned-out group into its jobs, a one-job group into its steps."""
    if len(jobs) == 1:
        return step_rows(jobs[0])
    ran = [job for job in jobs if job.conclusion != "skipped"]
    ran.sort(key=lambda job: job.started.isoformat() if job.started else "~")
    return [
        Row(job.name, job.conclusion or job.status, between(job.started, job.completed or now), 2)
        for job in ran
    ]


@dataclass(frozen=True)
class Environment:
    """One environment of `[deploy] environments` and the rules that map a run to it."""

    name: str
    rules: tuple[Mapping[str, Any], ...]


MATCH_KEYS = frozenset({"branch", "runName", "workflow", "since", "event"})


def environments(section: Mapping[str, Any]) -> list[Environment]:
    """The environments of a `[deploy]` section. None configured gives one over every run."""
    table = _table(section.get("environments"), "deploy.environments")
    if not table:
        return [Environment("all runs", ())]
    out: list[Environment] = []
    for name, body in table.items():
        key = f"deploy.environments.{name}"
        match = _table(body, key).get("match", {})
        rules = match if isinstance(match, list) else [match]
        checked: list[Mapping[str, Any]] = []
        for rule in rules:
            rule_table = _table(rule, f"{key}.match")
            unknown = sorted(set(rule_table) - MATCH_KEYS)
            if unknown:
                known = ", ".join(sorted(MATCH_KEYS))
                raise HarnessError(f"{key}.match has unknown keys {unknown}. Known keys: {known}")
            checked.append(rule_table)
        out.append(Environment(name, tuple(checked)))
    return out


def run_matches(env: Environment, run: Run) -> bool:
    """Whether `run` belongs to `env`: any rule holds, and a rule holds when each of its keys does.

    `branch` and `event` are a name or a list, `runName` a glob on the run name, `workflow` the
    workflow file and `since` the earliest creation time. `event` is the trigger, for example
    `push` or `workflow_dispatch`.
    """
    if not env.rules:
        return True
    return any(_rule_holds(rule, run) for rule in env.rules)


def _rule_holds(rule: Mapping[str, Any], run: Run) -> bool:
    for key, value in (("branch", run.branch), ("event", run.event)):
        wanted = rule.get(key)
        if wanted is not None and value not in ([wanted] if isinstance(wanted, str) else wanted):
            return False
    pattern = rule.get("runName")
    if pattern is not None and not fnmatchcase(run.title, str(pattern)):
        return False
    workflow = rule.get("workflow")
    if workflow is not None and run.workflow != workflow:
        return False
    since = rule.get("since")
    if since is not None:
        floor = since if isinstance(since, datetime) else moment(str(since))
        if floor is not None and floor.tzinfo is None:
            floor = floor.replace(tzinfo=UTC)
        if floor is not None and (run.created is None or run.created < floor):
            return False
    return True


def _table(value: object, key: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise HarnessError(f"{key} must be a table")
    return value


def _string(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise HarnessError(f"{key} must be a string")
    return value


def _strings(value: object, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise HarnessError(f"{key} must be a list of strings")
    return list(value)


def _string_table(value: object, key: str) -> dict[str, str]:
    table = _table(value, key)
    out: dict[str, str] = {}
    for name, surface in table.items():
        out[name] = _string(surface, f"{key}.{name}")
    return out
