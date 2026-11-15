"""env-drift names an env file whose bytes moved since the configured store last received it."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import hook_project

from agent_harness.env import envfile

HOOK = "env-drift.py"
CONFIG = """[harness.envDrift]
envs = ["production"]
stores = [{ name = "vault", push = "make push ENV={env}" }]
"""
FAKE = b"API_KEY=fake-value\n"


def start(project: Path) -> str:
    done = hook_project.run_hook(project, HOOK, "SessionStart", {"hook_event_name": "SessionStart"})
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_names_the_environment_store_and_push_command_after_an_edit(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project", CONFIG)
    (project / ".env.production").write_bytes(FAKE + b"MORE=1\n")
    (project / ".env.production.pushed-vault").write_text(hashlib.sha256(FAKE).hexdigest())

    answer = json.loads(start(project))["hookSpecificOutput"]

    assert answer == {
        "hookEventName": "SessionStart",
        "additionalContext": (
            "env-drift: production holds local edits the vault store has never been given. "
            "`make push ENV=production` sends them."
        ),
    }


def test_stays_silent_after_a_reformat_and_speaks_after_a_value_edit(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project", CONFIG)
    (project / ".env.production.pushed-vault").write_text(
        envfile.content_digest("API_KEY=fake-value\nPOOL=5\n") + "\n"
    )
    (project / ".env.production").write_text("# Api\nPOOL=5\n\n# Keys\nAPI_KEY=fake-value\n")
    reformatted = start(project)
    (project / ".env.production").write_text("# Api\nPOOL=6\n\n# Keys\nAPI_KEY=fake-value\n")

    assert reformatted == ""
    assert "production holds local edits the vault store" in start(project)


def test_names_each_store_of_the_env_stores_list_with_its_push_command(tmp_path: Path) -> None:
    config = (
        '[harness]\nenvCommand = "lw env"\n\n'
        '[[env.stores]]\nname = "fleet"\nkind = "file"\npath = "vault/.env.{env}"\n'
    )
    project = hook_project.make_project(tmp_path / "project", config)
    (project / ".env.production").write_bytes(FAKE + b"MORE=1\n")
    (project / ".env.production.pushed-fleet").write_text(hashlib.sha256(FAKE).hexdigest())

    answer = json.loads(start(project))["hookSpecificOutput"]

    assert answer["additionalContext"] == (
        "env-drift: production holds local edits the fleet store has never been given. "
        "`lw env push production --store fleet` sends them."
    )


def test_stays_silent_when_the_marker_matches_or_is_missing(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project", CONFIG)
    (project / ".env.production").write_bytes(FAKE)
    silent_without_marker = start(project)
    (project / ".env.production.pushed-vault").write_text(hashlib.sha256(FAKE).hexdigest())

    assert (silent_without_marker, start(project)) == ("", "")
