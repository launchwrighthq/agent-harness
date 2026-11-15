"""inject-memory adds the memory index named by the config, once per session."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import hook_project


def test_injects_the_configured_index_once_per_session(tmp_path: Path) -> None:
    project = hook_project.make_project(
        tmp_path / "project", '[harness]\nmemoryIndex = "notes/index.md"\n'
    )
    (project / "notes").mkdir()
    (project / "notes" / "index.md").write_text("- [Topic](topic.md)\n", encoding="utf-8")
    env = hook_project.hook_env(project, "memory-session", tmp_path)
    payload = {"hook_event_name": "SessionStart", "session_id": "memory-session"}

    first = hook_project.run_hook(project, "load-memory.sh", "SessionStart", payload, env)
    second = hook_project.run_hook(project, "load-memory.sh", "SessionStart", payload, env)

    text = json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
    assert text.endswith("- [Topic](topic.md)")
    assert f"({project / 'notes' / 'index.md'})" in text
    assert (second.returncode, second.stdout) == (0, "")
