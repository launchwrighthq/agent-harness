import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

# The `ah` fixture of conftest.py: run a launcher in the test repository.
Ah = Callable[..., subprocess.CompletedProcess[str]]


def every_file(root: Path) -> list[str]:
    out = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        out += [str((Path(folder) / f).relative_to(root)) for f in files]
    return sorted(out)


def test_writes_only_the_engine_and_the_skills_under_home(
    repo: Path, package: Path, ah: Ah, tmp_path: Path
) -> None:
    home = tmp_path / "home-dir"
    home.mkdir()

    done = ah("install", "--global", "--from", str(package), home=home)

    assert done.returncode == 0, done.stderr
    files = every_file(home)
    tops = sorted({"/".join(Path(f).parts[:2]) for f in files})
    assert tops == [".claude/agent-harness", ".claude/skills"]
    assert ".claude/agent-harness/bin/ah" in files
    assert ".claude/skills/lw-demo/SKILL.md" in files
    assert not any("/hooks/" in f or "/codex/" in f or "/rules/" in f for f in files)
    assert not (repo / ".claude").exists()
    lock = json.loads((home / ".claude/agent-harness/lock.json").read_text())
    assert (lock["mode"], lock["skills"]) == ("global", ["lw-demo"])


def test_checks_the_global_install_against_its_lockfile(
    package: Path, ah: Ah, tmp_path: Path
) -> None:
    home = tmp_path / "home-dir"
    home.mkdir()
    ah("install", "--global", "--from", str(package), home=home)
    clean = ah("check", "--global", home=home)
    skill = home / ".claude/skills/lw-demo/SKILL.md"
    skill.write_text(skill.read_text() + "x")

    edited = ah("check", "--global", home=home)

    assert clean.returncode == 0, clean.stdout
    assert edited.returncode == 1
    assert edited.stdout == "~/.claude/skills/lw-demo/SKILL.md edited\n"


def test_refuses_an_extra_manifest_in_a_global_install(
    package: Path, ah: Ah, tmp_path: Path
) -> None:
    done = ah("install", "--global", "--from", str(package), "--manifest", "x.toml", home=tmp_path)

    assert done.returncode == 2
    assert done.stderr == "ah install: --global installs no extra manifest\n"
