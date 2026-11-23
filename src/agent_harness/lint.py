"""`ah lint`: banned words, banned fillers and em dashes in Markdown and in code comments.

`--changed` and `--refs` add the spec references of `comment_refs`.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
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


DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _python_spans(text: str) -> list[_Span]:
    """`#` comments through tokenize and docstrings through ast, so a `#` in a string is code.

    Raises SyntaxError when the text is not Python.
    """
    lines = text.split("\n")
    spans: list[_Span] = []
    for node in ast.walk(ast.parse(text)):
        body = getattr(node, "body", None) if isinstance(node, DOCSTRING_OWNERS) else None
        first = body[0] if body else None
        if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)):
            continue
        if not isinstance(first.value.value, str) or first.end_lineno is None:
            continue
        for n in range(first.lineno, first.end_lineno + 1):
            spans.append(_Span(n, 1, lines[n - 1]))
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            row, col = tok.start
            if tok.type == tokenize.COMMENT and not (row == 1 and tok.string.startswith("#!")):
                spans.append(_Span(row, col + 2, tok.string[1:]))
    except tokenize.TokenError as error:
        raise SyntaxError(str(error)) from error
    return sorted(spans, key=lambda s: (s.line, s.column))


def comment_spans(name: str, text: str) -> list[_Span] | None:
    """The comment text of a code file, or None when `name` is not a code file that lint reads.

    Python gives its docstrings too. A Python file that does not parse raises SyntaxError.
    """
    suffix = Path(name).suffix.lower()
    if suffix == ".py":
        return _python_spans(text)
    if suffix in SLASH_COMMENTS:
        return _comment_spans(text, "slash")
    first = text.split("\n", 1)[0]
    if suffix in HASH_COMMENTS or SHEBANG.match(first):
        return _comment_spans(text, "hash")
    return None


def parse_finding(name: str, error: SyntaxError) -> Finding:
    """The finding of a Python file that does not parse: lint cannot read its comments."""
    message = f"not valid Python ({error.msg}); its comments and docstrings were not read"
    return Finding(name, error.lineno or 1, error.offset or 1, "parse-error", message)


def lint_text(
    name: str, text: str, words: Sequence[str] = (), allow: Sequence[str] = ()
) -> list[Finding]:
    """The findings of one text. `name` decides how it is read: Markdown, or code comments."""
    lines = text.split("\n")
    markdown = Path(name).suffix.lower() in MARKDOWN
    out: list[Finding] = []
    if markdown:
        spans = _markdown_spans(text)
    else:
        try:
            code = comment_spans(name, text)
        except SyntaxError as error:
            out.append(parse_finding(name, error))
            code = _comment_spans(text, "hash")
        if code is None:
            return []
        spans = code

    def skip(line: int) -> bool:
        return LINE_MARKER in (lines[line - 1] if line - 1 < len(lines) else "")

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


def expand(path: Path) -> list[Path]:
    """The files of a path. A walk skips `node_modules`, `dist` and dot folders."""
    if not path.exists():
        raise HarnessError(f"cannot read {path}: it does not exist")
    if not path.is_dir():
        return [path]
    out: list[Path] = []
    for entry in sorted(path.iterdir(), key=lambda p: p.name):
        if entry.name in SKIP_DIRS or entry.name.startswith("."):
            continue
        out.extend(expand(entry))
    return out


def lint_paths(
    paths: Iterable[Path], words: Sequence[str] = (), allow: Sequence[str] = ()
) -> list[Finding]:
    """Lint files and folders. A walk skips `node_modules`, `dist` and dot folders."""
    out: list[Finding] = []
    for path in paths:
        for file in expand(path):
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                raise HarnessError(f"cannot read {file}: {error}") from error
            out.extend(lint_text(str(file), text, words, allow))
    return out


USAGE = """usage: ah lint <path ...>
       ah lint --changed
       ah lint --refs <path ...>

<path ...>       banned words, fillers and em dashes in Markdown, code comments and docstrings
--changed        every rule, spec references included, on the lines added since the base branch
--refs <path>    spec references (harness.lint.commentPatterns) in comments, on every line"""


def main(argv: list[str]) -> int:
    from agent_harness import comment_refs, config

    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE, file=sys.stderr if not argv else sys.stdout)
        return 2 if not argv else 0
    changed, refs = argv[0] == "--changed", argv[0] == "--refs"
    if (changed and len(argv) > 1) or (refs and len(argv) == 1):
        print(USAGE, file=sys.stderr)
        return 2
    cfg = config.load()
    words = list(cfg.get("harness.lint.words"))
    allow = list(cfg.get("harness.lint.allow"))
    if changed:
        findings = comment_refs.changed_findings(cfg, words)
    elif refs:
        findings = comment_refs.path_findings(cfg, [Path(p) for p in argv[1:]])
    else:
        findings = lint_paths([Path(p) for p in argv], words, allow)
    for finding in findings:
        print(finding.format())
    return 1 if findings else 0
