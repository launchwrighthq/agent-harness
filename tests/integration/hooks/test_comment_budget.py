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


def project_on_main(tmp_path: Path, config: str = "") -> Path:
    project = hook_project.make_project(tmp_path / "project", config)
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


def test_gives_a_renamed_file_the_caps_of_its_old_path(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    (project / "src").mkdir()
    (project / "src/old.mts").write_text(ts_file(20, 10), encoding="utf-8")
    commit(project)
    subprocess.run(["git", "-C", str(project), "mv", "src/old.mts", "src/moved.mts"], check=True)

    assert run(project, "PostToolUse", "Edit", {"file_path": str(project / "src/moved.mts")}) == ""


def test_denies_a_terraform_block_over_the_cap_of_its_override(tmp_path: Path) -> None:
    config = '[harness.comments.overrides."**/*.tf"]\nmaxBlock = 1\nminLines = 0\n'
    project = project_on_main(tmp_path, config)
    commit(project)
    data = {"file_path": str(project / "infra/main.tf"), "content": "# a\n# b\nlocals {}\n"}

    out = json.loads(run(project, "PreToolUse", "Write", data))["hookSpecificOutput"]

    assert out["permissionDecision"] == "deny"
    reason = out["permissionDecisionReason"]
    assert "infra/main.tf would get a 2-line comment block, cap 1" in reason


def test_holds_the_opening_block_of_an_env_template_to_its_head_cap(tmp_path: Path) -> None:
    caps = "headBlock = 3\nmaxBlock = 1\nminLines = 0\n"
    project = project_on_main(tmp_path, f'[harness.comments.overrides."**/.env*.example"]\n{caps}')
    commit(project)
    path = str(project / ".env.example")
    within = {"file_path": path, "content": "# a\n# b\n# c\n\n# App\nAPP_URL=\n"}
    over = {"file_path": path, "content": "# a\n# b\n# c\n# d\n\n# App\nAPP_URL=\n"}

    assert run(project, "PreToolUse", "Write", within) == ""
    out = json.loads(run(project, "PreToolUse", "Write", over))["hookSpecificOutput"]

    assert out["permissionDecision"] == "deny"
    reason = out["permissionDecisionReason"]
    assert ".env.example would get a 4-line opening comment block, cap 3" in reason


def stop(project: Path, session: str = "s1") -> subprocess.CompletedProcess[str]:
    payload = {"cwd": str(project), "hook_event_name": "Stop", "session_id": session}
    return hook_project.run_hook(project, HOOK, "Stop", payload)


def shell_write(project: Path, rel: str, text: str) -> None:
    """Write a file as a shell command does: no Write or Edit event reaches the hook."""
    (project / rel).parent.mkdir(parents=True, exist_ok=True)
    (project / rel).write_text(text, encoding="utf-8")


def test_holds_the_turn_for_an_untracked_file_that_a_shell_command_wrote(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    commit(project)
    shell_write(project, "src/new.mts", ts_file(13, 10))

    done = stop(project)

    assert done.returncode == 2
    assert "comment-budget: src/new.mts has a 13-line comment block, cap 12" in done.stderr
    assert "end the turn" in done.stderr


def test_holds_the_turn_for_a_tracked_file_that_changed_since_the_base(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    shell_write(project, "src/old.mts", ts_file(5, 20))
    commit(project)
    subprocess.run(["git", "-C", str(project), "checkout", "-q", "-b", "work"], check=True)
    shell_write(project, "src/old.mts", ts_file(26, 20))
    commit(project)

    done = stop(project)

    assert done.returncode == 2
    assert "src/old.mts has a 26-line comment block, cap 25" in done.stderr


def test_lets_the_turn_end_on_a_second_stop_with_the_same_findings(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    commit(project)
    shell_write(project, "src/new.mts", ts_file(13, 10))
    assert stop(project).returncode == 2

    again = stop(project)

    assert (again.returncode, again.stdout) == (0, "")


def test_holds_the_turn_again_when_the_findings_change(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    commit(project)
    shell_write(project, "src/new.mts", ts_file(13, 10))
    assert stop(project).returncode == 2
    shell_write(project, "src/other.mts", ts_file(14, 10))

    done = stop(project)

    assert done.returncode == 2
    assert "src/other.mts has a 14-line comment block, cap 12" in done.stderr


def test_holds_each_session_once(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    commit(project)
    shell_write(project, "src/new.mts", ts_file(13, 10))
    assert stop(project, "s1").returncode == 2

    assert stop(project, "s2").returncode == 2


def test_lets_the_turn_end_when_no_changed_file_has_a_finding(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    shell_write(project, "src/old.mts", ts_file(30, 10))
    commit(project)
    shell_write(project, "src/new.mts", ts_file(12, 10))

    done = stop(project)

    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_holds_the_turn_again_after_a_clean_stop_removed_the_saved_findings(
    tmp_path: Path,
) -> None:
    project = project_on_main(tmp_path)
    commit(project)
    shell_write(project, "src/new.mts", ts_file(13, 10))
    assert stop(project).returncode == 2
    shell_write(project, "src/new.mts", ts_file(12, 10))
    clean = stop(project)
    saved = list((tmp_path / "comment-budget").iterdir())
    shell_write(project, "src/new.mts", ts_file(13, 10))

    again = stop(project)

    assert (clean.returncode, clean.stderr, saved) == (0, "", [])
    assert again.returncode == 2


def test_lets_the_turn_end_with_the_reason_when_git_fails(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    commit(project)
    shell_write(project, "src/new.mts", ts_file(13, 10))
    (project / ".git" / "index").write_bytes(b"no index")

    done = stop(project)

    assert (done.returncode, done.stdout) == (0, "")
    assert done.stderr.startswith(f"comment-budget: git diff failed in {project}: ")
    assert done.stderr.endswith("; no verdict\n")


def test_lets_the_turn_end_with_the_reason_for_a_file_that_is_not_utf8(tmp_path: Path) -> None:
    project = project_on_main(tmp_path)
    commit(project)
    (project / "src").mkdir()
    (project / "src" / "new.mts").write_bytes(b"// caf\xe9\nexport const v = 1;\n")

    done = stop(project)

    assert (done.returncode, done.stdout) == (0, "")
    assert done.stderr.startswith("comment-budget: 'utf-8' codec can't decode byte 0xe9")
    assert done.stderr.endswith("; no verdict\n")


def test_lets_the_turn_end_with_the_reason_when_it_cannot_save_the_findings(
    tmp_path: Path,
) -> None:
    project = project_on_main(tmp_path)
    commit(project)
    shell_write(project, "src/new.mts", ts_file(13, 10))
    blocked = tmp_path / "runtime"
    blocked.write_text("a file where the state folder goes", encoding="utf-8")
    env = {**hook_project.hook_env(project, "s1", tmp_path), "XDG_RUNTIME_DIR": str(blocked)}
    payload = {"cwd": str(project), "hook_event_name": "Stop", "session_id": "s1"}

    done = hook_project.run_hook(project, HOOK, "Stop", payload, env)

    assert (done.returncode, done.stdout) == (0, "")
    assert done.stderr.startswith("comment-budget: [Errno 20] Not a directory")
    assert done.stderr.endswith("; no verdict\n")
