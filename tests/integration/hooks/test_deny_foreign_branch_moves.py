"""deny-foreign-branch-moves refuses a ref-only move of a branch another worktree holds."""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import hook_project

HOOK = "deny-foreign-branch-moves.sh"
IDENTITY = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={**os.environ, **IDENTITY},
    )


def test_names_the_configured_land_command_when_it_refuses(tmp_path: Path) -> None:
    project = hook_project.make_project(
        tmp_path / "project", '[harness]\nlandCommand = "make land"\n'
    )
    git(project, "commit", "--allow-empty", "--quiet", "-m", "init")
    git(project, "worktree", "add", "--quiet", "-b", "peer", ".claude/worktrees/peer")

    done = hook_project.run_hook(
        project, HOOK, "PreToolUse", hook_project.bash_payload("git branch -f peer HEAD", project)
    )
    safe = hook_project.verdicts(project, HOOK, ["git push . HEAD:peer", "git branch other"])

    reason = json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason.startswith("peer is checked out in another worktree")
    assert "Run 'make land' from this worktree instead." in reason
    assert safe == ["silent", "silent"]


def test_refuses_a_rename_copy_or_delete_of_a_branch_another_worktree_holds(
    tmp_path: Path,
) -> None:
    project = hook_project.make_project(tmp_path / "project")
    git(project, "commit", "--allow-empty", "--quiet", "-m", "init")
    git(project, "worktree", "add", "--quiet", "-b", "peer", ".claude/worktrees/peer")
    git(project, "branch", "free")
    refused = [
        "git branch -m peer x",
        "git branch -M peer x",
        "git branch -d peer",
        "git branch -D peer",
        "git branch -c peer x",
        "git branch -C x peer",
        "git branch --delete peer",
        "git branch --move peer x",
        "git branch -q -D peer",
    ]
    allowed = [
        "git branch -m free x",
        "git branch -D free",
        "git branch -C free y",
        "git branch --contains peer",
    ]

    got = hook_project.verdicts(project, HOOK, refused + allowed)

    expected = [(c, "deny") for c in refused] + [(c, "silent") for c in allowed]
    assert list(zip(refused + allowed, got, strict=True)) == expected


def test_reads_every_option_form_and_every_git_directory_of_a_branch_move(
    tmp_path: Path,
) -> None:
    project = hook_project.make_project(tmp_path / "project")
    git(project, "commit", "--allow-empty", "--quiet", "-m", "init")
    git(project, "worktree", "add", "--quiet", "-b", "peer", ".claude/worktrees/peer")
    refused = [
        "git branch --forc peer HEAD",
        "git branch peer HEAD -f",
        "git branch --mov peer x",
        "git branch --track x peer -f",
        "git branch --d peer",
        f"git -C {project} status && git -C {project} branch -f other HEAD",
        f"git -C {project} -C {project} branch -f other HEAD",
        f"git -C {tmp_path} status && git branch -f other HEAD",
    ]
    allowed = [
        "git branch --list peer",
        "git branch -a --list peer",
        "git branch --show-current; echo peer",
        "git branch --list -- peer -D",
        f"git -C {project} branch -f other HEAD",
    ]

    got = hook_project.verdicts(project, HOOK, refused + allowed)

    expected = [(c, "deny") for c in refused] + [(c, "silent") for c in allowed]
    assert list(zip(refused + allowed, got, strict=True)) == expected


def test_reads_a_git_path_a_runner_a_continued_line_and_a_quoted_flag(tmp_path: Path) -> None:
    project = hook_project.make_project(tmp_path / "project")
    git(project, "commit", "--allow-empty", "--quiet", "-m", "init")
    git(project, "worktree", "add", "--quiet", "-b", "peer", ".claude/worktrees/peer")
    refused = [
        "/usr/bin/git branch -f peer HEAD",
        "command git branch -f peer HEAD",
        "env git branch -f peer HEAD",
        "git branch \\\n-f peer HEAD",
        "gi\\\nt branch -f peer HEAD",
        "git bra\\\nnch -f peer HEAD",
        "git branch --for\\\nce peer HEAD",
        "# note \\\ngit branch -f peer HEAD",
        "# note\\\ngit branch -f peer HEAD",
        "echo 'a\\\n'git branch -f peer HEAD",
        'git branch "-f" peer HEAD',
        "git branch '--force' peer HEAD",
        "git branch \\-f peer HEAD",
    ]
    # The forms the hook refused before this branch stay refused.
    floor = [
        "git branch -f peer HEAD",
        "git branch -M peer x",
        "git update-ref refs/heads/peer HEAD",
        "git push --force . HEAD:peer",
        f"git -C {project} branch -f peer HEAD",
    ]
    # A continuation with a comment or a single quote fails closed for a git ref command.
    unsure = [
        "echo 'a\\\nb' && git branch peer-copy \\\n# x",
        "git log --oneline \\\n-1 # 'branch'",
    ]
    allowed = ["git branch '--list' peer", "git log \\\n--oneline -1", "git status # a branch note"]

    commands = refused + floor + unsure + allowed

    got = hook_project.verdicts(project, HOOK, commands)

    expected = [(c, "deny") for c in refused + floor + unsure] + [(c, "silent") for c in allowed]
    assert list(zip(commands, got, strict=True)) == expected
