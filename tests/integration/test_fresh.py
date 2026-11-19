"""A hook of a worktree install runs the main checkout's copy when that install is newer."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))

import hook_project

HOOK = "prompt-directives.py"
STUB = 'print("from-main")\n'


def setup(tmp_path: Path, main_version: str, own_version: str) -> Path:
    main = hook_project.make_project(tmp_path / "main")
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(
        ["git", "-C", str(main), *identity, "commit", "--allow-empty", "-q", "-m", "init"],
        check=True,
    )
    worktree = main / ".claude" / "worktrees" / "wt"
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "-q", "-b", "wt", str(worktree)], check=True
    )
    hook_project.install_hooks(worktree)
    for root, version in ((main, main_version), (worktree, own_version)):
        lock = root / hook_project.INSTALLED / "lock.json"
        lock.write_text(json.dumps({"version": version}), encoding="utf-8")
    (main / hook_project.INSTALLED / "hooks" / HOOK).write_text(STUB, encoding="utf-8")
    return worktree


def prompt(worktree: Path) -> str:
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    done = hook_project.run_hook(worktree, HOOK, "UserPromptSubmit", payload)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_runs_the_main_checkout_copy_when_its_install_is_newer(tmp_path: Path) -> None:
    assert prompt(setup(tmp_path, "0.2.9", "0.2.8")) == "from-main"


def test_keeps_its_own_copy_when_the_main_install_is_not_newer(tmp_path: Path) -> None:
    assert prompt(setup(tmp_path, "0.2.8", "0.2.8")) == ""
