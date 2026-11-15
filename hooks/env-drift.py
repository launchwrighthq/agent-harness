#!/usr/bin/env python3
"""Say at session start when a local env file disagrees with what a secret store was last given.

Each store of `harness.envDrift.stores` and of `[[env.stores]]` in the config has its own marker,
`.env.<env>.pushed-<store>`. The push command writes it. It holds the sha256 of the sorted key
and value pairs (`envfile.content_digest`), so a reformat is no drift. A marker of an older push
holds the sha256 of the file bytes, and a match on either digest is no drift.

It prints the environment, the store and the command that sends it, and never a key, a value or
any part of a file. No marker is SILENCE rather than a warning, because a machine that never
pushed is the ordinary case.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import hookio  # noqa: E402
from agent_harness.env import envfile  # noqa: E402

HOOK = "env-drift"


def live_digests(live: Path) -> set[str]:
    """The content digest and the bytes digest of a live file. A file that does not parse has
    the bytes digest only, so its marker reads as drift."""
    data = live.read_bytes()
    found = {hashlib.sha256(data).hexdigest()}
    try:
        found.add(envfile.content_digest(data.decode("utf-8")))
    except (UnicodeDecodeError, envfile.EnvFileError):
        print(f"{HOOK}: {live.name} does not parse; only its bytes are compared", file=sys.stderr)
    return found


def drift_lines(root: Path, envs: list[Any], stores: list[Any]) -> list[str]:
    """One line per environment and store whose live file no longer matches what was pushed."""
    lines: list[str] = []
    for env in envs:
        live = root / f".env.{env}"
        if not live.exists():
            continue
        digests = live_digests(live)
        for store in stores:
            if not isinstance(store, dict):
                continue
            name, push = store.get("name"), store.get("push")
            pushed = Path(f"{live}.pushed-{name}")
            if not pushed.exists() or pushed.read_text(encoding="utf-8").strip() in digests:
                continue
            send = str(push).replace("{env}", str(env))
            lines.append(
                f"env-drift: {env} holds local edits the {name} store has never been given. "
                f"`{send}` sends them."
            )
    return lines


def engine_stores(cfg: Any) -> list[dict[str, str]]:
    """One drift store per `[[env.stores]]` entry: its marker name and the engine push command."""
    command = hookio.harness_value(cfg, "envCommand") or "ah env"
    found = []
    for entry in cfg.get("env.stores"):
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            name = entry["name"]
            push = f"{command} push {{env}} --store {name}"
            found.append({"name": str(entry.get("marker") or name), "push": push})
    return found


def main() -> None:
    cfg = hookio.hook_config(HOOK, __file__)
    drift = hookio.harness_value(cfg, "envDrift")
    drift = drift if isinstance(drift, dict) else {}
    envs: list[Any] = drift["envs"] if isinstance(drift.get("envs"), list) else ["production"]
    stores: list[Any] = drift["stores"] if isinstance(drift.get("stores"), list) else []
    named = {store.get("name") for store in stores if isinstance(store, dict)}
    stores += [store for store in engine_stores(cfg) if store["name"] not in named]
    root = os.environ.get("LW_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    lines = drift_lines(Path(root).resolve(), envs, stores)
    if lines:
        event = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "SessionStart"
        hookio.context(event, "\n".join(lines))


if __name__ == "__main__":
    main()
