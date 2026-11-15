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
# Terraform: `#` and `//` lines and `/* */` blocks.
TF_STYLE = re.compile(r"\.tf$")

# A file that the base branch does not hold is new: its blocks stop at `maxBlock` and its
# comment share at `maxShare` once it has `shareFloor` code lines. Every file stops at
# `longBlock`. `maxLineLength` 0 puts no cap on a comment line. `overrides` maps a path pattern
# to the caps that replace these for the files it matches.
DEFAULTS: dict[str, Any] = {
    "maxBlock": 12,
    "longBlock": 25,
    "maxShare": 0.35,
    "shareFloor": 60,
    "minLines": 20,
    "maxLineLength": 0,
    "include": [
        "**/*.ts",
        "**/*.mts",
        "**/*.cts",
        "**/*.tsx",
        "**/*.js",
        "**/*.mjs",
        "**/*.cjs",
        "**/*.py",
        "**/*.tf",
    ],
    "exclude": [
        "**/tests/**",
        "**/*.test.*",
        "**/test_*.py",
        "**/*.d.ts",
        "**/*.d.mts",
        "**/node_modules/**",
        "**/dist/**",
        "docs/**",
        ".claude/**",
    ],
    "overrides": {},
}
CAPS = ("maxBlock", "longBlock", "maxShare", "shareFloor", "minLines", "maxLineLength")

config.register_defaults("harness", {"comments": DEFAULTS})

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
    # The longest comment line in characters, indent left out, and its 1-based line number.
    widest: int = 0
    widest_at: int = 0


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
    if not any(style.search(p) for style in (C_STYLE, HASH_STYLE, TF_STYLE)):
        return False
    if any(glob_pattern(g).match(p) for g in budget["exclude"]):
        return False
    return any(glob_pattern(g).match(p) for g in budget["include"])


def caps_for(path: str, budget: Mapping[str, Any]) -> dict[str, Any]:
    """The budget of one file: the caps of the first `overrides` pattern that matches it."""
    out = dict(budget)
    p = path.replace("\\", "/")
    for glob, caps in budget["overrides"].items():
        if not glob_pattern(glob).match(p):
            continue
        unknown = sorted(set(caps) - set(CAPS)) if isinstance(caps, Mapping) else ["a value"]
        if unknown:
            raise HarnessError(
                f'harness.comments.overrides."{glob}" sets {", ".join(unknown)}. '
                f"An override is a table of {', '.join(CAPS)}"
            )
        out.update(caps)
        break
    return out


def measure_text(source: str, path: str = "x.mts") -> Measure:
    """The comment lines, the code lines, the comment share and the longest comment block.

    It scans lines and does not parse: `//`, `/* ... */` and a JSDoc block in JS and TS; `#`
    lines and a docstring that opens a line in Python and shell; `#`, `//` and `/* */` in
    Terraform. A shebang is code.
    """
    hash_style = HASH_STYLE.search(path) is not None
    tf_style = TF_STYLE.search(path) is not None
    closer: str | None = None
    comment = code = run = longest = widest = widest_at = 0
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
            is_comment = text.startswith("//") or (tf_style and text.startswith("#"))
        if is_comment:
            comment += 1
            run += 1
            longest = max(longest, run)
            if len(text) > widest:
                widest, widest_at = len(text), n + 1
        else:
            run = 0
            if text:
                code += 1
    total = comment + code
    share = 0 if total == 0 else comment / total
    return Measure(comment, code, share, longest, widest, widest_at)


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
    """What the budget of this one file (`caps_for`) says about it, one line per finding."""
    if measured.comment + measured.code < budget["minLines"]:
        return []
    out: list[str] = []
    cap = block_cap(is_new, budget)
    if measured.longest > cap:
        out.append(f"{path} has a {measured.longest}-line comment block, cap {cap}")
    if is_new and measured.code >= budget["shareFloor"] and measured.share > budget["maxShare"]:
        share, most = percent(measured.share), percent(budget["maxShare"])
        out.append(f"{path} is {share}% comments, cap {most}%")
    wide = budget["maxLineLength"]
    if wide and measured.widest > wide:
        out.append(
            f"{path}:{measured.widest_at} has a {measured.widest}-character comment line, "
            f"cap {wide}"
        )
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


def renamed(root: Path, base: str, budget: Mapping[str, Any]) -> set[str]:
    """The paths of the checkout that git finds renamed from a path that `base` holds.

    A moved file carries its prose with it, so it keeps the caps of an old file. Git sees a
    rename that is staged or committed. The similarity floor is 30%, not the git default of 50%:
    a move rewrites the import lines, and in a short file that is half the text.
    """
    args = ["diff", "--relative", "--find-renames=30%", "--diff-filter=R", "--name-status", "-z"]
    done = _git(root, [*args, base, "--", *(f":(glob){g}" for g in budget["include"])])
    if done.returncode != 0:
        return set()
    # One rename is three fields: the status, the old path and the new path.
    fields = done.stdout.split("\0")
    return {fields[i + 2] for i in range(0, len(fields) - 2, 3) if fields[i].startswith("R")}


def is_new(root: Path, base: str, path: str, budget: Mapping[str, Any]) -> bool:
    """True when `base` holds neither `path` nor a path that it was renamed from.

    Without the branch, no file is new.
    """
    if _git(root, ["rev-parse", "--verify", "--quiet", base]).returncode != 0:
        return False
    if _git(root, ["cat-file", "-e", f"{base}:{path}"]).returncode == 0:
        return False
    return path not in renamed(root, base, budget)


def comment_findings(
    root: Path, budget: Mapping[str, Any], base_branch: str, paths: list[str] | None = None
) -> list[str]:
    """Each finding of the comment budget in the checkout `root`, one line per finding.

    `paths` limits the run to these files or folders, relative to `root` or absolute. A file
    that `base_branch` does not hold, and that git does not find renamed from a file it holds,
    is new and gets the tighter caps.
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
    moved = renamed(root, base_branch, budget) if base is not None else set()
    out: list[str] = []
    for file in _files(root, rel):
        if not in_scope(file, budget) or not (root / file).is_file():
            continue
        new = base is not None and file not in base and file not in moved
        out.extend(findings(file, measure_file(root / file, file), new, caps_for(file, budget)))
    return out


USAGE = """usage: ah comments [path ...]

Checks the comment budget of the files of the checkout, or of the named files and folders:
the longest comment block, the comment share and the longest comment line. A file that the base
branch does not hold, and that git does not find renamed from a file it holds, is new and gets
the tighter caps. The caps are harness.comments of .agents/harness.toml."""


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
