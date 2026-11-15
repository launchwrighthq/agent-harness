from pathlib import Path

import pytest

from agent_harness import HarnessError, comments, config

BUDGET = comments.budget_of(config.Config(Path("."), {}, None))


def test_counts_a_jsdoc_block_line_comments_and_code_in_ts() -> None:
    measured = comments.measure_text(
        "/**\n * a\n */\n// b\nconst x = 1;\n\nconst y = 2;\n", "a.mts"
    )

    assert measured == comments.Measure(4, 2, 4 / 6, longest=4, widest=4, widest_at=4)


def test_counts_hash_comments_and_a_docstring_in_python_and_not_a_shebang() -> None:
    source = '#!/usr/bin/env python3\n"""Doc\nmore\n"""\n# note\nx = 1\n'

    assert comments.measure_text(source, "a.py") == comments.Measure(4, 2, 4 / 6, 4, 6, 2)


def test_counts_hash_and_slash_lines_and_a_block_in_terraform() -> None:
    source = '# a\n// b\n/* c\n d */\nresource "x" "y" {}\n'

    assert comments.measure_text(source, "infra/main.tf") == comments.Measure(4, 1, 0.8, 4, 4, 2)


def test_reports_the_widest_comment_line_over_the_line_cap() -> None:
    measured = comments.Measure(comment=1, code=1, share=0.5, longest=1, widest=101, widest_at=7)
    budget = {**BUDGET, "minLines": 0, "maxLineLength": 100}

    assert comments.findings("a.tf", measured, False, budget) == [
        "a.tf:7 has a 101-character comment line, cap 100"
    ]
    assert comments.findings("a.tf", measured, False, BUDGET) == []


def test_gives_a_file_the_caps_of_the_first_override_that_matches_it() -> None:
    tf = {"maxBlock": 1, "longBlock": 1, "maxLineLength": 100}
    budget = {**BUDGET, "overrides": {"infra/**/*.tf": tf, "**/*.tf": {"maxBlock": 3}}}

    assert comments.caps_for("infra/a/main.tf", budget)["longBlock"] == 1
    assert comments.caps_for("modules/main.tf", budget)["maxBlock"] == 3
    assert comments.caps_for("src/a.mts", budget)["maxBlock"] == BUDGET["maxBlock"]


def test_refuses_an_override_key_that_is_not_a_cap() -> None:
    budget = {**BUDGET, "overrides": {"**/*.tf": {"include": ["x"]}}}

    with pytest.raises(HarnessError, match="sets include"):
        comments.caps_for("main.tf", budget)


def test_reads_source_files_and_leaves_out_tests_docs_and_generated_files() -> None:
    read = ["src/a.mts", "app/b.tsx", "tools/c.py", "lib/d.cjs", "infra/main.tf"]
    skipped = ["src/tests/a.mts", "src/a.test.mts", "app/test_b.py", "types/x.d.ts"]
    skipped += ["docs/x.py", ".claude/hooks/x.py", "node_modules/p/i.js", "README.md", "run.sh"]

    assert [p for p in read + skipped if comments.in_scope(p, BUDGET)] == read


def test_holds_a_new_file_to_the_tight_cap_and_an_old_file_to_the_long_one() -> None:
    measured = comments.Measure(comment=13, code=10, share=13 / 23, longest=13)

    assert comments.findings("a.mts", measured, True, BUDGET) == [
        "a.mts has a 13-line comment block, cap 12"
    ]
    assert comments.findings("a.mts", measured, False, BUDGET) == []


def test_leaves_a_short_file_alone() -> None:
    measured = comments.Measure(comment=15, code=4, share=15 / 19, longest=15)

    assert comments.findings("a.mts", measured, True, BUDGET) == []


def test_matches_a_double_star_across_folders_and_a_star_inside_one_name() -> None:
    assert comments.glob_pattern("**/tests/**").match("a/b/tests/c/d.py")
    assert comments.glob_pattern("**/tests/**").match("tests/d.py")
    assert not comments.glob_pattern("src/*.py").match("src/a/b.py")
    assert comments.glob_pattern("src/?.py").match("src/a.py")
