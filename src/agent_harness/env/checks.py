"""`ah env check`: the env facts of a repository that a gate can enforce.

A finding names a file, a key and a line, never a value:

- a key that code reads (see `readers`) and that no master or declaration names;
- a key of a live env file that no master or declaration names;
- a declared `required` key that a live file of its environments lacks or leaves empty;
- an env file outside the master folder and the folders of `[env] dirs`;
- a live env file that git tracks.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_harness.env import envfile, layout
from agent_harness.env.args import expect, parse_args
from agent_harness.env.context import Context


def environment_of(name: str) -> str:
    """The environment of a live file name: `production` for `.env.production`, else ``."""
    return name.removeprefix(".env.") if name.startswith(".env.") else ""


def known_keys(ctx: Context) -> tuple[set[str], bool]:
    """The keys that a master or a template names, and whether declarations are configured."""
    names: set[str] = set()
    for path in [ctx.master, *ctx.templates(), *ctx.extra_masters()]:
        if path.is_file():
            names.update(envfile.read(path).key_names())
    return names, bool(ctx.settings.get("declarations"))


def is_declared(ctx: Context, key: str, names: set[str], declared: bool) -> bool:
    return key in names or (declared and ctx.declarations().find(key) is not None)


def read_findings(ctx: Context, names: set[str], declared: bool) -> list[str]:
    ignore = {str(k) for k in ctx.settings.get("ignore") or []}
    return [
        f"{ctx.show(read.path)}:{read.line} {read.key}: code reads it, and no master or "
        "declaration names it"
        for read in ctx.reads()
        if read.key not in ignore and not is_declared(ctx, read.key, names, declared)
    ]


def file_findings(ctx: Context, names: set[str], declared: bool) -> list[str]:
    found: list[str] = []
    for path in ctx.live_files():
        if not path.is_file():
            continue
        values = envfile.read(path).values()
        shown = ctx.show(path)
        for name in values:
            if not is_declared(ctx, name, names, declared):
                found.append(f"{shown}: {name} is in no master and no declaration")
        if not declared:
            continue
        environment = environment_of(path.name)
        for key in ctx.declarations().keys:
            if not key.required or not key.applies_to(environment):
                continue
            if key.name not in values:
                found.append(f"{shown}: required key {key.name} is missing")
            elif values[key.name] == "":
                found.append(f"{shown}: required key {key.name} is empty")
    return found


def stray_findings(ctx: Context) -> list[str]:
    """Each env file under the root outside the master folder and the `dirs` folders."""
    homes = {ctx.master.parent.resolve(), *(p.parent.resolve() for p in ctx.extra_masters())}
    pruned = {".git", *(str(s) for s in ctx.settings.get("skip") or [])}
    found: list[str] = []
    for folder, dirs, files in os.walk(ctx.root):
        dirs[:] = sorted(d for d in dirs if d not in pruned)
        here = Path(folder)
        if here.resolve() in homes:
            continue
        for name in sorted(files):
            if layout.is_env_name(name):
                found.append(f"{ctx.show(here / name)}: an env file outside the master folder")
    return found


def tracked_findings(ctx: Context) -> list[str]:
    done = ctx.runner(["git", "-C", str(ctx.root), "ls-files", "-z"], None)
    if done.code != 0:
        first = done.stderr.strip().splitlines()[:1]
        return [f"git ls-files failed (exit {done.code}): {' '.join(first)}"]
    found: list[str] = []
    for name in filter(None, done.stdout.split("\0")):
        base = name.rsplit("/", 1)[-1]
        if layout.is_env_name(base) and not layout.is_template(base):
            found.append(
                f"{name}: a live env file that git tracks. Run git rm --cached {name} and add "
                "it to .gitignore."
            )
    return found


def cmd_check(ctx: Context, argv: list[str]) -> int:
    expect(parse_args(argv), 0, 0, "no argument: check reads the whole repository")
    names, declared = known_keys(ctx)
    findings = [
        *read_findings(ctx, names, declared),
        *file_findings(ctx, names, declared),
        *stray_findings(ctx),
        *tracked_findings(ctx),
    ]
    for line in findings:
        ctx.out(line)
    ctx.out(f"{len(findings)} findings" if findings else "env check: no finding")
    return 1 if findings else 0
