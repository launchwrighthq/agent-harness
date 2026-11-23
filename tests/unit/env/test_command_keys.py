from pathlib import Path

import pytest

from agent_harness import HarnessError, config
from agent_harness.env import command, command_keys, stores

MASTER = "# Generated top block.\n\n# App\nAPP_URL=\n"
COMMAND = "node tools/print-env-keys.mjs"


class Schema:
    """A fake command: it gives one answer and records each call."""

    def __init__(self, stdout: str = "", code: int = 0, stderr: str = "") -> None:
        self.done = stores.Done(code, stdout, stderr)
        self.calls: list[tuple[str, Path, float]] = []

    def __call__(self, command: str, cwd: Path, seconds: float) -> stores.Done:
        self.calls.append((command, cwd, seconds))
        return self.done


def no_git(argv: list[str], stdin: str | None) -> stores.Done:
    return stores.Done(0, "", "")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".agents").mkdir()
    (root / ".agents" / "harness.toml").write_text(
        f'[env]\ndeclarationsCommand = "{COMMAND}"\n', encoding="utf-8"
    )
    (root / ".env.example").write_text(MASTER, encoding="utf-8")
    return root


def run(repo: Path, schema: Schema, *argv: str) -> tuple[int, list[str]]:
    out: list[str] = []
    code = command.main(
        ["--root", str(repo), *argv],
        out=out.append,
        err=out.append,
        runner=no_git,
        execute=schema,
    )
    return code, out


def test_reads_one_key_name_per_line_and_runs_the_command_in_the_root(tmp_path: Path) -> None:
    schema = Schema("QUEUE_URL\n\n  MAIL_KEY  \r\n_private\nQUEUE_URL\n")

    found = command_keys.names(COMMAND, tmp_path, schema)

    assert found == {"QUEUE_URL", "MAIL_KEY", "_private"}
    assert schema.calls == [(COMMAND, tmp_path, command_keys.TIMEOUT)]


def test_runs_no_command_for_the_default_setting(tmp_path: Path) -> None:
    schema = Schema("QUEUE_URL\n")

    assert config.get("env.declarationsCommand", tmp_path) == ""
    assert command_keys.names("", tmp_path, schema) == set()
    assert schema.calls == []


def test_refuses_an_exit_code_other_than_0_with_the_first_error_line(tmp_path: Path) -> None:
    schema = Schema("QUEUE_URL\n", code=3, stderr="schema module not found\nat line 2\n")

    with pytest.raises(HarnessError) as error:
        command_keys.names(COMMAND, tmp_path, schema)

    assert str(error.value) == (
        "env.declarationsCommand exited 3: schema module not found. It must print one key name "
        "per line and exit 0."
    )


def test_refuses_an_output_line_that_is_no_key_name_and_does_not_repeat_it(tmp_path: Path) -> None:
    schema = Schema("QUEUE_URL\nMAIL_KEY=mail-value-kilo-41\n")

    with pytest.raises(HarnessError) as error:
        command_keys.names(COMMAND, tmp_path, schema)

    assert str(error.value) == (
        "env.declarationsCommand: line 2 of its output is not an env key name. It must print "
        "one key name per line and nothing else."
    )


def test_refuses_a_setting_that_is_not_a_string(tmp_path: Path) -> None:
    with pytest.raises(HarnessError, match="must be a string that holds one shell command"):
        command_keys.names(["node", "keys.mjs"], tmp_path, Schema())


def test_passes_a_check_of_keys_that_only_the_command_declares(repo: Path) -> None:
    (repo / "app.py").write_text("import os\nos.getenv('QUEUE_URL')\n", encoding="utf-8")
    (repo / ".env.local").write_text("# App\nMAIL_KEY=mail-value-kilo-41\n", encoding="utf-8")
    schema = Schema("QUEUE_URL\nMAIL_KEY\n")

    assert run(repo, schema, "check") == (0, ["env check: no finding"])
    assert len(schema.calls) == 1


def test_reports_a_key_that_the_command_does_not_list(repo: Path) -> None:
    (repo / "app.py").write_text("import os\nos.getenv('QUEUE_URL')\n", encoding="utf-8")

    code, out = run(repo, Schema("MAIL_KEY\n"), "check")

    assert (code, out) == (
        1,
        [
            "app.py:2 QUEUE_URL: code reads it, and no master or declaration names it",
            "1 findings",
        ],
    )


def test_fails_a_check_with_the_reason_when_the_command_fails(repo: Path) -> None:
    code, out = run(repo, Schema(code=1, stderr="no schema\n"), "check")

    assert (code, out) == (
        1,
        [
            "ah env: env.declarationsCommand exited 1: no schema. It must print one key name "
            "per line and exit 0."
        ],
    )


def test_says_whether_the_command_lists_the_key_of_a_where_run(repo: Path) -> None:
    schema = Schema("QUEUE_URL\n")

    listed = run(repo, schema, "where", "QUEUE_URL")
    absent = run(repo, schema, "where", "APP_URL")

    assert listed == (
        0,
        [
            "QUEUE_URL",
            "  absent from .env.example",
            "  declared and read by env.declarationsCommand",
        ],
    )
    assert absent == (
        0,
        ["APP_URL", "  empty in .env.example", "  not listed by env.declarationsCommand"],
    )
