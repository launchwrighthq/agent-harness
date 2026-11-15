"""Count the tracked documents of each spec folder by status, and list what needs a reader.

    ah spec census [--json]

Besides the counts it lists three findings that no layout check fails on: a name in an active
folder and in its archive (a merge can restore a settled document, and the archived copy can be
the newer one), a settled status that names no evidence, and a document with no status. The
census judges nothing, so it exits 0.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from agent_harness import config
from agent_harness.spec import layout as layouts
from agent_harness.spec import render, store, tree
from agent_harness.spec.layout import Layout

NONE = "none"
# What may follow a status word without being evidence.
FILLER_RE = re.compile(r"^[\s\-\u2013:;,.`*]*$")


def order(counts: Counter[str]) -> dict[str, int]:
    """The vocabulary order of the renderer, then any other value, then no status."""
    known = [status for status in render.STATUS_VALUES if status in counts]
    other = sorted(set(counts) - set(known) - {NONE})
    tail = [NONE] if NONE in counts else []
    return {status: counts[status] for status in [*known, *other, *tail]}


def duplicates(layout: Layout, docs: dict[str, list[str]]) -> list[list[str]]:
    found: list[list[str]] = []
    for folder in layout.domain_dirs:
        archive = f"{folder}/{layout.archive}"
        archived = {name.rsplit("/", 1)[-1]: name for name in docs[archive]}
        for name in docs[folder]:
            twin = archived.get(name.rsplit("/", 1)[-1])
            if twin:
                found.append([name, twin])
    return found


def take(root: Path, layout: Layout) -> dict[str, Any]:
    docs = tree.documents(root, layout)
    folders: dict[str, dict[str, int]] = {}
    bare: list[str] = []
    missing: list[str] = []
    statuses: dict[str, str] = {}
    for folder, names in docs.items():
        counts: Counter[str] = Counter()
        for name in names:
            status, evidence = tree.status_parts(tree.read(root, name)) or (NONE, "")
            counts[status] += 1
            statuses[name] = status
            if status == NONE:
                missing.append(name)
            elif status in store.SETTLED and FILLER_RE.match(evidence):
                bare.append(name)
        folders[folder] = order(counts)
    return {
        "folders": folders,
        "duplicates": duplicates(layout, docs),
        "bare": bare,
        "missing": missing,
        "statuses": statuses,
    }


def render_census(found: dict[str, Any]) -> str:
    lines: list[str] = []
    for folder, counts in found["folders"].items():
        total = sum(counts.values())
        listed = ", ".join(f"{status} {n}" for status, n in counts.items())
        lines.append(f"{folder}: {total} documents" + (f": {listed}" if listed else ""))
    notes = [
        *(f"In an active folder and its archive: {a}, {b}" for a, b in found["duplicates"]),
        *(
            f"Settled with no evidence: {name} ({found['statuses'][name]})"
            for name in found["bare"]
        ),
        *(f"No status: {name}" for name in found["missing"]),
    ]
    return "\n".join([*lines, *([""] if notes else []), *notes])


def main(argv: list[str]) -> int:
    doc = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(prog="ah spec census", description=doc)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = config.find_root()
    found = take(root, layouts.from_config(config.load(root)))
    print(json.dumps(found, indent=2) if args.json else render_census(found))
    return 0
