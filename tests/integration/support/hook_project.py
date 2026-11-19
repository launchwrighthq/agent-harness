"""A temp project with the package hooks installed, and a runner for one hook call.

The project has the installed layout `.claude/agent-harness/`: a copy of `hooks/`, and links to
`bin/` and `src/` of the package. A hook in it finds its project and its engine as an installed
hook does.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

PACKAGE = Path(__file__).resolve().parents[3]
INSTALLED = Path(".claude") / "agent-harness"
LAUNCHER = INSTALLED / "hooks" / "run.sh"


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    return path


def copy_tree(source: Path, target: Path) -> None:
    """Copy the files of `source` into `target`, modes kept, caches left out."""
    for item in sorted(source.rglob("*")):
        if "__pycache__" in item.parts or item.is_dir():
            continue
        dest = target / item.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(item.read_bytes())
        dest.chmod(item.stat().st_mode)


def make_project(root: Path, config: str = "") -> Path:
    """A git checkout at `root` with the hooks installed and `config` as `.agents/harness.toml`."""
    git_init(root)
    return install_hooks(root, config)


def install_hooks(root: Path, config: str = "") -> Path:
    """The hooks installed into the existing checkout or worktree at `root`."""
    home = root / INSTALLED
    copy_tree(PACKAGE / "hooks", home / "hooks")
    (home / "src").symlink_to(PACKAGE / "src")
    (home / "bin").symlink_to(PACKAGE / "bin")
    if config:
        (root / ".agents").mkdir()
        (root / ".agents" / "harness.toml").write_text(config, encoding="utf-8")
    return root


def hook_env(project: Path, session: str, tmp: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "LW_"))}
    env.update(
        CLAUDE_PROJECT_DIR=str(project),
        CLAUDE_CODE_SESSION_ID=session,
        TMPDIR=str(tmp),
        XDG_RUNTIME_DIR=str(tmp),
    )
    return env


def run_hook(
    project: Path,
    script: str,
    event: str,
    payload: dict[str, Any],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `script` (a path under `hooks/`) through the installed launcher, as the client does."""
    return subprocess.run(
        ["bash", str(project / LAUNCHER), str(INSTALLED / "hooks" / script), event],
        cwd=project,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else hook_env(project, "hook-test", project.parent),
    )


def bash_payload(command: str, cwd: Path, session: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "cwd": str(cwd),
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if session is not None:
        payload["session_id"] = session
    return payload


def file_payload(tool: str, path: Path) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "cwd": str(path.parent),
        "tool_name": tool,
        "tool_input": {"file_path": str(path)},
    }


def decision(done: subprocess.CompletedProcess[str]) -> str:
    """`deny` or `silent` for one PreToolUse run. Any other answer raises with the output."""
    if done.returncode != 0:
        raise AssertionError(f"exit {done.returncode}: {done.stderr}")
    if done.stdout.strip() == "":
        return "silent"
    return str(json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"])


def verdicts(
    project: Path,
    script: str,
    commands: list[str],
    cwd: Path | None = None,
    session: str | None = None,
) -> list[str]:
    """The decision of `script` for each Bash command, in order."""
    return [
        decision(run_hook(project, script, "PreToolUse", bash_payload(c, cwd or project, session)))
        for c in commands
    ]
