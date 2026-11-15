from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.env import run, stores
from agent_harness.env.args import UsageError
from agent_harness.env.context import Context

TOKEN = "token-value-romeo-61"
DECLARED = (
    "[keys.APP_TOKEN]\nrequired = true\n"
    '[keys.APP_PORT]\nrequired = true\nenvironments = ["production"]\n'
    "[keys.APP_NAME]\n"
)


class Spawn:
    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv: list[str], environ: dict[str, str]) -> int:
        self.calls.append((argv, environ))
        return self.code


@pytest.fixture
def ctx(tmp_path: Path) -> Context:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env.ci").write_text(f"APP_TOKEN={TOKEN}\nAPP_NAME=from-file\n")
    (tmp_path / "env.toml").write_text(DECLARED, encoding="utf-8")
    settings = {"master": ".env.example", "declarations": ["env.toml"]}
    out: list[str] = []
    return Context(tmp_path, settings, out.append, out.append, lambda a, s: stores.Done(0, "", ""))


def test_runs_the_command_with_the_file_keys_and_an_exported_value_wins(ctx: Context) -> None:
    spawn = Spawn(code=7)

    code = run.cmd_run(
        ctx, [".env.ci", "--", "pnpm", "test"], spawn, {"PATH": "/bin", "APP_NAME": "exported"}
    )

    assert code == 7
    assert spawn.calls == [
        (["pnpm", "test"], {"APP_TOKEN": TOKEN, "APP_NAME": "exported", "PATH": "/bin"})
    ]


def test_fails_by_name_on_a_missing_required_key_and_runs_nothing(ctx: Context) -> None:
    spawn = Spawn()
    (ctx.root / ".env.production").write_text("APP_NAME=x\nAPP_TOKEN=\n", encoding="utf-8")

    with pytest.raises(HarnessError) as caught:
        run.cmd_run(ctx, [".env.production", "--require", "EXTRA", "--", "deploy"], spawn, {})

    assert str(caught.value) == (
        "required keys are missing or empty: APP_TOKEN, APP_PORT, EXTRA. Set them in "
        ".env.production, or export them. Nothing was run."
    )
    assert spawn.calls == []


def test_refuses_a_file_outside_the_repository(ctx: Context, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.env"
    outside.write_text("A=1\n", encoding="utf-8")
    spawn = Spawn()

    with pytest.raises(HarnessError) as caught:
        run.cmd_run(ctx, [str(outside), "--", "pnpm"], spawn, {})

    assert str(caught.value) == (
        f"{outside} is outside the repository. ah env run reads only an env file of the repository."
    )
    assert spawn.calls == []


def test_needs_a_command_after_the_separator(ctx: Context) -> None:
    with pytest.raises(UsageError) as caught:
        run.cmd_run(ctx, [".env.ci", "pnpm"], Spawn(), {})

    assert str(caught.value) == "expected <file> -- <command> [args]"


def test_names_a_command_that_is_not_found(ctx: Context) -> None:
    def missing(argv: list[str], environ: dict[str, str]) -> int:
        raise FileNotFoundError(argv[0])

    with pytest.raises(HarnessError) as caught:
        run.cmd_run(ctx, [".env.ci", "--", "no-such-tool"], missing, {})

    assert str(caught.value) == "cannot run no-such-tool: the command is not found"
