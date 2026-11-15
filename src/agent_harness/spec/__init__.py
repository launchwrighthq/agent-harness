"""The spec family: the renderer, the audit store, the audit digest, the Codex review and the goal.

    ah spec render <file.md> [--out <dir>] | --all
    ah spec audit digest|record|table ...
    ah spec codex run|task ...
    ah spec goal start|status|clear|check ...
    ah spec restate <file> '<status>' [--archive]
    ah spec repoint <old> <new> [<old> <new> ...] [--apply]
    ah spec check | census [--json] | verify <old.html> <new.md>

The folders come from `[specs]` of `.agents/harness.toml`, the base from `harness.baseBranch`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

USAGE = """usage: ah spec <command> [args]

  render   render a spec Markdown file to its HTML page
  audit    list the rows of a spec for an auditor, save the verdict, print the table
  codex    run Codex as the second reviewer of a spec
  goal     start, read or clear a goal that holds the turn until the audit allows it
  restate  rewrite the Status line of a spec and archive a settled one
  repoint  list or fix every reference to a spec path after a move
  check    check that settled documents sit in the archive and every path resolves
  verify   compare the words of a converted spec with its old HTML page
  census   count the tracked documents by status and list the loose ends"""


def _render(argv: list[str]) -> int:
    from agent_harness import config
    from agent_harness.spec import layout, render

    return render.main(argv, layout.from_config(config.load()))


def _audit(argv: list[str]) -> int:
    from agent_harness.spec.audit import main as run

    return run(argv)


def _codex(argv: list[str]) -> int:
    from agent_harness.spec.codex import main as run

    return run(argv)


def _goal(argv: list[str]) -> int:
    from agent_harness.spec.goal import main as run

    return run(argv)


def _restate(argv: list[str]) -> int:
    from agent_harness.spec.restate import main as run

    return run(argv)


def _repoint(argv: list[str]) -> int:
    from agent_harness.spec.repoint import main as run

    return run(argv)


def _check(argv: list[str]) -> int:
    from agent_harness.spec.check import main as run

    return run(argv)


def _verify(argv: list[str]) -> int:
    from agent_harness.spec.verify import main as run

    return run(argv)


def _census(argv: list[str]) -> int:
    from agent_harness.spec.census import main as run

    return run(argv)


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "render": _render,
    "audit": _audit,
    "codex": _codex,
    "goal": _goal,
    "restate": _restate,
    "repoint": _repoint,
    "check": _check,
    "verify": _verify,
    "census": _census,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE, file=sys.stdout if argv else sys.stderr)
        return 0 if argv else 2
    command = COMMANDS.get(argv[0])
    if command is None:
        print(f"ah spec: unknown command {argv[0]}\n\n{USAGE}", file=sys.stderr)
        return 2
    return command(argv[1:])
