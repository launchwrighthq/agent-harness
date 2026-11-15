#!/usr/bin/env python3
"""Take every mark of Claude authorship out of a commit message.

Not two named trailers: every SHAPE one can take, including names no harness has emitted yet. A
harness default that changes its wording next month must still find the wall here. So the rules
below key on what an attribution IS rather than on the strings seen so far: a trailer whose key
or value names us, a credit sentence, a session link, an `@anthropic.com` address.

The module is the whole mechanism, and the two callers see the same rules:

- the `commit-msg` git hook runs `strip` over the file git is about to read. It EDITS rather
  than rejects. A reject costs the author a retry and removes nothing on its own, while a
  generator emitting the trailer from a template hits the wall on every attempt.
- a project test can run `carries` over the history, because `git commit --no-verify` walks
  past any hook and leaves no trace.

Sharing `RULES` between them is the point. A check STRICTER than the hook fails a commit the
hook has already cleaned, and offers no way left to phrase it. A check LOOSER lets through
exactly what the hook would have taken.

The line that must not be crossed: a rule can only be explained by naming the thing it bans.
This file, the rule file, the test and any commit message about them all write
`Co-Authored-By:` and `Claude-Session:` in prose, and every one of them has to survive a pass
through `strip` byte for byte. A commit body can also cite a `.claude/...` path. So:

- Every INLINE rule demands a VALUE, never a bare name. An inline rule matching
  `Claude-Session[ \\t]*:[ \\t]*\\S+` eats a backtick out of a message whose token after the
  colon is "`," and not a URL.
- Every LINE rule skips the SUBJECT, so a summary is never deleted whatever it says.
- A trailer VALUE that only names a path (`.claude/rules`) is not an attribution. `IDENTITY` is
  what an attribution looks like: an address, a product link, a model name.
- The catch-all by VALUE reaches only the TRAILER BLOCK, which is git's own definition: the last
  paragraph, every line of it `Key: value`. Without that, a body line such as
  `Default: claude-opus-5` is deleted outright, and neither sweep can see the loss, because the
  line names a model.

To find over-reach, run the rules over a commit history and assert that every line they take
carries one of `claude`, `anthropic`, `co-authored` or the robot. That check is coarser than the
rules on purpose: a regex that eats prose fails it.

The regexes spell `\\w` and `\\b` as ASCII classes, so a non-ASCII letter is never a word
character.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

WORD = "[A-Za-z0-9_]"
START = f"(?<!{WORD})"
END = f"(?!{WORD})"
ROBOT = "\U0001f916"

# What an attribution VALUE looks like, as opposed to a mention: an address, a session or product
# link, or a model name. `.claude/rules/git.md` in a trailer value matches none of these.
#
# `docs.anthropic.com` is deliberately NOT here. A commit that cites the API documentation is not
# claiming Claude wrote it. `claude.ai` and `claude.com` ARE here: no link to either has ever been
# anything but a session or the product page.
IDENTITY = (
    r"(?:<[^>\n]*@[^>\n]*>"
    rf"|https?://(?:[A-Za-z0-9_.-]*\.)?claude\.(?:ai|com)\S*"
    r"|[^\n]*@anthropic\.com"
    rf"|{START}claude[-\s]?(?:code|opus|sonnet|haiku|fable|[0-9])"
    rf"|{START}anthropic{END})"
)

# No inline rule may start in the MIDDLE of a token. The authorship-verb rule once matched
# `Authored-By: ...` INSIDE `Co-Authored-By: ...` and left `Co-` standing in the message.
NOT_MID_WORD = r"(?<![A-Za-z0-9_-])"

# What an INLINE rule may consume, and where it must stop. A folded trailer sits inside a
# sentence, so nothing here may run to the end of the line. The bracketed address is tried FIRST,
# and the brackets must hold an `@`, so prose such as "written as <name> <email>" survives.
VALUE = r"(?:[^<\n]*<[^>\n]*@[^>\n]*>|https?://\S+|\S*@[A-Za-z0-9_.-]+\.[A-Za-z0-9_]+)"

# A trailer key: `Co-Authored-By`, `Claude-Session`, `Assisted-By`. Never `feat(x)` or `docs`.
KEY = r"[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*"

VERBS = r"(?:Generated|Created|Authored|Assisted|Written|Committed)-(?:By|With)"
CREDIT = r"(?:Generated|Created|Written|Made|Built|Authored)"
EOL = r"(?:\r?\n|$)"

LINE_FLAGS = re.IGNORECASE | re.MULTILINE


class Rule(NamedTuple):
    id: str
    line: re.Pattern[str] | None
    inline: re.Pattern[str] | None
    block_only: bool = False


# The rules, most specific first. A new shape is a row here and nothing else.
#
# `line` runs over the BODY only, and takes the whole line. `inline` runs over the whole message
# including the subject, and takes the construct out of the sentence it was folded into. A
# squashed body loses its newlines, so the same trailer arrives in both shapes.
RULES = (
    # Every co-author trailer, not only ours. A human co-author is removed too: no commit names
    # a second author.
    Rule(
        "Co-Authored-By",
        re.compile(rf"^[ \t]*Co-Authored-By[ \t]*:.*{EOL}", LINE_FLAGS),
        re.compile(rf"{NOT_MID_WORD}[ \t]*Co-Authored-By[ \t]*:[ \t]*{VALUE}", re.IGNORECASE),
    ),
    # Any key we could own, whatever comes after the hyphen: `Claude-Session` today,
    # `Claude-Run-Id` or `Anthropic-Trace` the day a harness starts writing one.
    Rule(
        "a Claude- or Anthropic- trailer",
        re.compile(rf"^[ \t]*(?:Claude|Anthropic)-[A-Za-z0-9-]+[ \t]*:.*{EOL}", LINE_FLAGS),
        re.compile(
            rf"{NOT_MID_WORD}[ \t]*(?:Claude|Anthropic)-[A-Za-z0-9-]+[ \t]*:[ \t]*{VALUE}",
            re.IGNORECASE,
        ),
    ),
    # The invented-name case. Any trailer at all whose key is one of the attribution verbs,
    # whatever it says: `Generated-By: an agent` names no model and still goes.
    Rule(
        "an authorship trailer",
        re.compile(rf"^[ \t]*{VERBS}[ \t]*:.*{EOL}", LINE_FLAGS),
        re.compile(rf"{NOT_MID_WORD}[ \t]*{VERBS}[ \t]*:[ \t]*{VALUE}", re.IGNORECASE),
    ),
    # The other invented-name case, from the value side. Any trailer whose value carries an
    # identity of ours, under a key nobody has thought of yet. LINE only, and only in the trailer
    # block: a body line reading `Default: claude-opus-5` is a sentence about a model.
    Rule(
        "a trailer naming Claude",
        re.compile(rf"^[ \t]*{KEY}[ \t]*:[ \t]*[^\n]*{IDENTITY}[^\n]*{EOL}", LINE_FLAGS),
        None,
        block_only=True,
    ),
    # The robot-and-"Generated with Claude Code" credit line, and every rewording of it.
    Rule(
        "a generated-with credit",
        re.compile(
            rf"^[ \t]*(?:{ROBOT}[ \t]*)?{CREDIT}[ \t]+(?:with|by)[ \t]+[^\n]*(?:claude|anthropic)"
            rf"[^\n]*{EOL}",
            LINE_FLAGS,
        ),
        re.compile(
            rf"{NOT_MID_WORD}[ \t]*(?:{ROBOT}[ \t]*)?{CREDIT}[ \t]+(?:with|by)[ \t]+"
            r"\[?(?:Claude|Anthropic)[^\]\n]*\]?(?:\([^)\n]*\))?",
            re.IGNORECASE,
        ),
    ),
    # A session or product link on its own, wherever it landed. Never prose: see IDENTITY.
    Rule(
        "a Claude link",
        None,
        re.compile(
            r"[ \t]*(?:\[[^\]\n]*\][ \t]*)?\(?https?://(?:[A-Za-z0-9_.-]*\.)?claude\.(?:ai|com)/\S*",
            re.IGNORECASE,
        ),
    ),
)

# A line that could belong to a trailer block. Git's own shape: a key, a colon, a value.
TRAILER_LINE = re.compile(r"[ \t]*[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*[ \t]*:")


def trailer_block(body: str) -> tuple[int, int] | None:
    """The character span of `body`'s trailer block, or None.

    Git calls the last paragraph a trailer block when every one of its lines is `Key: value`.
    """
    lines = body.split("\n")
    end = len(lines)
    while end > 0 and lines[end - 1].strip() == "":
        end -= 1
    start = end
    while start > 0 and lines[start - 1].strip() != "":
        start -= 1
    if start == end:
        return None
    if not all(TRAILER_LINE.match(line) for line in lines[start:end]):
        return None
    offset = sum(len(line) + 1 for line in lines[:start])
    return offset, offset + sum(len(line) + 1 for line in lines[start:end])


def strip(message: str) -> tuple[str, list[str]]:
    """`message` with every attribution removed, and the ids of the rules that fired.

    Pure: no file, no git, no process.
    """
    # The subject is never line-deleted. A summary saying `docs: the Co-Authored-By ban` is the
    # commit, not a trailer, and a rule that can delete line 1 can delete the whole message.
    brk = message.find("\n")
    subject = message if brk == -1 else message[: brk + 1]
    body = "" if brk == -1 else message[brk + 1 :]

    removed: list[str] = []
    for rule in RULES:
        if rule.line is None:
            continue
        if rule.block_only:
            span = trailer_block(body)
            if span is None:
                continue
            block = body[span[0] : span[1]]
            cleaned = rule.line.sub("", block)
            if cleaned != block:
                removed.append(rule.id)
                body = body[: span[0]] + cleaned + body[span[1] :]
            continue
        before = body
        body = rule.line.sub("", body)
        if body != before:
            removed.append(rule.id)

    out = subject + body
    for rule in RULES:
        if rule.inline is None:
            continue
        before = out
        out = rule.inline.sub("", out)
        if out != before and rule.id not in removed:
            removed.append(rule.id)

    if out == message:
        return message, []

    # Only when something went, so a clean message stays byte-identical.
    out = re.sub(r"[ \t]+(\r?\n)", r"\1", out)
    out = re.sub(r"(\r?\n){3,}", "\n\n", out)
    out = re.sub(r"\s+\Z", "", out) + "\n"
    return out, removed


def carries(message: str) -> str | None:
    """The id of the first rule that `strip` would apply to `message`, or None.

    The history check's question. `block_only` is honoured exactly as `strip` honours it.
    """
    brk = message.find("\n")
    body = "" if brk == -1 else message[brk + 1 :]
    for rule in RULES:
        if rule.block_only:
            span = trailer_block(body)
            if span and rule.line and rule.line.search(body[span[0] : span[1]]):
                return rule.id
            continue
        if (rule.line and rule.line.search(body)) or (rule.inline and rule.inline.search(message)):
            return rule.id
    return None


def strip_file(path: Path) -> list[str]:
    """Rewrite one commit-message file in place. Returns the ids of the rules that fired."""
    original = path.read_text(encoding="utf-8")
    message, removed = strip(original)
    if message != original:
        path.write_text(message, encoding="utf-8")
    return removed


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python3 strip_commit_trailers.py <commit-message-file>", file=sys.stderr)
        return 2
    removed = strip_file(Path(argv[0]))
    if removed:
        print(
            f"commit-msg: removed {', '.join(removed)}. No commit here names Claude as an author.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
