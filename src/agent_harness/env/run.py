"""`ah env run <file> [--require KEY ...] -- <command> [args]`: one command with the keys of an
env file in its environment.

A value that the environment of the engine exports wins over the file. Each declared `required`
key of the file's environment, and each `--require` key, must then be set and not empty, or
nothing runs. The command gets the standard input of the engine. The engine reads its standard
output and error line by line, writes each line out with every value of the file redacted
(`redact.Redaction`), and exits with the command's exit code.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from typing import IO

from agent_harness import HarnessError
from agent_harness.env import envfile
from agent_harness.env.args import UsageError, check_key, parse_args
from agent_harness.env.checks import environment_of
from agent_harness.env.context import Context
from agent_harness.env.redact import Redaction, pump

# Starts the command with the environment, redacts its output and gives its exit code.
Spawn = Callable[[list[str], dict[str, str], Redaction], int]


def spawn_process(argv: list[str], environ: dict[str, str], redaction: Redaction) -> int:
    child = subprocess.Popen(argv, env=environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert child.stdout is not None and child.stderr is not None
    streams: list[tuple[IO[bytes], IO[bytes]]] = [
        (child.stdout, sys.stdout.buffer),
        (child.stderr, sys.stderr.buffer),
    ]
    pumps = [threading.Thread(target=pump, args=(s, t, redaction)) for s, t in streams]
    for each in pumps:
        each.start()
    for each in pumps:
        each.join()
    return child.wait()


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
    loaded = envfile.read(path).values()
    merged = {**loaded, **environ}
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
        return spawn(command, merged, Redaction(loaded.values()))
    except FileNotFoundError as error:
        raise HarnessError(f"cannot run {command[0]}: the command is not found") from error
