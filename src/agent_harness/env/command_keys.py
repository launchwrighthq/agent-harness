"""The keys that a project command declares: `[env] declarationsCommand` (docs/env.md).

A project can keep its keys in a schema module, where no code line reads the environment by
name. Its command prints one key name per line, and `where` and `check` take each name as a
declared key that the code reads. A message never repeats a line of the output.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path

from agent_harness import HarnessError
from agent_harness.env import envfile
from agent_harness.env.stores import Done

SETTING = "env.declarationsCommand"
# Seconds that the command may take. A schema module of a large project loads in a few seconds.
TIMEOUT = 60.0

# Runs one shell command in a folder with a timeout in seconds. A test passes its own.
Execute = Callable[[str, Path, float], Done]


def execute(command: str, cwd: Path, seconds: float) -> Done:
    """Runs `command` in a shell of its own process group, so a timeout stops its children too."""
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as error:
        raise HarnessError(f"{SETTING} could not start: {error}") from error
    try:
        out, err = process.communicate(timeout=seconds)
    except subprocess.TimeoutExpired as error:
        # The group is gone when its last process ended after the timeout.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise HarnessError(
            f"{SETTING} gave no answer in {seconds:g} s. It must print one key name per line "
            "and exit."
        ) from error
    return Done(process.returncode, out, err)


def names(setting: object, root: Path, run: Execute = execute) -> set[str]:
    """The key names that the command of `setting` prints, or none for an empty setting."""
    if not isinstance(setting, str):
        raise HarnessError(f"{SETTING} must be a string that holds one shell command")
    if not setting.strip():
        return set()
    done = run(setting, root, TIMEOUT)
    if done.code != 0:
        first = " ".join(done.stderr.strip().splitlines()[:1])
        raise HarnessError(
            f"{SETTING} exited {done.code}{': ' + first if first else ''}. It must print one "
            "key name per line and exit 0."
        )
    found: set[str] = set()
    for number, line in enumerate(done.stdout.splitlines(), start=1):
        name = line.strip()
        if not name:
            continue
        if not envfile.KEY_NAME.match(name):
            raise HarnessError(
                f"{SETTING}: line {number} of its output is not an env key name. It must "
                "print one key name per line and nothing else."
            )
        found.add(name)
    return found
