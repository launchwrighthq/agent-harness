from agent_harness.spec.verify import markdown_words, visible, words


def test_keeps_the_text_after_an_unescaped_arrow_in_a_pre_block() -> None:
    text = visible("<body><pre>a <- b and more</pre></body>")

    assert words(text) == {"a": 1, "b": 1, "and": 1, "more": 1}


def test_drops_the_nav_and_inline_drawings_from_the_visible_text() -> None:
    page = "<body><nav>Contents One</nav><p>Body</p><svg><text>Label</text></svg></body>"

    assert words(visible(page)) == {"body": 1}


def test_counts_a_section_number_as_a_word() -> None:
    assert words("1. Scope, and 2.1 Rows.") == {"1": 1, "scope": 1, "and": 1, "2.1": 1, "rows": 1}


def test_drops_the_header_fields_and_image_targets_of_the_markdown() -> None:
    markdown = "# T\n\nDate: 2026-01-01\nStatus: draft\n\n![Board](assets/a-b.svg)\n"

    assert markdown_words(markdown) == {"t": 1, "board": 1}
