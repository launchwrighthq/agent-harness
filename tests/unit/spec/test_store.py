import json
from pathlib import Path

import pytest

from agent_harness.spec import store


def row(rid: str, state: str, **extra: str) -> dict[str, str]:
    return {"id": rid, "requirement": f"requirement {rid}", "state": state, **extra}


def finding(fid: str, kind: str, severity: str = "blocking", state: str = "open") -> dict[str, str]:
    return {
        "id": fid,
        "kind": kind,
        "severity": severity,
        "state": state,
        "where": "a.py:1",
        "finding": "text",
        "quote": "",
    }


def test_turns_an_unquoted_deferral_into_a_missing_row() -> None:
    rows = store.normalise_rows([row("R1", "deferred-by-operator", evidence="later")])

    assert rows[0]["state"] == "missing"
    assert rows[0]["evidence"] == "later (deferral has no operator quote)"


def test_refuses_a_blocked_row_without_a_question() -> None:
    with pytest.raises(ValueError, match="R2: a blocked row names the question"):
        store.normalise_rows([row("R2", "blocked")])


def test_refuses_a_row_id_that_appears_twice() -> None:
    with pytest.raises(ValueError, match="row id R1 appears twice"):
        store.normalise_rows([row("R1", "done"), row("R1", "done")])


def test_lists_open_and_blocked_row_ids_apart() -> None:
    audit = {"rows": [row("R1", "done"), row("R2", "partly"), row("R3", "blocked", question="?")]}

    assert store.open_ids(audit) == ["R2"]
    assert store.blocked_ids(audit) == ["R3"]


def test_saves_an_audit_in_the_folder_the_override_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path))

    path = store.write_audit({"stem": "a-spec", "rows": [row("R1", "done")]})

    assert path == tmp_path / "spec-audit" / "a-spec.json"
    assert json.loads(path.read_text())["rows"][0]["id"] == "R1"
    assert store.load_audit("a-spec") is not None


def test_demotes_a_style_finding_and_approves_without_blockers() -> None:
    answer = store.normalise_answer(
        {"verdict": "not-approved", "findings": [finding("F1", "style")]}
    )

    assert answer["verdict"] == "approved"
    assert answer["findings"][0]["severity"] == "note"


def test_keeps_an_unquoted_waiver_open_and_blocking() -> None:
    answer = store.normalise_answer(
        {"verdict": "approved", "findings": [finding("F1", "defect", state="waived-by-operator")]}
    )

    assert answer["verdict"] == "not-approved"
    assert store.open_blocking_ids(answer) == ["F1"]


def test_reaches_the_pass_cap_on_counted_reviews() -> None:
    assert store.at_the_cap({"reviews": store.PASS_CAP})
    assert not store.at_the_cap({"reviews": store.PASS_CAP - 1})
    assert not store.at_the_cap(None)


def test_reads_a_bold_status_in_the_first_window(tmp_path: Path) -> None:
    spec = tmp_path / "a.md"
    spec.write_text("# A\n\n**Status:** Implemented - landed\n", encoding="utf-8")

    assert store.status_of(spec) == "implemented"


def test_accepts_an_auditor_file_whose_description_names_the_auditor(tmp_path: Path) -> None:
    meta = tmp_path / "agent-a.meta.json"
    meta.write_text(json.dumps({"description": "spec-audit a-spec"}), encoding="utf-8")
    other = tmp_path / "agent-b.meta.json"
    other.write_text(json.dumps({"description": "build a-spec"}), encoding="utf-8")

    assert store.auditor_is_valid({"auditor": str(meta)})
    assert not store.auditor_is_valid({"auditor": str(other)})
    assert not store.auditor_is_valid({"auditor": None})
