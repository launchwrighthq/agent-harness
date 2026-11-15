"""local-run-guard reads its hint and the gate command from the config."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import hook_project

HOOK = "local-run-guard.py"
CONFIG = """[harness]
gateCommand = "make gate"

[harness.localRuns]
hint = "`make gate` for the gate"
dispatch = ["ci test", "ci verify"]
"""


def test_refuses_a_runner_with_the_configured_hint_and_passes_the_gate(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project", CONFIG)

    refused = hook_project.run_hook(
        project, HOOK, "PreToolUse", hook_project.bash_payload("pytest tests", project)
    )
    gate = hook_project.run_hook(
        project, HOOK, "PreToolUse", hook_project.bash_payload("make gate", project)
    )

    assert refused.returncode == 2
    assert "Use `make gate` for the gate." in refused.stderr
    assert (gate.returncode, gate.stdout) == (0, "")


def test_opens_local_runs_for_a_turn_that_asks_for_one(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project", CONFIG)
    env = hook_project.hook_env(project, "local-session", tmp_path)
    prompt = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "s1",
        "prompt": "run the tests locally",
    }
    bash = {**hook_project.bash_payload("pytest tests", project), "session_id": "s1"}

    hook_project.run_hook(project, HOOK, "UserPromptSubmit", prompt, env)
    allowed = hook_project.run_hook(project, HOOK, "PreToolUse", bash, env)

    assert (allowed.returncode, allowed.stdout) == (0, "")


def test_passes_a_dispatch_command_and_refuses_it_with_the_local_target(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project", CONFIG)

    def run(command: str) -> int:
        payload = hook_project.bash_payload(command, project)
        return hook_project.run_hook(project, HOOK, "PreToolUse", payload).returncode

    codes = {
        command: run(command)
        for command in (
            "ci test queue",
            "ci verify",
            "npx ci test queue --target local",
            "ci verify --target=local",
        )
    }

    assert codes == {
        "ci test queue": 0,
        "ci verify": 0,
        "npx ci test queue --target local": 2,
        "ci verify --target=local": 2,
    }
