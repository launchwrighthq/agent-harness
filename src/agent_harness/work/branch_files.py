"""What a worktree holds outside its commits, each file judged by its bytes.

A credential-shaped file is never opened, and a symbolic link is hashed as the link, so its
target is never opened either.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field

from agent_harness.work.branch_git import Git, fields, lines
from agent_harness.work.branch_model import Loose, Options

_CREDENTIAL = re.compile(
    r"^(\.env.*|.+\.(key|pem|p12|pfx|secret)|id_(rsa|dsa|ecdsa|ed25519).*|_?netrc|\.netrc"
    r"|\.git-credentials)$"
)


# The certificate bundle that a package ships: public certificates and no key, so it is cache.
PUBLIC_BUNDLE = "cacert.pem"


def is_credential(path: str) -> bool:
    """The family the read guards refuse, an `.example` and the public bundle aside."""
    name = os.path.basename(path)
    if name.endswith(".example") or name == PUBLIC_BUNDLE:
        return False
    shaped = re.search(r"(^|/)(sessions/[^/]+\.json|\.aws/credentials)$", path)
    return bool(_CREDENTIAL.match(name) or shaped)


def file_size(path: str) -> int:
    try:
        return os.lstat(path).st_size
    except FileNotFoundError:
        return 0


def mtime_ms(path: str) -> float:
    return os.lstat(path).st_mtime_ns / 1e6


def _file_hash(path: str) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Walk:
    size: int = 0
    newest: float = 0
    files: list[tuple[str, int]] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def walk(root: str) -> Walk:
    """Every file under `root`, never following a symlink. An unreadable folder is listed."""
    out = Walk()
    stack = [""]
    while stack:
        rel = stack.pop()
        try:
            entries = list(os.scandir(os.path.join(root, rel)))
        except PermissionError:
            out.unreadable.append(rel)
            continue
        for entry in entries:
            path = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir(follow_symlinks=False):
                stack.append(path)
                continue
            st = os.lstat(os.path.join(root, path))
            out.size += st.st_size
            out.newest = max(out.newest, st.st_mtime_ns / 1e6)
            if entry.is_file(follow_symlinks=False):
                out.files.append((path, st.st_size))
    return out


def disk_blob(cwd: str, full: str) -> str | None:
    """The blob git would store for a file on disk, or None for what it would not read.

    A symbolic link is hashed as its own target text, as git commits it: a link to a named pipe
    blocked `hash-object <path>`.
    """
    try:
        st = os.lstat(full)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        done = subprocess.run(
            ["git", "hash-object", "--stdin"],
            cwd=cwd,
            input=os.fsencode(os.readlink(full)),
            capture_output=True,
            check=False,
        )
        return done.stdout.decode().strip() if done.returncode == 0 else None
    if not stat.S_ISREG(st.st_mode):
        return None
    return Git(cwd).soft(["hash-object", "--", full], cwd=cwd)


class Files:
    """The loose files of the worktrees of the repository whose main checkout is `g.repo`."""

    def __init__(self, g: Git, opt: Options) -> None:
        self.g = g
        self.opt = opt

    def links_to_main(self, full: str, rel: str) -> bool:
        """A link to the main checkout's own entry at the same path holds no bytes of its own."""
        if not os.path.islink(full):
            return False
        target = os.path.normpath(os.path.join(os.path.dirname(full), os.readlink(full)))
        return target == os.path.join(self.g.repo, rel) and os.path.lexists(target)

    def is_copy(self, worktree: str, rel: str, size: int) -> bool:
        theirs = os.path.join(self.g.repo, rel)
        if os.path.realpath(theirs) != theirs:
            return False  # a symlinked folder on the way can lead back into the worktree itself
        try:
            st = os.lstat(theirs)
        except (FileNotFoundError, NotADirectoryError):
            return False
        return (
            stat.S_ISREG(st.st_mode)
            and st.st_size == size
            and _file_hash(theirs) == _file_hash(os.path.join(worktree, rel))
        )

    def _untracked(self, path: str, rel: str, code: str) -> Loose:
        """A stray holds bytes the base's history holds at that path. Anything else is unsaved:
        a copy only the worktree's own branch holds dies with the `-D` of the same plan."""
        full = os.path.join(path, rel)
        if is_credential(rel):
            return Loose(rel, "credential", file_size(full), code)
        if self.links_to_main(full, rel):
            return Loose(rel, "copy", code=code)
        base = self.opt.base
        blob = disk_blob(path, full)
        known = blob is not None and blob in self.g.history_blobs([base], rel)
        loose = Loose(rel, "stray" if known else "unsaved", code=code)
        if known:
            log = lines(self.g.soft(["log", base, "--format=%H", "--", rel]))
            at = next((c for c in log if self.g.blob_at(c, rel) == blob), None)
            loose.at = self.g.out(["rev-parse", "--short", at]) if at else None
        elif blob is not None:
            committed = self.g.soft(["log", "-1", "--format=%ct", base, "--", rel])
            if int(committed or 0) * 1000 > mtime_ms(full):
                loose.hint = "base_newer"
        return loose

    def dirt(self, path: str, entries: list[tuple[str, str]]) -> list[Loose]:
        """Every status entry: a tracked change, or an untracked file that is a stray or unsaved."""
        return [
            Loose(rel, "tracked_change", code=code)
            if code[0] != "?"
            else self._untracked(path, rel, code)
            for code, rel in entries
        ]

    def hidden_edits(self, path: str) -> list[Loose]:
        """A tracked file flagged assume-unchanged or skip-worktree whose bytes left the index."""
        listed = fields(self.g.soft(["ls-files", "-v", "-z"], cwd=path, raw=True))
        flagged = [e[2:] for e in listed if e[0].islower() or e[0] == "S"]
        if not flagged:
            return []
        staged = {}
        for entry in fields(self.g.out(["ls-files", "-s", "-z"], cwd=path, raw=True)):
            meta, rel = entry.split("\t", 1)
            staged[rel] = meta.split(" ")[1]
        out = []
        for rel in flagged:
            full = os.path.join(path, rel)
            if not os.path.lexists(full):
                continue  # a sparse checkout's skip-worktree file is absent; the index holds it
            if is_credential(rel) or disk_blob(path, full) != staged.get(rel):
                out.append(Loose(rel, "tracked_change", code="h "))
        return out

    def _judge(self, worktree: str, rel: str, size: int) -> Loose:
        if is_credential(rel):
            return Loose(rel, "credential", size)
        if os.path.basename(rel) == PUBLIC_BUNDLE:
            return Loose(rel, "cache", size)
        return Loose(rel, "copy" if self.is_copy(worktree, rel, size) else "data", size)

    def _ignored_entry(self, worktree: str, entry: str) -> list[Loose]:
        full = os.path.join(worktree, entry)
        is_dir = entry.endswith("/")
        rel = entry[:-1] if is_dir else entry
        walked = walk(full) if is_dir else None
        if walked is not None:
            files = [(f"{rel}/{r}", n) for r, n in walked.files]
        else:
            st = os.lstat(full)
            if not stat.S_ISREG(st.st_mode):
                return []
            files = [(rel, st.st_size)]
        creds = [Loose(r, "credential", n) for r, n in files if is_credential(r)]
        if any(seg in self.opt.rebuildable for seg in rel.split("/")) and (is_dir or not creds):
            size = walked.size if walked is not None else files[0][1]
            return [Loose(entry, "cache", size), *creds]
        judged = [self._judge(worktree, r, n) for r, n in files]
        unread = walked.unreadable if walked is not None else []
        judged += [Loose(f"{rel}/{u}/" if u else entry, "data") for u in unread]
        if is_dir and len({j.state for j in judged}) == 1:
            return [Loose(entry, judged[0].state, sum(j.size for j in judged))]
        return judged

    def ignored(self, worktree: str) -> list[Loose]:
        """Every ignored file as cache, copy, data or credential. A folder whose files agree is
        one row."""
        args = ["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"]
        listed = self.g.soft(args, cwd=worktree, raw=True)
        if listed is None:
            return [Loose("./", "data")]  # git could not list them: nothing proves them rebuildable
        return [loose for entry in fields(listed) for loose in self._ignored_entry(worktree, entry)]
