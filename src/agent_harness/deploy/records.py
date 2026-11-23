"""Deploy records from a workstation: a deploy that ran outside CI still prints as a run.

`[deploy] records` names a folder of JSON files or JSON lines files. Two record shapes are read:

- a job record: `environment`, `sha`, `branch`, `startedAt`, `finishedAt` and `jobs`, a table of
  job name to `{ result, seconds }`. Its verdict comes from the phase map, as a run's does.
- a release record: `id`, `image`, `digest`, `at` and `status` (`applied` or `stopped`), with an
  optional `rollback`. Its verdict comes from `status`.

A record without `environment` belongs to the environment that its file stem names, so the line
of `deploy/releases/production.jsonl` belongs to `production`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_harness import HarnessError
from agent_harness.config import git_common_dir
from agent_harness.deploy.github import Job, Run, moment
from agent_harness.deploy.phases import PhaseMap, verdict

APPLIED = frozenset({"applied", "success", "deployed"})


@dataclass(frozen=True)
class Record:
    """One deploy record. `jobs` is empty for a release record."""

    file: str
    environment: str
    sha: str
    branch: str
    started: datetime | None
    finished: datetime | None
    jobs: tuple[Job, ...]
    status: str
    id: str
    digest: str
    rollback: bool
    version: str = ""


@dataclass(frozen=True)
class Entry:
    """A run from CI, or a record shaped as a run, with its jobs."""

    run: Run
    jobs: tuple[Job, ...]
    record: Record | None = None

    def verdict(self, pm: PhaseMap) -> str:
        rec = self.record
        if rec is not None and not rec.jobs:
            if rec.status in APPLIED:
                return f"deployed (rollback to {rec.id})" if rec.rollback else "deployed"
            return f"failed (record status {rec.status or 'missing'})"
        return verdict(self.run, self.jobs, pm)


def record_folders(root: Path, folder: str) -> list[Path]:
    """The record folder in this checkout and in the main checkout, each once.

    A worktree is short-lived. A workstation deploy can write its record in the main checkout,
    and a release log sits in the checkout that ran the deploy.
    """
    if not folder:
        return []
    found = [root / folder]
    common = git_common_dir(root)
    if common is not None and common.name == ".git":
        found.append(common.parent / folder)
    out: list[Path] = []
    for path in found:
        if path.is_dir() and path.resolve() not in [p.resolve() for p in out]:
            out.append(path)
    return out


def read_records(
    folders: Sequence[Path], digest_key: str = "digest", version_key: str = ""
) -> list[Record]:
    """Every record in `folders`, newest first. A file that does not parse is an error.

    `digest_key` and `version_key` are dotted keys of a record, for example `digests.app`.
    An empty `version_key` reads no version.
    """
    records: list[Record] = []
    seen: set[Path] = set()
    for folder in folders:
        for path in sorted(folder.iterdir()):
            if path.suffix not in (".json", ".jsonl") or path.resolve() in seen:
                continue
            seen.add(path.resolve())
            records.extend(_read_file(path, digest_key, version_key))
    records.sort(key=lambda r: r.started.isoformat() if r.started else "", reverse=True)
    return records


def _read_file(path: Path, digest_key: str, version_key: str) -> list[Record]:
    text = path.read_text(encoding="utf-8")
    rows: list[object] = []
    try:
        if path.suffix == ".jsonl":
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            rows = [json.loads(text)]
    except json.JSONDecodeError as error:
        raise HarnessError(f"{path} is not a deploy record: {error}") from error
    out: list[Record] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise HarnessError(f"{path} holds a record that is not an object")
        out.append(_record(path, row, digest_key, version_key))
    return out


def dotted(row: Mapping[str, Any], key: str) -> object:
    """The value at a dotted key, or None when a part is missing."""
    node: object = row
    for part in key.split("."):
        if not isinstance(node, Mapping):
            return None
        node = node.get(part)
    return node


def _record(path: Path, row: Mapping[str, Any], digest_key: str, version_key: str) -> Record:
    jobs: list[Job] = []
    table = row.get("jobs")
    if isinstance(table, Mapping):
        for name, job in table.items():
            body = job if isinstance(job, Mapping) else {}
            seconds = body.get("seconds")
            jobs.append(
                Job(
                    name=str(name),
                    status="completed",
                    conclusion=str(body.get("result") or ""),
                    started=None,
                    completed=None,
                    seconds=seconds if isinstance(seconds, int) else None,
                )
            )
    started = moment(row.get("startedAt") or row.get("at"))
    return Record(
        file=path.name,
        environment=str(row.get("environment") or path.stem),
        sha=str(row.get("sha") or ""),
        branch=str(row.get("branch") or ""),
        started=started,
        finished=moment(row.get("finishedAt")) or started,
        jobs=tuple(jobs),
        status=str(row.get("status") or ""),
        id=str(row.get("id") or ""),
        digest=str(dotted(row, digest_key) or ""),
        rollback=row.get("rollback") is True,
        version=str((dotted(row, version_key) if version_key else None) or ""),
    )


def record_entry(record: Record) -> Entry:
    """A record in the shape of a run, so it prints as a run does."""
    failed = any(job.conclusion == "failure" for job in record.jobs) or (
        not record.jobs and record.status not in APPLIED
    )
    run = Run(
        id="workstation",
        title=f"workstation deploy, record {record.file}",
        branch=record.branch,
        sha=record.sha,
        status="completed",
        conclusion="failure" if failed else "success",
        created=record.started,
        started=record.started,
        updated=record.finished,
        attempt=1,
        workflow="",
    )
    return Entry(run, record.jobs, record)
