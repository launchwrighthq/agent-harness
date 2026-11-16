"""List what a spec promised, then save and print what an auditor found of it on a base branch.

    ah spec audit digest [<spec> ...] [--session [<id>]] [--open] [--base <branch>] [--json]
    ah spec audit record <spec> --rows <file.json> [--level merged] [--base <branch>]
    ah spec audit table <spec>

The command judges nothing. `digest` lists the requirement rows, the operator's later chat
messages and the branches that name the spec, for a fresh auditor to check against the base
branch (`harness.baseBranch` unless `--base` names another). `record` saves that auditor's
verdict in the spec store, and `table` prints it in the one shape the operator and the
`goal-spec` Stop hook read. A QA report has no requirement rows: a named one is refused, and
`--open` and `--session` leave it out.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_harness import config
from agent_harness.spec import layout, store
from agent_harness.work import status as ws

# An amendment is judged on its words. 400 keeps a two-paragraph change whole, where the
# 200 `work_status.chat_rows` cuts at drops the second half of one.
AMENDMENT_CHARS = 400
DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
KIND_SUFFIX_RE = re.compile(r"-(?:design|analysis|recon|handover)$")
# Shorter slug words ("spec", "main", "with") sit inside most branch names.
SLUG_WORD_MIN = 5
# The file name of a subagent's meta file in `<session>/subagents/`.
AUDITOR_FILE_RE = re.compile(r"^agent-[^/]+\.meta\.json$")
LOG_BOUND = 2000
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
DECISION_ID = r"[A-Z]{1,2}\d+[a-z]?"
# A bold ID may wrap onto the next line, never across a blank one.
DECISION_RE = re.compile(
    rf"^[ \t]*(?:[-*+][ \t]+|\d+\.[ \t]+)?"
    rf"(?:\*\*(?P<bold>{DECISION_ID})\.[ \t]*"
    rf"(?P<btitle>(?:(?!\*\*)[^\n]|\n(?![ \t]*\n)){{0,400}}?)\*\*"
    rf"|#{{2,6}}[ \t]+(?P<head>{DECISION_ID})\.[ \t]+(?P<htitle>[^\n]+))",
    re.M,
)


# -- requirement rows ------------------------------------------------------------------------


def html_as_markdown(raw: str) -> str:
    """Enough of an HTML spec turned back into markdown for the three row readers.

    Headings keep their level, bold keeps its stars, and each table row lands on one line,
    so a hand-written HTML spec is read by the same rules as a markdown one.
    """
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", raw).replace("|", "\\|")
    text = re.sub(r"(?is)<tr\b.*?</tr>", lambda row: re.sub(r"\s+", " ", row.group(0)), text)
    text = re.sub(r"(?i)<h([1-6])\b[^>]*>", lambda h: "\n" + "#" * int(h.group(1)) + " ", text)
    text = re.sub(r"(?i)</h[1-6]\s*>", "\n", text)
    text = re.sub(r"(?i)</?(?:b|strong)\b[^>]*>", "**", text)
    # The whitespace goes with the tag, and `</tr>` is left to the catch-all below rather than
    # made a newline: a blank line between two rows ends a markdown table, so the reader stops.
    text = re.sub(r"(?i)\s*<tr\b[^>]*>", "\n|", text)
    text = re.sub(r"(?i)</t[dh]\s*>", " |", text)
    text = re.sub(
        r"(?i)</?(?:p|li|ul|ol|div|br|table|thead|tbody|section|pre|blockquote)\b[^>]*>", "\n", text
    )
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def unfenced_lines(text: str) -> list[str]:
    """Every line, with fenced blocks blanked: a spec quotes example tables and IDs there."""
    lines: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            lines.append("")
            continue
        lines.append("" if fenced else line)
    return lines


def clean(cell: str) -> str:
    return re.sub(r"\s+", " ", cell.replace("\\|", "|").replace("**", "")).strip()


def split_cells(line: str) -> list[str]:
    """A table row's cells. A pipe escaped or inside backticks belongs to its cell."""
    body = line.strip().removeprefix("|")
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and body[index + 1 : index + 2] == "|":
            current.append("\\|")
            index += 2
            continue
        if char == "`":
            in_code = not in_code
        if char == "|" and not in_code:
            cells.append(clean("".join(current)))
            current = []
        else:
            current.append(char)
        index += 1
    cells.append(clean("".join(current)))
    return cells


def done_means_rows(lines: list[str]) -> list[dict[str, str]]:
    for start, line in enumerate(lines):
        heading = MARKDOWN_HEADING_RE.match(line)
        if not heading or not clean(heading.group(1)).rstrip(".:").lower().endswith("done means"):
            continue
        rows: list[dict[str, str]] = []
        header_seen = False
        for below in lines[start + 1 :]:
            stripped = below.strip()
            if not stripped.startswith("|"):
                if header_seen or MARKDOWN_HEADING_RE.match(below):
                    break
                continue
            cells = split_cells(stripped)
            if not header_seen:
                if cells[0].strip("`").upper() != "ID":
                    break
                header_seen = True
            elif not all(SEPARATOR_CELL_RE.match(cell) for cell in cells) and cells[0]:
                padded = [*cells, "", "", ""]
                rows.append(
                    {
                        "id": padded[0],
                        "requirement": padded[1],
                        "how": padded[2],
                        "level": padded[3],
                    }
                )
        if rows:
            return rows
    return []


def decision_rows(lines: list[str]) -> list[dict[str, str]]:
    text = "\n".join(lines)
    rows: dict[str, dict[str, str]] = {}
    for found in DECISION_RE.finditer(text):
        rid = found.group("bold") or found.group("head")
        title = found.group("btitle") if found.group("bold") else found.group("htitle")
        if not title.strip():
            # `**D4.** The title follows the bold` puts the words after the closing stars.
            title = text[found.end() :].split("\n", 1)[0]
        rows.setdefault(rid, {"id": rid, "requirement": clean(title), "how": "", "level": ""})
    return list(rows.values())


def heading_rows(lines: list[str]) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for line in lines:
        heading = ws.HEADING_RE.match(line)
        if heading:
            rid = heading.group(1)
            rows.setdefault(
                rid, {"id": rid, "requirement": clean(heading.group(2)), "how": "", "level": ""}
            )
    return list(rows.values())


def requirement_rows(spec: Path) -> tuple[str, list[dict[str, str]]]:
    """The rows an auditor checks, and which of the three shapes they were read from."""
    raw = spec.read_text(encoding="utf-8", errors="replace")
    lines = unfenced_lines(html_as_markdown(raw) if spec.suffix == ".html" else raw)
    for source, reader in (
        ("done-means", done_means_rows),
        ("acceptance", acceptance_rows),
        ("decision-ids", decision_rows),
        ("headings", heading_rows),
    ):
        rows = reader(lines)
        if rows:
            return source, rows
    return "none", []


def acceptance_rows(lines: list[str]) -> list[dict[str, str]]:
    """Read the acceptance tables of a spec without rewriting the document."""
    rows: list[dict[str, str]] = []
    active_level: int | None = None
    header: list[str] | None = None
    for line in lines:
        heading = MARKDOWN_HEADING_RE.match(line)
        if heading:
            level = len(line) - len(line.lstrip("#"))
            if "acceptance" in heading.group(1).lower():
                active_level, header = level, None
            elif active_level is not None and level <= active_level:
                active_level, header = None, None
            continue
        if active_level is None:
            continue
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = split_cells(line)
        if header is None:
            header = cells
            continue
        if all(SEPARATOR_CELL_RE.match(cell) for cell in cells):
            continue
        if len(cells) >= 2 and cells[0]:
            rows.append(
                {
                    "id": f"R{len(rows) + 1}",
                    "requirement": f"{cells[0]}: {cells[1]}",
                    "how": "",
                    "level": "",
                }
            )
    return rows


# -- the session -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    at: datetime
    text: str
    typed_while_busy: bool


def operator_messages(records: list[dict[str, Any]]) -> list[Message]:
    """What the operator typed, as prompts and as messages queued while the agent was busy.

    The same two sources `work_status.chat_rows` reads, without a peer's message, and cut
    at `AMENDMENT_CHARS` rather than the 200 that report prints.
    """
    said: list[Message] = []
    for record in records:
        if ws.is_human_prompt(record):
            content = (record.get("message") or {}).get("content")
            said.append(
                Message(record["_ts"], ws.excerpt(ws.text_of(content), AMENDMENT_CHARS), False)
            )
            continue
        if record.get("type") != "attachment":
            continue
        attachment = record.get("attachment") or {}
        if (
            attachment.get("type") == "queued_command"
            and (attachment.get("origin") or {}).get("kind") == "human"
        ):
            text = ws.excerpt(ws.text_of(attachment.get("prompt")), AMENDMENT_CHARS) or "(no text)"
            said.append(Message(record["_ts"], text, True))
    return said


def first_named(
    spec: Path, mentions: dict[str, tuple[datetime, datetime]], records: list[dict[str, Any]]
) -> datetime | None:
    """When the session first named the spec, by path or by its bare stem.

    `spec_mentions` matches a `.md` path only, so an HTML spec, or a stem typed after
    `/lw-goal-spec`, is found by the stem in what the operator or the assistant said.
    """
    times = [first for path, (first, _) in mentions.items() if Path(path).name == spec.name]
    for record in records:
        spoken = record.get("type") == "assistant" or ws.is_human_prompt(record)
        if spoken and spec.stem in ws.text_of((record.get("message") or {}).get("content")):
            times.append(record["_ts"])
            break
    return min(times) if times else None


def local_clock(when: datetime | str | None) -> str:
    if isinstance(when, str):
        when = ws.parse_ts(when)
    return when.astimezone().strftime("%Y-%m-%d %H:%M") if when else "unknown"


# -- git -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Branch:
    name: str
    ahead: int | None
    worktree: str | None


def local_branches(root: Path, base: str) -> list[Branch]:
    """Every local branch, its commits ahead of `base`, and the worktree holding it.

    One `for-each-ref` and one `worktree list`, however many specs the digest covers.
    """
    refs = ws._git(
        ["for-each-ref", "refs/heads", f"--format=%(refname:short)%09%(ahead-behind:{base})"], root
    )
    held: dict[str, str] = {}
    path: str | None = None
    for line in (ws._git(["worktree", "list", "--porcelain"], root) or "").splitlines():
        if line.startswith("worktree "):
            path = line.removeprefix("worktree ")
        elif line.startswith("branch refs/heads/") and path:
            held[line.removeprefix("branch refs/heads/")] = path
    branches: list[Branch] = []
    for line in (refs or "").splitlines():
        name, _, counts = line.partition("\t")
        ahead = counts.split()[0] if counts.split() else ""
        branches.append(Branch(name, int(ahead) if ahead.isdigit() else None, held.get(name)))
    return branches


def unmerged_mentions(root: Path, specs: list[Path], base: str) -> dict[str, set[str]]:
    """Per stem, the local branches holding a commit `base` lacks that names it or touches it.

    One log bounded at `LOG_BOUND` commits. Only unmerged commits are walked: a commit `base`
    already holds is the auditor's to find there, and `--source` would credit it to any
    branch cut after it.
    """
    out = ws._git(
        [
            "log",
            "--source",
            "--name-only",
            f"--max-count={LOG_BOUND}",
            "--format=%x1e%S%x1f%B%x1f",
            "--branches",
            "--not",
            base,
            "--",
        ],
        root,
    )
    found: dict[str, set[str]] = {spec.stem: set() for spec in specs}
    for chunk in (out or "").split("\x1e"):
        source, _, rest = chunk.strip().partition("\x1f")
        # `--branches` prints a short name; only a ref outside it, such as a stash, keeps `refs/`.
        source = source.removeprefix("refs/heads/")
        if not source or source.startswith("refs/"):
            continue
        for spec in specs:
            if spec.stem in rest:
                found[spec.stem].add(source)
    return found


def slug_words(stem: str) -> set[str]:
    slug = KIND_SUFFIX_RE.sub("", DATE_PREFIX_RE.sub("", stem))
    return {word for word in slug.split("-") if len(word) >= SLUG_WORD_MIN}


def related_branches(
    spec: Path, branches: list[Branch], by_commit: set[str], base: str
) -> list[dict[str, Any]]:
    words = slug_words(spec.stem)
    related: list[dict[str, Any]] = []
    for branch in branches:
        if branch.name == base:
            continue
        hits = sum(word in branch.name.lower() for word in words)
        why = [
            *(["branch name"] if words and hits >= min(2, len(words)) else []),
            *(["commits"] if branch.name in by_commit else []),
        ]
        if not why:
            continue
        dirty = None
        if branch.worktree:
            status = ws._git(["status", "--porcelain"], Path(branch.worktree))
            dirty = None if status is None else bool(status)
        related.append(
            {
                "name": branch.name,
                "ahead": branch.ahead,
                "merged": branch.ahead == 0,
                "worktree": branch.worktree,
                "dirty": dirty,
                "why": why,
            }
        )
    return related


def checkout_of(spec: Path) -> Path | None:
    top = ws._git(["rev-parse", "--show-toplevel"], spec.parent)
    return Path(top).resolve() if top else None


def relative_spec(spec: Path) -> str:
    root = checkout_of(spec)
    try:
        return spec.resolve().relative_to(root).as_posix() if root else str(spec)
    except ValueError:
        return str(spec)


def is_tracked(spec: Path) -> bool:
    root = checkout_of(spec)
    return (
        root is not None
        and ws._git(["ls-files", "--error-unmatch", str(spec.resolve())], root) is not None
    )


# -- digest ----------------------------------------------------------------------------------


@dataclass
class SpecDigest:
    path: str
    spec: str
    stem: str
    status: str | None
    tracked: bool
    source: str
    rows: list[dict[str, str]]
    first_named: str | None = None
    amendments: list[dict[str, Any]] | None = None
    branches: list[dict[str, Any]] = field(default_factory=list)


def open_specs() -> list[Path]:
    """Every unsettled spec directly in a spec folder of each checkout, first root wins."""
    chosen: dict[str, Path] = {}
    for root in store.checkout_roots():
        for name in layout.for_root(root).active("spec"):
            folder = root / name
            markdown = list(folder.glob("*.md"))
            pages = [p for p in folder.glob("*.html") if not p.with_suffix(".md").exists()]
            for spec in sorted([*markdown, *pages]):
                if spec.stem not in chosen and store.status_of(spec) not in store.SETTLED:
                    chosen[spec.stem] = spec
    return sorted(chosen.values(), key=lambda spec: spec.name)


def checked_session_id(chosen: str) -> str:
    """`chosen` when it is one session id. A glob character or a path separator would let a
    lookup under the projects root read another session's files."""
    if any(char in chosen for char in ws.REFUSED_IN_SESSION_ID):
        raise SystemExit(f"ah spec audit: --session takes one session id, not {chosen!r}")
    return chosen


def session_id_from(value: str | None) -> str | None:
    if value is None:
        return None
    chosen = value or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not chosen:
        raise SystemExit(
            "ah spec audit: --session has no value and CLAUDE_CODE_SESSION_ID is not set"
        )
    return checked_session_id(chosen)


def base_commit(base: str) -> str:
    head = store.branch_commit(base)
    if head is None:
        raise SystemExit(f"ah spec audit: the branch {base!r} does not resolve")
    return head


def refuse_qa_report(spec: Path) -> None:
    if store.qa_report(spec):
        print(f"ah spec audit: {spec} is a QA report, which has no rows to audit", file=sys.stderr)
        raise SystemExit(1)


def build_digest(args: argparse.Namespace) -> dict[str, Any]:
    head = base_commit(args.base)
    specs: list[Path] = []
    for arg in args.specs:
        resolved = store.resolve_spec(arg)
        if resolved is None:
            print(f"ah spec audit: no spec matches {arg}", file=sys.stderr)
            raise SystemExit(1)
        refuse_qa_report(resolved)
        specs.append(resolved)

    session = session_id_from(args.session)
    records: list[dict[str, Any]] = []
    mentions: dict[str, tuple[datetime, datetime]] = {}
    missing: list[str] = []
    if session:
        transcripts = ws.find_transcripts(session, args.projects_root)
        if not transcripts:
            print(
                f"ah spec audit: no transcript for session {session} under {args.projects_root}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        records = ws.load_records(transcripts)
        mentions = ws.spec_mentions(records, layout.for_root(config.find_root()).mention_re())
        for named in sorted(mentions, key=lambda path: mentions[path][0]):
            resolved = store.resolve_spec(named)
            if resolved is None:
                missing.append(named)
            elif not store.qa_report(resolved):
                specs.append(resolved)
    if args.open:
        specs.extend(open_specs())

    unique: dict[str, Path] = {}
    for spec in specs:
        unique.setdefault(spec.stem, spec)
    chosen = list(unique.values())

    root = Path.cwd()
    branches = local_branches(root, args.base)
    by_commit = unmerged_mentions(root, chosen, args.base) if chosen else {}
    said = operator_messages(records) if session else []
    digests: list[SpecDigest] = []
    for spec in chosen:
        source, rows = requirement_rows(spec)
        entry = SpecDigest(
            path=str(spec),
            spec=relative_spec(spec),
            stem=spec.stem,
            status=store.status_of(spec),
            tracked=is_tracked(spec),
            source=source,
            rows=rows,
            branches=related_branches(spec, branches, by_commit.get(spec.stem, set()), args.base),
        )
        if session:
            first = first_named(spec, mentions, records)
            entry.first_named = first.isoformat() if first else None
            entry.amendments = [
                {
                    "at": m.at.isoformat(),
                    "local": local_clock(m.at),
                    "text": m.text,
                    "typed_while_busy": m.typed_while_busy,
                }
                for m in said
                if first is not None and m.at > first
            ]
        digests.append(entry)
    return {
        "base": args.base,
        "head": head,
        "session": session,
        "specs": [asdict(entry) for entry in digests],
        "named_but_missing": missing,
    }


def cell(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().replace("|", "\\|")


def render_digest(digest: dict[str, Any]) -> str:
    lines = [f"Base: {digest['base']} at {digest['head']}"]
    if digest["session"]:
        lines.append(f"Session: {digest['session']}")
    for entry in digest["specs"]:
        tracked = "yes" if entry["tracked"] else "no"
        lines += [
            "",
            f"## {Path(entry['path']).name}",
            f"Path: {entry['path']}",
            f"Status: {entry['status'] or 'none'}. Tracked by git: {tracked}. "
            f"Rows from: {entry['source']} ({len(entry['rows'])}).",
            "",
            "| ID | Requirement | How to check | Level |",
            "|---|---|---|---|",
            *(
                f"| {cell(r['id'])} | {cell(r['requirement'])} | "
                f"{cell(r['how'])} | {cell(r['level'])} |"
                for r in entry["rows"]
            ),
        ]
        if entry["amendments"] is not None:
            lines.append("")
            if entry["first_named"] is None:
                lines.append("Amendment candidates: the session never named this spec.")
            else:
                lines.append(
                    f"Amendment candidates, operator messages after the spec was first named "
                    f"at {local_clock(entry['first_named'])}:"
                )
                lines += [
                    f"- {m['local']}"
                    f"{', typed while busy' if m['typed_while_busy'] else ''}: {m['text']}"
                    for m in entry["amendments"]
                ] or ["- none"]
        lines += ["", "Related branches:"]
        for branch in entry["branches"]:
            ahead = (
                "ahead unknown"
                if branch["ahead"] is None
                else f"{branch['ahead']} ahead of {digest['base']}"
            )
            merged = "merged" if branch["merged"] else "not merged"
            where = "no worktree"
            if branch["worktree"]:
                state = {True: "dirty", False: "clean", None: "state unknown"}[branch["dirty"]]
                where = f"worktree {branch['worktree']}, {state}"
            lines.append(
                f"- {branch['name']}: {ahead}, {merged}; {where}. "
                f"Found by: {', '.join(branch['why'])}."
            )
        if not entry["branches"]:
            lines.append("- none")
    if digest["named_but_missing"]:
        lines += [
            "",
            "Named in the session but not on disk: " + ", ".join(digest["named_but_missing"]),
        ]
    if not digest["specs"]:
        lines += ["", "No spec to digest."]
    return "\n".join(lines)


# -- record and table ------------------------------------------------------------------------


def commit_time(commit: str) -> int:
    """The committer time of `commit`, in seconds since the epoch."""
    found = store.git(["show", "-s", "--format=%ct", commit], None)
    if not found or not found.isdigit():
        raise SystemExit(f"ah spec audit: cannot read the commit time of {commit}")
    return int(found)


def codex_sessions() -> Path:
    """The folder where Codex writes its session rollouts: `$CODEX_HOME/sessions`."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"


def auditor_problem(
    auditor: Path, session: str | None, stem: str, projects_root: Path, since: int
) -> str | None:
    """Why `auditor` cannot back the rows of `stem`, or None.

    A Claude auditor is the `agent-<id>.meta.json` of a subagent of this session, at
    `<projects_root>/<project folder>/<session>/subagents/` after links are followed. Its
    description starts with the auditor prefix and names the stem. A Codex auditor is a rollout
    under `$CODEX_HOME/sessions/`; `store.auditor_is_valid` checks its stem and commit. Either
    file must change after `since`, the commit time of the audited base, so an older audit of
    another commit cannot back these rows.
    """
    real = auditor.resolve()
    if real.suffix == ".jsonl":
        sessions = codex_sessions().resolve()
        if not real.is_relative_to(sessions):
            return f"the Codex rollout {real} is not under {sessions}"
    else:
        if not session:
            return f"the auditor {real} needs a session id to check where it sits"
        root = projects_root.resolve()
        parts = real.relative_to(root).parts if real.is_relative_to(root) else ()
        if (
            len(parts) != 4
            or parts[1:3] != (session, "subagents")
            or not AUDITOR_FILE_RE.match(parts[3])
        ):
            return (
                f"the auditor {real} is not the agent-<id>.meta.json of a subagent of session "
                f"{session} under {root}/<project folder>/{session}/subagents/"
            )
        try:
            loaded = json.loads(real.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return f"the auditor {real} is not a readable meta.json: {error}"
        description = str(loaded.get("description", "")) if isinstance(loaded, dict) else ""
        if not description_names(description, stem):
            return (
                f"the auditor {real} has the description {description!r}, which does not start "
                f"with `{store.AUDITOR_PREFIX}` and name the stem {stem}"
            )
    try:
        stamp = real.stat().st_mtime
    except OSError as error:
        return f"cannot read the auditor {real}: {error.strerror or error}"
    if stamp <= since:
        return (
            f"the auditor {real} last changed at {local_clock(datetime.fromtimestamp(stamp))}, "
            f"not after the audited commit at {local_clock(datetime.fromtimestamp(since))}"
        )
    return None


def description_names(description: str, stem: str) -> bool:
    """True when a subagent description starts with the auditor prefix and names `stem`."""
    lowered = description.lower()
    return lowered.startswith(store.AUDITOR_PREFIX.lower()) and bool(
        re.search(rf"(?<![\w-]){re.escape(stem.lower())}(?![\w-])", lowered)
    )


def find_auditor(session: str | None, stem: str, projects_root: Path, since: int) -> Path | None:
    """The auditor subagent's own `meta.json`, found by the description it was spawned with.

    A subagent's Bash carries the parent's `CLAUDE_CODE_SESSION_ID` and no id of its own, so
    the file cannot be named directly. Only a file that `auditor_problem` accepts counts, so an
    auditor of another stem never backs these rows. The newest one wins.
    """
    if not session:
        return None
    marked: list[tuple[float, str]] = []
    for meta in projects_root.glob(f"*/{session}/subagents/agent-*.meta.json"):
        try:
            stamp = meta.stat().st_mtime
        except OSError:
            # A peer's meta file that went away. It is not this auditor's, which has existed
            # since the auditor started.
            continue
        if auditor_problem(meta, session, stem, projects_root, since) is None:
            marked.append((stamp, str(meta)))
    return Path(max(marked)[1]) if marked else None


def load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{path} is not a readable JSON rows file: {error}") from error
    rows = loaded.get("rows") if isinstance(loaded, dict) else loaded
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} holds neither a list of rows nor an object with `rows`")
    return rows


def check_rows(spec: Path, rows: list[dict[str, Any]]) -> None:
    """Refuse a row set the Stop hook could not trust: no row, a row without evidence, or a
    Done means ID that no row answers. A skipped row would read as no open row."""
    if not rows:
        raise ValueError("the rows file holds no row")
    for row in rows:
        if not str(row.get("evidence", "")).strip():
            raise ValueError(
                f"row {row.get('id', '?')} names no evidence: give the file and line, the "
                "commit, or the search that found nothing"
            )
    source, expected = requirement_rows(spec)
    if source != "done-means":
        return
    given = {str(row.get("id", "")).strip() for row in rows}
    missing = [row["id"] for row in expected if row["id"] not in given]
    if missing:
        raise ValueError(f"the Done means rows have no audit row: {', '.join(missing)}")


def record(args: argparse.Namespace) -> int:
    spec = store.resolve_spec(args.spec)
    if spec is None:
        print(f"ah spec audit: no spec matches {args.spec}", file=sys.stderr)
        return 1
    refuse_qa_report(spec)
    session = (
        args.session
        or os.environ.get("CLAUDE_CODE_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
    )
    if session:
        checked_session_id(session)
    head = base_commit(args.base)
    since = commit_time(head)
    if args.auditor:
        named = Path(args.auditor).resolve()
        if problem := auditor_problem(named, session, spec.stem, args.projects_root, since):
            print(f"ah spec audit: {problem}; nothing recorded", file=sys.stderr)
            return 2
        auditor: Path | None = named
    else:
        auditor = find_auditor(session, spec.stem, args.projects_root, since)
    audit = {
        "spec": relative_spec(spec),
        "stem": spec.stem,
        "base": args.base,
        "head": head,
        "session": session,
        "auditor": str(auditor) if auditor else None,
        "level": args.level,
    }
    try:
        audit["rows"] = load_rows(Path(args.rows))
        check_rows(spec, audit["rows"])
        path = store.write_audit(audit)
    except ValueError as error:
        print(f"ah spec audit: {error}", file=sys.stderr)
        return 2
    if not store.auditor_is_valid(audit):
        print(
            f"ah spec audit: warning: no `{store.AUDITOR_PREFIX}` subagent file backs this audit, "
            "so the goal-spec Stop hook will not accept it",
            file=sys.stderr,
        )
    print(path)
    print(render_table(store.load_audit(spec.stem) or audit))
    return 0


def render_table(audit: dict[str, Any]) -> str:
    rows = list(audit.get("rows", []))
    states = [row.get("state") for row in rows]
    done = states.count("done")
    deferred = [row for row in rows if row.get("state") == "deferred-by-operator"]
    opened = [row for row in rows if row.get("state") in store.OPEN_STATES]
    blocked = [row for row in rows if row.get("state") == store.BLOCKED]
    counts = f"Done: {done} of {len(rows)}. Not done: {len(opened)}. Needs you: {len(blocked)}."
    if deferred:
        counts += f" Deferred by you: {len(deferred)}."
    base = store.base_of(audit)
    sha = (store.head_of(audit) or "unknown")[:9]
    audited = f"Audited {base} {sha} at {local_clock(audit.get('audited_at'))}."
    if store.branch_commit(base) != store.head_of(audit):
        audited += f" {base} has moved since: re-audit."

    ordered = [
        *((row, row.get("evidence")) for row in opened),
        *((row, row.get("question")) for row in blocked),
        *((row, row.get("quote")) for row in deferred),
        *((row, row.get("evidence")) for row in rows if row.get("state") == "done"),
    ]
    waiting = store.open_ids(audit) + store.blocked_ids(audit)
    status = (
        f"Status line: in-progress - open: {', '.join(waiting)}"
        if waiting
        else f"Status line: implemented - {done} rows done, audited at {base} {sha}"
    )
    return "\n".join(
        [
            f"Spec: {Path(str(audit.get('spec', audit.get('stem', '')))).name}",
            counts,
            audited,
            "",
            "| ID | Requirement | State | Evidence |",
            "|---|---|---|---|",
            *(
                f"| {cell(row.get('id'))} | {cell(row.get('requirement'))} | "
                f"{cell(row.get('state'))} | {cell(say)} |"
                for row, say in ordered
            ),
            "",
            status,
        ]
    )


def table(args: argparse.Namespace) -> int:
    stem = store.stem_of(store.resolve_spec(args.spec) or args.spec)
    audit = store.load_audit(stem)
    if audit is None:
        print(f"no audit for {stem}: run /lw-spec-audit", file=sys.stderr)
        return 1
    print(render_table(audit))
    return 0


def main(argv: list[str] | None = None) -> int:
    doc = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(prog="ah spec audit", description=doc)
    commands = parser.add_subparsers(dest="command", required=True)

    digest = commands.add_parser("digest", help="list rows, amendments and branches for an auditor")
    digest.add_argument("specs", nargs="*", help="a spec path, basename or stem")
    digest.add_argument(
        "--session",
        nargs="?",
        const="",
        help="add every spec this session named; default $CLAUDE_CODE_SESSION_ID",
    )
    digest.add_argument(
        "--open", action="store_true", help="add every unsettled spec in the active folder"
    )
    digest.add_argument("--base", help="the branch the auditor reads; default harness.baseBranch")
    digest.add_argument("--json", action="store_true")
    digest.add_argument("--projects-root", type=Path, default=ws.PROJECTS_ROOT)

    saved = commands.add_parser("record", help="save an auditor's rows, then print the table")
    saved.add_argument("spec")
    saved.add_argument(
        "--rows", required=True, help="a JSON list of rows, or an object with `rows`"
    )
    # The done ladder of `.claude/rules/lw/operator-report.md`, rung for rung.
    saved.add_argument(
        "--level", choices=("built", "merged", "pushed", "deployed", "proven"), default="merged"
    )
    saved.add_argument("--base", help="the branch the auditor read; default harness.baseBranch")
    saved.add_argument("--session", help="default $CLAUDE_CODE_SESSION_ID")
    saved.add_argument(
        "--auditor",
        help="the auditor's subagent meta.json or Codex rollout, checked as a found one is; "
        "found by description when omitted",
    )
    saved.add_argument("--projects-root", type=Path, default=ws.PROJECTS_ROOT)

    shown = commands.add_parser("table", help="print a saved audit")
    shown.add_argument("spec")

    args = parser.parse_args(argv)
    if getattr(args, "base", "") is None:
        args.base = store.default_base()
    if args.command == "record":
        return record(args)
    if args.command == "table":
        return table(args)
    if not (args.specs or args.session is not None or args.open):
        parser.error("digest takes a spec, --session or --open")
    built = build_digest(args)
    print(json.dumps(built, indent=2, ensure_ascii=False) if args.json else render_digest(built))
    return 0
