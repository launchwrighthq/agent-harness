from pathlib import Path

import pytest

from agent_harness import HarnessError, config
from agent_harness.comment_refs import compile_patterns, in_scope, ref_findings

DEFAULTS = config.Config(Path("."), {}, None)
PATTERNS = compile_patterns(DEFAULTS.get("harness.lint.commentPatterns"))
EXCLUDE = list(DEFAULTS.get("harness.lint.commentExclude"))


def found(name: str, text: str, allow: tuple[str, ...] = ()) -> list[str]:
    findings = ref_findings(name, text, PATTERNS, allow)
    return [f"{f.line} {f.message.split(':')[0]}" for f in findings]


def test_finds_each_default_reference_in_ts_comments() -> None:
    ts = "\n".join(
        [
            "// Row N31: the retry limit.",
            "/* P2-D4 and (A9) */ const x = 1;",
            "const id = 'R40'; // spec 4.2 says so",
            "// added 2026-11-21, ported from the old service",
            "// plain comment about retries",
        ]
    )

    assert found("a.mts", ts) == [
        '1 spec-id "N31"',
        '2 spec-id "P2-D4"',
        '2 spec-id "A9"',
        '3 spec-section "spec 4.2"',
        '4 date "2026-11-21"',
        '4 ported-from "ported from"',
    ]


def test_reads_python_docstrings_and_passes_the_gap_marker() -> None:
    py = "\n".join(
        [
            '"""Implements R7 of the cache spec."""',
            "# lw-gap N3: remove when the store returns ids",
            "x = 'D8'  # D8",
            "y = 1  # lw-lint-ok R9 quoted on purpose",
        ]
    )

    assert found("a.py", py) == ['1 spec-id "R7"', '3 spec-id "D8"']


def test_skips_allowed_words_and_values_that_are_not_ids() -> None:
    ts = "// The R2 bucket, an L2 cache, color D6D6D6, P256 curve, and sha a1b2.\n"

    assert found("a.ts", ts, allow=("R2", "L2", "p256")) == []


def test_reads_code_only_and_honors_the_exclude_list() -> None:
    assert in_scope("src/a.mts", EXCLUDE)
    assert not in_scope("docs/a.mts", EXCLUDE)
    assert not in_scope("apps/web/node_modules/x/a.js", EXCLUDE)
    assert not in_scope("pyproject.toml", EXCLUDE)
    assert ref_findings("notes.md", "Row N31.", PATTERNS) == []


def test_turns_a_pattern_off_with_an_empty_value_and_refuses_a_bad_regex() -> None:
    assert list(compile_patterns({"date": "", "x": "a"})) == ["x"]
    with pytest.raises(HarnessError, match=r'commentPatterns\."bad"'):
        compile_patterns({"bad": "("})
