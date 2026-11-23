"""Turn the manifests and the config into the enabled items and the files they install.

Every path in a plan is relative to the install root: the project root for a project install, and
`~/.claude` for a global install.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent_harness import HarnessError
from agent_harness.manifest import Fixture, Item, Manifest

# The name of the package manifest in a lockfile and in messages.
PACKAGE_NAME = "agent-harness"
ENGINE_DIR = ".claude/agent-harness"
# The folders of the package that hold the source of each rule, skill and git hook.
SOURCE_PARTS = ("rules", "skills", "githooks")
# The folders of the package that a project install copies. The installed copy mirrors them, so
# it is a package root too and `ah install` runs from it.
ENGINE_PARTS = ("bin", "src", "hooks", "tools", "codex", *SOURCE_PARTS)
# A global install runs no hook, so it leaves out the hooks and the Codex adapter.
GLOBAL_ENGINE_PARTS = ("bin", "src", "tools")
MANIFEST_FILE = "harness.toml"
EXTRA_DIR = f"{ENGINE_DIR}/manifests"
LOCK_FILE = f"{ENGINE_DIR}/lock.json"
SKILL_DIR = ".claude/skills"
RULE_DIR = ".claude/rules/lw"
GITHOOK_DIR = ".githooks"
# The harness git hooks. `.githooks/<name>` is the dispatcher that runs them.
GITHOOK_OWN_DIR = f"{GITHOOK_DIR}/harness"
# The client-side hook names that the dispatcher covers, so a global hook of each name still runs.
DISPATCHED = (
    *("applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit"),
    *("prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout"),
    *("post-merge", "pre-push", "post-rewrite", "pre-auto-gc", "push-to-checkout"),
)
# The dispatcher source in the engine, relative to the package root.
DISPATCHER = "src/agent_harness/install/githook-dispatch.sh"

# The engine paths of a global install, relative to `~/.claude`.
GLOBAL_ENGINE_DIR = "agent-harness"
GLOBAL_SKILL_DIR = "skills"
GLOBAL_LOCK_FILE = f"{GLOBAL_ENGINE_DIR}/lock.json"

# The interpreter of a hook script, by suffix. Node runs only a hook of an extra manifest.
INTERPRETERS = {".py": "python3", ".sh": "bash", ".mjs": "node", ".js": "node"}
NODE_SUFFIXES = frozenset((".mjs", ".js"))

FRONT_MATTER = re.compile(r"^---\n.*?\n---\n", re.S)


def interpreter_for(script: str) -> str:
    """The program that runs a hook script, by its suffix."""
    suffix = posixpath.splitext(script)[1]
    if suffix not in INTERPRETERS:
        raise HarnessError(f"no interpreter is known for {script}; use .py or .sh")
    return INTERPRETERS[suffix]


@dataclass(frozen=True)
class PlannedHook:
    id: str
    manifest: str
    summary: str
    events: tuple[str, ...]
    matcher: str | None
    timeout: int
    codex: bool
    fixtures: tuple[Fixture, ...]
    # The installed script, relative to the install root.
    script: str


@dataclass(frozen=True)
class PlannedRule:
    id: str
    manifest: str
    summary: str
    paths: tuple[str, ...]
    target: str
    # The Covers cell of the AGENTS.md block, in place of the paths.
    agents_covers: str | None = None


@dataclass(frozen=True)
class PlannedSkill:
    id: str
    manifest: str
    target: str


@dataclass(frozen=True)
class PlannedGitHook:
    id: str
    manifest: str
    name: str
    target: str


@dataclass(frozen=True)
class PlannedFile:
    """A file that the installer copies."""

    target: str
    source: Path
    # The file is executable even when its source is not (a git hook).
    executable: bool = False
    # A rule with paths gets a `paths:` front matter block.
    paths: tuple[str, ...] | None = None
    # The rule adapter of the project: notes to append, or a body that replaces the rule body.
    notes: Path | None = None
    # A generated line that ends the rule, after the adapter.
    line: str | None = None

    def content(self) -> bytes:
        try:
            raw = self.source.read_bytes()
        except OSError as error:
            raise HarnessError(f"cannot read {self.source}: {error}") from error
        if self.paths is None and self.notes is None and self.line is None:
            return raw
        text = raw.decode("utf-8")
        found = FRONT_MATTER.match(text)
        head = found.group(0) if found else ""
        body = text[len(head) :]
        if self.paths is not None:
            lines = "\n".join(f'  - "{p}"' for p in self.paths)
            head = f"---\npaths:\n{lines}\n---\n"
        if self.notes is not None:
            body = with_notes(body, self.notes)
        if self.line is not None:
            body = f"{body.rstrip()}\n\n{self.line}\n"
        return f"{head}{body}".encode()

    def mode_executable(self) -> bool:
        return self.executable or (self.source.stat().st_mode & 0o111) != 0


def with_notes(body: str, notes: Path) -> str:
    """`body` with the rule adapter at `notes`: its body under `## Project notes`, or its body
    alone when its front matter holds `replace: true`."""
    try:
        text = notes.read_text(encoding="utf-8")
    except OSError as error:
        raise HarnessError(f"cannot read {notes}: {error}") from error
    found = FRONT_MATTER.match(text)
    replace = False
    for line in found.group(0).split("\n")[1:-2] if found else []:
        if line.strip() not in ("", "replace: true", "replace: false"):
            raise HarnessError(
                f"{notes}: unknown front matter line {line!r}; a rule adapter takes"
                " only replace: true or replace: false"
            )
        replace = replace or line.strip() == "replace: true"
    own = text[found.end() :] if found else text
    if not own.endswith("\n"):
        own += "\n"
    if replace:
        return own
    return f"{body.rstrip()}\n\n## Project notes\n\n{own}"


@dataclass
class Plan:
    hooks: list[PlannedHook] = field(default_factory=list)
    rules: list[PlannedRule] = field(default_factory=list)
    skills: list[PlannedSkill] = field(default_factory=list)
    githooks: list[PlannedGitHook] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    files: list[PlannedFile] = field(default_factory=list)
    # Every id the manifests define, enabled or not.
    known: list[str] = field(default_factory=list)


def files_under(folder: Path) -> list[str]:
    """Every file under `folder`, sorted, relative to it, with `/` separators.

    Python caches and names that start with a dot are left out.
    """
    out: list[str] = []

    def walk(rel: str) -> None:
        here = folder / rel if rel else folder
        for entry in sorted(here.iterdir(), key=lambda p: p.name):
            if entry.name == "__pycache__" or entry.name.endswith(".pyc"):
                continue
            if entry.name.startswith("."):
                continue
            path = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir():
                walk(path)
            else:
                out.append(path)

    if folder.is_dir():
        walk("")
    return out


def order(manifests: Sequence[Manifest]) -> list[Manifest]:
    """The package manifest first, then the extra manifests by name.

    The order does not depend on the order of `--manifest` options, so the registries keep their
    entry order and Codex keeps its trust.
    """
    packages = [m for m in manifests if not m.extra]
    extras = sorted((m for m in manifests if m.extra), key=lambda m: m.name)
    names = [m.name for m in extras]
    for name in names:
        if names.count(name) > 1:
            files = ", ".join(str(m.file) for m in extras if m.name == name)
            raise HarnessError(f"two extra manifests have the name {name}: {files}")
    return [*packages, *extras]


def hook_dir(m: Manifest, engine_dir: str = ENGINE_DIR) -> str:
    """The installed folder of the hook and tool files of a manifest."""
    return engine_dir if not m.extra else f"{engine_dir}/manifests/{m.name}"


def build_plan(
    manifests: Sequence[Manifest],
    *,
    disable: Iterable[str] = (),
    enable: Iterable[str] = (),
    project_skills: Iterable[str] = (),
    skill_dir: str = SKILL_DIR,
    engine_dir: str = ENGINE_DIR,
    rule_notes: Path | None = None,
    rule_paths: Mapping[str, Sequence[str]] | None = None,
    rule_lines: Mapping[str, str] | None = None,
) -> Plan:
    """The enabled items of `manifests` in a fixed order, and the files they install.

    Inside one manifest, the items keep the order it declares. `project_skills` are the skill
    folders of the project that `disable` and `enable` may also name. `rule_notes` is the folder
    of the rule adapters of the project: `<rule id>.md` changes that rule. `rule_paths` gives a
    rule id the globs of its `paths:` block, in place of the manifest paths. `rule_lines` gives a
    rule id one generated line that ends the installed rule.
    """
    ordered = order(manifests)
    disabled, enabled = set(disable), set(enable)
    owner: dict[str, Path] = {}
    for m in ordered:
        for item in m.items():
            if item.id in owner:
                raise HarnessError(f"id {item.id} is defined by {owner[item.id]} and {m.file}")
            owner[item.id] = m.file
    allowed = set(owner) | set(project_skills)
    for name in sorted(disabled | enabled):
        if name not in allowed:
            raise HarnessError(
                f"harness config names {name}, which no manifest or project skill defines"
            )

    scoped, lines = dict(rule_paths or {}), dict(rule_lines or {})
    rule_ids = {rule.id for m in ordered for rule in m.rules}
    for name in sorted(set(scoped) - rule_ids):
        raise HarnessError(
            f"harness.rules.paths names {name}, which no manifest defines as a rule;"
            f" the rule ids are: {', '.join(sorted(rule_ids))}"
        )

    def on(item: Item) -> bool:
        return item.id not in disabled and (item.default or item.id in enabled)

    plan = Plan(known=list(owner))
    targets: dict[str, Path] = {}

    def add(file: PlannedFile) -> None:
        prior = targets.get(file.target)
        if prior is not None:
            if prior == file.source:
                return
            raise HarnessError(f"{file.target} is written by two items ({prior} and {file.source})")
        targets[file.target] = file.source
        plan.files.append(file)

    for m in ordered:
        base = hook_dir(m, engine_dir)
        for hook in (h for h in m.hooks if on(h)):
            if not m.extra and posixpath.splitext(hook.script)[1] in NODE_SUFFIXES:
                raise HarnessError(f"{m.file}: hook {hook.id} needs Node; the package runs none")
            interpreter_for(hook.script)
            script = f"{base}/{hook.script}"
            for path in (hook.script, *hook.files):
                add(PlannedFile(f"{base}/{path}", m.dir / path))
            plan.hooks.append(
                PlannedHook(
                    id=hook.id,
                    manifest=m.name,
                    summary=hook.summary,
                    events=hook.events,
                    matcher=hook.matcher,
                    timeout=hook.timeout,
                    codex=hook.codex,
                    fixtures=hook.fixtures,
                    script=script,
                )
            )
        for tool in (t for t in m.tools if on(t)):
            for path in tool.files:
                add(PlannedFile(f"{base}/{path}", m.dir / path))
            plan.tools.append(tool.id)
        for rule in (r for r in m.rules if on(r)):
            target = f"{RULE_DIR}/{posixpath.basename(rule.file)}"
            notes = rule_notes / f"{rule.id}.md" if rule_notes is not None else None
            paths = tuple(scoped.get(rule.id, rule.paths))
            add(
                PlannedFile(
                    target,
                    m.dir / rule.file,
                    paths=paths or None,
                    notes=notes if notes is not None and notes.is_file() else None,
                    line=lines.get(rule.id),
                )
            )
            plan.rules.append(
                PlannedRule(rule.id, m.name, rule.summary, paths, target, rule.agents_covers)
            )
        for skill in (s for s in m.skills if on(s)):
            target = f"{skill_dir}/{skill.id}"
            source = m.dir / skill.dir
            for path in files_under(source):
                add(PlannedFile(f"{target}/{path}", source / path))
            plan.skills.append(PlannedSkill(skill.id, m.name, target))
        for githook in (g for g in m.githooks if on(g)):
            if any(g.name == githook.name for g in plan.githooks):
                raise HarnessError(f"two git hooks are named {githook.name}")
            target = f"{GITHOOK_OWN_DIR}/{githook.name}"
            add(PlannedFile(target, m.dir / githook.script, executable=True))
            for path in githook.files:
                add(PlannedFile(f"{GITHOOK_OWN_DIR}/{posixpath.basename(path)}", m.dir / path))
            plan.githooks.append(PlannedGitHook(githook.id, m.name, githook.name, target))
    return plan


def engine_files(
    package_root: Path, parts: Sequence[str], engine_dir: str = ENGINE_DIR
) -> list[PlannedFile]:
    """The engine copy: each folder of `parts` and the package manifest."""
    out = [
        PlannedFile(f"{engine_dir}/{part}/{path}", package_root / part / path)
        for part in parts
        for path in files_under(package_root / part)
    ]
    out.append(PlannedFile(f"{engine_dir}/{MANIFEST_FILE}", package_root / MANIFEST_FILE))
    return out


def dispatcher_files(package_root: Path, plan: Plan) -> list[PlannedFile]:
    """One dispatcher per client-side hook name and per harness git hook name, when the plan has
    a git hook."""
    if not plan.githooks:
        return []
    names = dict.fromkeys([*DISPATCHED, *(g.name for g in plan.githooks)])
    source = package_root / DISPATCHER
    return [PlannedFile(f"{GITHOOK_DIR}/{name}", source, executable=True) for name in names]


def extra_manifest_files(manifests: Sequence[Manifest]) -> list[PlannedFile]:
    """The copy of each extra manifest beside its installed hook and tool files."""
    return [
        PlannedFile(f"{EXTRA_DIR}/{m.name}/{MANIFEST_FILE}", m.file)
        for m in order(manifests)
        if m.extra
    ]
