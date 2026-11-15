"""`ah env run <file> [--require KEY ...] -- <command> [args]`: one command with the keys of an
env file in its environment.

A value that the environment of the engine exports wins over the file. Each declared `required`
key of the file's environment, and each `--require` key, must then be set and not empty, or
nothing runs. The engine prints no value: the output is the command's own, and the exit code is
the command's.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping

from agent_harness import HarnessError
from agent_harness.env import envfile
from agent_harness.env.args import UsageError, check_key, parse_args
from agent_harness.env.checks import environment_of
from agent_harness.env.context import Context

# Starts the command with the environment and gives its exit code.
Spawn = Callable[[list[str], dict[str, str]], int]


def spawn_process(argv: list[str], environ: dict[str, str]) -> int:
    return subprocess.run(argv, env=environ, check=False).returncode


def cmd_run(
    ctx: Context,
    argv: list[str],
    spawn: Spawn = spawn_process,
    environ: Mapping[str, str] = os.environ,
) -> int:
    if "--" not in argv or argv.index("--") == len(argv) - 1:
        raise UsageError("expected <file> -- <command> [args]")
    at = argv.index("--")
    args = parse_args(argv[:at], repeats=("--require",))
    if len(args.positionals) != 1:
        raise UsageError("expected <file> -- <command> [args]")
    path = ctx.at(args.positionals[0])
    bases = [base.resolve() for base in (ctx.root, ctx.main_checkout) if base is not None]
    if not any(path.resolve().is_relative_to(base) for base in bases):
        raise HarnessError(
            f"{ctx.show(path)} is outside the repository. ah env run reads only an env file of "
            "the repository."
        )
    merged = {**envfile.read(path).values(), **environ}
    needed = [check_key(key) for key in args.lists.get("--require", [])]
    if ctx.settings.get("declarations"):
        environment = environment_of(path.name)
        keys = ctx.declarations().keys
        needed = [k.name for k in keys if k.required and k.applies_to(environment)] + needed
    missing = [key for key in dict.fromkeys(needed) if not merged.get(key)]
    if missing:
        raise HarnessError(
            f"required keys are missing or empty: {', '.join(missing)}. Set them in "
            f"{ctx.show(path)}, or export them. Nothing was run."
        )
    command = argv[at + 1 :]
    try:
        return spawn(command, merged)
    except FileNotFoundError as error:
        raise HarnessError(f"cannot run {command[0]}: the command is not found") from error
