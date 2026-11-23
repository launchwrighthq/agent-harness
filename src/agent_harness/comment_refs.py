"""Spec references in code comments: `ah lint --changed` and `ah lint --refs`.

The spec points at the code. A comment that points back, with a row ID, a spec section, a date or
a port note, goes stale when the spec moves (`.claude/rules/lw/code-limits.md`). A gap that a
spec keeps open gets one marker comment, `lw-gap <ID>: <when to remove it>`, and the check passes
it. The patterns are `harness.lint.commentPatterns`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from agent_harness import HarnessError, comments, config
from agent_harness.lint import (
    LINE_MARKER,
    Finding,
    comment_spans,
    expand,
    lint_text,
    parse_finding,
)

GAP_MARKER = re.compile(r"^\s*lw-gap [A-Z]{1,2}\d+: \S")
# Config files carry pins and dates on purpose: the check reads code only.
CONFIG_SUFFIXES = frozenset((".toml", ".yaml", ".yml"))
RULE = "spec-ref"


def compile_patterns(patterns: Mapping[str, object]) -> dict[str, re.Pattern[str]]:
    """The patterns of the config, compiled. An empty value turns its pattern off."""
    out: dict[str, re.Pattern[str]] = {}
    for name, source in patterns.items():
        if not source:
            continue
        try:
            out[name] = re.compile(str(source))
        except re.error as error:
            key = f'harness.lint.commentPatterns."{name}"'
            raise HarnessError(f"{key} is not a valid regular expression: {error}") from error
    return out


def in_scope(path: str, exclude: Sequence[str]) -> bool:
    """True when the check reads the project-relative `path`."""
    p = path.replace("\\", "/")
    if Path(p).suffix.lower() in CONFIG_SUFFIXES:
        return False
    return not any(comments.glob_pattern(g).match(p) for g in exclude)


def ref_findings(
    name: str, text: str, patterns: Mapping[str, re.Pattern[str]], allow: Iterable[str] = ()
) -> list[Finding]:
    """Each spec reference in the comments and docstrings of one code file."""
    try:
        spans = comment_spans(name, text)
    except SyntaxError as error:
        return [parse_finding(name, error)]
    if spans is None:
        return []
    allowed = {a.lower() for a in allow}
    lines = text.split("\n")
    out: list[Finding] = []
    for span in spans:
        if GAP_MARKER.match(span.text) or LINE_MARKER in lines[span.line - 1]:
            continue
        for pattern, regex in patterns.items():
            for m in regex.finditer(span.text):
                if m.group(0).lower() in allowed:
                    continue
                message = (
                    f'{pattern} "{m.group(0)}": a comment names the spec. Delete it, or keep an '
                    'open gap as "lw-gap <ID>: <removal condition>"'
                )
                out.append(Finding(name, span.line, span.column + m.start(), RULE, message))
    return sorted(out, key=lambda f: (f.line, f.column))


def _settings(cfg: config.Config) -> tuple[dict[str, re.Pattern[str]], list[str], list[str]]:
    patterns = compile_patterns(cfg.get("harness.lint.commentPatterns"))
    exclude = list(cfg.get("harness.lint.commentExclude"))
    return patterns, exclude, list(cfg.get("harness.lint.allow"))


def changed_findings(cfg: config.Config, words: Sequence[str]) -> list[Finding]:
    """Every lint rule, spec references included, on the lines added since the base branch.

    The files are those of `comments.changed_files`. An untracked file is new on every line.
    """
    patterns, exclude, allow = _settings(cfg)
    root = cfg.root
    base = str(cfg.get("harness.baseBranch"))
    since = comments.fork_point(root, base)
    out: list[Finding] = []
    for path in comments.changed_files(root, base):
        file = root / path
        if not file.is_file():
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        found = lint_text(path, text, words, allow)
        if in_scope(path, exclude):
            found += ref_findings(path, text, patterns, allow)
        new = comments.added_lines(root, since, path)
        for finding in dict.fromkeys(found):
            if new is None or finding.line in new or finding.rule == "parse-error":
                out.append(finding)
    return out


def path_findings(cfg: config.Config, paths: Iterable[Path]) -> list[Finding]:
    """The spec references in the named files and folders, on every line."""
    patterns, exclude, allow = _settings(cfg)
    root = cfg.root.resolve()
    out: list[Finding] = []
    for path in paths:
        for file in expand(path):
            try:
                rel = file.resolve().relative_to(root).as_posix()
            except ValueError:
                rel = file.as_posix()
            if not in_scope(rel, exclude):
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                raise HarnessError(f"cannot read {file}: {error}") from error
            out.extend(ref_findings(str(file), text, patterns, allow))
    return out
