import json
from pathlib import Path

from agent_harness.install.plan import PlannedHook
from agent_harness.manifest import Fixture
from agent_harness.selftest import codex_payload, event_of, judge, label

ASK = Fixture(expect="context", prompt="x")
DENY = Fixture(expect="deny", tool="Bash", command="x", contains="no")


def planned(events: tuple[str, ...]) -> PlannedHook:
    return PlannedHook("h", "pkg", "s", events, None, 5, True, (), "h.py")


def test_accepts_context_under_the_right_event_and_rejects_other_shapes() -> None:
    ok = json.dumps(
        {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "Go."}}
    )
    wrong = json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "x"}}
    )

    assert judge("UserPromptSubmit", ASK, 0, ok, "") == ""
    assert '{"prompt"' in judge("UserPromptSubmit", ASK, 0, json.dumps({"prompt": "x"}), "")
    assert "not JSON" in judge("UserPromptSubmit", ASK, 0, "plain text", "")
    assert "hookEventName" in judge("UserPromptSubmit", ASK, 0, wrong, "")


def test_takes_a_json_deny_or_exit_two_as_a_deny() -> None:
    denied = json.dumps(
        {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "no"}}
    )

    assert judge("PreToolUse", DENY, 0, denied, "") == ""
    assert judge("PreToolUse", DENY, 2, "", "no way") == ""
    assert judge("PreToolUse", DENY, 0, "", "") == "expected a deny, got exit 0 and no output"
    assert "does not say" in judge("PreToolUse", DENY, 2, "", "blocked")


def test_takes_only_an_ask_decision_with_the_reason_as_an_ask() -> None:
    ask = Fixture(expect="ask", tool="Bash", command="x", contains="confirm")

    def answer(decision: str, reason: str) -> str:
        spec = {"permissionDecision": decision, "permissionDecisionReason": reason}
        return json.dumps({"hookSpecificOutput": spec})

    assert judge("PreToolUse", ask, 0, answer("ask", "confirm it"), "") == ""
    assert judge("PreToolUse", ask, 0, answer("deny", "confirm it"), "") == (
        f"expected an ask, got exit 0 and {answer('deny', 'confirm it')}"
    )
    assert (
        judge("PreToolUse", ask, 2, "", "confirm it") == "expected an ask, got exit 2 and no output"
    )
    assert "does not say" in judge("PreToolUse", ask, 0, answer("ask", "sure"), "")


def test_takes_a_block_decision_from_a_post_tool_hook() -> None:
    block = Fixture(expect="block", tool="Edit", file="a.md", contains="fix")

    assert judge("PostToolUse", block, 0, '{"decision": "block", "reason": "fix it"}', "") == ""
    assert "expected" in judge("PostToolUse", block, 0, "", "")


def test_runs_a_tool_fixture_on_post_tool_use_without_pre_tool_use() -> None:
    assert event_of(planned(("PostToolUse", "Stop")), DENY) == "PostToolUse"
    assert event_of(planned(("PreToolUse",)), ASK) is None


def test_sends_an_edit_to_codex_as_an_apply_patch(tmp_path: Path) -> None:
    edit = Fixture(expect="silent", tool="Edit", file="docs/a.md")

    payload = codex_payload("PreToolUse", edit, tmp_path, "s")

    assert payload["tool_name"] == "apply_patch"
    assert "*** Update File: docs/a.md" in payload["tool_input"]["command"]
    assert label(Fixture(expect="deny", tool="Bash", command="a\nb", decoy=True)) == (
        "Bash a\\nb (decoy workdir)"
    )
