"""Project and global install, the lockfile and its check, and the Claude and Codex registries.

`ah install [--project] [--manifest <path> ...] [--from <package root>] [--replace]`
`ah install --global [--from <package root>]`
`ah check [--global]`
`ah rules`: each installed rule and each project rule of `.claude/rules/`, with subtotals and the
always-loaded size
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import agent_harness

# The package root: the folder above src/. It holds the manifest, the skills and the engine, in
# the source tree and in an installed copy alike.
PACKAGE_ROOT = Path(agent_harness.__file__).resolve().parents[2]


def _require_sources(source: Path) -> None:
    """Refuse an installed copy that lacks a file its manifest names, such as a global copy."""
    from agent_harness import HarnessError
    from agent_harness.install.plan import MANIFEST_FILE, PACKAGE_NAME
    from agent_harness.manifest import read_manifest

    if not (source / "lock.json").is_file():
        return
    try:
        read_manifest(source / MANIFEST_FILE, name=PACKAGE_NAME, extra=False)
    except HarnessError as error:
        raise HarnessError(
            f"{source} is an installed copy that lacks a source file of its manifest ({error})."
            " Run ah install from the agent-harness package:"
            " python3 <package root>/bin/ah install, or pass --from <package root>"
        ) from error


def _say(line: str) -> None:
    print(line)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ah install",
        description="Install the harness into the current git repository, or globally.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--project", action="store_true", help="install into this repository")
    mode.add_argument(
        "--global",
        dest="global_",
        action="store_true",
        help="install the engine and the skills into ~/.claude",
    )
    parser.add_argument(
        "--manifest",
        action="append",
        default=[],
        type=Path,
        help="an extra harness.toml to install with the package and harness.manifests (repeatable)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="replace a project copy of a harness file and remove the extra files of the copy",
    )
    parser.add_argument(
        "--from",
        dest="source",
        type=Path,
        default=None,
        help="the package root to install from (default: this engine's own package)",
    )
    return parser


def main(argv: list[str]) -> int:
    from agent_harness import config
    from agent_harness.install.home import claude_home, install_global
    from agent_harness.install.project import install_project, manifest_files

    args = _parser().parse_args(argv)
    source = (args.source or PACKAGE_ROOT).resolve()
    _require_sources(source)
    if args.global_:
        if args.manifest:
            print("ah install: --global installs no extra manifest", file=sys.stderr)
            return 2
        if args.replace:
            print("ah install: --replace is for a project install", file=sys.stderr)
            return 2
        install_global(claude_home(), source, _say)
        return 0
    root = config.find_root()
    cfg = config.load(root)
    manifests = manifest_files(root, cfg, args.manifest)
    install_project(root, cfg, source, manifests, _say, replace=args.replace)
    return 0


def check(argv: list[str]) -> int:
    from agent_harness import config
    from agent_harness.install.home import check_global, claude_home
    from agent_harness.install.project import (
        always_loaded_line,
        check_project,
        duplicate_lines,
        machine_paths,
        over_cap,
        project_hooks,
        rule_cap,
    )

    parser = argparse.ArgumentParser(
        prog="ah check",
        description="Exit 1 and name each installed file or registry that differs.",
    )
    parser.add_argument("--global", dest="global_", action="store_true")
    args = parser.parse_args(argv)
    over = None
    if args.global_:
        diffs, again, prefix = check_global(claude_home()), "ah install --global", "~/.claude/"
    else:
        root = config.find_root()
        cfg = config.load(root)
        cap = rule_cap(cfg)
        diffs, again, prefix = check_project(root, cfg), "ah install --project", ""
        # Information only: a project hook, a likely duplicate or a machine path changes no
        # exit code.
        for line in [*project_hooks(root), *duplicate_lines(root, cfg), *machine_paths(cfg)]:
            print(line)
        print(always_loaded_line(root, cap))
        over = over_cap(root, cap)
    for path, kind in diffs:
        print(f"{prefix}{path} {kind}")
    if diffs:
        print(
            f"ah check: {len(diffs)} installed parts differ from the lockfile and the manifests;"
            f" change facts in .agents/harness.toml, then run {again}",
            file=sys.stderr,
        )
    if over:
        print(f"ah check: {over}", file=sys.stderr)
    if diffs or over:
        return 1
    print("ah check: the installed files match the lockfile")
    return 0


def rules(argv: list[str]) -> int:
    from agent_harness import config
    from agent_harness.install.project import (
        always_loaded_line,
        project_rule_sizes,
        rule_cap,
        rule_sizes,
    )

    argparse.ArgumentParser(
        prog="ah rules",
        description=(
            "Print each installed rule and each project rule of .claude/rules/ with its line"
            " count and character count, then the size of the rules that load in every session."
        ),
    ).parse_args(argv)
    root = config.find_root()
    sizes, own = rule_sizes(root), project_rule_sizes(root)
    for label, group in (("harness", sizes), ("project", own)):
        for rel, lines, chars in group:
            print(f"{rel} {lines} lines {chars} chars")
        # One group needs no subtotal: the total says it.
        if own:
            print(
                f"{label} rules: {len(group)} files {sum(s[1] for s in group)} lines"
                f" {sum(s[2] for s in group)} chars"
            )
    both = [*sizes, *own]
    print(f"total {sum(s[1] for s in both)} lines {sum(s[2] for s in both)} chars")
    print(always_loaded_line(root, rule_cap(config.load(root))))
    return 0
