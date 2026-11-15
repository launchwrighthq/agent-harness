import json
from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.deploy.phases import phase_map
from agent_harness.deploy.records import read_records, record_entry, record_folders

PHASES = phase_map(
    {"phases": {"moves": {"build": "image", "apply": "nodes"}, "serving": {"apply": "nodes"}}}
)


def release(release_id: str, at: str, status: str = "applied", rollback: bool = False) -> str:
    digest = "sha256:" + "d" * 64
    row = {"id": release_id, "image": "ghcr.io/o/app:v1", "digest": digest, "at": at}
    return json.dumps({**row, "status": status, "rollback": rollback})


def test_reads_a_release_log_as_records_of_its_target(tmp_path: Path) -> None:
    folder = tmp_path / "deploy" / "releases"
    folder.mkdir(parents=True)
    lines = [
        release("r1", "2026-11-10T10:00:00Z"),
        release("r2", "2026-11-11T10:00:00Z", status="stopped"),
        release("r1", "2026-11-11T11:00:00Z", rollback=True),
    ]
    (folder / "production.jsonl").write_text("\n".join(lines) + "\n")

    records = read_records(record_folders(tmp_path, "deploy/releases"))

    assert [(r.environment, r.id, r.status) for r in records] == [
        ("production", "r1", "applied"),
        ("production", "r2", "stopped"),
        ("production", "r1", "applied"),
    ]
    verdicts = [record_entry(r).verdict(PHASES) for r in records]
    assert verdicts == [
        "deployed (rollback to r1)",
        "failed (record status stopped)",
        "deployed",
    ]


def test_gives_a_job_record_the_verdict_of_its_phases(tmp_path: Path) -> None:
    (tmp_path / "deploys").mkdir()
    record = {
        "environment": "production",
        "sha": "e" * 40,
        "branch": "main",
        "startedAt": "2026-11-12T08:00:00Z",
        "finishedAt": "2026-11-12T08:09:00Z",
        "jobs": {
            "build": {"result": "success", "seconds": 300},
            "apply": {"result": "skipped", "seconds": None},
        },
    }
    (tmp_path / "deploys" / "one.json").write_text(json.dumps(record))

    (found,) = read_records(record_folders(tmp_path, "deploys"))
    entry = record_entry(found)

    assert entry.run.id == "workstation"
    assert entry.verdict(PHASES) == "moved image, no serving phase succeeded"


def test_fails_on_a_record_file_that_does_not_parse(tmp_path: Path) -> None:
    (tmp_path / "deploys").mkdir()
    (tmp_path / "deploys" / "bad.json").write_text("{not json")

    with pytest.raises(HarnessError, match=r"bad\.json is not a deploy record"):
        read_records(record_folders(tmp_path, "deploys"))


def test_reads_the_record_folder_of_the_main_checkout_from_a_worktree(tmp_path: Path) -> None:
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / ".git" / "worktrees" / "wt" / "commondir").write_text("../..\n")
    (main / "deploys").mkdir()
    (main / "deploys" / "staging.jsonl").write_text(release("r9", "2026-11-12T08:00:00Z") + "\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")

    records = read_records(record_folders(worktree, "deploys"))

    assert [(r.environment, r.id) for r in records] == [("staging", "r9")]
