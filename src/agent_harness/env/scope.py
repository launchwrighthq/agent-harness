"""Which keys of an env file a push sends and a pull takes, and the values a push refuses.

`--apps a,b` (or the store setting `apps`) keeps the keys that the declarations give to one of
those apps; an undeclared key is then out of scope. A push also keeps only the secret keys,
unless `--all`: with declarations, a key that a declaration marks `secret = false` stays home.
Without declarations, every key is in scope, as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agent_harness import HarnessError
from agent_harness.env import declarations, stores
from agent_harness.env.args import Args
from agent_harness.env.context import Context

# A value that a template or a person puts in place of a credential that nobody has filled.
PLACEHOLDERS = {"tobemodified", "changeme", "xxx"}


@dataclass
class Scope:
    apps: list[str] = field(default_factory=list)
    secrets_only: bool = False
    decl: declarations.Declarations | None = None

    def holds(self, key: str) -> bool:
        found = self.decl.find(key) if self.decl is not None else None
        if self.apps and (found is None or not any(found.read_by(app) for app in self.apps)):
            return False
        return not (self.secrets_only and found is not None and found.plain)

    def plain(self, key: str) -> bool:
        """True for a key that a declaration marks `secret = false`."""
        found = self.decl.find(key) if self.decl is not None else None
        return found is not None and found.plain


def of(ctx: Context, args: Args, store: stores.Store, secrets: bool) -> Scope:
    """The scope of one push (`secrets` True) or pull."""
    named = args.flags.get("--apps")
    configured = store.settings.get("apps") or []
    apps = named.split(",") if isinstance(named, str) else [str(app) for app in configured]
    apps = [app.strip() for app in apps if app.strip()]
    decl = ctx.declarations() if ctx.settings.get("declarations") else None
    if apps and decl is None:
        raise HarnessError("--apps needs [env] declarations, which give each key its apps")
    return Scope(apps, secrets and decl is not None and not args.flags.get("--all"), decl)


def refuse_unsafe(ctx: Context, path: Path, payload: Mapping[str, str]) -> None:
    """Refuse a key that the master of `path` does not name, and a declared secret that is
    empty or a placeholder. Without a master or declarations, the first check is skipped."""
    try:
        _, master = ctx.master_keys(path)
    except HarnessError:
        master = None
    if master is not None:
        unknown = [key for key in payload if key not in master]
        if unknown:
            raise HarnessError(
                f"{ctx.show(path)} holds keys that its master does not name: {', '.join(unknown)}. "
                "Add them to the master, or remove them from the file. Nothing was sent."
            )
    if not ctx.settings.get("declarations"):
        return
    decl = ctx.declarations()
    unset = [
        key
        for key, value in payload.items()
        if (found := decl.find(key)) is not None
        and found.secret
        and (value.strip() == "" or value.strip().lower() in PLACEHOLDERS)
    ]
    if unset:
        raise HarnessError(
            f"{ctx.show(path)} holds declared secrets that are empty or a placeholder: "
            f"{', '.join(unset)}. Set a real value first. Nothing was sent."
        )
