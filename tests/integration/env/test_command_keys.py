"""`[env] declarationsCommand` with a real shell command."""

import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.env import command_keys, stores

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"
PRINT = f"{sys.executable} print_keys.py"
# Prints the keys, writes one line of error output and exits with the code of its argument.
PRINT_KEYS = """\
import sys
print("QUEUE_URL")
print("MAIL_KEY")
if len(sys.argv) > 1:
    print("no schema", file=sys.stderr)
    sys.exit(int(sys.argv[1]))
"""
# Starts a child that waits for a signal, and waits for the child.
HANG = """\
import subprocess, sys
subprocess.run([sys.executable, "-c", "import signal; signal.pause()"])
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".env.example").write_text("# App\nAPP_URL=\n", encoding="utf-8")
    (root / "print_keys.py").write_text(PRINT_KEYS, encoding="utf-8")
    (root / "hang.py").write_text(HANG, encoding="utf-8")
    (root / "app.py").write_text("import os\nos.getenv('QUEUE_URL')\n", encoding="utf-8")
    return root


def ah_env(repo: Path, command: str, *args: str) -> subprocess.CompletedProcess[str]:
    (repo / ".agents").mkdir(exist_ok=True)
    (repo / ".agents/harness.toml").write_text(
        f"[env]\ndeclarationsCommand = '{command}'\n", encoding="utf-8"
    )
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "env", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_runs_the_command_in_the_given_folder_and_returns_its_output(repo: Path) -> None:
    done = command_keys.execute(f"{PRINT} 4", repo, 30)

    assert done == stores.Done(4, "QUEUE_URL\nMAIL_KEY\n", "no schema\n")


def test_stops_a_command_and_its_children_at_the_timeout(repo: Path) -> None:
    with pytest.raises(HarnessError, match=r"env\.declarationsCommand gave no answer in 0\.2 s"):
        command_keys.execute(f"{sys.executable} hang.py", repo, 0.2)


def test_takes_the_keys_of_the_command_as_declared_in_check_and_where(repo: Path) -> None:
    check = ah_env(repo, PRINT, "check")
    where = ah_env(repo, PRINT, "where", "QUEUE_URL")

    assert (check.returncode, check.stdout) == (0, "env check: no finding\n")
    assert where.stdout.splitlines() == [
        "QUEUE_URL",
        "  absent from .env.example",
        "  declared and read by env.declarationsCommand",
        "  read in app.py:2",
    ]


def test_fails_the_check_when_the_command_exits_with_an_error(repo: Path) -> None:
    done = ah_env(repo, f"{PRINT} 2", "check")

    assert (done.returncode, done.stdout) == (1, "")
    assert done.stderr == (
        "ah env: env.declarationsCommand exited 2: no schema. It must print one key name per "
        "line and exit 0.\n"
    )
