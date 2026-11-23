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
# Config files with `#` lines only: a workflow or another YAML file, a Dockerfile and an env
# template. A live env file never matches. The budget reads one only when a pattern of
# `include` or `overrides` names it.
CONFIG_STYLE = re.compile(
    r"(?:\.ya?ml|\.dockerfile|(?:^|/)Dockerfile(?:\.[^/]+)?|(?:^|/)\.env(?:\.[^/]+)?\.example)$"
)

# A file that the base branch does not hold is new: its blocks stop at `maxBlock` and its
# comment share at `maxShare` once it has `shareFloor` code lines. Every file stops at
# `longBlock`. `maxLineLength` 0 puts no cap on a comment line. `headBlock` above 0 is the cap
# of the block that opens a file, and the block caps then count the other blocks. `overrides`
# maps a path pattern to the caps that replace these for the files it matches.
DEFAULTS: dict[str, Any] = {
    "maxBlock": 12,
    "longBlock": 25,
    "maxShare": 0.35,
    "shareFloor": 60,
    "minLines": 20,
    "maxLineLength": 0,
    "headBlock": 0,
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
CAPS = (
    *("maxBlock", "longBlock", "maxShare", "shareFloor", "minLines", "maxLineLength"),
    "headBlock",
)

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
    # The block that opens the file, after blank lines and a shebang, and the longest other block.
    head: int = 0
    inner: int = 0
    # The lines of the Python module docstring, 0 for none.
    module_doc: int = 0


def module_docstring_lines(source: str) -> int:
    """The line count of the string that opens a Python file after comments and blank lines.

    A line scan, like `measure_text`: a string prefix such as `r` is not read.
    """
    lines = source.split("\n")
    for n, line in enumerate(lines):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        quote = text[:3] if text[:3] in ('"""', "'''") else None
        if quote is None:
            return 0
        if quote in text[3:]:
            return 1
        ends = [m for m in range(n + 1, len(lines)) if quote in lines[m]]
        return (ends[0] if ends else len(lines) - 1) - n + 1
    return 0


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
    config_style = CONFIG_STYLE.search(p) is not None
    if not config_style and not any(s.search(p) for s in (C_STYLE, HASH_STYLE, TF_STYLE)):
        return False
    if any(glob_pattern(g).match(p) for g in budget["exclude"]):
        return False
    named = [*budget["include"], *(budget["overrides"] if config_style else ())]
    return any(glob_pattern(g).match(p) for g in named)


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
    Terraform; `#` lines in a file of `CONFIG_STYLE`. A shebang is code.
    """
    hash_style = HASH_STYLE.search(path) is not None
    tf_style = TF_STYLE.search(path) is not None
    config_style = CONFIG_STYLE.search(path) is not None
    closer: str | None = None
    comment = code = run = longest = widest = widest_at = head = inner = 0
    opening = True
    for n, line in enumerate(source.split("\n")):
        text = line.strip()
        shebang = n == 0 and text.startswith("#!")
        if closer is not None:
            is_comment = True
            if closer in text:
                closer = None
        elif config_style:
            is_comment = text.startswith("#") and not shebang
        elif hash_style:
            quote = '"""' if text.startswith('"""') else "'''" if text.startswith("'''") else None
            if quote is not None:
                is_comment = True
                if quote not in text[3:]:
                    closer = quote
            else:
                is_comment = text.startswith("#") and not shebang
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
            if opening:
                head = run
            else:
                inner = max(inner, run)
            if len(text) > widest:
                widest, widest_at = len(text), n + 1
        else:
            # The opening block ends at the line after it, and at the first code line.
            opening = opening and run == 0 and (not text or shebang)
            run = 0
            if text:
                code += 1
    total = comment + code
    share = 0 if total == 0 else comment / total
    doc = module_docstring_lines(source) if path.endswith(".py") else 0
    return Measure(comment, code, share, longest, widest, widest_at, head, inner, doc)


def measure_file(file: Path, path: str) -> Measure:
    """The measure of a file on disk."""
    return measure_text(file.read_text(encoding="utf-8"), path)


def block_cap(is_new: bool, budget: Mapping[str, Any]) -> int:
    """The block cap of a file: the tighter one for a file that the base branch does not hold."""
    return int(budget["maxBlock"] if is_new else budget["longBlock"])


def block_checks(
    measured: Measure, is_new: bool, budget: Mapping[str, Any]
) -> list[tuple[str, int, int]]:
    """Each block length that has a cap, as `(name, lines, cap)`.

    Without `headBlock`, that is the longest block. With it, the block that opens the file has
    the cap `headBlock`, and the longest other block has the block cap.
    """
    cap = block_cap(is_new, budget)
    head_cap = int(budget.get("headBlock", 0))
    if head_cap <= 0:
        return [("comment block", measured.longest, cap)]
    return [
        ("opening comment block", measured.head, head_cap),
        ("comment block", measured.inner, cap),
    ]


def percent(share: float) -> int:
    """`share` in whole percent, a half rounded up."""
    return math.floor(share * 100 + 0.5)


def findings(path: str, measured: Measure, is_new: bool, budget: Mapping[str, Any]) -> list[str]:
    """What the budget of this one file (`caps_for`) says about it, one line per finding."""
    if measured.comment + measured.code < budget["minLines"]:
        return []
    out: list[str] = []
    for name, lines, cap in block_checks(measured, is_new, budget):
        if lines > cap:
            out.append(f"{path} has a {lines}-line {name}, cap {cap}")
    if measured.module_doc > budget["maxBlock"]:
        doc, most = measured.module_doc, budget["maxBlock"]
        out.append(f"{path} has a {doc}-line module docstring, cap {most}")
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
    a move rewrites the import lines, and in a short file that is half the text. The search
    covers the `include` patterns and the `overrides` patterns.
    """
    args = ["diff", "--relative", "--find-renames=30%", "--diff-filter=R", "--name-status", "-z"]
    named = [*budget["include"], *budget["overrides"]]
    done = _git(root, [*args, base, "--", *(f":(glob){g}" for g in named)])
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
    return _findings_of(root, budget, base_branch, _files(root, rel))


def _findings_of(
    root: Path, budget: Mapping[str, Any], base_branch: str, files: list[str]
) -> list[str]:
    """Each finding of the files of `files` that the budget reads, one line per finding."""
    base = _base_files(root, base_branch)
    moved = renamed(root, base_branch, budget) if base is not None else set()
    out: list[str] = []
    for file in files:
        if not in_scope(file, budget) or not (root / file).is_file():
            continue
        new = base is not None and file not in base and file not in moved
        out.extend(findings(file, measure_file(root / file, file), new, caps_for(file, budget)))
    return out


def fork_point(root: Path, base_branch: str) -> str:
    """The commit where HEAD left `base_branch`, or HEAD when the branch does not exist."""
    fork = _git(root, ["merge-base", base_branch, "HEAD"])
    return fork.stdout.strip() if fork.returncode == 0 else "HEAD"


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(root: Path, since: str, path: str) -> set[int] | None:
    """The line numbers of `path` that are new or changed since the commit `since`.

    None means every line: git does not track the file.
    """
    if _git(root, ["ls-files", "--error-unmatch", "--", path]).returncode != 0:
        return None
    args = ["diff", "--relative", "-U0", "--no-color", "--no-ext-diff", since, "--", path]
    done = _git(root, args)
    if done.returncode != 0:
        raise HarnessError(f"git diff failed in {root} for {path}: {done.stderr.strip()}")
    out: set[int] = set()
    for line in done.stdout.split("\n"):
        hunk = HUNK.match(line)
        if hunk is not None:
            start = int(hunk.group(1))
            count = 1 if hunk.group(2) is None else int(hunk.group(2))
            out.update(range(start, start + count))
    return out


def changed_files(root: Path, base_branch: str) -> list[str]:
    """The files of the working tree that changed since the base, relative to `root`.

    That is each tracked file that differs from the commit where HEAD left `base_branch`, and
    each untracked file that git does not ignore. Without the branch, the commit is HEAD. A
    deleted file is left out.
    """
    since = fork_point(root, base_branch)
    tracked = _git(root, ["diff", "--relative", "--name-only", "--diff-filter=d", "-z", since])
    untracked = _git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    for done, name in ((tracked, "diff"), (untracked, "ls-files")):
        if done.returncode != 0:
            raise HarnessError(f"git {name} failed in {root}: {done.stderr.strip()}")
    return sorted({f for f in (tracked.stdout + untracked.stdout).split("\0") if f})


def changed_findings(root: Path, budget: Mapping[str, Any], base_branch: str) -> list[str]:
    """Each finding of the comment budget in the files of `changed_files`."""
    return _findings_of(root, budget, base_branch, changed_files(root, base_branch))


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
