"""Prove that a Markdown spec converted from a hand-written HTML spec kept every word.

    ah spec verify <old.html> <new.md>

It renders the Markdown and compares its visible words with those of the old page. A word only
the old page has is a loss and exits 1. A word only the Markdown has is listed and passes. The
check reads text only: a callout turned prose or a diagram that lost its line breaks passes.

Both sides drop `<nav>` (the headings again) and inline `<svg>` (its labels live in the asset).
The extraction parses the HTML, because a `<pre>` can hold a bare `<`, such as `a <- b`.
"""

from __future__ import annotations

import argparse
import re
from html.parser import HTMLParser
from pathlib import Path

from agent_harness import HarnessError, config
from agent_harness.spec import layout, render

HIDDEN = frozenset({"nav", "svg", "script", "style", "head", "title", "template"})
HEADER_WORDS = frozenset({"status", "date", "subject", "range", *render.STATUS_VALUES})
WORD_RE = re.compile(r"[a-z0-9_./-]+")
WORD_EDGE_RE = re.compile(r"^[.,;:/-]+|[.,;:/-]+$")
BODY_RE = re.compile(r"<body\b", re.IGNORECASE)
HEADER_FIELD_RE = re.compile(r"^(Date|Status|Subject|Range):")
# The header fields sit in the first lines of a source, above the first section.
HEADER_LINES = 8
ASSET_TARGET_RE = re.compile(r"\]\(assets/[^)]*\)")
MARK_RE = re.compile(r"[*`~|>#\[\]()]")
SHOWN = 40


class VisibleText(HTMLParser):
    """The text a reader sees, with the elements of `HIDDEN` left out."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.hidden += tag in HIDDEN
        self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in HIDDEN and self.hidden:
            self.hidden -= 1
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def visible(page: str) -> str:
    body = BODY_RE.search(page)
    if body:
        page = page[page.find(">", body.start()) + 1 :]
    parser = VisibleText()
    parser.feed(page)
    parser.close()
    return "".join(parser.parts)


def words(text: str) -> dict[str, int]:
    """The lowercase tokens of `text` and their counts. A one-character token counts, because
    a section number is one."""
    counts: dict[str, int] = {}
    for match in WORD_RE.finditer(text.lower()):
        token = WORD_EDGE_RE.sub("", match.group(0))
        if token:
            counts[token] = counts.get(token, 0) + 1
    return counts


def markdown_words(markdown: str) -> dict[str, int]:
    """The words of the source without its header fields and its image targets."""
    lines = markdown.split("\n")
    kept = [
        line for n, line in enumerate(lines) if n >= HEADER_LINES or not HEADER_FIELD_RE.match(line)
    ]
    body = ASSET_TARGET_RE.sub("] ", "\n".join(kept))
    return words(MARK_RE.sub(" ", body))


def compare(old: str, markdown: str, source: str) -> tuple[list[str], list[str]]:
    """The words only the old page has, and the words only the Markdown has."""
    before = words(visible(old))
    page, _ = render.render(markdown, source, layout.from_config(config.load()))
    after = words(visible(page))
    lost = sorted(w for w in before if w not in after and w not in HEADER_WORDS)
    written = markdown_words(markdown)
    surplus = sorted(w for w in written if w not in before and w not in HEADER_WORDS)
    return lost, surplus


def read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise HarnessError(f"{path}: {error.strerror or error}") from error


def report(title: str, found: list[str]) -> None:
    if found:
        print(f"{len(found)} words are {title}:")
        for word in found[:SHOWN]:
            print(f"  {word}")


def main(argv: list[str]) -> int:
    doc = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(prog="ah spec verify", description=doc)
    parser.add_argument("old", help="the hand-written HTML page")
    parser.add_argument("new", help="the Markdown source under a spec folder")
    args = parser.parse_args(argv)
    try:
        lost, surplus = compare(read(args.old), read(args.new), args.new)
    except render.SpecError as error:
        raise HarnessError(f"{args.new}: did not render: {error}") from error
    old, new = Path(args.old).name, Path(args.new).name
    report(f"in {old} and nowhere in the rendered page", lost)
    report(f"in {new} and not in {old}", surplus)
    if not lost and not surplus:
        print("no words lost, no words invented")
    return 1 if lost else 0
