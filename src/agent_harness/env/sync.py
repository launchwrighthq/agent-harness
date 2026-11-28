"""The secret store subcommands of `ah env`: `push`, `pull` and `diff <env> --store`."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_harness import HarnessError
from agent_harness.env import envfile, layout, scope, stores
from agent_harness.env.args import ENV_NAME, Args, UsageError, expect, parse_args
from agent_harness.env.context import Context


def diff_keys(
    left: Mapping[str, str], right: Mapping[str, str]
) -> tuple[list[str], list[str], list[str]]:
    only_left = [k for k in left if k not in right]
    only_right = [k for k in right if k not in left]
    differ = [k for k in left if k in right and left[k] != right[k]]
    return only_left, only_right, differ


def store_settings(ctx: Context, name: str | None) -> dict[str, Any]:
    """The settings of the store `name` of `[[env.stores]]`, or of the one configured store.

    In `[[env.stores]]`, `name` names the store and is its marker unless it sets `marker`. The
    secret of a kind that `[env.store]` names by `name` is named by `secret` there.
    """
    named = [dict(entry) for entry in ctx.settings.get("stores") or []]
    names = ", ".join(str(entry.get("name")) for entry in named) or "none"
    if name is not None:
        found = next((entry for entry in named if entry.get("name") == name), None)
        if found is None:
            raise HarnessError(f"no secret store is named {name}. Stores: {names}")
        return _named(found)
    single = dict(ctx.settings.get("store") or {})
    if single.get("kind") or not named:
        return single
    if len(named) > 1:
        raise UsageError(f"several secret stores are configured: pass --store NAME, one of {names}")
    return _named(named[0])


def _named(entry: dict[str, Any]) -> dict[str, Any]:
    label = entry.pop("name", "")
    return {"marker": label, **entry, "name": entry.get("secret", "")}


def store_name(args: Args) -> str | None:
    name = args.flags.get("--store")
    return name if isinstance(name, str) else None


def _store(ctx: Context, env: str, name: str | None = None) -> tuple[stores.Store, Path]:
    if not ENV_NAME.match(env) or env == "example":
        raise UsageError("<env> is lower case letters, digits, - and _, and is not example")
    settings = store_settings(ctx, name)
    store = stores.open_store(settings, env, ctx.root, ctx.runner)
    file_pattern = str(settings.get("file") or ".env.{env}")
    path = ctx.at(file_pattern.replace("{env}", env))
    store.known = tuple(envfile.read(path).values().values()) if path.is_file() else ()
    return store, path


def store_diff(ctx: Context, env: str, name: str | None = None) -> int:
    store, path = _store(ctx, env, name)
    local = envfile.read(path).values() if path.is_file() else {}
    remote = store.fetch()
    if remote is None:
        ctx.out(f"the {store.kind} store holds nothing for {env}")
        remote = {}
    only_local, only_store, differ = diff_keys(local, remote)
    for key in only_local:
        ctx.out(f"only in {ctx.show(path)}: {key}")
    for key in only_store:
        ctx.out(f"only in the store: {key}")
    for key in differ:
        ctx.out(f"differs: {key}")
    if not (only_local or only_store or differ):
        ctx.out(f"{ctx.show(path)} and the {store.kind} store hold the same values")
        return 0
    return 1


def write_marker(path: Path, store: stores.Store) -> Path:
    """`<file>.pushed-<marker>`: the content digest of the file that the store holds now."""
    marker = path.with_name(f"{path.name}.pushed-{store.name}")
    digest = envfile.content_digest(path.read_text(encoding="utf-8"))
    envfile.write_text(marker, digest + "\n")
    return marker


def push_command(args: Args, env: str) -> str:
    """The push command line that a refusal names, without its confirmation flags."""
    name = store_name(args)
    return f"ah env push {env}" + (f" --store {name}" if name else "")


def _refuse_protected_create(ctx: Context, args: Args, env: str) -> None:
    """A protected environment is made by a person, with its rules, never by a push."""
    protected = [str(name) for name in ctx.settings.get("protected") or []]
    if args.flags.get("--create-environment") and env in protected:
        raise HarnessError(
            f"--create-environment: {env} is a protected environment. A person creates it in "
            "GitHub (Settings > Environments) with its protection rules."
        )


def guard_protected(ctx: Context, args: Args, env: str) -> None:
    """A push to an environment of `[env] protected` needs `--yes --confirm <env>`."""
    protected = [str(name) for name in ctx.settings.get("protected") or []]
    if env in protected and not (args.flags.get("--yes") and args.flags.get("--confirm") == env):
        raise HarnessError(
            f"{env} is a protected environment. Run {push_command(args, env)} --yes --confirm "
            f"{env} to push to it."
        )


def cmd_push(ctx: Context, argv: list[str]) -> int:
    args = parse_args(
        argv,
        values=("--store", "--confirm", "--apps"),
        switches=("--dry-run", "--yes", "--all", "--create-environment"),
        repeats=("--drop",),
    )
    (env,) = expect(args, 1, 1, "<env>")
    _refuse_protected_create(ctx, args, env)
    store, path = _store(ctx, env, store_name(args))
    if args.flags.get("--create-environment"):
        if not isinstance(store, stores.GithubSecrets):
            raise HarnessError("--create-environment applies to the github store only")
        store.create_environment = True
    keys = scope.of(ctx, args, store, secrets=True)
    payload = {k: v for k, v in envfile.read(path).values().items() if keys.holds(k)}
    scope.refuse_unsafe(ctx, path, payload)
    remote = store.fetch() if store.can_pull else None
    current = {k: v for k, v in (remote or {}).items() if keys.holds(k)}
    only_local, only_store, differ = diff_keys(payload, current)
    drops = _drops(ctx, args, store, path, only_store)
    dry_run = bool(args.flags.get("--dry-run"))
    asking = _confirm_rules(ctx, args, env, dry_run)
    verb = "would send" if dry_run or asking else "sent"
    changed = list(payload) if not store.can_pull else only_local + differ
    for key in changed:
        ctx.out(f"{verb} {key}")
    for key in drops:
        ctx.out(f"{'would drop' if verb == 'would send' else 'dropped'} {key}")
    if dry_run:
        ctx.out(f"dry run: {len(changed)} keys to the {store.kind} store for {env}")
        return 0
    if asking and not _agreed(
        ctx, f"push {len(changed)} keys of {ctx.show(path)} to the {store.kind} store for {env}?"
    ):
        ctx.out("push cancelled; nothing sent")
        return 1
    if not (changed or drops) and remote is not None:
        ctx.out(f"the {store.kind} store already holds the values of {ctx.show(path)}")
    else:
        kept = {k: v for k, v in (remote or {}).items() if not keys.holds(k)}
        plain = frozenset(k for k in payload if keys.plain(k))
        store.send({**kept, **payload} if store.whole else payload, remote, plain)
        ctx.out(f"pushed {ctx.show(path)} to the {store.kind} store for {env}: {len(changed)} keys")
    marker = write_marker(path, store)
    ctx.out(f"wrote {ctx.show(marker)}")
    return 0


def _drops(
    ctx: Context, args: Args, store: stores.Store, path: Path, only_store: list[str]
) -> list[str]:
    """The `--drop` keys. A whole-file store refuses to drop any other key of the scope that
    only it holds."""
    drops = args.lists.get("--drop", [])
    for key in drops:
        if key not in only_store:
            raise HarnessError(f"--drop {key}: the store holds no such key that the file lacks")
    refused = [k for k in only_store if k not in drops]
    if store.whole and refused:
        raise HarnessError(
            f"the store holds keys that {ctx.show(path)} lacks: {' '.join(refused)}. "
            "A push replaces the whole secret and drops them. Add them to the file, or "
            "pass --drop KEY for each key that nothing reads any more"
        )
    return drops if store.whole else []


def _confirm_rules(ctx: Context, args: Args, env: str, dry_run: bool) -> bool:
    """True when the push must ask on the terminal. Raises when it may not run at all."""
    if dry_run:
        return False
    guard_protected(ctx, args, env)
    asking = not args.flags.get("--yes")
    if asking and ctx.ask is None:
        raise HarnessError(
            "a push asks before it writes, and standard input is no terminal. Run "
            f"{push_command(args, env)} --yes to push without the question."
        )
    return asking


def _agreed(ctx: Context, question: str) -> bool:
    return ctx.ask is not None and ctx.ask(question + " [y/N] ").strip().lower() in ("y", "yes")


def cmd_pull(ctx: Context, argv: list[str]) -> int:
    args = parse_args(argv, values=("--store", "--apps"))
    (env,) = expect(args, 1, 1, "<env>")
    store, path = _store(ctx, env, store_name(args))
    ctx.writable(path)
    keys = scope.of(ctx, args, store, secrets=False)
    fetched = store.fetch()
    if fetched is None:
        raise HarnessError(f"the {store.kind} store holds nothing for {env}; nothing pulled")
    remote = {k: v for k, v in fetched.items() if keys.holds(k)}
    file = envfile.read_or_empty(path)
    values = file.values()
    shape = ctx.shape_for(path)
    changed = [k for k, v in remote.items() if values.get(k) != v]
    for key in changed:
        layout.put(file, key, envfile.lines_for(key, remote[key]), shape)
        values[key] = remote[key]
        ctx.out(f"pulled {key}")
    if changed or not path.is_file():
        envfile.save(path, file, values)
    only_local = [k for k in values if k not in remote]
    if only_local:
        ctx.out(f"kept, not in the store: {' '.join(only_local)}")
    ctx.out(f"pulled {len(changed)} keys from the {store.kind} store into {ctx.show(path)}")
    marker = write_marker(path, store)
    ctx.out(f"wrote {ctx.show(marker)}")
    return 0
