import io

from agent_harness.env.redact import Redaction, pump

TOKEN = "token-value-romeo-61"


def test_replaces_each_value_of_six_characters_or_more_in_a_line() -> None:
    redaction = Redaction([TOKEN, "short", "abcdef"])

    got = redaction.line(f"a {TOKEN} b short abcdef\n".encode())

    assert got == b"a *** b short ***\n"


def test_replaces_a_url_value_and_its_password_of_any_length() -> None:
    redaction = Redaction(["postgres://app:pw-x1@db.example/app"])

    got = redaction.line(b"url postgres://app:pw-x1@db.example/app pass pw-x1\n")

    assert got == b"url *** pass ***\n"


def test_replaces_each_line_of_a_value_that_spans_lines() -> None:
    redaction = Redaction(["-----BEGIN KEY-----\nMIIEvQIBADANBg\n-----END KEY-----"])

    assert redaction.line(b"MIIEvQIBADANBg\n") == b"***\n"


def test_replaces_a_longer_value_before_a_value_inside_it() -> None:
    redaction = Redaction(["secret", "secret-and-more"])

    assert redaction.line(b"secret-and-more\n") == b"***\n"


def test_copies_every_line_through_the_redaction_up_to_the_end() -> None:
    source = io.BytesIO(f"one {TOKEN}\ntwo\nlast {TOKEN}".encode())
    sink = io.BytesIO()

    pump(source, sink, Redaction([TOKEN]))

    assert sink.getvalue() == b"one ***\ntwo\nlast ***"
