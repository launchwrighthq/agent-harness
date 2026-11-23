from pathlib import Path

import pytest

from agent_harness import HarnessError, comments, config

BUDGET = comments.budget_of(config.Config(Path("."), {}, None))


def test_counts_a_jsdoc_block_line_comments_and_code_in_ts() -> None:
    measured = comments.measure_text(
        "/**\n * a\n */\n// b\nconst x = 1;\n\nconst y = 2;\n", "a.mts"
    )

    assert measured == comments.Measure(4, 2, 4 / 6, longest=4, widest=4, widest_at=4, head=4)


def test_counts_hash_comments_and_a_docstring_in_python_and_not_a_shebang() -> None:
    source = '#!/usr/bin/env python3\n"""Doc\nmore\n"""\n# note\nx = 1\n'

    measured = comments.measure_text(source, "a.py")

    assert measured == comments.Measure(4, 2, 4 / 6, 4, 6, 2, head=4, module_doc=3)


def test_caps_the_module_docstring_of_an_old_python_file_at_max_block() -> None:
    long_doc = '"""' + "\n".join(f"line {i}" for i in range(13)) + '\n"""\n'
    source = "# coding note\n" + long_doc + "".join(f"x{i} = {i}\n" for i in range(20))
    short = '"""One line."""\n' + "".join(f"x{i} = {i}\n" for i in range(20))

    measured = comments.measure_text(source, "a.py")

    assert measured.module_doc == 14
    assert comments.findings("a.py", measured, False, BUDGET) == [
        "a.py has a 14-line module docstring, cap 12"
    ]
    assert comments.measure_text(short, "a.py").module_doc == 1
    assert comments.measure_text(long_doc, "a.sh").module_doc == 0


def test_counts_hash_and_slash_lines_and_a_block_in_terraform() -> None:
    source = '# a\n// b\n/* c\n d */\nresource "x" "y" {}\n'

    measured = comments.measure_text(source, "infra/main.tf")

    assert measured == comments.Measure(4, 1, 0.8, 4, 4, 2, head=4)


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


def test_counts_hash_lines_in_a_workflow_a_dockerfile_and_an_env_template() -> None:
    source = "# one\n# two\n\nname: build\n# three\non: push\n"
    expected = comments.Measure(3, 2, 0.6, longest=2, widest=7, widest_at=5, head=2, inner=1)

    for path in (".github/workflows/build.yml", "deploy/Dockerfile", "apps/api/.env.example"):
        assert comments.measure_text(source, path) == expected


def test_counts_the_block_after_a_shebang_as_the_opening_block_and_no_later_block() -> None:
    assert comments.measure_text("#!/bin/sh\n\n# a\n# b\nx=1\n# c\n", "run.sh").head == 2
    assert comments.measure_text("x=1\n# a\n# b\n", "run.sh").head == 0
    assert comments.measure_text("x=1\n# a\n# b\n", "run.sh").inner == 2


def test_reads_a_config_file_only_when_an_include_or_override_pattern_names_it() -> None:
    files = [".github/workflows/build.yml", "deploy/Dockerfile", "deploy/Dockerfile.worker"]
    files += ["images/api.dockerfile", "compose.yaml", ".env.example", "apps/api/.env.ci.example"]
    capped = {**BUDGET, "overrides": {"**/*.yml": {"maxBlock": 3}, "**/.env*.example": {}}}
    included = {**BUDGET, "include": ["**/Dockerfile*", "**/*.dockerfile"]}

    assert [p for p in files if comments.in_scope(p, BUDGET)] == []
    assert [p for p in files if comments.in_scope(p, capped)] == [files[0], *files[5:]]
    assert [p for p in files if comments.in_scope(p, included)] == files[1:4]


def test_never_reads_a_live_env_file_and_keeps_the_exclude_list_for_a_config_file() -> None:
    budget = {**BUDGET, "include": ["**/*"], "overrides": {"**/.env*": {}, "**/*.yml": {}}}
    live = [".env", ".env.local", "apps/api/.env.production", ".env.example.bak"]

    assert [p for p in [*live, "docs/site.yml"] if comments.in_scope(p, budget)] == []


def test_caps_the_opening_block_apart_from_the_other_blocks_with_a_head_cap() -> None:
    measured = comments.Measure(comment=6, code=4, share=0.6, longest=4, head=4, inner=2)
    budget = {**BUDGET, "minLines": 0, "headBlock": 3, "maxBlock": 1, "longBlock": 1}

    assert comments.findings(".env.example", measured, False, budget) == [
        ".env.example has a 4-line opening comment block, cap 3",
        ".env.example has a 2-line comment block, cap 1",
    ]
    assert comments.findings(".env.example", measured, False, {**budget, "headBlock": 4}) == [
        ".env.example has a 2-line comment block, cap 1"
    ]
    assert comments.findings(".env.example", measured, False, {**BUDGET, "minLines": 0}) == []
