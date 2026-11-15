import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"

MASTER = (
    "# Generated top block.\n#\n# Every other file follows it.\n\n"
    "# Database\n# The connection string.\nDATABASE_URL=\n# Pool size.\nDATABASE_POOL=5\n\n"
    "# Mail\n# The mail key.\nMAIL_KEY=\nMAIL_FROM=\n"
)
DB_URL = "postgres://app:pw-quebec-73@db.example/app"
MAIL_KEY = "mail-key-sierra-29"
MAIL_FROM = "sender-tango-64@example.com"
POOL = "pool-value-uniform-12"
STORED = "stored-token-victor-88"
VALUES = (DB_URL, MAIL_KEY, MAIL_FROM, POOL, STORED, "pw-quebec-73")

# A fake provider CLI. It appends each call to $FAKE_LOG as one JSON line: the arguments,
# standard input and the payload of each `file://` or `--file=` path. It keeps one secret per
# name in $FAKE_STATE, so a push and a pull make a round trip.
FAKE_CLI = """#!{python}
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
stdin = None if sys.stdin.isatty() else sys.stdin.read()
files = {{}}
for arg in args:
    for prefix in ("file://", "--file="):
        if arg.startswith(prefix):
            files[arg] = Path(arg[len(prefix):]).read_text()
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({{"cli": Path(sys.argv[0]).name, "args": args, "stdin": stdin,
                          "files": files}}) + "\\n")
state = Path(os.environ["FAKE_STATE"])
state.mkdir(exist_ok=True)

def value_of(flag):
    for arg in args:
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return args[args.index(flag) + 1]

def slot(name):
    return state / name.replace("/", "_")

name = Path(sys.argv[0]).name
if name == "aws" and args[1] == "get-secret-value":
    secret = slot(value_of("--secret-id"))
    if not secret.exists():
        sys.exit("An error occurred (ResourceNotFoundException) when calling GetSecretValue")
    print(secret.read_text())
elif name == "aws" and args[1] in ("put-secret-value", "create-secret"):
    key = value_of("--secret-id") if args[1] == "put-secret-value" else value_of("--name")
    slot(key).write_text(next(iter(files.values())))
elif name == "gcloud" and args[:3] == ["secrets", "versions", "access"]:
    secret = slot(value_of("--secret"))
    if not secret.exists():
        sys.exit("ERROR: NOT_FOUND: Secret not found")
    sys.stdout.write(secret.read_text())
elif name == "gcloud":
    slot(args[3] if args[1] == "versions" else args[2]).write_text(stdin)
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".env.example").write_text(MASTER, encoding="utf-8")
    return root


class Ah:
    """Runs `bin/ah env` in a repository and keeps every output for the value check."""

    def __init__(self, root: Path, extra_env: dict[str, str] | None = None) -> None:
        self.root = root
        self.env = {**os.environ, **(extra_env or {})}
        self.outputs: list[str] = []

    def __call__(self, *args: str, stdin: str | None = None, keep: bool = True) -> str:
        done = subprocess.run(
            [sys.executable, str(LAUNCHER), "env", *args],
            cwd=self.root,
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
        if keep:
            self.outputs.append(done.stdout + done.stderr)
        assert done.returncode in (0, 1), done.stderr
        return done.stdout

    def printed_values(self) -> list[str]:
        return [value for value in VALUES if any(value in text for text in self.outputs)]


def read(root: Path, name: str) -> str:
    return (root / name).read_text(encoding="utf-8")


def test_changes_env_files_and_prints_no_value(repo: Path) -> None:
    ah = Ah(repo)

    ah("set", "MAIL_KEY", ".env.local", "--stdin", stdin=MAIL_KEY + "\n")
    ah("set", "DATABASE_URL", ".env.local", "--value", DB_URL)
    ah("set", "DATABASE_POOL", ".env.local", "--value", POOL)
    ah("copy", "MAIL_KEY", ".env.local", ".env.staging")
    ah("move", "DATABASE_URL", ".env.local", ".env.staging")
    ah("set", "MAIL_FROM", ".env.extra", "--value", MAIL_FROM)
    ah("merge", ".env.extra", ".env.staging")
    ah("rename", "MAIL_KEY", "MAIL_API_KEY")
    ah("unset", "DATABASE_POOL", ".env.local")
    diff = ah("diff", ".env.local", ".env.staging")
    ah("init", ".env.production")
    ah("template", ".env.staging")
    layout = ah("layout")

    assert read(repo, ".env.local") == f"# Mail\nMAIL_API_KEY={MAIL_KEY}\n"
    assert read(repo, ".env.staging") == (
        f"# Database\nDATABASE_URL={DB_URL}\n\n"
        f"# Mail\nMAIL_API_KEY={MAIL_KEY}\nMAIL_FROM={MAIL_FROM}\n"
    )
    assert read(repo, ".env.production") == (
        "# Database\nDATABASE_URL=\nDATABASE_POOL=\n\n# Mail\nMAIL_API_KEY=\nMAIL_FROM=\n"
    )
    assert read(repo, ".env.staging.example") == (
        "# Database\nDATABASE_URL=\nDATABASE_POOL=\n\n# Mail\nMAIL_API_KEY=\nMAIL_FROM=\n"
    )
    assert "MAIL_API_KEY=\n" in read(repo, ".env.example")
    assert diff.splitlines() == [
        "only in .env.staging: DATABASE_URL",
        "only in .env.staging: MAIL_FROM",
        "1 keys match",
    ]
    assert layout == "6 env files follow the layout\n"
    assert (repo / ".env.local").stat().st_mode & 0o777 == 0o600
    assert ah.printed_values() == []


def test_reveals_a_value_only_with_the_reveal_flag(repo: Path) -> None:
    (repo / ".env.local").write_text(f"# Mail\nMAIL_KEY={MAIL_KEY}\n", encoding="utf-8")
    ah = Ah(repo)

    masked = ah("get", "MAIL_KEY", ".env.local")
    revealed = ah("get", "MAIL_KEY", ".env.local", "--reveal", keep=False)

    assert masked.startswith(f"MAIL_KEY in .env.local: set, {len(MAIL_KEY)} chars, sha256:")
    assert revealed == MAIL_KEY + "\n"
    assert ah.printed_values() == []


def test_reports_layout_findings_and_fixes_the_order(repo: Path) -> None:
    (repo / ".env.local").write_text(
        f"# Mail\nMAIL_FROM={MAIL_FROM}\nMAIL_KEY={MAIL_KEY}\n\n"
        f"# Database\nDATABASE_URL={DB_URL}\n",
        encoding="utf-8",
    )
    ah = Ah(repo)

    found = ah("layout")
    ah("layout", "--fix")

    assert found.splitlines() == [
        ".env.local:3 L4 key MAIL_KEY is out of the master's key order",
        ".env.local:5 L4 the header on line 5 is out of the master's order",
    ]
    assert read(repo, ".env.local") == (
        f"# Database\nDATABASE_URL={DB_URL}\n\n# Mail\nMAIL_KEY={MAIL_KEY}\nMAIL_FROM={MAIL_FROM}\n"
    )
    assert ah.printed_values() == []


def fake_clis(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    folder = tmp_path / "fake-bin"
    folder.mkdir()
    for name in ("aws", "gcloud", "gh", "infisical"):
        cli = folder / name
        cli.write_text(FAKE_CLI.format(python=sys.executable), encoding="utf-8")
        cli.chmod(0o755)
    env = {
        "PATH": f"{folder}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LOG": str(tmp_path / "calls.jsonl"),
        "FAKE_STATE": str(tmp_path / "state"),
    }
    return folder, env


def calls(tmp_path: Path) -> list[dict[str, Any]]:
    log = tmp_path / "calls.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return [json.loads(line) for line in lines]


def write_store(repo: Path, settings: str) -> None:
    (repo / ".agents").mkdir(exist_ok=True)
    (repo / ".agents" / "harness.toml").write_text(f"[env.store]\n{settings}\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("settings", "cli"),
    [
        ('kind = "aws-secrets-manager"\nname = "app/{env}/env"', "aws"),
        ('kind = "gcp-secret-manager"\nname = "app-{env}-env"', "gcloud"),
    ],
)
def test_pushes_and_pulls_a_file_through_the_provider_cli(
    repo: Path, tmp_path: Path, settings: str, cli: str
) -> None:
    _, env = fake_clis(tmp_path)
    write_store(repo, settings)
    (repo / ".env.production").write_text(
        f"# Database\nDATABASE_URL={DB_URL}\n\n# Mail\nMAIL_KEY={MAIL_KEY}\n", encoding="utf-8"
    )
    ah = Ah(repo, env)

    pushed = ah("push", "production", "--yes")
    (repo / ".env.production").unlink()
    pulled = ah("pull", "production")
    same = ah("diff", "production", "--store")

    assert "pushed .env.production to the" in pushed
    assert "pulled 2 keys from the" in pulled
    assert read(repo, ".env.production") == (
        f"# Database\nDATABASE_URL={DB_URL}\n\n# Mail\nMAIL_KEY={MAIL_KEY}\n"
    )
    assert same.endswith("hold the same values\n")
    assert (repo / ".env.production.pushed-store").is_file()
    recorded = calls(tmp_path)
    assert {call["cli"] for call in recorded} == {cli}
    assert all(DB_URL not in " ".join(call["args"]) for call in recorded)
    payloads = [p for call in recorded for p in [call["stdin"] or "", *call["files"].values()]]
    assert any(MAIL_KEY in payload for payload in payloads)
    assert ah.printed_values() == []


def test_sends_each_github_secret_on_standard_input(repo: Path, tmp_path: Path) -> None:
    _, env = fake_clis(tmp_path)
    write_store(repo, 'kind = "github"\nenvironment = "{env}"')
    (repo / ".env.production").write_text(f"# Mail\nMAIL_KEY={MAIL_KEY}\n", encoding="utf-8")
    ah = Ah(repo, env)

    ah("push", "production", "--yes")

    assert calls(tmp_path) == [
        {
            "cli": "gh",
            "args": ["api", "repos/{owner}/{repo}/environments/production"],
            "stdin": "",
            "files": {},
        },
        {
            "cli": "gh",
            "args": ["secret", "set", "MAIL_KEY", "--env", "production"],
            "stdin": MAIL_KEY,
            "files": {},
        },
    ]
    assert ah.printed_values() == []


def test_names_the_install_hint_when_the_provider_cli_is_missing(repo: Path) -> None:
    write_store(repo, 'kind = "infisical"')
    (repo / ".env.production").write_text(f"# Mail\nMAIL_KEY={MAIL_KEY}\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(LAUNCHER), "env", "push", "production"],
        cwd=repo,
        env={**os.environ, "PATH": str(repo / "no-bin")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 1
    assert done.stderr == (
        "ah env: the infisical store needs the infisical CLI, which is not on PATH. "
        "Install it: https://infisical.com/docs/cli/overview\n"
    )


# Exits 0 when the child sees the file value of MAIL_KEY and the exported MAIL_FROM, else 9.
CHILD = (
    "import os, sys; "
    f"sys.exit(0 if os.environ.get('MAIL_KEY') == '{MAIL_KEY}' "
    "and os.environ.get('MAIL_FROM') == 'exported-from' else 9)"
)


def test_runs_a_command_with_the_keys_of_a_file_and_passes_its_exit_code(repo: Path) -> None:
    (repo / ".env.ci").write_text(f"MAIL_KEY={MAIL_KEY}\nMAIL_FROM={MAIL_FROM}\n")
    ah = Ah(repo, {"MAIL_FROM": "exported-from"})

    def run(*child: str) -> int:
        done = subprocess.run(
            [sys.executable, str(LAUNCHER), "env", "run", ".env.ci", "--", *child],
            cwd=repo,
            env=ah.env,
            capture_output=True,
            text=True,
            check=False,
        )
        ah.outputs.append(done.stdout + done.stderr)
        return done.returncode

    assert run(sys.executable, "-c", CHILD) == 0
    assert run(sys.executable, "-c", "raise SystemExit(4)") == 4
    assert ah.printed_values() == []


def test_checks_only_files_that_git_does_not_ignore_and_skips_a_vendored_path(
    repo: Path,
) -> None:
    files = {
        ".gitignore": "/generated/\n",
        "app/main.py": 'import os\nos.environ["DATABASE_URL"]\n',
        "generated/client.py": 'import os\nos.environ["IGNORED_READ_KEY"]\n',
        "generated/sample/.env.local": "",
        "third_party/lib/conf.py": 'import os\nos.getenv("VENDORED_READ_KEY")\n',
        "app/vendor/conf.py": 'import os\nos.getenv("NESTED_VENDOR_KEY")\n',
    }
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    (repo / ".agents").mkdir()
    (repo / ".agents/harness.toml").write_text(
        '[env]\nskip = ["node_modules", "third_party", "app/vendor"]\n', encoding="utf-8"
    )
    ah = Ah(repo)

    out = ah("check")

    assert out == "env check: no finding\n"
