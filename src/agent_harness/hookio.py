"""Shared by the hook scripts: the payload, the deny answer, file name patterns and the config.

A hook script computes ROOT, the folder above its own `hooks/` folder, puts ROOT/src on
`sys.path` and imports this module. The same hook file then works in the source tree and in an
installed copy at `<project>/.claude/agent-harness/hooks/`.

The project of a hook is the checkout that holds the hook file, never a folder that the payload
names. The hook reads `[harness]` of that project's `.agents/harness.toml`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent_harness import HarnessError, config, fresh

# Every Python hook imports this module first. A hook of a stale worktree install hands over to
# the main checkout's newer copy here, before it reads its payload.
fresh.hand_over()

# The folder of the installed hooks, relative to the project root.
INSTALLED_HOOKS = (".claude", "agent-harness", "hooks")

# The env engine command forms that every project accepts, beside `harness.envCommand`.
ENGINE_COMMANDS = (
    "python3 .claude/agent-harness/bin/ah env",
    "python3 ~/.claude/agent-harness/bin/ah env",
    "ah env",
)

# Every env file name. A committed `*.example` matches too, and each caller leaves it out.
ENV_NAME = re.compile(r"\.env(\..+)?")

# A credential backup: an env file with a backup suffix, or anything under `.local/secrets/`.
BACKUP_NAME = re.compile(r"\.env(\..+)?\.(bak|backup|orig|old|save)", re.IGNORECASE)
BACKUP_DIR = re.compile(r"(^|/)\.local/secrets(/|$)")


def project_root(hook_file: str | Path) -> Path:
    """The checkout that holds `hook_file`.

    An installed hook sits three levels below the project root. A copy outside the installed
    layout (the package source) takes the top of the git checkout it sits in.
    """
    here = Path(os.path.abspath(hook_file)).parent
    if here.parts[-3:] == INSTALLED_HOOKS:
        return here.parents[2]
    return config.find_root(here)


def hook_config(hook: str, hook_file: str | Path) -> config.Config:
    """The config of the project that holds `hook_file`, or the defaults when it is not TOML.

    The reason goes to stderr, which the client shows as a hook error, so a broken config file
    is reported and the hook still guards with its defaults.
    """
    root = project_root(hook_file)
    try:
        return config.load(root)
    except HarnessError as error:
        print(
            f"{hook}: cannot read .agents/harness.toml ({error}); using the defaults",
            file=sys.stderr,
        )
        return config.Config(root, {}, None)


def harness_value(cfg: config.Config, key: str) -> Any:
    """The value of `harness.<key>`. Every key of `[harness]` has a default."""
    return cfg.get(f"harness.{key}")


def read_payload(hook: str) -> dict[str, Any] | None:
    """The payload object on stdin, or None. A payload that is not JSON says why on stderr."""
    try:
        payload = json.load(sys.stdin)
    except ValueError as error:
        print(f"{hook}: the payload is not JSON ({error}); no verdict", file=sys.stderr)
        return None
    return payload if isinstance(payload, dict) else None


def deny(reason: str) -> None:
    """Print a PreToolUse deny.

    An `allow` would bypass the permission rules, so a guard denies, asks or stays silent.
    """
    decide("deny", reason)


def ask(reason: str) -> None:
    """Print a PreToolUse ask. The client shows `reason`, and the user allows or refuses it."""
    decide("ask", reason)


def decide(decision: str, reason: str) -> None:
    """Print the PreToolUse answer `decision` with `reason`, compact."""
    answer = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(answer, separators=(",", ":"), ensure_ascii=False))


def context(event: str, text: str) -> None:
    """Print added context for the model on `event`."""
    answer = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    print(json.dumps(answer, separators=(",", ":"), ensure_ascii=False))


def name_pattern(pattern: str) -> re.Pattern[str]:
    """A file name pattern (`*` and `?` inside one name) as a regex over a whole base name.

    Match it with `fullmatch`. The match ignores case.
    """
    body = "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in pattern)
    return re.compile(body, re.IGNORECASE)


def patterns(entries: Any, hook: str) -> list[dict[str, str]]:
    """The `{pattern, hint}` entries of a config list, from strings or tables.

    An entry of another shape is left out, and the reason goes to stderr.
    """
    if not isinstance(entries, list):
        return []
    out: list[dict[str, str]] = []
    for entry in entries:
        if isinstance(entry, str) and entry:
            out.append({"pattern": entry})
        elif isinstance(entry, Mapping) and isinstance(entry.get("pattern"), str):
            out.append({str(k): str(v) for k, v in entry.items()})
        else:
            print(
                f"{hook}: the config entry {entry!r} is no file pattern; it is left out",
                file=sys.stderr,
            )
    return out


def basename(path: str) -> str:
    """The last segment of `path`, trailing slashes ignored, as Node's `path.basename` gives it."""
    return path.rstrip("/").rsplit("/", 1)[-1]


def is_backup(path: str) -> bool:
    """True for a credential backup: a backup name, or a path under `.local/secrets/`."""
    return BACKUP_NAME.fullmatch(basename(path)) is not None or bool(BACKUP_DIR.search(path))


def _no_path(path: str) -> bool:
    return False


# The characters that make a file name pattern of an entry.
WILDCARDS = "*?"


def is_exact_name(entry: Any) -> bool:
    """True for a file name in full: a text with no `*` and no `?`."""
    return isinstance(entry, str) and entry != "" and not set(entry) & set(WILDCARDS)


def plain_names(entries: Any, hook: str) -> list[str]:
    """The exact file names of `harness.plainEnvFiles`.

    An entry that is no exact name opens nothing, and the reason goes to stderr.
    """
    out: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if is_exact_name(entry):
            out.append(entry)
        else:
            print(
                f"{hook}: the plainEnvFiles entry {entry!r} is no exact file name; it opens"
                " nothing",
                file=sys.stderr,
            )
    return out


def plain_env_test(cfg: config.Config, hook: str) -> Callable[[str], bool]:
    """The test for a path that `harness.plainEnvFiles` opens to the file tools and the readers.

    The project says that such a file holds no secret. An entry is one exact name, compared with
    the case kept, and it opens an env file name only. A name that `guardedFiles` or `secretFiles`
    matches stays closed, and so does a backup: nothing rotates a copy.
    """
    plain = plain_names(harness_value(cfg, "plainEnvFiles"), hook)
    if not plain:
        return _no_path
    closed = [
        name_pattern(entry["pattern"])
        for key in ("guardedFiles", "secretFiles")
        for entry in patterns(harness_value(cfg, key), hook)
    ]

    def test(path: str) -> bool:
        base = basename(path)
        return (
            ENV_NAME.fullmatch(base) is not None
            and not is_backup(path)
            and base in plain
            and not any(rx.fullmatch(base) for rx in closed)
        )

    return test


def env_commands(cfg: config.Config) -> list[str]:
    """Every command form of the env engine: the fixed forms, the launcher of the project by its
    absolute path, and `harness.envCommand`."""
    configured = harness_value(cfg, "envCommand")
    extra = [configured.strip()] if isinstance(configured, str) and configured.strip() else []
    own = f"python3 {cfg.root.resolve()}/.claude/agent-harness/bin/ah env"
    return [*ENGINE_COMMANDS, own, *extra]
