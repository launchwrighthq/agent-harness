import re
from pathlib import Path

from agent_harness.deploy.logs import LogRules, log_rows, log_rules

RECORDED = Path(__file__).resolve().parents[3] / "fixtures" / "deploy" / "github"
ROLLOUT = log_rules(
    {
        "banners": [r"^== (step \d/2: .*)$"],
        "final": "^== rollout: ",
        "fatal": "^xx (.*)$",
        "aside": "^== (note: .*)$",
    },
    "deploy.logPhases.rollout",
)


def test_splits_a_job_log_into_banner_phases() -> None:
    log = "\n".join(
        [
            "rollout\tRoll out\t\ufeff2026-11-12T10:00:00.1234567Z \x1b[36m== step 1/2 pull\x1b[0m",
            "rollout\tRoll out\t2026-11-12T10:00:40.0000000Z pulled",
            "rollout\tRoll out\t2026-11-12T10:01:00.0000000Z == step 2/2 switch",
            "a wrapped line with no time",
            "rollout\tRoll out\t2026-11-12T10:03:30.0000000Z done",
        ]
    )

    rows = log_rows(log, LogRules((re.compile(r"^== (step \d/\d)"),)))

    assert [(r.name, r.seconds) for r in rows] == [("step 1/2", 60), ("step 2/2", 150)]


def test_marks_the_last_phase_failed_and_names_the_fatal_line_without_a_final_banner() -> None:
    log = (RECORDED / "rollout-banners" / "log-died.txt").read_text()

    rows = log_rows(log, ROLLOUT)

    assert [(r.name, r.status, r.seconds) for r in rows][-2:] == [
        ("step 2/2: deploy", "failure", 211),
        ("died: node-2 did not come back", "failure", None),
    ]


def test_reads_the_duration_and_the_status_from_the_banner_text() -> None:
    log = (RECORDED / "check-banners" / "log-54000000201.txt").read_text()
    rules = log_rules(
        [r"^===== (?P<name>[a-z:]+): (?P<status>passed|FAILED).* in (?P<seconds>[\d.]+) s =====$"],
        "deploy.logPhases.verify",
    )

    rows = log_rows(log, rules)

    assert [(r.name, r.status, r.seconds) for r in rows] == [
        ("format:check", "success", 10),
        ("typecheck", "success", 25),
        ("lint", "success", 13),
        ("test", "success", 200),
        ("build", "success", 1),
        ("docs:check", "success", 1),
    ]
