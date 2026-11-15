"""The `ah` command: the global hand-over, then one area entry point per command.

Each command maps to a module and a function with the signature `main(argv) -> int`. The module
loads only when its command runs.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from agent_harness import HarnessError, __version__
from agent_harness.config import find_root

# Set in the environment of the project engine that a global engine starts. A launcher that sees
# it never hands over again, so two installs cannot start each other in a loop.
HANDOVER_GUARD = "AH_HANDED_OVER"

PROJECT_ENGINE = Path(".claude") / "agent-harness" / "bin" / "ah"

COMMANDS: dict[str, tuple[str, str, str]] = {
    "install": ("agent_harness.install", "main", "install the harness in a project or globally"),
    "check": ("agent_harness.install", "check", "compare the installed files with the lockfile"),
    "rules": ("agent_harness.install", "rules", "print the size of each installed rule"),
    "selftest": ("agent_harness.selftest", "main", "run the fixture of every installed hook"),
    "lint": ("agent_harness.lint", "main", "find banned words, fillers and em dashes"),
    "comments": ("agent_harness.comments", "main", "check the comment budget of the source files"),
    "config": ("agent_harness.config", "main", "print one value of .agents/harness.toml"),
    "spec": ("agent_harness.spec", "main", "render a spec and read the audit store"),
    "env": ("agent_harness.env", "main", "read and change .env files without showing values"),
    "deploy-status": ("agent_harness.deploy", "main", "print the verdict of the last deploy"),
    "work-status": ("agent_harness.work", "main", "report the work in progress from evidence"),
    "branch-audit": ("agent_harness.work", "branch_audit", "judge what each branch holds"),
    "land-results": ("agent_harness.land_results", "main", "read the failures of a gate log"),
    "worktree": ("agent_harness.worktree", "main", "add or remove a worktree in .claude/worktrees"),
}


def usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = [f"  {name.ljust(width)}  {text}" for name, (_, _, text) in COMMANDS.items()]
    return "\n".join([f"usage: ah <command> [args]  (agent-harness {__version__})", "", *lines])


def handover_target(own_root: Path, repo_root: Path, handed_over: bool) -> Path | None:
    """The project engine that this launcher hands over to, or None to run here.

    A launcher outside the repository (a global install) hands over when the repository has a
    project install. A launcher inside the repository runs its own engine. A launcher that a
    hand-over started never hands over again.
    """
    if handed_over:
        return None
    target = repo_root / PROJECT_ENGINE
    if not target.is_file():
        return None
    own = own_root.resolve()
    repo = repo_root.resolve()
    if own == repo or repo in own.parents:
        return None
    return target


def dispatch(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage(), file=sys.stdout if argv else sys.stderr)
        return 0 if argv else 2
    if argv[0] == "--version":
        print(__version__)
        return 0
    command, rest = argv[0], argv[1:]
    if command not in COMMANDS:
        print(f"ah: unknown command {command}\n\n{usage()}", file=sys.stderr)
        return 2
    module_name, function, _ = COMMANDS[command]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise
        print(f"ah {command}: not built yet", file=sys.stderr)
        return 2
    entry = cast(Callable[[list[str]], int], getattr(module, function))
    try:
        return entry(rest)
    except HarnessError as error:
        print(f"ah {command}: {error}", file=sys.stderr)
        return 1


def run(argv: list[str], own_root: Path) -> int:
    """The launcher body. `own_root` is the folder that holds the launcher's `bin/`."""
    # The guard leaves the environment here, so a child process of this engine can hand over in
    # another repository.
    handed_over = os.environ.pop(HANDOVER_GUARD, None) is not None
    # `install` always runs the engine that the user started: it installs that engine's package.
    installs = bool(argv) and argv[0] == "install"
    target = None if installs else handover_target(own_root, find_root(), handed_over)
    if target is not None:
        env = {**os.environ, HANDOVER_GUARD: "1"}
        os.execve(sys.executable, [sys.executable, str(target), *argv], env)
    return dispatch(argv)
