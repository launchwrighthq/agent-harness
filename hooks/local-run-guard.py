#!/usr/bin/env python3
"""Keeps tests, typechecks, lints and builds off this machine: they run on a remote runner.

Off by default. A project that has a remote runner turns it on with `harness.enable`.

On UserPromptSubmit it opens local runs for the turn when the user writes "verify locally", and
closes them on the next prompt that does not. On PreToolUse it refuses a runner such as pytest,
vitest, tsc or `make test` unless that turn is open. It refuses only a runner whose folder is in
this project: a `cd` or `pushd` moves the folder for the commands after it, and so does a folder
option such as `npm --prefix`, `uv --directory` or `make -C`. A linked worktree counts as its main
checkout. A folder that the hook cannot know, such as `cd "$DIR"`, counts as this project.
`make verify`, `npm run verify` and
`harness.gateCommand` of the config pass. So does each command of `harness.localRuns.dispatch`,
for example `ci test`: it dispatches to the remote runner or picks its target from the config.
With `--target local`, a dispatch command runs here, so it needs an open turn. The hint in the
refusal is `harness.localRuns.hint`.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import fresh, hookio  # noqa: E402

# "verify locally", "run the tests locally", "run it locally".
OPEN_PHRASE = re.compile(
    r"\b(?:verify|run|test|tests|build|lint|typecheck|check)\b[^.\n]{0,30}?"
    r"\blocally\b"
    r"|\blocal(?:ly)?\s+(?:run|test|verify)",
    re.IGNORECASE,
)
# Claude Code submits task notifications and command output through UserPromptSubmit too.
HARNESS_MARKERS = ("<task-notification>", "<command-name>", "<local-command-", "<system-reminder>")

RUNNERS = {"pytest", "py.test", "mypy", "vitest", "jest", "tsc", "eslint", "playwright"}
PYTHON_MODULES = {"pytest", "mypy"}
LAUNCHERS = {"npx", "bunx", "pnpx"}
NPM_LOCAL = re.compile(r"^(test|test:.*|verify:local|typecheck|lint|lint:.*|build|build:.*)$")
MAKE_LOCAL = {"test", "verify-local", "typecheck", "lint", "build"}
PREFIXES = {"timeout", "nice", "ionice", "time", "env", "command", "exec", "nohup", "stdbuf"}
KEYWORDS = {"do", "then", "else", "if", "while", "until", "!"}
OPERATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "{", "}"}
SHELLS = {"bash", "sh", "zsh"}
CHANGE_DIR = {"cd", "pushd"}
# The options that move a runner into another folder: `npm --prefix`, `uv --directory`, `make -C`.
DIR_OPTIONS = {"--directory", "--project", "--prefix", "--dir", "--cwd", "-C"}
# A folder word with one of these the shell expands, so the hook cannot know the folder.
UNKNOWN_DIR = re.compile(r"[$`*?\[]")


CONFIG = hookio.hook_config("local-run-guard", __file__)
LOCAL_RUNS = hookio.harness_value(CONFIG, "localRuns") or {}
HINT = LOCAL_RUNS.get("hint") or "the remote gate"
GATE = str(hookio.harness_value(CONFIG, "gateCommand") or "")
# Each dispatch command as its words, for example ["ci", "test"].
DISPATCH = [str(c).split() for c in LOCAL_RUNS.get("dispatch") or [] if str(c).split()]


def state_file(session: str) -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/claude-{os.getuid()}"
    return Path(base) / "local-run-guard" / re.sub(r"[^\w-]", "_", session)


def segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:  # unbalanced quotes: a plain split still finds the first word
        tokens = command.split()
    out: list[list[str]] = [[]]
    for token in tokens:
        if token in OPERATORS or set(token) <= set(";&|()"):
            out.append([])
        else:
            out[-1].append(token)
    return [seg for seg in out if seg]


def strip_prefix(seg: list[str]) -> list[str]:
    i = 0
    while i < len(seg):
        word = seg[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word) or word in KEYWORDS:
            i += 1
        elif os.path.basename(word) in PREFIXES:
            i += 1
            while i < len(seg) and (seg[i].startswith("-") or re.match(r"^\d+[smhd]?$", seg[i])):
                i += 1
        else:
            break
    return seg[i:]


def make_targets(args: list[str]) -> list[str]:
    targets, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg in {"-C", "-f", "-I", "-o", "-W"}:
            skip = True
        elif not arg.startswith("-") and "=" not in arg:
            targets.append(arg)
    return targets


def npm_script(args: list[str]) -> str | None:
    words = [a for a in args if not a.startswith("-")]
    for i, word in enumerate(args):
        if word == "--prefix":
            words = [a for a in args[:i] + args[i + 2 :] if not a.startswith("-")]
    if not words:
        return None
    if words[0] in {"test", "t", "tst"}:
        return "test"
    if words[0] in {"run", "run-script", "rum", "urn"} and len(words) > 1:
        return words[1]
    if words[0] in {"exec", "x"} and len(words) > 1:
        return f"exec:{words[1]}"
    return None


def without_dir_options(args: list[str]) -> list[str]:
    """`args` with each folder option and its value left out."""
    out, skip = [], False
    for i, arg in enumerate(args):
        if skip:
            skip = False
        elif arg in DIR_OPTIONS and i + 1 < len(args) and not args[i + 1].startswith("-"):
            skip = True
        elif not any(arg.startswith(f"{o}=") for o in DIR_OPTIONS if o.startswith("--")):
            out.append(arg)
    return out


def local_target(args: list[str]) -> bool:
    """True when the arguments hold `--target local` or `--target=local`."""
    return "--target=local" in args or ("--target", "local") in itertools.pairwise(args)


def dispatch_of(seg: list[str]) -> list[str] | None:
    """The dispatch command that this segment starts with, matched by the basename of its
    first word, or None."""
    if not seg:
        return None
    words = [os.path.basename(seg[0]), *seg[1:]]
    return next((d for d in DISPATCH if words[: len(d)] == d), None)


def local_run(seg: list[str]) -> str | None:
    """The runner this segment starts on this machine, or None when it starts none."""
    seg = strip_prefix(seg)
    if not seg:
        return None
    exe, args = os.path.basename(seg[0]), without_dir_options(seg[1:])
    if exe == "make" and "verify" in make_targets(args):
        return None
    # A dispatch command picks its target from the config, unless it names the local one.
    dispatch = dispatch_of(seg)
    if dispatch is not None:
        if local_target(seg[len(dispatch) :]):
            return f"{' '.join(dispatch)} --target local"
        return None
    flag = next((i for i, a in enumerate(args[:-1]) if re.match(r"^-[a-z]*c$", a)), None)
    if exe in SHELLS and flag is not None:
        return blocked(args[flag + 1])
    if exe in RUNNERS:
        return exe
    if exe == "vite" and "build" in args:
        return "vite build"
    if exe in LAUNCHERS or (
        exe in {"uv", "poetry", "pnpm", "yarn"} and args[:1] in (["run"], ["exec"])
    ):
        rest = args[1:] if exe not in LAUNCHERS else args
        lead = list(itertools.dropwhile(lambda a: a.startswith("-"), rest))
        if dispatch_of(lead) is not None:
            return local_run(lead)
        rest = [a for a in rest if not a.startswith("-")]
        return local_run(rest) if rest else None
    if re.match(r"^python[\d.]*$", exe) and "-m" in args[:-1]:
        module = args[args.index("-m") + 1]
        return module if module in PYTHON_MODULES else None
    if exe == "make":
        hit = [t for t in make_targets(args) if t in MAKE_LOCAL]
        return f"make {hit[0]}" if hit else None
    if exe == "npm":
        script = npm_script(args)
        if script is None or script in {"verify", "remote"}:
            return None
        if script.startswith("exec:"):
            return local_run([script[5:]])
        return f"npm run {script}" if NPM_LOCAL.match(script) else None
    return None


def blocked(command: str) -> str | None:
    return next(filter(None, (local_run(seg) for seg in segments(command))), None)


def folder(cwd: str | None, word: str) -> str | None:
    """The folder that `word` names from `cwd`, or None when the hook cannot know it."""
    if word == "-" or UNKNOWN_DIR.search(word):
        return None
    word = os.path.expanduser(word)
    if os.path.isabs(word):
        return word
    return None if cwd is None else os.path.join(cwd, word)


def run_dir(words: list[str], cwd: str | None) -> str | None:
    """The folder of a segment's runner: the value of a folder option, else `cwd`."""
    for i, word in enumerate(words[:-1]):
        if word in DIR_OPTIONS:
            return folder(cwd, words[i + 1])
    for word in words:
        name, _, value = word.partition("=")
        if value and name in DIR_OPTIONS:
            return folder(cwd, value)
    return cwd


def canonical(path: str) -> Path:
    """`path` with links resolved. A path in a linked worktree moves to its main checkout."""
    here = Path(os.path.realpath(path))
    for checkout in (here, *here.parents):
        if (checkout / ".git").exists():
            main = fresh.main_checkout(checkout)
            if main is None:
                return here
            return Path(os.path.realpath(main)) / here.relative_to(checkout)
    return here


PROJECT = canonical(str(CONFIG.root))


def in_project(path: str | None) -> bool:
    if path is None:
        return True
    here = canonical(path)
    return here == PROJECT or PROJECT in here.parents


def project_runner(command: str, cwd: str | None) -> str | None:
    """The first local runner of `command` whose folder is in this project, or None."""
    for seg in segments(command):
        words = strip_prefix(seg)
        if not words:
            continue
        if words[0] in CHANGE_DIR:
            target = next((w for w in words[1:] if w == "-" or not w.startswith("-")), "~")
            cwd = folder(cwd, target)
            continue
        flag = next((i for i, a in enumerate(words[1:-1], 1) if re.match(r"^-[a-z]*c$", a)), None)
        if os.path.basename(words[0]) in SHELLS and flag is not None:
            runner = project_runner(words[flag + 1], cwd)
        else:
            runner = local_run(seg)
            runner = runner if runner is not None and in_project(run_dir(words, cwd)) else None
        if runner is not None:
            return runner
    return None


def on_prompt(data: dict[str, object]) -> None:
    prompt, session = data.get("prompt"), str(data.get("session_id") or "")
    if not isinstance(prompt, str) or not session or any(m in prompt for m in HARNESS_MARKERS):
        return
    path = state_file(session)
    if OPEN_PHRASE.search(prompt):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)


def on_bash(data: dict[str, object]) -> int:
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return 0
    if GATE and command.strip() == GATE:
        return 0
    runner = project_runner(command, str(data.get("cwd") or os.getcwd()))
    if runner is None:
        return 0
    if state_file(str(data.get("session_id") or "")).exists():
        return 0
    print(
        f"local-run-guard: `{runner}` runs on the remote runner, never on this machine. "
        f"Use {HINT}. A local run is allowed only in a turn where the user asked for one "
        '("run the tests locally").',
        file=sys.stderr,
    )
    return 2


def main() -> int:
    data = json.load(sys.stdin)
    if not isinstance(data, dict):
        return 0
    if sys.argv[1:2] == ["UserPromptSubmit"]:
        on_prompt(data)
        return 0
    return on_bash(data)


if __name__ == "__main__":
    sys.exit(main())
