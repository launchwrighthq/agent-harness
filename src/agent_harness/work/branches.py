"""`ah branch-audit`: what every worktree, local branch and remote branch holds that the base lacks.

    ah branch-audit [--summary | --json | --prune] [--apply [--only <id>,...]] [--fetch]

A ref is judged by ancestry, patch id, content and subject (`branch_refs`). A worktree adds its
loose files (`branch_files`) and its holders (`branch_procs`). The plan and `--apply` are in
`branch_plan`. The repository facts come from the `[branches]` section of the config.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from agent_harness import HarnessError, config
from agent_harness.spec import layout
from agent_harness.work import branch_plan, branch_refs
from agent_harness.work.branch_files import Files, file_size, mtime_ms, walk
from agent_harness.work.branch_git import Git, lines, reflog_time, status_entries
from agent_harness.work.branch_model import (
    HOUR_MS,
    Entry,
    Loose,
    Op,
    Options,
    RefAudit,
    Report,
    Stray,
    WorktreeAudit,
)
from agent_harness.work.branch_procs import Processes, inside, now_ms
from agent_harness.work.branch_report import Human, to_json

PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ah branch-audit", description="Audit every worktree, branch and stray folder."
    )
    add = parser.add_argument
    add("--base", help="the base branch, default harness.baseBranch")
    add("--remote-base", help="the remote base, default branches.remoteBase or origin/<base>")
    add("--protect", help="refs never offered, comma separated, over branches.protect")
    add("--stale-hours", type=float, help="the age gate, default branches.staleHours")
    add("--session-idle-days", type=float, help="default branches.sessionIdleDays")
    add("--projects-root", type=Path, default=PROJECTS_ROOT, help="where transcripts live")
    add("--json", action="store_true", help="print the report as data")
    add("--summary", action="store_true", help="one line per verdict")
    add("--prune", action="store_true", help="print the plan under the report")
    add("--apply", action="store_true", help="run risk none, or the rows --only names")
    add("--only", default="", help="plan ids for --apply, comma separated")
    add("--fetch", action="store_true", help="git fetch --all --prune first")
    add("--no-verify", action="store_true", help="skip the second route")
    add("--no-color", action="store_true")
    add("--max-commits", type=int, default=6)
    add("--max-files", type=int, default=6)
    return parser


def options(argv: list[str], repo: str) -> Options:
    """The command line over the config of the main checkout."""
    args = _parser().parse_args(argv)
    cfg = config.load(Path(repo))
    spec_layout = layout.from_config(cfg)
    base = args.base or str(cfg.get("harness.baseBranch"))
    protect = [x for x in (args.protect or "").split(",") if x]
    return Options(
        base=base,
        remote_base=args.remote_base or str(cfg.get("branches.remoteBase")) or f"origin/{base}",
        protect=protect if args.protect is not None else list(cfg.get("branches.protect")),
        spec_roots=[f"{folder}/" for folder in spec_layout.dirs],
        archive=spec_layout.archive,
        worktree_root=os.path.normpath(os.path.join(repo, str(cfg.get("branches.worktreeRoot")))),
        rebuildable=list(cfg.get("branches.rebuildable")),
        land=str(cfg.get("harness.landCommand")),
        stale_hours=_number(args.stale_hours, cfg.get("branches.staleHours")),
        session_idle_days=_number(args.session_idle_days, cfg.get("branches.sessionIdleDays")),
        projects_root=str(args.projects_root),
        json=args.json,
        summary=args.summary,
        prune=args.prune or args.apply,
        apply=args.apply,
        only=[x for x in args.only.split(",") if x],
        fetch=args.fetch,
        verify=not args.no_verify,
        max_commits=args.max_commits,
        max_files=args.max_files,
        color=not args.no_color and not os.environ.get("NO_COLOR") and sys.stdout.isatty(),
    )


def _number(flag: float | None, configured: object) -> float:
    return flag if flag is not None else float(str(configured))


def _verdict(w: WorktreeAudit, unsaved: list[Loose], data: list[Loose], stale_hours: float) -> str:
    if w.holders or w.lock.state == "live":
        return "in_use"
    if w.lock.state == "stale_session":
        return "stale_session"
    if unsaved:
        return "uncommitted"
    if data:
        return "holds_data"
    if w.ref.verdict == "unmerged":
        return "unmerged"
    if w.idle_hours < stale_hours:
        return "in_progress"
    if w.stray or w.copies:
        return "stalled"
    return w.ref.verdict


class Audit:
    def __init__(self, cwd: str, common: str, argv: list[str]) -> None:
        self.cwd = cwd
        self.common = common
        self.repo = os.path.dirname(common) if os.path.basename(common) == ".git" else common
        self.opt = options(argv, self.repo)
        self.g = Git(self.repo)
        self.files = Files(self.g, self.opt)
        self.procs = Processes(self.opt)

    def branch_reflog(self, ref: str) -> float:
        return reflog_time(os.path.join(self.common, "logs", "refs", "heads", *ref.split("/")))

    def _loose(self, e: Entry) -> tuple[list[Loose], list[Loose]]:
        status = self.g.soft(["status", "--porcelain", "-z", "-uall"], cwd=e.path, raw=True)
        if status is None:
            return [Loose("./", "unsaved")], []  # git cannot say what it holds, so it holds work
        dirt = self.files.dirt(e.path, status_entries(status)) + self.files.hidden_edits(e.path)
        return dirt, self.files.ignored(e.path)

    def _touched(self, e: Entry, ref: RefAudit, dirt: list[Loose]) -> float:
        """The newest of the tip commit, the reflogs, the loose files and the session transcript."""
        git_dir = self.g.soft(["rev-parse", "--absolute-git-dir"], cwd=e.path)
        touched = [
            datetime.fromisoformat(ref.when).timestamp() * 1000,
            self.branch_reflog(e.branch) if e.branch else 0,
            reflog_time(os.path.join(git_dir, "logs", "HEAD")) if git_dir else 0,
            self.procs.session_written(e.path),
        ]
        for d in dirt:
            full = os.path.join(e.path, d.path)
            touched.append(mtime_ms(full) if os.path.lexists(full) else 0)
        return max([*touched, 0])

    def audit_worktree(self, e: Entry) -> WorktreeAudit:
        dirt, ignored = self._loose(e)
        ref = branch_refs.audit_ref(self.g, self.opt, e.branch or e.head or "HEAD", self.opt.base)
        lock = self.procs.lock_of(e)
        caller = inside(self.cwd, e.path)
        # The caller's own shell and its pipeline work in the caller's worktree. In any other,
        # a parent of this process holds it.
        skip = self.procs.ancestors(children=True) if caller else {os.getpid()}
        if lock.state == "stale_session" and lock.pid is not None:
            skip |= self.procs.family(lock.pid)
        for d in dirt:
            if d.state != "credential":
                d.size = file_size(os.path.join(e.path, d.path))
        w = WorktreeAudit(
            entry=e,
            name=os.path.basename(e.path),
            ref=ref,
            stray=[d for d in dirt if d.state == "stray"],
            copies=[d for d in dirt + ignored if d.state == "copy"],
            files=sorted(dirt + ignored, key=lambda f: f.path),
            holders=self.procs.holders(e.path, skip),
            lock=lock,
            idle_hours=(now_ms() - self._touched(e, ref, dirt)) / HOUR_MS,
            size=walk(e.path).size,
            caller=caller,
            verdict="merged",
            protected=branch_refs.is_protected(self.opt, e.branch) if e.branch else False,
        )
        unsaved = [d for d in dirt if d.state in ("unsaved", "tracked_change")]
        data = [d for d in dirt + ignored if d.state in ("data", "credential")]
        w.verdict = _verdict(w, unsaved, data, self.opt.stale_hours)
        return w

    def worktrees(self) -> list[Entry]:
        """`git worktree list --porcelain`, the main checkout first."""
        out: list[Entry] = []
        for line in lines(self.g.out(["worktree", "list", "--porcelain"])):
            if line.startswith("worktree "):
                path = line[9:]
                out.append(Entry(os.path.realpath(path) if os.path.exists(path) else path))
            elif line.startswith("HEAD "):
                out[-1].head = line[5:]
            elif line.startswith("branch "):
                out[-1].branch = line[7:].replace("refs/heads/", "", 1)
            elif line == "locked" or line.startswith("locked "):
                out[-1].locked = line[7:]
            elif line == "prunable" or line.startswith("prunable "):
                out[-1].prunable = True
        return out

    def stray_folders(self, known: set[str]) -> list[Stray]:
        """A folder under the worktree root that git does not list. One touched inside the stale
        window, or one a process works in, is not offered."""
        root = self.opt.worktree_root
        if not os.path.exists(root):
            return []
        out: list[Stray] = []
        for d in os.scandir(root):
            path = os.path.join(root, d.name)
            if not d.is_dir(follow_symlinks=False) or os.path.realpath(path) in known:
                continue
            st = os.lstat(path)
            walked = walk(path)
            try:
                owner = pwd.getpwuid(st.st_uid).pw_name
            except KeyError:
                owner = str(st.st_uid)
            idle = (now_ms() - (walked.newest or st.st_mtime_ns / 1e6)) / HOUR_MS
            held = bool(self.procs.holders(path, {os.getpid()}))
            offered = idle >= self.opt.stale_hours and not held
            out.append(
                Stray(d.name, path, walked.size, owner, idle, len(walked.unreadable), offered)
            )
        return sorted(out, key=lambda s: s.path)

    def local_branches(self, every: list[Entry]) -> list[RefAudit]:
        base = self.opt.base
        holder = {w.branch: w for w in every if w.branch}
        out: list[RefAudit] = []
        for name in lines(
            self.g.out(["for-each-ref", "--format=%(refname:lstrip=2)", "refs/heads/"])
        ):
            if name == base:
                continue
            local = branch_refs.audit_ref(self.g, self.opt, name, base)
            moved = max(
                self.branch_reflog(name), datetime.fromisoformat(local.when).timestamp() * 1000
            )
            if local.verdict != "unmerged" and (now_ms() - moved) / HOUR_MS < self.opt.stale_hours:
                local.verdict = "in_progress"
            local.holder = holder.get(name)
            local.worktree = os.path.basename(local.holder.path) if local.holder else None
            out.append(local)
        return out

    def remote_branches(self) -> list[RefAudit]:
        remote_base = self.opt.remote_base
        names = lines(self.g.out(["for-each-ref", "--format=%(refname:lstrip=2)", "refs/remotes/"]))
        return [
            branch_refs.audit_ref(self.g, self.opt, name, remote_base)
            for name in names
            if name != remote_base
            and not name.endswith("/HEAD")
            and self.g.ok(["rev-parse", "--verify", "--quiet", name])
        ]

    def run(self) -> Report:
        if self.opt.fetch:
            self.g.soft(["fetch", "--all", "--prune"])
        base, remote_base = self.opt.base, self.opt.remote_base
        every = self.worktrees()
        linked = every[1:]
        prunable = [w for w in linked if w.prunable and w.locked is None]
        audited = (self.audit_worktree(w) for w in linked if not w.prunable)
        trees = sorted(audited, key=lambda w: w.name)
        has_remote = self.g.ok(["rev-parse", "--verify", "--quiet", remote_base])
        ahead = (
            int(self.g.out(["rev-list", "--count", f"{remote_base}..{base}"]))
            if has_remote
            else None
        )
        behind = (
            int(self.g.out(["rev-list", "--count", f"{base}..{remote_base}"]))
            if has_remote
            else None
        )
        # Read again: an audit takes minutes, and a worktree a peer added meanwhile is no stray.
        known = {
            os.path.realpath(w.path) for w in [*every, *self.worktrees()] if os.path.exists(w.path)
        }
        data = Report(
            base,
            remote_base,
            ahead,
            behind,
            trees,
            self.local_branches(every),
            self.remote_branches() if has_remote else [],
            self.stray_folders(known),
            prunable,
        )
        data.plan = branch_plan.operations(data)
        risk_of = {id(o.row): o.risk for o in data.plan if o.row is not None}
        for tree in data.worktrees:
            tree.risk = risk_of.get(id(tree), "high")
        for local in data.locals:
            local.risk = risk_of.get(id(local), "high")
        return data

    def unchanged(self, op: Op) -> bool:
        """The row audited again just before it runs: a peer may have written to it meanwhile."""
        self.g.forget()
        self.procs.forget()
        if isinstance(op.row, RefAudit):
            tip = self.g.soft(["rev-parse", "--verify", "--quiet", f"refs/heads/{op.row.ref}"])
            return tip == op.row.tip
        if isinstance(op.row, WorktreeAudit):
            return _shape(self.audit_worktree(op.row.entry)) == _shape(op.row)
        return True


def _shape(w: WorktreeAudit) -> tuple[object, ...]:
    """What a removal was approved over. A dead lock the plan unlocked first reads as none."""
    lock = "none" if w.lock.state == "dead" else w.lock.state
    return (w.verdict, w.ref.tip, lock, [(f.path, f.state) for f in w.files], len(w.holders))


def main(
    argv: list[str],
    before_apply: Callable[[str], None] | None = None,
) -> int:
    if "--risk" in argv:
        print(
            "branch-audit: --apply runs risk none, and --apply --only <id,...> names each row",
            file=sys.stderr,
        )
        return 1
    cwd = os.path.realpath(os.getcwd())
    common = Git(cwd).soft(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd)
    if common is None:
        raise HarnessError("not a git repository")
    audit = Audit(cwd, os.path.realpath(common), argv)
    opt = audit.opt
    if not audit.g.ok(["rev-parse", "--verify", "--quiet", opt.base]):
        raise HarnessError(f"no such base ref: {opt.base}")
    data = audit.run()
    human = Human(audit.repo, opt, data)
    if opt.json:
        print(json.dumps(to_json(data), indent=2, ensure_ascii=False))
    elif opt.summary:
        human.summary()
        print("")
    else:
        human.report()
        if opt.prune:
            human.plan()
        print("")
    if not opt.apply:
        return 0

    def say(s: str) -> None:
        print(s, file=sys.stderr if opt.json else sys.stdout)

    done = branch_plan.apply(data.plan, opt, audit.repo, audit.unchanged, say, before_apply)
    return 0 if done else 1
