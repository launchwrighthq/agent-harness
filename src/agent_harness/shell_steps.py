"""A command line as the steps that bash runs, for the env guards (docs/hooks.md).

A step is one simple command: its words after the leading assignments, its redirections, its
heredoc bodies and the steps of each command substitution inside it. Quotes are removed from
each word as bash removes them. A word records whether bash can still change it (an unquoted
expansion, glob, brace group or `~`) or whether it holds text that the parser cannot know (an
expansion inside double quotes, or `$'...'`). Text that the parser cannot read raises
`ShellSyntaxError`, and the caller then fails closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent_harness import HarnessError

# The words that open or close a compound command. At the start of a step they are no command.
KEYWORDS = frozenset(
    {"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "!", "{", "}", "esac"}
)
JOINERS = ("&&", "||", ";;", "|&", ";", "|", "&", "\n")
BLANKS = " \t"
WORD_END = BLANKS + "\n;&|()<>"
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
NAME_START = re.compile(r"[A-Za-z0-9_?#!$*@-]")


class ShellSyntaxError(HarnessError):
    """A command line that the parser cannot read the way bash reads it."""


@dataclass(frozen=True)
class Word:
    text: str
    # The characters outside any expansion: `"$S/x"` gives `/x`.
    literal: str
    start: int
    end: int
    # An unquoted expansion, glob, brace group or `~`: bash can split the word or change it.
    expands: bool = False
    # An expansion inside double quotes, or `$'...'`: one word of text the parser cannot know.
    hidden: bool = False


@dataclass(frozen=True)
class Heredoc:
    body: str
    # Bash expands nothing in a body with a quoted delimiter, nor in a here-string body here.
    quoted: bool
    # Where the body lines of a `<<` heredoc sit in the text. A here-string has no span.
    start: int = field(default=0, compare=False)
    end: int = field(default=0, compare=False)


@dataclass
class Step:
    words: list[Word] = field(default_factory=list)
    assignments: list[Word] = field(default_factory=list)
    redirects: list[Word] = field(default_factory=list)
    heredocs: list[Heredoc] = field(default_factory=list)
    # The steps of each command or process substitution of the step, in order.
    nested: list[list[Step]] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.words or self.assignments or self.redirects or self.nested)


def parse(text: str) -> list[Step]:
    """The steps of `text`. Raises `ShellSyntaxError` on text it cannot read."""
    steps, end = _Parser(text).steps(0, closer=None)
    if end != len(text):
        raise ShellSyntaxError(f"an unmatched `)` at offset {end}")
    return steps


@dataclass
class _Pending:
    step: Step
    delimiter: str
    quoted: bool
    strip_tabs: bool


@dataclass
class _WordState:
    text: str = ""
    literal: str = ""
    expands: bool = False
    hidden: bool = False
    quoted: bool = False


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pending: list[_Pending] = []

    def steps(self, i: int, closer: str | None) -> tuple[list[Step], int]:
        """The steps from `i` up to `closer` (`)` of a substitution) or the end of the text."""
        text = self.text
        out: list[Step] = []
        step = Step()
        depth = 0
        while i < len(text):
            c = text[i]
            if c in BLANKS:
                i += 1
            elif text.startswith("\\\n", i):
                i += 2
            elif c == "#" and (i == 0 or text[i - 1] in WORD_END):
                while i < len(text) and text[i] != "\n":
                    i += 1
            elif text.startswith("((", i) and step.empty():
                i = self._arithmetic(i + 2)
            elif c == "(":
                step, depth, i = self._close(out, step), depth + 1, i + 1
            elif c == ")":
                if depth == 0 and closer == ")":
                    break
                step, depth, i = self._close(out, step), max(depth - 1, 0), i + 1
            elif text.startswith(("<(", ">("), i):
                nested, close = _Parser(text).steps(i + 2, closer=")")
                step.nested.append(nested)
                i = close + 1
            elif self._redirect_at(i):
                i = self._redirect(step, i)
            elif joiner := next((j for j in JOINERS if text.startswith(j, i)), None):
                step = self._close(out, step)
                i += len(joiner)
                if joiner == "\n":
                    i = self._bodies(i)
            else:
                i = self._word(step, i)
        else:
            if closer is not None:
                raise ShellSyntaxError("a command substitution that never closes")
        self._close(out, step)
        if closer is None:
            self._bodies(len(text))
        return out, i

    def _close(self, out: list[Step], step: Step) -> Step:
        if not step.empty():
            out.append(step)
        return Step()

    def _arithmetic(self, i: int) -> int:
        depth = 2
        while i < len(self.text) and depth:
            depth += {"(": 1, ")": -1}.get(self.text[i], 0)
            i += 1
        if depth:
            raise ShellSyntaxError("an arithmetic expression that never closes")
        return i

    def _redirect_at(self, i: int) -> bool:
        text = self.text
        if text[i] in "<>":
            return not text.startswith(("<(", ">("), i)
        return text.startswith(("&>", ">&"), i) and not text.startswith("&>(", i)

    def _redirect(self, step: Step, i: int) -> int:
        operator = re.match(r"&>>|&>|<<<|<<-|<<|<>|<&|>>|>&|>\||<|>", self.text[i:])
        assert operator is not None
        kind = operator.group(0)
        i += len(kind)
        while i < len(self.text) and self.text[i] in BLANKS:
            i += 1
        if i >= len(self.text) or self.text[i] in WORD_END:
            raise ShellSyntaxError(f"`{kind}` with no word after it")
        state, end = self._read_word(step, i)
        target = Word(state.text, state.literal, i, end, state.expands, state.hidden)
        if kind in ("<<", "<<-"):
            raw = self.text[i:end]
            quoted = any(q in raw for q in "'\"\\")
            self.pending.append(_Pending(step, state.text, quoted, kind == "<<-"))
        elif kind == "<<<":
            step.heredocs.append(Heredoc(state.text, quoted=True))
        else:
            step.redirects.append(target)
        return end

    def _bodies(self, i: int) -> int:
        """Read the body of each pending heredoc from `i`, the start of the next line."""
        text = self.text
        for pending in self.pending:
            lines: list[str] = []
            start = stop = min(i, len(text))
            while i < len(text):
                end = text.find("\n", i)
                end = len(text) if end < 0 else end
                line = text[i:end]
                i = end + 1
                shown = line.lstrip("\t") if pending.strip_tabs else line
                if shown == pending.delimiter:
                    break
                lines.append(shown + "\n")
                stop = end
            body = "".join(lines)
            pending.step.heredocs.append(Heredoc(body, pending.quoted, start, stop))
            if not pending.quoted:
                pending.step.nested.extend(body_substitutions(body))
        self.pending = []
        return min(i, len(text))

    def _word(self, step: Step, i: int) -> int:
        state, end = self._read_word(step, i)
        if end == i:
            raise ShellSyntaxError(f"no word at offset {i}")
        word = Word(state.text, state.literal, i, end, state.expands, state.hidden)
        if end < len(self.text) and self.text[end] in "<>" and state.text.isdigit():
            return end
        if not step.words and not state.quoted and state.text in KEYWORDS:
            return end
        raw = self.text[i:end]
        if not step.words and ASSIGNMENT.match(raw):
            step.assignments.append(word)
        else:
            step.words.append(word)
        return end

    def _read_word(self, step: Step, i: int) -> tuple[_WordState, int]:
        text = self.text
        state = _WordState()
        start = i
        while i < len(text) and text[i] not in WORD_END:
            c = text[i]
            if c == "'":
                end = text.find("'", i + 1)
                if end < 0:
                    raise ShellSyntaxError("a single quote that never closes")
                state.text += text[i + 1 : end]
                state.literal += text[i + 1 : end]
                state.quoted = True
                i = end + 1
            elif c == '"':
                i = self._double_quoted(step, state, i + 1)
            elif c == "\\":
                if i + 1 < len(text):
                    state.text += text[i + 1]
                    state.literal += text[i + 1]
                    state.quoted = True
                i += 2
            elif c == "$" and text.startswith("$'", i):
                end = ansi_c_end(text, i + 2)
                state.text += text[i + 2 : end - 1]
                state.hidden = state.quoted = True
                i = end
            elif c in "$`":
                i = self._expansion(step, state, i, quoted=False)
            else:
                if c in "*?[" or c == "{" or (c == "~" and i == start):
                    state.expands = True
                state.text += c
                state.literal += c
                i += 1
        return state, i

    def _double_quoted(self, step: Step, state: _WordState, i: int) -> int:
        text = self.text
        state.quoted = True
        while i < len(text) and text[i] != '"':
            c = text[i]
            if c == "\\" and i + 1 < len(text) and text[i + 1] in '$`"\\\n':
                state.text += text[i + 1]
                state.literal += text[i + 1]
                i += 2
            elif c in "$`":
                i = self._expansion(step, state, i, quoted=True)
            else:
                state.text += c
                state.literal += c
                i += 1
        if i >= len(text):
            raise ShellSyntaxError("a double quote that never closes")
        return i + 1

    def _expansion(self, step: Step, state: _WordState, i: int, quoted: bool) -> int:
        """Read the expansion at `i` into `state`: its text stays, and it marks the word."""
        text = self.text
        if text[i] == "`":
            end = text.find("`", i + 1)
            while end > 0 and text[end - 1] == "\\":
                end = text.find("`", end + 1)
            if end < 0:
                raise ShellSyntaxError("a backtick that never closes")
            step.nested.append(parse(text[i + 1 : end].replace("\\`", "`")))
            end += 1
        elif text.startswith("$((", i):
            end = self._arithmetic(i + 3)
        elif text.startswith("$(", i):
            nested, close = _Parser(text).steps(i + 2, closer=")")
            step.nested.append(nested)
            end = close + 1
        elif text.startswith("${", i):
            close = text.find("}", i + 2)
            if close < 0 or re.search(r"\$\(|`", text[i:close]):
                raise ShellSyntaxError("a parameter expansion that the parser cannot read")
            end = close + 1
        elif i + 1 < len(text) and NAME_START.match(text[i + 1]):
            name = re.match(r"[A-Za-z_][A-Za-z0-9_]*|.", text[i + 1 :])
            assert name is not None
            end = i + 1 + len(name.group(0))
        else:
            state.text += "$"
            state.literal += "$"
            return i + 1
        state.text += text[i:end]
        if quoted:
            state.hidden = True
        else:
            state.expands = True
        return end


def ansi_c_end(text: str, i: int) -> int:
    """The index after the quote that closes a `$'...'` body that starts at `i`."""
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == "'":
            return i + 1
        else:
            i += 1
    raise ShellSyntaxError("a $'...' quote that never closes")


def body_substitutions(body: str) -> list[list[Step]]:
    """The steps of each command substitution in an unquoted heredoc body."""
    out: list[list[Step]] = []
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
        elif body.startswith("$((", i):
            i += 3
        elif body.startswith("$(", i):
            nested, close = _Parser(body).steps(i + 2, closer=")")
            out.append(nested)
            i = close + 1
        elif body[i] == "`":
            end = body.find("`", i + 1)
            if end < 0:
                raise ShellSyntaxError("a backtick that never closes")
            out.append(parse(body[i + 1 : end]))
            i = end + 1
        else:
            i += 1
    return out
