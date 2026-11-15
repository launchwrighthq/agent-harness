import json
from pathlib import Path

import pytest

from agent_harness import land_results


def record(checks: dict[str, object]) -> str:
    return f"verify: failures {json.dumps(checks)}"


RED = {
    "lint": {"code": 0, "attributable": False, "failedFiles": []},
    "test": {"code": 1, "attributable": True, "failedFiles": ["b.test.mts", "a.test.mts"]},
    "e2e": {"code": 1, "attributable": True, "failedFiles": ["a.test.mts"]},
}


def test_lists_each_attributable_failed_file_once_and_sorted() -> None:
    assert land_results.failed_files(land_results.checks(record(RED))) == [
        "a.test.mts",
        "b.test.mts",
    ]


def test_lists_a_failed_step_that_names_no_file_as_unaccounted() -> None:
    checks = {**RED, "typecheck": {"code": 2, "attributable": False, "failedFiles": []}}

    assert land_results.unaccounted(land_results.checks(record(checks))) == ["typecheck"]


def test_reads_the_last_record_of_the_log() -> None:
    old = {"test": {"code": 1, "attributable": False, "failedFiles": []}}
    text = "\n".join(["start", record(old), "more output", record(RED), "end"])

    assert land_results.unaccounted(land_results.checks(text)) == []


@pytest.mark.parametrize(
    "text",
    [
        "verify: red\n",
        "verify: failures {not json\n",
        'verify: failures ["test"]\n',
        'verify: failures {"test": {"code": 1, "attributable": true, "failedFiles": "a"}}\n',
        record(RED) + "\nverify: refused result record: stale\n",
    ],
)
def test_reads_a_log_without_a_valid_record_as_one_unaccounted_gate(text: str) -> None:
    checks = land_results.checks(text)

    assert land_results.unaccounted(checks) == ["gate"]
    assert land_results.failed_files(checks) == []


def test_prints_the_files_of_a_log_named_on_the_command_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "gate.log"
    log.write_text(record(RED) + "\n", encoding="utf-8")

    code = land_results.main(["files", str(log)])

    assert code == 0
    assert capsys.readouterr().out == "a.test.mts\nb.test.mts\n"


def test_refuses_an_unknown_mode_with_the_usage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = land_results.main(["steps", str(tmp_path / "gate.log")])

    assert code == 2
    assert capsys.readouterr().err.startswith("usage: ah land-results files|unaccounted <log>")
