"""The two faces of a branch audit: `--json` data and the report a person reads."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from agent_harness.work.branch_model import (
    RISK_ORDER,
    RISK_PAINT,
    VERDICT,
    Loose,
    Options,
    RefAudit,
    Report,
    WorktreeAudit,
    age,
    mb_text,
    megabytes,
    number,
    round1,
)


def _loose(f: Loose) -> dict[str, Any]:
    row: dict[str, Any] = {"path": f.path, "state": f.state, "mb": number(megabytes(f.size))}
    if f.hint:
        row["hint"] = f.hint
    return row


def _ref(b: RefAudit) -> dict[str, Any]:
    return {
        "ref": b.ref,
        "verdict": b.verdict,
        "risk": b.risk,
        "worktree": b.worktree,
        "protected": b.protected,
        "agrees": b.agrees,
    }


def _worktree(w: WorktreeAudit) -> dict[str, Any]:
    idle = w.lock.session_idle_hours
    return {
        "name": w.name,
        "path": w.entry.path,
        "branch": w.entry.branch,
        "head": w.entry.head,
        "verdict": w.verdict,
        "risk": w.risk,
        "mb": number(megabytes(w.size)),
        "idle_hours": number(round1(w.idle_hours)),
        "lock": w.lock.state,
        "session_idle_hours": None if idle is None else number(idle),
        "caller": w.caller,
        "files": [_loose(f) for f in w.files],
    }


def to_json(data: Report) -> dict[str, Any]:
    base = {
        "name": data.base,
        "remote": data.remote_base,
        "ahead": data.ahead,
        "behind": data.behind,
    }
    strays = [
        {
            "path": s.path,
            "mb": number(megabytes(s.size)),
            "owner": s.owner,
            "idle_hours": number(round1(s.idle_hours)),
        }
        for s in data.strays
    ]
    plan = [
        {
            "id": o.id,
            "kind": o.kind,
            "subject": o.subject,
            "argv": o.argv,
            "risk": o.risk,
            "mb": number(o.mb),
            "runnable": o.runnable,
        }
        for o in data.plan
    ]
    return {
        "schema": 1,
        "base": base,
        "worktrees": [_worktree(w) for w in data.worktrees],
        "branches": [_ref(b) for b in data.locals],
        "remotes": [_ref(b) for b in data.remotes],
        "strays": strays,
        "plan": plan,
    }


class Paint:
    def __init__(self, on: bool) -> None:
        self.on = on

    def __call__(self, code: int, s: object) -> str:
        return f"\x1b[{code}m{s}\x1b[0m" if self.on else str(s)

    def dim(self, s: object) -> str:
        return self(90, s)

    def bold(self, s: object) -> str:
        return self(1, s)

    def red(self, s: object) -> str:
        return self(31, s)

    def green(self, s: object) -> str:
        return self(32, s)

    def yellow(self, s: object) -> str:
        return self(33, s)

    def blue(self, s: object) -> str:
        return self(34, s)

    def cyan(self, s: object) -> str:
        return self(36, s)


def _width(s: str) -> int:
    import re

    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))


def pad(s: str, n: int) -> str:
    return s + " " * max(0, n - _width(s))


def totals(data: Report, p: Paint) -> str:
    sums = dict.fromkeys(RISK_ORDER, 0.0)
    for o in data.plan:
        sums[o.risk] = round1(sums[o.risk] + o.mb)
    return ", ".join(f"{p(RISK_PAINT[r], r)} {mb_text(sums[r])}" for r in RISK_ORDER)


class Human:
    """The report a person reads: the base, the worktrees, the branches, then the plan."""

    RULE = 96

    def __init__(self, repo: str, opt: Options, data: Report) -> None:
        self.repo = repo
        self.opt = opt
        self.data = data
        self.p = Paint(opt.color)

    def heading(self, title: str, note: str | None = None) -> None:
        t = f" {title} "
        print("")
        print(
            self.p.dim("-" * 2) + self.p.bold(t) + self.p.dim("-" * max(0, self.RULE - len(t) - 2))
        )
        if note:
            print(f"   {self.p.dim(note)}")

    def say(self, s: str) -> None:
        print(f"   {s}")

    def verdict_tag(self, v: str) -> str:
        return self.p(VERDICT[v].paint, pad(VERDICT[v].label, 13))

    def risk_tag(self, risk: str) -> str:
        return "" if risk == "high" else f" {self.p(RISK_PAINT[risk], f'risk:{risk}')}"

    def drift_text(self) -> str:
        d, p = self.data, self.p
        if d.ahead is None or d.behind is None:
            return p.dim(f"no {d.remote_base}")
        if d.ahead == 0 and d.behind == 0:
            return p.green("in step")
        ahead = p.red(f"{d.ahead} unpushed") if d.ahead else ""
        sep = p.dim(", ") if d.ahead and d.behind else ""
        behind = p.yellow(f"{d.behind} unpulled") if d.behind else ""
        return f"{ahead}{sep}{behind}"

    def group(
        self, rows: Sequence[WorktreeAudit | RefAudit], v: str, render: Callable[[Any], None]
    ) -> None:
        mine = [row for row in rows if row.verdict == v]
        if not mine:
            return
        vd, p = VERDICT[v], self.p
        safe = p.green(", safe to delete") if vd.offered else ""
        print("")
        print(f"  {p(vd.paint, p.bold(vd.label))} {p.dim(f'- {vd.blurb}')}{safe}")
        for row in mine:
            render(row)

    def evidence(self, ref: RefAudit, indent: str = "      ") -> None:
        p, opt = self.p, self.opt
        if ref.second_held is not None and not ref.contained:
            if ref.agrees:
                print(f"{indent}{p.dim(f'both routes agree over {ref.second_checked} path(s)')}")
            else:
                walked, tip = len(ref.new_files), len(ref.second_held)
                said = f"commit walk says {walked} new, tip diff says {tip}"
                print(f"{indent}{p.red(f'ROUTES DISAGREE: {said}')}")
        if ref.contained:
            return
        parts = []
        if ref.duplicates:
            plural = "s" if ref.duplicates > 1 else ""
            parts.append(f"{ref.duplicates} commit{plural} already in {ref.base} by patch")
        resolved = [c for c in ref.candidates if not c.is_new]
        if resolved:
            states: dict[str, int] = {}
            for f in (f for c in resolved for f in c.files):
                states[f.state] = states.get(f.state, 0) + 1
            words = ", ".join(f"{n} {s}" for s, n in states.items() if s != "gone")
            parts.append(f"{len(resolved)} more resolved by content ({words})")
        if parts:
            print(p.dim(f"{indent}{'; '.join(parts)}"))
        for f in (f for c in resolved for f in c.files if f.note):
            print(p.dim(f"{indent}{f.path}: {f.note}"))
        fresh = [c for c in ref.candidates if c.is_new]
        for c in fresh[: opt.max_commits]:
            print(f"{indent}{p.yellow('+')} {c.sha[:9]} {c.subject[:70]}")
            new = [f for f in c.files if f.state == "new"]
            for f in new[: opt.max_files]:
                print(p.dim(f"{indent}    {f.path}"))
            if len(new) > opt.max_files:
                print(p.dim(f"{indent}    ... {len(new) - opt.max_files} more files"))
            if c.subject_in_base:
                print(
                    p.dim(
                        f"{indent}    (a commit with this subject is in {ref.base}: "
                        "check it by hand)"
                    )
                )
        if len(fresh) > opt.max_commits:
            print(
                p.dim(f"{indent}... {len(fresh) - opt.max_commits} more commits carry new content")
            )

    def summary(self) -> None:
        d, p = self.data, self.p
        self.heading("Branch audit", f"{d.base} vs {d.remote_base}")
        self.say(f"{p.bold(d.base)} {self.drift_text()}")
        counts = f"worktrees {len(d.worktrees)}, local {len(d.locals)}, remote {len(d.remotes)}"
        self.say(p.dim(f"{counts}, stray folders {len(d.strays)}"))
        rows = [
            (
                w.verdict,
                f"{w.name}{p.dim(f'({mb_text(megabytes(w.size))})')}",
                w.protected,
                w.caller,
            )
            for w in d.worktrees
        ]
        rows += [(b.verdict, b.ref, b.protected, False) for b in d.locals + d.remotes]
        print("")
        for v, vd in VERDICT.items():
            mine = [x for x in rows if x[0] == v]
            if mine:
                marks = ", ".join(
                    p(vd.paint, label)
                    + (p.cyan("[protected]") if prot else "")
                    + (p.blue("[here]") if here else "")
                    for _, label, prot, here in mine
                )
                self.say(f"  {pad(vd.label, 15)} {marks}")
        if d.strays:
            marks = ", ".join(
                f"{p.red(s.name)}{p.dim(f'({mb_text(megabytes(s.size))})')}" for s in d.strays
            )
            self.say(f"  {pad('STRAY FOLDER', 15)} {marks}")
        bad = [b.ref for b in d.locals + d.remotes if b.agrees is False]
        if bad:
            print("")
            self.say(
                p.red(f"ROUTES DISAGREE on {', '.join(bad)}: nothing automatic will touch them.")
            )
        print("")
        self.say(f"plan frees {totals(d, p)}")
        self.say(p.dim("drop --summary for the evidence behind every line"))

    def file_lines(self, w: WorktreeAudit) -> None:
        p, cap = self.p, self.opt.max_files
        shown: dict[str, int] = {}
        for f in w.files:
            n = shown.get(f.state, 0)
            shown[f.state] = n + 1
            if n == cap:
                print(p.dim(f"      ... more {f.state} files"))
            if n >= cap:
                continue
            size = f" {mb_text(megabytes(f.size))}" if f.size >= 1048576 else ""
            if f.state == "stray":
                print(
                    f"      {p.dim(f'o {f.path} (a copy of the version of {f.at}: safe to lose)')}"
                )
            elif f.state in ("cache", "copy"):
                print(p.dim(f"      o {f.path} ({f.state}{size})"))
            else:
                hint = (
                    p.dim(f" (the base committed {f.path} after this changed)")
                    if f.hint == "base_newer"
                    else ""
                )
                state = f"({f.state.replace('_', ' ', 1)}{size})"
                print(f"      {p.yellow('*')} {f.path} {p.dim(state)}{hint}")

    def worktree_row(self, w: WorktreeAudit) -> None:
        p = self.p
        lock = "" if w.lock.state == "none" else f" {p.yellow(f'lock:{w.lock.state}')}"
        idle = w.lock.session_idle_hours
        session = "" if idle is None else p.dim(f" session idle {age(idle)}")
        branch = pad(p.dim(w.entry.branch or "(detached)"), 36)
        size = pad(p.dim(mb_text(megabytes(w.size))), 10)
        here = p.blue(" [here]") if w.caller else ""
        print(
            f"    {self.verdict_tag(w.verdict)} {pad(p.bold(w.name), 26)} {branch} {size} "
            f"{p.dim(f'idle {age(w.idle_hours)}')}{lock}{session}{here}{self.risk_tag(w.risk)}"
        )
        for q in w.holders[:3]:
            print(p.dim(f"      process {q.pid} works in {q.cwd}"))
        self.file_lines(w)
        self.evidence(w.ref)

    def ref_row(self, b: RefAudit, local: bool) -> None:
        p = self.p
        where = p.blue(f"[{b.worktree}]") if b.worktree and local else ""
        risk = self.risk_tag(b.risk) if local else ""
        drift = pad(p.dim(f"{b.ahead} ahead {b.behind} behind"), 22)
        prot = p.cyan(" [protected]") if b.protected else ""
        print(
            f"    {self.verdict_tag(b.verdict)} {pad(p.bold(b.ref), 40)} {drift} "
            f"{p.dim(b.when[:10])}{' ' if local else ''}{where}{prot}{risk}"
        )
        self.evidence(b)

    def report(self) -> None:
        d, p = self.data, self.p
        self.heading("The base", f"{d.base} vs {d.remote_base}")
        self.say(f"{p.bold(d.base)} {self.drift_text()}")
        if d.ahead:
            self.say(
                p.red(
                    f"A remote branch merged into {d.base} is NOT on the server yet. "
                    "Do not delete one until you push."
                )
            )
        self.heading("Worktrees", f"{len(d.worktrees)} linked to {self.repo}")
        for v in VERDICT:
            self.group(d.worktrees, v, self.worktree_row)
        if d.strays:
            self.heading(
                "Stray folders", f"under {self.opt.worktree_root}, and not a worktree git knows"
            )
            for s in d.strays:
                partial = (
                    p.red(f" ({s.unreadable} folder(s) unreadable: the size is a floor)")
                    if s.unreadable
                    else ""
                )
                held = (
                    ""
                    if s.offered
                    else p.yellow(" (in use or touched inside --stale-hours: not offered)")
                )
                print(
                    f"    {pad(p.bold(s.name), 26)} {pad(p.dim(mb_text(megabytes(s.size))), 10)} "
                    f"{p.dim(f'owner {s.owner}, newest file {age(s.idle_hours)} ago')}"
                    f"{partial}{held}"
                )
        self.heading("Local branches", f"{len(d.locals)} beside {d.base}")
        for v in ("merged", "absorbed", "in_progress", "unmerged"):
            self.group(d.locals, v, lambda b: self.ref_row(b, True))
        self.heading(
            "Remote branches", f"judged against {d.remote_base}, never against local {d.base}"
        )
        for v in ("merged", "absorbed", "unmerged"):
            self.group(d.remotes, v, lambda b: self.ref_row(b, False))
        self.heading("Summary")
        verdicts = [w.verdict for w in d.worktrees] + [b.verdict for b in d.locals + d.remotes]
        keep = [v for v in verdicts if not VERDICT[v].offered]
        self.say(f"plan frees {totals(d, p)}")
        self.say(
            p.red(f"{len(keep)} rows need a human.") if keep else p.green("Nothing needs a human.")
        )
        prot = [x.ref for x in d.locals + d.remotes if x.protected]
        if prot:
            self.say(p.dim(f"{len(prot)} protected and never offered: {', '.join(prot)}"))

    def plan(self) -> None:
        d, p, ops = self.data, self.p, self.data.plan
        self.heading("Plan", "applying" if self.opt.apply else "nothing below has been run")
        if not ops:
            self.say(p.green("nothing to prune"))
            return
        cap = self.opt.max_files * 2
        for r in RISK_ORDER:
            mine = [o for o in ops if o.risk == r]
            if not mine:
                continue
            total = mb_text(round1(sum(o.mb for o in mine)))
            print("")
            print(
                f"  {p(RISK_PAINT[r], p.bold(f'risk:{r}'))} {p.dim(f'{len(mine)} row(s), {total}')}"
            )
            for kind in dict.fromkeys(o.kind for o in mine):
                rows = sorted((o for o in mine if o.kind == kind), key=lambda o: -o.mb)
                for o in rows[:cap]:
                    mark = p.green(">") if o.runnable else p.red("#")
                    size = p.dim(f" {mb_text(o.mb)}") if o.mb else ""
                    print(f"    {mark} {pad(o.id, 34)}{size}  {p.dim(' '.join(o.argv))}")
                    if o.kind == "remote_delete" and d.ahead:
                        refused = (
                            f"refused: {d.base} has {d.ahead} unpushed commit(s), "
                            "so the server is the only copy"
                        )
                        print(f"        {p.red(refused)}")
                if len(rows) > cap:
                    rest = rows[cap:]
                    more = (
                        f"{len(rest)} more {kind} rows, {mb_text(round1(sum(o.mb for o in rest)))}"
                    )
                    print(p.dim(f"      ... {more}"))
        print("")
        self.say(
            p.dim(
                "# rows are printed only: the user runs them. --apply runs risk:none; "
                "--apply --only <id,...> runs named rows up to medium."
            )
        )
        if self.opt.land:
            self.say(
                p.dim(
                    f"A row worth keeping is committed on its branch and landed with "
                    f"`{self.opt.land}` first."
                )
            )
