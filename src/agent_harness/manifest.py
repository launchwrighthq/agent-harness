"""The `harness.toml` manifest: the hooks, rules, skills, git hooks and tools of one package.

Paths in a manifest are relative to the folder that holds it. `docs/install.md` describes each
key of the format:

    [[hook]]          id, summary, events, matcher, script, files, timeout, codex, default
    [[hook.fixture]]  tool, command, file, prompt, content, expect, contains, decoy
    [[rule]]          id, summary, file, paths, agentsCovers, default
    [[skill]]         id, dir, default
    [[githook]]       id, summary, name, script, files, default
    [[tool]]          id, summary, files, default
"""

from __future__ import annotations

import posixpath
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_harness import HarnessError

# The hook events in the order the registries list them.
EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "Notification",
    "SessionEnd",
)

# The events Codex runs a project hook on.
CODEX_EVENTS = frozenset(("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"))

# The events whose matcher names a tool.
TOOL_EVENTS = frozenset(("PreToolUse", "PostToolUse"))

FIXTURE_TOOLS = ("Bash", "Read", "Edit", "Write")
EXPECTS = ("deny", "ask", "block", "context", "silent")

ID = re.compile(r"[a-z0-9][a-z0-9-]*")
REL_PATH = re.compile(r"[\w./-]+", re.ASCII)
MATCHER = re.compile(r"[\w|*]+", re.ASCII)
GIT_HOOK_NAME = re.compile(r"[a-z-]+")


@dataclass(frozen=True)
class Fixture:
    """One self-test case of a hook."""

    expect: str
    tool: str | None = None
    command: str | None = None
    file: str | None = None
    prompt: str | None = None
    # The text that the self-test writes at `file` for the run and removes after it.
    content: str | None = None
    contains: str | None = None
    # The payload names a decoy checkout as its working directory.
    decoy: bool = False


@dataclass(frozen=True)
class Hook:
    id: str
    summary: str
    events: tuple[str, ...]
    script: str
    files: tuple[str, ...] = ()
    matcher: str | None = None
    timeout: int = 5
    codex: bool = True
    default: bool = True
    fixtures: tuple[Fixture, ...] = ()


@dataclass(frozen=True)
class Rule:
    id: str
    summary: str
    file: str
    # Globs of the files the rule covers. Empty means every file.
    paths: tuple[str, ...] = ()
    # What the AGENTS.md block says the rule covers, in place of `paths`.
    agents_covers: str | None = None
    default: bool = True


@dataclass(frozen=True)
class Skill:
    id: str
    dir: str
    default: bool = True


@dataclass(frozen=True)
class GitHook:
    id: str
    summary: str
    # The git hook name, for example `commit-msg`.
    name: str
    script: str
    files: tuple[str, ...] = ()
    default: bool = True


@dataclass(frozen=True)
class Tool:
    id: str
    summary: str
    files: tuple[str, ...]
    default: bool = True


Item = Hook | Rule | Skill | GitHook | Tool


@dataclass(frozen=True)
class Manifest:
    """One parsed manifest.

    `name` is the package name for the manifest of the package itself (`extra` is False), else the
    name of the extra manifest. `dir` is the folder that the paths are relative to.
    """

    name: str
    dir: Path
    file: Path
    extra: bool
    hooks: tuple[Hook, ...] = ()
    rules: tuple[Rule, ...] = ()
    skills: tuple[Skill, ...] = ()
    githooks: tuple[GitHook, ...] = ()
    tools: tuple[Tool, ...] = ()

    def items(self) -> list[Item]:
        return [*self.hooks, *self.rules, *self.skills, *self.githooks, *self.tools]


class _Reader:
    """Reads the fields of one table and collects each problem with its dotted location."""

    def __init__(self, problems: list[str], where: str, data: Any) -> None:
        self.problems = problems
        self.where = where
        self.data: Mapping[str, Any] = data if isinstance(data, Mapping) else {}
        self.used: set[str] = set()
        if not isinstance(data, Mapping):
            self.fail("", "must be a table")

    def fail(self, key: str, problem: str) -> None:
        place = f"{self.where}.{key}" if key else self.where
        self.problems.append(f"{place}: {problem}")

    def raw(self, key: str) -> Any:
        self.used.add(key)
        return self.data.get(key)

    def text(
        self,
        key: str,
        *,
        required: bool = False,
        pattern: re.Pattern[str] | None = None,
        check: Callable[[str], str | None] | None = None,
    ) -> str | None:
        value = self.raw(key)
        if value is None:
            if required:
                self.fail(key, "is required")
            return None
        if not isinstance(value, str) or value == "":
            self.fail(key, "must be a non-empty string")
            return None
        if pattern is not None and not pattern.fullmatch(value):
            self.fail(key, f"{value!r} does not match {pattern.pattern}")
            return None
        problem = check(value) if check is not None else None
        if problem is not None:
            self.fail(key, problem)
            return None
        return value

    def rel_path(self, key: str, *, required: bool = False) -> str | None:
        return self.text(key, required=required, pattern=REL_PATH, check=_inside)

    def texts(self, key: str, *, path: bool = False, min_items: int = 0) -> tuple[str, ...]:
        value = self.raw(key)
        if value is None:
            value = []
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            self.fail(key, "must be a list of non-empty strings")
            return ()
        if len(value) < min_items:
            self.fail(key, f"needs at least {min_items} entries")
        if path:
            for entry in value:
                problem = None if REL_PATH.fullmatch(entry) else "use letters, digits, _ . / and -"
                problem = problem or _inside(entry)
                if problem is not None:
                    self.fail(key, f"{entry!r}: {problem}")
        return tuple(value)

    def flag(self, key: str, default: bool) -> bool:
        value = self.raw(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            self.fail(key, "must be true or false")
            return default
        return value

    def on_off(self) -> bool:
        value = self.raw("default")
        if value is None:
            return True
        if isinstance(value, bool):
            return value
        if value in ("on", "off"):
            return bool(value == "on")
        self.fail("default", 'must be "on", "off" or a boolean')
        return True

    def done(self) -> None:
        for key in sorted(set(self.data) - self.used):
            self.fail(key, "is not a key of this table")


def _inside(path: str) -> str | None:
    if path.startswith("/") or posixpath.normpath(path).startswith(".."):
        return "must stay inside the manifest folder"
    return None


def _fixture(problems: list[str], where: str, data: Any) -> Fixture | None:
    r = _Reader(problems, where, data)
    tool = r.text("tool")
    if tool is not None and tool not in FIXTURE_TOOLS:
        r.fail("tool", f"must be one of {', '.join(FIXTURE_TOOLS)}")
    expect = r.text("expect", required=True)
    if expect is not None and expect not in EXPECTS:
        r.fail("expect", f"must be one of {', '.join(EXPECTS)}")
    content = r.raw("content")
    if content is not None and not isinstance(content, str):
        r.fail("content", "must be a string")
    fixture = Fixture(
        expect=expect or "silent",
        tool=tool,
        command=r.text("command"),
        file=r.text("file"),
        prompt=r.text("prompt"),
        content=content if isinstance(content, str) else None,
        contains=r.text("contains"),
        decoy=r.flag("decoy", False),
    )
    r.done()
    if fixture.prompt is None and fixture.tool is None:
        r.fail("", "a fixture needs a prompt or a tool")
    if fixture.tool == "Bash" and fixture.command is None:
        r.fail("", "a Bash fixture needs a command")
    if fixture.tool not in (None, "Bash") and fixture.file is None:
        r.fail("", "a file tool fixture needs a file")
    if fixture.content is not None and (fixture.file is None or _inside(fixture.file)):
        r.fail("", "a fixture with content needs a file inside the project")
    return fixture


def _hook(problems: list[str], where: str, data: Any) -> Hook:
    r = _Reader(problems, where, data)
    events = r.texts("events", min_items=1)
    for event in events:
        if event not in EVENTS:
            r.fail("events", f"{event} is not one of {', '.join(EVENTS)}")
    timeout = r.raw("timeout")
    if timeout is None:
        timeout = 5
    elif isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 < timeout <= 600:
        r.fail("timeout", "must be a whole number of seconds from 1 to 600")
        timeout = 5
    raw_fixtures = r.raw("fixture") or []
    if not isinstance(raw_fixtures, list):
        r.fail("fixture", "must be an array of tables")
        raw_fixtures = []
    fixtures = [_fixture(problems, f"{where}.fixture.{i}", f) for i, f in enumerate(raw_fixtures)]
    hook = Hook(
        id=r.text("id", required=True, pattern=ID) or "",
        summary=r.text("summary", required=True) or "",
        events=events,
        matcher=r.text("matcher", pattern=MATCHER),
        script=r.rel_path("script", required=True) or "",
        files=r.texts("files", path=True),
        timeout=timeout,
        codex=r.flag("codex", True),
        default=r.on_off(),
        fixtures=tuple(f for f in fixtures if f is not None),
    )
    r.done()
    if hook.matcher is not None and not any(e in TOOL_EVENTS for e in hook.events):
        r.fail("matcher", f"hook {hook.id} has a matcher but no tool event")
    return hook


def _table_cell(text: str) -> str | None:
    return "must be one line with no |" if set(text) & set("|\n\r") else None


def _rule(problems: list[str], where: str, data: Any) -> Rule:
    r = _Reader(problems, where, data)
    file = r.rel_path("file", required=True)
    if file is not None and not file.endswith(".md"):
        r.fail("file", "a rule is a .md file")
    rule = Rule(
        id=r.text("id", required=True, pattern=ID) or "",
        summary=r.text("summary", required=True) or "",
        file=file or "",
        paths=r.texts("paths"),
        agents_covers=r.text("agentsCovers", check=_table_cell),
        default=r.on_off(),
    )
    r.done()
    return rule


def _skill(problems: list[str], where: str, data: Any) -> Skill:
    r = _Reader(problems, where, data)
    skill = Skill(
        id=r.text("id", required=True, pattern=ID) or "",
        dir=r.rel_path("dir", required=True) or "",
        default=r.on_off(),
    )
    r.done()
    return skill


def _githook(problems: list[str], where: str, data: Any) -> GitHook:
    r = _Reader(problems, where, data)
    githook = GitHook(
        id=r.text("id", required=True, pattern=ID) or "",
        summary=r.text("summary", required=True) or "",
        name=r.text("name", required=True, pattern=GIT_HOOK_NAME) or "",
        script=r.rel_path("script", required=True) or "",
        files=r.texts("files", path=True),
        default=r.on_off(),
    )
    r.done()
    return githook


def _tool(problems: list[str], where: str, data: Any) -> Tool:
    r = _Reader(problems, where, data)
    tool = Tool(
        id=r.text("id", required=True, pattern=ID) or "",
        summary=r.text("summary", required=True) or "",
        files=r.texts("files", path=True, min_items=1),
        default=r.on_off(),
    )
    r.done()
    return tool


TABLES = {"hook": _hook, "rule": _rule, "skill": _skill, "githook": _githook, "tool": _tool}


def _invalid(file: Path, problem: str) -> HarnessError:
    return HarnessError(f"{file} is invalid: {problem}")


def parse_manifest(
    text: str, *, name: str, dir: Path, file: Path, extra: bool, check_files: bool = True
) -> Manifest:
    """Parse manifest text. `file` names it in errors.

    `check_files` makes every named path exist under `dir`. An installed manifest has only its
    hook and tool files beside it, so the installer reads it with `check_files=False`.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise _invalid(file, f"not valid TOML: {error}") from error
    problems: list[str] = []
    parsed: dict[str, list[Any]] = {}
    for key, value in data.items():
        if key not in TABLES:
            problems.append(f"{key}: is not a table of the manifest ({', '.join(TABLES)})")
            continue
        if not isinstance(value, list):
            problems.append(f"{key}: must be an array of tables ([[{key}]])")
            continue
        parsed[key] = [TABLES[key](problems, f"{key}.{i}", entry) for i, entry in enumerate(value)]
    if problems:
        raise _invalid(file, "; ".join(problems))
    manifest = Manifest(
        name=name,
        dir=dir,
        file=file,
        extra=extra,
        hooks=tuple(parsed.get("hook", [])),
        rules=tuple(parsed.get("rule", [])),
        skills=tuple(parsed.get("skill", [])),
        githooks=tuple(parsed.get("githook", [])),
        tools=tuple(parsed.get("tool", [])),
    )
    seen: set[str] = set()
    for item in manifest.items():
        if item.id in seen:
            raise _invalid(file, f"id {item.id} is used twice")
        seen.add(item.id)
    if not extra:
        for skill in manifest.skills:
            if not skill.id.startswith("lw-"):
                raise _invalid(file, f"skill {skill.id} needs the lw- prefix")
    if check_files:
        _check_files(manifest)
    return manifest


def _check_files(m: Manifest) -> None:
    paths = [
        *(p for h in m.hooks for p in (h.script, *h.files)),
        *(r.file for r in m.rules),
        *(p for g in m.githooks for p in (g.script, *g.files)),
        *(p for t in m.tools for p in t.files),
    ]
    for path in paths:
        if not (m.dir / path).is_file():
            raise _invalid(m.file, f"{path} does not exist")
    for skill in m.skills:
        if not (m.dir / skill.dir / "SKILL.md").is_file():
            raise _invalid(m.file, f"{skill.dir}/SKILL.md does not exist")


def extra_name(file: Path) -> str:
    """The name of an extra manifest: its folder for a `harness.toml`, else its file stem.

    `modules/testing/harness.toml` gives `testing`, `harness/local.toml` gives `local`.
    """
    name = file.parent.name if file.name == "harness.toml" else file.stem
    if not ID.fullmatch(name):
        raise _invalid(file, f"its name {name!r} needs lower case letters, digits and -")
    return name


def read_manifest(
    file: Path, *, name: str | None = None, extra: bool, check_files: bool = True
) -> Manifest:
    """Read the manifest at `file`. An extra manifest without `name` gets `extra_name(file)`."""
    if not file.is_file():
        raise HarnessError(f"the manifest {file} does not exist")
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as error:
        raise HarnessError(f"cannot read the manifest {file}: {error}") from error
    resolved = name if name is not None else extra_name(file)
    return parse_manifest(
        text, name=resolved, dir=file.parent, file=file, extra=extra, check_files=check_files
    )
