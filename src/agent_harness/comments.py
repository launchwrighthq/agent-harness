"""The comment budget: what a comment line is, the caps, and `ah comments`.

`ah comments` and the `comment-budget` hook both import this module, so the check and the hook
cannot disagree. The caps are `harness.comments` of the config (`.claude/rules/lw/code-limits.md`).
"""

from __future__ import annotations

import math
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from agent_harness import HarnessError, config

C_STYLE = re.compile(r"\.(c|m)?(j|t)sx?$")
HASH_STYLE = re.compile(r"\.(py|sh|bash)$")

RULE = (
    "Say the invariant in fewer lines, and put the history in a document. "
    "`.claude/rules/lw/code-limits.md` is the rule."
)


@dataclass(frozen=True)
class Measure:
    comment: int
    code: int
    share: float
    longest: int


def budget_of(cfg: config.Config) -> dict[str, Any]:
    """The budget of a project: `harness.comments`, each key on top of its default."""
    value = cfg.get("harness.comments")
    return dict(value) if isinstance(value, Mapping) else {}


@cache
def glob_pattern(glob: str) -> re.Pattern[str]:
    """A path pattern as a regex: `**` crosses folders, `*` and `?` stay inside one name."""
    out = ""
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*" and glob[i + 1 : i + 2] == "*":
            slash = glob[i + 2 : i + 3] == "/"
            out += "(?:.*/)?" if slash else ".*"
            i += 3 if slash else 2
            continue
        out += "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c)
        i += 1
    return re.compile(f"^{out}$")


def in_scope(path: str, budget: Mapping[str, Any]) -> bool:
    """True when the budget reads the project-relative `path`."""
    p = path.replace("\\", "/")
    if not C_STYLE.search(p) and not HASH_STYLE.search(p):
        return False
    if any(glob_pattern(g).match(p) for g in budget["exclude"]):
        return False
    return any(glob_pattern(g).match(p) for g in budget["include"])


def measure_text(source: str, path: str = "x.mts") -> Measure:
    """The comment lines, the code lines, the comment share and the longest comment block.

    It scans lines and does not parse: `//`, `/* ... */` and a JSDoc block in JS and TS; `#`
    lines and a docstring that opens a line in Python and shell. A shebang is code.
    """
    hash_style = HASH_STYLE.search(path) is not None
    closer: str | None = None
    comment = code = run = longest = 0
    for n, line in enumerate(source.split("\n")):
        text = line.strip()
        if closer is not None:
            is_comment = True
            if closer in text:
                closer = None
        elif hash_style:
            quote = '"""' if text.startswith('"""') else "'''" if text.startswith("'''") else None
            if quote is not None:
                is_comment = True
                if quote not in text[3:]:
                    closer = quote
            else:
                is_comment = text.startswith("#") and not (n == 0 and text.startswith("#!"))
        elif text.startswith("/*"):
            is_comment = True
            if "*/" not in text:
                closer = "*/"
        else:
            is_comment = text.startswith("//")
        if is_comment:
            comment += 1
            run += 1
            longest = max(longest, run)
        else:
            run = 0
            if text:
                code += 1
    total = comment + code
    return Measure(comment, code, 0 if total == 0 else comment / total, longest)


def measure_file(file: Path, path: str) -> Measure:
    """The measure of a file on disk."""
    return measure_text(file.read_text(encoding="utf-8"), path)


def block_cap(is_new: bool, budget: Mapping[str, Any]) -> int:
    """The block cap of a file: the tighter one for a file that the base branch does not hold."""
    return int(budget["maxBlock"] if is_new else budget["longBlock"])


def percent(share: float) -> int:
    """`share` in whole percent, a half rounded up."""
    return math.floor(share * 100 + 0.5)


def findings(path: str, measured: Measure, is_new: bool, budget: Mapping[str, Any]) -> list[str]:
    """What the budget says about one measured file, one line per finding."""
    if measured.comment + measured.code < budget["minLines"]:
        return []
    out: list[str] = []
    cap = block_cap(is_new, budget)
    if measured.longest > cap:
        out.append(f"{path} has a {measured.longest}-line comment block, cap {cap}")
    if is_new and measured.code >= budget["shareFloor"] and measured.share > budget["maxShare"]:
        share, most = percent(measured.share), percent(budget["maxShare"])
        out.append(f"{path} is {share}% comments, cap {most}%")
    return out


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise HarnessError(f"git {args[0]} failed to start in {root}: {error}") from error


def _files(root: Path, paths: list[str]) -> list[str]:
    """The tracked and untracked (not ignored) files under `paths`, relative to `root`."""
    done = _git(
        root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *paths]
    )
    if done.returncode != 0:
        raise HarnessError(f"git ls-files failed in {root}; ah comments needs a git checkout")
    return sorted({f for f in done.stdout.split("\0") if f})


def _base_files(root: Path, base: str) -> set[str] | None:
    """The paths that `base` holds, or None when the branch does not exist: then no file is new."""
    done = _git(root, ["ls-tree", "-r", "--name-only", "-z", base])
    return {f for f in done.stdout.split("\0") if f} if done.returncode == 0 else None


def comment_findings(
    root: Path, budget: Mapping[str, Any], base_branch: str, paths: list[str] | None = None
) -> list[str]:
    """Each finding of the comment budget in the checkout `root`, one line per finding.

    `paths` limits the run to these files or folders, relative to `root` or absolute. A file
    that `base_branch` does not hold is new and gets the tighter caps.
    """
    rel: list[str] = []
    for p in paths or []:
        target = Path(p) if Path(p).is_absolute() else root / p
        try:
            r = target.resolve().relative_to(root.resolve()).as_posix()
        except ValueError as error:
            raise HarnessError(f"{p} is outside the checkout {root}") from error
        if not (root / r).exists():
            raise HarnessError(f"{p} does not exist")
        rel.append(r)
    base = _base_files(root, base_branch)
    out: list[str] = []
    for file in _files(root, rel):
        if not in_scope(file, budget) or not (root / file).is_file():
            continue
        is_new = base is not None and file not in base
        out.extend(findings(file, measure_file(root / file, file), is_new, budget))
    return out


USAGE = """usage: ah comments [path ...]

Checks the comment budget of the files of the checkout, or of the named files and folders:
the longest comment block and the comment share. A file that the base branch does not hold is
new and gets the tighter caps. The caps are harness.comments of .agents/harness.toml."""


def main(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    root = config.find_root()
    cfg = config.load(root)
    found = comment_findings(root, budget_of(cfg), str(cfg.get("harness.baseBranch")), argv)
    for line in found:
        print(line)
    if found:
        rule = ".claude/rules/lw/code-limits.md"
        print(f"ah comments: {len(found)} comment budget findings; see {rule}")
        return 1
    print("ah comments: every file is within the comment budget")
    return 0
