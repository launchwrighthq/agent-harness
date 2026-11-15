"""The git reads of `ah branch-audit`, with the blob lookups cached for one audit pass."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from agent_harness import HarnessError

SHA = re.compile(r"^[0-9a-f]{40}$")


def lines(text: str | None) -> list[str]:
    return [line for line in text.split("\n") if line] if text else []


def fields(text: str | None) -> list[str]:
    """A `-z` listing. Git quotes a non-ASCII path in its line form, and the quoted name matches
    no blob."""
    return [x for x in text.split("\0") if x] if text else []


def changed_lines(diff: str) -> list[str]:
    """The `+` and `-` lines after the first hunk header, so a `--- a/...` header never counts."""
    rows = diff.split("\n")
    start = next((i for i, line in enumerate(rows) if line.startswith("@@")), len(rows))
    return [line for line in rows[start:] if line[:1] in ("+", "-")]


def status_entries(status: str) -> list[tuple[str, str]]:
    """`git status --porcelain -z`: a rename or a copy carries its old path as the next field."""
    parts, out, i = status.split("\0"), [], 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) > 3:
            out.append((entry[:2], entry[3:]))
            if entry[0] in "RC":
                i += 1
    return out


def reflog_time(path: str) -> float:
    """The newest entry of a reflog file, in ms, or 0 when there is none."""
    if not os.path.exists(path):
        return 0
    entries = lines(Path(path).read_text(encoding="utf-8", errors="replace"))
    found = re.search(r"> (\d+) [+-]\d{4}\t?", entries[-1]) if entries else None
    return int(found[1]) * 1000 if found else 0


class Git:
    """Git in the main checkout `repo`, or in another folder of the same repository.

    `core.fsmonitor` names a program that `git status` runs, so every call turns it off.
    """

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self._blobs: dict[tuple[tuple[str, ...], str], set[str]] = {}
        self._at: dict[tuple[str, str], str | None] = {}

    def forget(self) -> None:
        self._blobs, self._at = {}, {}

    def run(
        self, args: list[str], cwd: str | None = None, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *args],
            cwd=cwd or self.repo,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def soft(self, args: list[str], cwd: str | None = None, raw: bool = False) -> str | None:
        """Stdout trimmed, or None on failure. `raw` keeps the leading space of a status line."""
        done = self.run(args, cwd)
        if done.returncode != 0:
            return None
        return done.stdout.removesuffix("\n") if raw else done.stdout.strip()

    def out(self, args: list[str], cwd: str | None = None, raw: bool = False) -> str:
        done = self.run(args, cwd)
        if done.returncode != 0:
            raise HarnessError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
        return done.stdout.removesuffix("\n") if raw else done.stdout.strip()

    def ok(self, args: list[str]) -> bool:
        return self.run(args).returncode == 0

    def blob_at(self, rev: str, path: str) -> str | None:
        key = (rev, path)
        if key not in self._at:
            self._at[key] = self.soft(["rev-parse", f"{rev}:{path}"])
        return self._at[key]

    def _batch_check(self, specs: list[str]) -> list[str]:
        done = self.run(["cat-file", "--batch-check=%(objectname)"], stdin="".join(specs))
        return done.stdout.split("\n")

    def prefetch(self, rev: str, paths: list[str]) -> None:
        """Every `rev:path` blob in one `cat-file` process, so no path count is too many."""
        want = [p for p in paths if (rev, p) not in self._at and "\n" not in p]
        if not want:
            return
        found = self._batch_check([f"{rev}:{p}\n" for p in want])
        for path, line in zip(want, found, strict=False):
            self._at[(rev, path)] = line if SHA.match(line) else None

    def history_blobs(self, revs: list[str], path: str) -> set[str]:
        """Every blob `path` has held under `revs`, over the whole history. A cap fakes UNMERGED."""
        key = (tuple(revs), path)
        if key not in self._blobs:
            commits = lines(self.soft(["log", "--format=%H", *revs, "--", path]))
            found = self._batch_check([f"{c}:{path}\n" for c in commits]) if commits else []
            self._blobs[key] = {line.strip() for line in found if SHA.match(line.strip())}
        return self._blobs[key]
