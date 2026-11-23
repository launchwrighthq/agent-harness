"""The worktree sweep: the apply path of `ah branch-audit` on a timer, for a narrow set of rows.

`[branches.autoApply]` turns it on. `staleHours` above 0 is the stale window of the sweep, and
`everyMinutes` is the least time between two sweeps. The `worktree-sweep` hook calls `start`,
which claims the sweep under a file lock and starts `run` as a process of its own, so no session
waits for an audit. The process keeps the lock until it ends, and its lines go to the log file.

`--apply` runs every row at risk `none`. The sweep runs fewer: the removal of a merged and clean
worktree that no live session owns, and the `-d` of the branch of such a worktree.
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agent_harness import HarnessError, config
from agent_harness.work import branch_plan
from agent_harness.work.branch_model import HOUR_MS, Op, WorktreeAudit
from agent_harness.work.branch_procs import newest_write, now_ms
from agent_harness.work.branches import Audit
from agent_harness.worktree import OWNER

KEY = "branches.autoApply"
FOLDER = "worktree-sweep"
LOCK = "lock"
STAMP = "stamp"
LOG = "log"
# The first argument of the hook script when it runs the sweep itself.
RUN = "run"
SESSION = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Settings:
    stale_hours: float
    every_minutes: float


def settings(cfg: config.Config) -> Settings:
    """`[branches.autoApply]`, each value a number of 0 or more."""
    table = cfg.get(KEY)
    if not isinstance(table, Mapping):
        raise HarnessError(f"config key {KEY} must be a table, not {table!r}")
    return Settings(_amount(table, "staleHours"), _amount(table, "everyMinutes"))


def _amount(table: Mapping[str, object], key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise HarnessError(f"config key {KEY}.{key} must be a number of 0 or more, not {value!r}")
    if not math.isfinite(value) or value < 0:
        raise HarnessError(f"config key {KEY}.{key} must be a number of 0 or more, not {value!r}")
    return float(value)


def main_checkout(project: Path) -> Path | None:
    """The main checkout of the repository of `project`, or None outside one."""
    common = config.git_common_dir(project)
    return common.parent if common is not None and common.name == ".git" else None


def state_folder(repo: Path, cfg: config.Config) -> Path:
    """The sweep folder in the state folder that every worktree of `repo` shares."""
    override = os.environ.get("AGENT_STATE_DIR")
    return (Path(override) if override else repo / str(cfg.get("specs.auditDir"))) / FOLDER


def claim(folder: Path, every_minutes: float, now: float) -> int | None:
    """The held lock file when a sweep is due, with `now` written to the stamp, else None.

    The lock is a `flock`, which ends with the last process that holds the descriptor. A second
    session gets None while a sweep runs, and inside `every_minutes` after its start.
    """
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    stamp = folder / STAMP
    try:
        last = float(stamp.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        last = 0.0
    if 0 <= now - last < every_minutes * 60:
        os.close(fd)
        return None
    stamp.write_text(f"{now}\n", encoding="utf-8")
    return fd


def start(project: Path, script: str) -> None:
    """Start one sweep of the repository of `project` when the config turns it on and one is due.

    `script` is the hook file. The sweep is `python3 <script> run <main checkout>`, in a session
    of its own, so it outlives the hook and the agent session.
    """
    repo = main_checkout(project)
    if repo is None:
        return
    cfg = config.load(repo)
    want = settings(cfg)
    if want.stale_hours == 0:
        return
    folder = state_folder(repo, cfg)
    lock = claim(folder, want.every_minutes, time.time())
    if lock is None:
        return
    try:
        with open(folder / LOG, "w", encoding="utf-8") as log:
            subprocess.Popen(
                [sys.executable, script, RUN, str(repo)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                pass_fds=(lock,),
                start_new_session=True,
            )
    finally:
        os.close(lock)


def owner_session(git_dir: str) -> str | None:
    """The session id in the owner record of a worktree, or None without a usable record."""
    try:
        text = Path(git_dir, OWNER).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if SESSION.fullmatch(text) else None


def session_written(projects_root: str, session: str) -> float:
    """The newest write of the transcript of `session` in any project folder, in ms, or 0."""
    newest = 0.0
    try:
        folders = [entry.path for entry in os.scandir(projects_root) if entry.is_dir()]
    except OSError:
        return newest
    for folder in folders:
        with contextlib.suppress(OSError):  # this project folder holds no such transcript
            newest = max(newest, os.lstat(f"{folder}/{session}.jsonl").st_mtime_ns / 1e6)
        newest = max(newest, newest_write(f"{folder}/{session}"))
    return newest


def owned(audit: Audit, row: WorktreeAudit) -> bool:
    """True when the owner session of the worktree wrote its transcript inside the stale window."""
    git_dir = audit.g.soft(["rev-parse", "--absolute-git-dir"], cwd=row.entry.path)
    session = owner_session(git_dir) if git_dir else None
    if session is None:
        return False
    written = session_written(audit.opt.projects_root, session)
    return written > 0 and (now_ms() - written) / HOUR_MS < audit.opt.stale_hours


def swept(audit: Audit, plan: list[Op]) -> list[Op]:
    """The rows of `plan` that the sweep runs."""
    removals = [
        op
        for op in plan
        if op.kind == "worktree_remove"
        and op.risk == "none"
        and isinstance(op.row, WorktreeAudit)
        and op.row.verdict == "merged"
        and not owned(audit, op.row)
    ]
    ids = {op.id for op in removals}
    deletes = [
        op for op in plan if op.kind == "branch_delete" and op.risk == "none" and op.after in ids
    ]
    return removals + deletes


def run(repo: Path) -> int:
    """Audit `repo` with the stale window of the sweep and run the swept rows. 0 when all ran."""
    want = settings(config.load(repo))
    cwd = os.path.realpath(os.getcwd())
    audit = Audit(cwd, os.path.realpath(repo / ".git"), ["--stale-hours", str(want.stale_hours)])
    if not audit.g.ok(["rev-parse", "--verify", "--quiet", audit.opt.base]):
        raise HarnessError(f"no such base ref: {audit.opt.base}")
    picked = swept(audit, audit.run().plan)
    print(f"{FOLDER}: {len(picked)} rows, stale window {want.stale_hours:g} hours")
    if not picked:
        return 0
    opt = dataclasses.replace(audit.opt, apply=True, only=[op.id for op in picked])

    def unchanged(op: Op) -> bool:
        still_free = not isinstance(op.row, WorktreeAudit) or not owned(audit, op.row)
        return still_free and audit.unchanged(op)

    return 0 if branch_plan.apply(picked, opt, audit.repo, unchanged, print) else 1
