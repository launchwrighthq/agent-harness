"""The render-spec hook renders a changed spec and holds a turn that links a missing page."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[3] / "hooks" / "render-spec.py"
SPEC = "# A spec\n\nDate: 2026-01-01\nStatus: draft\n\n## 1. Scope\n\nOne line.\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = Path(os.path.realpath(tmp_path / "project"))
    project.mkdir()
    subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    return project


@pytest.fixture
def tree(root: Path) -> Path:
    worktree = root / ".claude" / "worktrees" / "wt"
    worktree.mkdir(parents=True)
    # A worktree has `.git` as a file, which is all `repo_root_for` reads.
    (worktree / ".git").write_text("gitdir: /nowhere\n", encoding="utf-8")
    return worktree


def write(base: Path, relative: str, text: str) -> Path:
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def hook(root: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=root,
        input=json.dumps({"cwd": str(root), **payload}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def stop(root: Path, reply: str) -> subprocess.CompletedProcess[str]:
    return hook(root, {"hook_event_name": "Stop", "last_assistant_message": reply})


def test_lets_a_reply_with_a_working_link_or_no_link_end_the_turn(root: Path) -> None:
    write(root, "docs/specs/dist/live.html", "<html></html>")

    assert stop(root, f"See file://{root}/docs/specs/dist/live.html").returncode == 0
    assert stop(root, "No link here.").returncode == 0


def test_names_the_worktree_page_when_the_reply_links_the_main_checkout(
    root: Path, tree: Path
) -> None:
    write(tree, "docs/specs/2026-01-01-moved.md", SPEC)
    write(tree, "docs/specs/dist/2026-01-01-moved.html", "<html></html>")

    done = stop(root, f"The spec:\nfile://{root}/docs/specs/dist/2026-01-01-moved.html\n")

    assert done.returncode == 2
    assert f"file://{root}/docs/specs/dist/2026-01-01-moved.html does not exist" in done.stderr
    assert f"The page exists at file://{tree}/docs/specs/dist/2026-01-01-moved.html" in done.stderr


def test_renders_the_missing_page_of_a_landed_spec_and_ends_the_turn(root: Path) -> None:
    write(root, "docs/specs/2026-01-01-landed.md", SPEC)
    page = root / "docs/specs/dist/2026-01-01-landed.html"

    done = stop(root, f"[spec]({page})")

    assert done.stderr == ""
    assert done.returncode == 0
    assert page.is_file()


def test_holds_a_link_that_no_checkout_holds(root: Path) -> None:
    done = stop(root, f"file://{root}/docs/superpowers/plans/dist/2026-01-01-ghost.html")

    assert done.returncode == 2
    assert "No checkout of this project holds that page or its source" in done.stderr


def test_reads_the_reply_from_the_transcript_when_the_payload_has_none(root: Path) -> None:
    reply = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": f"{root}/docs/specs/dist/gone.html"}]},
    }
    asked = {"type": "user", "message": {"content": "go"}}
    transcript = write(root, "transcript.jsonl", f"{json.dumps(asked)}\n{json.dumps(reply)}\n")

    done = hook(root, {"hook_event_name": "Stop", "transcript_path": str(transcript)})

    assert done.returncode == 2
    assert f"file://{root}/docs/specs/dist/gone.html does not exist" in done.stderr


def test_adds_the_land_note_for_a_worktree_render_only(root: Path, tree: Path) -> None:
    in_tree = hook(
        root,
        {
            "hook_event_name": "PostToolUse",
            "tool_input": {"file_path": str(write(tree, "docs/specs/2026-01-01-wt.md", SPEC))},
        },
    )
    in_main = hook(
        root,
        {
            "hook_event_name": "PostToolUse",
            "tool_input": {"file_path": str(write(root, "docs/specs/2026-01-01-main.md", SPEC))},
        },
    )

    context = json.loads(in_tree.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "exists in this worktree only" in context
    assert (tree / "docs/specs/dist/2026-01-01-wt.html").is_file()
    assert "Rendered the spec" in in_main.stdout
    assert "worktree only" not in in_main.stdout


def test_reports_a_refused_render_with_the_renderer_message(root: Path) -> None:
    source = write(root, "docs/specs/2026-01-01-bad.md", "# Bad\n\nDate: 2026-01-01\n")

    done = hook(root, {"hook_event_name": "PostToolUse", "tool_input": {"file_path": str(source)}})

    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert context == (
        "The spec did not render, so there is no page yet: "
        f"{source}: missing 'Status:' below the H1"
    )


def test_renders_a_spec_in_a_folder_that_the_config_names(root: Path) -> None:
    write(root, ".agents/harness.toml", '[specs]\ndirs = ["design"]\n')
    source = write(root, "design/a.md", SPEC)

    done = hook(root, {"hook_event_name": "PostToolUse", "tool_input": {"file_path": str(source)}})

    assert f"file://{root}/design/dist/a.html" in done.stdout
    assert (root / "design/dist/a.html").is_file()


def test_renders_a_spec_that_a_shell_command_wrote_at_the_end_of_the_turn(root: Path) -> None:
    write(root, "docs/specs/2026-01-01-heredoc.md", SPEC)

    done = hook(root, {"hook_event_name": "Stop", "last_assistant_message": "done"})

    assert done.returncode == 0
    assert "Rendered the spec" in json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert (root / "docs/specs/dist/2026-01-01-heredoc.html").is_file()


def test_skips_every_render_without_a_trusted_project_root(root: Path) -> None:
    source = write(root, "docs/specs/2026-01-01-x.md", SPEC)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}

    done = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(
            {"hook_event_name": "PostToolUse", "tool_input": {"file_path": str(source)}}
        ),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert done.returncode == 0
    assert done.stdout == ""
    assert not (root / "docs/specs/dist/2026-01-01-x.html").exists()
