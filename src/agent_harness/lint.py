"""`ah lint`: banned words, banned fillers and em dashes in Markdown and in code comments."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_harness import HarnessError

# The banned words of the anti-slop rule, each with the forms it takes. A term of art keeps its
# meaning, so `harness` is banned only as a verb: a test harness stays legal.
BANNED_WORDS = {
    "delve": "delv(?:e|es|ed|ing)",
    "foster": "foster(?:s|ed|ing)?",
    "leverage": "leverag(?:e|es|ed|ing)",
    "utilize": "utili[sz](?:e|es|ed|ing|ation)",
    "facilitate": "facilitat(?:e|es|ed|ing|ion)",
    "empower": "empower(?:s|ed|ing|ment)?",
    "streamline": "streamlin(?:e|es|ed|ing)",
    "robust": "robust(?:ly|ness)?",
    "cutting-edge": "cutting[- ]edge",
    "paradigm shift": "paradigm shifts?",
    "game changer": "game[- ]changers?",
    "transformative": "transformative",
    "elevate": "elevat(?:e|es|ed|ing)",
    "embark": "embark(?:s|ed|ing)?",
    "supercharge": "supercharg(?:e|es|ed|ing)",
    "harness": "harness(?:ed|ing)|harness(?:es)? the power",
    "ever-evolving": "ever[- ]evolving",
    "tapestry": "tapestr(?:y|ies)",
    "realm": "realms?",
    "beacon": "beacons?",
    "multifaceted": "multi-?faceted",
    "meticulous": "meticulous(?:ly)?",
    "intricate": "intricate(?:ly)?",
    "paramount": "paramount",
}

BANNED_FILLERS = (
    "it's worth noting",
    "it's important to note",
    "at the end of the day",
    "when it comes to",
    "at its core",
    "in today's world",
    "the reality is",
    "let's dive in",
    "here's the thing",
    "let me be clear",
    "i'll be honest",
    "this changes everything",
)

EM_DASH = "—"
EM_DASH_MESSAGE = "em dash; use a period, comma, colon or parentheses"
# A line carrying this marker is skipped, for a line that must quote a banned form.
LINE_MARKER = "lw-lint-ok"

MARKDOWN = frozenset((".md", ".markdown"))
SLASH_COMMENTS = frozenset(
    (
        ".ts",
        ".mts",
        ".cts",
        ".tsx",
        ".js",
        ".mjs",
        ".cjs",
        ".jsx",
        ".css",
        ".scss",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".swift",
        ".c",
        ".h",
        ".cpp",
    )
)
HASH_COMMENTS = frozenset((".py", ".sh", ".bash", ".zsh", ".toml", ".yaml", ".yml", ".rb", ".tf"))
SKIP_DIRS = frozenset(("node_modules", ".git", "dist", ".venv", "__pycache__", ".lw"))
SHEBANG = re.compile(r"^#!.*\b(bash|sh|python3?)\b")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(`+)[^`]*?\1")


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    column: int
    rule: str
    message: str

    def format(self) -> str:
        return f"{self.file}:{self.line}:{self.column} {self.rule} {self.message}"


@dataclass(frozen=True)
class _Pattern:
    rule: str
    name: str
    regex: re.Pattern[str]


def _escape(text: str) -> str:
    return re.escape(text).replace("'", "['\u2019]")


def _patterns(words: Iterable[str], allow: Iterable[str]) -> list[_Pattern]:
    allowed = {w.lower() for w in allow}
    out: list[_Pattern] = []

    def bounded(form: str) -> re.Pattern[str]:
        return re.compile(rf"(?<![\w-])(?:{form})(?![\w-])", re.IGNORECASE)

    for name, form in BANNED_WORDS.items():
        if name not in allowed:
            out.append(_Pattern("banned-word", name, bounded(form)))
    for word in words:
        if word.lower() not in allowed:
            out.append(_Pattern("banned-word", word, bounded(_escape(word))))
    for filler in BANNED_FILLERS:
        out.append(_Pattern("banned-filler", filler, bounded(_escape(filler))))
    return out


@dataclass(frozen=True)
class _Span:
    """One line of text to check, with the column its text starts at."""

    line: int
    column: int
    text: str


def _markdown_spans(text: str) -> list[_Span]:
    """Markdown prose: fenced code is left out, inline code is blanked so columns stay right."""
    spans: list[_Span] = []
    fence: str | None = None
    for i, raw in enumerate(text.split("\n")):
        marker = FENCE.match(raw)
        if marker is not None:
            if fence is None:
                fence = marker.group(1)
            elif marker.group(1).startswith(fence):
                fence = None
            continue
        if fence is not None:
            continue
        blanked = INLINE_CODE.sub(lambda m: " " * len(m.group(0)), raw)
        spans.append(_Span(i + 1, 1, blanked))
    return spans


def _comment_spans(text: str, style: str) -> list[_Span]:
    """The comment part of each line, found by a scan that steps over quoted strings."""
    spans: list[_Span] = []
    block = False
    for i, raw in enumerate(text.split("\n")):
        quote: str | None = None
        j = 0
        while j < len(raw):
            c = raw[j]
            if block:
                end = raw.find("*/", j)
                stop = len(raw) if end == -1 else end
                spans.append(_Span(i + 1, j + 1, raw[j:stop]))
                if end == -1:
                    break
                block = False
                j = end + 2
                continue
            if quote is not None:
                if c == "\\":
                    j += 1
                elif c == quote:
                    quote = None
                j += 1
                continue
            if c in "\"'`":
                quote = c
                j += 1
                continue
            previous = raw[j - 1] if j > 0 else ""
            if style == "hash" and c == "#" and (j == 0 or previous.isspace()):
                spans.append(_Span(i + 1, j + 2, raw[j + 1 :]))
                break
            following = raw[j + 1] if j + 1 < len(raw) else ""
            if style == "slash" and c == "/" and following == "/" and previous != ":":
                spans.append(_Span(i + 1, j + 3, raw[j + 2 :]))
                break
            if style == "slash" and c == "/" and following == "*":
                block = True
                j += 2
                continue
            j += 1
    return spans


def lint_text(
    name: str, text: str, words: Sequence[str] = (), allow: Sequence[str] = ()
) -> list[Finding]:
    """The findings of one text. `name` decides how it is read: Markdown, or code comments."""
    suffix = Path(name).suffix.lower()
    lines = text.split("\n")
    markdown = suffix in MARKDOWN
    if markdown:
        spans = _markdown_spans(text)
    elif suffix in SLASH_COMMENTS:
        spans = _comment_spans(text, "slash")
    elif suffix in HASH_COMMENTS or SHEBANG.match(lines[0] if lines else ""):
        spans = _comment_spans(text, "hash")
    else:
        return []

    def skip(line: int) -> bool:
        return LINE_MARKER in (lines[line - 1] if line - 1 < len(lines) else "")

    out: list[Finding] = []
    if markdown:
        for i, raw in enumerate(lines):
            at = raw.find(EM_DASH)
            if at != -1 and not skip(i + 1):
                out.append(Finding(name, i + 1, at + 1, "em-dash", EM_DASH_MESSAGE))
    patterns = _patterns(words, allow)
    for span in spans:
        if skip(span.line):
            continue
        if not markdown:
            at = span.text.find(EM_DASH)
            if at != -1:
                out.append(Finding(name, span.line, span.column + at, "em-dash", EM_DASH_MESSAGE))
        for p in patterns:
            for m in p.regex.finditer(span.text):
                if p.rule == "banned-word":
                    message = f'banned word "{m.group(0)}" ({p.name}); name the plain fact'
                else:
                    message = f'banned filler "{m.group(0)}"; delete it'
                out.append(Finding(name, span.line, span.column + m.start(), p.rule, message))
    return sorted(out, key=lambda f: (f.line, f.column))


def _expand(path: Path) -> list[Path]:
    if not path.exists():
        raise HarnessError(f"cannot read {path}: it does not exist")
    if not path.is_dir():
        return [path]
    out: list[Path] = []
    for entry in sorted(path.iterdir(), key=lambda p: p.name):
        if entry.name in SKIP_DIRS or entry.name.startswith("."):
            continue
        out.extend(_expand(entry))
    return out


def lint_paths(
    paths: Iterable[Path], words: Sequence[str] = (), allow: Sequence[str] = ()
) -> list[Finding]:
    """Lint files and folders. A walk skips `node_modules`, `dist` and dot folders."""
    out: list[Finding] = []
    for path in paths:
        for file in _expand(path):
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                raise HarnessError(f"cannot read {file}: {error}") from error
            out.extend(lint_text(str(file), text, words, allow))
    return out


USAGE = "usage: ah lint <path ...>   banned words, fillers and em dashes in Markdown and comments"


def main(argv: list[str]) -> int:
    from agent_harness import config

    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE, file=sys.stderr if not argv else sys.stdout)
        return 2 if not argv else 0
    cfg = config.load()
    words = cfg.get("harness.lint.words")
    allow = cfg.get("harness.lint.allow")
    findings = lint_paths([Path(p) for p in argv], list(words), list(allow))
    for finding in findings:
        print(finding.format())
    return 1 if findings else 0
