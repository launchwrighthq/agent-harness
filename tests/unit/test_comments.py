from pathlib import Path

from agent_harness import comments, config

BUDGET = comments.budget_of(config.Config(Path("."), {}, None))


def test_counts_a_jsdoc_block_line_comments_and_code_in_ts() -> None:
    measured = comments.measure_text(
        "/**\n * a\n */\n// b\nconst x = 1;\n\nconst y = 2;\n", "a.mts"
    )

    assert measured == comments.Measure(comment=4, code=2, share=4 / 6, longest=4)


def test_counts_hash_comments_and_a_docstring_in_python_and_not_a_shebang() -> None:
    source = '#!/usr/bin/env python3\n"""Doc\nmore\n"""\n# note\nx = 1\n'

    assert comments.measure_text(source, "a.py") == comments.Measure(4, 2, 4 / 6, 4)


def test_reads_source_files_and_leaves_out_tests_docs_and_generated_files() -> None:
    read = ["src/a.mts", "app/b.tsx", "tools/c.py", "lib/d.cjs"]
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
