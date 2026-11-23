import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_harness import HarnessError
from agent_harness.deploy.github import Job, Run, parse_jobs, parse_runs
from agent_harness.deploy.phases import (
    Environment,
    PhaseMap,
    environments,
    group_of,
    group_span,
    grouped,
    phase_map,
    run_matches,
    step_rows,
    verdict,
)

RECORDED = Path(__file__).resolve().parents[3] / "fixtures" / "deploy" / "github"
FIVE_PHASE = RECORDED / "five-phase-workflow"
CHECK_BANNERS = RECORDED / "check-banners"

# The phase map of a deploy workflow with the jobs gate, build, migrate, apply and verify.
FIVE_PHASES = {
    "phases": {
        "moves": {"build": "image", "migrate": "database", "apply": "nodes"},
        "serving": {"apply": "nodes"},
        "noop": ["migrate"],
    },
    "ci": {"jobPrefix": ""},
}


def recorded_runs() -> dict[str, Run]:
    runs = parse_runs(json.loads((FIVE_PHASE / "runs.json").read_text()), "deploy.yml")
    return {run.id: run for run in runs}


def recorded_jobs(run_id: str) -> list[Job]:
    jobs, _ = parse_jobs(json.loads((FIVE_PHASE / f"jobs-{run_id}.json").read_text()))
    return jobs


def check_banner_jobs() -> dict[str, Job]:
    jobs, _ = parse_jobs(json.loads((CHECK_BANNERS / "jobs-19000000201.json").read_text()))
    return {j.name: j for j in jobs}


def job(name: str, conclusion: str = "success", **times: str) -> Job:
    row: dict[str, Any] = {"name": name, "status": "completed", "conclusion": conclusion}
    row.update(times)
    return parse_jobs({"jobs": [row], "total_count": 1})[0][0]


def run(**fields: Any) -> Run:
    row = {"databaseId": 7, "status": "completed", "conclusion": "success", **fields}
    return parse_runs([row], "deploy.yml")[0]


@pytest.mark.parametrize(
    ("run_id", "expected"),
    [
        ("19000000101", "deployed"),
        ("19000000102", "nothing to deploy"),
        ("19000000103", "failed at migrate"),
    ],
    ids=["full-deploy", "docs-only", "failed-migrate"],
)
def test_gives_the_verdict_of_each_recorded_run(run_id: str, expected: str) -> None:
    pm = phase_map(FIVE_PHASES)

    assert verdict(recorded_runs()[run_id], recorded_jobs(run_id), pm) == expected


def test_never_reads_a_green_run_as_deployed_without_a_phase_map() -> None:
    pm = phase_map({})

    found = verdict(recorded_runs()["19000000101"], recorded_jobs("19000000101"), pm)

    assert found == "finished, no phase map (set deploy.phases.moves)"


def test_reads_a_moved_run_without_a_serving_success_as_not_deployed() -> None:
    pm = PhaseMap(
        moves={"build": "image", "apply": "nodes"}, serving={"apply": "nodes"}, noop=frozenset()
    )
    jobs = [job("gate"), job("build"), job("apply", "skipped")]

    assert verdict(run(), jobs, pm) == "moved image, no serving phase succeeded"


def test_reads_deployed_only_from_the_serving_jobs_that_phases_deployed_names() -> None:
    pm = phase_map(
        {
            "phases": {
                "moves": {"apply": "app", "workers": "workers"},
                "serving": {"apply": "app", "workers": "workers"},
                "deployed": ["apply"],
            }
        }
    )

    workers_only = verdict(run(), [job("apply", "skipped"), job("workers")], pm)
    both = verdict(run(), [job("apply"), job("workers")], pm)

    assert (workers_only, both) == ("served workers only", "deployed")


def test_rejects_a_deployed_job_that_is_not_a_serving_job() -> None:
    phases = {"moves": {"apply": "app"}, "serving": {"apply": "app"}, "deployed": ["boxes"]}

    with pytest.raises(HarnessError, match=r"deploy\.phases\.deployed names boxes"):
        phase_map({"phases": phases})


def test_counts_a_successful_noop_phase_as_no_move() -> None:
    pm = PhaseMap(moves={"migrate": "database"}, serving={}, noop=frozenset({"migrate"}))

    assert verdict(run(), [job("gate"), job("migrate")], pm) == "nothing to deploy"


def test_folds_called_workflow_and_matrix_jobs_into_one_phase() -> None:
    names = ["deploy / verify / lint", "deploy / build (a)", "deploy / build (b)", "rollout"]

    folded = [group_of(name, "deploy / ") for name in names]

    assert folded == ["verify", "build", "build", "rollout"]


def test_measures_a_matrix_phase_as_wall_clock_over_jobs_that_ran() -> None:
    jobs = [
        job("build (a)", started_at="2026-11-12T10:00:00Z", completed_at="2026-11-12T10:11:29Z"),
        job("build (b)", started_at="2026-11-12T10:00:30Z", completed_at="2026-11-12T10:05:00Z"),
        job(
            "build (c)",
            "skipped",
            started_at="2026-11-12T09:59:00Z",
            completed_at="2026-11-12T09:58:59Z",
        ),
    ]
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)

    assert group_span(jobs, now) == 689


def test_reads_a_run_in_flight_as_running_at_its_phase() -> None:
    pm = phase_map(FIVE_PHASES)
    building = parse_jobs(
        {
            "jobs": [
                {"name": "gate", "status": "completed", "conclusion": "success"},
                {"name": "build", "status": "in_progress", "conclusion": None},
            ]
        }
    )[0]

    assert verdict(run(status="in_progress", conclusion=""), building, pm) == "running at build"


def test_falls_back_on_the_run_conclusion_for_a_run_without_jobs() -> None:
    pm = phase_map(FIVE_PHASES)

    assert verdict(run(conclusion="cancelled"), [], pm) == "cancelled, no job says where"


def test_orders_phases_by_their_first_start() -> None:
    jobs = recorded_jobs("19000000101")

    assert [name for name, _ in grouped(jobs)] == ["gate", "build", "migrate", "apply", "verify"]


def test_maps_a_run_to_an_environment_by_each_match_key() -> None:
    envs = environments(
        {
            "environments": {
                "production": {
                    "match": [
                        {"branch": "env/production"},
                        {"branch": "main", "since": "2026-01-01T00:00:00Z"},
                    ]
                },
                "staging": {"match": {"runName": "deploy staging*", "workflow": "deploy.yml"}},
            }
        }
    )
    production, staging = envs
    old_main = run(headBranch="main", createdAt="2025-12-01T00:00:00Z")
    new_main = run(headBranch="main", createdAt="2026-02-01T00:00:00Z")
    branch = run(headBranch="env/production", createdAt="2025-12-01T00:00:00Z")
    named = run(displayTitle="deploy staging", headBranch="feature")

    assert [run_matches(production, r) for r in (old_main, new_main, branch, named)] == [
        False,
        True,
        True,
        False,
    ]
    assert run_matches(staging, named) is True
    assert run_matches(staging, new_main) is False


def test_maps_a_run_to_an_environment_by_its_trigger_event() -> None:
    (production,) = environments(
        {"environments": {"production": {"match": {"event": ["workflow_dispatch", "release"]}}}}
    )
    dispatched = run(event="workflow_dispatch")
    pushed = run(event="push")

    assert (run_matches(production, dispatched), run_matches(production, pushed)) == (True, False)


def test_gives_one_environment_over_every_run_without_config() -> None:
    assert environments({}) == [Environment("all runs", ())]


def test_rejects_an_unknown_match_key() -> None:
    with pytest.raises(HarnessError, match=r"unknown keys \['ref'\]"):
        environments({"environments": {"production": {"match": {"ref": "main"}}}})


def test_keeps_named_steps_and_drops_runner_and_short_unnamed_steps() -> None:
    gate = recorded_jobs("19000000101")[0]

    rows = step_rows(gate)

    assert [(r.name, r.seconds) for r in rows] == [
        ("setup-node", 15),
        ("install", 50),
        ("changes", 1),
        ("verify", 134),
    ]


def test_rejects_a_phase_value_that_is_not_a_string() -> None:
    with pytest.raises(HarnessError, match=r"deploy\.phases\.moves\.build must be a string"):
        phase_map({"phases": {"moves": {"build": 1}}})


def test_drops_the_set_up_and_complete_runner_steps() -> None:
    verify = check_banner_jobs()["verify"]

    rows = step_rows(verify)

    assert [(r.name, r.seconds) for r in rows] == [("npm ci && npm run verify", 360)]


def test_keeps_zero_seconds_for_a_named_step_that_took_no_time() -> None:
    host = check_banner_jobs()["deploy-host"]

    rows = step_rows(host)

    assert [(r.name, r.seconds) for r in rows] == [
        ("Check out", 38),
        ("Build", 60),
        ("Deploy", 72),
        ("Verify", 0),
    ]
