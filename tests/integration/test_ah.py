import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "bin" / "ah"

# A stand-in project engine: it prints which engine ran, its arguments and the guard.
PROJECT_ENGINE = """#!/usr/bin/env python3
import json, os, sys
print(json.dumps({"engine": "project", "argv": sys.argv[1:],
                  "guard": os.environ.get("AH_HANDED_OVER")}))
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    return root


def install_project_engine(repo: Path) -> None:
    engine = repo / ".claude" / "agent-harness" / "bin" / "ah"
    engine.parent.mkdir(parents=True)
    engine.write_text(PROJECT_ENGINE, encoding="utf-8")


def run_ah(cwd: Path, *args: str, guard: bool = False) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "AH_HANDED_OVER"}
    if guard:
        env["AH_HANDED_OVER"] = "1"
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_hands_over_to_the_project_engine_of_the_repository(repo: Path) -> None:
    install_project_engine(repo)

    done = run_ah(repo, "config", "get", "specs.out")

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {
        "engine": "project",
        "argv": ["config", "get", "specs.out"],
        "guard": "1",
    }


def test_runs_its_own_engine_for_install_inside_a_project_install(repo: Path) -> None:
    install_project_engine(repo)

    done = run_ah(repo, "install", "--help")

    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("usage: ah install")


def test_runs_its_own_engine_in_a_repository_without_a_project_install(repo: Path) -> None:
    done = run_ah(repo, "config", "get", "specs.out")

    assert done.returncode == 0, done.stderr
    assert done.stdout == "dist\n"


def test_runs_its_own_engine_when_the_guard_is_set(repo: Path) -> None:
    install_project_engine(repo)

    done = run_ah(repo, "config", "get", "specs.out", guard=True)

    assert done.returncode == 0, done.stderr
    assert done.stdout == "dist\n"


def test_reads_the_config_of_the_repository_it_runs_in(repo: Path) -> None:
    (repo / ".agents").mkdir()
    (repo / ".agents" / "harness.toml").write_text('[harness]\nbaseBranch = "release"\n')
    (repo / "sub").mkdir()

    done = run_ah(repo / "sub", "config", "get", "harness.baseBranch")

    assert done.returncode == 0, done.stderr
    assert done.stdout == "release\n"
