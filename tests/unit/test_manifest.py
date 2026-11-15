from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.manifest import Fixture, extra_name, parse_manifest, read_manifest

HOOK = """
[[hook]]
id = "guard"
summary = "A guard"
events = ["PreToolUse"]
matcher = "Bash"
script = "hooks/guard.py"

[[hook.fixture]]
tool = "Bash"
command = "rm -rf x"
expect = "deny"
contains = "refused"
"""


def parse(text: str, tmp_path: Path, *, extra: bool = False, check_files: bool = False) -> None:
    parse_manifest(
        text,
        name="pkg",
        dir=tmp_path,
        file=tmp_path / "harness.toml",
        extra=extra,
        check_files=check_files,
    )


def test_reads_a_hook_with_its_fixtures_and_defaults(tmp_path: Path) -> None:
    m = parse_manifest(
        HOOK,
        name="pkg",
        dir=tmp_path,
        file=tmp_path / "harness.toml",
        extra=False,
        check_files=False,
    )

    hook = m.hooks[0]
    assert (hook.id, hook.events, hook.matcher, hook.timeout, hook.codex, hook.default) == (
        "guard",
        ("PreToolUse",),
        "Bash",
        5,
        True,
        True,
    )
    assert hook.fixtures == (
        Fixture(expect="deny", tool="Bash", command="rm -rf x", contains="refused"),
    )


def test_reads_an_ask_expectation_of_a_fixture(tmp_path: Path) -> None:
    text = HOOK.replace('expect = "deny"', 'expect = "ask"')

    m = parse_manifest(
        text, name="pkg", dir=tmp_path, file=tmp_path / "h.toml", extra=False, check_files=False
    )

    assert m.hooks[0].fixtures[0].expect == "ask"


def test_reads_on_and_off_as_the_default_of_an_item(tmp_path: Path) -> None:
    text = '[[skill]]\nid = "lw-a"\ndir = "a"\ndefault = "off"\n'
    m = parse_manifest(
        text, name="pkg", dir=tmp_path, file=tmp_path / "h.toml", extra=False, check_files=False
    )

    assert m.skills[0].default is False


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ('[[rule]]\nid = "Bad_Id"\nsummary = "s"\nfile = "r.md"\n', "rule.0.id"),
        ('[[rule]]\nid = "r"\nsummary = "s"\nfile = "r.txt"\n', "a rule is a .md file"),
        ('[[rule]]\nid = "r"\nsummary = "s"\nfile = "../r.md"\n', "inside the manifest folder"),
        ('[[rule]]\nid = "r"\nsummary = "s"\nfile = "r.md"\ncolour = 1\n', "rule.0.colour"),
        ('[[hook]]\nid = "h"\nsummary = "s"\nevents = ["Start"]\nscript = "h.py"\n', "Start"),
        (
            '[[hook]]\nid = "h"\nsummary = "s"\nevents = ["Stop"]\nmatcher = "Bash"\n'
            'script = "h.py"\n',
            "has a matcher but no tool event",
        ),
        (
            '[[hook]]\nid = "h"\nsummary = "s"\nevents = ["Stop"]\nscript = "h.py"\n'
            '[[hook.fixture]]\nexpect = "deny"\n',
            "a fixture needs a prompt or a tool",
        ),
        ('[[widget]]\nid = "w"\n', "widget: is not a table"),
        (
            '[[skill]]\nid = "a"\ndir = "a"\n[[rule]]\nid = "a"\nsummary = "s"\nfile = "a.md"\n',
            "id a is used twice",
        ),
        ('[[skill]]\nid = "demo"\ndir = "a"\n', "needs the lw- prefix"),
    ],
)
def test_refuses_an_invalid_manifest_and_names_the_problem(
    tmp_path: Path, text: str, problem: str
) -> None:
    with pytest.raises(HarnessError, match="is invalid") as error:
        parse(text, tmp_path)

    assert problem in str(error.value)


def test_allows_a_skill_without_the_prefix_in_an_extra_manifest(tmp_path: Path) -> None:
    parse('[[skill]]\nid = "demo"\ndir = "a"\n', tmp_path, extra=True)


def test_refuses_a_named_file_that_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(HarnessError, match=r"hooks/guard\.py does not exist"):
        parse(HOOK, tmp_path, check_files=True)


def test_names_an_extra_manifest_by_folder_or_stem(tmp_path: Path) -> None:
    assert extra_name(tmp_path / "modules" / "testing" / "harness.toml") == "testing"
    assert extra_name(tmp_path / "harness" / "local.toml") == "local"


def test_reads_a_manifest_file_with_its_folder_as_base(tmp_path: Path) -> None:
    (tmp_path / "audit").mkdir()
    file = tmp_path / "audit" / "harness.toml"
    file.write_text('[[rule]]\nid = "r"\nsummary = "s"\nfile = "r.md"\n')
    (tmp_path / "audit" / "r.md").write_text("# R\n")

    m = read_manifest(file, extra=True)

    assert (m.name, m.dir, m.extra) == ("audit", tmp_path / "audit", True)
