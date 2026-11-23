from pathlib import Path

import pytest

from agent_harness.env import command, envfile

MASTER = "# Generated top block.\n\n# App\nAPP_TOKEN=\nAPP_PORT=\n\n# Mail\nMAIL_KEY=\n"
TOKEN = "token-value-lima-58"
MAIL = "mail-value-oscar-17"
LIVE = f"# App\nAPP_TOKEN={TOKEN}\n\n# Mail\nMAIL_KEY={MAIL}\n"
TWO_STORES = """
[[env.stores]]
name = "backend"
kind = "file"
path = "vault/backend/.env.{env}"

[[env.stores]]
name = "fleet"
kind = "file"
path = "vault/fleet/.env.{env}"
"""


class Run:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.out: list[str] = []
        self.err: list[str] = []

    def __call__(self, *argv: str) -> int:
        self.out, self.err = [], []
        return command.main(
            ["--root", str(self.root), *argv], out=self.out.append, err=self.err.append
        )

    def text(self) -> str:
        return "\n".join(self.out + self.err)


def make_repo(tmp_path: Path, config: str) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".agents").mkdir()
    (root / ".agents" / "harness.toml").write_text(config, encoding="utf-8")
    (root / ".env.example").write_text(MASTER, encoding="utf-8")
    (root / ".env.production").write_text(LIVE, encoding="utf-8")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path, TWO_STORES)


def test_pushes_to_the_named_store_and_writes_its_own_marker(repo: Path) -> None:
    run = Run(repo)

    assert run("push", "production", "--store", "fleet", "--yes") == 0

    assert (repo / "vault" / "fleet" / ".env.production").is_file()
    assert not (repo / "vault" / "backend").exists()
    marker = repo / ".env.production.pushed-fleet"
    assert marker.read_text(encoding="utf-8") == envfile.content_digest(LIVE) + "\n"
    assert TOKEN not in run.text()


def test_asks_for_a_store_name_when_several_are_configured(repo: Path) -> None:
    run = Run(repo)

    assert run("push", "production") == 2

    assert run.err[0] == (
        "ah env: several secret stores are configured: pass --store NAME, one of backend, fleet"
    )


def test_names_the_known_stores_for_an_unknown_store_name(repo: Path) -> None:
    run = Run(repo)

    assert run("diff", "production", "--store", "vault") == 1

    assert run.err == ["ah env: no secret store is named vault. Stores: backend, fleet"]


def test_pulls_and_compares_with_the_named_store(repo: Path) -> None:
    run = Run(repo)
    run("push", "production", "--store", "backend", "--yes")
    (repo / ".env.production").unlink()

    pulled = run("pull", "production", "--store", "backend")
    same = run("diff", "production", "--store", "backend")

    assert (pulled, same) == (0, 0)
    assert envfile.read(repo / ".env.production").values() == {"APP_TOKEN": TOKEN, "MAIL_KEY": MAIL}


def test_keeps_the_one_store_form_with_its_marker_name(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, '[env.store]\nkind = "file"\npath = "vault/.env.{env}"\n')
    run = Run(repo)

    assert run("push", "production", "--yes") == 0

    assert (repo / ".env.production.pushed-store").is_file()


def push(repo: Path, *argv: str, answer: str | None = None) -> tuple[int, list[str], list[str]]:
    """A push with a terminal that answers `answer`, or with no terminal for None."""
    out: list[str] = []
    questions: list[str] = []

    def ask(question: str) -> str:
        questions.append(question)
        return str(answer)

    code = command.main(
        ["--root", str(repo), "push", "production", *argv],
        out=out.append,
        err=out.append,
        ask=None if answer is None else ask,
    )
    return code, out, questions


def test_refuses_a_push_without_a_terminal_unless_it_has_yes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, '[env.store]\nkind = "file"\npath = "vault/.env.{env}"\n')

    refused = push(repo)
    sent = push(repo, "--yes")

    assert refused == (
        1,
        [
            "ah env: a push asks before it writes, and standard input is no terminal. Run "
            "ah env push production --yes to push without the question."
        ],
        [],
    )
    assert sent[0] == 0
    assert (repo / "vault" / ".env.production").is_file()


def test_asks_on_a_terminal_and_sends_only_on_yes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, '[env.store]\nkind = "file"\npath = "vault/.env.{env}"\n')

    declined = push(repo, answer="n")
    stored_after_no = (repo / "vault" / ".env.production").exists()
    accepted = push(repo, answer="yes")

    assert declined == (
        1,
        ["would send APP_TOKEN", "would send MAIL_KEY", "push cancelled; nothing sent"],
        ["push 2 keys of .env.production to the file store for production? [y/N] "],
    )
    assert stored_after_no is False
    assert accepted[0] == 0
    assert (repo / "vault" / ".env.production").is_file()


def test_needs_yes_and_confirm_for_a_protected_environment(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        '[env]\nprotected = ["production"]\n\n[[env.stores]]\nname = "fleet"\nkind = "file"\n'
        'path = "vault/.env.{env}"\n',
    )

    attempts = [(), ("--yes",), ("--yes", "--confirm", "staging")]
    results = [push(repo, "--store", "fleet", *argv, answer="y")[0:2] for argv in attempts]
    sent = push(repo, "--store", "fleet", "--yes", "--confirm", "production")

    refusal = (
        "ah env: production is a protected environment. Run ah env push production --store "
        "fleet --yes --confirm production to push to it."
    )
    assert results == [(1, [refusal])] * 3
    assert sent[0] == 0


def test_refuses_to_create_the_github_environment_of_a_protected_environment(
    tmp_path: Path,
) -> None:
    repo = make_repo(
        tmp_path,
        '[env]\nprotected = ["production"]\n\n'
        '[env.store]\nkind = "github"\nenvironment = "{env}"\n',
    )

    code, out, _ = push(repo, "--yes", "--confirm", "production", "--create-environment")

    assert (code, out) == (
        1,
        [
            "ah env: --create-environment: production is a protected environment. A person "
            "creates it in GitHub (Settings > Environments) with its protection rules."
        ],
    )


def test_refuses_create_environment_for_a_store_that_is_not_github(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, '[env.store]\nkind = "file"\npath = "vault/.env.{env}"\n')

    code, out, _ = push(repo, "--yes", "--create-environment")

    assert (code, out[0]) == (1, "ah env: --create-environment applies to the github store only")


ONE_STORE = '[env.store]\nkind = "file"\npath = "vault/.env.{env}"\n'
DECLARED = (
    '[keys.APP_TOKEN]\nblock = "App"\nsecret = true\napps = ["api"]\n'
    '[keys.APP_PORT]\nblock = "App"\nsecret = false\napps = ["api"]\n'
    '[keys.MAIL_KEY]\nblock = "Mail"\nsecret = true\napps = ["mailer"]\n'
)


def declared_repo(tmp_path: Path, live: str) -> Path:
    repo = make_repo(tmp_path, ONE_STORE)
    (repo / "env.toml").write_text(DECLARED, encoding="utf-8")
    (repo / ".env.production").write_text(live, encoding="utf-8")
    return repo


def stored(repo: Path) -> dict[str, str]:
    return envfile.read(repo / "vault" / ".env.production").values()


def declared_run(repo: Path, *argv: str) -> tuple[int, list[str]]:
    out: list[str] = []
    code = command.main(
        ["--root", str(repo), "--declarations", "env.toml", *argv],
        out=out.append,
        err=out.append,
        ask=None,
    )
    return code, out


def test_refuses_a_key_that_the_master_does_not_name(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, ONE_STORE)
    (repo / ".env.production").write_text(LIVE + "\n# Other\nSTRAY_KEY=stray-v1\n")

    code, out = push(repo, "--yes")[0:2]

    assert (code, out) == (
        1,
        [
            "ah env: .env.production holds keys that its master does not name: STRAY_KEY. Add "
            "them to the master, or remove them from the file. Nothing was sent."
        ],
    )
    assert not (repo / "vault").exists()


@pytest.mark.parametrize("value", ["", "ChangeMe", "CHANGEME", "xxx"])
def test_refuses_a_declared_secret_that_is_empty_or_a_placeholder(
    tmp_path: Path, value: str
) -> None:
    repo = declared_repo(tmp_path, f"# App\nAPP_TOKEN={value}\n\n# Mail\nMAIL_KEY={MAIL}\n")

    code, out = declared_run(repo, "push", "production", "--yes")

    assert (code, out) == (
        1,
        [
            "ah env: .env.production holds declared secrets that are empty or a placeholder: "
            "APP_TOKEN. Set a real value first. Nothing was sent."
        ],
    )


def test_reads_the_placeholders_from_the_config(tmp_path: Path) -> None:
    repo = declared_repo(tmp_path, f"# App\nAPP_TOKEN=TBD\n\n# Mail\nMAIL_KEY={MAIL}\n")
    config = repo / ".agents" / "harness.toml"
    config.write_text('[env]\nplaceholders = ["tbd"]\n\n' + ONE_STORE, encoding="utf-8")

    code, out = declared_run(repo, "push", "production", "--yes")

    assert (code, out) == (
        1,
        [
            "ah env: .env.production holds declared secrets that are empty or a placeholder: "
            "APP_TOKEN. Set a real value first. Nothing was sent."
        ],
    )


def test_sends_only_the_secret_keys_unless_all_is_given(tmp_path: Path) -> None:
    live = f"# App\nAPP_TOKEN={TOKEN}\nAPP_PORT=8080\n\n# Mail\nMAIL_KEY={MAIL}\n"
    repo = declared_repo(tmp_path, live)

    secrets = declared_run(repo, "push", "production", "--yes")
    secret_store = stored(repo)
    every = declared_run(repo, "push", "production", "--yes", "--all")

    assert secrets[0] == 0
    assert secret_store == {"APP_TOKEN": TOKEN, "MAIL_KEY": MAIL}
    assert every[0] == 0
    assert stored(repo) == {"APP_TOKEN": TOKEN, "MAIL_KEY": MAIL, "APP_PORT": "8080"}


def test_limits_push_and_pull_to_the_keys_of_some_apps(tmp_path: Path) -> None:
    repo = declared_repo(tmp_path, LIVE)
    (repo / "vault").mkdir()
    (repo / "vault" / ".env.production").write_text("MAIL_KEY=mail-store-v2\n", encoding="utf-8")

    pushed = declared_run(repo, "push", "production", "--yes", "--apps", "api")
    after_push = stored(repo)
    (repo / "vault" / ".env.production").write_text(
        "APP_TOKEN=token-store-v3\nMAIL_KEY=mail-store-v4\n", encoding="utf-8"
    )
    pulled = declared_run(repo, "pull", "production", "--apps", "api")

    assert pushed == (
        0,
        [
            "sent APP_TOKEN",
            "pushed .env.production to the file store for production: 1 keys",
            "wrote .env.production.pushed-store",
        ],
    )
    assert after_push == {"MAIL_KEY": "mail-store-v2", "APP_TOKEN": TOKEN}
    assert pulled[0] == 0
    assert envfile.read(repo / ".env.production").values() == {
        "APP_TOKEN": "token-store-v3",
        "MAIL_KEY": MAIL,
    }
