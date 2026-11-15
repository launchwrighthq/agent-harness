"""The lockfile of an install, and the writer that copies the planned files and removes stale ones.

The lockfile records the package version, the installed manifests, a sha256 per installed file,
the executable files, the installed skills and the skill links. A later install removes each
file and link that the previous lock lists and the new one does not.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness import HarnessError
from agent_harness.install.plan import PlannedFile, files_under

Out = Callable[[str], None]


@dataclass
class Lock:
    version: str
    mode: str
    # Each installed manifest: {"name": ..., "file": <installed path>}, in plan order.
    manifests: list[dict[str, str]] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    executable: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        data = {
            "generatedBy": "ah install",
            "version": self.version,
            "mode": self.mode,
            "manifests": self.manifests,
            "files": dict(sorted(self.files.items())),
            "executable": sorted(self.executable),
            "skills": sorted(self.skills),
            "links": sorted(self.links),
        }
        return f"{json.dumps(data, indent=2)}\n"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_lock(path: Path) -> Lock | None:
    """The lock at `path`, or None when there is none."""
    if not path.is_file():
        return None
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HarnessError(
            f"{path} is not valid JSON ({error}); delete it and install again"
        ) from error
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise HarnessError(f"{path} has no files table; delete it and install again")
    return Lock(
        version=str(data.get("version", "")),
        mode=str(data.get("mode", "")),
        manifests=[dict(m) for m in data.get("manifests", [])],
        files={str(k): str(v) for k, v in data["files"].items()},
        executable=[str(p) for p in data.get("executable", [])],
        skills=[str(s) for s in data.get("skills", [])],
        links=[str(p) for p in data.get("links", [])],
    )


def merge(*groups: Iterable[PlannedFile]) -> list[PlannedFile]:
    """The files of every group, once each. Two sources for one target fail."""
    out: dict[str, PlannedFile] = {}
    for group in groups:
        for file in group:
            prior = out.get(file.target)
            if prior is None:
                out[file.target] = file
            elif prior.source.resolve() != file.source.resolve():
                raise HarnessError(
                    f"{file.target} is written by two items ({prior.source} and {file.source})"
                )
            elif file.executable or file.paths is not None:
                out[file.target] = file
    return list(out.values())


@dataclass(frozen=True)
class Content:
    data: bytes
    executable: bool


def read_all(files: Iterable[PlannedFile]) -> dict[str, Content]:
    """The content of every planned file, read before anything is written.

    An install from the installed copy reads and writes the same files, so every read comes
    first.
    """
    return {f.target: Content(f.content(), f.mode_executable()) for f in files}


def is_executable(path: Path) -> bool:
    return (path.stat().st_mode & 0o100) != 0


def write_text(root: Path, rel: str, text: str, out: Out) -> bool:
    """Write `text` at `rel` when it differs. Returns True when it wrote."""
    path = root / rel
    data = text.encode("utf-8")
    if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_bytes(data)
    out(f"wrote {rel}")
    return True


def apply_files(
    root: Path, contents: Mapping[str, Content], previous: Lock | None, out: Out
) -> int:
    """Write each file that differs and remove each file of `previous` that `contents` lacks."""
    changes = 0
    for rel, content in contents.items():
        path = root / rel
        same = path.is_file() and not path.is_symlink() and path.read_bytes() == content.data
        if not same:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                path.unlink()
            path.write_bytes(content.data)
            out(f"wrote {rel}")
            changes += 1
        if content.executable and not is_executable(path):
            path.chmod(path.stat().st_mode | 0o111)
            if same:
                out(f"made {rel} executable")
                changes += 1
    for rel in sorted(set(previous.files) - set(contents) if previous else ()):
        if remove(root, rel):
            out(f"removed {rel}")
            changes += 1
    return changes


def remove(root: Path, rel: str) -> bool:
    """Remove the file or link at `rel` and each parent folder it leaves empty, up to `root`."""
    path = root / rel
    if not path.is_symlink() and not path.exists():
        return False
    if path.is_dir() and not path.is_symlink():
        raise HarnessError(f"{rel} is a folder; the lock names a file there. Move the folder away")
    path.unlink()
    folder = path.parent
    while folder != root and root in folder.parents and folder.is_dir():
        if any(folder.iterdir()):
            break
        folder.rmdir()
        folder = folder.parent
    return True


def file_differences(root: Path, lock: Lock, owned: Iterable[str]) -> list[tuple[str, str]]:
    """Each locked file that is missing, edited or no longer executable, and each extra file.

    An extra file is a file under one of the `owned` folders that the lock does not list. Python
    caches and names that start with a dot are not extra.
    """
    out: list[tuple[str, str]] = []
    executable = set(lock.executable)
    for rel, digest in lock.files.items():
        path = root / rel
        if not path.is_file():
            out.append((rel, "missing"))
        elif sha256(path.read_bytes()) != digest:
            out.append((rel, "edited"))
        elif rel in executable and not is_executable(path):
            out.append((rel, "mode"))
    for folder in owned:
        for name in files_under(root / folder):
            rel = f"{folder}/{name}"
            if rel not in lock.files:
                out.append((rel, "extra"))
    return out


def link_state(path: Path) -> str | None:
    """The target of the link at `path`, '' for a file or folder there, None for nothing."""
    if path.is_symlink():
        return os.readlink(path)
    return "" if path.exists() else None
