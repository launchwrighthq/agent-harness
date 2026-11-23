"""local-run-guard reads its hint and the gate command from the config."""

import subprocess
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


def test_refuses_only_a_runner_whose_folder_is_in_the_project(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project", CONFIG)
    (project / "web").mkdir()
    other = hook_project.git_init(tmp_path / "other")

    def run(command: str, cwd: Path) -> int:
        payload = hook_project.bash_payload(command, cwd)
        return hook_project.run_hook(project, HOOK, "PreToolUse", payload).returncode

    codes = {
        (command, cwd.name): run(command, cwd)
        for command, cwd in (
            ("pytest tests", other),
            (f"cd {other} && pytest tests", project),
            (f"cd {project}/web && npx vitest run", other),
            (f"cd {project} && cd ../other && npx vitest run", other),
            (f"npm --prefix {project} test", other),
            (f"uv --directory {project} run pytest", other),
            (f"make -C {project} test", other),
            (f"bash -c 'cd {project} && pytest'", other),
            ('cd "$DIR" && pytest tests', other),
            (f"npm --prefix {other} test", project),
        )
    }

    assert codes == {
        ("pytest tests", "other"): 0,
        (f"cd {other} && pytest tests", "project"): 0,
        (f"cd {project}/web && npx vitest run", "other"): 2,
        (f"cd {project} && cd ../other && npx vitest run", "other"): 0,
        (f"npm --prefix {project} test", "other"): 2,
        (f"uv --directory {project} run pytest", "other"): 2,
        (f"make -C {project} test", "other"): 2,
        (f"bash -c 'cd {project} && pytest'", "other"): 2,
        ('cd "$DIR" && pytest tests', "other"): 2,
        (f"npm --prefix {other} test", "project"): 0,
    }


def test_counts_a_linked_worktree_as_its_main_checkout(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project", CONFIG)
    git = ["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "commit", "--quiet", "--allow-empty", "-m", "init"], check=True)
    linked = tmp_path / "linked"
    subprocess.run([*git, "worktree", "add", "--quiet", "-b", "w", str(linked)], check=True)
    payload = hook_project.bash_payload(f"cd {linked} && pytest", tmp_path)

    refused = hook_project.run_hook(project, HOOK, "PreToolUse", payload)

    assert refused.returncode == 2
