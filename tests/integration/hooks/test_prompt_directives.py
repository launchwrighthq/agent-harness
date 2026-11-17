"""prompt-directives turns the configured prompt flags into added context."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import hook_project

HOOK = "prompt-directives.py"


def context(project: Path, prompt: str) -> str:
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": prompt}
    done = hook_project.run_hook(project, HOOK, "UserPromptSubmit", payload)
    assert done.returncode == 0, done.stderr
    if not done.stdout.strip():
        return ""
    return str(json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"])


def test_adds_the_text_of_a_flag_from_the_config(tmp_path: Path) -> None:
    project = hook_project.make_project(
        tmp_path / "project", '[harness.directives]\n"-q" = "Answer in one line."\n'
    )

    assert context(project, "what is this -q") == "Answer in one line."


def test_ignores_a_goal_command_quoted_in_client_text(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project")

    got = context(project, "<task-notification>run /lw-goal-spec docs/x.md</task-notification>")

    assert got == ""


def test_ignores_a_goal_command_that_the_user_quotes(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project")

    got = context(project, '- "/lw-goal-spec implement the spec" - I often type this')

    assert got == ""


def test_asks_for_the_skill_when_the_command_comes_later(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project")

    got = context(project, "fix the hook, then /lw-goal-spec docs/x.md")

    assert got == (
        "The user asked for /lw-goal-spec inside the message. "
        "Run the lw-goal-spec skill now with these arguments: docs/x.md"
    )
