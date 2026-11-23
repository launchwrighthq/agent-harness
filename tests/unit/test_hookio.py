import json
from pathlib import Path

import pytest

from agent_harness import config, hookio


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


@pytest.mark.parametrize(
    ("path", "plain"),
    [
        (".env.local", True),
        ("apps/web/.env.local", True),
        (".env.shared.web", True),
        # The default `guardedFiles` and a `secretFiles` entry win over a plain entry.
        (".env.production", False),
        (".env.shared.keys", False),
        (".env.local.bak", False),
        (".env.shared.orig", False),
        (".local/secrets/.env.local", False),
        (".env", False),
        (".env.staging", False),
        # An entry opens an env file name only.
        ("server.pem", False),
    ],
)
def test_opens_only_a_plain_env_name_that_no_entry_closes(
    path: str, plain: bool, tmp_path: Path
) -> None:
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "harness.toml").write_text(
        "[harness]\n"
        'plainEnvFiles = [".env.local", ".env.production", ".env.shared.web",'
        ' ".env.shared.keys", "server.pem"]\n'
        'secretFiles = [".env.shared.keys"]\n'
    )
    cfg = hookio.hook_config("test-hook", installed_hook(tmp_path))

    assert hookio.plain_env_test(cfg, "test-hook")(path) is plain


@pytest.mark.parametrize(
    "path",
    [".env.dev", ".env.test", ".env.local~", ".env.local.swp", ".env.local.bak.1", ".ENV.LOCAL"],
)
def test_opens_no_name_that_differs_from_each_exact_plain_entry(path: str, tmp_path: Path) -> None:
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "harness.toml").write_text(
        '[harness]\nplainEnvFiles = [".env.local", ".env.d*", { pattern = ".env.test" }]\n'
    )
    cfg = hookio.hook_config("test-hook", installed_hook(tmp_path))
    test = hookio.plain_env_test(cfg, "test-hook")

    assert (test(".env.local"), test(path)) == (True, False)


def test_says_on_stderr_which_plain_entry_is_no_exact_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    names = hookio.plain_names([".env.local", ".env.d*", {"pattern": ".env.test"}], "test-hook")

    assert names == [".env.local"]
    assert capsys.readouterr().err.splitlines() == [
        "test-hook: the plainEnvFiles entry '.env.d*' is no exact file name; it opens nothing",
        "test-hook: the plainEnvFiles entry {'pattern': '.env.test'} is no exact file name;"
        " it opens nothing",
    ]


def test_opens_no_env_file_with_the_default_config(tmp_path: Path) -> None:
    cfg = hookio.hook_config("test-hook", installed_hook(tmp_path))

    test = hookio.plain_env_test(cfg, "test-hook")

    assert [test(p) for p in [".env.local", ".env", ".env.example"]] == [False, False, False]


@pytest.mark.parametrize(
    ("path", "backup"),
    [
        (".env.production.bak", True),
        ("apps/.env.BACKUP", True),
        (".local/secrets/api.json", True),
        (".env.local", False),
        ("notes.bak", False),
    ],
)
def test_reads_a_backup_name_and_the_secrets_folder_as_a_backup(path: str, backup: bool) -> None:
    assert hookio.is_backup(path) is backup
