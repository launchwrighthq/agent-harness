import json
from pathlib import Path
from typing import Any

import pytest

from agent_harness import HarnessError
from agent_harness.env import stores

SECRET = "stored-value-kilo-41"


class FakeCli:
    """Records each call, and the payload of each `file://`, `--file=` or `--spec` path at call
    time."""

    def __init__(self, replies: list[stores.Done] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.replies = replies or []

    def __call__(self, argv: list[str], stdin: str | None) -> stores.Done:
        files = {}
        for at, arg in enumerate(argv):
            for prefix in ("file://", "--file="):
                if arg.startswith(prefix):
                    files[arg] = Path(arg.removeprefix(prefix)).read_text(encoding="utf-8")
            if at > 0 and argv[at - 1] == "--spec":
                files[arg] = Path(arg).read_text(encoding="utf-8")
        self.calls.append({"argv": argv, "stdin": stdin, "files": files})
        return self.replies.pop(0) if self.replies else stores.Done(0, "", "")


@pytest.fixture
def path_with_clis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "bin"
    folder.mkdir()
    for name in ("aws", "gcloud", "gh", "infisical", "doctl", "ssh"):
        cli = folder / name
        cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))
    return folder


def open_store(settings: dict[str, Any], runner: FakeCli, root: Path) -> stores.Store:
    return stores.open_store(settings, "production", root, runner)


def all_args(cli: FakeCli) -> str:
    return " ".join(" ".join(call["argv"]) for call in cli.calls)


def test_fails_with_the_install_hint_when_the_cli_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(HarnessError) as caught:
        open_store({"kind": "aws-ssm", "prefix": "/app/{env}"}, FakeCli(), tmp_path)

    assert str(caught.value) == (
        "the aws-ssm store needs the aws CLI, which is not on PATH. Install it: "
        + stores.INSTALL_HINTS["aws"]
    )


def test_writes_one_json_secret_through_a_private_file(
    path_with_clis: Path, tmp_path: Path
) -> None:
    cli = FakeCli()
    store = open_store(
        {"kind": "aws-secrets-manager", "name": "app/{env}/env", "region": "eu-west-1"},
        cli,
        tmp_path,
    )

    store.send({"TOKEN": SECRET}, {"TOKEN": "old"})

    (call,) = cli.calls
    assert call["argv"][1:6] == [
        "secretsmanager",
        "put-secret-value",
        "--secret-id",
        "app/production/env",
        "--secret-string",
    ]
    assert call["argv"][-2:] == ["--region", "eu-west-1"]
    assert json.loads(next(iter(call["files"].values()))) == {"TOKEN": SECRET}
    assert SECRET not in all_args(cli)


def test_creates_the_secret_when_the_store_holds_none(path_with_clis: Path, tmp_path: Path) -> None:
    cli = FakeCli()
    store = open_store({"kind": "aws-secrets-manager", "name": "app/{env}"}, cli, tmp_path)

    store.send({"TOKEN": SECRET}, None)

    assert cli.calls[0]["argv"][1:5] == [
        "secretsmanager",
        "create-secret",
        "--name",
        "app/production",
    ]


def test_reads_a_missing_aws_secret_as_an_empty_store(path_with_clis: Path, tmp_path: Path) -> None:
    cli = FakeCli([stores.Done(254, "", "An error occurred (ResourceNotFoundException)")])
    store = open_store({"kind": "aws-secrets-manager", "name": "app/{env}"}, cli, tmp_path)

    assert store.fetch() is None


def test_writes_one_ssm_parameter_per_changed_key(path_with_clis: Path, tmp_path: Path) -> None:
    cli = FakeCli()
    store = open_store({"kind": "aws-ssm", "prefix": "/app/{env}"}, cli, tmp_path)

    store.send({"SAME": "kept-value", "TOKEN": SECRET}, {"SAME": "kept-value"})

    (call,) = cli.calls
    assert call["argv"][1:5] == ["ssm", "put-parameter", "--name", "/app/production/TOKEN"]
    assert list(call["files"].values()) == [SECRET]
    assert SECRET not in all_args(cli)


def test_reads_ssm_parameters_under_the_prefix(path_with_clis: Path, tmp_path: Path) -> None:
    reply = json.dumps({"Parameters": [{"Name": "/app/production/TOKEN", "Value": SECRET}]})
    store = open_store(
        {"kind": "aws-ssm", "prefix": "/app/{env}/"}, FakeCli([stores.Done(0, reply, "")]), tmp_path
    )

    assert store.fetch() == {"TOKEN": SECRET}


def test_sends_the_gcp_secret_text_on_standard_input(path_with_clis: Path, tmp_path: Path) -> None:
    cli = FakeCli()
    store = open_store(
        {"kind": "gcp-secret-manager", "name": "app-{env}-env", "project": "p1"}, cli, tmp_path
    )

    store.send({"TOKEN": SECRET}, {})

    (call,) = cli.calls
    assert call["argv"][1:] == [
        "secrets",
        "versions",
        "add",
        "app-production-env",
        "--data-file=-",
        "--project=p1",
    ]
    assert call["stdin"] == f"TOKEN={SECRET}\n"


def test_sets_each_github_secret_from_standard_input(path_with_clis: Path, tmp_path: Path) -> None:
    cli = FakeCli()
    store = open_store({"kind": "github", "repo": "o/r"}, cli, tmp_path)

    store.send({"TOKEN": SECRET}, None)

    assert cli.calls == [
        {
            "argv": [str(path_with_clis / "gh"), "secret", "set", "TOKEN", "--repo", "o/r"],
            "stdin": SECRET,
            "files": {},
        }
    ]


def test_sets_a_plain_key_as_a_github_variable_in_an_existing_environment(
    path_with_clis: Path, tmp_path: Path
) -> None:
    cli = FakeCli()
    store = open_store({"kind": "github", "environment": "{env}", "repo": "o/r"}, cli, tmp_path)

    store.send({"TOKEN": SECRET, "PORT": "8080"}, None, frozenset({"PORT"}))

    scope = ["--repo", "o/r", "--env", "production"]
    assert [(call["argv"][1:], call["stdin"]) for call in cli.calls] == [
        (["api", "repos/o/r/environments/production"], None),
        (["secret", "set", "TOKEN", *scope], SECRET),
        (["variable", "set", "PORT", *scope], "8080"),
    ]


def test_refuses_a_missing_github_environment_and_creates_none(
    path_with_clis: Path, tmp_path: Path
) -> None:
    cli = FakeCli([stores.Done(1, "", "gh: Not Found (HTTP 404)")])
    store = open_store({"kind": "github", "environment": "{env}"}, cli, tmp_path)

    with pytest.raises(HarnessError) as caught:
        store.send({"TOKEN": SECRET}, None)

    assert str(caught.value) == (
        "the GitHub environment production does not exist. Create it in GitHub (Settings > "
        "Environments) with its protection rules, then push again. --create-environment "
        "creates it with no protection rule."
    )
    assert [call["argv"][1:] for call in cli.calls] == [
        ["api", "repos/{owner}/{repo}/environments/production"]
    ]


def test_creates_a_missing_github_environment_only_when_asked(
    path_with_clis: Path, tmp_path: Path
) -> None:
    cli = FakeCli([stores.Done(1, "", "gh: Not Found (HTTP 404)")])
    store = open_store({"kind": "github", "environment": "{env}"}, cli, tmp_path)
    assert isinstance(store, stores.GithubSecrets)
    store.create_environment = True

    store.send({"TOKEN": SECRET}, None)

    assert [call["argv"][1:] for call in cli.calls] == [
        ["api", "repos/{owner}/{repo}/environments/production"],
        ["api", "--method", "PUT", "repos/{owner}/{repo}/environments/production"],
        ["secret", "set", "TOKEN", "--env", "production"],
    ]


def test_refuses_a_pull_from_the_write_only_github_store(
    path_with_clis: Path, tmp_path: Path
) -> None:
    store = open_store({"kind": "github"}, FakeCli(), tmp_path)

    with pytest.raises(HarnessError) as caught:
        store.fetch()

    assert "the github store is push only" in str(caught.value)


def test_sets_infisical_secrets_from_a_private_file(path_with_clis: Path, tmp_path: Path) -> None:
    cli = FakeCli()
    store = open_store({"kind": "infisical", "projectId": "p9"}, cli, tmp_path)

    store.send({"TOKEN": SECRET}, None)

    (call,) = cli.calls
    assert call["argv"][1:3] == ["secrets", "set"]
    assert call["argv"][-2:] == ["--env=production", "--projectId=p9"]
    assert list(call["files"].values()) == [f"TOKEN={SECRET}\n"]
    assert SECRET not in all_args(cli)


def test_names_the_cli_and_its_error_when_a_call_fails(
    path_with_clis: Path, tmp_path: Path
) -> None:
    cli = FakeCli([stores.Done(1, "", "AccessDenied: no permission\nmore detail")])
    store = open_store({"kind": "aws-ssm", "prefix": "/app"}, cli, tmp_path)

    with pytest.raises(HarnessError) as caught:
        store.fetch()

    assert str(caught.value) == (
        "aws failed to read the parameters for production (exit 1): "
        "AccessDenied: no permission | more detail"
    )


def test_keeps_a_file_store_copy_with_mode_0600(tmp_path: Path) -> None:
    store = open_store({"kind": "file", "path": "vault/.env.{env}"}, FakeCli(), tmp_path)

    store.send({"TOKEN": SECRET}, None)

    copy = tmp_path / "vault" / ".env.production"
    assert copy.read_text(encoding="utf-8") == f"TOKEN={SECRET}\n"
    assert copy.stat().st_mode & 0o777 == 0o600
    assert store.fetch() == {"TOKEN": SECRET}


def test_refuses_an_unknown_store_kind(tmp_path: Path) -> None:
    with pytest.raises(HarnessError) as caught:
        open_store({"kind": "vaultish"}, FakeCli(), tmp_path)

    assert str(caught.value).startswith("unknown store kind vaultish. Known kinds: aws-secrets")


@pytest.mark.parametrize(("kind", "cli"), [("digitalocean-app", "doctl"), ("ssh-file", "ssh")])
def test_names_the_install_hint_of_the_doctl_and_ssh_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, cli: str
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(HarnessError) as caught:
        open_store({"kind": kind}, FakeCli(), tmp_path)

    assert str(caught.value).endswith(f"Install it: {stores.INSTALL_HINTS[cli]}")


SPEC = {
    "name": "shop",
    "envs": [
        {"key": "TOKEN", "value": "EV[1:old-cipher]", "type": "SECRET", "scope": "RUN_TIME"},
        {"key": "OTHER", "value": "EV[1:kept-cipher]", "type": "SECRET"},
    ],
    "services": [{"name": "web"}],
}


def test_writes_the_app_level_envs_of_a_digitalocean_app_through_a_private_spec(
    path_with_clis: Path, tmp_path: Path
) -> None:
    cli = FakeCli([stores.Done(0, json.dumps(SPEC), "")])
    store = open_store({"kind": "digitalocean-app", "app": "app-123"}, cli, tmp_path)

    store.send({"TOKEN": SECRET, "PORT": "8080"}, None, frozenset({"PORT"}))

    get, update = cli.calls
    assert get["argv"][1:] == ["apps", "spec", "get", "app-123", "--format", "json"]
    assert update["argv"][1:4] == ["apps", "update", "app-123"]
    assert update["argv"][4] == "--spec"
    written = json.loads(next(iter(update["files"].values())))
    assert written["services"] == [{"name": "web"}]
    assert written["envs"] == [
        {"key": "TOKEN", "value": SECRET, "type": "SECRET", "scope": "RUN_TIME"},
        {"key": "OTHER", "value": "EV[1:kept-cipher]", "type": "SECRET"},
        {"key": "PORT", "value": "8080", "type": "GENERAL", "scope": "RUN_AND_BUILD_TIME"},
    ]
    assert SECRET not in all_args(cli)


def test_refuses_a_pull_from_the_encrypted_digitalocean_store(
    path_with_clis: Path, tmp_path: Path
) -> None:
    store = open_store({"kind": "digitalocean-app", "app": "app-123"}, FakeCli(), tmp_path)

    with pytest.raises(HarnessError) as caught:
        store.fetch()

    assert str(caught.value).startswith("the digitalocean-app store is push only")
    assert store.can_pull is False


class SshHost(FakeCli):
    """A fake `ssh` that runs no remote command. It answers each call from `replies`."""

    def remote(self, at: int) -> str:
        return str(self.calls[at]["argv"][-1])


def test_reads_the_env_file_of_a_host_and_reads_a_missing_one_as_empty(
    path_with_clis: Path, tmp_path: Path
) -> None:
    host = SshHost([stores.Done(0, f"TOKEN={SECRET}\n", ""), stores.Done(3, "", "")])
    store = open_store(
        {"kind": "ssh-file", "host": "deploy@box", "path": "/srv/app/.env.{env}"}, host, tmp_path
    )

    assert store.fetch() == {"TOKEN": SECRET}
    assert store.fetch() is None
    assert host.calls[0]["argv"][1:4] == ["-o", "BatchMode=yes", "deploy@box"]
    assert host.remote(0) == (
        "if [ -f /srv/app/.env.production ]; then cat -- /srv/app/.env.production; else exit 3; fi"
    )


def test_backs_up_the_host_file_before_it_writes_the_new_one(
    path_with_clis: Path, tmp_path: Path
) -> None:
    host = SshHost()
    settings = {"kind": "ssh-file", "host": "box", "path": "/srv/my app/.env.{env}"}
    store = open_store(settings, host, tmp_path)
    assert isinstance(store, stores.SshFile)
    store.clock = lambda: 1700000000.5

    store.send({"TOKEN": SECRET}, {"TOKEN": "old"})

    (call,) = host.calls
    assert call["argv"][-1] == (
        "set -eu; umask 077; mkdir -p -- '/srv/my app'; "
        "t=$(mktemp '/srv/my app/.env.ah.XXXXXX'); b=''; "
        'trap \'rm -f "$t" ${b:+"$b"}\' EXIT; '
        "if [ -f '/srv/my app/.env.production' ]; then "
        "b=$(mktemp '/srv/my app/.env.ah-backup.XXXXXX'); "
        'cp -- \'/srv/my app/.env.production\' "$b"; chmod 600 "$b"; '
        "mv -f -- \"$b\" '/srv/my app/.env.production.1700000000'; b=''; fi; "
        'cat > "$t"; chmod 600 "$t"; mv -f -- "$t" \'/srv/my app/.env.production\'; '
        "trap - EXIT"
    )
    assert call["stdin"] == f"TOKEN={SECRET}\n"
    assert SECRET not in all_args(host)


def test_writes_the_host_file_through_a_private_temp_file_with_no_fixed_name(
    path_with_clis: Path, tmp_path: Path
) -> None:
    host = SshHost()
    settings = {"kind": "ssh-file", "host": "box", "path": "/srv/app/.env.{env}"}

    open_store(settings, host, tmp_path).send({"TOKEN": SECRET}, None)

    script = str(host.calls[0]["argv"][-1])
    assert "t=$(mktemp /srv/app/.env.ah.XXXXXX)" in script
    assert 'chmod 600 "$t"' in script
    assert "ah-tmp" not in script
    assert 'cat > "$t"' in script
