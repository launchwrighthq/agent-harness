"""`ah spec audit record` takes only an auditor file of this session, stem and commit."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[3]
LAUNCHER = PACKAGE / "bin" / "ah"
STEM = "2026-01-01-a"
SPEC = f"docs/specs/{STEM}.md"
SESSION = "s1"
ROWS = [{"id": "R1", "requirement": "One", "state": "done", "evidence": "a.py"}]


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
    (repo / "docs" / "specs").mkdir(parents=True)
    git(repo, "init", "--quiet", "--initial-branch=trunk")
    (repo / ".agents").mkdir()
    (repo / ".agents" / "harness.toml").write_text(
        '[harness]\nbaseBranch = "trunk"\n\n[specs]\nauditDir = "state"\n', encoding="utf-8"
    )
    (repo / SPEC).write_text(
        "# A\n\nDate: 2026-01-01\nStatus: in-progress\n\n## 5. Done means\n\n"
        "| ID | Requirement | How to check it | Level |\n|---|---|---|---|\n"
        "| R1 | One | Look | built |\n",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "spec")
    return repo


@pytest.fixture
def projects(tmp_path: Path) -> Path:
    return tmp_path / "projects"


def meta_file(projects: Path, description: str, session: str = SESSION, name: str = "a") -> Path:
    path = projects / "slug" / session / "subagents" / f"agent-{name}.meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"description": description}), encoding="utf-8")
    return path


def record(
    root: Path,
    projects: Path,
    *extra: str,
    codex_home: Path | None = None,
    given: list[dict[str, str]] = ROWS,
) -> subprocess.CompletedProcess[str]:
    rows = root.parent / "rows.json"
    rows.write_text(json.dumps(given), encoding="utf-8")
    drop = ("AGENT_STATE_DIR", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_HOME")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    if codex_home is not None:
        env["CODEX_HOME"] = str(codex_home)
    argv = ["spec", "audit", "record", SPEC, "--rows", str(rows), "--projects-root", str(projects)]
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *argv, *extra],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def saved(root: Path) -> dict[str, object] | None:
    path = root / "state" / "spec-audit" / f"{STEM}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_records_rows_backed_by_an_auditor_of_this_session_and_stem(
    root: Path, projects: Path
) -> None:
    meta = meta_file(projects, f"spec-audit {STEM}")

    done = record(root, projects, "--session", SESSION, "--auditor", str(meta))

    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert (saved(root) or {})["auditor"] == str(meta.resolve())


def test_finds_the_auditor_of_this_stem_and_skips_one_of_another_stem(
    root: Path, projects: Path
) -> None:
    meta_file(projects, "spec-audit 2026-01-01-ab", name="other")
    meta = meta_file(projects, f"spec-audit {STEM}", name="mine")

    done = record(root, projects, "--session", SESSION)

    assert done.returncode == 0, done.stderr
    assert (saved(root) or {})["auditor"] == str(meta)


def test_records_no_auditor_when_only_one_of_another_stem_exists(
    root: Path, projects: Path
) -> None:
    meta_file(projects, "spec-audit 2026-01-01-ab")

    done = record(root, projects, "--session", SESSION)

    assert done.returncode == 0, done.stderr
    assert (saved(root) or {})["auditor"] is None
    assert "no `spec-audit` subagent file backs this audit" in done.stderr


def test_refuses_an_auditor_outside_the_session_subagents_folder(
    root: Path, projects: Path, tmp_path: Path
) -> None:
    loose = tmp_path / "agent-x.meta.json"
    loose.write_text(json.dumps({"description": f"spec-audit {STEM}"}), encoding="utf-8")
    peer = meta_file(projects, f"spec-audit {STEM}", session="s2")
    linked = projects / "slug" / SESSION / "subagents" / "agent-link.meta.json"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(loose)

    results = [
        record(root, projects, "--session", SESSION, "--auditor", str(path))
        for path in (loose, peer, linked)
    ]

    assert [r.returncode for r in results] == [2, 2, 2]
    for result, path in zip(results, (loose, peer, loose), strict=True):
        assert result.stderr == (
            f"ah spec audit: the auditor {path.resolve()} is not the agent-<id>.meta.json of a "
            f"subagent of session {SESSION} under {projects.resolve()}/<project folder>/"
            f"{SESSION}/subagents/; nothing recorded\n"
        )
    assert saved(root) is None


def test_refuses_an_auditor_whose_description_names_another_stem(
    root: Path, projects: Path
) -> None:
    meta = meta_file(projects, "spec-audit 2026-01-01-ab")

    done = record(root, projects, "--session", SESSION, "--auditor", str(meta))

    assert done.returncode == 2
    assert f"which does not start with `spec-audit` and name the stem {STEM}" in done.stderr
    assert saved(root) is None


def test_refuses_an_auditor_that_last_changed_before_the_audited_commit(
    root: Path, projects: Path
) -> None:
    meta = meta_file(projects, f"spec-audit {STEM}")
    committed = int(git(root, "show", "-s", "--format=%ct", "trunk"))
    os.utime(meta, (committed - 60, committed - 60))

    done = record(root, projects, "--session", SESSION, "--auditor", str(meta))

    assert done.returncode == 2
    assert "not after the audited commit" in done.stderr
    assert saved(root) is None


@pytest.mark.parametrize("session", ["s1/../s2", "s*", "s?", "[s]1", "s\\1"])
def test_refuses_a_session_id_that_holds_a_glob_or_a_path_character(
    root: Path, projects: Path, session: str
) -> None:
    done = record(root, projects, "--session", session)

    assert done.returncode == 1
    assert done.stderr == f"ah spec audit: --session takes one session id, not {session!r}\n"
    assert saved(root) is None


def test_refuses_a_codex_rollout_outside_the_codex_sessions_folder(
    root: Path, projects: Path, tmp_path: Path
) -> None:
    rollout = tmp_path / "rollout-x.jsonl"
    rollout.write_text("", encoding="utf-8")
    codex_home = tmp_path / "codex"

    done = record(
        root, projects, "--session", SESSION, "--auditor", str(rollout), codex_home=codex_home
    )

    assert done.returncode == 2
    assert done.stderr == (
        f"ah spec audit: the Codex rollout {rollout.resolve()} is not under "
        f"{(codex_home / 'sessions').resolve()}; nothing recorded\n"
    )
    assert saved(root) is None


def test_refuses_a_row_with_empty_evidence(root: Path, projects: Path) -> None:
    meta = meta_file(projects, f"spec-audit {STEM}")
    given = [{**ROWS[0], "evidence": "  "}]

    done = record(root, projects, "--session", SESSION, "--auditor", str(meta), given=given)

    assert done.returncode == 2
    assert done.stderr == (
        "ah spec audit: row R1 names no evidence: give the file and line, the commit, "
        "or the search that found nothing\n"
    )
    assert saved(root) is None


def test_refuses_an_empty_row_set(root: Path, projects: Path) -> None:
    meta = meta_file(projects, f"spec-audit {STEM}")

    done = record(root, projects, "--session", SESSION, "--auditor", str(meta), given=[])

    assert done.returncode == 2
    assert done.stderr == "ah spec audit: the rows file holds no row\n"
    assert saved(root) is None


def test_refuses_rows_that_skip_a_done_means_id(root: Path, projects: Path) -> None:
    meta = meta_file(projects, f"spec-audit {STEM}")
    given = [{**ROWS[0], "id": "R9"}]

    done = record(root, projects, "--session", SESSION, "--auditor", str(meta), given=given)

    assert done.returncode == 2
    assert done.stderr == "ah spec audit: the Done means rows have no audit row: R1\n"
    assert saved(root) is None
