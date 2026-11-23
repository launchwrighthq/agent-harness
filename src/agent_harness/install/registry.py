"""The parts of a project that the installer writes and the project also edits.

These are the harness entries of the `hooks` key of `.claude/settings.json` and of
`.codex/hooks.json`, the marked block of `AGENTS.md`, the hook state lines of `.gitignore` and
the `.agents/skills/` links.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent_harness import HarnessError
from agent_harness.install.plan import ENGINE_DIR, SKILL_DIR, Plan, interpreter_for
from agent_harness.manifest import CODEX_EVENTS, EVENTS, TOOL_EVENTS

SETTINGS_FILE = ".claude/settings.json"
CODEX_FILE = ".codex/hooks.json"
CODEX_ADAPTER = f"{ENGINE_DIR}/codex/run-hook.py"
AGENTS_FILE = "AGENTS.md"
GITIGNORE_FILE = ".gitignore"
LINK_DIR = ".agents/skills"
BLOCK_START = "<!-- lw-harness:start -->"
BLOCK_END = "<!-- lw-harness:end -->"

# Each line that `.gitignore` needs, with the forms that already ignore the same files. The hooks
# and skills keep their state in `.claude/state/`. Python writes caches beside the engine.
IGNORED = (
    ("/.claude/state/", ("/.claude/state/", "/.claude/state", ".claude/state/", ".claude/state")),
    (
        "/.claude/agent-harness/**/__pycache__/",
        (
            "/.claude/agent-harness/**/__pycache__/",
            "__pycache__/",
            "__pycache__",
            "**/__pycache__/",
            "**/__pycache__",
            "*.pyc",
            "*.py[cod]",
        ),
    ),
)

Registry = dict[str, list[dict[str, Any]]]

# The command of a registry entry that the installer writes: the Claude form runs a script under
# the engine folder, the Codex form runs the adapter of the engine folder. Every other command is
# a project hook, and an install keeps it.
OWN_COMMAND = re.compile(
    r'(?:python3|bash|node) "\$CLAUDE_PROJECT_DIR/' + re.escape(ENGINE_DIR) + r"/.*"
    r'|python3 "\$\(git rev-parse --show-toplevel\)/(?:[^"]*/)?'
    + re.escape(f"{ENGINE_DIR}/codex/run-hook.py")
    + r'" .*',
    re.S,
)

# The characters that a project folder may hold. The Codex command names the folder inside double
# quotes, where the shell still expands `$`, a backtick and `\`.
SAFE_FOLDER = re.compile(r"[A-Za-z0-9._/-]*")


def claude_command(script: str, event: str) -> str:
    """The Claude Code command of one hook on one event: the interpreter and the installed copy."""
    return f'{interpreter_for(script)} "$CLAUDE_PROJECT_DIR/{script}" {event}'


def codex_command(script: str, event: str, project: str = "") -> str:
    """The Codex command of one hook on one event, through the adapter.

    `project` is the project folder relative to the top of its git checkout, empty at the top.
    Codex runs the command in the checkout, so a project in a subfolder names its own adapter.
    """
    adapter = f"{project}/{CODEX_ADAPTER}" if project else CODEX_ADAPTER
    return f'python3 "$(git rev-parse --show-toplevel)/{adapter}" {script} {event}'


def project_folder(root: Path) -> str:
    """The folder of the project at `root` relative to the top of its git checkout.

    It is empty at the top, outside a git checkout and without git. A folder with a character
    outside `SAFE_FOLDER` is refused, because the Codex command names it inside double quotes.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ""
    top = done.stdout.strip()
    if done.returncode != 0 or not top:
        return ""
    folder = root.resolve().relative_to(Path(top).resolve()).as_posix()
    if not SAFE_FOLDER.fullmatch(folder):
        raise HarnessError(
            f"cannot install into the project folder {folder!r} of {top}: it holds a character"
            " outside A-Z, a-z, 0-9, '.', '_', '/' and '-', and the Codex hook command names the"
            " folder inside double quotes. Rename the folder."
        )
    return "" if folder == "." else folder


def codex_matcher(matcher: str) -> str:
    """Codex edits files with `apply_patch`, so a matcher on Edit or Write also names it."""
    names = matcher.split("|")
    if "*" in names or "apply_patch" in names:
        return matcher
    if any(n in ("Edit", "Write", "MultiEdit") for n in names):
        return f"{matcher}|apply_patch"
    return matcher


def registry(plan: Plan, client: str, project: str = "") -> Registry:
    """One registry. Each hook has one group per event, in plan order, so the order is stable.

    `project` is the argument of `codex_command`.
    """
    out: Registry = {}
    for event in EVENTS:
        groups: list[dict[str, Any]] = []
        for hook in plan.hooks:
            if event not in hook.events:
                continue
            if client == "codex" and (not hook.codex or event not in CODEX_EVENTS):
                continue
            if client == "claude":
                command = claude_command(hook.script, event)
            else:
                command = codex_command(hook.script, event, project)
            group: dict[str, Any] = {}
            if event in TOOL_EVENTS and hook.matcher is not None:
                group["matcher"] = (
                    hook.matcher if client == "claude" else codex_matcher(hook.matcher)
                )
            group["hooks"] = [{"type": "command", "command": command, "timeout": hook.timeout}]
            groups.append(group)
        if groups:
            out[event] = groups
    return out


def is_own(entry: Any) -> bool:
    """True for a hook entry whose command the installer writes."""
    command = entry.get("command") if isinstance(entry, dict) else None
    return isinstance(command, str) and OWN_COMMAND.fullmatch(command) is not None


def project_entries(text: str | None, file: str) -> Registry:
    """The groups of the `hooks` key of `text` without the commands that the installer writes.

    A group keeps its other keys and its project commands in their order. A group that held
    only harness commands goes. A group of another shape stays as it is.
    """
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise HarnessError(f"{file} is not valid JSON: {error}") from error
    hooks = data.get("hooks", {}) if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        raise HarnessError(f"{file}: the hooks key is not an object. Fix the file.")
    out: Registry = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise HarnessError(f"{file}: hooks.{event} is not a list. Fix the file.")
        kept: list[Any] = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept.append(group)
                continue
            own = [e for e in entries if not is_own(e)]
            if own or not entries:
                kept.append({**group, "hooks": own})
        if kept:
            out[event] = kept
    return out


def merged(own: Registry, project: Registry) -> Registry:
    """The harness groups first in each event, then the project groups in their old order.

    Codex keeps its trust per entry position, so the harness entries keep their positions. An
    event with project groups only comes after the harness events.
    """
    events = [*own, *(e for e in project if e not in own)]
    return {e: [*own.get(e, []), *project.get(e, [])] for e in events}


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def codex_file(current: str | None, plan: Plan, root: Path) -> str:
    """`.codex/hooks.json` of the project at `root`, with its project entries kept."""
    hooks = merged(
        registry(plan, "codex", project_folder(root)), project_entries(current, CODEX_FILE)
    )
    if current is None:
        return f"{dump_json({'hooks': hooks})}\n"
    return splice_top_level_key(current, "hooks", hooks, CODEX_FILE)


# The JSON splice: replace one top-level key of an object in its text, so every other byte stays.


def _skip_space(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _string_end(text: str, i: int) -> int:
    j = i + 1
    while j < len(text):
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == '"':
            return j + 1
        j += 1
    raise ValueError("unterminated string")


def _value_end(text: str, i: int) -> int:
    c = text[i] if i < len(text) else ""
    if c == '"':
        return _string_end(text, i)
    if c in "{[":
        depth = 0
        j = i
        while j < len(text):
            d = text[j]
            if d == '"':
                j = _string_end(text, j)
                continue
            if d in "{[":
                depth += 1
            elif d in "}]":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise ValueError("unterminated object or array")
    m = re.match(r"[^,}\]\s]+", text[i:])
    if m is None:
        raise ValueError(f"unexpected character at {i}")
    return i + len(m.group(0))


def _top_level_keys(text: str) -> tuple[int, int, list[tuple[str, str, int, int]]]:
    """The open and close offsets of the top object, and each key with its indent and value span."""
    start = _skip_space(text, 0)
    if start >= len(text) or text[start] != "{":
        raise ValueError("the top level is not an object")
    keys: list[tuple[str, str, int, int]] = []
    i = _skip_space(text, start + 1)
    while i < len(text) and text[i] != "}":
        if text[i] != '"':
            raise ValueError(f"expected a key at {i}")
        end = _string_end(text, i)
        key = json.loads(text[i:end])
        line_start = text.rfind("\n", 0, i) + 1
        indent_match = re.match(r"[ \t]*", text[line_start:i])
        indent = indent_match.group(0) if indent_match else ""
        j = _skip_space(text, end)
        if j >= len(text) or text[j] != ":":
            raise ValueError(f"expected : at {j}")
        j = _skip_space(text, j + 1)
        value_end = _value_end(text, j)
        keys.append((key, indent, j, value_end))
        i = _skip_space(text, value_end)
        if i < len(text) and text[i] == ",":
            i = _skip_space(text, i + 1)
    return start, i, keys


def _serialize(value: Any, indent: str) -> str:
    return dump_json(value).replace("\n", f"\n{indent}")


def splice_top_level_key(text: str, key: str, value: Any, file: str) -> str:
    """The JSON object `text` with top-level `key` set to `value`.

    An existing value is replaced in place. A missing key is added after the last one. `file`
    names the text in errors.
    """
    try:
        json.loads(text)
    except json.JSONDecodeError as error:
        raise HarnessError(f"{file} is not valid JSON: {error}") from error
    try:
        start, close, keys = _top_level_keys(text)
    except ValueError as error:
        raise HarnessError(f"{file}: {error}") from error
    for name, indent, value_start, value_end in keys:
        if name == key:
            return text[:value_start] + _serialize(value, indent) + text[value_end:]
    indent = keys[-1][1] if keys else "  "
    entry = f"{json.dumps(key)}: {_serialize(value, indent)}"
    if not keys:
        return f"{text[: start + 1]}\n{indent}{entry}\n{text[close:]}"
    last_end = keys[-1][3]
    return f"{text[:last_end]},\n{indent}{entry}{text[last_end:]}"


def settings_file(current: str | None, plan: Plan) -> str:
    """`.claude/settings.json` with the harness entries of its `hooks` key replaced."""
    hooks = merged(registry(plan, "claude"), project_entries(current, SETTINGS_FILE))
    return splice_top_level_key(current or "{}\n", "hooks", hooks, SETTINGS_FILE)


def agents_block(plan: Plan, skills: Sequence[str], adapter_dir: str) -> str:
    """The marked block of AGENTS.md: the rules for Codex, which does not load `.claude/rules/`."""
    lines = [
        BLOCK_START,
        "## Harness rules and skills",
        "",
        "`ah install` writes this block from the harness manifests. Do not edit it by hand.",
        "Claude Code loads the rule files by itself. Codex does not load `.claude/rules/` and does"
        " not expand",
        "`@` imports, so read each rule below that covers the files you change before you change"
        " them.",
        "",
    ]
    if plan.rules:
        lines += ["| Rule | Covers | File |", "|---|---|---|"]
        for rule in plan.rules:
            covers = ", ".join(f"`{p}`" for p in rule.paths) if rule.paths else "every file"
            covers = rule.agents_covers or covers
            lines.append(f"| {rule.summary} | {covers} | `{rule.target}` |")
        lines.append("")
    if skills:
        names = ", ".join(f"`{s}`" for s in skills)
        lines += [
            f"Skills: {names}. Claude Code runs one as `/<name>`, Codex as `$<name>`.",
            "A skill reads project facts from `.agents/harness.toml` and free-text notes from"
            f" `{adapter_dir}/<skill name without lw->/`.",
            "",
        ]
    lines.append(BLOCK_END)
    return "\n".join(lines)


def with_agents_block(current: str | None, block: str) -> str:
    """`AGENTS.md` with the block replaced, or added at the end."""
    if current is None:
        return f"# Agent rules\n\n{block}\n"
    start = current.find(BLOCK_START)
    end = current.find(BLOCK_END)
    if start != -1 and end > start:
        return current[:start] + block + current[end + len(BLOCK_END) :]
    base = current if current.endswith("\n") else f"{current}\n"
    return f"{base}\n{block}\n"


def with_ignored(current: str | None) -> str:
    """`.gitignore` with each line of `IGNORED` added at the end, unless a form of it is there."""
    text = current or ""
    present = {line.strip() for line in text.split("\n")}
    for line, forms in IGNORED:
        if present.isdisjoint(forms):
            if text and not text.endswith("\n"):
                text += "\n"
            text += f"{line}\n"
            present.add(line)
    return text


def link_target(skill_id: str) -> str:
    """The relative target of the `.agents/skills/<id>` link."""
    return f"../../{SKILL_DIR}/{skill_id}"
