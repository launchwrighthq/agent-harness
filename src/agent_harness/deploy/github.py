"""The CI half on GitHub Actions: runs, jobs and job logs through the `gh` CLI.

Every call is a read. `gh run list` gives the runs, `gh api .../jobs` gives the jobs with their
steps, and `gh run view --job <id> --log` gives one log. The parsers take the JSON as `gh` prints
it, so a test feeds them recorded output.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_harness import HarnessError

# Runs asked of `gh run list` per workflow before a window is applied. A window past this is
# named in the footer.
RUN_LIST_LIMIT = 200

# The pages of 100 jobs that one run may span. A large matrix passes the default 30.
JOB_PAGES = 10

RUN_FIELDS = (
    "attempt,conclusion,createdAt,databaseId,displayTitle,event,headBranch,headSha,"
    "startedAt,status,updatedAt,workflowName"
)

_FRACTION = re.compile(r"^(.*\.\d{6})\d*(.*)$")

# How `gh` reports a 404 of the API.
NOT_FOUND = re.compile(r"HTTP 404|Not Found")


def moment(text: object) -> datetime | None:
    """An RFC 3339 time as GitHub writes it, or None for an empty value.

    A job log has seven fraction digits. The fraction is cut to six, because a phase needs the
    second only.
    """
    if not isinstance(text, str) or not text:
        return None
    value = text.replace("Z", "+00:00")
    cut = _FRACTION.match(value)
    if cut:
        value = cut.group(1) + cut.group(2)
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise HarnessError(f"{text!r} is not an RFC 3339 time") from error


@dataclass(frozen=True)
class Step:
    """One step of a job, as the jobs API reports it."""

    name: str
    status: str
    conclusion: str
    started: datetime | None
    completed: datetime | None


@dataclass(frozen=True)
class Job:
    """One job of a run. A workstation record gives `seconds` and no clock."""

    name: str
    status: str
    conclusion: str
    started: datetime | None
    completed: datetime | None
    steps: tuple[Step, ...] = ()
    id: int | None = None
    seconds: int | None = None


@dataclass(frozen=True)
class Run:
    """One workflow run. `workflow` is the workflow file that the run list was asked for."""

    id: str
    title: str
    branch: str
    sha: str
    status: str
    conclusion: str
    created: datetime | None
    started: datetime | None
    updated: datetime | None
    attempt: int
    workflow: str
    event: str = ""


def parse_runs(payload: object, workflow: str) -> list[Run]:
    """The runs of `gh run list --json <RUN_FIELDS>` for one workflow file."""
    if not isinstance(payload, list):
        raise HarnessError(f"gh run list for {workflow} did not print a JSON list")
    runs: list[Run] = []
    for row in payload:
        if not isinstance(row, Mapping):
            raise HarnessError(f"gh run list for {workflow} printed a row that is not an object")
        runs.append(
            Run(
                id=str(row.get("databaseId", "")),
                title=str(row.get("displayTitle") or ""),
                branch=str(row.get("headBranch") or ""),
                sha=str(row.get("headSha") or ""),
                status=str(row.get("status") or ""),
                conclusion=str(row.get("conclusion") or ""),
                created=moment(row.get("createdAt")),
                started=moment(row.get("startedAt")),
                updated=moment(row.get("updatedAt")),
                attempt=int(row.get("attempt") or 1),
                workflow=workflow,
                event=str(row.get("event") or ""),
            )
        )
    return runs


def _step(row: Mapping[str, Any]) -> Step:
    return Step(
        name=str(row.get("name") or ""),
        status=str(row.get("status") or ""),
        conclusion=str(row.get("conclusion") or ""),
        started=moment(row.get("started_at")),
        completed=moment(row.get("completed_at")),
    )


def parse_jobs(payload: object) -> tuple[list[Job], int]:
    """The jobs of one page of `gh api .../runs/<id>/jobs`, and the `total_count` of the run."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("jobs"), list):
        raise HarnessError("gh api .../jobs did not print an object with a jobs list")
    jobs: list[Job] = []
    for row in payload["jobs"]:
        if not isinstance(row, Mapping):
            raise HarnessError("gh api .../jobs printed a job that is not an object")
        steps = row.get("steps") or []
        jobs.append(
            Job(
                name=str(row.get("name") or ""),
                status=str(row.get("status") or ""),
                conclusion=str(row.get("conclusion") or ""),
                started=moment(row.get("started_at")),
                completed=moment(row.get("completed_at")),
                steps=tuple(_step(s) for s in steps if isinstance(s, Mapping)),
                id=int(row["id"]) if isinstance(row.get("id"), int) else None,
            )
        )
    total = payload.get("total_count")
    return jobs, total if isinstance(total, int) else len(jobs)


Gh = Callable[[list[str]], str]


def gh_command(root: Path) -> Gh:
    """A function that runs `gh <args>` in `root` and returns its output.

    Only the exit code decides a failure. `gh` can print notes to stderr on a call that works.
    """
    binary = shutil.which("gh")
    if binary is None:
        raise HarnessError(
            "gh is not on PATH. Install the GitHub CLI (https://cli.github.com), then run "
            "`gh auth login`."
        )

    def run(args: list[str]) -> str:
        done = subprocess.run(
            [binary, *args], cwd=root, capture_output=True, text=True, check=False
        )
        if done.returncode != 0:
            lines = [line for line in done.stderr.strip().splitlines() if line.strip()]
            last = lines[-1] if lines else "no error text"
            raise HarnessError(f"gh {' '.join(args[:2])} exited {done.returncode}: {last}")
        return done.stdout

    return run


class GitHub:
    """The read calls of the CI half. `gh` is the function that runs one `gh` command.

    `repo` is `<owner>/<name>`. Empty means the repository of the checkout that `gh` runs in.
    """

    def __init__(self, gh: Gh, repo: str = "") -> None:
        self.gh = gh
        self.repo = repo
        self.flag = ["--repo", repo] if repo else []

    def runs(self, workflow: str) -> list[Run]:
        text = self.gh(
            [
                "run",
                "list",
                f"--workflow={workflow}",
                "--limit",
                str(RUN_LIST_LIMIT),
                "--json",
                RUN_FIELDS,
                *self.flag,
            ]
        )
        return parse_runs(_json(text, f"gh run list --workflow={workflow}"), workflow)

    def jobs(self, run_id: str) -> list[Job]:
        """Every job of one run, across the pages GitHub splits them into."""
        found: list[Job] = []
        repo = self.repo or "{owner}/{repo}"
        for page in range(1, JOB_PAGES + 1):
            route = f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}"
            jobs, total = parse_jobs(_json(self.gh(["api", route]), f"gh api {route}"))
            found.extend(jobs)
            if not jobs or len(found) >= total:
                break
        return found

    def log(self, job_id: int) -> str:
        """The whole log of one finished job. A job in flight has no log yet."""
        return self.gh(["run", "view", "--job", str(job_id), "--log", *self.flag])

    def check_repo(self) -> None:
        """Fails when the active `gh` account cannot see the repository. GitHub answers 404 for a
        private repository that the account cannot read, and `gh run list` then blames the
        workflow."""
        try:
            self.gh(["api", f"repos/{self.repo or '{owner}/{repo}'}", "--jq", ".full_name"])
        except HarnessError as error:
            if not NOT_FOUND.search(str(error)):
                raise
            shown = self.repo or "of this checkout"
            raise HarnessError(
                f"the active gh account cannot see the repository {shown}. Check the gh identity"
                " of this project: `gh auth status` names the active account, and"
                f" `gh auth switch` picks another. ({error})"
            ) from error

    def check_auth(self) -> None:
        """Fails when `gh` holds no token. An empty run list must never read as no deploy."""
        try:
            self.gh(["auth", "status"])
        except HarnessError as error:
            raise HarnessError(
                f"gh is not authenticated. Run `gh auth login`. ({error})"
            ) from error


def _json(text: str, what: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise HarnessError(f"{what} did not print JSON: {error}") from error
