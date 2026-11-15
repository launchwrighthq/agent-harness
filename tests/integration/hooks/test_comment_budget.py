"""comment-budget refuses a write over a block cap and reports the budget of a written file."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import hook_project

HOOK = "comment-budget.py"


def ts_file(block: int, code: int) -> str:
    """A TS file with one comment block of `block` lines and `code` code lines."""
    lines = ["/**", *(f" * line {i}" for i in range(block - 2)), " */"]
    lines += [f"export const v{i} = {i};" for i in range(code)]
    return "\n".join([*lines, ""])


def project_on_main(tmp_path: Path) -> Path:
    project = hook_project.make_project(tmp_path / "project")
    git = ["git", "-C", str(project)]
    subprocess.run([*git, "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
    return project


def commit(project: Path) -> None:
    git = ["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)


def run(project: Path, event: str, tool: str, data: dict[str, Any]) -> str:
    payload = {"cwd": str(project), "hook_event_name": event, "tool_name": tool}
    done = hook_project.run_hook(project, HOOK, event, {**payload, "tool_input": data})
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_denies_a_write_that_gives_a_new_file_a_block_over_the_cap(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    commit(project)
    data = {"file_path": str(project / "src/new.mts"), "content": ts_file(13, 10)}

    out = json.loads(run(project, "PreToolUse", "Write", data))["hookSpecificOutput"]

    assert out["permissionDecision"] == "deny"
    assert (
        "src/new.mts would get a 13-line comment block, cap 12" in out["permissionDecisionReason"]
    )


def test_lets_an_edit_through_that_keeps_a_block_the_file_already_held(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    (project / "src").mkdir()
    (project / "src/old.mts").write_text(ts_file(30, 10), encoding="utf-8")
    commit(project)
    data = {
        "file_path": str(project / "src/old.mts"),
        "old_string": "export const v0 = 0;",
        "new_string": "export const v0 = 1;",
    }

    assert run(project, "PreToolUse", "Edit", data) == ""


def test_reports_the_finding_of_a_written_file_as_added_context(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    commit(project)
    (project / "src").mkdir()
    (project / "src/new.mts").write_text(ts_file(13, 10), encoding="utf-8")
    data = {"file_path": str(project / "src/new.mts")}

    out = json.loads(run(project, "PostToolUse", "Write", data))["hookSpecificOutput"]

    assert out["hookEventName"] == "PostToolUse"
    assert (
        "comment-budget: src/new.mts has a 13-line comment block, cap 12"
        in out["additionalContext"]
    )
