"""Secret stores: each kind moves the values of one env file through a provider CLI.

docs/env.md ("Stores") lists the kinds, their keys and their CLIs. A `{env}` in `name`, `prefix`,
`path`, `environment`, `envName` and `app` is the environment name. A value never goes into an
argument list, because `ps` shows every argument to every account on the machine. A value goes
on standard input, or in a 0600 temp file that the CLI reads by path and that is removed after
the call.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_harness import HarnessError
from agent_harness.env import envfile
from agent_harness.env.redact import Redaction


@dataclass
class Done:
    code: int
    stdout: str
    stderr: str


# Runs one CLI call: the argument list and the bytes for standard input.
Runner = Callable[[list[str], str | None], Done]


def run_process(argv: list[str], stdin: str | None) -> Done:
    done = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False)
    return Done(done.returncode, done.stdout, done.stderr)


INSTALL_HINTS = {
    "aws": "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html",
    "gcloud": "https://cloud.google.com/sdk/docs/install",
    "gh": "https://cli.github.com",
    "infisical": "https://infisical.com/docs/cli/overview",
    "doctl": "https://docs.digitalocean.com/reference/doctl/how-to/install/",
    "ssh": "https://www.openssh.com/portable.html",
}


class StoreError(HarnessError):
    """A store call that failed. The message names the store and the CLI, never a value."""


def _first_lines(text: str, count: int = 3) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return " | ".join(lines[:count])


@contextmanager
def private_file(text: str) -> Iterator[Path]:
    """A 0600 temp file that holds `text`, removed when the block ends."""
    folder = Path(tempfile.mkdtemp(prefix="ah-env-"))
    path = folder / "payload"
    try:
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        yield path
    finally:
        path.unlink(missing_ok=True)
        folder.rmdir()


def dotenv_text(values: Mapping[str, str]) -> str:
    return "".join("\n".join(envfile.lines_for(k, v)) + "\n" for k, v in values.items())


class Store:
    """One configured store. A subclass sets `kind`, `cli` and the three calls."""

    kind = ""
    cli: str | None = None
    # A whole-file store replaces every key on push, so a key only the store holds is dropped.
    whole = False
    can_pull = True
    # The values of the live env file. No failure message of the command store shows one.
    known: tuple[str, ...] = ()

    def __init__(self, settings: Mapping[str, Any], env: str, root: Path, runner: Runner) -> None:
        self.settings = settings
        self.env = env
        self.root = root
        self.runner = runner
        self.binary = self._find_cli()

    @property
    def name(self) -> str:
        return str(self.settings.get("marker") or "store")

    def setting(self, key: str, default: str = "") -> str:
        value = self.settings.get(key, default)
        if not isinstance(value, str):
            raise StoreError(f"[env.store] {key} must be a string")
        return value.replace("{env}", self.env)

    def required(self, key: str) -> str:
        value = self.setting(key)
        if not value:
            raise StoreError(f"the {self.kind} store needs [env.store] {key}")
        return value

    def _find_cli(self) -> str:
        if self.cli is None:
            return ""
        found = shutil.which(self.cli)
        if found is None:
            raise StoreError(
                f"the {self.kind} store needs the {self.cli} CLI, which is not on PATH. "
                f"Install it: {INSTALL_HINTS[self.cli]}"
            )
        return found

    def call(self, args: list[str], stdin: str | None = None, what: str = "call") -> Done:
        done = self.runner([self.binary, *args], stdin)
        if done.code != 0:
            raise StoreError(
                f"{self.cli} failed to {what} for {self.env} (exit {done.code}): "
                f"{_first_lines(done.stderr)}"
            )
        return done

    def fetch(self) -> dict[str, str] | None:
        """Every key and value in the store, or None when the store has nothing for `env`."""
        raise NotImplementedError

    def send(
        self,
        values: Mapping[str, str],
        current: Mapping[str, str] | None,
        plain: frozenset[str] = frozenset(),
    ) -> None:
        """Write `values`. `plain` names the keys that the declarations mark not secret."""
        raise NotImplementedError


class AwsStore(Store):
    cli = "aws"

    def aws_args(self) -> list[str]:
        args = []
        if region := self.setting("region"):
            args += ["--region", region]
        if profile := self.setting("profile"):
            args += ["--profile", profile]
        return args


class AwsSecretsManager(AwsStore):
    kind = "aws-secrets-manager"
    whole = True

    def fetch(self) -> dict[str, str] | None:
        name = self.required("name")
        done = self.runner(
            [
                self.binary,
                "secretsmanager",
                "get-secret-value",
                "--secret-id",
                name,
                "--query",
                "SecretString",
                "--output",
                "text",
                *self.aws_args(),
            ],
            None,
        )
        if done.code != 0 and "ResourceNotFoundException" in done.stderr:
            return None
        if done.code != 0:
            raise StoreError(
                f"aws failed to read the secret of {self.env} (exit {done.code}): "
                f"{_first_lines(done.stderr)}"
            )
        try:
            payload = json.loads(done.stdout)
        except ValueError as error:
            raise StoreError(f"the secret of {self.env} is not a JSON object") from error
        if not isinstance(payload, dict):
            raise StoreError(f"the secret of {self.env} is not a JSON object")
        return {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in payload.items()}

    def send(
        self,
        values: Mapping[str, str],
        current: Mapping[str, str] | None,
        plain: frozenset[str] = frozenset(),
    ) -> None:
        name = self.required("name")
        with private_file(json.dumps(dict(values), indent=2)) as payload:
            if current is None:
                args = ["secretsmanager", "create-secret", "--name", name]
            else:
                args = ["secretsmanager", "put-secret-value", "--secret-id", name]
            args += ["--secret-string", f"file://{payload}", *self.aws_args()]
            self.call(args, what="write the secret")


class AwsSsm(AwsStore):
    kind = "aws-ssm"

    def prefix(self) -> str:
        prefix = self.required("prefix")
        return prefix if prefix.endswith("/") else prefix + "/"

    def fetch(self) -> dict[str, str] | None:
        prefix = self.prefix()
        done = self.call(
            [
                "ssm",
                "get-parameters-by-path",
                "--path",
                prefix,
                "--recursive",
                "--with-decryption",
                "--output",
                "json",
                *self.aws_args(),
            ],
            what="read the parameters",
        )
        try:
            parameters = json.loads(done.stdout or "{}").get("Parameters", [])
        except (ValueError, AttributeError) as error:
            raise StoreError(f"aws returned no parameter list for {self.env}") from error
        found = {
            str(p["Name"]).removeprefix(prefix): str(p.get("Value", ""))
            for p in parameters
            if isinstance(p, dict) and "Name" in p
        }
        return found or None

    def send(
        self,
        values: Mapping[str, str],
        current: Mapping[str, str] | None,
        plain: frozenset[str] = frozenset(),
    ) -> None:
        prefix = self.prefix()
        for key, value in values.items():
            if current is not None and current.get(key) == value:
                continue
            with private_file(value) as payload:
                self.call(
                    [
                        "ssm",
                        "put-parameter",
                        "--name",
                        prefix + key,
                        "--type",
                        "SecureString",
                        "--overwrite",
                        "--value",
                        f"file://{payload}",
                        *self.aws_args(),
                    ],
                    what=f"write the parameter {key}",
                )


class GcpSecretManager(Store):
    kind = "gcp-secret-manager"
    cli = "gcloud"
    whole = True

    def project_args(self) -> list[str]:
        project = self.setting("project")
        return [f"--project={project}"] if project else []

    def fetch(self) -> dict[str, str] | None:
        name = self.required("name")
        done = self.runner(
            [
                self.binary,
                "secrets",
                "versions",
                "access",
                "latest",
                f"--secret={name}",
                *self.project_args(),
            ],
            None,
        )
        if done.code != 0 and "NOT_FOUND" in done.stderr:
            return None
        if done.code != 0:
            raise StoreError(
                f"gcloud failed to read the secret of {self.env} (exit {done.code}): "
                f"{_first_lines(done.stderr)}"
            )
        return envfile.parse(done.stdout, f"the secret of {self.env}").values()

    def send(
        self,
        values: Mapping[str, str],
        current: Mapping[str, str] | None,
        plain: frozenset[str] = frozenset(),
    ) -> None:
        name = self.required("name")
        if current is None:
            args = ["secrets", "create", name, "--data-file=-", *self.project_args()]
        else:
            args = ["secrets", "versions", "add", name, "--data-file=-", *self.project_args()]
        self.call(args, stdin=dotenv_text(values), what="write the secret")


class GithubSecrets(Store):
    kind = "github"
    cli = "gh"
    can_pull = False
    # A missing GitHub Environment is created only when push gets --create-environment, because
    # it then has no protection rule.
    create_environment = False

    def scope_args(self) -> list[str]:
        args = []
        if repo := self.setting("repo"):
            args += ["--repo", repo]
        if environment := self.setting("environment"):
            args += ["--env", environment]
        return args

    def fetch(self) -> dict[str, str] | None:
        raise StoreError(
            "the github store is push only: GitHub never returns a secret value. "
            "Use push, or keep another store as the source of the values."
        )

    def send(
        self,
        values: Mapping[str, str],
        current: Mapping[str, str] | None,
        plain: frozenset[str] = frozenset(),
    ) -> None:
        self.ensure_environment()
        for key, value in values.items():
            kind = "variable" if key in plain else "secret"
            self.call([kind, "set", key, *self.scope_args()], stdin=value, what=f"set {key}")

    def ensure_environment(self) -> None:
        """Check that the GitHub Environment of `environment` exists. A missing one is refused,
        or created with no protection rule when `create_environment` is set."""
        environment = self.setting("environment")
        if not environment:
            return
        path = f"repos/{self.setting('repo') or '{owner}/{repo}'}/environments/{environment}"
        done = self.runner([self.binary, "api", path], None)
        if done.code == 0:
            return
        if "404" not in done.stderr and "Not Found" not in done.stderr:
            raise StoreError(
                f"gh failed to read the environment {environment} (exit {done.code}): "
                f"{_first_lines(done.stderr)}"
            )
        if not self.create_environment:
            raise StoreError(
                f"the GitHub environment {environment} does not exist. Create it in GitHub "
                "(Settings > Environments) with its protection rules, then push again. "
                "--create-environment creates it with no protection rule."
            )
        self.call(["api", "--method", "PUT", path], what=f"create the environment {environment}")


class Infisical(Store):
    kind = "infisical"
    cli = "infisical"

    def scope_args(self) -> list[str]:
        args = [f"--env={self.setting('envName', '{env}')}"]
        if project := self.setting("projectId"):
            args.append(f"--projectId={project}")
        if path := self.setting("secretPath"):
            args.append(f"--path={path}")
        return args

    def fetch(self) -> dict[str, str] | None:
        done = self.call(
            ["export", "--format=dotenv", *self.scope_args()], what="export the secrets"
        )
        found = envfile.parse(done.stdout, f"the infisical export of {self.env}").values()
        return found or None

    def send(
        self,
        values: Mapping[str, str],
        current: Mapping[str, str] | None,
        plain: frozenset[str] = frozenset(),
    ) -> None:
        with private_file(dotenv_text(values)) as payload:
            self.call(
                ["secrets", "set", f"--file={payload}", *self.scope_args()],
                what="set the secrets",
            )


class FileStore(Store):
    kind = "file"
    whole = True

    def path(self) -> Path:
        path = Path(self.required("path"))
        return path if path.is_absolute() else self.root / path

    def fetch(self) -> dict[str, str] | None:
        path = self.path()
        return envfile.read(path).values() if path.is_file() else None

    def send(
        self,
        values: Mapping[str, str],
        current: Mapping[str, str] | None,
        plain: frozenset[str] = frozenset(),
    ) -> None:
        envfile.write_text(self.path(), dotenv_text(values))


class DigitalOceanApp(Store):
    """The app-level envs of one App Platform app. The API returns a SECRET value encrypted, so
    the store is push only. A push rewrites the app spec with each key set, and keeps every
    other env and every other part of the spec as it is."""

    kind = "digitalocean-app"
    cli = "doctl"
    can_pull = False

    def fetch(self) -> dict[str, str] | None:
        raise StoreError(
            "the digitalocean-app store is push only: App Platform returns each SECRET value "
            "encrypted. Use push, or keep another store as the source of the values."
        )

    def send(
        self,
        values: Mapping[str, str],
        current: Mapping[str, str] | None,
        plain: frozenset[str] = frozenset(),
    ) -> None:
        app = self.required("app")
        done = self.call(["apps", "spec", "get", app, "--format", "json"], what="read the spec")
        try:
            spec = json.loads(done.stdout)
        except ValueError as error:
            raise StoreError(f"doctl returned no JSON spec for the app {app}") from error
        envs = [dict(entry) for entry in spec.get("envs") or []]
        for key, value in values.items():
            entry = next((e for e in envs if e.get("key") == key), None)
            if entry is None:
                entry = {"key": key, "scope": "RUN_AND_BUILD_TIME"}
                envs.append(entry)
            entry.update(value=value, type="GENERAL" if key in plain else "SECRET")
        spec["envs"] = [_env_order(entry) for entry in envs]
        with private_file(json.dumps(spec, indent=2)) as payload:
            self.call(["apps", "update", app, "--spec", str(payload)], what="update the spec")


def _env_order(entry: dict[str, Any]) -> dict[str, Any]:
    """`key`, `value`, `type`, `scope`, then the other fields, as the spec prints them."""
    first: dict[str, Any] = {n: entry[n] for n in ("key", "value", "type", "scope") if n in entry}
    return {**first, **entry}


class SshFile(Store):
    """One env file on a host, read and written over ssh. A write first copies the file to
    `<path>.<unix time>` on the host, then replaces it through a `mktemp` file of the target
    folder with mode 0600."""

    kind = "ssh-file"
    cli = "ssh"
    whole = True
    # The time of a backup name. A test sets a fixed one.
    clock: Callable[[], float] = staticmethod(time.time)

    def ssh(self, script: str, stdin: str | None = None) -> Done:
        return self.runner(
            [self.binary, "-o", "BatchMode=yes", self.required("host"), script], stdin
        )

    def fetch(self) -> dict[str, str] | None:
        path = shlex.quote(self.required("path"))
        done = self.ssh(f"if [ -f {path} ]; then cat -- {path}; else exit 3; fi")
        if done.code == 3:
            return None
        if done.code != 0:
            raise StoreError(
                f"ssh failed to read the env file of {self.env} (exit {done.code}): "
                f"{_first_lines(done.stderr)}"
            )
        return envfile.parse(done.stdout, f"the host file of {self.env}").values()

    def send(
        self,
        values: Mapping[str, str],
        current: Mapping[str, str] | None,
        plain: frozenset[str] = frozenset(),
    ) -> None:
        raw = self.required("path")
        folder = str(Path(raw).parent)
        path, backup = shlex.quote(raw), shlex.quote(f"{raw}.{int(self.clock())}")
        temp = shlex.quote(f"{folder}/.env.ah.XXXXXX")
        backup_temp = shlex.quote(f"{folder}/.env.ah-backup.XXXXXX")
        # mktemp makes each temp file with a random name and mode 0600, so a link that another
        # account plants on the host is never followed. mv replaces a link, it never writes
        # through one.
        script = (
            f"set -eu; umask 077; mkdir -p -- {shlex.quote(folder)}; "
            f"t=$(mktemp {temp}); b=''; "
            'trap \'rm -f "$t" ${b:+"$b"}\' EXIT; '
            f"if [ -f {path} ]; then b=$(mktemp {backup_temp}); "
            f'cp -- {path} "$b"; chmod 600 "$b"; mv -f -- "$b" {backup}; b=\'\'; fi; '
            f'cat > "$t"; chmod 600 "$t"; mv -f -- "$t" {path}; trap - EXIT'
        )
        done = self.ssh(script, dotenv_text(values))
        if done.code != 0:
            raise StoreError(
                f"ssh failed to write the env file of {self.env} (exit {done.code}): "
                f"{_first_lines(done.stderr)}"
            )


class CommandStore(Store):
    """Two commands of the project: `push` writes the values and `pull` prints them. Each one
    starts with no shell in the repository root, and gets one JSON object on standard input.
    docs/env.md ("The command store") holds the contract."""

    kind = "command"
    # The exit code of a pull command whose store holds nothing for the environment.
    EMPTY = 3

    def __init__(self, settings: Mapping[str, Any], env: str, root: Path, runner: Runner) -> None:
        super().__init__(settings, env, root, runner)
        push = self._argv("push")
        if push is None:
            raise StoreError("the command store needs [env.store] push")
        self.push = push
        self.pull = self._argv("pull")
        self.can_pull = self.pull is not None
        whole = settings.get("whole", False)
        if not isinstance(whole, bool):
            raise StoreError("[env.store] whole must be true or false")
        self.whole = whole

    def _argv(self, key: str) -> list[str] | None:
        """The argument list of `key` with `{env}` replaced and its program resolved, or None
        when the config does not set the key."""
        raw = self.settings.get(key)
        if raw is None:
            return None
        if not isinstance(raw, list) or not all(isinstance(part, str) for part in raw):
            raise StoreError(f"[env.store] {key} must be a list of strings")
        argv = [part.replace("{env}", self.env) for part in raw]
        if not argv or not argv[0]:
            raise StoreError(f"[env.store] {key} must start with a program")
        program = argv[0]
        found = shutil.which(str(self.root / program) if "/" in program else program)
        if found is None:
            where = "in the repository root" if "/" in program else "on PATH"
            raise StoreError(
                f"the command store found no program {program} {where}, "
                f"the first element of [env.store] {key}"
            )
        return [found, *argv[1:]]

    def _run(self, what: str, argv: list[str], payload: dict[str, Any], secrets: list[str]) -> Done:
        # The runner takes no working folder, so the call changes the folder of this process.
        with contextlib.chdir(self.root):
            done = self.runner(argv, json.dumps(payload))
        if done.code != 0 and not (what == "pull" and done.code == self.EMPTY):
            shown = Redaction([*secrets, *self.known]).line(done.stderr.encode("utf-8"))
            raise StoreError(
                f"the command store failed to {what} for {self.env} (exit {done.code}): "
                f"{_first_lines(shown.decode('utf-8'))}"
            )
        return done

    def fetch(self) -> dict[str, str] | None:
        if self.pull is None:
            raise StoreError(
                "the command store is push only: [env.store] has no pull. Set pull to the "
                "command that prints the values, or keep another store as their source."
            )
        done = self._run("pull", self.pull, {"version": 1, "env": self.env}, [])
        if done.code == self.EMPTY:
            return None
        fault = (
            "the pull command of the command store printed no JSON object of string values "
            f"for {self.env}"
        )
        try:
            found = json.loads(done.stdout)
        except ValueError as error:
            raise StoreError(fault) from error
        if not isinstance(found, dict) or not all(isinstance(v, str) for v in found.values()):
            raise StoreError(fault)
        return found

    def send(
        self,
        values: Mapping[str, str],
        current: Mapping[str, str] | None,
        plain: frozenset[str] = frozenset(),
    ) -> None:
        payload = {
            "version": 1,
            "env": self.env,
            "values": dict(values),
            "plain": sorted(plain),
            "current": None if current is None else dict(current),
        }
        self._run("push", self.push, payload, [*values.values(), *(current or {}).values()])


KINDS: dict[str, type[Store]] = {
    store.kind: store
    for store in (
        AwsSecretsManager,
        AwsSsm,
        GcpSecretManager,
        GithubSecrets,
        Infisical,
        FileStore,
        DigitalOceanApp,
        SshFile,
        CommandStore,
    )
}


def open_store(
    settings: Mapping[str, Any], env: str, root: Path, runner: Runner = run_process
) -> Store:
    kind = settings.get("kind", "")
    if not kind:
        raise StoreError(
            "no secret store is configured. Set [env.store] kind in .agents/harness.toml to one "
            f"of {', '.join(sorted(KINDS))}"
        )
    if kind not in KINDS:
        raise StoreError(f"unknown store kind {kind}. Known kinds: {', '.join(sorted(KINDS))}")
    return KINDS[kind](settings, env, root, runner)
