import pytest

from agent_harness import HarnessError
from agent_harness.spec.restate import restate_text


def test_rewrites_the_first_status_line_only() -> None:
    text = "# A\n\nStatus: draft\n\nStatus: draft again\n"

    assert restate_text(text, "implemented - `a.py`") == (
        "# A\n\nStatus: implemented - `a.py`\n\nStatus: draft again\n"
    )


def test_keeps_the_comment_close_of_an_html_status() -> None:
    assert restate_text("<!-- Status: draft -->\n", "answered") == "<!-- Status: answered -->\n"


def test_keeps_the_earliest_closer_and_the_header_text_after_it() -> None:
    text = "<span>Status: draft &middot; Date: 2026-01-01</span>\n"

    assert restate_text(text, "answered") == (
        "<span>Status: answered &middot; Date: 2026-01-01</span>\n"
    )


def test_finds_a_status_token_that_starts_inside_the_window_on_a_long_line() -> None:
    lead = "x" * 3990

    assert restate_text(f"{lead} Status: draft {'y' * 50}\n", "abandoned") == (
        f"{lead} Status: abandoned\n"
    )


def test_refuses_a_status_token_that_starts_past_the_window() -> None:
    with pytest.raises(HarnessError, match="no Status: line in the first 4000 characters"):
        restate_text(f"{'x' * 4000}Status: draft\n", "implemented")


def test_refuses_a_file_without_a_status_line() -> None:
    with pytest.raises(HarnessError, match="no Status: line in the first 4000 characters"):
        restate_text("# A\n", "implemented")
