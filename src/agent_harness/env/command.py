"""`ah env <subcommand>`: every env file operation, and the secret store.

No subcommand prints a value, except `get --reveal`. A masked value prints in the one form of
`masking.mask`. An error names a file, a key or a line number.
"""

from __future__ import annotations

import enum
import re
import sys
from collections.abc import Callable
from pathlib import Path

from agent_harness import HarnessError, config
from agent_harness.env import (
    checks,
    declarations,
    envfile,
    generate,
    layout,
    masking,
    run,
    stores,
    sync,
)
from agent_harness.env.args import (
    Args,
    UsageError,
    check_key,
    expect,
    parse_args,
)
from agent_harness.env.context import Context
from agent_harness.env.envfile import EnvFile

USAGE = """usage: ah env [--root DIR] [--master FILE] [--declarations FILE ...] <subcommand> [args]

  keys <file|app>                     the keys of a file, or the keys an app reads
  status [<file> ...] [--matrix]      per file: key count, empty keys, keys missing against its
                                      master (its own <file>.example when that is the master,
                                      else the root master), keys absent from it; --matrix
                                      prints one row per key with set, empty or - per file
                                      and its apps
  check                               keys code reads and nothing declares, undeclared keys,
                                      empty required keys, stray and git-tracked env files
  where <KEY>                         each file that sets KEY, each declaration that reads it,
                                      each workflow or infra file that names it, and each code
                                      line that reads it
  get <KEY> <file> [--reveal]         the masked value; --reveal prints the value itself
  set <KEY> <file> --value <v>        set or add KEY in its master block and order
  set <KEY> <file> --stdin            the same, with the value on standard input
  unset <KEY> <file>                  remove KEY
  copy <KEY> <from> <to> [--as NEW]   copy one key between files
  move <KEY> <from> <to>              copy one key, then remove it from <from>
  rename <OLD> <NEW> [<file> ...]     rename a key in each file and in the master
  diff <a> <b>                        keys only in a, only in b, and keys whose values differ
  merge <from> <to> [--secrets-only]  add the keys of <from> that <to> lacks; --secrets-only
                                      adds only the keys that the declarations mark secret
  slice <app> <file> [--to <dest>] [--environment <env>]
                                      the keys that <app> reads; --to writes them to <dest>;
                                      --environment fills a key the file lacks from the
                                      declared values of <env>
  layout [DIR ...] [--fix [--dry-run]]
                                      check rules L1 to L7 in the master folder and [env] dirs,
                                      or in each DIR against DIR/.env.example; --fix reorders
                                      each live file to its master; --dry-run only says so
  template [<file> ...]               write <file>.example from the master: its blocks and
                                      keys, values empty
  template --master [--check]         write the master and .env.<env>.example of each declared
                                      environment from the declarations; --check fails on a
                                      stale one and writes nothing
  init <file>                         create <file> from the master, values empty
  push <env> [--store NAME] [--apps a,b] [--all] [--dry-run] [--drop KEY ...] [--yes]
       [--confirm <env>] [--create-environment]
                                      send the secret keys of .env.<env> to the secret store;
                                      it asks first, or needs --yes with no terminal
  pull <env> [--store NAME] [--apps a,b]
                                      write the values of the secret store into .env.<env>
  diff <env> --store [NAME]           keys that differ between .env.<env> and the store
  run <file> [--require KEY ...] -- <command> [args]
                                      start one command with the keys of <file>

A relative file path resolves against the repository root. From a worktree, a live env file
that only the main checkout holds resolves there. A file that ends in .example is a template:
set, copy, move, merge, unset and init refuse to write a value into one. Every subcommand that
writes refuses a file that is not named .env or .env.<name>, or that resolves outside the
repository."""


# Subcommands. Each takes the context and its arguments and returns the exit code.


def cmd_keys(ctx: Context, argv: list[str]) -> int:
    (target,) = expect(parse_args(argv), 1, 1, "<file|app>")
    path = ctx.at(target)
    if path.is_file():
        for name in envfile.read(path).key_names():
            ctx.out(name)
        return 0
    decl = ctx.declarations()
    if target in decl.apps():
        for key in decl.keys_of(target):
            ctx.out(f"{key.name} {'secret' if key.secret else 'plain'} {key.file}")
        return 0
    known = ", ".join(decl.apps()) or "none, no declarations are configured"
    raise HarnessError(f"{target} is neither an env file nor an app. Apps: {known}")


def cmd_status(ctx: Context, argv: list[str]) -> int:
    args = parse_args(argv, switches=("--matrix",))
    names = args.positionals
    files = [ctx.at(n) for n in names] if names else ctx.live_files()
    if args.flags.get("--matrix"):
        return status_matrix(ctx, [path for path in files if path.is_file()])
    master = ctx.master
    master_keys = envfile.read(master).key_names() if master.is_file() else None
    if master_keys is not None:
        ctx.out(f"{ctx.show(master)}: {len(master_keys)} keys (the master)")
    else:
        ctx.out(f"{ctx.show(master)}: no master")
    for path in files:
        if not path.is_file():
            ctx.out(f"{ctx.show(path)}: does not exist")
            continue
        values = envfile.read(path).values()
        empty = [k for k, v in values.items() if v == ""]
        parts = [f"{len(values)} keys", f"{len(empty)} empty"]
        missing: list[str] = []
        extra: list[str] = []
        # The master of a file is its own pair template when it has one, as in check and layout.
        own = ctx.own_template(path)
        expected = envfile.read(own).key_names() if own is not None else master_keys
        against = ctx.show(own) if own is not None else "the master"
        if expected is not None:
            missing = [k for k in expected if k not in values]
            extra = [k for k in values if k not in expected]
            parts += [
                f"{len(missing)} missing against {against}",
                f"{len(extra)} not in {against}",
            ]
        ctx.out(f"{ctx.show(path)}: {', '.join(parts)}")
        for label, keys in (("empty", empty), ("missing", missing), (f"not in {against}", extra)):
            if keys:
                ctx.out(f"  {label}: {' '.join(keys)}")
    return 0


def status_matrix(ctx: Context, files: list[Path]) -> int:
    """One row per key and one column per file: `set`, `empty` or `-`. The APP column holds the
    apps of the declaration, `all` for a key without `apps`, or `-` for an undeclared key."""
    values = {path: envfile.read(path).values() for path in files}
    keys = envfile.read(ctx.master).key_names() if ctx.master.is_file() else []
    for held in values.values():
        keys += [key for key in held if key not in keys]
    declared = ctx.declarations() if ctx.settings.get("declarations") else None
    rows = [["KEY", "APP", *(ctx.show(path) for path in files)]]
    for key in keys:
        found = declared.find(key) if declared is not None else None
        apps = "-" if found is None else ",".join(found.apps) if found.apps is not None else "all"
        cells = [
            "-" if key not in v else "empty" if v[key] == "" else "set" for v in values.values()
        ]
        rows.append([key, apps, *cells])
    widths = [max(len(row[at]) for row in rows) for at in range(len(rows[0]))]
    for row in rows:
        ctx.out(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        )
    return 0


def _scan_paths(ctx: Context) -> list[Path]:
    found: list[Path] = []
    for name in ctx.settings.get("scan") or []:
        path = ctx.root / str(name)
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found += sorted(p for p in path.rglob("*") if p.is_file())
    return [p for p in found if not layout.is_env_name(p.name) and not p.name.startswith(".env")]


def cmd_where(ctx: Context, argv: list[str]) -> int:
    (key,) = expect(parse_args(argv), 1, 1, "<KEY>")
    check_key(key)
    ctx.out(key)
    files = [ctx.master, *[t for t in ctx.templates() if t != ctx.master], *ctx.live_files()]
    for path in files:
        if not path.is_file():
            continue
        values = envfile.read(path).values()
        if key in values:
            state = "empty" if values[key] == "" else "set"
            ctx.out(f"  {state} in {ctx.show(path)}")
        else:
            ctx.out(f"  absent from {ctx.show(path)}")
    decl = ctx.declarations()
    declared = decl.find(key)
    if declared is not None:
        apps = ",".join(declared.apps) if declared.apps is not None else "every app"
        ctx.out(f"  declared in {declared.file}, read by {apps}")
    elif decl.files:
        ctx.out("  declared in no declaration file")
    pattern = re.compile(rf"\b{re.escape(key)}\b")
    for path in _scan_paths(ctx):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, start=1):
            # The line text can hold a value, so only its place prints.
            if pattern.search(line):
                ctx.out(f"  named in {ctx.show(path)}:{number}")
    for read in ctx.reads():
        if read.key == key:
            ctx.out(f"  read in {ctx.show(read.path)}:{read.line}")
    return 0


def cmd_get(ctx: Context, argv: list[str]) -> int:
    args = parse_args(argv, switches=("--reveal",))
    key, name = expect(args, 2, 2, "<KEY> <file>")
    path = ctx.at(name)
    values = envfile.read(path).values()
    if key not in values:
        raise HarnessError(f"{key} is not in {ctx.show(path)}")
    # The guards let a reveal of a public key pass with no question, so a credential shape in
    # its value prints masked.
    shape = masking.credential_shape(values[key])
    public = masking.public_key(key, ctx.settings.get("publicKeys", []))
    if args.flags.get("--reveal") and public and shape is not None:
        ctx.out(
            f"{key} in {ctx.show(path)}: {masking.mask(values[key])} (a public key, and its value "
            f"looks like a credential: {shape}, so it prints masked)"
        )
    elif args.flags.get("--reveal"):
        ctx.out(values[key])
    else:
        declared = ctx.declarations().find(key)
        plain = declared is not None and declared.plain
        ctx.out(f"{key} in {ctx.show(path)}: {masking.mask(values[key], plain)}")
    return 0


def set_value(
    ctx: Context, path: Path, key: str, value: str, lines: list[str] | None = None
) -> str:
    """Set one key in a live file and write it. Returns `added`, `replaced` or `unchanged`."""
    ctx.writable(path)
    declared = ctx.declarations().find(key) if ctx.settings.get("declarations") else None
    if declared is not None and not declarations.matches_type(declared, value):
        raise HarnessError(f"the value of {key} does not match type '{declared.type}'")
    env = envfile.read_or_empty(path)
    values = env.values()
    if key in values and values[key] == value and path.is_file():
        return "unchanged"
    new_lines = lines if lines is not None else envfile.lines_for(key, value)
    if envfile.value_of(new_lines) != value:
        new_lines = envfile.lines_for(key, value)
    state = layout.put(env, key, new_lines, ctx.shape_for(path))
    envfile.save(path, env, {**values, key: value})
    return state


def cmd_set(ctx: Context, argv: list[str], stdin: Callable[[], str]) -> int:
    args = parse_args(argv, values=("--value",), switches=("--stdin",))
    key, name = expect(args, 2, 2, "<KEY> <file> --value <v> | --stdin")
    check_key(key)
    if args.flags.get("--stdin"):
        value = stdin().rstrip("\r\n")
    elif isinstance(args.flags.get("--value"), str):
        value = str(args.flags["--value"])
    else:
        raise UsageError("set needs --value <v> or --stdin")
    path = ctx.at(name)
    if ctx.settings.get("declarations"):
        _check_declared(ctx, path, key, value)
    state = set_value(ctx, path, key, value)
    ctx.out(f"set {key} in {ctx.show(path)} ({state})")
    return 0


def _check_declared(ctx: Context, path: Path, key: str, value: str) -> None:
    """With declarations configured, `set` writes only a declared key, as one line, into a
    file that holds only declared keys."""
    decl = ctx.declarations()
    declared = decl.find(key)
    if declared is None:
        raise HarnessError(f"{key} is not declared in any declaration file")
    if "\n" in value or "\r" in value:
        raise HarnessError(f"the value of {key} must be one line")
    if not declarations.matches_type(declared, value):
        raise HarnessError(f"the value of {key} does not match type '{declared.type}'")
    held = envfile.read(path).key_names() if path.is_file() else []
    extra = [k for k in held if decl.find(k) is None]
    if extra:
        raise HarnessError(
            f"{ctx.show(path)} holds keys that no declaration file declares: {', '.join(extra)}. "
            "Declare or remove them first."
        )


def cmd_unset(ctx: Context, argv: list[str]) -> int:
    key, name = expect(parse_args(argv), 2, 2, "<KEY> <file>")
    path = ctx.writable(ctx.at(name))
    env = envfile.read(path)
    values = env.values()
    if key not in values:
        raise HarnessError(f"{key} is not in {ctx.show(path)}, so nothing was removed")
    envfile.remove_entries(env, key)
    del values[key]
    envfile.save(path, env, values)
    ctx.out(f"removed {key} from {ctx.show(path)} ({len(values)} keys left)")
    return 0


def _copy(ctx: Context, key: str, source: Path, dest: Path, as_key: str) -> str:
    found = envfile.read(source).find(key)
    if found is None:
        raise HarnessError(f"{key} is not in {ctx.show(source)}")
    entry = found[1]
    lines = entry.lines if as_key == key else None
    return set_value(ctx, dest, as_key, entry.value, lines)


def cmd_copy(ctx: Context, argv: list[str]) -> int:
    args = parse_args(argv, values=("--as",))
    key, source, dest = expect(args, 3, 3, "<KEY> <from> <to>")
    as_key = check_key(str(args.flags.get("--as", key)))
    src, dst = ctx.at(source), ctx.at(dest)
    state = _copy(ctx, key, src, dst, as_key)
    shown = key if as_key == key else f"{key} as {as_key}"
    ctx.out(f"copied {shown} from {ctx.show(src)} to {ctx.show(dst)} ({state})")
    return 0


def cmd_move(ctx: Context, argv: list[str]) -> int:
    key, source, dest = expect(parse_args(argv), 3, 3, "<KEY> <from> <to>")
    src, dst = ctx.writable(ctx.at(source)), ctx.at(dest)
    state = _copy(ctx, key, src, dst, key)
    env = envfile.read(src)
    values = env.values()
    envfile.remove_entries(env, key)
    del values[key]
    envfile.save(src, env, values)
    ctx.out(f"moved {key} from {ctx.show(src)} to {ctx.show(dst)} ({state})")
    return 0


def _rename_in(env: EnvFile, old: str, new: str) -> None:
    for entry in env.entries():
        if entry.key == old:
            first = entry.lines[0]
            at = first.index(old)
            entry.lines[0] = first[:at] + new + first[at + len(old) :]
            entry.key = new


def cmd_rename(ctx: Context, argv: list[str]) -> int:
    args = parse_args(argv)
    if len(args.positionals) < 2:
        raise UsageError("expected <OLD> <NEW> [<file> ...]")
    old, new, *names = args.positionals
    check_key(new)
    if names:
        paths = [ctx.at(n) for n in names]
        if ctx.master.is_file() and ctx.master not in paths:
            paths.append(ctx.master)
    else:
        paths = [ctx.master, *[t for t in ctx.templates() if t != ctx.master], *ctx.live_files()]
    plans: list[tuple[Path, EnvFile, dict[str, str]]] = []
    for path in dict.fromkeys(paths):
        if not path.is_file():
            continue
        ctx.destination(path)
        env = envfile.read(path)
        values = env.values()
        if new in values and old in values:
            raise HarnessError(f"{new} is already in {ctx.show(path)}; nothing renamed")
        if old in values:
            values[new] = values.pop(old)
            plans.append((path, env, values))
    if not plans:
        raise HarnessError(f"{old} is in none of the files; nothing renamed")
    for path, env, values in plans:
        _rename_in(env, old, new)
        mode = 0o644 if layout.is_template(path.name) else 0o600
        envfile.save(path, env, values, mode)
        ctx.out(f"renamed {old} to {new} in {ctx.show(path)}")
    declared = ctx.declarations().find(old) if ctx.settings.get("declarations") else None
    if declared is not None:
        ctx.err(f"{old} is declared in {declared.file}: rename it there too")
    return 0


def cmd_diff(ctx: Context, argv: list[str]) -> int:
    if "--store" in argv:
        # `--store` alone compares with the store, `--store NAME` with the store of that name.
        at = argv.index("--store")
        rest = argv[at + 1 :]
        named = rest[:1] if rest and not rest[0].startswith("-") else []
        args = parse_args(argv[:at] + rest[len(named) :])
        (env,) = expect(args, 1, 1, "<env> --store [NAME]")
        return sync.store_diff(ctx, env, named[0] if named else None)
    args = parse_args(argv)
    a, b = expect(args, 2, 2, "<a> <b>, or <env> --store")
    pa, pb = ctx.at(a), ctx.at(b)
    left, right = envfile.read(pa).values(), envfile.read(pb).values()
    only_left, only_right, differ = sync.diff_keys(left, right)
    for key in only_left:
        ctx.out(f"only in {ctx.show(pa)}: {key}")
    for key in only_right:
        ctx.out(f"only in {ctx.show(pb)}: {key}")
    for key in differ:
        ctx.out(f"differs: {key} (a {masking.digest(left[key])}, b {masking.digest(right[key])})")
    same = len([k for k in left if k in right and left[k] == right[k]])
    ctx.out(f"{same} keys match")
    return 1 if only_left or only_right or differ else 0


def cmd_merge(ctx: Context, argv: list[str]) -> int:
    args = parse_args(argv, switches=("--secrets-only",))
    source, dest = expect(args, 2, 2, "<from> <to>")
    secrets_only = bool(args.flags.get("--secrets-only"))
    if secrets_only and not ctx.settings.get("declarations"):
        raise HarnessError(
            "--secrets-only reads the secret field of the declarations, and none are configured"
        )
    src, dst = ctx.at(source), ctx.writable(ctx.at(dest))
    from_env = envfile.read(src)
    env = envfile.read_or_empty(dst)
    values = env.values()
    shape = ctx.shape_for(dst)
    added: list[str] = []
    kept: list[str] = []
    last = {entry.key: entry for entry in from_env.entries()}
    if secrets_only:
        decl = ctx.declarations()
        last = {k: e for k, e in last.items() if (d := decl.find(k)) is not None and d.secret}
    for key, entry in last.items():
        if key in values:
            kept.append(key)
            continue
        layout.put(env, key, entry.lines, shape)
        values[key] = entry.value
        added.append(key)
    if added:
        envfile.save(dst, env, values)
    ctx.out(f"merged {len(added)} keys into {ctx.show(dst)}; {len(kept)} already there, kept")
    if added:
        ctx.out(f"  added: {' '.join(added)}")
    return 0


def cmd_slice(ctx: Context, argv: list[str]) -> int:
    args = parse_args(argv, values=("--to", "--environment"))
    app, name = expect(args, 2, 2, "<app> <file>")
    decl = ctx.declarations()
    # A key without `apps` goes to every app, so any app name reads those keys.
    if app not in decl.apps() and not any(key.apps is None for key in decl.keys):
        known = ", ".join(decl.apps()) or "none, no declarations are configured"
        raise HarnessError(f"{app} is no app of the declarations. Apps: {known}")
    path = ctx.at(name)
    environment = args.flags.get("--environment")
    defaults: dict[str, str] = {}
    if isinstance(environment, str):
        defaults = decl.environments.get(environment, {})
        source = envfile.read_or_empty(path)
    else:
        source = envfile.read(path)
    entries = {entry.key: entry for entry in source.entries()}
    # Declaration order. The value of the file wins over the declared value of the environment.
    lines: dict[str, list[str]] = {}
    for key in decl.keys_of(app):
        if key.name in entries:
            lines[key.name] = entries[key.name].lines
        elif key.name in defaults:
            lines[key.name] = envfile.lines_for(key.name, defaults[key.name])
    # Then the members of a family that the key list lacks, such as API_KEY_GAMMA, sorted.
    listed = {key.name for key in decl.keys}
    for key_name in sorted((set(entries) | set(defaults)) - listed):
        member = decl.find(key_name)
        if member is None or member.family is None or not member.read_by(app):
            continue
        if key_name in entries:
            lines[key_name] = entries[key_name].lines
        else:
            lines[key_name] = envfile.lines_for(key_name, defaults[key_name])
    dest_name = args.flags.get("--to")
    if not isinstance(dest_name, str):
        for key_name in lines:
            ctx.out(key_name)
        return 0
    dest = ctx.writable(ctx.at(dest_name))
    env = EnvFile()
    shape = ctx.shape_for(dest)
    for key_name, entry_lines in lines.items():
        layout.put(env, key_name, entry_lines, shape)
    values = {key_name: envfile.value_of(entry_lines) for key_name, entry_lines in lines.items()}
    envfile.save(dest, env, values)
    ctx.out(f"wrote {len(values)} keys of app {app} to {ctx.show(dest)}")
    return 0


def cmd_layout(ctx: Context, argv: list[str]) -> int:
    """Check L1 to L7 in the master folder and the `[env] dirs` folders, or in each DIR.

    Each folder has its own master, `<DIR>/.env.example`.
    """
    args = parse_args(argv, switches=("--fix", "--dry-run"))
    if args.flags.get("--dry-run") and not args.flags.get("--fix"):
        raise UsageError("--dry-run shows what --fix would write; pass --fix too")
    if args.positionals:
        folders = [ctx.for_folder(name) for name in args.positionals]
    else:
        folders = [ctx, *(ctx.for_folder(str(name)) for name in ctx.settings.get("dirs") or [])]
    findings: list[layout.Finding] = []
    count = 0
    for each in folders:
        if args.flags.get("--fix"):
            _reorder(each, write=not args.flags.get("--dry-run"))
        texts, found = _layout_findings(each)
        count += len(texts)
        findings += found
    for finding in findings:
        ctx.out(str(finding))
    if not findings:
        ctx.out(f"{count} env files follow the layout")
    return 1 if findings else 0


def _reorder(ctx: Context, write: bool) -> None:
    """Reorder each live file of `ctx` to its master, or only say so when `write` is False."""
    for path in ctx.live_files():
        if not path.is_file():
            continue
        shape = ctx.shape_for(path)
        if shape is None:
            continue
        env = envfile.read(path)
        fixed, extra = layout.reorder(env, shape)
        if fixed.render() != path.read_text(encoding="utf-8"):
            if write:
                envfile.save(path, fixed, env.values())
            ctx.out(f"{'reordered' if write else 'would reorder'} {ctx.show(path)} to its master")
        if extra:
            ctx.err(f"{ctx.show(path)}: not in the master, kept under Other: {' '.join(extra)}")


def _layout_findings(ctx: Context) -> tuple[dict[str, str], list[layout.Finding]]:
    # Keyed by name: the templates of the root and the live files, which from a worktree can sit
    # in the main checkout.
    texts: dict[str, str] = {}
    shown: dict[str, str] = {}
    for path in [*ctx.templates(), *ctx.live_files()]:
        if path.is_file():
            texts[path.name] = path.read_text(encoding="utf-8")
            shown[path.name] = ctx.show(path)
    return texts, layout.check_texts(texts, shown)


def _shown_master(ctx: Context, source: Path | None) -> str:
    return ctx.show(source) if source is not None else "the declarations"


def cmd_template(ctx: Context, argv: list[str]) -> int:
    """Write `<file>.example` from the master of each live file.

    A file whose master is its own pair template keeps that template, because the template is
    the master.
    """
    args = parse_args(argv, switches=("--master", "--check"))
    if args.flags.get("--master"):
        return generate.cmd_template_master(ctx, args)
    if args.flags.get("--check"):
        raise UsageError("--check checks the templates of template --master")
    names = args.positionals
    paths = [ctx.at(n) for n in names] if names else ctx.live_files()
    written = 0
    for path in paths:
        if layout.is_template(path.name):
            raise HarnessError(f"{ctx.show(path)} is a template already; name the live file")
        own = ctx.own_template(path)
        if own is not None:
            ctx.out(f"kept {ctx.show(own)}: it is the master of {path.name}")
            continue
        source, keys = ctx.master_keys(path)
        # A template is the root's own, also for a live file of the main checkout.
        base = ctx.main_checkout
        in_root = ctx.root / path.relative_to(base) if base and path.is_relative_to(base) else path
        example = ctx.destination(in_root.with_name(path.name + ".example"))
        if example == ctx.master:
            ctx.out(f"kept {ctx.show(example)}: it is the master")
            continue
        env = layout.template_of(keys, ctx.shape_for(path))
        envfile.save(example, env, dict.fromkeys(keys, ""), mode=0o644)
        shown = _shown_master(ctx, source)
        ctx.out(f"wrote {ctx.show(example)} from {shown}: {len(keys)} keys, values empty")
        written += 1
    if written == 0 and not names:
        ctx.out("no live env file to write a template for")
    return 0


def cmd_init(ctx: Context, argv: list[str]) -> int:
    (name,) = expect(parse_args(argv), 1, 1, "<file>")
    path = ctx.writable(ctx.at(name))
    if path.exists():
        raise HarnessError(
            f"{ctx.show(path)} exists, and init creates a new file. "
            "Run merge <from> <to> to add the keys of another file."
        )
    source, keys = ctx.master_keys(path)
    env = layout.template_of(keys, ctx.shape_for(path))
    envfile.save(path, env, dict.fromkeys(keys, ""))
    ctx.out(f"created {ctx.show(path)} from {_shown_master(ctx, source)}: {len(keys)} keys, empty")
    return 0


SUBCOMMANDS: dict[str, Callable[[Context, list[str]], int]] = {
    "keys": cmd_keys,
    "status": cmd_status,
    "check": checks.cmd_check,
    "where": cmd_where,
    "get": cmd_get,
    "unset": cmd_unset,
    "copy": cmd_copy,
    "move": cmd_move,
    "rename": cmd_rename,
    "diff": cmd_diff,
    "merge": cmd_merge,
    "slice": cmd_slice,
    "layout": cmd_layout,
    "template": cmd_template,
    "init": cmd_init,
    "push": sync.cmd_push,
    "pull": sync.cmd_pull,
    "run": run.cmd_run,
}


class Terminal(enum.Enum):
    DETECT = "detect"


def _read_stdin() -> str:
    return sys.stdin.read()


def main(
    argv: list[str],
    root: Path | None = None,
    out: Callable[[str], None] | None = None,
    err: Callable[[str], None] | None = None,
    stdin: Callable[[], str] = _read_stdin,
    runner: stores.Runner = stores.run_process,
    ask: Callable[[str], str] | Terminal | None = Terminal.DETECT,
) -> int:
    """`ask` defaults to `input` when standard input is a terminal, else to None."""
    asker = (input if sys.stdin.isatty() else None) if ask is Terminal.DETECT else ask
    write_out = out or print
    write_err = err or (lambda line: print(line, file=sys.stderr))
    try:
        globals_, rest = _split_globals(argv)
        if not rest or rest[0] in ("-h", "--help", "help"):
            write_out(USAGE) if rest else write_err(USAGE)
            return 0 if rest else 2
        sub, sub_argv = rest[0], rest[1:]
        if sub != "set" and sub not in SUBCOMMANDS:
            raise UsageError(f"unknown subcommand {sub}")
        top = Path(str(globals_.flags["--root"])).resolve() if "--root" in globals_.flags else root
        loaded = config.load(top)
        settings = dict(loaded.get("env"))
        if "--master" in globals_.flags:
            settings["master"] = globals_.flags["--master"]
        if "--declarations" in globals_.lists:
            settings["declarations"] = globals_.lists["--declarations"]
        ctx = Context(loaded.root, settings, write_out, write_err, runner, asker)
        if sub == "set":
            return cmd_set(ctx, sub_argv, stdin)
        return SUBCOMMANDS[sub](ctx, sub_argv)
    except UsageError as error:
        write_err(f"ah env: {error}")
        write_err(USAGE)
        return 2
    except HarnessError as error:
        write_err(f"ah env: {error}")
        return 1


def _split_globals(argv: list[str]) -> tuple[Args, list[str]]:
    """The global options before the subcommand, and the rest."""
    index = 0
    head: list[str] = []
    while index < len(argv) and argv[index] in ("--root", "--master", "--declarations"):
        head += argv[index : index + 2]
        index += 2
    parsed = parse_args(head, values=("--root", "--master"), repeats=("--declarations",))
    return parsed, argv[index:]
