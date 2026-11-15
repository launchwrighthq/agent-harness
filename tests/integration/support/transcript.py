"""Claude Code transcript records and a fake `~/.claude/projects` tree for one session."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

T0 = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
SID = "11111111-2222-3333-4444-555555555555"
SLUG = "-tmp-proj"
WT_SLUG = "-tmp-proj--claude-worktrees-wt-a"
AGENT_A = "a" + "1" * 16
AGENT_B = "a" + "2" * 16
AGENT_C = "a" + "3" * 16
AGENT_D = "a" + "4" * 16
AGENT_E = "a" + "5" * 16

Record = dict[str, Any]


def ts(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def rec(kind: str, minutes: int, **extra: Any) -> Record:
    base: Record = {
        "type": kind,
        "sessionId": SID,
        "timestamp": ts(minutes),
        "cwd": "/tmp/proj",
        "isSidechain": False,
    }
    base.update(extra)
    return base


def human(minutes: int, text: str) -> Record:
    return rec("user", minutes, userType="external", message={"role": "user", "content": text})


def assistant_text(minutes: int, text: str) -> Record:
    content = [{"type": "text", "text": text}]
    return rec("assistant", minutes, message={"role": "assistant", "content": content})


def tool_use(minutes: int, tid: str, name: str, inp: dict[str, Any]) -> Record:
    block = {"type": "tool_use", "id": tid, "name": name, "input": inp}
    return rec("assistant", minutes, message={"role": "assistant", "content": [block]})


def tool_result(minutes: int, tid: str, content: Any, result: Any = None) -> Record:
    block = {"type": "tool_result", "tool_use_id": tid, "content": content}
    message = {"role": "user", "content": [block]}
    record = rec("user", minutes, userType="external", message=message)
    if result is not None:
        record["toolUseResult"] = result
    return record


def attachment(minutes: int, att: dict[str, Any]) -> Record:
    return rec("attachment", minutes, attachment=att)


def notification(
    minutes: int,
    task_id: str,
    status: str = "completed",
    summary: str = 'Agent "x" finished',
    result: str = "Done.",
) -> Record:
    text = (
        "<task-notification>\n"
        f"<task-id>{task_id}</task-id>\n<tool-use-id>toolu_x</tool-use-id>\n"
        f"<status>{status}</status>\n<summary>{summary}</summary>\n"
        f"<result>{result}</result>\n</task-notification>"
    )
    return human(minutes, text)


def compact(minutes: int) -> Record:
    return rec(
        "system",
        minutes,
        subtype="compact_boundary",
        content="Conversation compacted",
        compactMetadata={"preTokens": 900000, "postTokens": 17000},
    )


def queued(minutes: int, prompt: Any, origin: dict[str, Any] | None) -> Record:
    att = {"type": "queued_command", "prompt": prompt, "origin": origin, "timestamp": ts(minutes)}
    return attachment(minutes, att)


def agent_spawn(
    minutes: int, tid: str, agent_id: str, description: str, prompt: str, model: str = "opus"
) -> list[Record]:
    """The two records one async Agent call leaves: the call and its launch result."""
    inp = {
        "description": description,
        "prompt": prompt,
        "subagent_type": "general-purpose",
        "model": model,
    }
    launched = {
        "isAsync": True,
        "status": "async_launched",
        "agentId": agent_id,
        "description": description,
        "resolvedModel": f"claude-{model}-5",
    }
    return [
        tool_use(minutes, tid, "Agent", inp),
        tool_result(minutes, tid, "Async agent launched successfully.", launched),
    ]


class Projects:
    """A fake `~/.claude/projects` tree beside a project folder, both under `root`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.projects = root / "projects"
        self.projects.mkdir()
        self.proj = root / "proj"
        self.proj.mkdir()

    def write(self, slug: str, records: list[Record]) -> Path:
        folder = self.projects / slug
        folder.mkdir(exist_ok=True)
        path = folder / f"{SID}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return path

    def subagent(
        self, slug: str, agent_id: str, description: str, prompt: str, report: str
    ) -> None:
        folder = self.projects / slug / SID / "subagents"
        folder.mkdir(parents=True, exist_ok=True)
        meta = {"agentType": "general-purpose", "description": description, "spawnDepth": 1}
        (folder / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        rows = [
            {
                "type": "user",
                "agentId": agent_id,
                "timestamp": ts(0),
                "message": {"role": "user", "content": prompt},
            },
            {
                "type": "assistant",
                "agentId": agent_id,
                "timestamp": ts(1),
                "message": {"role": "assistant", "content": [{"type": "text", "text": report}]},
            },
        ]
        text = "".join(json.dumps(row) + "\n" for row in rows)
        (folder / f"agent-{agent_id}.jsonl").write_text(text, encoding="utf-8")
