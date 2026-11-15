import json
from pathlib import Path

import pytest

from agent_harness import config, hookio

ENGINE = ["python3 .claude/agent-harness/bin/ah env", "ah env", "lw env"]


def installed_hook(root: Path) -> Path:
    return root / ".claude" / "agent-harness" / "hooks" / "x.py"


def test_finds_the_project_three_levels_above_installed_hooks(tmp_path: Path) -> None:
    assert hookio.project_root(installed_hook(tmp_path)) == tmp_path


def test_reads_the_harness_section_of_the_hook_project(tmp_path: Path) -> None:
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "harness.toml").write_text('[harness]\nenvCommand = "lw env"\n')

    cfg = hookio.hook_config("test-hook", installed_hook(tmp_path))

    assert hookio.harness_value(cfg, "envCommand") == "lw env"
    assert hookio.env_commands(cfg)[-1] == "lw env"


def test_names_the_launcher_of_the_project_by_its_absolute_path(tmp_path: Path) -> None:
    cfg = hookio.hook_config("test-hook", installed_hook(tmp_path))

    commands = hookio.env_commands(cfg)

    assert f"python3 {tmp_path.resolve()}/.claude/agent-harness/bin/ah env" in commands


def test_falls_back_to_the_defaults_when_the_config_is_not_toml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "harness.toml").write_text("[harness\n")

    cfg = hookio.hook_config("test-hook", installed_hook(tmp_path))

    expected = config.SECTION_DEFAULTS["harness"]["guardedFiles"]
    assert hookio.harness_value(cfg, "guardedFiles") == expected
    assert capsys.readouterr().err.startswith("test-hook: cannot read .agents/harness.toml (")


@pytest.mark.parametrize(
    ("pattern", "name", "matches"),
    [
        ("*.secret", "prod.secret", True),
        ("*.secret", "dir/prod.secret", False),
        (".env.production", ".ENV.Production", True),
        (".env.production", ".env-production", False),
        ("key?.pem", "key1.pem", True),
        ("key?.pem", "key12.pem", False),
    ],
)
def test_matches_a_whole_base_name_with_a_file_pattern(
    pattern: str, name: str, matches: bool
) -> None:
    assert (hookio.name_pattern(pattern).fullmatch(name) is not None) is matches


def test_keeps_strings_and_tables_with_a_pattern_and_reports_the_rest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    entries = ["*.secret", {"pattern": "*.vault", "hint": "Ask."}, 3, {"hint": "no pattern"}]

    got = hookio.patterns(entries, "test-hook")

    assert got == [{"pattern": "*.secret"}, {"pattern": "*.vault", "hint": "Ask."}]
    assert capsys.readouterr().err.count("is no file pattern") == 2


@pytest.mark.parametrize(
    "command",
    [
        "ah env get API_KEY .env.production",
        "  lw env set API_KEY .env.production --stdin  ",
        "python3 .claude/agent-harness/bin/ah env get API_KEY .env.production --reveal",
        "ah env set API_KEY .env.production --value 'a;b|c$d`e<f>g'",
        "ah env set API_KEY .env.production --value 'two\nlines'",
    ],
)
def test_exempts_a_lone_run_of_the_env_engine(command: str) -> None:
    assert hookio.lone_env_command(command, ENGINE) is True


@pytest.mark.parametrize(
    "command",
    [
        "ah env get API_KEY .env.production; cat x",
        "ah env get API_KEY .env.production\ncat x",
        "ah env get API_KEY .env.production | head",
        "ah env get $KEY .env.production",
        'ah env get API_KEY ".env.production"',
        "ah env get API_KEY .env\\.production",
        "ah env get API_KEY 'unclosed",
        "ah env .env.production",
        "ah env",
        "ah environment get API_KEY .env.production",
        "cd /srv && ah env get API_KEY .env.production",
        "npm run env get API_KEY .env.production",
    ],
)
def test_voids_the_exemption_for_anything_but_a_lone_engine_run(command: str) -> None:
    assert hookio.lone_env_command(command, ENGINE) is False


def test_reads_the_subcommand_after_the_global_options_it_can_check(tmp_path: Path) -> None:
    scope = hookio.EnvScope(tmp_path, tmp_path, lambda path: path.endswith(".secret"))
    commands = [
        f"ah env --root {tmp_path}/apps get API_KEY .env.production",
        "ah env --root apps --master a/.env.example --declarations e.toml --declarations f.toml k",
        "ah env --root get API_KEY .env.production",
        "ah env --root apps",
        "ah env --file x get API_KEY .env.production",
        "ah env --root /srv/app get API_KEY .env.production",
        "ah env --master app.secret keys x",
        "ah env --master 'a$b' keys x",
    ]

    got = [hookio.env_arguments(c, ENGINE, scope) for c in commands]

    assert got == [["get", "API_KEY", ".env.production"], ["k"], *[None] * 6]


def test_gives_the_command_that_a_lone_run_subcommand_starts() -> None:
    commands = [
        "ah env run .env.ci -- pnpm test --filter 'a b'",
        "ah env run .env.ci --require A -- cat .env.production",
        "ah env run .env.ci pnpm test",
        "ah env get A .env.ci -- x",
        "ah env run .env.ci -- pnpm test; cat x",
    ]

    got = [hookio.env_run_command(c, ENGINE) for c in commands]

    assert got == ["pnpm test --filter 'a b'", "cat .env.production", None, None, None]


def test_lets_a_run_pass_only_for_an_allowed_plain_command_on_a_plain_project_file(
    tmp_path: Path,
) -> None:
    scope = hookio.EnvScope(
        tmp_path,
        tmp_path,
        lambda path: False,
        held=lambda path: path.endswith(".vault"),
        run_commands=frozenset({"pnpm", "python3"}),
        protected=frozenset({"production"}),
    )
    commands = {
        "ah env run .env.ci -- pnpm test --filter api": None,
        "ah env run .env.ci -- python3 -c 'import os'": ("ask", ".env.ci"),
        "ah env run .env.ci -- pnpm --eval x": ("ask", ".env.ci"),
        "ah env run .env.ci -- pnpm '*.ts'": ("ask", ".env.ci"),
        "ah env run .env.ci -- bin/pnpm test": ("ask", ".env.ci"),
        "ah env run .env.ci -- yarn test": ("ask", ".env.ci"),
        "ah env run .env.production -- pnpm test": ("ask", ".env.production"),
        "ah env run app.vault -- pnpm test": ("ask", "app.vault"),
        f"ah env run {tmp_path.parent}/.env.ci -- pnpm test": (
            "deny",
            f"{tmp_path.parent}/.env.ci",
        ),
        "ah env get A .env.ci": None,
    }

    got = {c: hookio.env_run_verdict(c, ENGINE, scope) for c in commands}

    assert got == commands


def test_voids_the_exemption_for_a_global_option_without_a_scope_to_check_it() -> None:
    assert hookio.env_arguments("ah env --root . keys x", ENGINE) is None


@pytest.mark.parametrize(
    "command",
    [
        "ah env keys .env.prod*",
        "ah env keys .env.productio?",
        "ah env keys .env.productio[n]",
        "ah env keys .env.production{,}",
        "ah env keys ~/.env.production",
        "ah env keys .env.production #x",
        "ah env keys .env.production!",
    ],
)
def test_voids_the_exemption_for_an_unquoted_expansion_character(command: str) -> None:
    assert hookio.lone_env_command(command, ENGINE) is False


def test_names_the_key_of_a_lone_reveal_run() -> None:
    commands = [
        "ah env get API_KEY .env.production --reveal",
        "lw env get --reveal 'DB_URL' .env.local",
        "python3 .claude/agent-harness/bin/ah env get API_KEY '.env.local' --reveal",
        "ah env get API_KEY .env.production --reveal=1",
        "ah env get API_KEY .env.production",
    ]

    got = [hookio.env_reveal(c, ENGINE) for c in commands]

    assert got == ["API_KEY", "DB_URL", "API_KEY", "API_KEY", None]


@pytest.mark.parametrize(
    "command",
    [
        "ah env --reveal",
        "ah env get API_KEY .env.production --reveal; cat x",
        'ah env get API_KEY .env.production "--reveal"',
        "ah env get $KEY .env.production",
        "ah env get API_KEY .env.production --rev\\eal",
        "ah env get API_KEY .env.production $'--rev\\x65al'",
        "ah env get API_KEY .env.production --rev*",
        "ah env get API_KEY .env.production --rev{e,}al",
        "x=--reveal; ah env get API_KEY .env.production $x",
        "a''h e''nv get API_KEY .env.production --reveal",
        "./bin/ah env get API_KEY .env.production --reveal",
    ],
)
def test_fails_closed_to_a_reveal_on_a_hidden_or_compound_one(command: str) -> None:
    assert hookio.env_reveal(command, ENGINE) == "a key"


@pytest.mark.parametrize(
    "command",
    [
        "grep -n reveal docs/env.md",
        "echo --reveal",
        "ah env keys .env.production | head",
    ],
)
def test_finds_no_reveal_without_a_reveal_word_or_without_the_engine(command: str) -> None:
    assert hookio.env_reveal(command, ENGINE) is None


@pytest.mark.parametrize(
    "command",
    [
        'ah env status; echo "exit=$?"',
        "python3 .claude/agent-harness/bin/ah env check && echo $?",
        "ah env --root apps/api keys .env.local; echo $?",
        'ah env layout && ah env diff .env.local .env.staging; echo "exit=$?"',
    ],
)
def test_asks_to_run_a_quiet_subcommand_alone_when_the_line_hides_a_word(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    hookio.ask_env(command, ENGINE, "a key")

    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "prints no value" in reason
    assert "does not run alone, so the guard cannot clear the whole line" in reason
    assert "prints the value" not in reason


@pytest.mark.parametrize(
    "command",
    [
        'ah env get API_KEY .env.local; echo "exit=$?"',
        "ah env status --reveal",
        'ah env status; ah env run .env.local -- pnpm test; echo "$?"',
        "ah env $SUB; echo $?",
    ],
)
def test_asks_about_a_reveal_when_a_subcommand_can_print_a_value(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    hookio.ask_env(command, ENGINE, "a key")

    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason.startswith("This command prints the value of a key into the agent context.")


def test_prints_a_compact_ask_answer(capsys: pytest.CaptureFixture[str]) -> None:
    hookio.ask("confirm")

    assert json.loads(capsys.readouterr().out) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "confirm",
        }
    }


def test_prints_a_compact_deny_answer(capsys: pytest.CaptureFixture[str]) -> None:
    hookio.deny("refusé")

    out = capsys.readouterr().out
    assert json.loads(out) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "refusé",
        }
    }
    assert ", " not in out


def test_takes_the_last_segment_as_node_basename_does() -> None:
    assert [hookio.basename(p) for p in ["a/b/.env", "a/b/", ".env", "/"]] == [
        ".env",
        "b",
        ".env",
        "",
    ]
