"""The goal-spec hook reads the base branch and the audit folder from the config."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[3]
HOOK = PACKAGE / "hooks" / "goal-spec.py"
LAUNCHER = PACKAGE / "bin" / "ah"
SPEC = "docs/specs/2026-01-01-a.md"
SESSION = "s1"


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=trunk")
    (repo / ".agents").mkdir()
    (repo / ".agents" / "harness.toml").write_text(
        '[harness]\nbaseBranch = "trunk"\n\n[specs]\nauditDir = "state"\n', encoding="utf-8"
    )
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / SPEC).write_text(
        "# A\n\nDate: 2026-01-01\nStatus: in-progress\n\n## 5. Done means\n\n"
        "| ID | Requirement | How to check it | Level |\n|---|---|---|---|\n"
        "| R1 | One | Look | built |\n",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "spec")
    return repo


def clean_env() -> dict[str, str]:
    drop = ("AGENT_STATE_DIR", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
    return {k: v for k, v in os.environ.items() if k not in drop}


def run(root: Path, args: list[str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=root,
        input=stdin,
        capture_output=True,
        text=True,
        env=clean_env(),
        check=False,
    )


def stop(root: Path, transcript: Path) -> subprocess.CompletedProcess[str]:
    payload = {
        "session_id": SESSION,
        "cwd": str(root),
        "transcript_path": str(transcript),
        "last_assistant_message": "done",
    }
    return run(root, [str(HOOK), "Stop"], json.dumps(payload))


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    path = tmp_path / "projects" / "slug" / f"{SESSION}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    return path


def test_starts_a_goal_on_the_configured_base_branch(root: Path) -> None:
    done = run(root, [str(HOOK), "start", "--session", SESSION, SPEC])

    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "goal-spec active for 2026-01-01-a at level merged. "
        "The turn ends when the saved audit shows zero open rows.\n"
    )
    goal = json.loads((root / "state" / "goal-spec" / f"{SESSION}.json").read_text())
    assert goal["base"] == "trunk"
    assert goal["base_commit"] == git(root, "rev-parse", "trunk")


def test_holds_the_turn_until_an_audit_shows_zero_open_rows(
    root: Path, transcript: Path, tmp_path: Path
) -> None:
    run(root, [str(HOOK), "start", "--session", SESSION, SPEC])

    held = stop(root, transcript)

    assert held.returncode == 2
    assert held.stderr.startswith("goal-spec: not done (no-audit)\n2026-01-01-a: no audit\n")

    meta = transcript.parent / SESSION / "subagents" / "agent-x.meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"description": "spec-audit 2026-01-01-a"}), encoding="utf-8")
    rows = tmp_path / "rows.json"
    rows.write_text(
        json.dumps([{"id": "R1", "requirement": "One", "state": "done", "evidence": "a.py"}]),
        encoding="utf-8",
    )
    record = [str(LAUNCHER), "spec", "audit", "record", SPEC, "--rows", str(rows)]
    recorded = run(
        root,
        [
            *record,
            "--session",
            SESSION,
            "--auditor",
            str(meta),
            "--projects-root",
            str(tmp_path / "projects"),
        ],
    )
    assert recorded.returncode == 0, recorded.stderr
    assert "Audited trunk" in recorded.stdout

    released = stop(root, transcript)

    assert released.returncode == 0, released.stderr
    goal = json.loads((root / "state" / "goal-spec" / f"{SESSION}.json").read_text())
    assert goal["status"] == "met"
    assert goal["last_verdict"]["kind"] == "met"


def test_lets_the_turn_end_when_the_session_has_no_goal(root: Path, transcript: Path) -> None:
    done = stop(root, transcript)

    assert done.returncode == 0
    assert done.stderr == ""
