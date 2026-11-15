from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.env import declarations

MODULE = """
[keys.DATABASE_URL]
type = "url"
secret = true
block = "Database"
apps = ["api", "worker"]
summary = "Postgres URL"

[keys.LOG_LEVEL]
type = "enum"
enum = ["debug", "info"]
secret = false
block = "Logging"
summary = "Lowest level"

[environments.dev]
LOG_LEVEL = "debug"
"""

PROJECT = """
[keys.DATABASE_URL]
apps = ["api"]
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_merges_files_and_a_later_file_wins_per_field(tmp_path: Path) -> None:
    loaded = declarations.load(
        [write(tmp_path, "module.toml", MODULE), write(tmp_path, "env.toml", PROJECT)]
    )

    url = loaded.find("DATABASE_URL")
    assert url is not None
    assert (url.apps, url.secret, url.block, url.type) == (["api"], True, "Database", "url")
    assert loaded.apps() == ["api"]
    assert [k.name for k in loaded.keys_of("worker")] == ["LOG_LEVEL"]


def test_names_the_file_key_and_field_of_a_problem(tmp_path: Path) -> None:
    bad = write(tmp_path, "env.toml", '[keys.TOKEN]\nsecret = "yes-secret-text"\n')

    with pytest.raises(HarnessError) as caught:
        declarations.load([bad])

    assert f"{bad}: key TOKEN: field 'secret' must be a boolean" in str(caught.value)
    assert "yes-secret-text" not in str(caught.value)


def test_keeps_the_decoder_text_out_of_a_toml_problem(tmp_path: Path) -> None:
    bad = write(tmp_path, "env.toml", "[keys.TOKEN\nvalue = 'secret-text'\n")

    with pytest.raises(HarnessError) as caught:
        declarations.load([bad])

    assert str(caught.value) == f"env declarations:\n{bad}: not valid TOML"


def test_checks_a_value_against_the_declared_type(tmp_path: Path) -> None:
    loaded = declarations.load([write(tmp_path, "env.toml", MODULE)])
    url, level = loaded.find("DATABASE_URL"), loaded.find("LOG_LEVEL")
    assert url is not None
    assert level is not None

    assert declarations.matches_type(url, "postgres://h/db")
    assert not declarations.matches_type(url, "not a url")
    assert declarations.matches_type(level, "info")
    assert not declarations.matches_type(level, "loud")
    assert declarations.matches_type(level, "")


FAMILIES = """
[keys.DATABASE_URL]
block = "Database"

[keys.DEFAULT_SITE]
block = "Keys"

[keys.LOG_LEVEL]
block = "Logging"

[families."API_KEY_<NAME>"]
secret = true
block = "Keys"
members = ["ALPHA"]

[families."LLM_<NAME>_API_KEY"]
secret = true
block = "LLM providers"
apps = ["api"]

[environments.dev]
API_KEY_BETA = ""
LLM_OPENAI_API_KEY = ""
"""


def test_lists_the_known_family_members_after_the_last_key_of_their_block(
    tmp_path: Path,
) -> None:
    loaded = declarations.load([write(tmp_path, "env.toml", FAMILIES)])

    assert [(k.name, k.family) for k in loaded.keys] == [
        ("DATABASE_URL", None),
        ("DEFAULT_SITE", None),
        ("API_KEY_ALPHA", "API_KEY_<NAME>"),
        ("API_KEY_BETA", "API_KEY_<NAME>"),
        ("LOG_LEVEL", None),
        ("LLM_OPENAI_API_KEY", "LLM_<NAME>_API_KEY"),
    ]


def test_declares_a_family_member_that_no_list_names(tmp_path: Path) -> None:
    loaded = declarations.load([write(tmp_path, "env.toml", FAMILIES)])

    member = loaded.find("API_KEY_GAMMA")

    assert member is not None
    assert (member.family, member.secret, member.block) == ("API_KEY_<NAME>", True, "Keys")
    assert loaded.find("API_KEY_") is None


def test_refuses_a_family_name_without_one_placeholder(tmp_path: Path) -> None:
    bad = write(tmp_path, "env.toml", '[families."API_KEY"]\nblock = "Keys"\n')

    with pytest.raises(HarnessError) as caught:
        declarations.load([bad])

    problem = f"{bad}: family name 'API_KEY' is not UPPER_SNAKE_CASE with one <PART>"
    assert problem in str(caught.value)


FIELDS = """
[keys.DATABASE_URL]
required = true
summary = "The Postgres URL"
environments = ["production", "staging"]

[keys.POOL]
default = "5"
"""


def test_reads_required_summary_default_and_environments(tmp_path: Path) -> None:
    loaded = declarations.load([write(tmp_path, "env.toml", FIELDS)])
    url, pool = loaded.find("DATABASE_URL"), loaded.find("POOL")
    assert url is not None
    assert pool is not None

    got = [(k.required, k.summary, k.default, k.environments) for k in (url, pool)]

    assert got == [
        (True, "The Postgres URL", "", ["production", "staging"]),
        (False, "", "5", None),
    ]
    assert (url.applies_to("staging"), url.applies_to("local"), pool.applies_to("local")) == (
        True,
        False,
        True,
    )


def test_names_each_problem_of_the_new_fields(tmp_path: Path) -> None:
    bad = write(
        tmp_path,
        "env.toml",
        '[keys.A]\nrequired = "yes"\nsummary = "two\\nlines"\ndefault = 5\nenvironments = "prod"\n',
    )

    with pytest.raises(HarnessError) as caught:
        declarations.load([bad])

    assert str(caught.value).splitlines()[1:] == [
        f"{bad}: key A: field 'required' must be a boolean",
        f"{bad}: key A: field 'summary' must be one line of text",
        f"{bad}: key A: field 'default' must be a one-line string",
        f"{bad}: key A: field 'environments' must be a list of environment names",
    ]
