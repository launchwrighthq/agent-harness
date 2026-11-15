"""`/lw-goal-spec`: a goal that lets a turn end only when the saved spec audit allows it.

It reads the saved audit, the base branch and origin, never the chat, and holds a turn with exit 2.

    ah spec goal start [--base <branch>] [--level merged|pushed] [--codex] [spec ...]
    ah spec goal status | clear | check
    hooks/goal-spec.py Stop       the Stop hook; the payload arrives on stdin

The base branch defaults to `harness.baseBranch`. The goal and the audits live in the spec store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from agent_harness import config
from agent_harness.spec import layout, store
from agent_harness.work import status as ws

LEVELS = ("merged", "pushed")
# A goal in one of these is evaluated at every stop. `met` and `cleared` are finished.
LIVE = ("active", "stuck")
# The verdicts after which a turn may end. Every other kind holds it.
ENDS_TURN = ("met", "blocked-asked", "agents-running", "review-capped")

# The stall at which the hook holds once more for a plain report, and the one it lets go at.
STUCK_REPORT = 3
STUCK_RELEASE = 4
STUCK_REASON = (
    "goal-spec: no progress in three stops. "
    "End the turn with a plain report naming each open ID and what blocks it."
)

EXIT_CHOOSE = 3
PROJECTS_ROOT = Path.home() / ".claude" / "projects"
CODEX_RUNNER = "python3 .claude/agent-harness/bin/ah spec codex run"


def _spec_re(cwd: Path) -> re.Pattern[str]:
    """The spec paths of the repository that holds `cwd`, as a transcript writes them."""
    return layout.for_root(config.find_root(cwd)).mention_re()


@dataclass(frozen=True, slots=True)
class Verdict:
    kind: str
    open_ids: dict[str, list[str]]
    blocked_ids: dict[str, list[str]]
    notes: dict[str, str]
    reason: str
    # The open blocking Codex finding IDs per spec, for a `review-rejected` or `review-capped` one.
    findings: dict[str, list[str]] = field(default_factory=dict)

    @property
    def holds(self) -> bool:
        return self.kind not in ENDS_TURN

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "holds": self.holds,
            "open_ids": self.open_ids,
            "blocked_ids": self.blocked_ids,
            "notes": self.notes,
            "reason": self.reason,
            "findings": self.findings,
        }


def _short(sha: object) -> str:
    return sha[:9] if isinstance(sha, str) and sha else "unknown"


def _names(message: str, rid: str) -> bool:
    """`R1` is named by "R1?" and "R1-R3", and not by "R12"."""
    return re.search(rf"(?<!\w){re.escape(rid)}(?!\w)", message) is not None


def _rows_note(open_ids: list[str], blocked: list[str]) -> str:
    parts = [f"open {', '.join(open_ids)}"] if open_ids else []
    parts += [f"blocked {', '.join(blocked)}"] if blocked else []
    return "; ".join(parts) or "zero open rows"


def _review_gate(
    goal: dict[str, Any],
    reviews: dict[str, dict[str, Any] | None],
    head: str | None,
    blocked: dict[str, list[str]],
    notes: dict[str, str],
) -> Verdict | None:
    """What a `--codex` goal still waits on from Codex, None once every review approves the base
    branch, and at the pass cap the verdict that hands the findings to the operator."""
    stems: list[str] = list(goal["stems"])
    found = {stem: reviews.get(stem) or {} for stem in stems}

    def noted(said: dict[str, str]) -> dict[str, str]:
        return {
            stem: f"{note}; {said[stem]}" if stem in said else note for stem, note in notes.items()
        }

    owed = [stem for stem in stems if store.head_of(found[stem]) != head]
    if owed:
        said = {
            stem: f"the Codex review read {store.base_of(found[stem])} "
            f"at {_short(store.head_of(found[stem]))}"
            if found[stem]
            else "no Codex review"
            for stem in owed
        }
        reason = (
            f"Run `{CODEX_RUNNER} <spec>` as a background job, once for each of: {', '.join(owed)}."
        )
        return Verdict("review-owed", {}, blocked, noted(said), reason)
    unverified = [stem for stem in stems if not store.review_is_valid(found[stem])]
    if unverified:
        said = dict.fromkeys(unverified, "no codex exec session of this commit gave this review")
        reason = (
            f"Only a Codex run writes a review. Run `{CODEX_RUNNER}` for {', '.join(unverified)}."
        )
        return Verdict("review-unverified", {}, blocked, noted(said), reason)
    rejected = {
        stem: store.open_blocking_ids(found[stem])
        for stem in stems
        if found[stem].get("verdict") != store.APPROVED
    }
    if rejected:
        said = {
            stem: f"Codex open blocking {', '.join(ids) or 'none named'}"
            for stem, ids in rejected.items()
        }
        if all(store.at_the_cap(found[stem]) for stem in rejected):
            named = "; ".join(
                f"{stem} {', '.join(ids) or 'none named'}" for stem, ids in rejected.items()
            )
            reason = (
                f"The Codex review reached the {store.PASS_CAP}-pass cap, "
                f"still open blocking: {named}. "
                "The operator decides whether each one is worth another pass."
            )
            return Verdict("review-capped", {}, blocked, noted(said), reason, rejected)
        reason = (
            "Fix each open blocking finding, merge, run /lw-spec-audit, "
            "then run the Codex review again. "
            "Dispute a finding with --reply <file>."
        )
        return Verdict("review-rejected", {}, blocked, noted(said), reason, rejected)
    return None


def _judge(
    goal: dict[str, Any],
    audits: dict[str, dict[str, Any] | None],
    head: str | None,
    in_origin: bool,
    last_message: str,
    reviews: dict[str, dict[str, Any] | None],
) -> Verdict:
    base = store.base_of(goal)
    stems: list[str] = list(goal["stems"])
    missing = [stem for stem in stems if audits.get(stem) is None]
    if missing:
        notes = dict.fromkeys(missing, "no audit")
        reason = f"Run /lw-spec-audit {' '.join(missing)}. This hook reads only a saved audit."
        return Verdict("no-audit", {}, {}, notes, reason)
    found: dict[str, dict[str, Any]] = {stem: audits[stem] or {} for stem in stems}
    stale = [stem for stem in stems if store.head_of(found[stem]) != head]
    if stale:
        notes = {
            stem: f"the audit read {store.base_of(found[stem])} "
            f"at {_short(store.head_of(found[stem]))}, "
            f"{base} is {_short(head)}"
            for stem in stale
        }
        reason = f"{base} moved since the audit. Run /lw-spec-audit {' '.join(stale)} again."
        return Verdict("stale-audit", {}, {}, notes, reason)
    unauthored = [stem for stem in stems if not store.auditor_is_valid(found[stem])]
    if unauthored:
        notes = dict.fromkeys(unauthored, "the audit names no spec-audit agent file")
        reason = (
            f"Only the spec-audit agent writes an audit. Run /lw-spec-audit {' '.join(unauthored)}."
        )
        return Verdict("unauthored-audit", {}, {}, notes, reason)

    open_ids = {stem: ids for stem in stems if (ids := store.open_ids(found[stem]))}
    blocked = {stem: ids for stem in stems if (ids := store.blocked_ids(found[stem]))}
    notes = {stem: _rows_note(open_ids.get(stem, []), blocked.get(stem, [])) for stem in stems}
    every_blocked = [rid for ids in blocked.values() for rid in ids]
    if open_ids:
        reason = (
            "Build the open rows, or mark one blocked with a question. "
            f"Then run /lw-spec-audit {' '.join(open_ids)}."
        )
        return Verdict("open", open_ids, blocked, notes, reason)
    if goal.get("codex") and (review := _review_gate(goal, reviews, head, blocked, notes)):
        return review
    if goal.get("level") == "pushed" and not in_origin:
        kind = "push-owed"
        reason = f"The goal's level is pushed, and origin/{base} does not hold {base}. Push {base}."
    elif every_blocked and not all(_names(last_message, rid) for rid in every_blocked):
        kind = "blocked-unasked"
        reason = (
            "End the turn with a question to the operator naming each blocked ID: "
            f"{', '.join(every_blocked)}."
        )
    elif every_blocked:
        kind = "blocked-asked"
        reason = "The last message names every blocked ID, so the question reaches the operator."
    else:
        kind = "met"
        reason = "Every audit shows zero open rows."
    return Verdict(kind, open_ids, blocked, notes, reason)


def evaluate(
    goal: dict[str, Any],
    audits: dict[str, dict[str, Any] | None],
    head: str | None,
    in_origin: bool,
    last_message: str,
    running: int,
    reviews: dict[str, dict[str, Any] | None] | None = None,
) -> Verdict:
    """The verdict for one goal, from values the caller read. It writes nothing.

    The order is the order a builder fixes things in: an audit must exist, read the base's commit,
    and come from the auditor before its rows mean anything. Codex reviews before a push or a
    blocked question. A holding verdict lets the turn end while agents still run.
    """
    verdict = _judge(goal, audits, head, in_origin, last_message, reviews or {})
    if not verdict.holds or running <= 0:
        return verdict
    reason = f"{running} agents or background jobs still run. Their notifications wake the session."
    return Verdict(
        "agents-running",
        verdict.open_ids,
        verdict.blocked_ids,
        verdict.notes,
        reason,
        verdict.findings,
    )


def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        done = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return -1, str(error)
    return done.returncode, done.stdout.strip()


def _in_origin(base: str, cwd: Path) -> bool:
    """No fetch: a hook that reaches the network at every stop would stall every turn end."""
    code, _ = _git(["merge-base", "--is-ancestor", base, f"origin/{base}"], cwd)
    return code == 0


def _fingerprint(verdict: Verdict, head: str | None, cwd: Path) -> str:
    """What progress changes: the base's commit, a worktree's HEAD, or the rows of the audit."""
    _, listing = _git(["worktree", "list", "--porcelain"], cwd)
    heads = sorted(line.split()[1] for line in listing.splitlines() if line.startswith("HEAD "))
    parts = [
        head or "",
        *heads,
        verdict.kind,
        json.dumps(verdict.open_ids, sort_keys=True),
        json.dumps(verdict.blocked_ids, sort_keys=True),
        json.dumps(verdict.findings, sort_keys=True),
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Transcript:
    """Where the session's transcript lives, read only when a verdict needs it."""

    session: str
    root: Path
    named: Path | None = None

    def records(self) -> list[dict[str, Any]]:
        return _records(self.session, self.root, self.named)

    def last_assistant_text(self) -> str:
        for record in reversed(self.records()):
            if record.get("type") == "assistant":
                text = ws.text_of((record.get("message") or {}).get("content"))
                if text.strip():
                    return str(text)
        return ""

    def running(self) -> int:
        """Agents and background jobs with no finish notification.

        The subagent directory is not read: `task_rows` adopts an agent file whose spawn it
        never saw as `running`, and such a row never finishes, so it would free every stop.
        """
        rows = ws.task_rows(self.records(), [], _spec_re(Path.cwd()))
        return sum(1 for row in rows if row.state == "running")


@cache
def _records(session: str, root: Path, named: Path | None) -> list[dict[str, Any]]:
    paths = ws.find_transcripts(session, root)
    if not paths and named is not None and named.is_file():
        paths = [named]
    return list(ws.load_records(paths))


def judge(
    goal: dict[str, Any], cwd: Path, transcript: Transcript, last_message: object
) -> tuple[Verdict, str | None]:
    """Read the facts `evaluate` needs, each only when the verdict can depend on it."""
    audits = {stem: store.load_audit(stem, cwd) for stem in goal["stems"]}
    reviews = (
        {stem: store.load_review(stem, cwd) for stem in goal["stems"]} if goal.get("codex") else {}
    )
    base = store.base_of(goal)
    head = store.branch_commit(base, cwd)
    in_origin = goal.get("level") != "pushed" or _in_origin(base, cwd)
    message = ""
    if any(audit and store.blocked_ids(audit) for audit in audits.values()):
        message = (
            last_message if isinstance(last_message, str) else transcript.last_assistant_text()
        )
    verdict = evaluate(goal, audits, head, in_origin, message, 0, reviews)
    if verdict.holds:
        verdict = evaluate(goal, audits, head, in_origin, message, transcript.running(), reviews)
    return verdict, head


def _is_session(session: object) -> bool:
    """A session id names one file. A separator in it would name a file somewhere else."""
    return isinstance(session, str) and re.fullmatch(r"[\w-]+", session) is not None


def _checked_session(session: object) -> str:
    if not _is_session(session):
        raise SystemExit(f"goal-spec: {session!r} is not a session id; pass --session")
    return str(session)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def hold_message(verdict: Verdict) -> str:
    lines = [f"goal-spec: not done ({verdict.kind})"]
    lines += [f"{stem}: {note}" for stem, note in verdict.notes.items()]
    return "\n".join([*lines, verdict.reason])


def stop(payload: dict[str, Any], fallback_session: str | None) -> int:
    session = payload.get("session_id") or fallback_session
    if not _is_session(session):
        return 0
    cwd = Path(payload["cwd"]) if isinstance(payload.get("cwd"), str) else Path.cwd()
    path = store.goal_path(str(session), cwd)
    goal = store.read_json(path)
    if goal is None or goal.get("status") not in LIVE:
        return 0
    try:
        return _stop_on_goal(goal, path, payload, cwd)
    # A crashed hook must not trap the session in a turn it cannot end. `status` shows the error.
    except (Exception, SystemExit) as error:  # noqa: BLE001 - recorded on the goal
        goal["last_error"] = f"{_now()} {type(error).__name__}: {error}"
        store.write_json(path, goal)
        print(
            f"goal-spec: the Stop hook failed and let the turn end: {goal['last_error']}",
            file=sys.stderr,
        )
        return 0


def _stop_on_goal(goal: dict[str, Any], path: Path, payload: dict[str, Any], cwd: Path) -> int:
    """Hold the turn, or let it end, and count the stops that changed nothing. `stop_hook_active`
    is no early return: a held stop sets it on the next turn, so the stall count is the guard."""
    named = payload.get("transcript_path")
    named_path = Path(named) if isinstance(named, str) and named else None
    root = named_path.parent.parent if named_path else PROJECTS_ROOT
    transcript = Transcript(goal["session"], root, named_path)
    verdict, head = judge(goal, cwd, transcript, payload.get("last_assistant_message"))
    goal["last_verdict"] = {**verdict.as_json(), "at": _now()}
    goal["last_error"] = None
    if not verdict.holds:
        if verdict.kind == "met":
            goal["status"] = "met"
        store.write_json(path, goal)
        return 0
    fingerprint = _fingerprint(verdict, head, cwd)
    if fingerprint == goal.get("fingerprint"):
        goal["stalls"] = int(goal.get("stalls") or 0) + 1
    else:
        goal.update(stalls=0, status="active", fingerprint=fingerprint)
    if goal["stalls"] >= STUCK_RELEASE:
        goal.update(stalls=0, status="stuck")
        store.write_json(path, goal)
        return 0
    store.write_json(path, goal)
    print(
        STUCK_REASON if goal["stalls"] == STUCK_REPORT else hold_message(verdict), file=sys.stderr
    )
    return 2


def _candidates(session: str, cwd: Path, root: Path) -> list[Path]:
    """The unsettled specs the session named, newest mention first, each once."""
    records = ws.load_records(ws.find_transcripts(session, root))
    mentions = ws.spec_mentions(records, _spec_re(cwd))
    found: list[Path] = []
    for named in sorted(mentions, key=lambda each: mentions[each][1], reverse=True):
        spec = store.resolve_spec(named, cwd)
        if spec is not None and spec not in found and store.status_of(spec) not in store.SETTLED:
            found.append(spec)
    return found


def _codex_missing() -> str | None:
    """Why Codex cannot review on this machine, or None. `login status` exits 1 with no login."""
    if shutil.which("codex") is None:
        return "the codex CLI is not on PATH. Install it with `npm install -g @openai/codex`."
    try:
        done = subprocess.run(
            ["codex", "login", "status"], capture_output=True, text=True, check=False, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"`codex login status` failed: {error}"
    return None if done.returncode == 0 else "codex is not logged in. Run `codex login`."


def start(args: argparse.Namespace, cwd: Path) -> int:
    session = _checked_session(args.session)
    configured = store.default_base(cwd)
    args.base = args.base or configured
    base_commit = store.branch_commit(args.base, cwd)
    if base_commit is None:
        print(f"goal-spec: the branch {args.base!r} does not resolve", file=sys.stderr)
        return 1
    if args.codex and (missing := _codex_missing()):
        print(f"goal-spec: --codex refused, {missing}", file=sys.stderr)
        return 1
    specs: list[Path] = []
    for arg in args.specs:
        spec = store.resolve_spec(arg, cwd)
        if spec is None:
            print(f"goal-spec: no spec matches {arg!r}", file=sys.stderr)
            return 1
        specs.append(spec)
    if not specs:
        specs = _candidates(session, cwd, args.projects_root)
        if not specs:
            print(
                "goal-spec: this session named no unsettled spec. Pass a spec path.",
                file=sys.stderr,
            )
            return 1
        if len(specs) > 1:
            for number, spec in enumerate(specs, 1):
                print(f"{number}. {spec}")
            print(
                "goal-spec: several unsettled specs. Ask the operator which to build.",
                file=sys.stderr,
            )
            return EXIT_CHOOSE
    stems = [store.stem_of(spec) for spec in specs]
    goal = {
        "session": session,
        "specs": [str(spec) for spec in specs],
        "stems": stems,
        "level": args.level,
        "codex": args.codex,
        "base": args.base,
        "base_commit": base_commit,
        "started_at": _now(),
        "status": "active",
        "stalls": 0,
        "fingerprint": None,
        "last_verdict": None,
        "last_error": None,
    }
    store.write_json(store.goal_path(session, cwd), goal)
    also = f" and Codex approves {args.base}" if args.codex else ""
    on = "" if args.base == configured else f" on {args.base}"
    print(
        f"goal-spec active for {', '.join(stems)}{on} at level {args.level}. "
        f"The turn ends when the saved audit shows zero open rows{also}."
    )
    return 0


def _goal_or_exit(args: argparse.Namespace, cwd: Path) -> tuple[dict[str, Any], Path]:
    session = _checked_session(args.session)
    path = store.goal_path(session, cwd)
    goal = store.read_json(path)
    if goal is None:
        raise SystemExit(f"goal-spec: no goal for session {session}")
    return goal, path


def _fresh(goal: dict[str, Any], args: argparse.Namespace, cwd: Path) -> Verdict:
    transcript = Transcript(goal["session"], args.projects_root)
    return judge(goal, cwd, transcript, None)[0]


def status(args: argparse.Namespace, cwd: Path) -> int:
    goal, _ = _goal_or_exit(args, cwd)
    last = goal.get("last_verdict") or {}
    verdict = _fresh(goal, args, cwd)
    print(
        f"goal-spec {goal.get('status')} for {', '.join(goal['stems'])} on {store.base_of(goal)} "
        f"at level {goal.get('level')}, "
        f"started {goal.get('started_at')}, stalls {goal.get('stalls')}"
    )
    print(f"last verdict: {last.get('kind', 'none')} at {last.get('at', 'never')}")
    print(f"last error: {goal.get('last_error') or 'none'}")
    print(f"now: {verdict.kind}")
    for stem, note in verdict.notes.items():
        print(f"{stem}: {note}")
    if goal.get("codex"):
        for stem in goal["stems"]:
            print(f"codex review of {stem}: {_review_line(store.load_review(stem, cwd))}")
    print(verdict.reason)
    return 0


def _review_line(review: dict[str, Any] | None) -> str:
    if review is None:
        return "none"
    blocking = ", ".join(store.open_blocking_ids(review)) or "none"
    return (
        f"{review.get('verdict')} at {store.base_of(review)} {_short(store.head_of(review))}, "
        f"pass {review.get('pass')}, open blocking {blocking}"
    )


def clear(args: argparse.Namespace, cwd: Path) -> int:
    goal, path = _goal_or_exit(args, cwd)
    goal["status"] = "cleared"
    store.write_json(path, goal)
    print(f"goal-spec cleared for {', '.join(goal['stems'])}.")
    return 0


def check(args: argparse.Namespace, cwd: Path) -> int:
    goal, _ = _goal_or_exit(args, cwd)
    print(json.dumps(_fresh(goal, args, cwd).as_json(), indent=2))
    return 0


def _parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"ah spec goal {command}")
    parser.add_argument(
        "--session",
        default=os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"),
    )
    parser.add_argument(
        "--projects-root", type=Path, default=PROJECTS_ROOT, help="where transcripts live"
    )
    if command == "start":
        parser.add_argument(
            "--base", help="the branch the goal is judged on; default harness.baseBranch"
        )
        parser.add_argument("--level", choices=LEVELS, default="merged")
        parser.add_argument(
            "--codex", action="store_true", help="Codex must also approve the base branch"
        )
        parser.add_argument("specs", nargs="*")
    return parser


COMMANDS = {"start": start, "status": status, "clear": clear, "check": check}


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command == "Stop":
        args = _parser(command).parse_args(argv[1:])
        payload = json.load(sys.stdin)
        return stop(payload if isinstance(payload, dict) else {}, args.session)
    if command not in COMMANDS:
        print(f"usage: ah spec goal {{{','.join(COMMANDS)}}} [...]", file=sys.stderr)
        return 2
    # Intermixed, so `start spec-a --level pushed spec-b` reads both specs.
    args = _parser(command).parse_intermixed_args(argv[1:])
    return COMMANDS[command](args, Path.cwd())
