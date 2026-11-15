import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_harness import HarnessError, config
from agent_harness.deploy.serving import Done
from agent_harness.deploy.status import report, window_hours

RECORDED = Path(__file__).resolve().parents[3] / "fixtures" / "deploy" / "github"
FIVE_PHASE = RECORDED / "five-phase-workflow"
CHECK_BANNERS = RECORDED / "check-banners"
ROLLOUT_BANNERS = RECORDED / "rollout-banners"
NOW = datetime(2026, 11, 12, 18, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64

# The [deploy] section of a deploy workflow with the jobs gate, build, migrate, apply and verify.
FIVE_PHASE_CONFIG = """
[deploy]
version = "digest"
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
"""


class FakeGh:
    """Answers `gh` from the recorded fixtures and keeps every call."""

    def __init__(
        self,
        drop: tuple[str, ...] = (),
        add: tuple[dict[str, Any], ...] = (),
        case: Path = FIVE_PHASE,
        logs: dict[str, str] | None = None,
        failures: dict[str, str] | None = None,
    ) -> None:
        self.drop = drop
        self.add = add
        self.case = case
        self.logs = logs or {}
        # The error text that a call fails with, by its first two words, such as "run list".
        self.failures = failures or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        failure = self.failures.get(" ".join(args[:2]))
        if failure is not None:
            raise HarnessError(failure)
        if args[0] == "api" and args[1].startswith("repos/") and "/actions/" not in args[1]:
            return json.dumps({"full_name": "example-org/example-app"})
        if args[:2] == ["run", "list"]:
            runs = [*self.add, *json.loads((self.case / "runs.json").read_text())]
            return json.dumps([r for r in runs if str(r["databaseId"]) not in self.drop])
        if args[0] == "api":
            found = re.search(r"/runs/(\d+)/jobs", args[1])
            assert found is not None
            if any(str(r["databaseId"]) == found.group(1) for r in self.add):
                return json.dumps({"total_count": 0, "jobs": []})
            return (self.case / f"jobs-{found.group(1)}.json").read_text()
        if args[:2] == ["run", "view"]:
            if args[3] not in self.logs:
                raise HarnessError("gh run view exited 1: HTTP 410: the log has expired")
            return self.logs[args[3]]
        if args[:2] == ["auth", "status"]:
            return ""
        raise AssertionError(f"unexpected gh call {args}")


def lw_status(problems: list[str]) -> Done:
    node = {"node": "node-1", "release": "r7", "digest": DIGEST, "healthy": True, "containers": []}
    body = {
        "target": "production",
        "nodes": [node],
        "last": None,
        "ok": not problems,
        "problems": problems,
    }
    return Done(1 if problems else 0, json.dumps(body), "")


def write_config(root: Path, text: str) -> None:
    (root / ".agents").mkdir()
    (root / ".agents" / "harness.toml").write_text(text)


def run_report(
    root: Path, *argv: str, gh: FakeGh | None = None, problems: list[str] | None = None
) -> str:
    out = io.StringIO()

    def commands(args: list[str], cwd: Path) -> Done:
        return lw_status(problems or [])

    code = report(list(argv), root, out, gh=gh or FakeGh(), run=commands, now=NOW)
    assert code == 0
    return out.getvalue()


def test_reports_the_failed_migrate_as_the_last_deploy(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)

    text = run_report(tmp_path)

    assert "  failed at migrate. Moved: image\n" in text
    assert "run 19000000103  failure  commit c3c3c3c3" in text
    assert re.search(r"│ nodes +│ apply +│ a1a1a1a1 +│ +- +│ 19000000101 +│", text)


def test_steps_over_a_docs_only_run_to_the_last_deploy(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)

    text = run_report(tmp_path, gh=FakeGh(drop=("19000000103",)))

    assert "  1 newer run(s) moved nothing: nothing to deploy (1).\n" in text
    assert "  deployed. Moved: image, database (possible no-op), nodes\n" in text


def test_prints_every_verdict_in_the_history_view(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)

    text = run_report(tmp_path, "--history", "2d")

    statuses = re.findall(r"│ (failed at migrate|nothing to deploy|deployed) +│", text)
    assert statuses == ["failed at migrate", "nothing to deploy", "deployed"]
    assert "  1 of 3 run(s) deployed.\n" in text


def test_prints_the_verdicts_as_json(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)

    doc = json.loads(run_report(tmp_path, "--history", "2d", "--json"))

    (env,) = doc["environments"]
    assert env["environment"] == "production"
    assert [(r["run"], r["verdict"]) for r in env["runs"]] == [
        ("19000000103", "failed at migrate"),
        ("19000000102", "nothing to deploy"),
        ("19000000101", "deployed"),
    ]
    assert env["targets"][0]["rows"][0]["version"] == "r7"


def test_warns_when_a_deployed_run_disagrees_with_its_target(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)

    text = run_report(
        tmp_path, gh=FakeGh(drop=("19000000103",)), problems=["node-1 is not healthy"]
    )

    assert "warning: the run says deployed, and target production reports 1 problem(s)." in text
    assert "  problem: node-1 is not healthy\n" in text


def test_opens_each_phase_into_its_steps_with_the_steps_flag(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)

    doc = json.loads(run_report(tmp_path, "--steps", "--json", gh=FakeGh(drop=("19000000103",))))

    phases = [(p["name"], p["indent"]) for p in doc["environments"][0]["phases"]]
    assert phases[:3] == [("gate", 0), ("setup-node", 2), ("install", 2)]
    assert ("lw deploy apply", 2) in phases


def write_release(root: Path) -> None:
    (root / "deploy" / "releases").mkdir(parents=True)
    line = {
        "id": "r8",
        "image": "ghcr.io/o/app:v8",
        "digest": DIGEST,
        "at": "2026-11-12T17:00:00Z",
        "status": "applied",
    }
    (root / "deploy" / "releases" / "production.jsonl").write_text(json.dumps(line) + "\n")


def test_shows_a_workstation_release_record_as_a_deploy(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)
    write_release(tmp_path)

    doc = json.loads(run_report(tmp_path, "--json"))

    latest = doc["environments"][0]["latest"]
    assert (latest["run"], latest["record"], latest["verdict"]) == (
        "workstation",
        "production.jsonl",
        "deployed",
    )
    assert latest["version"] == DIGEST


def test_labels_a_workstation_deploy_alike_in_the_header_and_the_serving_table(
    tmp_path: Path,
) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)
    write_release(tmp_path)

    text = run_report(tmp_path)

    assert "  run workstation  success  commit -" in text
    assert re.search(r"│ nodes +│ apply +│ - +│ sha256:a+ +│ workstation +│", text)


def test_prints_a_dash_and_a_footer_when_the_version_command_fails(tmp_path: Path) -> None:
    command = FIVE_PHASE_CONFIG.replace(
        'version = "digest"', 'version = "command"\nversionCommand = ["tools/version.sh"]'
    )
    write_config(tmp_path, command)
    out = io.StringIO()

    def commands(args: list[str], cwd: Path) -> Done:
        if args[0] == "tools/version.sh":
            return Done(3, "", "no such commit\n")
        return lw_status([])

    report(["--history", "2d"], tmp_path, out, gh=FakeGh(), run=commands, now=NOW)

    text = out.getvalue()
    assert re.findall(
        r"│ (\S+) +│ +[0-9hms ]+│ (?:failed at migrate|nothing to deploy|deployed)", text
    ) == [
        "-",
        "-",
        "-",
    ]
    assert (
        "  the version lookup failed for 3 commit(s), so each reads -. The first: "
        "tools/version.sh exited 3 for c3c3c3c3: no such commit\n"
    ) in text


def test_names_a_stuck_queued_run_with_its_age_when_no_run_moved_anything(
    tmp_path: Path,
) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)
    queued = {
        "attempt": 1,
        "conclusion": "",
        "createdAt": "2026-11-10T08:00:00Z",
        "databaseId": 19000000104,
        "displayTitle": "deploy production",
        "headBranch": "main",
        "headSha": "d4" * 20,
        "status": "queued",
    }

    text = run_report(tmp_path, gh=FakeGh(drop=("19000000101", "19000000103"), add=(queued,)))

    assert "  none of the last 2 runs moved anything: nothing to deploy (1), queued (1).\n" in text
    assert "  run 19000000104 queued for 58h 00m, since 2026-11-10 08:00 UTC.\n" in text


def test_refuses_a_repository_that_is_not_owner_and_name(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG.replace("[deploy.ci]", '[deploy.ci]\nrepo = "app"'))

    with pytest.raises(HarnessError, match=r"deploy\.ci\.repo must be <owner>/<name>, not 'app'"):
        run_report(tmp_path)


# The [deploy] section of a deploy whose host job prints a banner as each part of it starts.
HOST_CONFIG = """
[deploy]
version = "digest"

[deploy.phases.moves]
deploy-host = "hosts"

[deploy.phases.serving]
deploy-host = "hosts"

[deploy.logPhases]
deploy-host = ['^== (\\w+)$']
"""


def host_line(at: str, text: str) -> str:
    return f"deploy-host\tUNKNOWN STEP\t2026-11-12T{at}.0000000Z {text}"


def test_puts_each_log_phase_under_the_step_that_printed_it(tmp_path: Path) -> None:
    write_config(tmp_path, HOST_CONFIG)
    log = "\n".join(
        [
            host_line("13:17:50", "== pull"),
            host_line("13:18:10", "== migrate"),
            host_line("13:18:50", "== switch"),
            host_line("13:19:58", "Cleaning up orphan processes"),
        ]
    )
    gh = FakeGh(case=CHECK_BANNERS, logs={"54000000202": log})

    doc = json.loads(run_report(tmp_path, "--steps", "--json", gh=gh))

    phases = [(p["name"], p["seconds"], p["indent"]) for p in doc["environments"][0]["phases"]]
    start = phases.index(("Build", 60, 2))
    assert phases[start:] == [
        ("Build", 60, 2),
        ("pull", 20, 4),
        ("migrate", 34, 4),
        ("Deploy", 72, 2),
        ("switch", 66, 4),
        ("Verify", 0, 2),
    ]


def test_adds_a_problem_line_for_a_log_that_cannot_be_read(tmp_path: Path) -> None:
    write_config(tmp_path, HOST_CONFIG)

    text = run_report(tmp_path, "--steps", gh=FakeGh(case=CHECK_BANNERS))

    assert re.search(r"│   Deploy +│ success +│ +1m 12s │", text)
    assert (
        "  problem: the log of deploy-host could not be read: "
        "gh run view exited 1: HTTP 410: the log has expired\n"
    ) in text


# A rollout job whose script prints a banner as each phase starts and one final line.
ROLLOUT_CONFIG = """
[deploy]
version = "digest"

[deploy.ci]
workflows = ["production.yml"]
jobPrefix = "deploy / "

[deploy.phases.moves]
rollout = "fleet"

[deploy.phases.serving]
rollout = "fleet"

[deploy.logPhases.rollout]
banners = ['^== (step \\d/2: .*)$']
final = '^== rollout: '
fatal = '^xx (.*)$'
aside = '^== (note: .*)$'
"""


def test_gives_log_phases_a_status_and_closes_the_last_at_the_final_banner(
    tmp_path: Path,
) -> None:
    write_config(tmp_path, ROLLOUT_CONFIG)
    log = (ROLLOUT_BANNERS / "log-54000000301.txt").read_text()
    gh = FakeGh(case=ROLLOUT_BANNERS, logs={"54000000301": log})

    doc = json.loads(run_report(tmp_path, "--steps", "--json", gh=gh))

    phases = [(p["name"], p["status"], p["seconds"]) for p in doc["environments"][0]["phases"]]
    start = phases.index(("Roll out", "success", 600))
    assert phases[start + 1 :] == [
        ("step 1/2: build", "success", 200),
        ("note: the release notes are ready", "-", None),
        ("step 2/2: deploy", "success", 380),
    ]


def test_prints_the_log_summary_line_under_the_run_header(tmp_path: Path) -> None:
    summary = "\n[deploy.logSummary]\nrollout = '^== rollout: (.*)$'\n"
    write_config(tmp_path, ROLLOUT_CONFIG + summary)
    log = (ROLLOUT_BANNERS / "log-54000000301.txt").read_text()
    gh = FakeGh(case=ROLLOUT_BANNERS, logs={"54000000301": log})

    text = run_report(tmp_path, gh=gh)

    assert (
        "  deploy-production\n  rollout: production is on release r2\n"
    ) in text


def write_job_record(root: Path, **fields: Any) -> None:
    (root / "deploy" / "releases").mkdir(parents=True)
    record = {
        "environment": "production",
        "sha": "f6" * 20,
        "branch": "main",
        "startedAt": "2026-11-12T17:00:00Z",
        "finishedAt": "2026-11-12T17:09:00Z",
        "jobs": {"build": {"result": "success", "seconds": 300}, "apply": {"result": "success"}},
        **fields,
    }
    (root / "deploy" / "releases" / "one.json").write_text(json.dumps(record))


def test_reads_the_record_digest_from_a_nested_key(tmp_path: Path) -> None:
    write_config(
        tmp_path, FIVE_PHASE_CONFIG.replace("[deploy]", '[deploy]\nrecordDigest = "digests.origin"')
    )
    write_job_record(tmp_path, digests={"origin": DIGEST, "media": None})

    doc = json.loads(run_report(tmp_path, "--json"))

    assert doc["environments"][0]["latest"]["version"] == DIGEST


def test_falls_back_on_the_record_build_number_for_a_commit_without_a_version(
    tmp_path: Path,
) -> None:
    keys = (
        'version = "command"\nversionCommand = ["tools/version.sh"]\nrecordVersion = "buildNumber"'
    )
    write_config(tmp_path, FIVE_PHASE_CONFIG.replace('version = "digest"', keys))
    write_job_record(tmp_path, buildNumber="1234")
    out = io.StringIO()

    def commands(args: list[str], cwd: Path) -> Done:
        if args[0] == "tools/version.sh":
            return Done(0, "", "")
        return lw_status([])

    report(["--json"], tmp_path, out, gh=FakeGh(), run=commands, now=NOW)

    assert json.loads(out.getvalue())["environments"][0]["latest"]["version"] == "1234"


def test_cautions_that_a_newer_red_serving_job_can_still_have_rolled_out(
    tmp_path: Path,
) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)
    write_job_record(
        tmp_path, jobs={"build": {"result": "success"}, "apply": {"result": "failure"}}
    )

    text = run_report(tmp_path)

    assert re.search(r"│ nodes +│ apply +│ a1a1a1a1 +│ +- +│ 19000000101 +│", text)
    assert (
        "  caution: 1 newer run(s) failed at apply, and a red apply can still have rolled out "
        "nodes. Read the targets: workstation.\n"
    ) in text


def test_refuses_an_environment_that_the_config_does_not_name(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)

    with pytest.raises(
        HarnessError, match=r"--env staging is not in deploy.environments \(known: production\)"
    ):
        run_report(tmp_path, "--env", "staging")


def test_registers_the_deploy_defaults(tmp_path: Path) -> None:
    section: dict[str, Any] = config.load(tmp_path).get("deploy")

    assert section["ci"] == {
        "provider": "github",
        "workflows": ["deploy.yml"],
        "jobPrefix": "",
        "repo": "",
    }
    assert section["phases"] == {"moves": {}, "serving": {}, "noop": [], "deployed": []}
    assert (section["version"], section["records"], section["targets"]) == ("git-count", "", {})


@pytest.mark.parametrize(("text", "hours"), [("24h", 24), ("7d", 168), ("72", 72)])
def test_reads_a_history_window_in_hours_or_days(text: str, hours: int) -> None:
    assert window_hours(text) == hours


def test_names_the_gh_account_when_it_cannot_see_the_repository(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)
    route = "api repos/{owner}/{repo}"
    gh = FakeGh(failures={route: "gh api exited 1: gh: Not Found (HTTP 404)"})

    with pytest.raises(HarnessError) as refused:
        run_report(tmp_path, gh=gh)

    assert str(refused.value).startswith(
        "the active gh account cannot see the repository of this checkout. Check the gh identity"
        " of this project"
    )
    assert ["run", "list"] not in [call[:2] for call in gh.calls]


WORKFLOW_404 = "gh run list exited 1: HTTP 404: workflow deploy.yml not found on the default branch"


def test_says_no_deploy_workflow_is_configured_with_the_default_config(tmp_path: Path) -> None:
    out = io.StringIO()
    gh = FakeGh(failures={"run list": WORKFLOW_404})

    code = report([], tmp_path, out, gh=gh, run=lambda args, cwd: lw_status([]), now=NOW)

    assert code == 0
    assert out.getvalue() == (
        "deploy-status: no deploy workflow configured. deploy.ci.workflows keeps its default"
        " (deploy.yml), and the repository has no such workflow. Set deploy.ci.workflows in"
        " .agents/harness.toml to the file names of the deploy workflows.\n"
    )


def test_fails_on_a_missing_workflow_that_the_config_names(tmp_path: Path) -> None:
    write_config(tmp_path, FIVE_PHASE_CONFIG)

    with pytest.raises(HarnessError, match=r"workflow deploy\.yml not found"):
        run_report(tmp_path, gh=FakeGh(failures={"run list": WORKFLOW_404}))
