import json
from pathlib import Path

from agent_harness.install.plan import Plan, PlannedHook
from agent_harness.install.registry import codex_file


def stop_hook() -> PlannedHook:
    return PlannedHook(
        id="a",
        manifest="agent-harness",
        summary="s",
        events=("Stop",),
        matcher=None,
        timeout=5,
        codex=True,
        fixtures=(),
        script=".claude/agent-harness/hooks/a.py",
    )


def test_merges_the_codex_entries_into_the_file_the_project_holds(tmp_path: Path) -> None:
    plan = Plan(hooks=[stop_hook()])
    own = {"hooks": [{"type": "command", "command": "python3 own.py"}]}
    current = json.dumps({"other": 1, "hooks": {"Stop": [own]}}, indent=2) + "\n"

    merged = json.loads(codex_file(current, plan, tmp_path))
    fresh = json.loads(codex_file(None, plan, tmp_path))

    assert merged["other"] == 1
    assert merged["hooks"]["Stop"] == [*fresh["hooks"]["Stop"], own]
