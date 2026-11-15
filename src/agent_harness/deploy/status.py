"""`ah deploy-status`: the verdict of the last deploy, its phases, and what each target serves.

The current view walks the runs back from the newest until it holds the newest run that moved
something and, for each serving phase, the newest run where that phase succeeded. The history
view prints one row per run in a window. Both views end with the serving targets, because a run
says what someone started and a target says what runs now.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from agent_harness import HarnessError, config
from agent_harness.deploy.github import RUN_LIST_LIMIT, GitHub, Job, Run, gh_command
from agent_harness.deploy.logs import (
    LogRules,
    rules_of,
    steps_with_log,
    summaries_of,
    summary,
)
from agent_harness.deploy.phases import (
    Environment,
    PhaseMap,
    Row,
    between,
    environments,
    group_span,
    group_status,
    grouped,
    moved,
    open_group,
    phase_map,
    run_matches,
    statuses,
    surfaces,
)
from agent_harness.deploy.records import (
    APPLIED,
    Entry,
    Record,
    read_records,
    record_entry,
    record_folders,
)
from agent_harness.deploy.serving import (
    Execute,
    Fetch,
    Reading,
    execute,
    fetch_url,
    read_targets,
)

# How many runs the current view walks back. Several runs in a row can move nothing, such as
# runs for docs changes, so the view looks past them.
CURRENT_SCAN = 20

# Job lists fetched at once. Each is one REST call of about a second.
FETCH_LANES = 8

# A wait between the creation and the start of a run that is worth a word.
QUEUE_FLOOR = 30

VERSION_KINDS = ("git-count", "digest", "command")
SHA = re.compile(r"[0-9a-f]{40}")
REPO = re.compile(r"[\w.-]+/[\w.-]+")


def span(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def window_hours(text: str) -> int:
    """`24h`, `7d`, or a bare number read as hours."""
    found = re.fullmatch(r"(\d+)\s*([hd]?)", text.strip().lower())
    if not found or int(found.group(1)) == 0:
        raise HarnessError(f"--history takes a window such as 24h or 7d, not {text!r}")
    value = int(found.group(1))
    return value * 24 if found.group(2) == "d" else value


class Versions:
    """The version of a commit, by `[deploy] version`: `git-count`, `digest` or `command`.

    `git-count` is `git rev-list --count <sha>`, so it needs no network call. A commit that this
    checkout does not hold has no version. `digest` takes the digest of a record. `command` runs
    `versionCommand` with the commit as its last argument. A lookup that fails gives no version
    and a line in `failures`, so one bad commit never stops the report.
    """

    def __init__(self, kind: str, command: Sequence[str], root: Path, run: Execute) -> None:
        if kind not in VERSION_KINDS:
            raise HarnessError(f"deploy.version must be one of {', '.join(VERSION_KINDS)}")
        if kind == "command" and not command:
            raise HarnessError("deploy.version is command, so deploy.versionCommand must be set")
        self.kind = kind
        self.command = list(command)
        self.root = root
        self.run = run
        self.cache: dict[str, str | None] = {}
        self.failures: dict[str, str] = {}

    def of(self, entry: Entry) -> str | None:
        """The version of an entry. A record version fills in a commit that gives none."""
        if self.kind == "digest":
            return (entry.record.digest or None) if entry.record is not None else None
        found = self._of_sha(entry.run.sha)
        if found is None and entry.record is not None:
            return entry.record.version or None
        return found

    def _of_sha(self, sha: str) -> str | None:
        if not SHA.fullmatch(sha):
            return None
        if sha not in self.cache:
            self.cache[sha] = self._count(sha) if self.kind == "git-count" else self._run(sha)
        return self.cache[sha]

    def failed(self, shas: Iterable[object]) -> list[str]:
        """The failure line of each commit in `shas` whose lookup failed, each commit once."""
        unique = dict.fromkeys(s for s in shas if isinstance(s, str))
        return [self.failures[s] for s in unique if s in self.failures]

    def _count(self, sha: str) -> str | None:
        held = subprocess.run(
            ["git", "-C", str(self.root), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
            check=False,
        )
        if held.returncode != 0:
            return None
        done = subprocess.run(
            ["git", "-C", str(self.root), "rev-list", "--count", sha],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            self.failures[sha] = f"git rev-list --count {sha[:8]} failed: {_last(done.stderr)}"
            return None
        return done.stdout.strip()

    def _run(self, sha: str) -> str | None:
        try:
            done = self.run([*self.command, sha], self.root)
        except HarnessError as error:
            self.failures[sha] = f"{self.command[0]} did not run for {sha[:8]}: {error}"
            return None
        if done.code != 0:
            self.failures[sha] = (
                f"{self.command[0]} exited {done.code} for {sha[:8]}: {_last(done.err)}"
            )
            return None
        return done.out.strip() or None


def _last(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no error text"


@dataclass
class Context:
    """One report: the config, the sources and the clock."""

    root: Path
    section: Mapping[str, Any]
    pm: PhaseMap
    envs: list[Environment]
    versions: Versions
    github: GitHub | None
    run: Execute
    fetch: Fetch
    now: datetime
    steps: bool = False
    warnings: list[str] = field(default_factory=list)
    log_rules: Mapping[str, LogRules] = field(default_factory=dict)
    summaries: Mapping[str, re.Pattern[str]] = field(default_factory=dict)
    # Job id to its log, or None for a log that could not be read. Each log is read once.
    logs: dict[int, str | None] = field(default_factory=dict)


def workflows(section: Mapping[str, Any]) -> list[str]:
    ci = section.get("ci") or {}
    if not isinstance(ci, Mapping):
        raise HarnessError("deploy.ci must be a table")
    provider = ci.get("provider", "github")
    if provider != "github":
        raise HarnessError(f"deploy.ci.provider {provider!r} is not supported. Use github.")
    names = ci.get("workflows") or []
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise HarnessError("deploy.ci.workflows must be a list of workflow file names")
    return list(names)


def all_runs(ctx: Context) -> list[Run]:
    """The runs of every deploy workflow, newest first."""
    names = workflows(ctx.section)
    if not names or ctx.github is None:
        return []
    runs: list[Run] = []
    for name in names:
        runs.extend(ctx.github.runs(name))
    if not runs:
        ctx.github.check_auth()
    runs.sort(key=lambda r: r.created.isoformat() if r.created else "", reverse=True)
    return runs


def fetch_jobs(ctx: Context, runs: Sequence[Run]) -> list[Entry]:
    github = ctx.github
    if github is None or not runs:
        return []
    with ThreadPoolExecutor(max_workers=FETCH_LANES) as pool:
        jobs = list(pool.map(lambda run: github.jobs(run.id), runs))
    return [Entry(run, tuple(found)) for run, found in zip(runs, jobs, strict=True)]


def counts(entry: Entry, pm: PhaseMap) -> bool:
    """Whether an entry is a deploy: a record, or a run where a phase that moves ran."""
    if entry.record is not None and not entry.record.jobs:
        return True
    if not pm.moves:
        return True
    return moved(entry.jobs, pm)


def serves(entry: Entry, job: str, pm: PhaseMap) -> bool:
    rec = entry.record
    if rec is not None and not rec.jobs:
        return rec.status in APPLIED
    return statuses(entry.jobs, pm).get(job) == "success"


def scan(ctx: Context, runs: Sequence[Run]) -> list[Entry]:
    """Runs from the newest back, a batch at a time, until the current view is answered."""
    found: list[Entry] = []
    limit = min(len(runs), CURRENT_SCAN)
    for at in range(0, limit, FETCH_LANES):
        found.extend(fetch_jobs(ctx, runs[at : min(at + FETCH_LANES, limit)]))
        latest = any(counts(e, ctx.pm) for e in found)
        serving = all(any(serves(e, job, ctx.pm) for e in found) for job in ctx.pm.serving)
        if latest and serving:
            break
    return found


def merge(entries: Iterable[Entry], records: Iterable[Record]) -> list[Entry]:
    out = [*entries, *(record_entry(r) for r in records)]
    out.sort(key=lambda e: e.run.created.isoformat() if e.run.created else "", reverse=True)
    return out


def iso(at: datetime | None) -> str | None:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z") if at else None


def entry_view(ctx: Context, entry: Entry) -> dict[str, Any]:
    run = entry.run
    started = run.started or run.created
    finished = run.updated if run.status == "completed" else ctx.now
    queued = between(run.created, run.started) if run.attempt == 1 else None
    return {
        "run": run.id,
        "workflow": run.workflow,
        "title": run.title,
        "branch": run.branch,
        "commit": run.sha,
        "status": run.status,
        "conclusion": run.conclusion,
        "attempt": run.attempt,
        "started": iso(started),
        "seconds": between(started, finished),
        "queued": queued if queued is not None and queued > QUEUE_FLOOR else None,
        "version": ctx.versions.of(entry),
        "verdict": entry.verdict(ctx.pm),
        "surfaces": surfaces(entry.jobs, ctx.pm),
        "record": entry.record.file if entry.record else None,
        "releaseId": entry.record.id if entry.record and entry.record.id else None,
    }


def job_log(ctx: Context, name: str, job: Job, problems: list[str]) -> str | None:
    """The log of one finished job. A log that cannot be read adds a problem line."""
    if ctx.github is None or job.id is None:
        return None
    if job.id not in ctx.logs:
        try:
            ctx.logs[job.id] = ctx.github.log(job.id)
        except HarnessError as error:
            ctx.logs[job.id] = None
            problems.append(f"the log of {name} could not be read: {error}")
    return ctx.logs[job.id]


def log_summaries(ctx: Context, entry: Entry, problems: list[str]) -> list[dict[str, str]]:
    """The summary line of each job of `deploy.logSummary` that ran as one finished job."""
    groups = dict(grouped(entry.jobs, ctx.pm.prefix))
    out: list[dict[str, str]] = []
    for name, pattern in ctx.summaries.items():
        jobs = groups.get(name, [])
        if len(jobs) != 1 or jobs[0].status != "completed":
            continue
        log = job_log(ctx, name, jobs[0], problems)
        text = summary(log, pattern) if log is not None else None
        if text:
            out.append({"job": name, "text": text})
    return out


def phase_rows(ctx: Context, entry: Entry, problems: list[str]) -> list[Row]:
    rows: list[Row] = []
    for name, jobs in grouped(entry.jobs, ctx.pm.prefix):
        fan = f"  ({len(jobs)} jobs, wall clock)" if len(jobs) > 1 else ""
        status = group_status(jobs)
        rows.append(Row(f"{name}{fan}", status, group_span(jobs, ctx.now), 0))
        if not ctx.steps or status == "skipped":
            continue
        rules = ctx.log_rules.get(name)
        job = jobs[0]
        single = len(jobs) == 1 and job.status == "completed"
        log = job_log(ctx, name, job, problems) if rules and single else None
        if rules and log is not None:
            rows.extend(steps_with_log(job, log, rules))
        else:
            rows.extend(open_group(jobs, ctx.now))
    return rows


def targets_of(env: Environment, readings: Sequence[Reading], ctx: Context) -> list[Reading]:
    names = {e.name for e in ctx.envs}
    if not any(e.rules for e in ctx.envs):
        return list(readings)
    return [r for r in readings if r.env == env.name or r.env not in names]


def reading_view(reading: Reading) -> dict[str, Any]:
    return {
        "target": reading.target,
        "read": reading.method,
        "env": reading.env,
        "rows": [
            {
                "node": s.node,
                "role": s.role,
                "version": s.version,
                "digest": s.digest,
                "state": s.state,
                "detail": s.detail,
            }
            for s in reading.rows
        ],
        "notes": reading.notes,
        "problems": reading.problems,
    }


def records_of(env: Environment, records: Sequence[Record], ctx: Context) -> list[Record]:
    if not any(e.rules for e in ctx.envs):
        return list(records)
    return [r for r in records if r.environment == env.name]


def red_serving(
    ctx: Context, entries: Sequence[Entry], job: str, surface: str, hit: Entry | None
) -> str | None:
    """A caution for each run newer than `hit` where the serving `job` failed.

    A job can fail after its rollout went live, for example in a step that runs after it, so a
    red serving job is no proof that the surface still serves the older run.
    """
    newer = entries[: entries.index(hit)] if hit is not None else entries
    red = [e.run.id for e in newer if statuses(e.jobs, ctx.pm).get(job) == "failure"]
    if not red:
        return None
    return (
        f"{len(red)} newer run(s) failed at {job}, and a red {job} can still have rolled out "
        f"{surface}. Read the targets: {', '.join(red)}."
    )


def current_view(
    ctx: Context,
    env: Environment,
    runs: Sequence[Run],
    records: Sequence[Record],
    readings: Sequence[Reading],
) -> dict[str, Any]:
    mine = [run for run in runs if run_matches(env, run)]
    entries = merge(scan(ctx, mine), records_of(env, records, ctx))
    view: dict[str, Any] = {
        "environment": env.name,
        "scanned": len(entries),
        "latest": None,
        "idle": [],
        "servingFromCi": [],
        "phases": [],
        "targets": [reading_view(r) for r in targets_of(env, readings, ctx)],
        "warnings": [],
        "problems": [],
        "cautions": [],
        "versionFailures": [],
    }
    latest = next((e for e in entries if counts(e, ctx.pm)), None)
    view["waiting"] = [
        {
            "run": e.run.id,
            "status": e.run.status,
            "since": iso(e.run.created),
            "seconds": between(e.run.created, ctx.now),
        }
        for e in entries
        if e.record is None and e.run.status != "completed" and e is not latest
    ]
    if latest is None:
        view["idle"] = [{"run": e.run.id, "verdict": e.verdict(ctx.pm)} for e in entries]
        return view
    index = entries.index(latest)
    view["idle"] = [{"run": e.run.id, "verdict": e.verdict(ctx.pm)} for e in entries[:index]]
    view["latest"] = entry_view(ctx, latest)
    view["latest"]["summaries"] = log_summaries(ctx, latest, view["problems"])
    view["phases"] = [
        {"name": r.name, "status": r.status, "seconds": r.seconds, "indent": r.indent}
        for r in phase_rows(ctx, latest, view["problems"])
    ]
    for job, surface in ctx.pm.serving.items():
        hit = next((e for e in entries if serves(e, job, ctx.pm)), None)
        view["servingFromCi"].append(
            {
                "surface": surface,
                "job": job,
                "run": hit.run.id if hit else None,
                "commit": hit.run.sha if hit else None,
                "version": ctx.versions.of(hit) if hit else None,
                "since": iso(hit.run.updated) if hit else None,
                "searched": len(entries),
            }
        )
        caution = red_serving(ctx, entries, job, surface, hit)
        if caution:
            view["cautions"].append(caution)
    commits = [view["latest"]["commit"], *(s["commit"] for s in view["servingFromCi"])]
    view["versionFailures"] = ctx.versions.failed(commits)
    if view["latest"]["verdict"] == "deployed":
        for reading in view["targets"]:
            if reading["problems"]:
                view["warnings"].append(
                    f"the run says deployed, and target {reading['target']} reports "
                    f"{len(reading['problems'])} problem(s). What the target runs wins."
                )
    return view


def history_view(
    ctx: Context,
    env: Environment,
    runs: Sequence[Run],
    records: Sequence[Record],
    readings: Sequence[Reading],
    hours: int,
) -> dict[str, Any]:
    since = ctx.now - timedelta(hours=hours)
    mine = [r for r in runs if run_matches(env, r) and (r.created or ctx.now) >= since]
    local = [r for r in records_of(env, records, ctx) if (r.started or ctx.now) >= since]
    entries = merge(fetch_jobs(ctx, mine), local)
    rows = [entry_view(ctx, e) for e in entries]
    return {
        "environment": env.name,
        "windowHours": hours,
        "runs": rows,
        "deployed": sum(1 for r in rows if str(r["verdict"]).startswith("deployed")),
        "missingVersions": sum(1 for r in rows if r["version"] is None),
        "versionFailures": ctx.versions.failed(r["commit"] for r in rows),
        # The window reaches the oldest run that the run list gave, so older runs may exist.
        "capped": len(runs) >= RUN_LIST_LIMIT and bool(mine) and mine[-1] is runs[-1],
        "targets": [reading_view(r) for r in targets_of(env, readings, ctx)],
    }


def table(out: TextIO, headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: str) -> None:
    """One boxed table with a rule between every row. Column widths come from the data."""
    cols = range(len(headers))
    widths = [max([len(headers[c]), *(len(row[c]) for row in rows)]) for c in cols]

    def rule(left: str, mid: str, right: str) -> str:
        return "  " + left + mid.join("─" * (widths[c] + 2) for c in cols) + right

    def line(cells: Sequence[str]) -> str:
        parts = [
            cells[c].rjust(widths[c]) if aligns[c] == "r" else cells[c].ljust(widths[c])
            for c in cols
        ]
        return "  │ " + " │ ".join(parts) + " │"

    print(rule("┌", "┬", "┐"), file=out)
    print(line(headers), file=out)
    print(rule("├", "┼", "┤"), file=out)
    for index, row in enumerate(rows):
        print(line(row), file=out)
        last = index == len(rows) - 1
        print(rule("└", "┴", "┘") if last else rule("├", "┼", "┤"), file=out)


def stamp(text: object, short: bool = False) -> str:
    if not isinstance(text, str):
        return "-"
    at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return at.strftime("%m-%d %H:%M" if short else "%Y-%m-%d %H:%M")


def short_sha(sha: object) -> str:
    return str(sha)[:8] if sha else "-"


def print_targets(out: TextIO, targets: Sequence[Mapping[str, Any]]) -> None:
    for reading in targets:
        print(f"  target {reading['target']} (read {reading['read']})", file=out)
        rows = [
            [
                r["node"],
                r["role"],
                str(r["version"])[:24],
                str(r["digest"])[:19],
                r["state"] + (f" ({r['detail']})" if r["detail"] else ""),
            ]
            for r in reading["rows"]
        ]
        if rows:
            table(out, ["NODE", "ROLE", "VERSION", "DIGEST", "STATE"], rows, "lllll")
        for note in reading["notes"]:
            print(f"  {note}", file=out)
        for problem in reading["problems"]:
            print(f"  problem: {problem}", file=out)
        print(file=out)


def print_version_failures(out: TextIO, failures: Sequence[str]) -> None:
    if failures:
        print(
            f"  the version lookup failed for {len(failures)} commit(s), so each reads -. "
            f"The first: {failures[0]}",
            file=out,
        )


def tally(idle: Sequence[Mapping[str, Any]]) -> str:
    counted: dict[str, int] = {}
    for item in idle:
        counted[item["verdict"]] = counted.get(item["verdict"], 0) + 1
    return ", ".join(f"{what} ({count})" for what, count in counted.items())


def print_waiting(out: TextIO, waiting: Sequence[Mapping[str, Any]]) -> None:
    for item in waiting:
        print(
            f"  run {item['run']} {item['status']} for {span(item['seconds'])}, "
            f"since {stamp(item['since'])} UTC.",
            file=out,
        )


def print_current(out: TextIO, view: Mapping[str, Any]) -> None:
    print(view["environment"], file=out)
    latest = view["latest"]
    idle = view["idle"]
    if latest is None:
        if view["scanned"] == 0:
            print("  no deploy run and no deploy record exists yet.", file=out)
        else:
            why = f": {tally(idle)}" if idle else ""
            print(f"  none of the last {view['scanned']} runs moved anything{why}.", file=out)
        print_waiting(out, view["waiting"])
        print(file=out)
        print_targets(out, view["targets"])
        return
    if idle:
        print(f"  {len(idle)} newer run(s) moved nothing: {tally(idle)}.", file=out)
    print_waiting(out, view["waiting"])
    run = latest["run"]
    head = f"  run {run}  {latest['conclusion'] or latest['status']}"
    head += f"  commit {short_sha(latest['commit'])}  version {latest['version'] or '-'}"
    print(head, file=out)
    print(f"  {latest['title']}", file=out)
    for item in latest["summaries"]:
        print(f"  {item['job']}: {item['text']}", file=out)
    times = f"  started {stamp(latest['started'])} UTC   total {span(latest['seconds'])}"
    if latest["attempt"] > 1:
        times += f"   attempt {latest['attempt']}"
    elif latest["queued"]:
        times += f"   queued {span(latest['queued'])}"
    print(times, file=out)
    moves = ", ".join(latest["surfaces"]) or "nothing"
    print(f"  {latest['verdict']}. Moved: {moves}", file=out)
    print(file=out)
    serving = view["servingFromCi"]
    if serving:
        rows = []
        for item in serving:
            if item["run"] is None:
                rows.append(
                    [item["surface"], item["job"], "-", "-", "-", f"none in {item['searched']}"]
                )
                continue
            rows.append(
                [
                    item["surface"],
                    item["job"],
                    short_sha(item["commit"]),
                    item["version"] or "-",
                    str(item["run"]),
                    f"{stamp(item['since'])} UTC",
                ]
            )
        table(out, ["SERVING", "JOB", "COMMIT", "VERSION", "RUN", "SINCE"], rows, "lllrll")
        print(file=out)
    phases = view["phases"]
    if phases:
        data = []
        for row in phases:
            label = " " * row["indent"] + row["name"]
            label = label if len(label) <= 66 else label[:65] + "~"
            data.append([label, row["status"], span(row["seconds"])])
        table(out, ["PHASE", "STATUS", "DURATION"], data, "llr")
        print(file=out)
    for problem in view["problems"]:
        print(f"  problem: {problem}", file=out)
    for warning in view["warnings"]:
        print(f"  warning: {warning}", file=out)
    for caution in view["cautions"]:
        print(f"  caution: {caution}", file=out)
    print_version_failures(out, view["versionFailures"])
    print_targets(out, view["targets"])


def print_history(out: TextIO, view: Mapping[str, Any]) -> None:
    runs = view["runs"]
    print(f"{view['environment']}   last {view['windowHours']}h   {len(runs)} run(s)", file=out)
    if not runs:
        print("  no deploy run and no deploy record in that window.", file=out)
    else:
        rows = [
            [
                stamp(r["started"], short=True),
                str(r["run"]),
                short_sha(r["commit"]) if r["commit"] else (r["releaseId"] or "-"),
                r["version"] or "-",
                span(r["seconds"]),
                r["verdict"],
            ]
            for r in runs
        ]
        table(out, ["STARTED", "RUN", "COMMIT", "VERSION", "TOOK", "STATUS"], rows, "llllrl")
        print(f"  {view['deployed']} of {len(runs)} run(s) deployed.", file=out)
        if view["missingVersions"]:
            print(
                f"  {view['missingVersions']} run(s) have no version. A commit that this "
                "checkout does not hold has none: `git fetch` fills it in.",
                file=out,
            )
        print_version_failures(out, view["versionFailures"])
        if view["capped"]:
            print(
                f"  the run list stops at {RUN_LIST_LIMIT} runs, so the window may reach further.",
                file=out,
            )
    print(file=out)
    print_targets(out, view["targets"])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ah deploy-status",
        description=(
            "The verdict of the last deploy, its phases with a duration each, and what each "
            "target serves now. Read-only."
        ),
    )
    parser.add_argument("--env", help="one environment of deploy.environments")
    parser.add_argument("--steps", action="store_true", help="open each phase into jobs or steps")
    parser.add_argument(
        "--history",
        nargs="?",
        const="24h",
        metavar="WINDOW",
        help="recent runs instead of the last deploy: 24h by default, or 72h, 7d",
    )
    parser.add_argument("--json", action="store_true", help="print one JSON document")
    return parser.parse_args(argv)


def build_context(
    root: Path,
    steps: bool,
    gh: Callable[[list[str]], str] | None,
    run: Execute,
    fetch: Fetch,
    now: datetime,
) -> Context:
    section = config.load(root).get("deploy")
    if not isinstance(section, Mapping):
        raise HarnessError("deploy must be a table")
    command = section.get("versionCommand") or []
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not all(isinstance(c, str) for c in command):
        raise HarnessError("deploy.versionCommand must be a command string or a list of strings")
    names = workflows(section)
    repo = (section.get("ci") or {}).get("repo") or ""
    if not isinstance(repo, str) or (repo and not REPO.fullmatch(repo)):
        raise HarnessError(f"deploy.ci.repo must be <owner>/<name>, not {repo!r}")
    github = None
    if names:
        github = GitHub(gh if gh is not None else gh_command(root), repo)
    return Context(
        root=root,
        section=section,
        pm=phase_map(section),
        envs=environments(section),
        versions=Versions(str(section.get("version")), command, root, run),
        github=github,
        run=run,
        fetch=fetch,
        now=now,
        steps=steps,
        log_rules=rules_of(section),
        summaries=summaries_of(section),
    )


def report(
    argv: list[str],
    root: Path,
    out: TextIO,
    gh: Callable[[list[str]], str] | None = None,
    run: Execute = execute,
    fetch: Fetch = fetch_url,
    now: datetime | None = None,
) -> int:
    """The whole command with its sources passed in. `main` passes the real ones."""
    args = parse_args(argv)
    hours = window_hours(args.history) if args.history is not None else None
    ctx = build_context(root, args.steps, gh, run, fetch, now or datetime.now(UTC))
    chosen = ctx.envs
    if args.env is not None:
        chosen = [e for e in ctx.envs if e.name == args.env]
        if not chosen:
            known = ", ".join(e.name for e in ctx.envs if e.rules) or "none"
            raise HarnessError(f"--env {args.env} is not in deploy.environments (known: {known})")
    runs = all_runs(ctx)
    folder = ctx.section.get("records") or ""
    if not isinstance(folder, str):
        raise HarnessError("deploy.records must be a folder path")
    digest_key = ctx.section.get("recordDigest") or "digest"
    version_key = ctx.section.get("recordVersion") or ""
    if not isinstance(digest_key, str) or not isinstance(version_key, str):
        raise HarnessError("deploy.recordDigest and deploy.recordVersion must be dotted keys")
    records = read_records(record_folders(root, folder), digest_key, version_key)
    readings = read_targets(ctx.section, root, ctx.run, ctx.fetch)
    views: list[dict[str, Any]] = []
    for env in chosen:
        if hours is None:
            views.append(current_view(ctx, env, runs, records, readings))
        else:
            views.append(history_view(ctx, env, runs, records, readings, hours))
    if args.json:
        kind = "current" if hours is None else "history"
        print(json.dumps({"view": kind, "environments": views}, indent=2), file=out)
        return 0
    for view in views:
        if hours is None:
            print_current(out, view)
        else:
            print_history(out, view)
    return 0


def main(argv: list[str]) -> int:
    return report(argv, config.find_root(), sys.stdout)
