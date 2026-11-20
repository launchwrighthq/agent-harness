"""Every hook fixture of `harness.toml` runs through its hook and gets the answer it expects."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))

import hook_project

MANIFEST = tomllib.loads((hook_project.PACKAGE / "harness.toml").read_text(encoding="utf-8"))
HOOKS = {hook["id"]: hook for hook in MANIFEST["hook"]}
WITH_FIXTURES = [hook_id for hook_id, hook in HOOKS.items() if hook.get("fixture")]

# What a decoy copy prints. An answer that holds it came from a hook outside the project.
DECOY_MARK = "agent-harness decoy ran"


def event_of(hook: dict[str, Any], fixture: dict[str, Any]) -> str | None:
    """A prompt fixture runs on UserPromptSubmit, a tool fixture on PreToolUse or PostToolUse."""
    events = hook["events"]
    if "prompt" in fixture:
        return "UserPromptSubmit" if "UserPromptSubmit" in events else None
    return next((e for e in ("PreToolUse", "PostToolUse") if e in events), None)


def payload_of(event: str, fixture: dict[str, Any], cwd: Path, session: str) -> dict[str, Any]:
    base = {"session_id": session, "cwd": str(cwd), "hook_event_name": event, "transcript_path": ""}
    if "prompt" in fixture:
        return {**base, "prompt": fixture["prompt"]}
    if fixture["tool"] == "Bash":
        tool_input = {"command": fixture["command"]}
    else:
        tool_input = {"file_path": str(cwd / fixture["file"])}
    return {**base, "tool_name": fixture["tool"], "tool_input": tool_input}


def plant_decoy(tmp: Path, script: str) -> Path:
    """A git checkout with its own launcher and hook copy, which only print the decoy mark."""
    decoy = hook_project.git_init(tmp / "decoy")
    answer = json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": DECOY_MARK}}
    )
    for rel in (hook_project.LAUNCHER, hook_project.INSTALLED / "hooks" / script):
        file = decoy / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"print({answer!r})\n" if rel.suffix == ".py" else f"cat >/dev/null\necho '{answer}'\n"
        )
        file.write_text(body, encoding="utf-8")
        file.chmod(0o755)
    return decoy


def judge(event: str, fixture: dict[str, Any], done: subprocess.CompletedProcess[str]) -> str:
    """Why the run did not answer as the fixture expects, or an empty string."""
    out: dict[str, Any] = {}
    if done.stdout.strip():
        try:
            out = json.loads(done.stdout)
        except ValueError as error:
            return f"stdout is not JSON ({error}): {done.stdout.strip()[:200]}"
    spec = out.get("hookSpecificOutput") or {}
    expect, contains = fixture["expect"], fixture.get("contains")
    if expect == "deny":
        if spec.get("permissionDecision") == "deny":
            reason = str(spec.get("permissionDecisionReason", ""))
        elif done.returncode == 2:
            reason = done.stderr
        else:
            shown = done.stdout.strip() or done.stderr.strip() or "no output"
            return f"expected a deny, got exit {done.returncode} and {shown}"
        if contains is not None and contains not in reason:
            return f'the deny reason does not say "{contains}": {reason}'
        return ""
    if expect == "ask":
        if spec.get("permissionDecision") != "ask":
            shown = done.stdout.strip() or done.stderr.strip() or "no output"
            return f"expected an ask, got exit {done.returncode} and {shown}"
        reason = str(spec.get("permissionDecisionReason", ""))
        if contains is not None and contains not in reason:
            return f'the ask reason does not say "{contains}": {reason}'
        return ""
    if expect == "context":
        if done.returncode != 0:
            return f"exit {done.returncode}: {done.stderr.strip()}"
        text = spec.get("additionalContext")
        if not isinstance(text, str) or text == "":
            return f"no additionalContext in {done.stdout.strip() or 'empty output'}"
        if spec.get("hookEventName") != event:
            return f"hookEventName is {spec.get('hookEventName')}, not {event}"
        if contains is not None and contains not in text:
            return f'the context does not say "{contains}"'
        return ""
    if done.returncode != 0 or done.stdout.strip():
        shown = done.stdout.strip() or done.stderr.strip()
        return f"expected silence, got exit {done.returncode} and {shown}"
    return ""


def label(fixture: dict[str, Any]) -> str:
    if "prompt" in fixture:
        text = f"prompt {fixture['prompt']!r}"
    elif fixture["tool"] == "Bash":
        text = f"Bash {fixture['command']!r}"
    else:
        text = f"{fixture['tool']} {fixture['file']}"
    return f"{text} (decoy workdir)" if fixture.get("decoy") else text


@pytest.mark.parametrize("hook_id", WITH_FIXTURES)
def test_answers_every_fixture_of_the_hook_as_expected(hook_id: str, tmp_path: Path) -> None:
    hook = HOOKS[hook_id]
    # One level down, so the decoy checkouts are no siblings that `harness.workspace.repos` trusts.
    project = hook_project.make_project(tmp_path / "work" / "project")
    script = str(Path(hook["script"]).relative_to("hooks"))
    failures: list[str] = []
    for n, fixture in enumerate(hook["fixture"]):
        event = event_of(hook, fixture)
        if event is None:
            failures.append(f"{label(fixture)}: the hook does not run on the fixture event")
            continue
        session = f"fixture-{hook_id}-{n}"
        cwd = plant_decoy(tmp_path / f"run-{n}", script) if fixture.get("decoy") else project
        env = hook_project.hook_env(project, session, tmp_path)
        done = hook_project.run_hook(
            project, script, event, payload_of(event, fixture, cwd, session), env
        )
        detail = (
            f"ran the hook copy of the decoy checkout {cwd}"
            if DECOY_MARK in done.stdout
            else judge(event, fixture, done)
        )
        if detail:
            failures.append(f"{label(fixture)}: {detail}")

    assert failures == []


def test_names_an_existing_file_for_every_hook_script_of_this_track() -> None:
    # The spec track writes render-spec.py and goal-spec.py; every other script is a hook here.
    others = {"render-spec", "goal-spec"}
    missing = [
        hook["script"]
        for hook_id, hook in HOOKS.items()
        if hook_id not in others and not (hook_project.PACKAGE / hook["script"]).is_file()
    ]

    assert missing == []
