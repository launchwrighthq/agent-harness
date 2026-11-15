"""The code lines that read an env key: `where` shows them and `check` compares them with the
declarations.

Node and TypeScript: `process.env.X`, `process.env["X"]` and `import.meta.env.X`. Python:
`os.environ["X"]`, `os.environ.get("X")`, `os.getenv("X")`, and the fields of a pydantic
`BaseSettings` class read from its syntax tree, with `env_prefix` and a field `alias`. A read
names a key, a file and a line, never the text of the line.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_harness import HarnessError

NAME = r"([A-Za-z_][A-Za-z0-9_]*)"
NODE_READS = [
    re.compile(rf"\bprocess\.env\.{NAME}"),
    re.compile(rf"""\bprocess\.env\[\s*["'`]{NAME}["'`]\s*\]"""),
    re.compile(rf"\bimport\.meta\.env\.{NAME}"),
]
PYTHON_READS = [
    re.compile(rf"""(?<![\w.])(?:os\.)?environ\[\s*["']{NAME}["']\s*\]"""),
    re.compile(rf"""(?<![\w.])(?:os\.)?environ\.get\(\s*["']{NAME}["']"""),
    re.compile(rf"""(?<![\w.])(?:os\.)?getenv\(\s*["']{NAME}["']"""),
]
NODE_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte"}


@dataclass(frozen=True)
class Read:
    key: str
    path: Path
    line: int


def scan(root: Path, globs: Sequence[str], skip: Sequence[str]) -> list[Read]:
    """Every read in the files under `root` that match `globs`, by path, then by line."""
    found: list[Read] = []
    for path in sorted(files(root, globs, skip)):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += reads_in(path, text, path.relative_to(root).as_posix())
    return found


def files(root: Path, globs: Sequence[str], skip: Sequence[str]) -> Iterator[Path]:
    """The files under `root` whose relative path matches a glob, outside the `skip` folders.

    In a git repository, only the files that git tracks or does not ignore count, so generated
    and vendored code that `.gitignore` names stays out. Elsewhere every file counts.
    """
    listed = git_files(root)
    if listed is None:
        listed = []
        for folder, dirs, names in os.walk(root):
            here = Path(folder).relative_to(root).as_posix()
            dirs[:] = [d for d in dirs if not skipped(d if here == "." else f"{here}/{d}", skip)]
            listed += [(Path(folder) / name).relative_to(root).as_posix() for name in names]
    for relative in listed:
        if skipped(relative.rsplit("/", 1)[0] if "/" in relative else "", skip):
            continue
        if any(fnmatch.fnmatchcase(relative, glob) for glob in globs):
            path = root / relative
            if path.is_file():
                yield path


def skipped(folder: str, skip: Sequence[str]) -> bool:
    """True when the relative `folder` is `.git` or lies under an entry of `skip`. An entry
    without a `/` is a folder name at any depth, and an entry with one is a path from the root."""
    if not folder:
        return False
    parts = folder.split("/")
    for entry in (".git", *skip):
        path = entry.strip("/")
        if "/" not in path and path in parts:
            return True
        if "/" in path and (folder == path or folder.startswith(f"{path}/")):
            return True
    return False


def git_files(root: Path) -> list[str] | None:
    """The files that git tracks or does not ignore under `root`, or None outside a checkout."""
    if not is_checkout(root):
        return None
    return _git_list(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])


def ignored_dirs(root: Path) -> set[str]:
    """The folders under `root` that `.gitignore` ignores as a whole, without the slash."""
    if not is_checkout(root):
        return set()
    args = ["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"]
    return {name.rstrip("/") for name in _git_list(root, args) if name.endswith("/")}


def is_checkout(root: Path) -> bool:
    """True when `root` holds a git checkout: a `.git` file of a worktree, or a `.git` folder
    with a HEAD. No git runs for the answer."""
    return (root / ".git").is_file() or (root / ".git" / "HEAD").is_file()


def _git_list(root: Path, args: list[str]) -> list[str]:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise HarnessError("git is not installed; ah env reads the file list with it") from error
    if done.returncode != 0:
        raise HarnessError(f"git {args[0]} failed in {root}: {done.stderr.strip()}")
    return sorted(name for name in done.stdout.split("\0") if name)


def reads_in(path: Path, text: str, shown: str) -> list[Read]:
    if path.suffix == ".py":
        found = _line_reads(path, text, PYTHON_READS)
        if "BaseSettings" in text:
            found += settings_reads(path, text, shown)
        return sorted(found, key=lambda read: read.line)
    if path.suffix in NODE_SUFFIXES:
        return _line_reads(path, text, NODE_READS)
    return []


def _line_reads(path: Path, text: str, patterns: list[re.Pattern[str]]) -> list[Read]:
    found: list[Read] = []
    for number, line in enumerate(text.splitlines(), start=1):
        hits = sorted((m.start(), m.group(1)) for p in patterns for m in p.finditer(line))
        found += [Read(key, path, number) for _, key in hits]
    return found


def settings_reads(path: Path, text: str, shown: str) -> list[Read]:
    """The fields of each `BaseSettings` class, and of each class of the file built on one."""
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise HarnessError(
            f"{shown}: not valid Python, so its settings class is not read. Fix the file, or "
            "leave it out of [env] readers."
        ) from error
    prefixes: dict[str, str] = {}
    found: list[Read] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        parents = [_base_name(base) for base in node.bases]
        if "BaseSettings" not in parents and not any(p in prefixes for p in parents):
            continue
        inherited = next((prefixes[p] for p in parents if p in prefixes), "")
        own = _prefix(node)
        prefixes[node.name] = own if own is not None else inherited
        found += _fields(node, path, prefixes[node.name])
    return found


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _text(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _prefix(cls: ast.ClassDef) -> str | None:
    """`env_prefix` of `model_config = SettingsConfigDict(...)`, of a dict, or of `class Config`."""
    for item in cls.body:
        value = item.value if isinstance(item, (ast.Assign, ast.AnnAssign)) else None
        target = _target(item)
        if target == "model_config" and isinstance(value, ast.Call):
            found = next((k.value for k in value.keywords if k.arg == "env_prefix"), None)
            if _text(found) is not None:
                return _text(found)
        if target == "model_config" and isinstance(value, ast.Dict):
            for key, entry in zip(value.keys, value.values, strict=True):
                if _text(key) == "env_prefix" and _text(entry) is not None:
                    return _text(entry)
        if isinstance(item, ast.ClassDef) and item.name == "Config":
            for inner in item.body:
                if _target(inner) == "env_prefix" and isinstance(inner, ast.Assign):
                    return _text(inner.value)
    return None


def _target(item: ast.stmt) -> str | None:
    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
        return item.target.id
    if isinstance(item, ast.Assign) and len(item.targets) == 1:
        target = item.targets[0]
        return target.id if isinstance(target, ast.Name) else None
    return None


def _fields(cls: ast.ClassDef, path: Path, prefix: str) -> list[Read]:
    found: list[Read] = []
    for item in cls.body:
        if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
            continue
        name = item.target.id
        if name.startswith("_") or name == "model_config":
            continue
        alias = _alias(item.value)
        found.append(Read((alias or prefix + name).upper(), path, item.lineno))
    return found


def _alias(value: ast.expr | None) -> str | None:
    """The `alias` or `validation_alias` of a `Field(...)` call."""
    if not isinstance(value, ast.Call):
        return None
    for keyword in value.keywords:
        if keyword.arg in ("alias", "validation_alias") and _text(keyword.value) is not None:
            return _text(keyword.value)
    return None
