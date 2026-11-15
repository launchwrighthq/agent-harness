"""Rewrite the `Status:` of a spec, and move a settled spec into its `archive/` folder.

    ah spec restate <file> '<status text without "Status:">' [--archive]

Only the first `Status:` token that starts in the first 4000 characters changes, the window of
every status reader. On an HTML page the value ends at the earliest closing mark, which stays.

`--archive` takes a settled status only and refuses a file already in the archive. It stages
the new status before `git mv`, because `git mv` carries the index copy to the new path. Then
it prints the `ah spec repoint` command for the old path.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from agent_harness import HarnessError, config
from agent_harness.spec import layout, store

# What the status sits inside on a hand-written HTML page. The earliest one after the token ends
# the value, and the rest of the line is markup to keep.
CLOSERS = ("</span>", "-->", "&middot;", "</p>")
WINDOW = 4000
TOKEN = "Status:"


def status_line(lines: list[str]) -> int | None:
    """The index of the first line whose `Status:` token starts inside the window."""
    offset = 0
    for index, line in enumerate(lines):
        if offset >= WINDOW:
            return None
        at = line.find(TOKEN)
        if at != -1:
            return index if offset + at < WINDOW else None
        offset += len(line) + 1
    return None


def value_end(line: str, at: int) -> int:
    """Where the status value that starts at `at` ends: the earliest closer, else the line end."""
    ends = [end for end in (line.find(closer, at) for closer in CLOSERS) if end != -1]
    return min(ends, default=len(line))


def value_of(line: str) -> str:
    """The status text of a line, without the token, the bold marks and the closer."""
    at = line.index(TOKEN)
    return line[at + len(TOKEN) : value_end(line, at)].strip(" *")


def restate_line(line: str, status: str) -> str:
    at = line.index(TOKEN)
    end = value_end(line, at)
    tail = ""
    if end < len(line):
        # Keep the space before the mark, so `<!-- Status: draft -->` keeps its shape.
        gap = " " if end > at and line[end - 1].isspace() else ""
        tail = gap + line[end:]
    return f"{line[:at]}{TOKEN} {status}{tail}"


def restate_text(text: str, status: str) -> str:
    """`text` with its first `Status:` value in the first `WINDOW` characters set to `status`."""
    lines = text.split("\n")
    hit = status_line(lines)
    if hit is None:
        raise HarnessError(f"no {TOKEN} line in the first {WINDOW} characters")
    lines[hit] = restate_line(lines[hit], status)
    return "\n".join(lines)


def refuse_unless_archivable(path: Path, status: str, archive: str) -> None:
    if path.parent.name == archive:
        raise HarnessError(f"{path} is already in {archive}/")
    word = (status.split() or [""])[0].lower()
    if word not in store.SETTLED:
        settled = ", ".join(store.SETTLED)
        raise HarnessError(f"--archive takes a settled status ({settled}), not {word!r}")


def git_write(args: list[str]) -> None:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise HarnessError(f"git {' '.join(args)} failed: {done.stderr.strip()}")


def main(argv: list[str]) -> int:
    doc = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(prog="ah spec restate", description=doc)
    parser.add_argument("file")
    parser.add_argument("status", help='the new status text, without "Status:"')
    parser.add_argument("--archive", action="store_true", help="git mv the file into archive/")
    args = parser.parse_args(argv)
    path = Path(args.file)
    root = config.find_root()
    archive = layout.for_root(root).archive
    if args.archive:
        refuse_unless_archivable(path, args.status, archive)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise HarnessError(f"{path}: {error.strerror or error}") from error
    try:
        path.write_text(restate_text(text, args.status), encoding="utf-8")
    except HarnessError as error:
        raise HarnessError(f"{path}: {error}") from error
    if not args.archive:
        print(f"restated {path}")
        return 0
    dest = path.parent / archive / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    git_write(["add", "--", str(path)])
    git_write(["mv", str(path), str(dest)])
    old = path.resolve().relative_to(root.resolve()).as_posix()
    new = dest.resolve().relative_to(root.resolve()).as_posix()
    print(f"archived {new}")
    print(f"Repoint its references: ah spec repoint {old} {new} --apply")
    return 0
