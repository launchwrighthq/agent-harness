"""The tracked documents of the spec folders, read through the git index.

An untracked draft reaches no reviewer and no CI run, so the checks and the census judge
tracked files only.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from agent_harness import HarnessError
from agent_harness.spec import restate
from agent_harness.spec.layout import INDEX_NAME, Layout

DOC_SUFFIXES = (".md", ".html")
STATUS_WORD_RE = re.compile(r"[a-z-]+")


def tracked(root: Path, pathspecs: list[str]) -> list[str]:
    """The tracked paths under `root` that match `pathspecs`, relative to `root`."""
    done = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--", *pathspecs],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise HarnessError(f"git ls-files failed in {root}: {done.stderr.strip()}")
    return sorted(name for name in done.stdout.split("\0") if name)


def documents(root: Path, layout: Layout) -> dict[str, list[str]]:
    """Per folder (each spec folder, then its archive), the tracked documents directly in it.

    A folder's own README describes the folder, so it is no document.
    """
    found: dict[str, list[str]] = {folder: [] for folder in layout.folders()}
    for name in tracked(root, list(found)):
        parent, _, base = name.rpartition("/")
        if parent in found and base.endswith(DOC_SUFFIXES) and base != INDEX_NAME:
            found[parent].append(name)
    return found


def status_parts(text: str) -> tuple[str, str] | None:
    """The status word and the text after it, from the first `Status:` token in the window."""
    lines = text.split("\n")
    hit = restate.status_line(lines)
    if hit is None:
        return None
    word, _, rest = restate.value_of(lines[hit]).partition(" ")
    found = STATUS_WORD_RE.match(word.lower())
    return (found.group(0), rest.strip()) if found else None


def read(root: Path, name: str) -> str:
    try:
        return (root / name).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise HarnessError(f"{name}: {error.strerror or error}") from error
