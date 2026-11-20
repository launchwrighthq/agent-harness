"""The repository config `.agents/harness.toml` with the defaults of each section.

Every key has a default, so a repository with no config file works. This module owns the
defaults of `[harness]` and `[specs]`. An area module registers the defaults of its own section
with `register_defaults` when it is imported, for example `register_defaults("env", {...})`.
`agent_harness.comments` registers `harness.comments` the same way.

A default can be a function of the repository root. `get` calls it with the root, so
`harness.baseBranch` reads the origin HEAD branch of the repository it runs in.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_harness import HarnessError

CONFIG_FILE = Path(".agents") / "harness.toml"

# The area packages that register section defaults when they are imported.
AREAS = (
    "agent_harness.install",
    "agent_harness.spec",
    "agent_harness.env",
    "agent_harness.deploy",
    "agent_harness.work",
    "agent_harness.comments",
)

DEFAULT_DIRECTIVES = {
    "-u": "Use the maximum amount of reasoning on this. Be thorough.",
    "-e": (
        "Analyze carefully: weigh the evidence, do NOT jump to conclusions, then answer in short."
    ),
    "-d": "Think hard. Keep it simple. Answer in short.",
}

# The keys `git -c` may set in an agent command. `*` matches any rest of the key. No key on the
# list runs a program or reads a file.
DEFAULT_GIT_CONFIG_ALLOW = [
    "user.name",
    "user.email",
    "color.*",
    "core.pager",
    "core.quotepath",
    "advice.*",
    "diff.algorithm",
    "diff.colormoved",
    "diff.colormovedws",
    "diff.context",
    "diff.indentheuristic",
    "diff.interhunkcontext",
    "diff.mnemonicprefix",
    "diff.noprefix",
    "diff.relative",
    "diff.renamelimit",
    "diff.renames",
    "diff.statgraphwidth",
    "log.*",
    "format.pretty",
    "format.numbered",
    "format.subjectprefix",
    "format.signoff",
    "merge.conflictstyle",
    "pull.rebase",
    "init.defaultbranch",
]


def git_common_dir(root: Path) -> Path | None:
    """The git directory that the checkouts of `root` share, read from the files in `.git`.

    A main checkout has a `.git` folder. A linked worktree has a `.git` file that names its own
    git directory, and that directory holds a `commondir` file.
    """
    dot = root / ".git"
    if dot.is_dir():
        git_dir = dot
    elif dot.is_file():
        text = dot.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return None
        git_dir = (root / text.removeprefix("gitdir:").strip()).resolve()
    else:
        return None
    common = git_dir / "commondir"
    if common.is_file():
        return (git_dir / common.read_text(encoding="utf-8").strip()).resolve()
    return git_dir


def origin_head_branch(root: Path) -> str | None:
    """The branch that `refs/remotes/origin/HEAD` points at, or None without that ref."""
    common = git_common_dir(root)
    if common is None:
        return None
    head = common / "refs" / "remotes" / "origin" / "HEAD"
    if not head.is_file():
        return None
    text = head.read_text(encoding="utf-8").strip()
    prefix = "ref: refs/remotes/origin/"
    return text.removeprefix(prefix) if text.startswith(prefix) else None


def default_base_branch(root: Path) -> str:
    """The origin HEAD branch, else `main`."""
    return origin_head_branch(root) or "main"


SECTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "harness": {
        "baseBranch": default_base_branch,
        # An empty command means the repository has none. A skill then asks the user.
        "gateCommand": "",
        "testCommand": "",
        "landCommand": "bash .claude/skills/lw-land/land.sh",
        # The lw-land options. An empty value turns the option off.
        "land": {"greenToken": "", "probeCommand": "", "afterMerge": "", "overlapExclude": []},
        "envCommand": "python3 .claude/agent-harness/bin/ah env",
        "memoryIndex": ".claude/memory/memory.md",
        "guardedFiles": [".env.production", "*.secret"],
        "secretFiles": [],
        "disable": [],
        "enable": [],
        # Extra manifests of the project, relative to the root. `ah install` adds `--manifest`.
        "manifests": [],
        "adapterDir": "harness/adapters",
        "workspace": {
            "worktreesOnly": False,
            "gitConfigAllow": DEFAULT_GIT_CONFIG_ALLOW,
            # A pattern: each repository of the operator in ~/Projects, at any depth.
            "repos": ["~/Projects/*"],
        },
        "directives": DEFAULT_DIRECTIVES,
        # `dispatch`: commands that pick their runner, for example "ci test".
        "localRuns": {"hint": "the remote gate", "dispatch": []},
        "scratchCap": {"memoryMax": "8G", "cpuMax": "400%"},
        "envDrift": {"envs": ["production"], "stores": []},
        "lint": {"words": [], "allow": []},
    },
    "specs": {
        "dirs": ["docs/specs", "docs/superpowers/plans"],
        "archive": "archive",
        "out": "dist",
        # Relative to the main checkout, so every worktree shares one audit store.
        "auditDir": ".claude/state",
        # The checks that `ah spec check` runs, and the git pathspecs it reads pointers from.
        "checks": ["archive", "superseded", "pointers", "assets"],
        "pointerFiles": ["*.md"],
        # Settled documents that stay in their active folder as a living reference.
        "keep": [],
        # Per spec folder: another kind, badge word or status vocabulary.
        "kinds": {},
    },
}


def register_defaults(section: str, defaults: Mapping[str, Any]) -> None:
    """Add the defaults of `section`. A key that the section already has gets the new value."""
    SECTION_DEFAULTS[section] = _merge(SECTION_DEFAULTS.get(section, {}), defaults)


def _merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """`base` with the keys of `over` on top. Two tables under one key merge key by key."""
    out = dict(base)
    for key, value in over.items():
        below = out.get(key)
        if isinstance(below, Mapping) and isinstance(value, Mapping):
            out[key] = _merge(below, value)
        else:
            out[key] = value
    return out


# Names the project root when the project is a folder inside a larger checkout, for example a
# demo project in a subfolder of another repository. It applies only when no `start` is given.
ROOT_ENV = "AH_ROOT"


def find_root(start: Path | None = None) -> Path:
    """The project root: `AH_ROOT` when it is set, else the top folder of the git checkout that
    holds `start`, else `start` itself."""
    named = os.environ.get(ROOT_ENV, "") if start is None else ""
    if named:
        return Path(named).resolve()
    here = (start or Path.cwd()).resolve()
    try:
        done = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return here
    top = done.stdout.strip()
    return Path(top) if done.returncode == 0 and top else here


class Config:
    """The values of one repository: the file values on top of the section defaults."""

    def __init__(self, root: Path, values: Mapping[str, Any], file: Path | None) -> None:
        self.root = root
        self.values = dict(values)
        self.file = file

    def get(self, key: str) -> Any:
        """The value at the dotted `key`, for example `harness.workspace.worktreesOnly`."""
        _import_areas()
        parts = key.split(".")
        node: Any = _merge(SECTION_DEFAULTS, self.values)
        for depth, part in enumerate(parts):
            if not isinstance(node, Mapping) or part not in node:
                raise HarnessError(_unknown(key, parts[:depth], node))
            node = node[part]
        return _resolve(node, self.root)


def _resolve(value: Any, root: Path) -> Any:
    if callable(value):
        return value(root)
    if isinstance(value, Mapping):
        return {k: _resolve(v, root) for k, v in value.items()}
    if isinstance(value, list):
        # A copy, so a caller that changes the list leaves the defaults as they are.
        return [_resolve(v, root) for v in value]
    return value


def _unknown(key: str, found: list[str], node: Any) -> str:
    where = ".".join(found)
    if isinstance(node, Mapping) and node:
        known = ", ".join(sorted(node))
        place = f"[{where}]" if where else "the config"
        return f"unknown config key {key}. The keys of {place} are: {known}"
    return f"unknown config key {key}. {where} is a value, not a table"


def _import_areas() -> None:
    for name in AREAS:
        importlib.import_module(name)


def load(root: Path | None = None) -> Config:
    """The config of the repository at `root`, or of the checkout that holds the current folder."""
    top = find_root() if root is None else root
    file = top / CONFIG_FILE
    if not file.is_file():
        return Config(top, {}, None)
    try:
        values = tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise HarnessError(f"{file} is not valid TOML: {error}. Fix the file.") from error
    return Config(top, values, file)


def get(key: str, root: Path | None = None) -> Any:
    """The value at the dotted `key` in the config of `root`."""
    return load(root).get(key)


USAGE = """usage: ah config get <key>

Prints one value of .agents/harness.toml, or its default. A string prints as it is. Any other
value prints as JSON. Keys are dotted, for example harness.baseBranch or specs.dirs."""


def main(argv: list[str], root: Path | None = None) -> int:
    if argv in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) != 2 or argv[0] != "get":
        print(USAGE, file=sys.stderr)
        return 2
    try:
        value = get(argv[1], root)
    except HarnessError as error:
        print(f"ah config: {error}", file=sys.stderr)
        return 1
    print(value if isinstance(value, str) else json.dumps(value))
    return 0
