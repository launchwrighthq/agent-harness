from pathlib import Path

import pytest

from agent_harness import config, hookio
from agent_harness.env_guard import EnvScope, blank, check_line, reveal_reason

ENGINE = ["python3 .claude/agent-harness/bin/ah env", "ah env", "lw env"]


def public_keys() -> tuple[str, ...]:
    return tuple(config.Config(Path("."), {}, None).get("env.publicKeys"))


def env_name(path: str) -> bool:
    base = hookio.basename(path)
    return base.startswith(".env") and not base.endswith(".example")


def scope(root: Path) -> EnvScope:
    return EnvScope(
        root,
        root,
        env_name,
        held=lambda path: hookio.basename(path) == ".env.production",
        protected=frozenset({"staging"}),
        public=public_keys(),
    )


def decisions(command: str, root: Path) -> list[str]:
    return [answer.decision for answer in check_line(command, ENGINE, scope(root)).answers]


def scanned(command: str, root: Path) -> str:
    return blank(command, check_line(command, ENGINE, scope(root)).exempt)


@pytest.mark.parametrize(
    "command",
    [
        'grep -n "subcommand\\|ah env\\|spawn" $F/env.command.mts | head -60',
        'sed -n 1,40p $H/x.py; grep -n "ah env\\|reveal" $H/x.py | head -20',
        "git commit -q -F - <<'EOF'\nfeat(env): ah env status --matrix\n\nRun $X on *.py.\nEOF\n"
        "git log --oneline -1",
        "python3 - <<'EOF'\nprint('ah env get A .env.local --reveal')\nEOF\ngrep -n x y.py | head",
        'echo "ah env get A .env.local --reveal"; echo rc=$?',
        "cat > msg.txt <<'EOF'\n- callers run `ah env` (x.mts goes)\nEOF",
    ],
)
def test_finds_no_engine_run_in_quoted_text_or_in_a_data_heredoc(
    command: str, tmp_path: Path
) -> None:
    check = check_line(command, ENGINE, scope(tmp_path))

    assert (check.answers, check.exempt) == ([], [])


@pytest.mark.parametrize(
    "command",
    [
        "AH=ah; $AH env status",
        "$(echo ah) env get A .env.local",
        "bash <<'EOF'\nah env get A .env.local --reveal\nEOF",
        'echo "$(ah env get A .env.local --reveal)"',
        "cat <<EOF\n$(ah env get A .env.local --reveal)\nEOF",
        "timeout 5 ah env get A .env.local --reveal",
        "uv run ah env get A .env.local --reveal",
        "bash -c 'ah env status'",
        "ah env get A .env.local --reveal 'unclosed",
        "./bin/ah env get A .env.local --reveal",
    ],
)
def test_asks_about_an_engine_run_that_it_cannot_clear(command: str, tmp_path: Path) -> None:
    assert decisions(command, tmp_path) == ["ask"]


@pytest.mark.parametrize(
    "command",
    [
        'ah env copy A .env.local "$SCRATCH/x.env"',
        "ah env keys .env.loc*",
        "for f in a b; do ah env keys $f; done",
        'ah env status; echo "exit=$?"',
        'ah env get A "$SCRATCH/x.env" 2>&1 | tail -3',
        'cd /srv/app && ah env --root /srv/app status; echo "exit=$?"',
        "ah env status --reveal",
    ],
)
def test_lets_a_quiet_step_pass_whatever_its_later_arguments_expand_to(
    command: str, tmp_path: Path
) -> None:
    assert decisions(command, tmp_path) == []


@pytest.mark.parametrize(
    "command",
    [
        "ah env $SUB .env.local",
        "ah env --root $R status",
        "ah env get A $F",
        'ah env get A "$F"',
        "ah env get A .env.local $'--rev\\x65al'",
        "ah env get A x{,} --reveal",
    ],
)
def test_asks_when_an_expansion_can_change_what_the_step_prints(
    command: str, tmp_path: Path
) -> None:
    assert decisions(command, tmp_path) == ["ask"]


@pytest.mark.parametrize(
    "command",
    [
        "cd /srv/app/tree; timeout 300 npx lw env check 2>&1 | tail -10; echo exit=$?",
        "timeout -k 5 300 ah env keys .env.local",
        "nice -n 5 nohup ah env status",
        "env -u HOME LANG=C ah env diff .env.local .env.ci",
        "stdbuf -o L npx -y lw env layout",
        "timeout 60 ah env get S3_BUCKET .env.production --reveal",
    ],
)
def test_judges_the_engine_step_inside_a_plain_wrapper(command: str, tmp_path: Path) -> None:
    assert decisions(command, tmp_path) == []


@pytest.mark.parametrize(
    "command",
    [
        "timeout 300 ah env get DATABASE_URL .env.local --reveal",
        "env -S 'ah env get A .env.local --reveal'",
        "timeout $T ah env status",
        "xargs ah env get A --reveal",
        "npx -c 'ah env get A .env.local --reveal'",
    ],
)
def test_asks_about_a_wrapped_engine_step_that_it_cannot_clear(
    command: str, tmp_path: Path
) -> None:
    assert decisions(command, tmp_path) == ["ask"]


def test_skips_only_the_words_of_a_quiet_engine_step_in_the_name_scan(tmp_path: Path) -> None:
    engine = "ah env keys .env.production"
    command = f"cd /srv/app && {engine} 2>&1 | head; cat .env.local"

    assert scanned(command, tmp_path) == (
        f"cd /srv/app && {' ' * len(engine)} 2>&1 | head; cat .env.local"
    )


@pytest.mark.parametrize(
    "command",
    [
        "ah env get A $F .env.production",
        "ah env --root /srv/app keys .env.production",
        "ah env --master .env.production status",
        "./bin/ah env get A .env.production",
        "ah env .env.production",
        "npm run env get A .env.production",
    ],
)
def test_keeps_the_name_scan_on_a_step_it_cannot_vouch_for(command: str, tmp_path: Path) -> None:
    assert check_line(command, ENGINE, scope(tmp_path)).exempt == []


@pytest.mark.parametrize(
    "command",
    [
        "ah env get S3_BUCKET .env.production --reveal",
        "ah env get STORAGE_DRIVER .env.production --reveal",
        "ah env get S3_ENDPOINT_URL .env.production --reveal",
        "lw env --root apps get CACHE_REGION config/app.env --reveal",
        "cd /srv/app && ah env get NODE_ENV .env.production --reveal",
    ],
)
def test_reveals_a_public_key_of_any_file_without_an_ask(command: str, tmp_path: Path) -> None:
    check = check_line(command, ENGINE, scope(tmp_path))

    assert check.answers == []
    assert ".env.production" not in blank(command, check.exempt)


def test_asks_to_reveal_a_key_that_is_not_public_and_names_it(tmp_path: Path) -> None:
    commands = [
        "ah env get DATABASE_URL .env.production --reveal",
        "ah env get S3_SECRET_ACCESS_KEY .env.production --reveal",
        "ah env get S3_REGION .env.production --reveal=1",
    ]

    got = [check_line(c, ENGINE, scope(tmp_path)).answers for c in commands]

    assert [[(a.decision, a.reason) for a in answers] for answers in got] == [
        [("ask", reveal_reason("DATABASE_URL"))],
        [("ask", reveal_reason("S3_SECRET_ACCESS_KEY"))],
        [("ask", reveal_reason("S3_REGION"))],
    ]


@pytest.mark.parametrize(
    "command",
    [
        "ah env run .env.local -- node -e 'console.log(new URL(process.env.DATABASE_URL).host)'",
        "ah env run .env.ci -- .venv/bin/python /tmp/x.py",
        "ah env run .env.local-extra --require K -- node /tmp/x.mjs",
        "ah env run .env.ci -- sh -c 'echo $X'",
        "ah env run .env.ci -- printenv",
    ],
)
def test_lets_a_run_of_any_program_pass_on_a_plain_project_file(
    command: str, tmp_path: Path
) -> None:
    assert decisions(command, tmp_path) == []


def test_skips_the_run_options_and_scans_the_command_that_it_starts(tmp_path: Path) -> None:
    head = "ah env run .env.ci --require K --"

    assert scanned(f"{head} cat .env.production", tmp_path) == (
        f"{' ' * len(head)} cat .env.production"
    )


def test_asks_before_a_run_on_a_held_file_or_a_protected_environment(tmp_path: Path) -> None:
    commands = ["ah env run .env.production -- pnpm test", "ah env run .env.staging -- pnpm"]

    got = [check_line(c, ENGINE, scope(tmp_path)).answers for c in commands]

    assert [[a.decision for a in answers] for answers in got] == [["ask"], ["ask"]]
    assert ".env.production" in got[0][0].reason
    assert ".env.staging" in got[1][0].reason


@pytest.mark.parametrize(
    "command",
    [
        "ah env run ../other/.env.ci -- pnpm test",
        "cd /srv/other && ah env run .env.ci -- pnpm test",
        "ah env --root /srv/other run .env.ci -- pnpm test",
    ],
)
def test_refuses_a_run_on_a_file_outside_the_project(command: str, tmp_path: Path) -> None:
    assert decisions(command, tmp_path) == ["deny"]


@pytest.mark.parametrize(
    "command",
    [
        'cd "$D" && ah env run .env.ci -- pnpm test',
        "ah env run $F -- pnpm test",
        "echo $(ah env run .env.ci -- pnpm test)",
    ],
)
def test_asks_before_a_run_whose_folder_or_file_it_cannot_see(command: str, tmp_path: Path) -> None:
    assert decisions(command, tmp_path) == ["ask"]


def test_exempts_a_global_option_only_when_it_can_check_the_value(tmp_path: Path) -> None:
    commands = [
        f"ah env --root {tmp_path}/apps get API_KEY .env.production",
        "ah env --root apps --master a/.env.example --declarations e.toml keys .env.production",
        "ah env --root get API_KEY .env.production",
        "ah env --file x get API_KEY .env.production",
        "ah env --master .env.local status .env.production",
        "ah env --master 'a$b' keys .env.production",
    ]

    got = [".env.production" in scanned(c, tmp_path) for c in commands]

    assert got == [False, False, True, True, True, True]


def test_answers_nothing_for_a_line_that_runs_no_engine_and_does_not_parse(
    tmp_path: Path,
) -> None:
    assert check_line("echo 'unclosed", ENGINE, scope(tmp_path)).answers == []


def test_says_that_a_hidden_run_prints_a_key_it_cannot_name(tmp_path: Path) -> None:
    (answer,) = check_line("AH=ah; $AH env status", ENGINE, scope(tmp_path)).answers

    assert answer.reason == reveal_reason("a key")
    assert answer.reason.startswith("This command prints the value of a key into the agent")
