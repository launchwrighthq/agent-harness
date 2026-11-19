import pytest

from agent_harness.shell_steps import Heredoc, ShellSyntaxError, Step, parse


def commands(steps: list[Step]) -> list[list[str]]:
    return [[w.text for w in step.words] for step in steps]


def test_splits_a_line_into_steps_at_each_shell_operator() -> None:
    steps = parse("a 1; b 2 && c || d | e |& f & g\nh (i) { j; }")

    assert commands(steps) == [
        ["a", "1"],
        ["b", "2"],
        ["c"],
        ["d"],
        ["e"],
        ["f"],
        ["g"],
        ["h"],
        ["i"],
        ["j"],
    ]


def test_steps_over_assignments_and_keywords_to_the_command_word() -> None:
    steps = parse(
        "X=1 Y='a b' cmd x; if true; then ah env status; fi\n"
        "for f in a; do ah env keys $f; done; ! ah env check"
    )

    assert commands(steps) == [
        ["cmd", "x"],
        ["true"],
        ["ah", "env", "status"],
        ["for", "f", "in", "a"],
        ["ah", "env", "keys", "$f"],
        ["ah", "env", "check"],
    ]
    assert [w.text for w in steps[0].assignments] == ["X=1", "Y=a b"]


def test_removes_quotes_and_marks_each_word_that_bash_can_change() -> None:
    (step,) = parse("""grep "ah env\\|x" 'a b' $F/x "$S/y" *.py ~/z {a,b} $'q' c\\d""")

    got = [(w.text, w.expands, w.hidden) for w in step.words]

    assert got == [
        ("grep", False, False),
        ("ah env\\|x", False, False),
        ("a b", False, False),
        ("$F/x", True, False),
        ("$S/y", False, True),
        ("*.py", True, False),
        ("~/z", True, False),
        ("{a,b}", True, False),
        ("q", False, True),
        ("cd", False, False),
    ]


def test_keeps_the_literal_text_of_a_word_apart_from_its_expansions() -> None:
    (step,) = parse('x "$SCRATCH/seed.env" "$F" a"$G"b')

    assert [w.literal for w in step.words] == ["x", "/seed.env", "", "ab"]


def test_gives_the_span_of_each_word_in_the_line() -> None:
    text = "a \"b c\"  d'e' 2>&1 >out"

    (step,) = parse(text)

    assert [text[w.start : w.end] for w in step.words] == ["a", '"b c"', "d'e'"]
    assert [w.text for w in step.redirects] == ["1", "out"]


def test_takes_a_heredoc_body_out_of_the_command_line() -> None:
    steps = parse("git commit -F - <<'EOF'\nah env get A .env --reveal; x\nEOF\nls")

    assert commands(steps) == [["git", "commit", "-F", "-"], ["ls"]]
    assert steps[0].heredocs == [Heredoc("ah env get A .env --reveal; x\n", quoted=True)]


def test_reads_a_tab_stripped_heredoc_and_a_here_string() -> None:
    steps = parse("cat <<-END\n\tbody\n\tEND\nbash <<< 'ah env status'")

    assert steps[0].heredocs == [Heredoc("body\n", quoted=False)]
    assert steps[1].heredocs == [Heredoc("ah env status", quoted=True)]


def test_parses_each_command_substitution_as_its_own_steps() -> None:
    (step,) = parse('echo "$(ah env get A f --reveal)" `ah env keys f` <(ah env status)')

    assert [commands(nested) for nested in step.nested] == [
        [["ah", "env", "get", "A", "f", "--reveal"]],
        [["ah", "env", "keys", "f"]],
        [["ah", "env", "status"]],
    ]


def test_reads_the_substitutions_of_an_unquoted_heredoc_body_only() -> None:
    (open_body,) = parse("cat <<EOF\n$(ah env status)\nEOF")
    (quoted_body,) = parse("cat <<'EOF'\n$(ah env status)\nEOF")

    assert [commands(nested) for nested in open_body.nested] == [[["ah", "env", "status"]]]
    assert quoted_body.nested == []


def test_reads_a_heredoc_inside_a_substitution_with_a_quote_in_its_body() -> None:
    (step,) = parse("git commit -m \"$(cat <<'EOF'\nit's done\nEOF\n)\"")

    assert step.nested[0][0].heredocs == [Heredoc("it's done\n", quoted=True)]


def test_reads_an_arithmetic_command_as_no_heredoc() -> None:
    steps = parse("((x<<2))\nah env status\necho $((1<<3))")

    assert commands(steps) == [["ah", "env", "status"], ["echo", "$((1<<3))"]]


@pytest.mark.parametrize(
    "text",
    ["echo 'x", 'echo "x', "echo $(x", "echo `x", "echo ${x:-$(y)}", "echo $'x"],
)
def test_refuses_an_unclosed_quote_or_a_substitution_it_cannot_read(text: str) -> None:
    with pytest.raises(ShellSyntaxError):
        parse(text)
