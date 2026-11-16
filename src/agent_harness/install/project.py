"""The per-project install, its check, and the plan of an installed project."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_harness import HarnessError, __version__
from agent_harness.config import CONFIG_FILE, Config
from agent_harness.install import registry
from agent_harness.install.lock import (
    Content,
    Lock,
    Out,
    apply_files,
    file_differences,
    link_state,
    merge,
    read_all,
    read_lock,
    remove,
    sha256,
    write_text,
)
from agent_harness.install.plan import (
    ENGINE_DIR,
    ENGINE_PARTS,
    EXTRA_DIR,
    GITHOOK_DIR,
    INTERPRETERS,
    LOCK_FILE,
    MANIFEST_FILE,
    PACKAGE_NAME,
    RULE_DIR,
    SKILL_DIR,
    Plan,
    build_plan,
    dispatcher_files,
    engine_files,
    extra_manifest_files,
    files_under,
    order,
)
from agent_harness.manifest import Manifest, read_manifest

STARTER_CONFIG = """\
# The project facts of agent-harness. Every key has a default, so this file can stay as it is.
# `ah install` writes this file only when none exists. It never rewrites it.
# `python3 .claude/agent-harness/bin/ah config get <key>` prints a value, for example
# harness.baseBranch. The agent-harness README lists every key.

[harness]
# baseBranch = "main"                 # the origin HEAD branch, else main
# gateCommand = ""                    # the command that gates a landing; empty means none
# testCommand = ""                    # the command that runs the tests; empty means none
# disable = []                        # ids of hooks, rules, skills and git hooks to leave out
# enable = []                         # ids of items that are off by default
# manifests = []                      # extra manifests of the project, relative to the root

[specs]
# dirs = ["docs/specs", "docs/superpowers/plans"]
# A QA report folder (docs/spec.md): add "docs/qa" to dirs, then:
# [specs.kinds."docs/qa"]
# kind = "qa"

[deploy]
# docs/deploy.md has examples, one for a workstation deploy with Kamal nodes
"""


def list_skill_dirs(root: Path) -> list[str]:
    """The skill folders in `.claude/skills/` that hold a SKILL.md, sorted."""
    folder = root / SKILL_DIR
    if not folder.is_dir():
        return []
    return sorted(
        p.name
        for p in folder.iterdir()
        if p.is_dir() and not p.name.startswith(".") and (p / "SKILL.md").is_file()
    )


def _texts(cfg: Config, key: str) -> list[str]:
    value = cfg.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise HarnessError(f"config key {key} must be a list of strings")
    return list(value)


def manifest_files(root: Path, cfg: Config, named: Sequence[Path]) -> list[Path]:
    """The extra manifests: each of `harness.manifests`, then each of `named`, once each."""
    out: list[Path] = []
    for rel in _texts(cfg, "harness.manifests"):
        path = (root / rel).resolve()
        if not path.is_file():
            raise HarnessError(f"harness.manifests names {rel}, which is no file under {root}")
        out.append(path)
    for path in named:
        if path.resolve() not in out:
            out.append(path.resolve())
    return out


@dataclass
class Registries:
    """What the registries hold for one plan."""

    plan: Plan
    skills: list[str]
    links: dict[str, str]
    agents_block: str


def registries(
    root: Path, cfg: Config, plan: Plan, previous: Lock | None, *, from_lock: bool = False
) -> Registries:
    """The skills, links and AGENTS.md block of `plan` and of the project's own skills.

    A skill folder that the previous lock names was installed, so it is no project skill. An
    install reads the project skills from `.claude/skills/`. A check (`from_lock`) reads them
    from the links of the lock, so a project skill added after the install changes nothing.
    """
    planned = {s.id for s in plan.skills}
    installed_before = set(previous.skills) if previous else set()
    disabled = set(_texts(cfg, "harness.disable"))
    if from_lock:
        found = sorted(Path(rel).name for rel in previous.links) if previous else []
    else:
        found = list_skill_dirs(root)
    own = [s for s in found if s not in planned and s not in installed_before and s not in disabled]
    skills = [*(s.id for s in plan.skills), *own]
    links = {f"{registry.LINK_DIR}/{s}": registry.link_target(s) for s in sorted(skills)}
    adapter_dir = cfg.get("harness.adapterDir")
    block = registry.agents_block(plan, skills, str(adapter_dir))
    return Registries(plan, skills, links, block)


def plan_for(root: Path, cfg: Config, manifests: Sequence[Manifest]) -> Plan:
    return build_plan(
        manifests,
        disable=_texts(cfg, "harness.disable"),
        enable=_texts(cfg, "harness.enable"),
        project_skills=list_skill_dirs(root),
        rule_notes=root / str(cfg.get("harness.adapterDir")) / "rules",
    )


def adapter_hashes(root: Path, plan: Plan) -> dict[str, str]:
    """A sha256 per rule adapter that the plan applies, by its path relative to `root`."""
    return {
        f.notes.relative_to(root).as_posix(): sha256(f.notes.read_bytes())
        for f in plan.files
        if f.notes is not None
    }


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise HarnessError("git is not installed; the installer needs it") from error


def hooks_path(root: Path) -> str | None:
    """The `core.hooksPath` of the repository config, or None when it is unset.

    A value in the global config does not count: the repository value overrides it.
    """
    done = _git(root, "config", "--local", "--get", "core.hooksPath")
    return done.stdout.strip() if done.returncode == 0 else None


def _hooks_dir(root: Path) -> str:
    """The `.githooks` folder of the main checkout, as an absolute path.

    Git runs a push hook in the git directory, where a relative `core.hooksPath` does not
    resolve, so `push-to-checkout` would not run.
    """
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common.returncode != 0:
        raise HarnessError(f"git rev-parse --git-common-dir failed: {common.stderr.strip()}")
    return str((Path(common.stdout.strip()) / ".." / GITHOOK_DIR).resolve())


def _set_hooks_path(root: Path, out: Out) -> int:
    if _git(root, "rev-parse", "--git-dir").returncode != 0:
        out(f"{root} is no git repository, so core.hooksPath stays unset")
        return 0
    current = hooks_path(root)
    wanted = _hooks_dir(root)
    if current is None or current.rstrip("/") in (GITHOOK_DIR, f"./{GITHOOK_DIR}"):
        done = _git(root, "config", "core.hooksPath", wanted)
        if done.returncode != 0:
            raise HarnessError(f"git config core.hooksPath failed: {done.stderr.strip()}")
        out(f"set core.hooksPath {wanted}")
        return 1
    if current.rstrip("/") not in (wanted, str(root / GITHOOK_DIR)):
        out(f"core.hooksPath is {current}, so the git hooks in {GITHOOK_DIR}/ do not run")
    return 0


def _set_receive(root: Path, out: Out) -> int:
    """Let a push to the base branch update its checkout, which `push-to-checkout` needs."""
    if _git(root, "rev-parse", "--git-dir").returncode != 0:
        return 0
    key = "receive.denyCurrentBranch"
    done = _git(root, "config", "--local", "--get", key)
    current = done.stdout.strip() if done.returncode == 0 else None
    if current is None:
        done = _git(root, "config", key, "updateInstead")
        if done.returncode != 0:
            raise HarnessError(f"git config {key} failed: {done.stderr.strip()}")
        out(f"set {key} updateInstead")
        return 1
    if current != "updateInstead":
        out(f"{key} is {current}, so the lw-land push cannot update the base checkout")
    return 0


def project_copies(
    root: Path, contents: Mapping[str, Content], previous: Lock | None, plan: Plan
) -> list[tuple[str, str]]:
    """The files of a project copy that an install would replace or leave behind, as (path,
    kind), sorted by path.

    A kind is `differs` (a planned file that the previous lock does not list holds other bytes)
    or `extra` (a file in a folder that the install owns, which neither the plan nor the
    previous lock lists). The check reports an extra file, so an install must not leave one.
    """
    locked = set(previous.files) if previous else set()
    found: list[tuple[str, str]] = []
    for rel, content in contents.items():
        path = root / rel
        if rel in locked or not path.is_file() or path.is_symlink():
            continue
        if path.read_bytes() != content.data:
            found.append((rel, "differs"))
    owned = [ENGINE_DIR, RULE_DIR, *(f"{SKILL_DIR}/{s.id}" for s in plan.skills)]
    for folder in owned:
        for name in files_under(root / folder):
            rel = f"{folder}/{name}"
            if rel not in contents and rel not in locked and rel != LOCK_FILE:
                found.append((rel, "extra"))
    return sorted(found)


def _is_dispatcher(rel: str) -> bool:
    """True for `.githooks/<name>`, the path of a dispatcher, which a project git hook can hold."""
    folder, _, name = rel.rpartition("/")
    return folder == GITHOOK_DIR and bool(name)


def _githook_advice(hooks: Sequence[str]) -> str:
    return (
        f"\nproject git hooks that the dispatcher would replace: {', '.join(hooks)}.\n"
        "Such a hook can hold a rule of the project, such as a landing rule in"
        " push-to-checkout. To keep one, keep it in .git/hooks/<name>, which the dispatcher runs"
        " (git does not version that folder, so each clone needs its own copy), or declare it as"
        " a [[githook]] of a project manifest that harness.manifests names (with the harness"
        " git hook of the same name in harness.disable)."
    )


def _take_over(root: Path, copies: Sequence[tuple[str, str]], replace: bool, out: Out) -> int:
    """Refuse a project copy, or with `replace` remove its extra files. Returns the changes."""
    if not copies:
        return 0
    hooks = [rel for rel, kind in copies if kind == "differs" and _is_dispatcher(rel)]
    if not replace:
        lines = [f"  {rel} {kind}" for rel, kind in copies]
        raise HarnessError(
            "these files hold a project copy that the install would replace or leave behind:\n"
            + "\n".join(lines)
            + "\nMove each file you still need out of these folders, then install again. Or run"
            " ah install --project --replace to replace the copy and remove its extra files."
            + (_githook_advice(hooks) if hooks else "")
        )
    changes = 0
    for rel, kind in copies:
        if kind == "differs" and rel in hooks:
            name = rel.rsplit("/", 1)[-1]
            out(
                f"replaced {rel}: a project git hook; its own rule no longer runs."
                f" Keep it in .git/hooks/{name} or as a [[githook]] of a project manifest."
            )
        elif kind == "differs":
            out(f"replaced {rel}: a project copy")
        elif remove(root, rel):
            out(f"removed {rel}: a file of a project copy")
            changes += 1
    return changes


def install_project(
    root: Path,
    cfg: Config,
    package_root: Path,
    manifest_files: Sequence[Path],
    out: Out,
    *,
    replace: bool = False,
) -> int:
    """Install the package and each extra manifest into the project at `root`.

    Returns the number of changes. A second install with no change returns 0. An install over
    a project copy of a harness file refuses and writes nothing, unless `replace` is set.
    """
    package = read_manifest(package_root / MANIFEST_FILE, name=PACKAGE_NAME, extra=False)
    extras = [read_manifest(f, extra=True) for f in manifest_files]
    manifests = order([package, *extras])
    previous = read_lock(root / LOCK_FILE)
    plan = plan_for(root, cfg, manifests)
    files = merge(
        engine_files(package_root, ENGINE_PARTS),
        plan.files,
        dispatcher_files(package_root, plan),
        extra_manifest_files(extras),
    )
    contents = read_all(files)
    # Built before the first write: they refuse a project folder that the Codex command cannot
    # name, and a hook file that is not valid JSON.
    codex_text = registry.codex_file(_read(root / registry.CODEX_FILE), plan, root)
    settings_text = registry.settings_file(_read(root / registry.SETTINGS_FILE), plan)
    changes = _take_over(root, project_copies(root, contents, previous, plan), replace, out)
    changes += apply_files(root, contents, previous, out)

    regs = registries(root, cfg, plan, previous)
    for rel in sorted(set(previous.links) - set(regs.links) if previous else ()):
        if remove(root, rel):
            out(f"removed {rel}")
            changes += 1
    for rel, target in regs.links.items():
        changes += _link(root, rel, target, out)

    texts = {
        registry.SETTINGS_FILE: settings_text,
        registry.CODEX_FILE: codex_text,
        registry.AGENTS_FILE: registry.with_agents_block(
            _read(root / registry.AGENTS_FILE), regs.agents_block
        ),
        registry.GITIGNORE_FILE: registry.with_ignored(_read(root / registry.GITIGNORE_FILE)),
    }
    for rel, text in texts.items():
        changes += write_text(root, rel, text, out)
    if not (root / CONFIG_FILE).exists():
        changes += write_text(root, CONFIG_FILE.as_posix(), STARTER_CONFIG, out)
    if plan.githooks:
        changes += _set_hooks_path(root, out)
        changes += _set_receive(root, out)

    lock = Lock(
        version=__version__,
        mode="project",
        manifests=[{"name": m.name, "file": installed_manifest(m)} for m in manifests],
        files={rel: sha256(c.data) for rel, c in contents.items()},
        executable=[rel for rel, c in contents.items() if c.executable],
        skills=[s.id for s in plan.skills],
        links=list(regs.links),
        adapters=adapter_hashes(root, plan),
    )
    if write_text(root, LOCK_FILE, lock.to_text(), lambda _line: None):
        changes += 1
    # Information only: the project removes an old copy itself (docs/install.md).
    for line in likely_duplicates(root, plan):
        out(line)
    out(
        f"ah install: {len(plan.hooks)} hooks, {len(plan.rules)} rules, {len(plan.skills)} skills,"
        f" {len(plan.githooks)} git hooks; "
        + ("nothing changed" if changes == 0 else f"{changes} changes")
    )
    return changes


def installed_manifest(m: Manifest) -> str:
    if not m.extra:
        return f"{ENGINE_DIR}/{MANIFEST_FILE}"
    return f"{EXTRA_DIR}/{m.name}/{MANIFEST_FILE}"


def _link(root: Path, rel: str, target: str, out: Out) -> int:
    path = root / rel
    state = link_state(path)
    if state == target:
        return 0
    if state == "":
        raise HarnessError(f"{rel} is a file or folder, not a link; move it to {target}")
    if state is not None:
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    out(f"linked {rel}")
    return 1


def installed_manifests(root: Path, lock: Lock) -> list[Manifest]:
    """The manifests that the lock names, read from their installed copies."""
    out = []
    for entry in lock.manifests:
        name, file = entry.get("name", ""), entry.get("file", "")
        out.append(
            read_manifest(root / file, name=name, extra=name != PACKAGE_NAME, check_files=False)
        )
    return out


# The folder of every rule file that Claude Code loads. The harness owns only `RULE_DIR` in it.
PROJECT_RULE_DIR = ".claude/rules"

# The hook files that a project also edits. The check reads them as JSON, so the layout of a
# hand-written project hook is no difference.
HOOK_FILES = (registry.SETTINGS_FILE, registry.CODEX_FILE)


def _same_json(current: str, expected: str) -> bool:
    try:
        return bool(json.loads(current) == json.loads(expected))
    except json.JSONDecodeError:
        return False


def project_hooks(root: Path) -> list[str]:
    """One line per project hook in the two hook files: an entry that no manifest declares."""
    lines = []
    for rel in HOOK_FILES:
        for event, groups in registry.project_entries(_read(root / rel), rel).items():
            for group in groups:
                matcher = group.get("matcher", "-") if isinstance(group, dict) else "-"
                entries = group.get("hooks") if isinstance(group, dict) else None
                for entry in entries if isinstance(entries, list) else [group]:
                    command = entry.get("command") if isinstance(entry, dict) else None
                    lines.append(
                        f"{rel} {event} {matcher}: a project hook that no manifest declares:"
                        f" {command if isinstance(command, str) else json.dumps(entry)}"
                    )
    return lines


def _script_stem(word: str) -> str | None:
    """The file name of a script word without its suffix, or None for a word that is no script."""
    name = word.strip("\"'").rsplit("/", 1)[-1]
    stem, dot, suffix = name.rpartition(".")
    return stem if dot and f".{suffix}" in INTERPRETERS and stem else None


def likely_duplicates(root: Path, plan: Plan) -> list[str]:
    """One line per project hook or skill that likely duplicates a harness one.

    A project hook entry is a likely duplicate when a word of its command is a script with the
    file name of a harness hook script, suffix aside. A project skill is one when its folder name
    is a harness skill name without the `lw-` prefix.
    """
    hooks = {_script_stem(h.script): h.id for h in plan.hooks}
    lines = []
    for rel in HOOK_FILES:
        for event, groups in registry.project_entries(_read(root / rel), rel).items():
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    continue
                for entry in group["hooks"]:
                    command = entry.get("command") if isinstance(entry, dict) else None
                    if not isinstance(command, str):
                        continue
                    for word in command.split():
                        hook_id = hooks.get(_script_stem(word))
                        if hook_id is None:
                            continue
                        name = word.strip("\"'").rsplit("/", 1)[-1]
                        lines.append(
                            f"{rel} {event} {group.get('matcher', '-')}: a likely duplicate of"
                            f" the harness hook {hook_id} (same script name {name}): {command}"
                        )
                        break
    planned = {s.id for s in plan.skills}
    for name in list_skill_dirs(root):
        if name not in planned and f"lw-{name}" in planned:
            lines.append(f"{SKILL_DIR}/{name}: a likely duplicate of the harness skill lw-{name}")
    return lines


def duplicate_lines(root: Path, cfg: Config) -> list[str]:
    """The likely duplicates of an installed project, from the manifests that the lock names."""
    lock = require_lock(root)
    if any(not (root / m.get("file", "")).is_file() for m in lock.manifests):
        return []
    return likely_duplicates(root, plan_for(root, cfg, installed_manifests(root, lock)))


def rule_sizes(root: Path) -> list[tuple[str, int, int]]:
    """Each installed rule with its line count and character count, sorted by path."""
    lock = require_lock(root)
    installed = sorted(rel for rel in lock.files if rel.startswith(f"{RULE_DIR}/"))
    return [_size(root, rel) for rel in installed]


def project_rule_sizes(root: Path) -> list[tuple[str, int, int]]:
    """Each rule file of the project: a `.md` file under `.claude/rules/` outside the harness
    folder, which Claude Code loads too. Sorted by path."""
    folder = root / PROJECT_RULE_DIR
    if not folder.is_dir():
        return []
    found = (path.relative_to(root).as_posix() for path in folder.rglob("*.md") if path.is_file())
    own = sorted(rel for rel in found if not rel.startswith(f"{RULE_DIR}/"))
    return [_size(root, rel) for rel in own]


def _size(root: Path, rel: str) -> tuple[str, int, int]:
    text = (root / rel).read_text(encoding="utf-8")
    return rel, len(text.splitlines()), len(text)


def require_lock(root: Path) -> Lock:
    lock = read_lock(root / LOCK_FILE)
    if lock is None:
        raise HarnessError(f"{root} has no project install; run ah install --project")
    return lock


def check_project(root: Path, cfg: Config) -> list[tuple[str, str]]:
    """Each installed file and registry that differs, as (path, kind), sorted by path.

    A kind is `missing`, `edited`, `extra`, `mode` (no longer executable) or `differs` (a
    registry that differs from the installed manifests).
    """
    lock = require_lock(root)
    owned = [ENGINE_DIR, RULE_DIR, *(f"{SKILL_DIR}/{s}" for s in lock.skills)]
    diffs = [d for d in file_differences(root, lock, owned) if d[0] != LOCK_FILE]
    # Without every installed manifest, the registries cannot be rebuilt. The lock names the
    # missing manifest, so the file differences already report it.
    if any(not (root / m.get("file", "")).is_file() for m in lock.manifests):
        return sorted(diffs)
    plan = plan_for(root, cfg, installed_manifests(root, lock))
    adapters = adapter_hashes(root, plan)
    for rel in sorted(set(adapters) | set(lock.adapters)):
        if adapters.get(rel) != lock.adapters.get(rel):
            diffs.append((rel, "stale"))
    regs = registries(root, cfg, plan, lock, from_lock=True)
    expected = {
        registry.SETTINGS_FILE: registry.settings_file(_read(root / registry.SETTINGS_FILE), plan),
        registry.CODEX_FILE: registry.codex_file(_read(root / registry.CODEX_FILE), plan, root),
        registry.AGENTS_FILE: registry.with_agents_block(
            _read(root / registry.AGENTS_FILE), regs.agents_block
        ),
        registry.GITIGNORE_FILE: registry.with_ignored(_read(root / registry.GITIGNORE_FILE)),
    }
    for rel, text in expected.items():
        current = _read(root / rel)
        if current is None:
            diffs.append((rel, "missing"))
        elif current != text and not (rel in HOOK_FILES and _same_json(current, text)):
            diffs.append((rel, "differs"))
    for rel, target in regs.links.items():
        state = link_state(root / rel)
        if state is None:
            diffs.append((rel, "missing"))
        elif state != target:
            diffs.append((rel, "differs"))
    for rel in lock.links:
        if rel not in regs.links and link_state(root / rel) is not None:
            diffs.append((rel, "extra"))
    # The install leaves core.hooksPath unset outside a git repository, so the check does too.
    is_repo = _git(root, "rev-parse", "--git-dir").returncode == 0
    if plan.githooks and is_repo and hooks_path(root) is None:
        diffs.append(("core.hooksPath", "missing"))
    return sorted(diffs)
