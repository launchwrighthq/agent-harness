#!/usr/bin/env python3
"""UserPromptSubmit hook: add directives to the model's context for flags and goals in a prompt.

The output is `hookSpecificOutput.additionalContext`. The UserPromptSubmit output schema has no
`prompt` field, so a rewritten prompt printed as `{"prompt": ...}` reaches nothing.

The flags and their text come from `harness.directives` in the config.

The client runs a slash command only when the prompt BEGINS with it. `/lw-goal-spec` typed
further in asks for the skill here instead. `/goal` typed further in starts no goal at all.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import hookio  # noqa: E402

HOOK = "prompt-directives"

# Claude Code submits its own text through this event too: a background task's completion
# notification, a slash command's output, a system reminder. None of it was typed by anyone, so
# a `/lw-goal-spec` quoted inside an agent's report must not start a goal. The `lw-work-status`
# skill draws the same line over the transcript.
HARNESS_MARKERS = (
    "<task-notification>",
    "<command-name>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "<system-reminder>",
    "[SYSTEM NOTIFICATION",
)

# A slash or a word character before the slash means a path such as `skills/lw-goal-spec/`.
# Python's `\w` is a Unicode letter, digit or underscore, so a non-Latin word counts too.
# A quote mark or a backtick before it means the user quotes the command and asks for nothing.
NOT_A_COMMAND = r"(?<![\w/\"'`])"
GOAL_SPEC_RE = re.compile(NOT_A_COMMAND + r"/(?:lw-)?goal-spec(?![\w/-])")
# Not followed by `-`, so `/lw-goal-spec` never also reads as the built-in.
GOAL_RE = re.compile(NOT_A_COMMAND + r"/goal(?![\w/-])")

GOAL_WARNING = (
    "The user typed /goal inside a longer message, so no built-in goal is active. "
    "Say so in your first line, and offer /lw-goal-spec for spec work."
)


def has_flag(prompt: str, flag: str) -> bool:
    """A flag is a whole word: a hyphen and a letter, with whitespace or an edge on both sides."""
    return re.search(rf"(?:^|\s){re.escape(flag)}(?:\s|$)", prompt) is not None


def embedded(pattern: re.Pattern[str], prompt: str) -> list[re.Match[str]]:
    """The matches past the prompt's start, or none when the prompt begins with the command.

    At the start the client has already run the command, and a second note would run it twice.
    """
    lead = len(prompt) - len(prompt.lstrip())
    found = list(pattern.finditer(prompt))
    if any(match.start() == lead for match in found):
        return []
    return found


def goal_notes(prompt: str) -> list[str]:
    notes: list[str] = []
    spec = embedded(GOAL_SPEC_RE, prompt)
    if spec:
        rest = prompt[spec[0].end() :]
        args = rest.split("\n", 1)[0].strip()
        tail = "with no arguments." if args == "" else f"with these arguments: {args}"
        notes.append(
            "The user asked for /lw-goal-spec inside the message. "
            f"Run the lw-goal-spec skill now {tail}"
        )
    if embedded(GOAL_RE, prompt):
        notes.append(GOAL_WARNING)
    return notes


def from_harness(prompt: str) -> bool:
    """Text the client submitted rather than a person: a notification quoting a command."""
    return any(marker in prompt for marker in HARNESS_MARKERS)


def notes_for(prompt: object, directives: dict[str, str]) -> list[str]:
    if not isinstance(prompt, str) or prompt.strip() == "" or from_harness(prompt):
        return []
    flags = [text for flag, text in directives.items() if has_flag(prompt, flag)]
    return [*flags, *goal_notes(prompt)]


def main() -> None:
    payload = hookio.read_payload(HOOK)
    if payload is None:
        return
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or prompt.strip() == "" or from_harness(prompt):
        return
    configured = hookio.harness_value(hookio.hook_config(HOOK, __file__), "directives")
    directives = (
        {str(k): str(v) for k, v in configured.items()} if isinstance(configured, dict) else {}
    )
    notes = notes_for(prompt, directives)
    if notes:
        hookio.context("UserPromptSubmit", "\n\n".join(notes))


if __name__ == "__main__":
    main()
