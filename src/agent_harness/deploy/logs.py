"""Phases that banner lines split a job log into, placed under the step that printed them.

A GitHub log line is `<job>\\t<step>\\t<time> <text>`. The step column often reads `UNKNOWN STEP`,
so a banner belongs to the first step whose clock holds its time. The jobs API gives a step
clock in whole seconds, so a step holds every time up to one second past its end.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from agent_harness import HarnessError
from agent_harness.deploy.github import Job, Step, moment
from agent_harness.deploy.phases import Row, between, step_entries

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOG_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
STEP_CLOCK = timedelta(seconds=1)
RULE_KEYS = frozenset({"banners", "final", "fatal", "aside"})
PASSED = frozenset({"pass", "passed", "ok", "success", "succeeded"})


@dataclass(frozen=True)
class LogRules:
    """The patterns of one `logPhases.<job>`.

    `banners` start a phase. `final` is the line that a finished script prints last: it closes
    the last phase. `fatal` is a line that names why the script stopped, and counts only when no
    final line came. `aside` is a line that gets a row and no duration. With neither `final` nor
    `fatal`, a phase has the status `logged`.
    """

    banners: tuple[re.Pattern[str], ...]
    final: re.Pattern[str] | None = None
    fatal: re.Pattern[str] | None = None
    aside: re.Pattern[str] | None = None


@dataclass(frozen=True)
class Line:
    """One log line with a time, its colour codes removed."""

    when: datetime
    text: str


@dataclass(frozen=True)
class Banner:
    when: datetime
    name: str
    seconds: int | None
    status: str | None


def _compile(text: object, key: str) -> re.Pattern[str]:
    if not isinstance(text, str):
        raise HarnessError(f"{key} must be a pattern string")
    try:
        return re.compile(text)
    except re.error as error:
        raise HarnessError(f"{key} holds a bad pattern: {error}") from error


def log_rules(value: object, key: str) -> LogRules:
    """The rules of one `logPhases.<job>`: a list of banner patterns, or a table."""
    if isinstance(value, Mapping):
        unknown = sorted(set(value) - RULE_KEYS)
        if unknown:
            raise HarnessError(f"{key} has unknown keys {unknown}. Known keys: {sorted(RULE_KEYS)}")
        banners = value.get("banners")
        more = {
            name: _compile(value[name], f"{key}.{name}")
            for name in ("final", "fatal", "aside")
            if value.get(name)
        }
        return LogRules(_banners(banners, f"{key}.banners"), **more)
    return LogRules(_banners(value, key))


def _banners(value: object, key: str) -> tuple[re.Pattern[str], ...]:
    texts = [value] if isinstance(value, str) else value
    if not isinstance(texts, list) or not texts:
        raise HarnessError(f"{key} must be a list of patterns")
    return tuple(_compile(text, key) for text in texts)


def rules_of(section: Mapping[str, Any]) -> dict[str, LogRules]:
    """`deploy.logPhases`: job key to its rules."""
    table = section.get("logPhases") or {}
    if not isinstance(table, Mapping):
        raise HarnessError("deploy.logPhases must be a table")
    return {str(job): log_rules(value, f"deploy.logPhases.{job}") for job, value in table.items()}


def summaries_of(section: Mapping[str, Any]) -> dict[str, re.Pattern[str]]:
    """`deploy.logSummary`: job key to the pattern of the line that sums up its log."""
    table = section.get("logSummary") or {}
    if not isinstance(table, Mapping):
        raise HarnessError("deploy.logSummary must be a table")
    return {str(job): _compile(text, f"deploy.logSummary.{job}") for job, text in table.items()}


def summary(log: str, pattern: re.Pattern[str]) -> str | None:
    """The first group of the first line that `pattern` matches, or the whole match."""
    for line in log_lines(log):
        hit = pattern.search(line.text)
        if hit:
            return _named(hit)
    return None


def log_lines(log: str) -> list[Line]:
    """The lines of a job log that carry a time. A wrapped line has none and is left out."""
    out: list[Line] = []
    for raw in log.splitlines():
        parts = raw.split("\t", 2)
        if len(parts) < 3:
            continue
        stamp, _, body = parts[2].lstrip("﻿").partition(" ")
        if not LOG_STAMP.fullmatch(stamp):
            continue
        when = moment(stamp)
        if when is not None:
            out.append(Line(when, ANSI.sub("", body).strip()))
    return out


def home_step(steps: Sequence[Step], when: datetime) -> Step | None:
    """The first step whose clock holds `when`."""
    for step in steps:
        if step.started is None or step.completed is None:
            continue
        if step.started <= when < step.completed + STEP_CLOCK:
            return step
    return None


def _named(hit: re.Match[str]) -> str:
    if "name" in hit.re.groupindex:
        return hit.group("name")
    return hit.group(1) if hit.groups() else hit.group(0)


def _status(text: str) -> str:
    word = text.strip().lower()
    if word in PASSED:
        return "success"
    return "failure" if word.startswith("fail") else word


def _banner(line: Line, hit: re.Match[str]) -> Banner:
    fields = hit.re.groupindex
    seconds = round(float(hit.group("seconds"))) if "seconds" in fields else None
    status = _status(hit.group("status")) if "status" in fields else None
    return Banner(line.when, _named(hit), seconds, status)


def log_phases(
    log: str, rules: LogRules, steps: Sequence[Step] = ()
) -> list[tuple[Step | None, Row]]:
    """Each phase, aside and fatal line of a log, in log order, with the step that printed it.

    A banner names its phase by the group `name`, else by the first group, else by the whole
    match. A group `seconds` gives the duration and a group `status` the status. Else a phase
    ends at the next banner, the final line or the end of its step, whichever comes first. With
    no step clock and no final line, the last line of the log closes the last phase.
    """
    lines = log_lines(log)
    banners: list[Banner] = []
    asides: list[tuple[datetime, Row]] = []
    final: datetime | None = None
    fatal: tuple[datetime, str] | None = None
    for line in lines:
        if rules.final is not None and rules.final.search(line.text):
            final = final or line.when
            continue
        if rules.fatal is not None and (hit := rules.fatal.search(line.text)):
            fatal = (line.when, _named(hit))
            continue
        if rules.aside is not None and (hit := rules.aside.search(line.text)):
            asides.append((line.when, Row(_named(hit), "-", None, 4)))
            continue
        hit = next((m for p in rules.banners if (m := p.search(line.text))), None)
        if hit is not None:
            banners.append(_banner(line, hit))
    strict = rules.final is not None or rules.fatal is not None
    died = fatal is not None and final is None
    found: list[tuple[datetime, Row]] = list(asides)
    for index, banner in enumerate(banners):
        last = index + 1 == len(banners)
        status = banner.status or _phase_status(strict, last, final is not None, died)
        seconds = banner.seconds
        if seconds is None:
            seconds = between(banner.when, _end(banners, index, final, steps, lines))
        found.append((banner.when, Row(banner.name, status, seconds, 4)))
    if died and fatal is not None:
        found.append((fatal[0], Row(f"died: {fatal[1]}", "failure", None, 4)))
    found.sort(key=lambda item: item[0])
    return [(home_step(steps, when), row) for when, row in found]


def _phase_status(strict: bool, last: bool, finished: bool, died: bool) -> str:
    if not strict:
        return "logged"
    if not last or finished:
        return "success"
    return "failure" if died else "incomplete"


def _end(
    banners: Sequence[Banner],
    index: int,
    final: datetime | None,
    steps: Sequence[Step],
    lines: Sequence[Line],
) -> datetime | None:
    when = banners[index].when
    ends = [banners[index + 1].when] if index + 1 < len(banners) else []
    if final is not None and final >= when:
        ends.append(final)
    home = home_step(steps, when)
    if home is not None and home.completed is not None:
        ends.append(home.completed)
    if ends:
        return min(ends)
    return lines[-1].when if lines else None


def log_rows(log: str, rules: LogRules) -> list[Row]:
    """The phases of a log with no step clock."""
    return [row for _, row in log_phases(log, rules)]


def steps_with_log(job: Job, log: str, rules: LogRules) -> list[Row]:
    """The step rows of one job, each followed by the log phases that its step printed.

    A phase whose step gets no row, or that no step clock holds, follows the last row.
    """
    phases = log_phases(log, rules, job.steps)
    rows: list[Row] = []
    placed: set[int] = set()
    for step, row in step_entries(job):
        rows.append(row)
        for index, (home, phase) in enumerate(phases):
            if home is step:
                rows.append(phase)
                placed.add(index)
    rows.extend(phase for index, (_, phase) in enumerate(phases) if index not in placed)
    return rows
