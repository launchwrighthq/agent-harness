from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.env import envfile

PEM_VALUE = (
    "-----BEGIN " + "PRIVATE KEY-----\nAAAAfirstline\n"
    "BBBBsecond\n-----END PRIVATE KEY-----"
)


def test_reads_a_quoted_value_that_spans_lines_with_a_blank_line() -> None:
    text = '# Keys\nCERT="line one\n\n# not a comment\nline four"\nNEXT=after\n'

    values = envfile.parse(text).values()

    assert values == {"CERT": "line one\n\n# not a comment\nline four", "NEXT": "after"}


def test_names_only_the_line_number_of_a_stray_line() -> None:
    with pytest.raises(HarnessError) as caught:
        envfile.parse("stray-secret-text\nKEY=value\n", ".env.local")

    assert str(caught.value) == (
        ".env.local: line 1 is neither blank, a comment nor a KEY=value line"
    )


def test_folds_a_pem_key_onto_one_line_and_reads_it_back() -> None:
    lines = envfile.lines_for("TLS_KEY", PEM_VALUE)

    assert len(lines) == 1
    head = 'TLS_KEY="-----BEGIN ' + 'PRIVATE KEY-----\\nAAAA'
    assert lines[0].startswith(head)
    assert envfile.value_of(lines) == PEM_VALUE


def test_quotes_a_value_that_a_reader_would_misread() -> None:
    assert envfile.lines_for("A", "plain") == ["A=plain"]
    assert envfile.lines_for("A", "x #y") == ['A="x #y"']
    assert envfile.lines_for("A", 'say "hi" ') == ["A='say \"hi\" '"]
    assert envfile.lines_for("A", "one\ntwo") == ['A="one', 'two"']


def test_refuses_a_value_with_both_quotes_that_needs_quotes() -> None:
    with pytest.raises(HarnessError) as caught:
        envfile.lines_for("A", " both ' and \" ")

    assert "holds both quote characters" in str(caught.value)
    assert "both '" not in str(caught.value)


def test_digests_the_key_values_so_a_reformat_keeps_the_digest() -> None:
    first = envfile.content_digest("# App\nB=2\nA='one'\n")
    reformatted = envfile.content_digest("# Other\nA=one\n\n# Block\nB=2\n")
    edited = envfile.content_digest("A=one\nB=3\n")

    assert first == reformatted
    assert first != edited
    assert len(first) == 64


def test_saves_with_mode_0600_and_keeps_entry_bytes(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text("# App\nA='kept as written'\nB=2\n", encoding="utf-8")
    env = envfile.read(path)
    envfile.remove_entries(env, "B")

    envfile.save(path, env, {"A": "kept as written"})

    assert path.read_text(encoding="utf-8") == "# App\nA='kept as written'\n"
    assert path.stat().st_mode & 0o777 == 0o600


def test_refuses_to_save_text_that_reads_back_wrong(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    env = envfile.parse("A=one\n")

    with pytest.raises(HarnessError) as caught:
        envfile.save(path, env, {"A": "two"})

    assert "would not read back the same values for A" in str(caught.value)
    assert not path.exists()


def test_removes_a_block_that_loses_its_last_entry() -> None:
    env = envfile.parse("# One\nA=1\n\n# Two\nB=2\n")

    assert envfile.remove_entries(env, "A") == 1
    assert env.render() == "# Two\nB=2\n"
