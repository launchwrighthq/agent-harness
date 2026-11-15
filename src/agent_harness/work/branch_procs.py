"""Who holds a worktree: a process inside it, a git lock with a live owner, a live session.

On Linux every process comes from `/proc`, with its working directory, its program and its open
files. Elsewhere `lsof` gives the working directories only.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from agent_harness.work.branch_model import HOUR_MS, Entry, Lock, Options, Proc, round1


def has_proc() -> bool:
    return os.path.exists("/proc/self/stat")


def now_ms() -> float:
    return time.time() * 1000


def inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + os.sep)


def _links(pid: str) -> list[str]:
    """The program and the open files of a process, empty for one another user owns."""
    out = []
    with contextlib.suppress(OSError):  # a kernel thread has no program
        out.append(os.readlink(f"/proc/{pid}/exe"))
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return out  # another user's process, or one that exited: neither shows its files
    for fd in fds:
        with contextlib.suppress(OSError):  # a descriptor closed mid-walk holds nothing
            out.append(os.readlink(f"/proc/{pid}/fd/{fd}"))
    return out


def proc_stat(pid: int | str) -> tuple[int, str] | None:
    """The ppid and the start time (field 22 of `/proc/N/stat`), or None for a process gone."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return None
    rest = text[text.rindex(")") + 2 :].split(" ")
    return int(rest[1]), rest[19]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _from_proc() -> list[Proc]:
    table: list[Proc] = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            stat = proc_stat(name)
            if stat:
                cwd = os.readlink(f"/proc/{name}/cwd")
                table.append(Proc(int(name), stat[0], cwd, (cwd, *_links(name))))
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue  # another user's process, or one that exited mid-walk: neither holds ours
    return table


def _from_lsof() -> list[Proc]:
    try:
        done = subprocess.run(
            ["lsof", "-d", "cwd", "-Fpn"], capture_output=True, text=True, check=False
        )
    except OSError as error:
        print(f"branch-audit: lsof failed ({error}); no process is seen", file=sys.stderr)
        return []
    table: list[Proc] = []
    pid = 0
    for line in done.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n"):
            table.append(Proc(pid, 0, line[1:], (line[1:],)))
    return table


class Processes:
    """The process table, read once per audit pass."""

    def __init__(self, opt: Options) -> None:
        self.opt = opt
        self._table: list[Proc] | None = None

    def forget(self) -> None:
        self._table = None

    def table(self) -> list[Proc]:
        if self._table is None:
            self._table = _from_proc() if has_proc() else _from_lsof()
        return self._table

    def ancestors(self, children: bool) -> set[int]:
        """This process and every ancestor. In the caller's own worktree, every child of one."""
        mine = {os.getpid(), os.getppid()}
        pid = os.getppid()
        while has_proc() and pid > 1:
            stat = proc_stat(pid)
            if not stat:
                break
            mine.add(stat[0])
            pid = stat[0]
        if children:
            mine |= {q.pid for q in self.table() if q.ppid in mine}
        return mine

    def family(self, pid: int) -> set[int]:
        out = {pid}
        grew = True
        while grew:
            grew = False
            for q in self.table():
                if q.ppid in out and q.pid not in out:
                    out.add(q.pid)
                    grew = True
        return out

    def holders(self, path: str, skip: set[int]) -> list[Proc]:
        return [
            q for q in self.table() if q.pid not in skip and any(inside(x, path) for x in q.paths)
        ]

    def transcript_folder(self, path: str) -> str:
        """Claude Code's folder name: one `-` per UTF-16 code unit outside A-Z, a-z and 0-9."""
        name = re.sub(r"[^A-Za-z0-9]", lambda m: "-" * (2 if ord(m[0]) > 0xFFFF else 1), path)
        return os.path.join(self.opt.projects_root, name)

    def session_written(self, path: str) -> float:
        """The newest write of the transcript folder of a worktree, in ms, or 0 with none."""
        return newest_write(self.transcript_folder(path))

    def session_idle(self, worktree: str, pid: int) -> float | None:
        folders = [self.transcript_folder(worktree)]
        owner = next((q for q in self.table() if q.pid == pid), None)
        if owner:
            folders.append(self.transcript_folder(owner.cwd))
        newest = max((newest_write(f) for f in folders), default=0)
        return (now_ms() - newest) / HOUR_MS if newest else None

    def lock_of(self, entry: Entry) -> Lock:
        """The reason carries `pid N start T`. A live pid with another start time is reused."""
        if entry.locked is None:
            return Lock("none")
        found = re.search(r"pid (\d+) start (\d+)", entry.locked)
        if not found:
            return Lock("live")
        pid = int(found[1])
        stat = proc_stat(pid) if has_proc() else None
        live = alive(pid) and (not has_proc() or (stat is not None and stat[1] == found[2]))
        if not live:
            return Lock("dead", pid)
        idle = self.session_idle(entry.path, pid)
        stale = idle is not None and idle > self.opt.session_idle_days * 24
        return Lock(
            "stale_session" if stale else "live", pid, None if idle is None else round1(idle)
        )


def newest_write(folder: str) -> float:
    """The newest mtime under `folder`, in ms, or 0 for a folder that is not there."""
    newest = 0.0
    for top, _, names in os.walk(folder):
        for name in names:
            with contextlib.suppress(OSError):
                newest = max(newest, os.lstat(os.path.join(top, name)).st_mtime_ns / 1e6)
    return newest
