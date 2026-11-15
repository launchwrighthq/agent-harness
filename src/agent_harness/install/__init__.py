"""Project and global install, the lockfile and its check, and the Claude and Codex registries.

`ah install [--project] [--manifest <path> ...] [--from <package root>] [--replace]`
`ah install --global [--from <package root>]`
`ah check [--global]`
`ah rules`
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import agent_harness

# The package root: the folder above src/. It holds the manifest, the skills and the engine, in
# the source tree and in an installed copy alike.
PACKAGE_ROOT = Path(agent_harness.__file__).resolve().parents[2]


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
        help="an extra harness.toml to install with the package and harness.manifests"
        " (repeatable)",
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
    from agent_harness.install.project import check_project, project_hooks

    parser = argparse.ArgumentParser(
        prog="ah check",
        description="Exit 1 and name each installed file or registry that differs.",
    )
    parser.add_argument("--global", dest="global_", action="store_true")
    args = parser.parse_args(argv)
    if args.global_:
        diffs, again, prefix = check_global(claude_home()), "ah install --global", "~/.claude/"
    else:
        root = config.find_root()
        diffs, again, prefix = check_project(root, config.load(root)), "ah install --project", ""
        # Information only: a project hook changes no exit code.
        for line in project_hooks(root):
            print(line)
    for path, kind in diffs:
        print(f"{prefix}{path} {kind}")
    if diffs:
        print(
            f"ah check: {len(diffs)} installed parts differ from the lockfile and the manifests;"
            f" change facts in .agents/harness.toml, then run {again}",
            file=sys.stderr,
        )
        return 1
    print("ah check: the installed files match the lockfile")
    return 0


def rules(argv: list[str]) -> int:
    from agent_harness import config
    from agent_harness.install.project import rule_sizes

    argparse.ArgumentParser(
        prog="ah rules",
        description="Print each installed rule with its line count and character count.",
    ).parse_args(argv)
    sizes = rule_sizes(config.find_root())
    for rel, lines, chars in sizes:
        print(f"{rel} {lines} lines {chars} chars")
    print(f"total {sum(s[1] for s in sizes)} lines {sum(s[2] for s in sizes)} chars")
    return 0
