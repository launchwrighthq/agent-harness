"""The one reader and writer of a saved spec audit, a saved Codex review and a saved goal.

`ah spec audit` and `ah spec codex` write, the `goal-spec` Stop hook decides, and `ah work-status`
counts open rows. All of them use this module, so none can disagree about what a state means or
where a file lives.

The files live in `[specs] auditDir` (default `.claude/state`) of the MAIN checkout, because the
Stop hook runs from the project root while a reviewer can run from any worktree. An absolute
`auditDir` is used as it is. `AGENT_STATE_DIR` overrides the folder for one process.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_harness import config
from agent_harness.spec import layout

# A row in one of these still owes work, whatever else the audit says.
OPEN_STATES = ("partly", "missing", "branch-only", "uncommitted")
# Owes the operator an answer, not a build. It ends a turn only once the question is asked.
BLOCKED = "blocked"
CLOSED_STATES = ("done", "deferred-by-operator")
STATES = (*CLOSED_STATES, *OPEN_STATES, BLOCKED)

# The Agent description every auditor spawn starts with. Its subagent `meta.json` carries it,
# and a hand-written audit file has no such agent behind it.
AUDITOR_PREFIX = "spec-audit"

# The base of a record saved before records named their base.
DEFAULT_BASE = "main"


def git(args: list[str], cwd: Path | None) -> str | None:
    try:
        done = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def main_root(cwd: Path | None = None) -> Path | None:
    """The main checkout of the repository that holds `cwd`, from the shared git folder."""
    common = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    return Path(common).parent if common else None


def checkout_root(cwd: Path | None = None) -> Path | None:
    top = git(["rev-parse", "--show-toplevel"], cwd)
    return Path(top) if top else None


def state_dir(cwd: Path | None = None) -> Path:
    """`AGENT_STATE_DIR`, else `[specs] auditDir` of the main checkout."""
    override = os.environ.get("AGENT_STATE_DIR")
    if override:
        return Path(override)
    root = main_root(cwd)
    if root is None:
        raise SystemExit("spec store: not inside a git checkout, and AGENT_STATE_DIR is unset")
    folder = Path(str(config.load(root).get("specs.auditDir")))
    return folder if folder.is_absolute() else root / folder


def default_base(cwd: Path | None = None) -> str:
    """`harness.baseBranch` of the checkout that holds `cwd`."""
    return str(config.load(checkout_root(cwd) or Path.cwd()).get("harness.baseBranch"))


def branch_commit(branch: str, cwd: Path | None = None) -> str | None:
    return git(["rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}"], cwd)


def base_of(record: dict[str, Any] | None) -> str:
    return str((record or {}).get("base") or DEFAULT_BASE)


def head_of(record: dict[str, Any]) -> str | None:
    """The commit of its base that a record read. An older record has `main_commit`."""
    head = record.get("head") or record.get("main_commit")
    return str(head) if head else None


def stem_of(spec: str | Path) -> str:
    return Path(spec).stem


# The settled statuses of `.claude/rules/lw/spec-format.md`.
SETTLED = ("implemented", "superseded-by", "abandoned", "answered")
# The window and the spelling a status is read with. Older specs bold it.
STATUS_RE = re.compile(r"\bStatus:?\**\s*([a-z-]+)", re.IGNORECASE)


def status_of(spec: Path) -> str | None:
    try:
        head = spec.read_bytes()[:4000].decode("utf-8", errors="replace")
    except OSError:
        return None
    found = STATUS_RE.search(head)
    return found.group(1).lower() if found else None


def checkout_roots(cwd: Path | None = None) -> list[Path]:
    """This checkout first, then the main one: a spec can sit untracked in either."""
    top = checkout_root(cwd)
    roots = [top] if top else []
    main = main_root(cwd)
    if main and main not in roots:
        roots.append(main)
    return roots


def resolve_spec(arg: str, cwd: Path | None = None) -> Path | None:
    """A path as given, else a stem or file name looked up in each checkout's spec folders."""
    given = Path(arg).expanduser()
    if not given.is_absolute():
        given = (cwd or Path.cwd()) / given
    if given.is_file():
        return given.resolve()
    name = Path(arg).name
    names = [name] if Path(name).suffix in (".md", ".html") else [f"{name}.md", f"{name}.html"]
    for root in checkout_roots(cwd):
        for folder in layout.for_root(root).folders():
            for candidate in names:
                if (root / folder / candidate).is_file():
                    return root / folder / candidate
    return None


def audit_path(stem: str, cwd: Path | None = None) -> Path:
    return state_dir(cwd) / "spec-audit" / f"{stem}.json"


def goal_path(session: str, cwd: Path | None = None) -> Path:
    return state_dir(cwd) / "goal-spec" / f"{session}.json"


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"spec store: {path} is unreadable: {error}") from error
    return loaded if isinstance(loaded, dict) else None


def write_json(path: Path, data: dict[str, Any]) -> Path:
    """Write atomically, so the Stop hook never reads half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".json.partial")
    partial.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    partial.replace(path)
    return path


def load_audit(stem: str, cwd: Path | None = None) -> dict[str, Any] | None:
    return read_json(audit_path(stem, cwd))


def normalise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Refuse a row no reader could act on, and turn an unquoted deferral into `missing`.

    Only the operator defers. A deferral that the auditor cannot quote the operator for is an
    agent's decision, which is the loss this store exists to stop.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        rid, state = str(row.get("id", "")).strip(), row.get("state")
        if not rid or not str(row.get("requirement", "")).strip():
            raise ValueError(f"a row needs an id and a requirement: {row!r}")
        if rid in seen:
            raise ValueError(f"row id {rid} appears twice")
        if state not in STATES:
            raise ValueError(f"row {rid}: state {state!r} is not one of {', '.join(STATES)}")
        if state == BLOCKED and not str(row.get("question", "")).strip():
            raise ValueError(f"row {rid}: a blocked row names the question for the operator")
        fixed = dict(row)
        if state == "deferred-by-operator" and not str(row.get("quote", "")).strip():
            fixed["state"] = "missing"
            fixed["evidence"] = (
                f"{row.get('evidence', '')} (deferral has no operator quote)".strip()
            )
        seen.add(rid)
        out.append(fixed)
    return out


def open_ids(audit: dict[str, Any]) -> list[str]:
    return [r["id"] for r in audit.get("rows", []) if r.get("state") in OPEN_STATES]


def blocked_ids(audit: dict[str, Any]) -> list[str]:
    return [r["id"] for r in audit.get("rows", []) if r.get("state") == BLOCKED]


def write_audit(audit: dict[str, Any], cwd: Path | None = None) -> Path:
    audit = {**audit, "rows": normalise_rows(list(audit.get("rows", [])))}
    audit.setdefault("audited_at", datetime.now(UTC).isoformat(timespec="seconds"))
    return write_json(audit_path(audit["stem"], cwd), audit)


def auditor_is_valid(audit: dict[str, Any]) -> bool:
    """A Claude auditor marker or an independent Codex session backs these rows."""
    raw = audit.get("auditor")
    if not raw:
        return False
    try:
        if str(raw).endswith(".jsonl"):
            meta, last = rollout_answer(Path(raw))
            answer = json.loads(last)
            return bool(
                meta.get("originator") == CODEX_ORIGINATOR
                and isinstance(meta.get("id"), str)
                and isinstance(audit.get("session"), str)
                and meta.get("id") != audit.get("session")
                and (meta.get("git") or {}).get("commit_hash") == head_of(audit)
                and answer.get("head") == head_of(audit)
                and answer.get("stem") == audit.get("stem")
                and normalise_rows(answer["rows"]) == normalise_rows(audit.get("rows", []))
            )
        meta = json.loads(Path(raw).read_text())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return isinstance(meta, dict) and str(meta.get("description", "")).lower().startswith(
        AUDITOR_PREFIX
    )


# Codex, the optional second reviewer of a `--codex` goal. `ah spec codex` saves its verdict.
APPROVED = "approved"
VERDICTS = (APPROVED, "not-approved")
SEVERITIES = ("blocking", "note")
WAIVED = "waived-by-operator"
FINDING_STATES = ("open", "addressed", WAIVED)
# What a finding is about. The severity follows from it, so a cosmetic one cannot hold a turn.
KINDS = (
    "unmet-requirement",
    "defect",
    "crossed-rule",
    "acceptance-precision",
    "spec-wording",
    "style",
)
BLOCKING_KINDS = KINDS[:3]
# Review passes of one spec before the operator decides instead. Later passes tend to polish
# the acceptance table, not the design.
PASS_CAP = 5
# The schema requires each of these as a string. `str(None)` would pass a waiver's quote check.
FINDING_TEXTS = ("id", "where", "finding", "quote")
CODEX_ORIGINATOR = "codex_exec"


def at_the_cap(review: dict[str, Any] | None) -> bool:
    """Whether one spec has had `PASS_CAP` reviews that reached a verdict.

    `reviews` is that count. `pass` numbers the folder, and a crashed pass takes a number
    without a verdict, so it reads high. It is the fallback for a review saved before `reviews`.
    """
    if not review:
        return False
    counted = review.get("reviews") or review.get("pass") or 0
    return int(counted) >= PASS_CAP


def review_path(stem: str, cwd: Path | None = None) -> Path:
    return state_dir(cwd) / "codex-review" / f"{stem}.json"


def passes_dir(stem: str, cwd: Path | None = None) -> Path:
    """Each Codex pass's prompt, schema, log and answer, kept after the run."""
    return state_dir(cwd) / "codex-review" / "passes" / stem


def load_review(stem: str, cwd: Path | None = None) -> dict[str, Any] | None:
    return read_json(review_path(stem, cwd))


def _open_blocking(findings: list[dict[str, Any]]) -> list[str]:
    return [
        f["id"] for f in findings if f.get("severity") == "blocking" and f.get("state") == "open"
    ]


def open_blocking_ids(review: dict[str, Any]) -> list[str]:
    return _open_blocking(list(review.get("findings", [])))


def normalise_answer(answer: object) -> dict[str, Any]:
    """Codex's answer as the `verdict` and `findings` a review saves, or ValueError.

    A waiver with no operator quote stays `open`, a finding outside `BLOCKING_KINDS` becomes a
    `note`, and the verdict is read off the findings. Only the operator waives, and Codex's own
    word neither holds a turn nor releases one.
    """
    if not isinstance(answer, dict) or answer.get("verdict") not in VERDICTS:
        raise ValueError(f"the answer carries no verdict among {', '.join(VERDICTS)}")
    findings = answer.get("findings")
    if not isinstance(findings, list):
        raise ValueError("the answer carries no findings list")
    seen: set[str] = set()
    fixed: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict) or not all(
            isinstance(finding.get(k), str) for k in FINDING_TEXTS
        ):
            raise ValueError(f"a finding lacks a string {', '.join(FINDING_TEXTS)}: {finding!r}")
        fid = finding["id"].strip()
        if not fid or fid in seen:
            raise ValueError(f"a finding id is empty or repeated: {finding!r}")
        if finding.get("severity") not in SEVERITIES or finding.get("state") not in FINDING_STATES:
            raise ValueError(f"finding {fid}: severity or state is not one this store knows")
        if finding.get("kind") not in KINDS:
            raise ValueError(
                f"finding {fid}: kind {finding.get('kind')!r} is not one of {', '.join(KINDS)}"
            )
        one = dict(finding)
        if one["state"] == WAIVED and not one["quote"].strip():
            one["state"] = "open"
        if one["kind"] not in BLOCKING_KINDS:
            one["severity"] = "note"
        seen.add(fid)
        fixed.append(one)
    verdict = "not-approved" if _open_blocking(fixed) else APPROVED
    return {"verdict": verdict, "findings": fixed}


def write_review(review: dict[str, Any], cwd: Path | None = None) -> Path:
    review = {**review, **normalise_answer(review)}
    review.setdefault("reviewed_at", datetime.now(UTC).isoformat(timespec="seconds"))
    return write_json(review_path(review["stem"], cwd), review)


def rollout_answer(path: Path) -> tuple[dict[str, Any], str]:
    """A Codex session file's `session_meta` payload, and the last message its agent gave."""
    meta: dict[str, Any] = {}
    last = ""
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            record = json.loads(line) if line.strip() else {}
            payload = record.get("payload") or {}
            if record.get("type") == "session_meta":
                meta = payload
            elif record.get("type") == "event_msg" and payload.get("type") == "task_complete":
                last = payload.get("last_agent_message") or last
    return meta, last


def review_is_valid(review: dict[str, Any]) -> bool:
    """A `codex exec` session of the reviewed commit gave exactly this answer.

    Like `auditor_is_valid`, it stops a hand-written review file, not a forged session file.
    """
    raw = review.get("rollout")
    if not raw:
        return False
    try:
        meta, last = rollout_answer(Path(raw))
        answer = normalise_answer(json.loads(last))
    except (OSError, ValueError):
        return False
    return bool(
        meta.get("originator") == CODEX_ORIGINATOR
        and (meta.get("git") or {}).get("commit_hash") == head_of(review)
        and answer == {"verdict": review.get("verdict"), "findings": review.get("findings")}
    )
