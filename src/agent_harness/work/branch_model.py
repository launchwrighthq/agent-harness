"""The rows of `ah branch-audit`: verdicts, file states, the audit of a ref and of a worktree."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

RISK_ORDER = ("none", "low", "medium", "high")
# The order the plan runs in: a prune and an unlock free a worktree before its removal, and a
# removal frees a branch before its delete.
KINDS = (
    "worktree_prune",
    "worktree_unlock",
    "worktree_remove",
    "branch_delete",
    "remote_delete",
    "stray_remove",
    "data_remove",
)
HOUR_MS = 3_600_000
MB = 1_048_576


@dataclass(frozen=True)
class Verdict:
    label: str
    paint: int
    offered: bool
    blurb: str


# A worktree takes the first verdict that applies, in this order.
VERDICT = {
    "in_use": Verdict("IN USE", 31, False, "a live process or a live lock holds it"),
    "stale_session": Verdict(
        "STALE SESSION", 33, False, "a live lock whose session is idle past the limit"
    ),
    "uncommitted": Verdict("UNCOMMITTED", 33, False, "holds content that exists nowhere else"),
    "holds_data": Verdict("HOLDS DATA", 33, False, "ignored bytes found nowhere else"),
    "unmerged": Verdict("UNMERGED", 31, False, "carries commits the base does not have"),
    "in_progress": Verdict("IN PROGRESS", 35, False, "touched inside the stale window"),
    "stalled": Verdict("STALLED", 36, True, "nothing new; every loose file is a stray or a copy"),
    "absorbed": Verdict("ABSORBED", 32, True, "landed by rebase or cherry-pick, under other SHAs"),
    "merged": Verdict("MERGED", 32, True, "every commit is in the base"),
}
RISK_PAINT = {"none": 32, "low": 36, "medium": 33, "high": 31}


def round1(x: float) -> float:
    """Half up to one decimal."""
    return math.floor(x * 10 + 0.5) / 10


def megabytes(size: float) -> float:
    return round1(size / MB)


def number(x: float) -> int | float:
    """A whole float prints as an integer."""
    return int(x) if float(x).is_integer() else x


def fixed1(x: float) -> str:
    return str(Decimal(x).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def mb_text(mb: float) -> str:
    return f"{fixed1(mb / 1024)} GB" if mb >= 1024 else f"{number(mb)} MB"


def age(hours: float) -> str:
    if hours < 1:
        return f"{math.floor(hours * 60 + 0.5)}m"
    if hours < 48:
        return f"{fixed1(hours)}h"
    return f"{math.floor(hours / 24 + 0.5)}d"


@dataclass
class FileState:
    """One path of a commit against the base: gone, identical, superseded, obsolete-draft, new."""

    path: str
    state: str
    note: str | None = None


@dataclass
class Candidate:
    """A commit that `git cherry` found no patch twin for, with the state of each of its files."""

    sha: str
    subject: str
    files: list[FileState]
    is_new: bool
    subject_in_base: bool = False


@dataclass
class Entry:
    """One `git worktree list --porcelain` record. `locked` is the reason, "" for a bare lock."""

    path: str
    branch: str | None = None
    head: str | None = None
    locked: str | None = None
    prunable: bool = False


@dataclass
class RefAudit:
    ref: str
    base: str
    tip: str
    when: str
    ahead: int
    behind: int
    protected: bool
    contained: bool
    duplicates: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)
    verdict: str = "merged"
    second_held: list[str] | None = None
    second_checked: int = 0
    agrees: bool | None = None
    holder: Entry | None = None
    worktree: str | None = None
    risk: str = "high"


@dataclass
class Loose:
    """A file a worktree holds outside its commits, with the state that decides its fate."""

    path: str
    state: str
    size: int = 0
    code: str = ""
    at: str | None = None
    hint: str | None = None


@dataclass
class Lock:
    state: str
    pid: int | None = None
    session_idle_hours: float | None = None


@dataclass(frozen=True)
class Proc:
    """`paths` is the working directory, then the program and every open file `/proc` shows."""

    pid: int
    ppid: int
    cwd: str
    paths: tuple[str, ...]


@dataclass
class WorktreeAudit:
    entry: Entry
    name: str
    ref: RefAudit
    stray: list[Loose]
    copies: list[Loose]
    files: list[Loose]
    holders: list[Proc]
    lock: Lock
    idle_hours: float
    size: int
    caller: bool
    verdict: str
    protected: bool
    risk: str = "high"


@dataclass
class Stray:
    """A folder under the worktree root that git does not list."""

    name: str
    path: str
    size: int
    owner: str
    idle_hours: float
    unreadable: int
    offered: bool


@dataclass
class Op:
    """One row of the plan. A row that is not `runnable` is printed for the user to run."""

    id: str
    kind: str
    subject: str
    argv: list[str]
    risk: str
    mb: float = 0
    runnable: bool = True
    after: str | None = None
    row: RefAudit | WorktreeAudit | None = None


@dataclass
class Report:
    base: str
    remote_base: str
    ahead: int | None
    behind: int | None
    worktrees: list[WorktreeAudit]
    locals: list[RefAudit]
    remotes: list[RefAudit]
    strays: list[Stray]
    prunable: list[Entry]
    plan: list[Op] = field(default_factory=list)


@dataclass
class Options:
    """The command line over the `[branches]` config. Each path is absolute."""

    base: str
    remote_base: str
    protect: list[str]
    spec_roots: list[str]
    archive: str
    worktree_root: str
    rebuildable: list[str]
    land: str
    stale_hours: float
    session_idle_days: float
    projects_root: str
    json: bool = False
    summary: bool = False
    prune: bool = False
    apply: bool = False
    only: list[str] = field(default_factory=list)
    fetch: bool = False
    verify: bool = True
    max_commits: int = 6
    max_files: int = 6
    color: bool = False
