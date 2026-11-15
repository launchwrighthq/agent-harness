"""Fixtures of the installer tests: a small harness package, a plain git repository and `ah`."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2]
LAUNCHER = PACKAGE / "bin" / "ah"

Ah = Callable[..., subprocess.CompletedProcess[str]]

MANIFEST = """\
[[hook]]
id = "rm-guard"
summary = "Refuse rm -rf"
events = ["PreToolUse"]
matcher = "Bash"
script = "hooks/rm_guard.py"

[[hook.fixture]]
tool = "Bash"
command = "rm -rf build"
expect = "deny"
contains = "is refused"

[[hook.fixture]]
tool = "Bash"
command = "ls"
expect = "silent"

[[hook]]
id = "flag-directive"
summary = "Turn the -u flag into added context"
events = ["UserPromptSubmit"]
script = "hooks/flag_directive.py"

[[hook.fixture]]
prompt = "fix it -u"
expect = "context"
contains = "thorough"

[[hook]]
id = "session-note"
summary = "Read the session start payload"
events = ["SessionStart"]
script = "hooks/session_note.sh"

[[hook]]
id = "edit-check"
summary = "Check an edited file"
events = ["PostToolUse"]
matcher = "Write|Edit"
script = "hooks/edit_check.py"
default = "off"

[[rule]]
id = "house-style"
summary = "House style"
file = "rules/house-style.md"
paths = ["docs/**"]

[[rule]]
id = "plain"
summary = "Plain rule"
file = "rules/plain.md"

[[skill]]
id = "lw-demo"
dir = "skills/lw-demo"

[[githook]]
id = "commit-msg-demo"
summary = "Leave a commit message as it is"
name = "commit-msg"
script = "githooks/commit-msg"
"""

RM_GUARD = """\
import json, sys
payload = json.load(sys.stdin)
if "rm -rf" in payload.get("tool_input", {}).get("command", ""):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "deny", "permissionDecisionReason": "rm -rf is refused"}}))
"""

FLAG_DIRECTIVE = """\
import json, sys
payload = json.load(sys.stdin)
if payload.get("prompt", "").endswith(" -u"):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
        "additionalContext": "Be thorough."}}))
"""

FILES = {
    "harness.toml": MANIFEST,
    "hooks/rm_guard.py": RM_GUARD,
    "hooks/flag_directive.py": FLAG_DIRECTIVE,
    "hooks/session_note.sh": "cat >/dev/null\n",
    "hooks/edit_check.py": "import sys\nsys.stdin.read()\n",
    "rules/house-style.md": "# House style\n\nWrite short sentences.\n",
    "rules/plain.md": "# Plain rule\n",
    "skills/lw-demo/SKILL.md": "---\nname: lw-demo\ndescription: A demo skill.\n---\n# Demo\n",
    "skills/lw-demo/tool.sh": "#!/usr/bin/env bash\necho demo\n",
    "githooks/commit-msg": "#!/usr/bin/env bash\nexit 0\n",
}


def write_files(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if rel.endswith(".sh") or rel.startswith("githooks/"):
            path.chmod(0o755)


@pytest.fixture
def package(tmp_path: Path) -> Path:
    """A harness package: the real engine (bin, src, codex) and a small manifest of its own."""
    root = tmp_path / "package"
    root.mkdir()
    for part in ("bin", "src", "codex"):
        (root / part).symlink_to(PACKAGE / part, target_is_directory=True)
    write_files(root, FILES)
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A plain git repository with no harness config."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    return root


@pytest.fixture
def ah(repo: Path) -> Ah:
    """Run an `ah` launcher in the repository: the source launcher, or `engine` when given."""

    def run(
        *args: str, engine: Path = LAUNCHER, cwd: Path | None = None, home: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k != "AH_HANDED_OVER"}
        if home is not None:
            env["HOME"] = str(home)
        return subprocess.run(
            [sys.executable, str(engine), *args],
            cwd=cwd or repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    return run
