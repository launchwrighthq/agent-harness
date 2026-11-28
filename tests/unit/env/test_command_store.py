import json
import os
from pathlib import Path
from typing import Any

import pytest

from agent_harness import HarnessError
from agent_harness.env import command, envfile, stores

MASTER = "# Generated top block.\n\n# App\nAPP_TOKEN=\nAPP_PORT=\n\n# Mail\nMAIL_KEY=\n"
TOKEN = "token-value-lima-58"
MAIL = "mail-value-oscar-17"
OLD = "token-store-old-77"
LIVE = f"# App\nAPP_TOKEN={TOKEN}\n\n# Mail\nMAIL_KEY={MAIL}\n"
DECLARED = (
    '[keys.APP_TOKEN]\nblock = "App"\nsecret = true\napps = ["api"]\n'
    '[keys.APP_PORT]\nblock = "App"\nsecret = false\napps = ["api"]\n'
    '[keys.MAIL_KEY]\nblock = "Mail"\nsecret = true\napps = ["mailer"]\n'
)
PUSH = 'push = ["env-store", "push", "{env}"]\n'
PULL = 'pull = ["env-store", "pull", "{env}"]\n'
BOTH = '[env.store]\nkind = "command"\n' + PUSH + PULL
PUSH_ONLY = '[env.store]\nkind = "command"\n' + PUSH
READS: dict[str, Any] = {"push": ["env-store", "push"], "pull": ["env-store", "pull", "{env}"]}
BAD_OUTPUT = "the pull command of the command store printed no JSON object of string values"


class Host:
    """A fake of the two commands. Records each call with the working folder at call time."""

    def __init__(self, pull: stores.Done | None = None, push: stores.Done | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.replies = {"pull": pull, "push": push}

    def __call__(self, argv: list[str], stdin: str | None) -> stores.Done:
        self.calls.append({"argv": argv, "stdin": json.loads(stdin or ""), "cwd": os.getcwd()})
        return self.replies[argv[1]] or stores.Done(0, "", "")

    def sent(self) -> list[dict[str, Any]]:
        return [call["stdin"] for call in self.calls if call["argv"][1] == "push"]


def holds(values: dict[str, str]) -> stores.Done:
    return stores.Done(0, json.dumps(values), "")


@pytest.fixture
def program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`env-store` on PATH. The fake runner answers for it, so it never starts."""
    folder = tmp_path / "bin"
    folder.mkdir()
    path = folder / "env-store"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))
    return path


def make_repo(tmp_path: Path, config: str) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".agents").mkdir()
    (root / ".agents" / "harness.toml").write_text(config, encoding="utf-8")
    (root / ".env.example").write_text(MASTER, encoding="utf-8")
    (root / ".env.production").write_text(LIVE, encoding="utf-8")
    (root / "env.toml").write_text(DECLARED, encoding="utf-8")
    return root


def ah_env(repo: Path, host: Host, *argv: str) -> tuple[int, list[str]]:
    out: list[str] = []
    code = command.main(
        ["--root", str(repo), *argv], out=out.append, err=out.append, runner=host, ask=None
    )
    return code, out


def open_store(settings: dict[str, Any], root: Path, host: Host | None = None) -> stores.Store:
    return stores.open_store({"kind": "command", **settings}, "production", root, host or Host())


def test_opens_the_command_kind_as_a_push_only_store_that_keeps_other_keys(
    program: Path, tmp_path: Path
) -> None:
    store = open_store({"push": ["env-store", "push"]}, tmp_path)

    assert (store.kind, store.can_pull, store.whole) == ("command", False, False)


def test_names_the_command_kind_for_an_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(HarnessError) as caught:
        stores.open_store({"kind": "vault"}, "production", tmp_path, Host())

    assert "unknown store kind vault. Known kinds:" in str(caught.value)
    assert " command, " in str(caught.value)


def test_starts_the_push_list_in_the_root_with_the_values_on_standard_input(
    program: Path, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    host = Host()
    store = open_store({"push": ["env-store", "push", "--to={env}", "{env}"]}, root, host)

    values = {"APP_TOKEN": TOKEN, "APP_PORT": "8080"}
    store.send(values, {"APP_TOKEN": OLD}, frozenset({"APP_PORT"}))

    (call,) = host.calls
    assert call["argv"] == [str(program), "push", "--to=production", "production"]
    assert Path(call["cwd"]).resolve() == root.resolve()
    assert call["stdin"] == {
        "version": 1,
        "env": "production",
        "values": {"APP_TOKEN": TOKEN, "APP_PORT": "8080"},
        "plain": ["APP_PORT"],
        "current": {"APP_TOKEN": OLD},
    }
    assert Path.cwd().resolve() != root.resolve()


def test_sorts_the_plain_keys_and_sends_null_for_no_current(program: Path, tmp_path: Path) -> None:
    host = Host()
    store = open_store({"push": ["env-store", "push"]}, tmp_path, host)

    store.send({"B_URL": "b", "A_URL": "a"}, None, frozenset({"B_URL", "A_URL"}))

    assert host.calls[0]["stdin"]["plain"] == ["A_URL", "B_URL"]
    assert host.calls[0]["stdin"]["current"] is None


def test_resolves_a_program_with_a_slash_against_the_root(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "env-store"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    host = Host()
    store = open_store({"push": ["scripts/env-store", "push"]}, tmp_path, host)

    store.send({"APP_TOKEN": TOKEN}, None)

    assert host.calls[0]["argv"] == [str(script), "push"]


def test_pushes_through_ah_env_with_no_value_in_an_argument_and_writes_the_marker(
    program: Path, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path, BOTH)
    host = Host(pull=stores.Done(3, "", ""))

    code, out = ah_env(repo, host, "push", "production", "--yes")

    assert code == 0
    assert [call["argv"][1:] for call in host.calls] == [
        ["pull", "production"],
        ["push", "production"],
    ]
    assert host.sent() == [
        {
            "version": 1,
            "env": "production",
            "values": {"APP_TOKEN": TOKEN, "MAIL_KEY": MAIL},
            "plain": [],
            "current": None,
        }
    ]
    marker = repo / ".env.production.pushed-store"
    assert marker.read_text(encoding="utf-8") == envfile.content_digest(LIVE) + "\n"
    assert TOKEN not in "\n".join(out)


def test_marks_the_declared_plain_keys_of_a_push(program: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, PUSH_ONLY)
    live = f"# App\nAPP_TOKEN={TOKEN}\nAPP_PORT=8080\n"
    (repo / ".env.production").write_text(live, encoding="utf-8")
    host = Host()

    code, _ = ah_env(
        repo, host, "--declarations", "env.toml", "push", "production", "--yes", "--all"
    )

    assert code == 0
    assert host.sent()[0]["plain"] == ["APP_PORT"]
    assert host.sent()[0]["values"] == {"APP_TOKEN": TOKEN, "APP_PORT": "8080"}


def test_reads_the_object_that_the_pull_list_prints(program: Path, tmp_path: Path) -> None:
    host = Host(pull=holds({"APP_TOKEN": TOKEN}))
    store = open_store(READS, tmp_path, host)

    found = store.fetch()

    assert found == {"APP_TOKEN": TOKEN}
    assert host.calls[0]["argv"] == [str(program), "pull", "production"]
    assert host.calls[0]["stdin"] == {"version": 1, "env": "production"}
    assert Path(host.calls[0]["cwd"]).resolve() == tmp_path.resolve()


def test_holds_nothing_when_the_pull_command_exits_3(program: Path, tmp_path: Path) -> None:
    host = Host(pull=stores.Done(3, "", "no such environment"))
    store = open_store(READS, tmp_path, host)

    assert store.fetch() is None


def test_fails_a_pull_on_any_other_exit_code(program: Path, tmp_path: Path) -> None:
    host = Host(pull=stores.Done(4, json.dumps({"APP_TOKEN": TOKEN}), "access denied\n"))
    store = open_store(READS, tmp_path, host)

    with pytest.raises(stores.StoreError) as caught:
        store.fetch()

    assert str(caught.value) == (
        "the command store failed to pull for production (exit 4): access denied"
    )


@pytest.mark.parametrize(
    "output",
    [
        "",
        f"APP_TOKEN={TOKEN}",
        json.dumps([TOKEN]),
        json.dumps(TOKEN),
        json.dumps({"APP_TOKEN": 5}),
        json.dumps({"APP_TOKEN": {"value": TOKEN}}),
        json.dumps({"APP_TOKEN": TOKEN}) + "\n" + json.dumps({"MAIL_KEY": MAIL}),
    ],
)
def test_fails_a_pull_whose_output_is_no_object_of_strings_without_showing_it(
    program: Path, tmp_path: Path, output: str
) -> None:
    host = Host(pull=stores.Done(0, output, ""))
    store = open_store(READS, tmp_path, host)

    with pytest.raises(stores.StoreError) as caught:
        store.fetch()

    assert str(caught.value) == f"{BAD_OUTPUT} for production"


def test_pulls_the_printed_values_into_the_file(program: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, BOTH)
    host = Host(pull=holds({"APP_TOKEN": "token-store-v3", "MAIL_KEY": MAIL}))

    code, out = ah_env(repo, host, "pull", "production")

    assert (code, out) == (
        0,
        [
            "pulled APP_TOKEN",
            "pulled 1 keys from the command store into .env.production",
            "wrote .env.production.pushed-store",
        ],
    )
    assert envfile.read(repo / ".env.production").values() == {
        "APP_TOKEN": "token-store-v3",
        "MAIL_KEY": MAIL,
    }


def test_pulls_nothing_from_an_empty_store(program: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, BOTH)
    host = Host(pull=stores.Done(3, "", ""))

    pulled = ah_env(repo, host, "pull", "production")
    compared = ah_env(repo, host, "diff", "production", "--store")

    assert pulled == (
        1,
        ["ah env: the command store holds nothing for production; nothing pulled"],
    )
    assert compared[1][0] == "the command store holds nothing for production"
    assert (repo / ".env.production").read_text(encoding="utf-8") == LIVE
    assert not (repo / ".env.production.pushed-store").exists()


@pytest.mark.parametrize("argv", [("pull", "production"), ("diff", "production", "--store")])
def test_refuses_to_read_a_store_with_no_pull_and_names_the_key(
    program: Path, tmp_path: Path, argv: tuple[str, ...]
) -> None:
    repo = make_repo(tmp_path, PUSH_ONLY)
    host = Host()

    code, out = ah_env(repo, host, *argv)

    assert (code, out) == (
        1,
        [
            "ah env: the command store is push only: [env.store] has no pull. Set pull to the "
            "command that prints the values, or keep another store as their source."
        ],
    )
    assert host.calls == []


def test_lists_every_key_of_the_scope_in_a_push_to_a_store_with_no_pull(
    program: Path, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path, PUSH_ONLY)
    host = Host()

    code, out = ah_env(repo, host, "push", "production", "--yes")

    assert (code, out) == (
        0,
        [
            "sent APP_TOKEN",
            "sent MAIL_KEY",
            "pushed .env.production to the command store for production: 2 keys",
            "wrote .env.production.pushed-store",
        ],
    )
    assert [call["argv"][1] for call in host.calls] == ["push"]
    assert host.sent()[0]["current"] is None


@pytest.mark.parametrize(
    ("whole", "sent"),
    [
        ("true", {"MAIL_KEY": "mail-store-v2", "APP_TOKEN": TOKEN}),
        ("false", {"APP_TOKEN": TOKEN}),
    ],
)
def test_sends_the_kept_keys_only_for_a_whole_store(
    program: Path, tmp_path: Path, whole: str, sent: dict[str, str]
) -> None:
    repo = make_repo(tmp_path, BOTH + f"whole = {whole}\n")
    host = Host(pull=holds({"MAIL_KEY": "mail-store-v2"}))

    code, _ = ah_env(
        repo, host, "--declarations", "env.toml", "push", "production", "--yes", "--apps", "api"
    )

    assert code == 0
    assert host.sent()[0]["values"] == sent
    assert host.sent()[0]["current"] == {"MAIL_KEY": "mail-store-v2"}


def test_refuses_a_push_to_a_whole_store_that_drops_a_key_until_drop_names_it(
    program: Path, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path, BOTH + "whole = true\n")
    (repo / ".env.production").write_text(f"# App\nAPP_TOKEN={TOKEN}\n", encoding="utf-8")
    host = Host(pull=holds({"APP_TOKEN": TOKEN, "MAIL_KEY": MAIL}))

    refused = ah_env(repo, host, "push", "production", "--yes")
    sent_after_refusal = host.sent()
    dropped = ah_env(repo, host, "push", "production", "--yes", "--drop", "MAIL_KEY")

    assert refused[0] == 1
    assert "the store holds keys that .env.production lacks: MAIL_KEY" in refused[1][0]
    assert sent_after_refusal == []
    assert dropped[0] == 0
    assert "dropped MAIL_KEY" in dropped[1]
    assert host.sent()[0]["values"] == {"APP_TOKEN": TOKEN}


def test_keeps_a_key_that_only_the_store_holds_when_the_store_is_not_whole(
    program: Path, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path, BOTH)
    (repo / ".env.production").write_text(f"# App\nAPP_TOKEN={TOKEN}\n", encoding="utf-8")
    host = Host(pull=holds({"APP_TOKEN": OLD, "MAIL_KEY": MAIL}))

    code, _ = ah_env(repo, host, "push", "production", "--yes")

    assert code == 0
    assert host.sent()[0]["values"] == {"APP_TOKEN": TOKEN}


def test_masks_each_known_value_in_the_message_of_a_failed_push_and_writes_no_marker(
    program: Path, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path, BOTH)
    errors = f"cannot write {TOKEN}\n\nthe store had {OLD}\nmail is {MAIL}\nfourth line\n"
    host = Host(pull=holds({"APP_TOKEN": OLD}), push=stores.Done(1, TOKEN, errors))

    code, out = ah_env(
        repo, host, "--declarations", "env.toml", "push", "production", "--yes", "--apps", "api"
    )

    assert host.sent()[0]["values"] == {"APP_TOKEN": TOKEN}
    assert (code, out[-1]) == (
        1,
        "ah env: the command store failed to push for production (exit 1): "
        "cannot write *** | the store had *** | mail is ***",
    )
    assert not (repo / ".env.production.pushed-store").exists()


def test_masks_the_values_of_the_live_file_in_the_message_of_a_failed_pull(
    program: Path, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path, BOTH)
    host = Host(pull=stores.Done(2, "", f"denied for {TOKEN}\n"))

    code, out = ah_env(repo, host, "pull", "production")

    assert (code, out) == (
        1,
        ["ah env: the command store failed to pull for production (exit 2): denied for ***"],
    )


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({}, "the command store needs [env.store] push"),
        ({"push": "env-store push"}, "[env.store] push must be a list of strings"),
        ({"push": ["env-store", 3]}, "[env.store] push must be a list of strings"),
        ({"push": []}, "[env.store] push must start with a program"),
        ({"push": ["", "push"]}, "[env.store] push must start with a program"),
        (
            {"push": ["env-store"], "pull": "env-store pull"},
            "[env.store] pull must be a list of strings",
        ),
        ({"push": ["env-store"], "pull": []}, "[env.store] pull must start with a program"),
        ({"push": ["env-store"], "whole": "true"}, "[env.store] whole must be true or false"),
        ({"push": ["env-store"], "whole": 1}, "[env.store] whole must be true or false"),
        (
            {"push": ["env-store-{env}", "push"]},
            "the command store found no program env-store-production on PATH, "
            "the first element of [env.store] push",
        ),
        (
            {"push": ["env-store"], "pull": ["scripts/absent.mjs", "pull"]},
            "the command store found no program scripts/absent.mjs in the repository root, "
            "the first element of [env.store] pull",
        ),
    ],
)
def test_refuses_a_bad_config_and_names_the_key_or_the_element(
    program: Path, tmp_path: Path, settings: dict[str, Any], message: str
) -> None:
    with pytest.raises(stores.StoreError) as caught:
        open_store(settings, tmp_path)

    assert str(caught.value) == message


def test_refuses_a_push_list_that_is_no_list_through_ah_env(program: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, '[env.store]\nkind = "command"\npush = "env-store push"\n')
    host = Host()

    code, out = ah_env(repo, host, "push", "production", "--yes")

    assert (code, out) == (1, ["ah env: [env.store] push must be a list of strings"])
    assert host.calls == []
