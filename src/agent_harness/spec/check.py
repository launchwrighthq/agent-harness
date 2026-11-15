"""Check the layout of the spec folders: the rules each project used to run as its own tests.

    ah spec check

`[specs] checks` picks the checks, and any finding exits 1. `archive`: a settled document in an
active folder, or an unsettled one in the archive. `superseded`: a `superseded-by` path that does
not resolve. `pointers`: a spec path in a file of `[specs] pointerFiles` that does not exist.
`assets`: a file in `<folder>/assets/` whose name no document of the folder holds.

A pointer inside an archive is history, and a path with a placeholder is a pattern, so neither
counts. A pointer to a rendered page resolves when its source exists.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from pathlib import Path

from agent_harness import HarnessError, config
from agent_harness.spec import layout as layouts
from agent_harness.spec import store, tree
from agent_harness.spec.layout import ASSETS, Layout

CHECKS = ("archive", "superseded", "pointers", "assets")
# The window every status reader of the harness reads.
WINDOW = 4000
SUPERSEDED_RE = re.compile(r"\bStatus:\**\s*superseded-by\s+(\S+)", re.IGNORECASE)
LINK_TARGET_RE = re.compile(r"\]\(([^)\s]+)\)")
TARGET_EDGE_RE = re.compile(r"^[`*]+|[`*,;.]+$")
PLACEHOLDER_CHARS = frozenset("<>*{}$")
Docs = dict[str, list[str]]


def status_word(root: Path, name: str) -> str | None:
    parts = tree.status_parts(tree.read(root, name))
    return parts[0] if parts else None


def archive_findings(root: Path, layout: Layout, docs: Docs) -> list[str]:
    found: list[str] = []
    for folder in layout.domain_dirs:
        archive = f"{folder}/{layout.archive}"
        for name in docs[folder]:
            status = status_word(root, name)
            if status in store.SETTLED:
                found.append(f"{name}: status {status} is settled, so it belongs in {archive}/")
        for name in docs[archive]:
            status = status_word(root, name)
            if status not in store.SETTLED:
                found.append(
                    f"{name}: status {status or 'none'} is not settled, so it belongs in {folder}/"
                )
    return found


def superseded_target(text: str) -> str | None:
    """The path a `Status: superseded-by` line names, in backticks, a link or bare."""
    match = SUPERSEDED_RE.search(text[:WINDOW])
    if match is None:
        return None
    link = LINK_TARGET_RE.search(match.group(1))
    target = link.group(1) if link else match.group(1)
    return TARGET_EDGE_RE.sub("", target)


def superseded_findings(root: Path, layout: Layout, docs: Docs) -> list[str]:
    found: list[str] = []
    for name in (name for names in docs.values() for name in names):
        target = superseded_target(tree.read(root, name))
        if target is not None and not (root / target).exists():
            found.append(f"{name}: superseded-by {target} does not resolve")
    return found


def pointer_re(layout: Layout) -> re.Pattern[str]:
    names = "|".join(re.escape(folder) for folder in layout.dirs)
    return re.compile(
        rf"`((?:{names})/[^`\s]+\.(?:md|html))`"
        rf"|\]\(((?:{names})/[^)\s#]+\.(?:md|html))(?:#[^)\s]*)?\)"
    )


def resolves(root: Path, layout: Layout, target: str) -> bool:
    """Whether `target` exists. A page in an `out` folder resolves through its `.md` source."""
    if (root / target).exists():
        return True
    for folder in layout.longest_first():
        pages = f"{folder}/{layout.out}/"
        if target.startswith(pages) and target.endswith(".html"):
            source = f"{folder}/{target[len(pages) : -len('.html')]}.md"
            return (root / source).exists()
    return False


def in_history(name: str, layout: Layout) -> bool:
    """Whether `name` sits in an archive or a page folder, whose paths are history."""
    return any(
        name.startswith((f"{folder}/{layout.archive}/", f"{folder}/{layout.out}/"))
        for folder in layout.domain_dirs
    )


def pointer_findings(root: Path, layout: Layout, files: list[str]) -> list[str]:
    pattern = pointer_re(layout)
    found: list[str] = []
    for name in tree.tracked(root, files):
        if in_history(name, layout):
            continue
        for match in pattern.finditer(tree.read(root, name)):
            target = match.group(1) or match.group(2)
            if PLACEHOLDER_CHARS & set(target) or resolves(root, layout, target):
                continue
            finding = f"{name}: names {target}, which does not exist"
            if finding not in found:
                found.append(finding)
    return found


def asset_findings(root: Path, layout: Layout) -> list[str]:
    found: list[str] = []
    for folder in layout.domain_dirs:
        assets = f"{folder}/{ASSETS}/"
        # A file under a more specific folder of the layout belongs to that folder.
        inner = layout.inner(folder)
        names = [n for n in tree.tracked(root, [folder]) if not (inner and n.startswith(inner))]
        text = "\n".join(
            tree.read(root, name)
            for name in names
            if name.endswith(tree.DOC_SUFFIXES)
            and not name.startswith((assets, f"{folder}/{layout.out}/"))
        )
        for name in names:
            base = name.rsplit("/", 1)[-1]
            if name.startswith(assets) and base not in text:
                found.append(f"{name}: no document of {folder} names {base}")
    return found


def strings(cfg: config.Config, key: str) -> list[str]:
    value = cfg.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise HarnessError(f"{key} must be a list of strings, got {value!r}")
    return list(value)


def findings(root: Path, cfg: config.Config) -> tuple[list[str], int]:
    """Every finding of the checks that `[specs] checks` names, and the number of documents."""
    layout = layouts.from_config(cfg)
    chosen = strings(cfg, "specs.checks")
    unknown = sorted(set(chosen) - set(CHECKS))
    if unknown:
        raise HarnessError(f"specs.checks: unknown check {', '.join(unknown)}")
    docs = tree.documents(root, layout)
    runs: dict[str, Callable[[], list[str]]] = {
        "archive": lambda: archive_findings(root, layout, docs),
        "superseded": lambda: superseded_findings(root, layout, docs),
        "pointers": lambda: pointer_findings(root, layout, strings(cfg, "specs.pointerFiles")),
        "assets": lambda: asset_findings(root, layout),
    }
    found = [line for check in CHECKS if check in chosen for line in runs[check]()]
    return found, sum(len(names) for names in docs.values())


def main(argv: list[str]) -> int:
    doc = (__doc__ or "").splitlines()[0]
    argparse.ArgumentParser(prog="ah spec check", description=doc).parse_args(argv)
    root = config.find_root()
    found, count = findings(root, config.load(root))
    for line in found:
        print(line)
    if found:
        print(f"spec check: {len(found)} findings in {count} documents")
        return 1
    print(f"spec check: {count} documents, no finding")
    return 0
