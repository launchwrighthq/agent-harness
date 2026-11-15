#!/usr/bin/env python3
"""Refuse a live production credential file.

The guarded names are `harness.guardedFiles` of the config (default `.env.production` and any
`*.secret`). In Bash, a command that holds such a name is refused, whatever program it runs: a
list of readers misses `source`, `< file` and `python3 -c open(...)`. The file tools are refused
every env file except a `.example`. A credential backup in the tree (`.env.production.bak`,
`.local/secrets/`) is refused too. The one exemption is a lone run of the env engine
(`hookio.lone_env_command`, `hookio.env_reveal`, `hookio.env_run_verdict`). docs/hooks.md, "The
credential name guard", gives the reasons.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import hookio  # noqa: E402

HOOK = "deny-env-reads"

# Characters a path is made of, so a guarded name ends where one of the others starts.
NOT_PATH = re.compile(r"[^A-Za-z0-9._@+~/-]+")

# A credential backup: an env file with a backup suffix, or anything under `.local/secrets/`.
BACKUP_NAME = re.compile(r"\.env(\..+)?\.(bak|backup|orig|old|save)", re.IGNORECASE)
BACKUP_DIR = re.compile(r"(^|/)\.local/secrets(/|$)")

FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob"}

# Every env file but a committed `*.example`: the names that bash-read-guard refuses to a reader.
ENV_FILE = re.compile(r"\.env(\..+)?")


class Guard:
    def __init__(self, payload: dict[str, Any]) -> None:
        cfg = hookio.hook_config(HOOK, __file__)
        self.scope = hookio.env_scope(cfg, payload, self.credential, self.held)
        guarded = hookio.harness_value(cfg, "guardedFiles")
        self.guarded = [
            (entry, hookio.name_pattern(entry["pattern"]))
            for entry in hookio.patterns(guarded, HOOK)
        ]
        self.env_commands = hookio.env_commands(cfg)
        configured = hookio.harness_value(cfg, "envCommand")
        self.env_command = configured if isinstance(configured, str) and configured else "ah env"

    def guarded_entry(self, name: str) -> dict[str, str] | None:
        """The guarded entry `name` matches, or None. A `.example` is never a secret."""
        base = hookio.basename(name)
        if base == "" or base.endswith(".example"):
            return None
        return next((entry for entry, rx in self.guarded if rx.fullmatch(base)), None)

    def credential(self, path: str) -> bool:
        """True for a guarded name, a backup or an env file that is no `.example`."""
        return self.guarded_entry(path) is not None or is_backup(path) or env_file(path)

    def held(self, path: str) -> bool:
        """True for a guarded name or a backup: an `ah env run` of it always asks."""
        return self.guarded_entry(path) is not None or is_backup(path)

    def refuse_backup(self, name: str) -> None:
        hookio.deny(
            f"{name} is a credential backup in the tree, and this hook refuses it. Secrets go to "
            f"the secret store through `{self.env_command}`, never into a copy that nothing "
            "rotates."
        )

    def refuse(self, name: str, entry: dict[str, str]) -> None:
        how = entry.get("hint") or (
            f"To read or change it, run `{self.env_command} <subcommand>` ALONE (no cd, pipe, ;, "
            "&&, $, <, >, newline, double quote or backslash outside single quotes)."
        )
        hookio.deny(
            f"{name} holds live production credentials, and this hook refuses any command naming "
            f"it. {how} Keep the name out of shell text such as a commit message."
        )

    def check_file(self, path: str) -> None:
        if is_backup(path):
            self.refuse_backup(hookio.basename(path))
            return
        entry = self.guarded_entry(path)
        if entry is not None:
            self.refuse(hookio.basename(path), entry)
        elif env_file(path):
            hookio.deny(
                f"{hookio.basename(path)} is an env file, and `.claude/rules/lw/secrets.md` "
                f"forbids reading one. Read `.env.example` for the key names, or run "
                f"`{self.env_command} keys <file>` or `{self.env_command} get <KEY> <file>`; they "
                "print no value."
            )

    def check_command(self, command: str) -> None:
        # `curl -d @.env.production` names the file after an `@`.
        fragments = [f.lstrip("@") for f in NOT_PATH.split(re.sub(r"['\"\\]", "", command))]
        # A backup is refused even to the env engine: nothing rotates a copy.
        if any(is_backup(fragment) for fragment in fragments):
            self.refuse_backup("That file")
            return
        key = hookio.env_reveal(command, self.env_commands, self.scope)
        verdict = hookio.env_run_verdict(command, self.env_commands, self.scope)
        if verdict is not None:
            hookio.answer_env_run(verdict)
            return
        inner = hookio.env_run_command(command, self.env_commands, self.scope)
        # A lone engine run is exempt, except the command that `run` starts.
        if not hookio.lone_env_command(command, self.env_commands, self.scope):
            scanned: str | None = command
        else:
            scanned = inner
        if scanned is not None:
            parts = [f.lstrip("@") for f in NOT_PATH.split(re.sub(r"['\"\\]", "", scanned))]
            entry = next(filter(None, map(self.guarded_entry, parts)), None)
            if entry is not None:
                self.refuse("That file", entry)
                return
        # A deny wins. A reveal that no deny covers asks, in a lone run or not.
        if key is not None:
            hookio.ask_env(command, self.env_commands, key)


def env_file(path: str) -> bool:
    base = hookio.basename(path)
    return ENV_FILE.fullmatch(base) is not None and not base.endswith(".example")


def is_backup(path: str) -> bool:
    return BACKUP_NAME.fullmatch(hookio.basename(path)) is not None or bool(BACKUP_DIR.search(path))


def main() -> None:
    payload = hookio.read_payload(HOOK)
    if payload is None:
        return
    tool = payload.get("tool_name")
    fields: Any = payload.get("tool_input") or {}
    if not isinstance(fields, dict):
        return
    if tool in FILE_TOOLS:
        path = fields.get("file_path") or fields.get("path") or fields.get("notebook_path") or ""
        Guard(payload).check_file(str(path))
        return
    if tool != "Bash":
        return
    command = str(fields.get("command") or "")
    if command:
        Guard(payload).check_command(command)


if __name__ == "__main__":
    main()
