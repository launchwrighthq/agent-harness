"""worktree-path-guard refuses `git worktree add` outside `.claude/worktrees/<name>`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import hook_project

HOOK = "worktree-path-guard.py"


def test_allows_a_direct_child_of_the_worktree_folder_only(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project")
    (project / "sub").mkdir()
    allowed = [
        "git worktree add .claude/worktrees/feature",
        "git worktree add -b feature .claude/worktrees/feature",
        f"git worktree add {project}/.claude/worktrees/feature",
        "git -C sub worktree add ../.claude/worktrees/feature",
        "git status",
    ]
    refused = [
        "git worktree add ../sibling",
        "git worktree add .claude/worktrees/a/b",
        "git worktree add .claude/worktrees",
        "git worktree add worktrees/feature",
        "git -C sub worktree add feature",
    ]

    got = hook_project.verdicts(project, HOOK, allowed + refused)

    assert list(zip(allowed + refused, got, strict=True)) == [(c, "silent") for c in allowed] + [
        (c, "deny") for c in refused
    ]


def test_stays_silent_for_a_folder_that_holds_no_repository(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project")
    bare = tmp_path / "bare"
    bare.mkdir()

    done = hook_project.run_hook(
        project, HOOK, "PreToolUse", hook_project.bash_payload("git worktree add ../x", bare)
    )

    assert hook_project.decision(done) == "silent"
    assert f"worktree-path-guard: no repository at {bare}" in done.stderr


@pytest.mark.parametrize(
    "script",
    [
        "worktree-path-guard.py",
        "workspace-guard.py",
        "bash-read-guard.py",
        "deny-env-reads.py",
        "deny-foreign-branch-moves.sh",
    ],
)
def test_lets_the_worktree_command_of_the_engine_pass(tmp_path: Path, script: str) -> None:
    project = hook_project.make_project(tmp_path / "project")
    commands = [
        "python3 .claude/agent-harness/bin/ah worktree add land-a",
        "python3 .claude/agent-harness/bin/ah worktree add land-b --from main",
        "python3 .claude/agent-harness/bin/ah worktree remove land-a",
    ]

    got = hook_project.verdicts(project, script, commands)

    assert list(zip(commands, got, strict=True)) == [(c, "silent") for c in commands]
