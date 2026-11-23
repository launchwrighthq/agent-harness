import pytest

from agent_harness.spec.layout import Domain, Layout
from agent_harness.spec.render import (
    SpecError,
    dist_for,
    inline,
    render_with_template,
    slug,
    source_for,
    split_code_spans,
)

LAYOUT = Layout(dirs=("docs/specs", "docs/superpowers/plans"), archive="archive", out="dist")
SHELL = "|".join(
    [
        "{{TITLE}}",
        "{{DATE}}",
        '<li class="chip">{{STATUS}}</li>',
        '<li class="chip">{{SUBJECT}}</li>',
        '<li class="chip">{{RANGE}}</li>',
        "{{STANDFIRST}}",
        "{{KIND}}",
        "{{KIND_CLASS}}",
        "{{NAV}}",
        "{{BODY}}",
        "{{SOURCE}}",
        "{{WORDMARK}}",
        "{{FOOTER}}",
    ]
)
HEAD = "# T\n\nDate: 2026-01-01\nStatus: draft\n\n"


def body_of(markdown: str, source: str = "docs/specs/a.md") -> str:
    html, _ = render_with_template(markdown, source, SHELL, LAYOUT)
    return html.split("|")[9]


def test_derives_the_page_path_under_the_out_folder() -> None:
    assert dist_for("docs/specs/a.md", LAYOUT) == "docs/specs/dist/a.html"
    assert (
        dist_for("/r/docs/superpowers/plans/archive/b.md", LAYOUT)
        == "/r/docs/superpowers/plans/dist/archive/b.html"
    )


def test_maps_a_page_back_to_its_markdown_source() -> None:
    assert source_for("/r/docs/specs/dist/a.html", LAYOUT) == "/r/docs/specs/a.md"


def test_refuses_a_source_outside_every_spec_folder() -> None:
    with pytest.raises(
        SpecError, match=r"^notes/a\.md: not under docs/specs/, docs/superpowers/plans/"
    ):
        dist_for("notes/a.md", LAYOUT)


def test_closes_a_code_span_only_on_an_equal_backtick_run() -> None:
    pieces = split_code_spans("a `` `x` `` b")

    assert [(p.code, p.text) for p in pieces] == [(False, "a "), (True, "`x`"), (False, " b")]


def test_keeps_snake_case_names_plain_and_marks_italic() -> None:
    assert inline("a snake_case_name and _this_") == "a snake_case_name and <em>this</em>"


def test_prefixes_an_asset_image_with_the_walk_back_from_the_page() -> None:
    assert (
        inline("![x](assets/a.png)", "../") == '<img src="../assets/a.png" alt="x" loading="lazy">'
    )


def test_renders_an_image_that_the_check_names_as_a_placeholder_box() -> None:
    assert (
        inline(
            "![x](assets/<a>.png) ![y](assets/b.png)",
            "../",
            lambda url: "gone" if "<a>" in url else None,
        )
        == '<span class="missing-asset">image missing: assets/&lt;a&gt;.png</span>'
        ' <img src="../assets/b.png" alt="y" loading="lazy">'
    )


def test_collects_one_warning_per_missing_image_of_the_header_and_the_body() -> None:
    warnings: list[str] = []
    markdown = HEAD + "![s](assets/s.png)\n\n## One\n\n| a |\n|---|\n| ![t](assets/t.mp4) |\n"

    html, _ = render_with_template(
        markdown, "docs/specs/a.md", SHELL, LAYOUT, lambda url: f"no {url}", warnings
    )

    assert warnings == ["docs/specs/a.md:6: no assets/s.png", "docs/specs/a.md:12: no assets/t.mp4"]
    assert html.count('<span class="missing-asset">image missing: assets/') == 2
    assert "<img" not in html
    assert "<video" not in html


def test_refuses_an_inline_data_image_and_names_the_line() -> None:
    with pytest.raises(SpecError, match=r"^docs/specs/a\.md:6: an inline data: image"):
        body_of(HEAD + "![x](data:image/png;base64,AAAA)\n")


def test_refuses_a_status_outside_the_vocabulary() -> None:
    with pytest.raises(SpecError, match="Status 'done' is not one of draft, approved"):
        body_of("# T\n\nDate: 2026-01-01\nStatus: done\n")


def test_takes_the_date_from_the_file_name_prefix() -> None:
    html, _ = render_with_template(
        "# T\n\nStatus: draft\n", "docs/specs/2026-03-04-x.md", SHELL, LAYOUT
    )

    assert html.split("|")[1] == "2026-03-04"


def test_gives_a_repeated_heading_a_numbered_anchor() -> None:
    body = body_of(HEAD + "## A\n\n## A\n")

    assert 'id="a"' in body
    assert 'id="a-2"' in body


def test_keeps_an_ordered_list_open_across_an_indented_paragraph() -> None:
    body = body_of(HEAD + "## S\n\n1. One\n\n   More of one.\n2. Two\n")

    assert body.endswith("\n<ol><li>One<p>More of one.</p></li><li>Two</li></ol>")


def test_keeps_a_placeholder_quoted_in_a_heading_as_text() -> None:
    body = body_of(HEAD + "## Quote {{TITLE}}\n")

    assert "Quote {{TITLE}}</h2>" in body


def test_slugs_a_heading_to_lowercase_ascii_words() -> None:
    assert slug("2.1 The \u00dcn\u00efcode Title!") == "2-1-the-n-code-title"


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "java\tscript:alert(1)",
        " javascript:alert(1)",
        "data:text/html,x",
        "vbscript:x",
        "file:///etc/hosts",
        "//evil.test/x",
    ],
)
def test_refuses_a_link_with_a_scheme_outside_the_list_and_names_the_line(href: str) -> None:
    with pytest.raises(SpecError, match=r"^docs/specs/a\.md:8: a link "):
        body_of(HEAD + f"## S\n\nSee [x]({href}).\n")


def test_refuses_a_script_link_in_a_table_cell() -> None:
    with pytest.raises(SpecError, match=r"^docs/specs/a\.md:10: a link must be a relative path"):
        body_of(HEAD + "## S\n\n| A |\n|---|\n| [x](javascript:y) |\n")


def test_refuses_a_script_link_in_the_standfirst() -> None:
    with pytest.raises(SpecError, match=r"^docs/specs/a\.md:6: a link must be a relative path"):
        body_of(HEAD + "See [x](javascript:y).\n\n## S\n")


def test_renders_relative_fragment_asset_and_web_links() -> None:
    hrefs = ["b.md", "../c/d.md#s", "#top", "assets/e.pdf", "https://x.test", "mailto:a@x.example"]
    expected = [
        "b.md",
        "../c/d.md#s",
        "#top",
        "../assets/e.pdf",
        "https://x.test",
        "mailto:a@x.example",
    ]

    got = [inline(f"[l]({href})", "../") for href in hrefs]

    assert got == [f'<a href="{href}">l</a>' for href in expected]


def test_keeps_a_script_link_inside_a_code_span_as_text() -> None:
    assert inline("`[x](javascript:y)`") == "<code>[x](javascript:y)</code>"


QA_LAYOUT = Layout(
    dirs=("docs/qa",),
    archive="archive",
    out="dist",
    kinds=(("docs/qa", Domain("docs/qa", "qa", "QA REPORT", ("untested", "tested"))),),
)


def test_renders_a_qa_report_with_its_badge_and_its_own_status() -> None:
    html, _ = render_with_template(
        "# R\n\nDate: 2026-01-01\nStatus: tested\n", "docs/qa/r.md", SHELL, QA_LAYOUT
    )

    fields = html.split("|")
    assert fields[2] == '<li class="chip is-live">tested</li>'
    assert fields[6:8] == ["QA REPORT", "kind-qa"]


def test_gives_an_untested_qa_report_a_chip_that_is_not_live() -> None:
    html, _ = render_with_template(
        "# R\n\nDate: 2026-01-01\nStatus: untested\n", "docs/qa/r.md", SHELL, QA_LAYOUT
    )

    assert html.split("|")[2] == '<li class="chip">untested</li>'


def test_refuses_a_status_outside_the_vocabulary_of_its_folder() -> None:
    with pytest.raises(SpecError, match="Status 'draft' is not one of untested, tested"):
        render_with_template(
            "# R\n\nDate: 2026-01-01\nStatus: draft\n", "docs/qa/r.md", SHELL, QA_LAYOUT
        )


def test_escapes_the_badge_word_of_the_folder() -> None:
    marked = Layout(
        dirs=("docs/qa",),
        archive="archive",
        out="dist",
        kinds=(("docs/qa", Domain("docs/qa", "qa", "<b>QA</b>")),),
    )

    html, _ = render_with_template(
        "# R\n\nDate: 2026-01-01\nStatus: draft\n", "docs/qa/r.md", SHELL, marked
    )

    assert html.split("|")[6] == "&lt;b&gt;QA&lt;/b&gt;"


NESTED_LAYOUT = Layout(
    dirs=("docs/specs",),
    archive="archive",
    out="dist",
    kinds=(
        (
            "docs/specs/assets",
            Domain("docs/specs/assets", "qa", "QA REPORT", ("untested", "tested")),
        ),
    ),
)


def test_gives_a_file_the_kind_and_page_folder_of_its_longest_kinds_folder() -> None:
    html, _ = render_with_template(
        "# R\n\nDate: 2026-01-01\nStatus: tested\n", "docs/specs/assets/r.md", SHELL, NESTED_LAYOUT
    )

    assert html.split("|")[6:8] == ["QA REPORT", "kind-qa"]
    assert dist_for("docs/specs/assets/r.md", NESTED_LAYOUT) == "docs/specs/assets/dist/r.html"
    assert dist_for("docs/specs/a.md", NESTED_LAYOUT) == "docs/specs/dist/a.html"
    assert source_for("/r/docs/specs/assets/dist/r.html", NESTED_LAYOUT) == (
        "/r/docs/specs/assets/r.md"
    )


CHECK = '<div class="code" data-lang="bash"><pre><code>ah check</code></pre></div>'
ASK = "<blockquote><p>Is it done?</p></blockquote>"


@pytest.mark.parametrize(
    ("markdown", "html"),
    [
        ("- Run it:\n\n  ```bash\n  ah check\n  ```\n", f"<ul><li>Run it:{CHECK}</li></ul>"),
        ("- Run it:\n  ```bash\n  ah check\n  ```\n", f"<ul><li>Run it:{CHECK}</li></ul>"),
        (
            "1. Run it:\n\n   ```bash\n   ah check\n   ```\n2. Next\n",
            f"<ol><li>Run it:{CHECK}</li><li>Next</li></ol>",
        ),
        ("- Ask:\n  > Is it done?\n", f"<ul><li>Ask:{ASK}</li></ul>"),
        ("1. Ask:\n\n   > Is it done?\n", f"<ol><li>Ask:{ASK}</li></ol>"),
        (
            "- Steps:\n\n  ```text\n  - not an item\n  1. not either\n  ```\n",
            '<ul><li>Steps:<div class="code" data-lang="text"><pre><code>- not an item\n'
            "1. not either</code></pre></div></li></ul>",
        ),
        (
            "- Run it:\n\n  ```bash\n  ah check\n  ```\n\n  It prints the result.\n- Next\n",
            f"<ul><li>Run it:{CHECK}<p>It prints the result.</p></li><li>Next</li></ul>",
        ),
    ],
)
def test_renders_a_block_indented_under_a_list_item_inside_that_item(
    markdown: str, html: str
) -> None:
    body = body_of(HEAD + "## S\n\n" + markdown)

    assert body.endswith("\n" + html)


def test_refuses_a_fenced_code_line_left_of_its_list_item_and_says_how_to_indent_it() -> None:
    with pytest.raises(SpecError) as refused:
        body_of(HEAD + "## S\n\n- Run it:\n\n  ```bash\nah check\n  ```\n")

    assert str(refused.value) == (
        "docs/specs/a.md:11: a fenced code block in a list item has a line at or left of the item"
        " marker. Indent every line of the block, the closing fence too, under the item text, or"
        " end the list with a blank line and start the fence at the left margin below it"
    )


def test_shows_an_image_with_the_page_source_that_the_caller_gives() -> None:
    sources = {"assets/board.svg": "data:image/svg+xml;base64,QQ=="}

    got = inline("![x](assets/board.svg) ![y](assets/plain.svg)", "../", None, sources.get)

    assert got == (
        '<img src="data:image/svg+xml;base64,QQ==" alt="x" loading="lazy"> '
        '<img src="../assets/plain.svg" alt="y" loading="lazy">'
    )


def test_writes_the_default_wordmark_and_footer_of_the_layout() -> None:
    html, _ = render_with_template(HEAD, "docs/specs/a.md", SHELL, LAYOUT)

    assert html.split("|")[11:] == ["spec", "Generated by ah spec render"]


def test_escapes_the_wordmark_and_the_footer_of_the_layout() -> None:
    named = Layout(
        dirs=("docs/specs",),
        archive="archive",
        out="dist",
        wordmark="<b>Orders</b> & more",
        footer='Made by <a href="x">the docs team</a>',
    )

    html, _ = render_with_template(HEAD, "docs/specs/a.md", SHELL, named)

    assert html.split("|")[11:] == [
        "&lt;b&gt;Orders&lt;/b&gt; &amp; more",
        'Made by &lt;a href="x"&gt;the docs team&lt;/a&gt;',
    ]
