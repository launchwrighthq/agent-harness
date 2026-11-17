import json

import pytest

from agent_harness import HarnessError
from agent_harness.install.plan import Plan, PlannedHook, PlannedRule
from agent_harness.install.registry import (
    BLOCK_END,
    BLOCK_START,
    agents_block,
    codex_command,
    codex_matcher,
    is_own,
    project_entries,
    registry,
    settings_file,
    splice_top_level_key,
    with_agents_block,
    with_ignored,
)


def hook(
    hook_id: str, events: tuple[str, ...], matcher: str | None, codex: bool = True
) -> PlannedHook:
    return PlannedHook(
        id=hook_id,
        manifest="agent-harness",
        summary="s",
        events=events,
        matcher=matcher,
        timeout=5,
        codex=codex,
        fixtures=(),
        script=f".claude/agent-harness/hooks/{hook_id}.py",
    )


def test_replaces_one_key_and_keeps_every_other_byte() -> None:
    text = '{\n  "a": 1,\n  "hooks": {"old": true},\n  "z": [1, 2]\n}\n'

    out = splice_top_level_key(text, "hooks", {"Stop": []}, "s.json")

    assert out == '{\n  "a": 1,\n  "hooks": {\n    "Stop": []\n  },\n  "z": [1, 2]\n}\n'


def test_adds_a_missing_key_after_the_last_one() -> None:
    assert (
        splice_top_level_key('{\n  "a": 1\n}\n', "b", 2, "s.json") == '{\n  "a": 1,\n  "b": 2\n}\n'
    )
    assert splice_top_level_key("{}\n", "b", [], "s.json") == '{\n  "b": []\n}\n'


def test_refuses_text_that_is_not_json() -> None:
    with pytest.raises(HarnessError, match=r"s\.json is not valid JSON"):
        splice_top_level_key("{a", "b", 1, "s.json")


def test_adds_apply_patch_to_an_edit_matcher_for_codex() -> None:
    assert codex_matcher("Write|Edit") == "Write|Edit|apply_patch"
    assert codex_matcher("Bash") == "Bash"
    assert codex_matcher("*") == "*"


def test_points_the_codex_adapter_at_the_project_folder_inside_the_checkout() -> None:
    top = '"$(git rev-parse --show-toplevel)'

    at_root = codex_command(".claude/agent-harness/hooks/a.py", "Stop")
    in_folder = codex_command(".claude/agent-harness/hooks/a.py", "Stop", "playground")

    assert at_root == (
        f'python3 {top}/.claude/agent-harness/codex/run-hook.py"'
        " .claude/agent-harness/hooks/a.py Stop"
    )
    assert in_folder == (
        f'python3 {top}/playground/.claude/agent-harness/codex/run-hook.py"'
        " .claude/agent-harness/hooks/a.py Stop"
    )


def test_writes_the_project_folder_into_each_codex_entry() -> None:
    plan = Plan(hooks=[hook("a", ("Stop",), None)])

    codex = registry(plan, "codex", "apps/demo")

    assert (
        "/apps/demo/.claude/agent-harness/codex/run-hook.py"
        in (codex["Stop"][0]["hooks"][0]["command"])
    )


def test_gives_each_hook_one_group_per_event_in_plan_order() -> None:
    plan = Plan(
        hooks=[
            hook("a", ("SessionStart", "PreToolUse"), "Bash"),
            hook("b", ("PreToolUse", "SubagentStop"), "Edit"),
            hook("c", ("PreToolUse",), "Bash", codex=False),
        ]
    )

    claude = registry(plan, "claude")
    codex = registry(plan, "codex")

    assert list(claude) == ["SessionStart", "PreToolUse", "SubagentStop"]
    assert [g.get("matcher") for g in claude["PreToolUse"]] == ["Bash", "Edit", "Bash"]
    assert list(codex) == ["SessionStart", "PreToolUse"]
    assert [g.get("matcher") for g in codex["PreToolUse"]] == ["Bash", "Edit|apply_patch"]
    assert "matcher" not in claude["SessionStart"][0]
    assert json.dumps(claude).count('"timeout": 5') == 5


def test_puts_the_project_entry_after_the_harness_entry_and_keeps_the_text() -> None:
    plan = Plan(hooks=[hook("a", ("Stop",), None)])
    own = {"hooks": [{"type": "command", "command": "bash x.sh"}]}
    first = settings_file('{"hooks": {"Stop": [' + json.dumps(own) + "]}}\n", plan)

    assert json.loads(first)["hooks"]["Stop"][-1] == own
    assert settings_file(first, plan) == first


def test_lists_each_rule_and_skill_in_the_agents_block() -> None:
    plan = Plan(rules=[PlannedRule("style", "pkg", "Style", ("docs/**",), ".claude/rules/lw/s.md")])

    block = agents_block(plan, ["lw-demo"], "harness/adapters")

    assert block.startswith(BLOCK_START)
    assert "| Style | `docs/**` | `.claude/rules/lw/s.md` |" in block
    assert "Skills: `lw-demo`." in block
    assert block.endswith(BLOCK_END)


def test_replaces_the_marked_block_and_keeps_the_rest() -> None:
    old = f"# Rules\n\nmine\n\n{BLOCK_START}\nold\n{BLOCK_END}\n\ntail\n"

    assert with_agents_block(old, f"{BLOCK_START}\nnew\n{BLOCK_END}") == (
        f"# Rules\n\nmine\n\n{BLOCK_START}\nnew\n{BLOCK_END}\n\ntail\n"
    )
    assert with_agents_block("# Rules", "B") == "# Rules\n\nB\n"
    assert with_agents_block(None, "B") == "# Agent rules\n\nB\n"


def test_adds_the_ignore_lines_that_the_file_lacks() -> None:
    assert with_ignored(None) == "/.claude/state/\n/.claude/agent-harness/**/__pycache__/\n"
    assert with_ignored("node_modules/") == (
        "node_modules/\n/.claude/state/\n/.claude/agent-harness/**/__pycache__/\n"
    )
    kept = ".claude/state\n__pycache__/\n"
    assert with_ignored(kept) == kept


def test_owns_only_the_commands_that_run_the_engine_or_its_adapter() -> None:
    claude = 'bash "$CLAUDE_PROJECT_DIR/.claude/agent-harness/hooks/x.sh" Stop'
    codex = codex_command(".claude/agent-harness/hooks/x.py", "Stop", "apps/demo")

    assert is_own({"command": claude}) is True
    assert is_own({"command": codex}) is True
    assert is_own({"command": "bash scripts/x.sh"}) is False
    assert is_own({"command": 'bash "$CLAUDE_PROJECT_DIR/scripts/agent-harness/x.sh"'}) is False


def test_refuses_a_hooks_key_that_is_not_an_object() -> None:
    with pytest.raises(HarnessError, match="the hooks key is not an object"):
        project_entries('{"hooks": []}', ".claude/settings.json")
