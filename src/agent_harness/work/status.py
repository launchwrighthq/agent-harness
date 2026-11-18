"""Print what one Claude Code session is doing, read from its own transcript files.

    ah work-status [--session <id>] [--since 12h] [--all] [--spec <path>] [--json]
    ah work-status --repo [--json]

The transcript keeps what compaction drops and what a busy agent never saw (docs/work.md). A
result body can echo a token and the digest is pasted into a chat, so only a spawn's tool result
is read: one line of an agent's report, a workflow's summary, or an error. The user's own shell
commands and their output are dropped for the same reason. The spec folders come from
`[specs] dirs`, the audits from the spec store, the base from config.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from agent_harness import config
from agent_harness.spec import layout, store

PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# One transcript record, as JSON gives it, with the `_ts` time this module adds.
Record = dict[str, Any]

NOT_AUDITED = "not audited"
STALE = "stale"
ALL_DONE = "all done"
# A settled spec in one of these states owes nothing the audit knows of, so the Specs named
# section may fold it into one count line. Every other state keeps its own line.
FOLDABLE_AUDITS = (ALL_DONE, NOT_AUDITED)

# The client files an interrupt as a user record. The next prompt carries the request.
INTERRUPT = "[Request interrupted by user"

# Claude Code files slash commands, their output, agent notifications and the user's own shell
# commands with their output as user records too. A prompt opening with one is not a request,
# and shell output can echo a token, so the digest drops it.
CHAT_MARKERS = (
    "<task-notification>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "<command-name>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "<system-reminder>",
    INTERRUPT,
)

# The body of a file edit or write is file text. A spec path in test code names no spec.
BODY_KEYS = frozenset({"content", "old_string", "new_string", "edits", "new_source"})

# The time a record carries when it has none of its own and nothing precedes it.
NO_TIME = datetime.min.replace(tzinfo=UTC)

# A spec heads its sections `## 5.4 Title`, a plan heads its tasks `### Task 3: Title`.
HEADING_RE = re.compile(r"^#{2,3}\s+(?:Task\s+)?(\d+(?:\.\d+)*[a-z]?)[.:]?\s+(.+?)\s*$")

# A section's first sentence has to be prose. A table row, a quote and a list item each
# say something about the section without saying what it is.
SECTION_PROSE_SKIP = ("|", ">", "-", "*")
ORDERED_ITEM_RE = re.compile(r"^\d+\.")

WORKTREE_ABS_RE = re.compile(r"(/[^\s'\"`]*?/\.claude/worktrees/[\w-]+(?:\.[\w-]+)*)")
WORKTREE_RE = re.compile(r"\.claude/worktrees/([\w-]+(?:\.[\w-]+)*)")
SECTION_RE = re.compile(r"(?i)\b(?:section|part|task|fix|step)\s*#?\s*(\d+(?:\.\d+)*[a-z]?)\b")
NOTIFICATION_RE = re.compile(r"<(task-id|status|summary|result)>(.*?)</\1>", re.S)
NOTIFICATION_BLOCK_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
EXIT_CODE_RE = re.compile(r"exit code (\d+)")

# An agent id is `a` and 16 hex digits. A background command's id is not, and that is how
# a notification for a task this session never saw spawned still lands in the right list.
AGENT_ID_RE = re.compile(r"^a[0-9a-f]{16}$")

# A row in one of these states needs no further work, so the recency rule may hide it.
FINISHED = frozenset({"reported", "landed", "merged", "stopped", "failed"})

SINCE_RE = re.compile(r"^(\d+)([mhd])$")
SINCE_UNITS = {"m": "minutes", "h": "hours", "d": "days"}

# With no spec to measure from, the window opens this far back.
DEFAULT_WINDOW = timedelta(hours=4)

# How long after an agent's last sighting its commits still count as its own. A completion
# notification can arrive before the commit exists.
LANDING_GRACE = timedelta(minutes=2)

# A long session carries many prompts inside the window. Past this many the report stops
# being a page, and the older ones are the ones already dealt with.
CHAT_CAP = 30

# A session id is a UUID. Any of these in one would make `--session` a glob, and a glob
# folds every transcript it matches into one digest that describes no session at all.
REFUSED_IN_SESSION_ID = ("*", "?", "[", "/", "\\")


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    name: str | None
    cwd: str | None
    worktree: str | None
    started: datetime | None
    last: datetime | None
    compactions: int
    goal: str | None
    goal_met: bool | None
    human_prompts: int


@dataclass(frozen=True)
class ChatRow:
    at: datetime
    text: str
    source: str
    typed_while_busy: bool
    answered: bool


@dataclass
class TaskRow:
    """One agent, background command or workflow the session started.

    Mutable, because what is known about a task arrives in pieces: the spawn names it, the
    result carries its id, each notification updates its state, and the subagent file holds
    the report an async spawn never returns.
    """

    task_id: str | None
    kind: str
    description: str
    model: str | None
    agent_type: str | None
    spec: str | None
    section: str | None
    worktree: str | None
    spawned_at: datetime
    last_seen: datetime
    state: str = "running"
    report: str = ""
    exit_code: int | None = None
    notifications: int = 0
    dirty: bool = False


@dataclass
class SpecSection:
    """One numbered section of the spec, and the agents whose prompts named its number.

    Mutable, because a section is complete only after two later passes: the first sentence
    comes from the lines under the heading, and the agents from the task rows.
    """

    number: str
    title: str
    first_sentence: str = ""
    agents: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SpecInfo:
    """The spec or plan the session is working from.

    `path` is the text the transcript named, which is what the user recognises, and
    `resolved` is the file that was opened. Keeping both is what lets the report say the
    file is missing rather than drop the spec.
    """

    path: str
    resolved: Path | None
    kind: str
    status: str | None
    first_mention: datetime
    last_mention: datetime
    sections: list[SpecSection]


@dataclass(frozen=True)
class NamedSpec:
    """One spec or plan the session named, and what its saved audit says is left.

    The Spec section reads the newest path alone, and a session can name many. Without this
    row an older spec with unbuilt work has no line. `status` is `missing` with no file.
    """

    path: str
    basename: str
    resolved: Path | None
    status: str | None
    first_mention: datetime
    last_mention: datetime
    audit: str
    open_ids: list[str] = field(default_factory=list)
    blocked_ids: list[str] = field(default_factory=list)


@dataclass
class Digest:
    """Everything the report prints.

    The whole shape is declared here, so a `--json` reader sees the same keys whether or
    not a section found anything.
    """

    session: SessionInfo
    # The checkout that holds the session's cwd when it is not the repository that the command
    # runs in. None for this repository or an unknown cwd.
    repository: str | None = None
    spec: SpecInfo | None = None
    agents: list[TaskRow] = field(default_factory=list)
    background: list[TaskRow] = field(default_factory=list)
    chat: list[ChatRow] = field(default_factory=list)
    peers: list[ChatRow] = field(default_factory=list)
    anchor: datetime | None = None
    hidden_count: int = 0
    # How many chat rows the window held before the cap took the newest of them. The report
    # says so, because a row dropped by the cap is not a row the recency rule hid.
    chat_total: int = 0
    specs_named: list[NamedSpec] = field(default_factory=list)


def find_transcripts(session_id: str, root: Path) -> list[Path]:
    """A session that enters a worktree keeps writing under a second slug directory.

    Reading one slug loses every record written after the move.
    """
    return sorted(root.glob(f"*/{session_id}.jsonl"))


def find_subagent_dirs(session_id: str, root: Path) -> list[Path]:
    return sorted(root.glob(f"*/{session_id}/subagents"))


def parse_ts(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Every time in the merge has to be aware, or sorting raises on the first mixed pair.
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def load_records(paths: list[Path]) -> list[Record]:
    """Merge a session's transcript files into one list ordered by time.

    Title records carry no timestamp, so each undated record takes the time of the record
    before it in its own file. Without that they all sort to the front, away from the turn
    they belong to. The sort is stable, so records within one file keep their order.
    """
    records: list[Record] = []
    for path in paths:
        previous = NO_TIME
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                stamp = parse_ts(record.get("timestamp"))
                record["_ts"] = previous if stamp is None else stamp
                previous = record["_ts"]
                records.append(record)
    records.sort(key=lambda record: record["_ts"])
    return records


def text_of(content: Any) -> str:
    """The words in a message, with every non-text block left behind."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def has_tool_result(content: Any) -> bool:
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )


def is_human_prompt(record: Record) -> bool:
    """What the user typed, told apart from the rest of what is filed as a user record.

    Tool results, slash commands, agent notifications and the post-compaction summary all
    arrive as `user` records. Take one for a prompt and a tool result body reaches the
    output.
    """
    if record.get("type") != "user" or record.get("userType") != "external":
        return False
    # A loaded skill's text, and anything else the harness files under the user's name, arrives
    # as an external user record carrying `isMeta`. The user never typed it.
    if record.get("isCompactSummary") or record.get("isMeta"):
        return False
    content = (record.get("message") or {}).get("content")
    if has_tool_result(content):
        return False
    return not text_of(content).strip().startswith(CHAT_MARKERS)


def excerpt(text: str, limit: int = 200) -> str:
    """One line of at most `limit` characters. A single prompt can run to a page."""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return excerpt(line)
    return ""


def first_sentence(text: str) -> str:
    """The opening sentence of a spec section, cut at the first sentence break.

    A section's first paragraph runs to a page and the report gives it one column, so a
    cut at the period keeps the claim and drops the argument for it.
    """
    cut = text.find(". ")
    return excerpt(text if cut == -1 else text[: cut + 1], 160)


def session_info(session_id: str, records: list[Record]) -> SessionInfo:
    """The session's own identity: what it is called, where it runs, how long it has run."""
    chosen_name: str | None = None
    ai_name: str | None = None
    cwd: str | None = None
    worktree: str | None = None
    relocated: str | None = None
    started: datetime | None = None
    last: datetime | None = None
    compactions = 0
    goal: str | None = None
    goal_met: bool | None = None
    prompts = 0

    for record in records:
        kind = record.get("type")
        if kind == "custom-title":
            chosen_name = record.get("customTitle") or chosen_name
        elif kind == "agent-name":
            chosen_name = record.get("agentName") or chosen_name
        elif kind == "ai-title":
            ai_name = record.get("aiTitle") or ai_name
        elif kind == "relocated":
            relocated = record.get("relocatedCwd") or relocated
        elif kind == "worktree-state":
            worktree = (record.get("worktreeSession") or {}).get("worktreePath") or worktree
        elif kind == "system" and record.get("subtype") == "compact_boundary":
            compactions += 1
        elif kind == "attachment":
            attachment = record.get("attachment") or {}
            if attachment.get("type") == "goal_status":
                goal = attachment.get("condition")
                goal_met = attachment.get("met")
        if isinstance(record.get("cwd"), str):
            cwd = record["cwd"]
        stamp = record.get("_ts")
        if stamp is not None and stamp != NO_TIME:
            started = stamp if started is None else started
            last = stamp
        if is_human_prompt(record):
            prompts += 1

    # A name set on the session outranks the generated one whenever both exist.
    return SessionInfo(
        session_id=session_id,
        name=chosen_name or ai_name,
        cwd=cwd,
        worktree=worktree or relocated,
        started=started,
        last=last,
        compactions=compactions,
        goal=goal,
        goal_met=goal_met,
        human_prompts=prompts,
    )


def _answered_between(records: list[Record], start: int, end: int) -> bool:
    for record in records[start:end]:
        if record.get("type") != "assistant":
            continue
        if text_of((record.get("message") or {}).get("content")).strip():
            return True
    return False


def _queued_text(prompt: Any) -> str:
    """The words in a queued message, which arrives as content blocks when it carries an image.
    An image with no words still gets a row, because the user did send something."""
    return excerpt(text_of(prompt)) or "(no text)"


def chat_rows(records: list[Record]) -> list[ChatRow]:
    """Every message addressed to this session, whether or not it became a prompt.

    A message typed while the agent is busy arrives as a `queued_command` attachment, and
    most such messages never appear again as a top-level prompt. Reading the prompts alone
    is how a request goes missing.
    """
    seeds: list[tuple[int, datetime, str, str, bool]] = []
    peers_seen: set[str] = set()
    for index, record in enumerate(records):
        at = record.get("_ts")
        if at is None:
            continue
        if is_human_prompt(record):
            text = excerpt(text_of((record.get("message") or {}).get("content")))
            seeds.append((index, at, text, "human", False))
            continue
        # A peer message that finds the session idle is a meta user record with its origin.
        # One that finds it busy is a queued attachment.
        if record.get("type") == "user":
            origin, prompt = record.get("origin") or {}, None
        elif record.get("type") == "attachment":
            attachment = record.get("attachment") or {}
            if attachment.get("type") != "queued_command":
                continue
            origin, prompt = attachment.get("origin") or {}, attachment.get("prompt")
            if text_of(prompt).strip().startswith(CHAT_MARKERS):
                continue
            if origin.get("kind") == "human":
                seeds.append((index, at, _queued_text(prompt), "human", True))
                continue
        else:
            continue
        if origin.get("kind") != "peer":
            continue
        body = origin.get("body") or prompt
        key = str(origin.get("msg_id") or text_of(body))
        if key in peers_seen:
            continue
        peers_seen.add(key)
        seeds.append((index, at, _queued_text(body), f"peer:{origin.get('name') or '?'}", False))

    return [
        ChatRow(at=at, text=text, source=source, typed_while_busy=busy, answered=answered)
        for (_, at, text, source, busy), answered in zip(
            seeds, _answers(records, seeds), strict=True
        )
    ]


def _answers(
    records: list[Record], seeds: list[tuple[int, datetime, str, str, bool]]
) -> list[bool]:
    """Whether a reply followed each message before the next human message.

    A peer message that arrives mid-turn does not end the window, so it cannot take the reply
    from the user's request before it. A request that the user interrupted and went on with
    takes the answer of the message after the interrupt.
    """
    humans = [seed[0] for seed in seeds if seed[3] == "human"]
    answered: list[bool] = []
    for start in (seed[0] for seed in seeds):
        later = [index for index in humans if index > start]
        end = later[0] if later else len(records)
        while (
            not _answered_between(records, start + 1, end)
            and later
            and _interrupted_between(records, start + 1, end)
        ):
            start, later = later[0], later[1:]
            end = later[0] if later else len(records)
        answered.append(_answered_between(records, start + 1, end))
    return answered


def _interrupted_between(records: list[Record], start: int, end: int) -> bool:
    return any(
        record.get("type") == "user"
        and text_of((record.get("message") or {}).get("content")).strip().startswith(INTERRUPT)
        for record in records[start:end]
    )


def _worktree_named(prompt: str) -> str | None:
    """The worktree an agent prompt names: absolute when the prompt spells one, else the bare name.
    Only the session's cwd can root a bare name, and `resolve_worktree_names` does that."""
    absolute = WORKTREE_ABS_RE.search(prompt)
    if absolute:
        return absolute.group(1)
    bare = WORKTREE_RE.search(prompt)
    return bare.group(1) if bare else None


def _task_row(kind: str, description: str, at: datetime) -> TaskRow:
    """A row for work that carries no agent prompt, so the prompt fields stay empty.
    Each caller names the work in text of any length, so the cut to one line is here."""
    return TaskRow(
        task_id=None,
        kind=kind,
        description=excerpt(description),
        model=None,
        agent_type=None,
        spec=None,
        section=None,
        worktree=None,
        spawned_at=at,
        last_seen=at,
    )


def _spawned_row(block: Record, at: datetime, spec_re: re.Pattern[str]) -> TaskRow | None:
    """The row an Agent, a background Bash or a Workflow call opens, and `None` for any other call.
    A Bash command line never becomes the description, because a command line can carry a token."""
    inp = block.get("input") or {}
    name = block.get("name")
    if name == "Agent":
        prompt = inp.get("prompt") or ""
        description = excerpt(inp.get("description") or "agent")
        spec = spec_re.search(prompt)
        # The description holds one number and a prompt can hold several, such as `## Part 1`
        # in the brief of "Review Task 4".
        section = SECTION_RE.search(description) or SECTION_RE.search(prompt)
        return TaskRow(
            task_id=None,
            kind="agent",
            description=description,
            model=inp.get("model"),
            agent_type=inp.get("subagent_type"),
            spec=spec.group(0) if spec else None,
            section=section.group(1) if section else None,
            worktree=_worktree_named(prompt),
            spawned_at=at,
            last_seen=at,
        )
    if name == "Bash" and inp.get("run_in_background"):
        return _task_row("background", inp.get("description") or "background command", at)
    if name == "Workflow":
        return _task_row("workflow", inp.get("description") or "workflow", at)
    return None


def _spawn_result(row: TaskRow, result: Any, content: Any, at: datetime) -> Any:
    """Fold a spawn's result into its row, and answer with the task id notifications are keyed on.
    A blocking Agent call answers with the agent's own report instead of an id."""
    if not isinstance(result, dict):
        # An errored tool files `toolUseResult` as a plain string, `Error: Exit code 1\n...`.
        # There is no task, so no notification can ever correct this row.
        row.state = "failed"
        row.last_seen = at
        row.report = first_line(result) if isinstance(result, str) else ""
        return None
    if row.kind == "agent":
        row.model = row.model or result.get("resolvedModel")
        if result.get("status") != "async_launched":
            row.state = "reported"
            row.report = first_line(text_of(content))
            row.last_seen = at
        return result.get("agentId")
    if row.kind == "background":
        return result.get("backgroundTaskId")
    # A workflow names itself in a summary of any length, and the report gives a row a line.
    named = result.get("summary") or result.get("workflowName")
    row.description = excerpt(named) if isinstance(named, str) else row.description
    return result.get("taskId")


def _notified(row: TaskRow, tags: dict[str, str], at: datetime) -> None:
    """Fold one `<task-notification>` into its row, which keeps the newest report and a count.
    A stopped row stays stopped: an agent still posts a completion notification after TaskStop."""
    summary = tags.get("summary", "")
    row.notifications += 1
    row.last_seen = at
    row.report = first_line(tags.get("result", "")) or excerpt(summary)
    code = EXIT_CODE_RE.search(summary)
    if code:
        row.exit_code = int(code.group(1))
    if row.state == "stopped":
        return
    broke = tags.get("status", "completed") != "completed" or row.exit_code not in (None, 0)
    row.state = "failed" if broke else "reported"


def _read_json(path: Path) -> Record:
    """A missing or half-written meta file must not stop the report."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _subagent_end(path: Path) -> tuple[str, datetime | None]:
    """The last thing a subagent said, which is its report, and the time its turn ended.

    An async Agent call answers with an id alone, so the report is here or in a notification.
    An agent that another agent or a workflow started sends its notification to that parent,
    never to this session, so the end of its own file is the only proof that it stopped.
    """
    records = [r for r in load_records([path]) if r.get("type") in ("assistant", "user")]
    last = records[-1] if records else {}
    message = last.get("message") or {}
    ended = (
        last.get("_ts")
        if last.get("type") == "assistant" and message.get("stop_reason") == "end_turn"
        else None
    )
    for record in reversed(records):
        if record.get("type") != "assistant":
            continue
        text = text_of((record.get("message") or {}).get("content"))
        if text.strip():
            return first_line(text), ended
    return "", ended


def _adopt_subagent_files(
    rows: list[TaskRow], by_task: dict[str, TaskRow], dirs: list[Path]
) -> None:
    """Compaction can drop a spawn while its agent's files survive. A file with no row becomes one.
    A file is read only for a row with no report or still running, because a long session
    leaves hundreds."""
    for directory in dirs:
        for path in sorted(directory.glob("agent-*.jsonl")):
            agent_id = path.stem.removeprefix("agent-")
            row = by_task.get(agent_id)
            if row is None:
                meta = _read_json(path.parent / f"agent-{agent_id}.meta.json")
                at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                row = _task_row("agent", meta.get("description") or "agent", at)
                row.task_id = agent_id
                row.agent_type = meta.get("agentType")
                rows.append(row)
                by_task[agent_id] = row
            if row.report and row.state != "running":
                continue
            report, ended = _subagent_end(path)
            row.report = row.report or report
            if row.state == "running" and ended is not None:
                row.state = "reported"
                row.last_seen = ended


def task_rows(
    records: list[Record], subagent_dirs: list[Path], spec_re: re.Pattern[str]
) -> list[TaskRow]:
    """Every agent, background command and workflow the session started, in spawn order.

    A task is addressed two ways, so the pass keeps two maps: by tool_use id until the
    spawn's result names it, by task id after. Both are needed because the spawn and the
    notification that ends the task share no other key.
    """
    rows: list[TaskRow] = []
    by_tool_use: dict[str, TaskRow] = {}
    by_task: dict[str, TaskRow] = {}
    stopped: set[str] = set()
    notices: set[str] = set()

    for record in records:
        at = record.get("_ts", NO_TIME)
        content = (record.get("message") or {}).get("content")
        blocks = (
            [block for block in content if isinstance(block, dict)]
            if isinstance(content, list)
            else []
        )
        kind = record.get("type")

        if kind == "assistant":
            for block in blocks:
                if block.get("type") != "tool_use":
                    continue
                row = _spawned_row(block, at, spec_re)
                if row is not None:
                    rows.append(row)
                    if isinstance(block.get("id"), str):
                        by_tool_use[block["id"]] = row
                elif block.get("name") == "TaskStop":
                    task_id = (block.get("input") or {}).get("task_id")
                    if isinstance(task_id, str):
                        # Remembered even when no row carries the id yet, so the agent's
                        # own completion notification cannot report it as finished work.
                        stopped.add(task_id)
                        if task_id in by_task:
                            by_task[task_id].state = "stopped"
        elif kind == "user":
            for block in blocks:
                if block.get("type") != "tool_result":
                    continue
                row = by_tool_use.get(str(block.get("tool_use_id")))
                if row is None:
                    continue
                spawned_id = _spawn_result(
                    row, record.get("toolUseResult") or {}, block.get("content"), at
                )
                if isinstance(spawned_id, str):
                    row.task_id = spawned_id
                    by_task[spawned_id] = row
        elif kind == "attachment":
            attachment = record.get("attachment") or {}
            row = by_task.get(str(attachment.get("taskId")))
            if attachment.get("type") == "task_status" and row is not None:
                row.last_seen = at
                if attachment.get("status") == "running" and attachment.get("deltaSummary"):
                    row.report = excerpt(attachment["deltaSummary"])
        for chunk in NOTIFICATION_BLOCK_RE.finditer(_notice_text(record)):
            notice = chunk.group(0).strip()
            if notice in notices:
                continue
            notices.add(notice)
            _fold_notice(chunk.group(1), at, rows, by_task, stopped)

    _adopt_subagent_files(rows, by_task, subagent_dirs)
    return rows


def _notice_text(record: Record) -> str:
    """The text that can carry a `<task-notification>`. The client files one in a user record,
    in a queued attachment while the session is busy, or only in the queue record of its
    enqueue. One notice often appears in two of them, so the caller counts each text once."""
    kind = record.get("type")
    if kind == "user":
        return text_of((record.get("message") or {}).get("content"))
    if kind == "attachment":
        attachment = record.get("attachment") or {}
        queued = attachment.get("type") == "queued_command"
        return text_of(attachment.get("prompt")) if queued else ""
    if kind == "queue-operation" and record.get("operation") == "enqueue":
        return text_of(record.get("content"))
    return ""


def _fold_notice(
    body: str,
    at: datetime,
    rows: list[TaskRow],
    by_task: dict[str, TaskRow],
    stopped: set[str],
) -> None:
    tags = dict(NOTIFICATION_RE.findall(body))
    task_id = tags.get("task-id")
    if not task_id:
        return
    row = by_task.get(task_id)
    if row is None:
        kind_of_id = "agent" if AGENT_ID_RE.match(task_id) else "background"
        row = _task_row(kind_of_id, tags.get("summary", ""), at)
        row.task_id = task_id
        rows.append(row)
        by_task[task_id] = row
    if task_id in stopped:
        row.state = "stopped"
    _notified(row, tags, at)


def spec_mentions(
    records: list[Record], spec_re: re.Pattern[str]
) -> dict[str, tuple[datetime, datetime]]:
    """Every spec and plan path the session named, with the first and last time it did.

    Only what the user typed and what the assistant said or passed to a tool is read. A
    tool result can quote a whole directory listing, and one such listing would outvote
    the path the session is working from.
    """
    seen: dict[str, tuple[datetime, datetime]] = {}
    for record in records:
        at = record.get("_ts", NO_TIME)
        content = (record.get("message") or {}).get("content")
        if record.get("type") == "assistant":
            blocks = content if isinstance(content, list) else []
            spoken = [text_of(content)] + [
                json.dumps(
                    {k: v for k, v in (block.get("input") or {}).items() if k not in BODY_KEYS}
                )
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
            haystack = "\n".join(spoken)
        elif is_human_prompt(record):
            haystack = text_of(content)
        else:
            continue
        for match in spec_re.finditer(haystack):
            path = match.group(0)
            first = seen.get(path, (at, at))[0]
            seen[path] = (first, at)
    return seen


def resolve_spec_path(path: str, candidates: list[Path]) -> Path | None:
    """The file a mentioned path names, tried against every root the session has used.

    A transcript writes the path relative to a working directory it never repeats, and the
    session may have moved into a worktree since. The first root holding the file wins.
    """
    if path.startswith("/"):
        absolute = Path(path)
        return absolute if absolute.exists() else None
    for root in candidates:
        found = root / path
        if found.exists():
            return found
    return None


def parse_spec(file: Path) -> tuple[str | None, list[SpecSection]]:
    """The spec's `Status:` and its numbered sections, in file order.

    The sections are the task list the report hangs each agent off. A plan's checkboxes
    stay unchecked after the work ships and `Status:` is not maintained either, so neither
    is read here as evidence of progress. Git is.
    """
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, []
    status: str | None = None
    sections: list[SpecSection] = []
    waiting: SpecSection | None = None
    fenced = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if status is None and line.startswith("Status:"):
            status = line[len("Status:") :].strip() or None
        heading = HEADING_RE.match(line)
        if heading:
            waiting = SpecSection(number=heading.group(1), title=heading.group(2))
            sections.append(waiting)
            continue
        if stripped.startswith("#"):
            # Any other heading ends the search, so a section with no prose of its own
            # keeps the empty sentence instead of borrowing the next section's.
            waiting = None
            continue
        if waiting is None or not stripped:
            continue
        if stripped.startswith(SECTION_PROSE_SKIP) or ORDERED_ITEM_RE.match(stripped):
            continue
        waiting.first_sentence = first_sentence(stripped)
        waiting = None
    return status, sections


def link_agents(sections: list[SpecSection], rows: list[TaskRow]) -> None:
    """Hang each agent off the section its prompt named.

    A prompt says "section 5.4" where the spec heads that work `## 5`, so a number with no
    section of its own falls back to its parent. Without the fallback the section reads as
    nobody's work while an agent is inside it.
    """
    by_number = {section.number: section for section in sections}
    for row in rows:
        if not row.section:
            continue
        section = by_number.get(row.section) or by_number.get(row.section.split(".")[0])
        if section is not None:
            section.agents.append(row.description)


def spec_info(
    records: list[Record],
    rows: list[TaskRow],
    candidates: list[Path],
    override: str | None,
    spec_re: re.Pattern[str],
) -> SpecInfo | None:
    """The spec the session is working from, read off disk and linked to the agents.

    The path mentioned last wins, which is a guess, and `--spec` overrides it. An override
    nobody mentioned takes the first dated record as its first mention, because the recency
    rule measures from it and an undated record would print a midnight the session never saw.
    """
    mentions = spec_mentions(records, spec_re)
    if override:
        path = override
        start = next((record["_ts"] for record in records if record["_ts"] != NO_TIME), NO_TIME)
        first, last = mentions.get(override, (start, start))
    elif mentions:
        path = max(mentions, key=lambda named: mentions[named][1])
        first, last = mentions[path]
    else:
        return None
    resolved = resolve_spec_path(path, candidates)
    status, sections = parse_spec(resolved) if resolved else (None, [])
    link_agents(sections, rows)
    return SpecInfo(
        path=path,
        resolved=resolved,
        kind="plan" if "/plans/" in path else "spec",
        status=status,
        first_mention=first,
        last_mention=last,
        sections=sections,
    )


def audit_state(stem: str, cwd: Path | None) -> tuple[str, list[str], list[str]]:
    """What the saved audit says is left of one spec: its state, open ids and blocked ids.

    An audit of an older commit of its base branch reads `stale` whatever its rows say.
    `load_audit` exits on an
    unreadable file and on a directory outside every checkout, and one spec is not worth the
    whole digest, so the row carries the reason instead.
    """
    try:
        audit = store.load_audit(stem, cwd)
    except SystemExit as error:
        return f"unreadable: {error}", [], []
    if audit is None:
        return NOT_AUDITED, [], []
    opened, blocked = store.open_ids(audit), store.blocked_ids(audit)
    if store.head_of(audit) != store.branch_commit(store.base_of(audit), cwd):
        return STALE, opened, blocked
    if opened or blocked:
        return f"{len(opened)} open, {len(blocked)} blocked", opened, blocked
    return ALL_DONE, opened, blocked


def named_specs(
    records: list[Record], candidates: list[Path], spec_re: re.Pattern[str]
) -> list[NamedSpec]:
    """Every spec and plan the session named, in the order it first named them.

    A settled spec moves to `archive/`, where the path the transcript wrote no longer
    opens, so a path that misses every root is looked up again by its basename. The audit
    is read beside the checkout that holds the file, which is where the auditor saved it.
    """
    home = next((root for root in candidates if root.is_dir()), None)
    rows: list[NamedSpec] = []
    for path, (first, last) in spec_mentions(records, spec_re).items():
        basename = Path(path).name
        resolved = resolve_spec_path(path, candidates) or store.resolve_spec(basename, home)
        where = resolved.parent if resolved else home
        state, opened, blocked = audit_state(store.stem_of(basename), where)
        rows.append(
            NamedSpec(
                path=path,
                basename=basename,
                resolved=resolved,
                status=store.status_of(resolved) if resolved else "missing",
                first_mention=first,
                last_mention=last,
                audit=state,
                open_ids=opened,
                blocked_ids=blocked,
            )
        )
    return rows


def foldable(spec: NamedSpec) -> bool:
    """A settled spec the audit holds nothing open against, and nothing else."""
    return spec.status in store.SETTLED and spec.audit in FOLDABLE_AUDITS


def resolve_worktree_names(rows: list[TaskRow], cwd: str | None) -> None:
    """Turn the bare `.claude/worktrees/<name>` an agent prompt wrote into a path.

    The prompt names the directory against the session's own cwd, and git needs the whole
    path. With no cwd the row loses the name, because every later reader takes `worktree`
    for a path it can open and a bare name is a path relative to whatever it runs in.
    """
    for row in rows:
        if row.worktree and not row.worktree.startswith("/"):
            row.worktree = str(Path(cwd) / ".claude" / "worktrees" / row.worktree) if cwd else None


def _git(args: list[str], cwd: Path) -> str | None:
    """One read-only git command, answering `None` whenever git refuses or hangs.
    A deleted worktree, a repository mid-rebase or a hung filesystem does not fail the digest.
    `core.fsmonitor` names a program that `git status` runs, so it is off."""
    try:
        done = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def base_refs(path: Path) -> list[str]:
    """The branches a worktree merges into, best first: `harness.baseBranch`, then main, master."""
    configured = str(config.load(path).get("harness.baseBranch"))
    return list(dict.fromkeys(ref for ref in (configured, "main", "master") if ref))


def _merged_into(path: Path, main_ref: str) -> bool:
    """Whether one of main's last 500 merges took this worktree's HEAD as a second parent.
    A merge leaves HEAD behind main rather than on it, so that parent is the only trace left."""
    head = _git(["rev-parse", "HEAD"], path)
    if not head:
        return False
    merges = _git(["log", main_ref, "--merges", "-n", "500", "--format=%P"], path)
    return any(head in line.split()[1:] for line in (merges or "").splitlines())


@cache
def worktree_state(path: Path) -> tuple[str | None, bool, str | None]:
    """What a worktree says about itself: its verdict, whether it is dirty, and main's name.

    The one verdict is `merged`: HEAD on main's tip, or a HEAD a merge on main absorbed. A branch
    fast-forwarded into a main that then moved gives none, and a worktree that committed nothing
    while main stood still reads `merged`. Memoised, because a plan gives many agents one worktree.
    """
    if not (path / ".git").exists():
        return None, False, None
    dirty = bool(_git(["status", "--porcelain"], path))
    main_ref = next(
        (ref for ref in base_refs(path) if _git(["rev-parse", "--verify", "--quiet", ref], path)),
        None,
    )
    if main_ref is None:
        return None, dirty, None
    counts = _git(["rev-list", "--left-right", "--count", f"{main_ref}...HEAD"], path)
    parts = counts.split() if counts else []
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None, dirty, main_ref
    behind, ahead = int(parts[0]), int(parts[1])
    if ahead:
        # Unmerged commits sit here. Whose they are is the per-row question, and a
        # `merged` answer would be wrong while the branch is ahead.
        return None, dirty, main_ref
    if behind == 0:
        return "merged", dirty, main_ref
    return ("merged" if _merged_into(path, main_ref) else None), dirty, main_ref


def _landed_by(row: TaskRow, path: Path, main_ref: str) -> bool:
    """Whether the worktree gained a commit main lacks during this agent's own life.
    Many agents share one worktree and reviewers commit nothing, so the worktree cannot say."""
    counts = _git(
        [
            "rev-list",
            "--count",
            f"--since={row.spawned_at.isoformat()}",
            f"--until={(row.last_seen + LANDING_GRACE).isoformat()}",
            f"{main_ref}..HEAD",
        ],
        path,
    )
    return bool(counts and counts.isdigit() and int(counts) > 0)


def _common_dir(path: Path) -> str | None:
    return _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], path)


def foreign_repository(cwd: str | None, root: Path) -> str | None:
    """The checkout that holds `cwd` when it is outside the repository at `root`, else None.

    A path under a worktree of `root` belongs to it. Git runs only at `root`: the checkout of a
    foreign `cwd` is the nearest folder that holds `.git`, else `cwd` itself.
    """
    if not cwd:
        return None
    here = Path(cwd)
    listing = _git(["worktree", "list", "--porcelain"], root) or ""
    prefix = "worktree "
    homes = [Path(line[len(prefix) :]) for line in listing.splitlines() if line.startswith(prefix)]
    homes = homes or [root]
    if any(here.is_relative_to(home) for home in homes):
        return None
    return next((str(f) for f in (here, *here.parents) if (f / ".git").exists()), cwd)


def apply_git_evidence(rows: list[TaskRow], root: Path) -> None:
    """Let each worktree correct the state its agent's notification claimed.

    A notification can arrive before its agent stops writing, so the commits are the
    evidence. Only a `reported` row is corrected: a running row has nothing to prove yet, and
    `stopped` or `failed` is a decision no commit reverses. A transcript can name a worktree of
    another repository, whose config can run a program, so git runs only in a worktree of the
    repository at `root`.
    """
    home = _common_dir(root)
    for row in rows:
        if not row.worktree or row.state != "reported":
            continue
        path = Path(row.worktree)
        if home is None or not path.is_dir() or _common_dir(path) != home:
            continue
        state, dirty, main_ref = worktree_state(path)
        row.dirty = dirty
        if main_ref is not None and _landed_by(row, path, main_ref):
            row.state = "landed"
        elif state is not None:
            row.state = state


def parse_since(text: str) -> timedelta:
    """How far back `--since` opens the window."""
    match = SINCE_RE.match(text)
    if not match:
        raise ValueError(f"--since wants <n>m, <n>h or <n>d, got {text!r}")
    return timedelta(**{SINCE_UNITS[match.group(2)]: int(match.group(1))})


def anchor_for(
    spec: SpecInfo | None, session: SessionInfo, since: str | None, show_all: bool
) -> datetime | None:
    """Where the window opens, or `None` when every row is shown.

    The spec's first mention is the default because it is when this piece of work started,
    and everything the session did before it belongs to the piece before. Measured from the
    last record rather than the clock, so the answer is the same tomorrow.
    """
    if show_all or session.last is None:
        return None
    if since:
        return session.last - parse_since(since)
    if spec is not None:
        return spec.first_mention
    return session.last - DEFAULT_WINDOW


def _capped(chat: list[ChatRow]) -> list[ChatRow]:
    """The newest `CHAT_CAP` answered messages, and every unanswered one.
    The cap never takes an unanswered message, which is what the report exists to keep."""
    answered = [index for index, row in enumerate(chat) if row.answered]
    if len(answered) <= CHAT_CAP:
        return list(chat)
    dropped = set(answered[:-CHAT_CAP])
    return [row for index, row in enumerate(chat) if index not in dropped]


def apply_window(digest: Digest) -> None:
    """Drop the finished rows that fall before the anchor, and count them.

    Unfinished work is never dropped, whatever its age, and neither is a message nobody
    answered. That is the whole rule: a long session stays readable and no task leaves
    the report by getting old.
    """
    anchor = digest.anchor
    if anchor is None:
        # `--all` lifts the cap with the anchor. The Hidden line offers it as the way to
        # see everything the report left out, so it has to be the surface that does.
        digest.hidden_count = 0
        digest.chat_total = len(digest.chat)
        return

    hidden = 0
    for tasks in (digest.agents, digest.background):
        kept = [row for row in tasks if row.state not in FINISHED or row.last_seen >= anchor]
        hidden += len(tasks) - len(kept)
        tasks[:] = kept
    for said in (digest.chat, digest.peers):
        heard = [row for row in said if not row.answered or row.at >= anchor]
        hidden += len(said) - len(heard)
        said[:] = heard

    digest.hidden_count = hidden
    digest.chat_total = len(digest.chat)
    digest.chat[:] = _capped(digest.chat)


def _clock(when: datetime | None, day: datetime | None) -> str:
    """A time in the machine's own zone, dated when it falls outside the day `day` names.
    Transcripts are stamped UTC, and a UTC time printed as it is reads wrong in any other zone."""
    if when is None or when == NO_TIME:
        return "--:--"
    local = when.astimezone()
    if day is not None and day != NO_TIME and local.date() != day.astimezone().date():
        return local.strftime("%m-%d %H:%M")
    return local.strftime("%H:%M")


def _day_clock(when: datetime | None) -> str:
    return when.astimezone().strftime("%Y-%m-%d %H:%M") if when and when != NO_TIME else "?"


def _leaf(path: str | None) -> str | None:
    """The last element of a worktree path, which is the name the user gave the worktree."""
    return path.rstrip("/").rsplit("/", 1)[-1] if path else None


def _flags(row: ChatRow) -> str:
    marks = []
    if row.typed_while_busy:
        marks.append("[typed-while-busy]")
    if not row.answered:
        marks.append("[unanswered]")
    return "".join(mark + " " for mark in marks)


def _task_line(row: TaskRow, day: datetime | None) -> str:
    """One task to a line, with every field it has no answer for left out.
    A background command has no model, section or worktree, and an agent has no exit code."""
    parts = [row.task_id[:8] if row.task_id else "?", row.description]
    who = row.model or row.agent_type
    if who:
        parts.append(who)
    if row.section:
        parts.append(f"section {row.section}")
    leaf = _leaf(row.worktree)
    if leaf:
        parts.append(leaf)
    parts.append(row.state)
    if row.dirty:
        # The agent reported and left the worktree uncommitted, so the work is not where
        # the state says it is.
        parts.append("dirty")
    if row.exit_code is not None:
        parts.append(f"exit {row.exit_code}")
    parts.append(_clock(row.last_seen, day))
    if row.report:
        parts.append(row.report)
    return "- " + " | ".join(parts)


def _spec_lines(spec: SpecInfo | None, day: datetime | None) -> list[str]:
    """The Spec block. A path that resolves to nothing still prints, with the reason it is empty."""
    if spec is None:
        return ["none named"]
    lines = [
        f"{spec.path} ({spec.status or 'no status'}) "
        f"first mentioned {_clock(spec.first_mention, day)}"
    ]
    if spec.resolved is None:
        lines.append(f"file not found: {spec.path}")
    for section in spec.sections:
        parts = [f"{section.number}. {section.title}"]
        if section.first_sentence:
            parts.append(section.first_sentence)
        parts.append(f"agents: {', '.join(section.agents) or 'none'}")
        lines.append("- " + " | ".join(parts))
    return lines


def _named_spec_lines(digest: Digest, day: datetime | None) -> list[str]:
    """The Specs named block: one line per spec, with the settled and finished folded.
    The fold ignores age, so an unsettled, stale or open spec keeps its line. `--all` unfolds it."""
    if not digest.specs_named:
        return ["none named"]
    folded = [spec for spec in digest.specs_named if digest.anchor is not None and foldable(spec)]
    lines = [
        f"- {spec.basename} | {spec.status or 'no status'} | "
        f"{_clock(spec.first_mention, day)} to {_clock(spec.last_mention, day)} | {spec.audit}"
        for spec in digest.specs_named
        if spec not in folded
    ]
    if folded:
        lines.append(f"- {len(folded)} settled, all done or not audited; --all lists them")
    return lines


def _hidden_line(digest: Digest, day: datetime | None) -> str:
    """What the window took away, and the two flags that give it back."""
    if not digest.hidden_count or digest.anchor is None:
        return "nothing hidden"
    return (
        f"{digest.hidden_count} finished rows before {_clock(digest.anchor, day)}; "
        "--all shows them, --since <n>h moves the line"
    )


def render_markdown(digest: Digest) -> str:
    """The digest as the page the user reads, one row to a line.

    Times are converted to the machine's zone here rather than in the digest, so `--json`
    keeps the transcript's own UTC stamps and only the reading is localised.
    """
    session = digest.session
    day = session.last
    lines = [f"# work-status: {session.name or session.session_id}"]
    lines.append(
        " | ".join(
            [
                f"session {session.session_id[:8]}",
                f"cwd {session.cwd or 'unknown'}",
                f"worktree {_leaf(session.worktree) or 'none'}",
                f"started {_day_clock(session.started)}",
                f"last {_clock(session.last, day)}",
                f"compactions {session.compactions}",
                f"prompts {session.human_prompts}",
                "times local",
            ]
        )
    )
    if digest.repository:
        lines.append(
            f"repository {digest.repository}: not the repository that this command runs in, so"
            " its worktrees and spec files are not read"
        )
    if session.goal:
        # `/goal` leaves the condition with no verdict until it judges one, and a missing
        # verdict printed as "not met" is a claim about work nobody has looked at.
        verdict = (
            "" if session.goal_met is None else f" ({'met' if session.goal_met else 'not met'})"
        )
        lines.append(f"goal: {session.goal}{verdict}")

    lines += ["", "## Spec"]
    lines += _spec_lines(digest.spec, day)

    lines += ["", "## Specs named"]
    lines += _named_spec_lines(digest, day)

    running = sum(1 for row in digest.agents if row.state not in FINISHED)
    lines += ["", f"## Agents ({running} running, {len(digest.agents) - running} finished)"]
    lines += [_task_line(row, day) for row in digest.agents] or ["none"]

    lines += ["", "## Background"]
    lines += [_task_line(row, day) for row in digest.background] or ["none"]

    since = _clock(digest.anchor, day) if digest.anchor else "the start"
    capped = ""
    if digest.chat_total > len(digest.chat):
        capped = f" (showing {len(digest.chat)} of {digest.chat_total})"
    lines += ["", f"## Chat since {since}{capped}"]
    lines += [f"- {_clock(row.at, day)} {_flags(row)}{row.text}" for row in digest.chat] or ["none"]

    lines += ["", "## Peers"]
    lines += [
        f"- {_clock(row.at, day)} {row.source} {_flags(row)}{row.text}" for row in digest.peers
    ] or ["none"]

    lines += ["", "## Hidden", _hidden_line(digest, day)]
    return "\n".join(lines)


def to_json(digest: Digest) -> str:
    return json.dumps(dataclasses.asdict(digest), default=str, indent=2)


def _spec_roots(session: SessionInfo, records: list[Record], rows: list[TaskRow]) -> list[Path]:
    """Every directory a relative spec path can hang off, best first.
    The agents' worktrees come last: each holds the same file, and the user reads the session's."""
    first_cwd = records[0].get("cwd") if records else None
    named = [
        session.cwd,
        session.worktree,
        first_cwd if isinstance(first_cwd, str) else None,
        *[row.worktree for row in rows if row.kind == "agent"],
    ]
    roots: list[Path] = []
    for name in named:
        root = Path(name) if name else None
        if root is not None and root not in roots:
            roots.append(root)
    return roots


def mention_re(spec_layout: layout.Layout) -> re.Pattern[str]:
    """A spec path as a transcript writes it, in a spec folder or in the archive folder of one.
    A session that reads a settled spec names it where it lies, inside the archive."""
    names = "|".join(re.escape(folder) for folder in spec_layout.dirs)
    archive = re.escape(spec_layout.archive)
    return re.compile(rf"(?:{names})/(?:{archive}/)?[\w.-]+\.md")


def build_digest(
    session_id: str,
    projects_root: Path = PROJECTS_ROOT,
    since: str | None = None,
    show_all: bool = False,
    spec_override: str | None = None,
    spec_re: re.Pattern[str] | None = None,
    root: Path | None = None,
) -> Digest:
    """Read one session off disk and answer with everything the report needs.

    The worktree names are resolved before git runs, because a bare name opens nothing.
    The window closes the pass: it needs the spec's first mention and every row's state,
    so it can only run once both exist.
    """
    paths = find_transcripts(session_id, projects_root)
    if not paths:
        raise FileNotFoundError(
            f"no transcript for session {session_id} under {projects_root}/*/{session_id}.jsonl"
        )
    root = root or config.find_root()
    if spec_re is None:
        spec_re = mention_re(layout.for_root(root))
    records = load_records(paths)
    session = session_info(session_id, records)
    said = chat_rows(records)
    tasks = task_rows(records, find_subagent_dirs(session_id, projects_root), spec_re)
    resolve_worktree_names(tasks, session.cwd)
    apply_git_evidence(tasks, root)
    roots = _spec_roots(session, records, tasks)
    digest = Digest(
        session=session,
        repository=foreign_repository(session.cwd, root),
        spec=spec_info(records, tasks, roots, spec_override, spec_re),
        agents=[row for row in tasks if row.kind == "agent"],
        background=[row for row in tasks if row.kind != "agent"],
        chat=[row for row in said if row.source == "human"],
        peers=[row for row in said if row.source != "human"],
        specs_named=named_specs(records, roots, spec_re),
    )
    digest.anchor = anchor_for(digest.spec, session, since, show_all)
    apply_window(digest)
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ah work-status", description="Print what a Claude Code session is doing."
    )
    parser.add_argument(
        "--session",
        default=os.environ.get("CLAUDE_CODE_SESSION_ID"),
        help="session id, default $CLAUDE_CODE_SESSION_ID",
    )
    parser.add_argument(
        "--projects-root", type=Path, default=PROJECTS_ROOT, help="where the transcripts live"
    )
    parser.add_argument("--since", help="move the recency line, for example 12h")
    parser.add_argument("--all", action="store_true", help="show finished rows of any age")
    parser.add_argument("--spec", help="read this spec or plan instead of the newest one named")
    parser.add_argument("--json", action="store_true", help="print the digest as JSON")
    parser.add_argument(
        "--repo", action="store_true", help="every worktree, unsettled spec and live goal"
    )
    args = parser.parse_args(argv)

    if args.repo:
        from agent_harness.work import repo

        return repo.main(config.find_root(), args.json)
    if not args.session:
        print("work-status: no --session and CLAUDE_CODE_SESSION_ID is not set", file=sys.stderr)
        return 2
    if any(char in args.session for char in REFUSED_IN_SESSION_ID):
        print(f"work-status: --session takes one session id, not {args.session!r}", file=sys.stderr)
        return 2
    try:
        digest = build_digest(
            args.session,
            projects_root=args.projects_root,
            since=args.since,
            show_all=args.all,
            spec_override=args.spec,
        )
    except FileNotFoundError as error:
        print(str(error), file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"work-status: {error}", file=sys.stderr)
        return 2
    print(to_json(digest) if args.json else render_markdown(digest))
    return 0
