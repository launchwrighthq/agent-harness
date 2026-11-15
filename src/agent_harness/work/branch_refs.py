"""Judge one ref against the base: ancestry, patch id, content blob against blob, subject.

A second route classifies the tip diff from the merge base. A ref whose two routes disagree gets
no row in any plan.
"""

from __future__ import annotations

import os
import re

from agent_harness.work.branch_git import Git, changed_lines, fields, lines
from agent_harness.work.branch_model import Candidate, FileState, Options, RefAudit


def is_protected(opt: Options, ref: str) -> bool:
    return ref in opt.protect or re.sub(r"^[^/]+/", "", ref, count=1) in opt.protect


def _spec_root(opt: Options, path: str) -> str | None:
    return next(
        (
            root
            for root in opt.spec_roots
            if path.startswith(root) and not path.startswith(f"{root}{opt.archive}/")
        ),
        None,
    )


def _obsolete_draft(g: Git, opt: Options, ref: str, base: str, path: str) -> bool:
    """A spec draft the base carries in the archive, differing only in its `Status:` line."""
    if _spec_root(opt, path) is None:
        return False
    archived = f"{os.path.dirname(path)}/{opt.archive}/{os.path.basename(path)}"
    twin = g.soft(["diff", "--no-color", f"{base}:{archived}", f"{ref}:{path}"])
    if twin is None:
        return False
    return not [line for line in changed_lines(twin) if not re.match(r"^[-+]Status:", line)]


def classify_file(g: Git, opt: Options, ref: str, base: str, path: str) -> FileState:
    """One path at the tip of the ref against the base."""
    mine = g.blob_at(ref, path)
    if mine is None:
        return FileState(path, "gone")
    theirs = g.blob_at(base, path)
    if theirs == mine:
        return FileState(path, "identical")
    if theirs is None:
        if _obsolete_draft(g, opt, ref, base, path):
            note = "the base carries it archived, differing only in Status:"
            return FileState(path, "obsolete-draft", note)
        return FileState(path, "new")
    return FileState(path, "superseded" if mine in g.history_blobs([base], path) else "new")


def classify_paths(g: Git, opt: Options, ref: str, base: str, paths: list[str]) -> list[FileState]:
    g.prefetch(ref, paths)
    g.prefetch(base, paths)
    return [classify_file(g, opt, ref, base, p) for p in paths]


def classify_commit(g: Git, opt: Options, ref: str, base: str, sha: str) -> Candidate:
    changed = fields(g.soft(["diff-tree", "--no-commit-id", "--name-only", "-z", "-r", sha]))
    files = classify_paths(g, opt, ref, base, changed)
    subject = g.out(["log", "-1", "--format=%s", sha])
    return Candidate(sha, subject, files, any(f.state == "new" for f in files))


def second_opinion(g: Git, opt: Options, ref: str, base: str) -> tuple[list[str], int]:
    """The tip diff from the merge base, per path: a file the commit walk skipped is read here."""
    merge_base = g.soft(["merge-base", base, ref])
    if not merge_base:
        return [], 0
    paths = fields(g.soft(["diff", "--name-only", "-z", merge_base, ref]))
    held = [f.path for f in classify_paths(g, opt, ref, base, paths) if f.state == "new"]
    return held, len(paths)


def audit_ref(g: Git, opt: Options, ref: str, base: str) -> RefAudit:
    out = RefAudit(
        ref=ref,
        base=base,
        tip=g.out(["rev-parse", "--verify", f"{ref}^{{commit}}"]),
        when=g.out(["log", "-1", "--format=%cI", ref]),
        ahead=int(g.out(["rev-list", "--count", f"{base}..{ref}"])),
        behind=int(g.out(["rev-list", "--count", f"{ref}..{base}"])),
        protected=is_protected(opt, ref),
        contained=g.ok(["merge-base", "--is-ancestor", ref, base]),
    )
    if out.contained:
        if opt.verify:
            out.second_held, out.agrees = [], True
        return out
    cherry = lines(g.soft(["cherry", base, ref]))
    out.duplicates = sum(1 for line in cherry if line.startswith("-"))
    for sha in (line[2:] for line in cherry if line.startswith("+")):
        commit = classify_commit(g, opt, ref, base, sha)
        if commit.is_new:
            grep = g.soft(["log", base, "--format=%s", "-F", "--grep", commit.subject])
            commit.subject_in_base = bool(grep)
        out.candidates.append(commit)
    new = (f.path for c in out.candidates for f in c.files if f.state == "new")
    out.new_files = list(dict.fromkeys(new))
    out.verdict = "unmerged" if out.new_files else "absorbed"
    if opt.verify:
        held, checked = second_opinion(g, opt, ref, base)
        out.second_held, out.second_checked = held, checked
        out.agrees = bool(held) == bool(out.new_files)
    return out
