#!/usr/bin/env python3
"""Refuse a live production credential file.

The guarded names are `harness.guardedFiles` of the config (default `.env.production` and any
`*.secret`). In Bash, a command that holds such a name is refused, whatever program it runs: a
list of readers misses `source`, `< file` and `python3 -c open(...)`. The file tools are refused
every env file except a `.example` and a name of `harness.plainEnvFiles` that is not guarded. A
credential backup in the tree (`.env.production.bak`, `.local/secrets/`) is refused too, also
the backup of a plain file. The one exemption is the words of an env engine step that
`env_guard.check_line` clears. docs/hooks.md, "The credential name guard", gives the reasons.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import env_guard, hookio  # noqa: E402

HOOK = "deny-env-reads"

# Characters a path is made of, so a guarded name ends where one of the others starts.
NOT_PATH = re.compile(r"[^A-Za-z0-9._@+~/-]+")

FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob"}


class Guard:
    def __init__(self, payload: dict[str, Any]) -> None:
        cfg = hookio.hook_config(HOOK, __file__)
        self.scope = env_guard.env_scope(cfg, payload, self.credential, self.held)
        guarded = hookio.harness_value(cfg, "guardedFiles")
        self.guarded = [
            (entry, hookio.name_pattern(entry["pattern"]))
            for entry in hookio.patterns(guarded, HOOK)
        ]
        self.plain = hookio.plain_env_test(cfg, HOOK)
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
        """True for a guarded name, a backup or a closed env file."""
        return self.guarded_entry(path) is not None or is_backup(path) or self.closed_env(path)

    def closed_env(self, path: str) -> bool:
        """True for an env file that is no `.example` and no plain file of the config."""
        return env_file(path) and not self.plain(path)

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
            f"To read or change it, run `{self.env_command} <subcommand>` in its own step, with no "
            "`$` or backtick in the words of a `get` or a `run`."
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
        elif self.closed_env(path):
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
        check = env_guard.check_line(command, self.env_commands, self.scope)
        denied = check.first("deny")
        if denied is not None:
            hookio.deny(denied.reason)
            return
        # The scan skips the words of each engine step that the check exempts, and reads every
        # other step and every heredoc body.
        scanned = env_guard.blank(command, check.exempt)
        parts = [f.lstrip("@") for f in NOT_PATH.split(re.sub(r"['\"\\]", "", scanned))]
        entry = next(filter(None, map(self.guarded_entry, parts)), None)
        if entry is not None:
            self.refuse("That file", entry)
            return
        # A deny wins. An ask of the check that no deny covers asks.
        asked = check.first("ask")
        if asked is not None:
            hookio.ask(asked.reason)


def env_file(path: str) -> bool:
    """True for every env file but a committed `*.example`: the names that bash-read-guard
    refuses to a reader."""
    base = hookio.basename(path)
    return hookio.ENV_NAME.fullmatch(base) is not None and not base.endswith(".example")


is_backup = hookio.is_backup


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
