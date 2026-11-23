"""`ah land-results`: the failures that a gate or a probe printed for `lw-land`.

A gate prints one line `verify: failures <json>`. The JSON maps each step to
`{"code": <exit>, "attributable": <bool>, "failedFiles": [<path>, ...]}`. A step is attributable
when its failed files name every failure, so a probe can run them again on the base branch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agent_harness import HarnessError

PREFIX = "verify: failures "
# A gate that does not trust its own record prints this line, and the record then counts as none.
REFUSED = "verify: refused result record:"
NO_RECORD: dict[str, dict[str, Any]] = {
    "gate": {"code": 1, "attributable": False, "failedFiles": []}
}

# The exit code of `steps` for a log without a valid record.
NO_RECORD_EXIT = 3

USAGE = """usage: ah land-results files|unaccounted|steps <log>

files        the failed files of the attributable steps, one per line, sorted
unaccounted  the failed steps that are not attributable, one per line, sorted
steps        one line per failed step, sorted: `<step>: exit <code>, failed files: <a>, <b>`

The last `verify: failures <json>` line of the log is the record. A log without a valid record
reads as one failed step `gate` that is not attributable. `steps` prints no line for such a log
and exits 3."""


def _valid(check: Any) -> bool:
    files = check.get("failedFiles") if isinstance(check, dict) else None
    return isinstance(files, list) and all(isinstance(f, str) for f in files)


def checks(text: str) -> dict[str, dict[str, Any]]:
    """The steps of the last record in `text`, or the one unaccounted step `gate`."""
    lines = text.splitlines()
    records = [line for line in lines if line.startswith(PREFIX)]
    if not records or any(line.startswith(REFUSED) for line in lines):
        return NO_RECORD
    try:
        parsed = json.loads(records[-1][len(PREFIX) :])
    except json.JSONDecodeError:
        return NO_RECORD
    if not isinstance(parsed, dict) or not all(_valid(c) for c in parsed.values()):
        return NO_RECORD
    return parsed


def failed_files(steps: dict[str, dict[str, Any]]) -> list[str]:
    return sorted({f for c in steps.values() if c.get("attributable") for f in c["failedFiles"]})


def unaccounted(steps: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(s for s, c in steps.items() if c.get("code") != 0 and not c.get("attributable"))


def step_lines(steps: dict[str, dict[str, Any]]) -> list[str]:
    """One line per step whose exit code is not 0: the step, the code and its failed files."""
    lines = []
    for step in sorted(steps):
        code = steps[step].get("code")
        if code == 0:
            continue
        files = ", ".join(steps[step]["failedFiles"])
        named = f"failed files: {files}" if files else "no failed file"
        lines.append(f"{step}: exit {'unknown' if code is None else code}, {named}")
    return lines


def main(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) != 2 or argv[0] not in ("files", "unaccounted", "steps"):
        print(USAGE, file=sys.stderr)
        return 2
    mode, log = argv
    try:
        text = Path(log).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise HarnessError(f"cannot read the gate log {log}: {error}") from error
    steps = checks(text)
    if mode == "steps":
        if steps is NO_RECORD:
            return NO_RECORD_EXIT
        names = step_lines(steps)
    else:
        names = failed_files(steps) if mode == "files" else unaccounted(steps)
    for name in names:
        print(name)
    return 0
