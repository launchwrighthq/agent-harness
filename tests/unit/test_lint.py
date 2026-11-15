from pathlib import Path

import pytest

from agent_harness import lint
from agent_harness.lint import lint_paths, lint_text

DASH = "—"


def found(name: str, text: str, **options: list[str]) -> list[str]:
    return [f"{f.line}:{f.column} {f.rule}" for f in lint_text(name, text, **options)]


def test_flags_words_fillers_and_dashes_in_markdown_prose() -> None:
    md = "\n".join(
        [
            "# Title",
            "",
            "We leverage the cache. It\u2019s worth noting that it is robust.",
            f"A long pause {DASH} then more.",
            "The `leverage` flag in code is fine.",
            "```",
            "leverage inside a fence is fine",
            "```",
            "A test harness and this harness module are fine; harnessing is not.",
            "This line says leverage lw-lint-ok.",
        ]
    )

    assert found("doc.md", md) == [
        "3:4 banned-word",
        "3:24 banned-filler",
        "3:53 banned-word",
        "4:14 em-dash",
        "9:50 banned-word",
    ]


def test_reads_only_the_comments_of_code() -> None:
    ts = "\n".join(
        [
            "const label = 'leverage'; // we leverage this",
            "/* a robust block",
            "   still robust */ const x = 1;",
            "const url = 'https://example.com'; // plain",
            f"// a dash {DASH} here",
        ]
    )
    py = 'x = "# not a comment, robust"\ny = 1  # utilize it\n#!/not/a/shebang here'

    assert [f.line for f in lint_text("a.mts", ts)] == [1, 2, 3, 5]
    assert [f.line for f in lint_text("a.py", py)] == [2]
    assert [f.line for f in lint_text("script", "#!/usr/bin/env bash\n# delve in\necho ok\n")] == [
        2
    ]
    assert lint_text("data.json", f'{{"a": "robust {DASH}"}}') == []


def test_takes_extra_words_and_an_allow_list() -> None:
    assert found("a.md", "A seamless flow.", words=["seamless"]) == ["1:3 banned-word"]
    assert found("a.md", "A robust flow.", allow=["robust"]) == []


def test_prints_each_finding_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("Fine text.\nWe delve here.\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "b.md").write_text("We delve here.\n")

    findings = lint_paths([tmp_path])

    assert [f.format() for f in findings] == [
        f'{tmp_path / "docs" / "a.md"}:2:4 banned-word banned word "delve" (delve);'
        " name the plain fact"
    ]
    assert lint.main([]) == 2
    assert "usage: ah lint" in capsys.readouterr().err
