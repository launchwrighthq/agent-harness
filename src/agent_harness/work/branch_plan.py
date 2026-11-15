"""The prune plan of `ah branch-audit`, and `--apply`, which runs the rows the user names.

Risk `none` is what git proves. `low` is a `-D` only the content check proves, or the unlock of a
dead lock. `medium` is a `--force` over strays or a worktree that holds copies. `high` is a remote
branch, a stray folder or data: printed for the user, never run.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

from agent_harness import HarnessError
from agent_harness.work.branch_model import (
    KINDS,
    RISK_ORDER,
    VERDICT,
    Op,
    Options,
    RefAudit,
    Report,
    WorktreeAudit,
    megabytes,
)


def ref_risk(ref: RefAudit) -> str:
    return "none" if ref.contained else "low"


def worktree_risk(w: WorktreeAudit) -> str:
    risk = ref_risk(w.ref)
    if w.stray or w.copies:
        risk = "medium"
    if w.lock.state == "dead" and RISK_ORDER.index(risk) < RISK_ORDER.index("low"):
        risk = "low"
    return risk


def offered(w: WorktreeAudit) -> bool:
    return (
        VERDICT[w.verdict].offered
        and not w.caller
        and not w.protected
        and w.ref.agrees is not False
    )


def _ref_offered(b: RefAudit) -> bool:
    return VERDICT[b.verdict].offered and not b.protected and b.agrees is not False


def _worktree_ops(data: Report) -> tuple[list[Op], set[str], dict[str, str]]:
    ops: list[Op] = []
    removed = {w.branch for w in data.prunable if w.branch}
    remove_of: dict[str, str] = {}
    for w in (x for x in data.worktrees if offered(x)):
        path = w.entry.path
        if w.lock.state == "dead":
            unlock = ["git", "worktree", "unlock", path]
            ops.append(Op(f"unlock:{w.name}", "worktree_unlock", w.name, unlock, "low"))
        force = ["--force"] if w.stray or any(c.code for c in w.copies) else []
        argv = ["git", "worktree", "remove", *force, path]
        op = Op(f"remove:{w.name}", "worktree_remove", w.name, argv, worktree_risk(w))
        op.mb, op.row = megabytes(w.size), w
        ops.append(op)
        if w.entry.branch:
            removed.add(w.entry.branch)
            remove_of[w.entry.branch] = op.id
    return ops, removed, remove_of


def _branch_ops(data: Report, removed: set[str], remove_of: dict[str, str]) -> list[Op]:
    """`-d` where git sees the ancestry, `-D` where only the content check can. A branch that a
    worktree holds waits on that worktree's removal."""
    ops: list[Op] = []
    for b in data.locals:
        if not _ref_offered(b) or (b.holder and b.ref not in removed):
            continue
        argv = ["git", "branch", "-d" if b.contained else "-D", b.ref]
        ops.append(
            Op(
                f"branch:{b.ref}",
                "branch_delete",
                b.ref,
                argv,
                ref_risk(b),
                after=remove_of.get(b.ref),
                row=b,
            )
        )
    for b in data.remotes:
        if not _ref_offered(b) or "/" not in b.ref:
            continue  # `refs/remotes/stray` has no `<remote>/` to push to
        remote, name = b.ref.split("/", 1)
        argv = ["git", "push", remote, "--delete", name]
        ops.append(Op(f"remote:{b.ref}", "remote_delete", b.ref, argv, "high", runnable=False))
    return ops


def _printed_ops(data: Report) -> list[Op]:
    ops = [
        Op(
            f"stray:{s.name}",
            "stray_remove",
            s.name,
            ["rm", "-rf", s.path],
            "high",
            megabytes(s.size),
            False,
        )
        for s in data.strays
        if s.offered
    ]
    for w in (x for x in data.worktrees if not offered(x)):
        for c in w.copies:
            argv = ["rm", "-rf", os.path.join(w.entry.path, c.path.removesuffix("/"))]
            subject = f"{w.name}/{c.path}"
            ops.append(
                Op(
                    f"data:{subject}",
                    "data_remove",
                    subject,
                    argv,
                    "high",
                    megabytes(c.size),
                    False,
                )
            )
    return ops


def operations(data: Report) -> list[Op]:
    """One operation per row, in the order they must run, then by megabytes freed."""
    ops: list[Op] = []
    if data.prunable:
        subject = ", ".join(os.path.basename(w.path) for w in data.prunable)
        ops.append(Op("prune", "worktree_prune", subject, ["git", "worktree", "prune"], "none"))
    removals, removed, remove_of = _worktree_ops(data)
    ops += removals + _branch_ops(data, removed, remove_of) + _printed_ops(data)
    return sorted(ops, key=lambda o: (KINDS.index(o.kind), -o.mb, o.id))


def chosen(ops: list[Op], opt: Options, say: Callable[[str], None]) -> list[Op] | None:
    """The rows `--apply` runs, or None when `--only` names an id the plan lacks."""
    cap = RISK_ORDER.index("medium")
    if not opt.only:
        return [o for o in ops if o.runnable and o.risk == "none"]
    unknown = [i for i in opt.only if not any(o.id == i for o in ops)]
    if unknown:
        say(f"    x not in the plan, so nothing ran: {', '.join(unknown)}")
        return None
    for i in opt.only:
        op = next(o for o in ops if o.id == i)
        if not op.runnable or RISK_ORDER.index(op.risk) > cap:
            say(f"    x {i}: printed only, never run (risk:{op.risk})")
    return [o for o in ops if o.id in opt.only and o.runnable and RISK_ORDER.index(o.risk) <= cap]


def apply(
    ops: list[Op],
    opt: Options,
    repo: str,
    unchanged: Callable[[Op], bool],
    say: Callable[[str], None],
    before_apply: Callable[[str], None] | None = None,
) -> bool:
    """Run the chosen git operations in plan order, each audited again just before it runs.
    False when one failed, changed since the audit, or `--only` named a stranger.
    `before_apply` gets the main checkout after the audit, before the first operation."""
    picked = chosen(ops, opt, say)
    if picked is None:
        return False
    if not picked:
        say("    --apply had nothing to run.")
        return True
    if before_apply is not None:
        before_apply(repo)
    fine = True
    failed: set[str] = set()
    ids = {o.id for o in picked}
    for op in picked:
        if op.argv[0] != "git":
            raise HarnessError(f"refusing to run {' '.join(op.argv)}")
        label = " ".join(op.argv)
        if op.after and (op.after in failed or op.after not in ids):
            holder = op.row.holder if isinstance(op.row, RefAudit) else None
            if holder and os.path.exists(holder.path):
                say(f"    - {label}: skipped, its worktree is still there")
                continue
        if not unchanged(op):
            say(f"    x {op.id}: changed since the audit, so it did not run; audit again")
            failed.add(op.id)
            fine = False
            continue
        done = subprocess.run(op.argv, cwd=repo, capture_output=True, text=True, check=False)
        if done.returncode == 0:
            say(f"    ok {label}")
        else:
            say(f"    x {label}: {done.stderr.strip()}")
            failed.add(op.id)
            fine = False
    return fine
