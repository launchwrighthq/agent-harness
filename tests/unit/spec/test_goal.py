import json
from pathlib import Path
from typing import Any

import pytest

from agent_harness.spec.goal import evaluate, hold_message

HEAD = "a" * 40
Audits = dict[str, dict[str, Any] | None]


@pytest.fixture
def auditor(tmp_path: Path) -> str:
    meta = tmp_path / "agent-x.meta.json"
    meta.write_text(json.dumps({"description": "spec-audit a"}), encoding="utf-8")
    return str(meta)


def make_goal(**overrides: Any) -> dict[str, Any]:
    return {"stems": ["a"], "base": "trunk", "level": "merged", **overrides}


def make_audit(auditor: str, *rows: dict[str, str], head: str = HEAD) -> dict[str, Any]:
    return {"stem": "a", "base": "trunk", "head": head, "auditor": auditor, "rows": list(rows)}


def done(rid: str) -> dict[str, str]:
    return {"id": rid, "requirement": "r", "state": "done"}


def test_holds_a_goal_whose_spec_has_no_saved_audit() -> None:
    verdict = evaluate(make_goal(), {"a": None}, HEAD, True, "", 0)

    assert verdict.kind == "no-audit"
    assert verdict.holds
    assert "Run /lw-spec-audit a" in verdict.reason


def test_holds_an_audit_of_an_older_base_commit(auditor: str) -> None:
    audits: Audits = {"a": make_audit(auditor, done("R1"), head="b" * 40)}

    verdict = evaluate(make_goal(), audits, HEAD, True, "", 0)

    assert verdict.kind == "stale-audit"
    assert verdict.notes["a"] == f"the audit read trunk at {'b' * 9}, trunk is {'a' * 9}"


def test_holds_open_rows_and_names_them(auditor: str) -> None:
    open_row = {"id": "R2", "requirement": "r", "state": "partly"}

    verdict = evaluate(
        make_goal(), {"a": make_audit(auditor, done("R1"), open_row)}, HEAD, True, "", 0
    )

    assert verdict.kind == "open"
    assert verdict.open_ids == {"a": ["R2"]}
    assert hold_message(verdict).startswith("goal-spec: not done (open)\na: open R2")


def test_meets_the_goal_when_every_row_is_done(auditor: str) -> None:
    verdict = evaluate(make_goal(), {"a": make_audit(auditor, done("R1"))}, HEAD, True, "", 0)

    assert verdict.kind == "met"
    assert not verdict.holds


def test_owes_a_push_at_the_pushed_level(auditor: str) -> None:
    audits: Audits = {"a": make_audit(auditor, done("R1"))}

    verdict = evaluate(make_goal(level="pushed"), audits, HEAD, False, "", 0)

    assert verdict.kind == "push-owed"
    assert "origin/trunk does not hold trunk" in verdict.reason


def test_ends_the_turn_once_the_reply_names_each_blocked_row(auditor: str) -> None:
    blocked = {"id": "R3", "requirement": "r", "state": "blocked", "question": "which?"}
    audits: Audits = {"a": make_audit(auditor, blocked)}

    assert evaluate(make_goal(), audits, HEAD, True, "about R12", 0).kind == "blocked-unasked"
    assert evaluate(make_goal(), audits, HEAD, True, "R3: which one?", 0).kind == "blocked-asked"


def test_lets_the_turn_end_while_agents_still_run() -> None:
    verdict = evaluate(make_goal(), {"a": None}, HEAD, True, "", 2)

    assert verdict.kind == "agents-running"
    assert not verdict.holds
