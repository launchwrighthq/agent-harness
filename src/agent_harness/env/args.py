"""The argument parser of `ah env`. An error never repeats an argument, because an argument
can be a value."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from agent_harness import HarnessError
from agent_harness.env import envfile


class UsageError(HarnessError):
    """A wrong call. `main` prints it with the usage and exits 2."""


ENV_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass
class Args:
    positionals: list[str] = field(default_factory=list)
    flags: dict[str, str | bool] = field(default_factory=dict)
    lists: dict[str, list[str]] = field(default_factory=dict)


def parse_args(
    argv: Sequence[str],
    values: Sequence[str] = (),
    switches: Sequence[str] = (),
    repeats: Sequence[str] = (),
) -> Args:
    """Split `argv` into positionals and known options. An error never repeats an argument,
    because an argument can be a value."""
    out = Args()
    index = 0
    while index < len(argv):
        word = argv[index]
        if word in switches:
            out.flags[word] = True
        elif word in values or word in repeats:
            if index + 1 >= len(argv):
                raise UsageError(f"{word} needs a value")
            index += 1
            if word in repeats:
                out.lists.setdefault(word, []).append(argv[index])
            else:
                out.flags[word] = argv[index]
        elif word.startswith("--") and len(word) > 2:
            name = word.split("=", 1)[0]
            raise UsageError(f"unknown option {name}")
        else:
            out.positionals.append(word)
        index += 1
    return out


def expect(args: Args, low: int, high: int, what: str) -> list[str]:
    if not low <= len(args.positionals) <= high:
        raise UsageError(f"expected {what}")
    return args.positionals


def check_key(key: str) -> str:
    if not envfile.KEY_NAME.match(key):
        raise UsageError("a KEY is letters, digits and _, and does not start with a digit")
    return key
