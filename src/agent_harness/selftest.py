"""`ah selftest`: run every fixture of every enabled hook of the installed manifests.

Each fixture runs twice: as Claude Code runs the hook (the interpreter and the installed script)
and through the Codex adapter. The answer must have the shape that the client reads. The Codex
adapter turns an ask into a deny, so an `ask` fixture expects a deny through it.

The portability checks follow: each skill resolves through its `.agents/skills` link, and the
guards whose answer depends on the checkout run from a worktree subfolder of a scratch repository.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agent_harness import HarnessError
from agent_harness.install.plan import ENGINE_DIR, Plan, PlannedHook, interpreter_for
from agent_harness.install.registry import CODEX_ADAPTER, LINK_DIR
from agent_harness.manifest import Fixture

# What a decoy copy prints. An answer that holds it came from a hook outside the project.
DECOY_MARK = "lw-selftest decoy ran"


@dataclass(frozen=True)
class Result:
    hook: str
    client: str
    fixture: str
    # Why the case failed, empty when it passed.
    detail: str

    @property
    def ok(self) -> bool:
        return self.detail == ""

    def line(self) -> str:
        if self.ok:
            return f"ok   {self.hook} {self.client} {self.fixture}"
        return f"FAIL {self.hook} {self.client} {self.fixture}: {self.detail}"


def event_of(hook: PlannedHook, fixture: Fixture) -> str | None:
    """The event a fixture runs on.

    A prompt fixture is UserPromptSubmit. A tool fixture is PreToolUse, or PostToolUse for a hook
    that runs only after the tool.
    """
    if fixture.prompt is not None:
        return "UserPromptSubmit" if "UserPromptSubmit" in hook.events else None
    for event in ("PreToolUse", "PostToolUse"):
        if event in hook.events:
            return event
    return None


def label(f: Fixture) -> str:
    if f.prompt is not None:
        text = f"prompt {json.dumps(f.prompt)}"
    elif f.tool == "Bash":
        text = "Bash " + (f.command or "").replace("\n", "\\n")
    else:
        text = f"{f.tool or ''} {f.file or ''}"
    return f"{text} (decoy workdir)" if f.decoy else text


def claude_payload(event: str, f: Fixture, root: Path, session: str) -> dict[str, Any]:
    """The payload Claude Code sends."""
    base: dict[str, Any] = {
        "session_id": session,
        "cwd": str(root),
        "hook_event_name": event,
        "transcript_path": "",
    }
    if f.prompt is not None:
        return {**base, "prompt": f.prompt}
    tool_input = (
        {"command": f.command} if f.tool == "Bash" else {"file_path": str(root / (f.file or ""))}
    )
    return {**base, "tool_name": f.tool, "tool_input": tool_input}


def codex_payload(event: str, f: Fixture, root: Path, session: str) -> dict[str, Any]:
    """The payload Codex sends: `exec_command` with `cmd` for a shell, `apply_patch` for an edit."""
    base: dict[str, Any] = {"session_id": session, "cwd": str(root), "hook_event_name": event}
    if f.prompt is not None:
        return {**base, "prompt": f.prompt}
    if f.tool == "Bash":
        return {
            **base,
            "tool_name": "exec_command",
            "tool_input": {"cmd": f.command, "workdir": str(root)},
        }
    patch = f"*** Begin Patch\n*** Update File: {f.file or ''}\n@@\n-a\n+b\n*** End Patch\n"
    return {
        **base,
        "tool_name": "apply_patch",
        "tool_input": {"command": patch, "workdir": str(root)},
    }


def codex_expectation(f: Fixture) -> Fixture:
    """The fixture as the Codex adapter answers it: an `ask` becomes a deny with the same reason."""
    return replace(f, expect="deny") if f.expect == "ask" else f


def judge(event: str, f: Fixture, status: int | None, stdout: str, stderr: str) -> str:
    """Whether one run answered as the fixture expects. Returns the reason it did not, or ''."""
    out: Any = None
    if stdout.strip():
        try:
            out = json.loads(stdout)
        except json.JSONDecodeError as error:
            return f"stdout is not JSON ({error}): {stdout.strip()[:200]}"
    if not isinstance(out, dict):
        out = {} if out is None else {"value": out}
    spec = out.get("hookSpecificOutput")
    spec = spec if isinstance(spec, dict) else {}
    shown = stdout.strip()
    if f.expect == "deny":
        if spec.get("permissionDecision") == "deny":
            reason = str(spec.get("permissionDecisionReason") or "")
        elif status == 2:
            reason = stderr
        else:
            return f"expected a deny, got exit {status} and {shown or 'no output'}"
        if f.contains is not None and f.contains not in reason:
            return f'the deny reason does not say "{f.contains}": {reason}'
        return ""
    if f.expect == "ask":
        # An ask has one form: an exit 2 is a deny, never an ask.
        if spec.get("permissionDecision") != "ask":
            return f"expected an ask, got exit {status} and {shown or 'no output'}"
        reason = str(spec.get("permissionDecisionReason") or "")
        if f.contains is not None and f.contains not in reason:
            return f'the ask reason does not say "{f.contains}": {reason}'
        return ""
    if f.expect == "block":
        if status != 0:
            return f"exit {status}: {stderr.strip()}"
        reason_value = out.get("reason")
        if out.get("decision") != "block" or not isinstance(reason_value, str):
            return f'expected {{"decision": "block"}}, got {shown or "no output"}'
        if f.contains is not None and f.contains not in reason_value:
            return f'the block reason does not say "{f.contains}": {reason_value}'
        return ""
    if f.expect == "context":
        if status != 0:
            return f"exit {status}: {stderr.strip()}"
        if "prompt" in out:
            return 'printed {"prompt": ...}, which the client ignores'
        context = spec.get("additionalContext")
        if not isinstance(context, str) or context == "":
            return f"no hookSpecificOutput.additionalContext in {shown or 'empty output'}"
        if spec.get("hookEventName") != event:
            return f"hookEventName is {spec.get('hookEventName')}, not {event}"
        if f.contains is not None and f.contains not in context:
            return f'the context does not say "{f.contains}"'
        return ""
    if status != 0 or shown:
        return f"expected silence, got exit {status} and {shown}"
    return ""


def _plant_content(root: Path, f: Fixture) -> Path | None:
    """Write the `content` of a fixture at its `file`. Returns what to remove after the run.

    The file must not exist, so the self-test never overwrites a project file.
    """
    if f.content is None or f.file is None:
        return None
    file = root / f.file
    if file.exists():
        raise HarnessError(f"the fixture file {f.file} exists; the self-test writes it, so move it")
    top = file.parent
    while top.parent != root and not top.parent.exists():
        top = top.parent
    made = None if top.exists() else top
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f.content, encoding="utf-8")
    return made or file


def _remove(path: Path | None) -> None:
    if path is None:
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _plant_decoy(tmp: Path, script: str) -> Path:
    """A git checkout under `tmp` with its own copy of `script` and of the Codex adapter.

    Each copy only prints DECOY_MARK as context. A decoy fixture names the checkout as the
    payload's directory.
    """
    decoy = Path(tempfile.mkdtemp(prefix="decoy-", dir=tmp))
    init = subprocess.run(
        ["git", "init", "-q", str(decoy)], capture_output=True, text=True, check=False
    )
    if init.returncode != 0:
        raise HarnessError(f"cannot create a decoy checkout: {init.stderr.strip()}")
    answer = json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": DECOY_MARK}}
    )
    node = f"process.stdout.write({json.dumps(answer + chr(10))});\n"
    bodies = {".mjs": node, ".js": node, ".py": f"print({json.dumps(answer)})\n"}
    for rel in (script, CODEX_ADAPTER):
        file = decoy / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            bodies.get(Path(rel).suffix, f"cat >/dev/null\nprintf '%s\\n' '{answer}'\n"),
            encoding="utf-8",
        )
        file.chmod(0o755)
    return decoy


def _at(payload: dict[str, Any], decoy: Path | None) -> dict[str, Any]:
    if decoy is None:
        return payload
    out = {**payload, "cwd": str(decoy)}
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict) and "workdir" in tool_input:
        out["tool_input"] = {**tool_input, "workdir": str(decoy)}
    return out


def run_selftest(plan: Plan, root: Path) -> list[Result]:
    """Run every fixture of every enabled hook in the installed project at `root`."""
    results: list[Result] = []
    with tempfile.TemporaryDirectory(prefix="ah-selftest-") as tmp_name:
        tmp = Path(tmp_name)
        n = 0
        for hook in plan.hooks:
            for fixture in hook.fixtures:
                event = event_of(hook, fixture)
                if event is None:
                    detail = "the hook does not run on the fixture event"
                    results.append(Result(hook.id, "claude", label(fixture), detail))
                    continue
                n += 1
                session = f"ah-selftest-{os.getpid()}-{n}"
                env = {
                    **os.environ,
                    "CLAUDE_PROJECT_DIR": str(root),
                    "CLAUDE_CODE_SESSION_ID": session,
                    "TMPDIR": str(tmp),
                }
                decoy = _plant_decoy(tmp, hook.script) if fixture.decoy else None
                runs = [
                    (
                        "claude",
                        fixture,
                        [interpreter_for(hook.script), str(root / hook.script), event],
                        _at(claude_payload(event, fixture, root, session), decoy),
                    )
                ]
                if hook.codex:
                    runs.append(
                        (
                            "codex",
                            codex_expectation(fixture),
                            ["python3", str(root / CODEX_ADAPTER), hook.script, event],
                            _at(codex_payload(event, fixture, root, session), decoy),
                        )
                    )
                for client, expected, argv, payload in runs:
                    detail = _run_one(root, env, hook, expected, event, argv, payload, decoy)
                    results.append(Result(hook.id, client, label(fixture), detail))
    return results


def _run_one(
    root: Path,
    env: dict[str, str],
    hook: PlannedHook,
    fixture: Fixture,
    event: str,
    argv: list[str],
    payload: dict[str, Any],
    decoy: Path | None,
) -> str:
    planted = _plant_content(root, fixture)
    try:
        done = subprocess.run(
            argv,
            cwd=root,
            env=env,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=max(30, hook.timeout),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"could not run {argv[0]}: {error}"
    finally:
        _remove(planted)
    if DECOY_MARK in done.stdout:
        return f"ran the hook copy of the decoy checkout {decoy}, a directory the payload named"
    return judge(event, fixture, done.returncode, done.stdout, done.stderr)


@dataclass(frozen=True)
class PortabilityCase:
    """A guard, a command from the worktree, and the answer it must give."""

    hook: str
    # `{shared}` stands for the main checkout of the scratch repository.
    command: str
    expect: str
    contains: str | None = None


PORTABILITY_CASES = (
    PortabilityCase("workspace-guard", "git -C {shared} reset --hard HEAD", "deny"),
    PortabilityCase("workspace-guard", "git stash", "deny", "stash"),
    PortabilityCase("workspace-guard", "git status --short", "silent"),
    PortabilityCase(
        "worktree-path-guard", "git worktree add ../outside", "deny", ".claude/worktrees"
    ),
    PortabilityCase("bash-read-guard", "cat .env.local", "deny", "credential file"),
)


def _git(cwd: Path, *args: str) -> None:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise HarnessError(f"git {' '.join(args)} failed in {cwd}: {done.stderr.strip()}")


def _scratch_repository(tmp: Path, root: Path) -> tuple[Path, Path]:
    """A scratch repository with the installed engine committed, and a worktree at
    `.claude/worktrees/own` with a subfolder `sub`. Returns the main checkout and the worktree."""
    shared = tmp / "shared checkout"
    shared.mkdir()
    _git(shared, "init", "-q", "-b", "main")
    _git(shared, "config", "user.email", "selftest@example.invalid")
    _git(shared, "config", "user.name", "ah selftest")
    shutil.copytree(
        root / ENGINE_DIR, shared / ENGINE_DIR, ignore=shutil.ignore_patterns("__pycache__")
    )
    _git(shared, "add", ".claude")
    _git(shared, "commit", "-q", "-m", "hooks")
    own = shared / ".claude" / "worktrees" / "own"
    _git(shared, "worktree", "add", "-q", "-b", "task", str(own))
    (own / "sub").mkdir()
    return shared.resolve(), own.resolve()


def skill_links(plan: Plan, root: Path) -> list[Result]:
    """Each skill resolves through `.agents/skills/<id>/SKILL.md` to its own file, which has a
    description line."""
    results: list[Result] = []
    for skill in plan.skills:
        canonical = root / skill.target / "SKILL.md"
        linked = root / LINK_DIR / skill.id / "SKILL.md"
        detail = ""
        if not linked.exists():
            detail = f"{LINK_DIR}/{skill.id}/SKILL.md does not resolve"
        elif linked.resolve() != canonical.resolve():
            detail = f"{LINK_DIR}/{skill.id} points at {linked.resolve()}, not {skill.target}"
        elif not any(
            line.startswith("description: ")
            for line in linked.read_text(encoding="utf-8").splitlines()
        ):
            detail = "SKILL.md has no description line"
        name = f"skill {skill.id} through {LINK_DIR}"
        results.append(Result("portability", "codex", name, detail))
    return results


def run_portability(plan: Plan, root: Path) -> list[Result]:
    """The skill links, then each case of `PORTABILITY_CASES` whose hook is enabled, from a
    worktree subfolder of a scratch repository, for Claude Code and through the Codex adapter."""
    results = skill_links(plan, root)
    hooks = {hook.id: hook for hook in plan.hooks}
    cases = [case for case in PORTABILITY_CASES if case.hook in hooks]
    if not cases:
        return results
    with tempfile.TemporaryDirectory(prefix="ah-portability-") as tmp_name:
        tmp = Path(tmp_name)
        shared, own = _scratch_repository(tmp, root)
        sub = own / "sub"
        event = "PreToolUse"
        for n, case in enumerate(cases):
            hook = hooks[case.hook]
            command = case.command.replace("{shared}", json.dumps(str(shared)))
            fixture = Fixture(
                expect=case.expect, tool="Bash", command=command, contains=case.contains
            )
            session = f"ah-portability-{os.getpid()}-{n}"
            env = {
                **os.environ,
                "CLAUDE_PROJECT_DIR": str(shared),
                "CLAUDE_CODE_SESSION_ID": session,
                "TMPDIR": str(tmp),
            }
            shown = case.command.replace("{shared}", "<main checkout>")
            name = f"{case.hook} from a worktree subfolder: {shown}"
            runs = [
                (
                    "claude",
                    [interpreter_for(hook.script), str(own / hook.script), event],
                    {**claude_payload(event, fixture, own, session), "cwd": str(sub)},
                )
            ]
            if hook.codex:
                payload = codex_payload(event, fixture, own, session)
                tool_input = {**payload["tool_input"], "workdir": str(sub)}
                runs.append(
                    (
                        "codex",
                        ["python3", str(own / CODEX_ADAPTER), hook.script, event],
                        {**payload, "cwd": str(sub), "tool_input": tool_input},
                    )
                )
            for client, argv, body in runs:
                try:
                    done = subprocess.run(
                        argv,
                        cwd=sub,
                        env=env,
                        input=json.dumps(body),
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    detail = f"could not run {argv[0]}: {error}"
                else:
                    detail = judge(event, fixture, done.returncode, done.stdout, done.stderr)
                results.append(Result("portability", client, name, detail))
    return results


def main(argv: list[str]) -> int:
    from agent_harness import config
    from agent_harness.install.project import (
        check_project,
        installed_manifests,
        plan_for,
        require_lock,
    )

    if argv:
        print("usage: ah selftest", file=sys.stderr)
        return 2
    root = config.find_root()
    cfg = config.load(root)
    diffs = check_project(root, cfg)
    if diffs:
        names = ", ".join(path for path, _ in diffs[:5])
        raise HarnessError(
            f"the install differs from its lockfile ({names}); selftest runs the installed hooks,"
            " so run ah install --project first"
        )
    plan = plan_for(root, cfg, installed_manifests(root, require_lock(root)))
    results = run_selftest(plan, root) + run_portability(plan, root)
    for result in results:
        print(result.line())
    failed = sum(1 for r in results if not r.ok)
    print(f"ah selftest: {len(results) - failed} of {len(results)} fixture runs pass")
    return 0 if failed == 0 else 1
