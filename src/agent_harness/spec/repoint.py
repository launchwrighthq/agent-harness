"""List or fix every tracked reference to a spec path after the spec moved.

    ah spec repoint <old path> <new path> [<old path> <new path> ...] [--apply]

Each pair is relative to the repository root, for example `docs/specs/a.md` and
`docs/specs/archive/a.md`. Without `--apply` the command lists each tracked file that names an
old path and changes nothing. With `--apply` it writes the new path into each of those files.

The search is `git grep`, so it reads tracked files only. A recursive grep also reads the
worktrees under `.claude/worktrees/` and any large untracked data, and can take minutes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from agent_harness import HarnessError, config


def citing_files(root: Path, old: str, new: str) -> list[str]:
    """The tracked files under `root` that name `old`, without the moved file itself."""
    done = subprocess.run(
        ["git", "-C", str(root), "grep", "-l", "-F", "-e", old, "--", ".", f":!{new}", f":!{old}"],
        capture_output=True,
        text=True,
        check=False,
    )
    # `git grep` exits 1 when nothing matches, and 2 or more on an error.
    if done.returncode > 1:
        raise HarnessError(f"git grep failed in {root}: {done.stderr.strip()}")
    return [line for line in done.stdout.splitlines() if line]


def repoint(root: Path, old: str, new: str, apply: bool) -> list[str]:
    """Each file that names `old`. With `apply`, `new` replaces every `old` in each of them."""
    files = citing_files(root, old, new)
    if apply:
        for name in files:
            path = root / name
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace(old, new), encoding="utf-8")
    return files


def main(argv: list[str]) -> int:
    doc = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(prog="ah spec repoint", description=doc)
    parser.add_argument("paths", nargs="+", help="old path, new path, and more pairs")
    parser.add_argument("--apply", action="store_true", help="write the new path into each file")
    args = parser.parse_args(argv)
    pairs = list(zip(args.paths[::2], args.paths[1::2], strict=False))
    if len(args.paths) % 2 or any(old == new or not old or not new for old, new in pairs):
        print("ah spec repoint: give pairs of two different paths", file=sys.stderr)
        return 2
    root = config.find_root()
    for old, new in pairs:
        report(old, repoint(root, old, new, args.apply), args.apply)
    return 0


def report(old: str, files: list[str], applied: bool) -> None:
    verb = "repointed" if applied else "cites"
    for name in files:
        print(f"{verb} {name}")
    if not files:
        print(f"no tracked file names {old}")
    elif not applied:
        print(f"{len(files)} files name {old}. Run again with --apply to repoint them.")
