import json
from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.deploy.github import GitHub, moment

FIVE_PHASE = (
    Path(__file__).resolve().parents[3] / "fixtures" / "deploy" / "github" / "five-phase-workflow"
)


def test_parses_the_recorded_run_list_with_its_workflow() -> None:
    calls: list[list[str]] = []

    def gh(args: list[str]) -> str:
        calls.append(args)
        return (FIVE_PHASE / "runs.json").read_text()

    runs = GitHub(gh).runs("deploy.yml")

    assert [r.id for r in runs] == ["19000000103", "19000000102", "19000000101"]
    assert {r.workflow for r in runs} == {"deploy.yml"}
    assert runs[0].title == "deploy production"
    assert runs[0].conclusion == "failure"
    assert calls[0][:3] == ["run", "list", "--workflow=deploy.yml"]


def test_reads_every_page_of_jobs_of_one_run() -> None:
    recorded = json.loads((FIVE_PHASE / "jobs-19000000101.json").read_text())
    pages = {
        "1": {"total_count": 5, "jobs": recorded["jobs"][:3]},
        "2": {"total_count": 5, "jobs": recorded["jobs"][3:]},
    }
    routes: list[str] = []

    def gh(args: list[str]) -> str:
        routes.append(args[1])
        return json.dumps(pages[args[1].rsplit("page=", 1)[1]])

    jobs = GitHub(gh).jobs("19000000101")

    assert [j.name for j in jobs] == ["gate", "build", "migrate", "apply", "verify"]
    assert len(routes) == 2


def test_names_the_configured_repository_in_every_call() -> None:
    calls: list[list[str]] = []

    def gh(args: list[str]) -> str:
        calls.append(args)
        if args[:2] == ["run", "list"]:
            return (FIVE_PHASE / "runs.json").read_text()
        if args[0] == "api":
            return (FIVE_PHASE / "jobs-19000000101.json").read_text()
        return "log"

    github = GitHub(gh, repo="example-org/example-app")
    github.runs("deploy.yml")
    github.jobs("19000000101")
    github.log(54000000001)

    assert calls[0][-2:] == ["--repo", "example-org/example-app"]
    assert calls[1][1].startswith("/repos/example-org/example-app/actions/runs/19000000101/jobs")
    assert calls[2] == [
        "run",
        "view",
        "--job",
        "54000000001",
        "--log",
        "--repo",
        "example-org/example-app",
    ]


def test_fails_on_gh_output_that_is_not_json() -> None:
    with pytest.raises(HarnessError, match="did not print JSON"):
        GitHub(lambda args: "<html>").runs("deploy.yml")


def test_cuts_a_log_time_to_six_fraction_digits() -> None:
    at = moment("2026-11-12T10:00:00.1234567Z")

    assert at is not None
    assert at.microsecond == 123456
    assert moment("") is None
