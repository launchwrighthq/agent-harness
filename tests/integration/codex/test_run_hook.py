import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ADAPTER = ".claude/agent-harness/codex/run-hook.py"
RM_GUARD = ".claude/agent-harness/hooks/rm_guard.py"
REVEAL_GUARD = ".claude/agent-harness/hooks/reveal_guard.py"
REVEAL_REASON = "This command prints the value of API_KEY."

# A hook that asks the user to confirm a `--reveal`, as the read guard does.
REVEAL_HOOK = """
[[hook]]
id = "reveal-guard"
summary = "Ask before a reveal"
events = ["PreToolUse"]
matcher = "Bash"
script = "hooks/reveal_guard.py"

[[hook.fixture]]
tool = "Bash"
command = "show API_KEY --reveal"
expect = "ask"
contains = "prints the value of API_KEY"
"""
REVEAL_SCRIPT = f"""\
import json, sys
payload = json.load(sys.stdin)
if "--reveal" in payload.get("tool_input", {{}}).get("command", ""):
    print(json.dumps({{"hookSpecificOutput": {{"hookEventName": "PreToolUse",
        "permissionDecision": "ask", "permissionDecisionReason": {REVEAL_REASON!r}}}}}))
"""


def clean_env(**extra: str) -> dict[str, str]:
    drop = ("AH_HANDED_OVER", "AH_ROOT")
    env = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("CLAUDE_")}
    env.update(extra)
    return env


def install_demo(repo: Path, package: Path) -> Path:
    """Install the package into `repo/demo`, a project in a subfolder of the checkout."""
    demo = repo / "demo"
    demo.mkdir()
    done = subprocess.run(
        [
            sys.executable,
            str(package / "bin" / "ah"),
            "install",
            "--project",
            "--from",
            str(package),
        ],
        cwd=repo,
        env=clean_env(AH_ROOT=str(demo)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return demo


def add_reveal_guard(package: Path) -> None:
    with (package / "harness.toml").open("a", encoding="utf-8") as manifest:
        manifest.write(REVEAL_HOOK)
    (package / "hooks" / "reveal_guard.py").write_text(REVEAL_SCRIPT, encoding="utf-8")


def run_adapter(
    project: Path, env: dict[str, str], script: str = RM_GUARD, command: str = "rm -rf build"
) -> subprocess.CompletedProcess[str]:
    """Send a Codex `exec_command` of `command` to the hook `script` through the adapter."""
    payload = {
        "session_id": "codex-test",
        "cwd": str(project),
        "hook_event_name": "PreToolUse",
        "tool_name": "exec_command",
        "tool_input": {"cmd": command, "workdir": str(project)},
    }
    return subprocess.run(
        [sys.executable, str(project / ADAPTER), script, "PreToolUse"],
        cwd=project,
        env=env,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


def decision(done: subprocess.CompletedProcess[str]) -> str:
    assert done.returncode == 0, done.stderr
    return str(json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"])


def test_runs_the_hook_of_a_subfolder_project_that_ah_root_names(repo: Path, package: Path) -> None:
    demo = install_demo(repo, package)

    done = run_adapter(demo, clean_env(AH_ROOT=str(demo)))

    assert decision(done) == "deny"


def test_runs_the_hook_of_a_subfolder_project_without_ah_root(repo: Path, package: Path) -> None:
    demo = install_demo(repo, package)

    done = run_adapter(demo, clean_env())

    assert decision(done) == "deny"


def test_refuses_an_ah_root_that_names_another_folder(repo: Path, package: Path) -> None:
    demo = install_demo(repo, package)
    other = repo / "other"
    other.mkdir()

    done = run_adapter(demo, clean_env(AH_ROOT=str(other)))

    assert done.returncode == 1
    assert f"AH_ROOT names {other}, but this adapter belongs to {demo}" in done.stderr
    assert done.stdout == ""


def test_refuses_an_adapter_copy_in_a_subfolder_that_is_no_project(
    repo: Path, package: Path
) -> None:
    demo = install_demo(repo, package)
    stray = repo / "stray"
    shutil.copytree(demo / ".claude/agent-harness", stray / ".claude/agent-harness", symlinks=True)
    (stray / ".claude/agent-harness/lock.json").unlink()

    done = run_adapter(stray, clean_env())

    assert done.returncode == 1
    assert f"{stray} has no .agents/harness.toml or .claude/agent-harness/lock.json" in done.stderr
    assert done.stdout == ""


def test_passes_every_codex_fixture_of_a_subfolder_project(repo: Path, package: Path) -> None:
    demo = install_demo(repo, package)

    done = subprocess.run(
        [sys.executable, str(demo / ".claude/agent-harness/bin/ah"), "selftest"],
        cwd=repo,
        env=clean_env(AH_ROOT=str(demo)),
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines()[-1] == "ah selftest: 7 of 7 fixture runs pass"
    assert "ok   rm-guard codex Bash rm -rf build" in done.stdout


def test_turns_an_ask_into_a_deny_that_keeps_the_reason(repo: Path, package: Path) -> None:
    add_reveal_guard(package)
    demo = install_demo(repo, package)

    done = run_adapter(demo, clean_env(), REVEAL_GUARD, "show API_KEY --reveal")

    output = json.loads(done.stdout)["hookSpecificOutput"]
    assert decision(done) == "deny"
    assert output["permissionDecisionReason"] == (
        f"{REVEAL_REASON} Codex does not confirm a hook ask, so the Codex adapter denies the"
        " command. To see the value, the user runs the reveal themselves, or confirms it in"
        " Claude Code."
    )


def test_passes_an_ask_fixture_as_an_ask_in_claude_and_a_deny_in_codex(
    repo: Path, package: Path
) -> None:
    add_reveal_guard(package)
    demo = install_demo(repo, package)

    done = subprocess.run(
        [sys.executable, str(demo / ".claude/agent-harness/bin/ah"), "selftest"],
        cwd=repo,
        env=clean_env(AH_ROOT=str(demo)),
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok   reveal-guard claude Bash show API_KEY --reveal" in done.stdout
    assert "ok   reveal-guard codex Bash show API_KEY --reveal" in done.stdout
    assert done.stdout.splitlines()[-1] == "ah selftest: 9 of 9 fixture runs pass"
