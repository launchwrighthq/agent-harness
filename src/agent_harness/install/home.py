"""The global install: the engine in `~/.claude/agent-harness/`, the skills in `~/.claude/skills/`.

A global install writes no hook, no rule, no registry and no git hook.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_harness import HarnessError, __version__
from agent_harness.install.lock import (
    Lock,
    Out,
    apply_files,
    file_differences,
    merge,
    read_all,
    read_lock,
    sha256,
    write_text,
)
from agent_harness.install.plan import (
    GLOBAL_ENGINE_DIR,
    GLOBAL_ENGINE_PARTS,
    GLOBAL_LOCK_FILE,
    GLOBAL_SKILL_DIR,
    MANIFEST_FILE,
    PACKAGE_NAME,
    build_plan,
    engine_files,
)
from agent_harness.manifest import read_manifest


def claude_home() -> Path:
    """`$HOME/.claude`. HOME decides, so a test can point it at a temp folder."""
    home = os.environ.get("HOME")
    if not home:
        raise HarnessError("HOME is not set, so the global install has no folder")
    return Path(home) / ".claude"


def install_global(home: Path, package_root: Path, out: Out) -> int:
    """Install the engine and the default skills of the package under `home`."""
    package = read_manifest(package_root / MANIFEST_FILE, name=PACKAGE_NAME, extra=False)
    plan = build_plan([package], skill_dir=GLOBAL_SKILL_DIR, engine_dir=GLOBAL_ENGINE_DIR)
    skill_files = [f for f in plan.files if f.target.startswith(f"{GLOBAL_SKILL_DIR}/")]
    files = merge(engine_files(package_root, GLOBAL_ENGINE_PARTS, GLOBAL_ENGINE_DIR), skill_files)
    contents = read_all(files)
    previous = read_lock(home / GLOBAL_LOCK_FILE)
    changes = apply_files(home, contents, previous, out)
    lock = Lock(
        version=__version__,
        mode="global",
        manifests=[{"name": PACKAGE_NAME, "file": f"{GLOBAL_ENGINE_DIR}/{MANIFEST_FILE}"}],
        files={rel: sha256(c.data) for rel, c in contents.items()},
        executable=[rel for rel, c in contents.items() if c.executable],
        skills=[s.id for s in plan.skills],
    )
    if write_text(home, GLOBAL_LOCK_FILE, lock.to_text(), lambda _line: None):
        changes += 1
    out(
        f"ah install --global: the engine and {len(plan.skills)} skills in {home}; "
        + ("nothing changed" if changes == 0 else f"{changes} changes")
    )
    return changes


def check_global(home: Path) -> list[tuple[str, str]]:
    """Each file of the global install that is missing, edited or extra, sorted by path."""
    lock = read_lock(home / GLOBAL_LOCK_FILE)
    if lock is None:
        raise HarnessError(f"{home} has no global install; run ah install --global")
    owned = [GLOBAL_ENGINE_DIR, *(f"{GLOBAL_SKILL_DIR}/{s}" for s in lock.skills)]
    return sorted(d for d in file_differences(home, lock, owned) if d[0] != GLOBAL_LOCK_FILE)
