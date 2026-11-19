"""The env engine runs of a Bash command and what the env guards answer for each one.

`deny-env-reads` and `bash-read-guard` call `check_line`. It reads the command as shell steps
(`shell_steps`), finds each step that runs the env engine in command position, and gives the
asks and denies of those steps and the word spans that the credential name scan skips. The
rules are in docs/hooks.md, "The env engine exemption".
"""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness import config, shell_steps
from agent_harness.env.masking import public_key
from agent_harness.hookio import basename
from agent_harness.shell_steps import ShellSyntaxError, Step, Word

# The global options of the engine. Each takes one value and comes before the subcommand.
ENGINE_GLOBALS = ("--root", "--master", "--declarations")

# The first word after the env command and its global options: a subcommand, never a path.
SUBCOMMAND = re.compile(r"[a-z][a-z-]*")

# The subcommands that never print a value: key names, counts, file names, line numbers, layout
# findings or a short digest. `push` and `pull` print what the store tool prints, as they do
# alone. `get` and `run` have rules of their own.
QUIET_SUBCOMMANDS = frozenset(
    {
        *("keys", "status", "check", "where", "layout", "diff", "template", "init", "help"),
        *("set", "unset", "copy", "move", "rename", "merge", "slice", "push", "pull"),
    }
)

# A program that runs the words it gets as a command, a package runner included, so an engine
# run hides in its arguments.
WRAPPERS = frozenset(
    {
        *("builtin", "command", "doas", "env", "eval", "exec", "ionice", "nice", "nohup"),
        *("parallel", "setsid", "stdbuf", "sudo", "time", "timeout", "watch", "xargs"),
        *("npx", "npm", "pnpm", "yarn", "bunx", "uv", "uvx", "poetry", "pipx"),
    }
)
SHELLS = frozenset({"sh", "bash", "dash", "ash", "zsh", "ksh", "mksh", "fish", "csh", "tcsh"})

# The wrappers whose arguments are options, then one plain command. Each entry gives the options
# that take a value and the operands, such as the duration of `timeout`, before that command.
PLAIN_WRAPPERS: dict[str, tuple[frozenset[str], int]] = {
    "timeout": (frozenset({"-s", "--signal", "-k", "--kill-after"}), 1),
    "nice": (frozenset({"-n", "--adjustment"}), 0),
    "stdbuf": (frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}), 0),
    "env": (frozenset({"-u", "--unset"}), 0),
    "exec": (frozenset({"-a"}), 0),
    "npx": (frozenset({"-p", "--package"}), 0),
    **{name: (frozenset(), 0) for name in ("nohup", "time", "setsid", "command", "bunx")},
}
# The wrapper options that make the words after them more than one plain command.
NOT_PLAIN = frozenset({"-S", "--split-string", "-C", "--chdir", "-c", "--call"})
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")

# The shell operators and blanks that end a word in the fallback test.
WORD_END = re.compile(r"[\s;|&()<>`]+")


def _never(path: str) -> bool:
    return False


@dataclass(frozen=True)
class EnvScope:
    """What the checks run against: the project root, the folder the command runs in, the
    guard's test for a credential path, its test for a file whose `run` always asks (`held`),
    `[env] protected` and `[env] publicKeys`."""

    root: Path
    cwd: Path
    credential: Callable[[str], bool]
    held: Callable[[str], bool] = _never
    protected: frozenset[str] = frozenset()
    public: tuple[str, ...] = ()


def env_scope(
    cfg: config.Config,
    payload: Mapping[str, Any],
    credential: Callable[[str], bool],
    held: Callable[[str], bool] = _never,
) -> EnvScope:
    """The scope of one hook call: the root of `cfg` and the `cwd` of the payload."""
    cwd = payload.get("cwd")
    return EnvScope(
        cfg.root,
        Path(cwd) if isinstance(cwd, str) and cwd else cfg.root,
        credential,
        held,
        frozenset(_strings(cfg.get("env.protected"))),
        tuple(_strings(cfg.get("env.publicKeys"))),
    )


def _strings(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


@dataclass(frozen=True)
class Answer:
    decision: str
    reason: str


@dataclass
class EnvCheck:
    # The spans of the engine words that the credential name scan skips.
    exempt: list[tuple[int, int]] = field(default_factory=list)
    answers: list[Answer] = field(default_factory=list)

    def first(self, decision: str) -> Answer | None:
        return next((a for a in self.answers if a.decision == decision), None)


def check_line(command: str, commands: Iterable[str], scope: EnvScope) -> EnvCheck:
    """The answers and the exempt spans of every env engine run of `command`.

    A command that the parser cannot read gets no exemption, and it asks when its words hold
    an engine command anywhere.
    """
    check = EnvCheck()
    try:
        steps = shell_steps.parse(command)
    except ShellSyntaxError:
        if runs_env_engine(normalized_words(command), commands):
            check.answers.append(Answer("ask", reveal_reason("a key")))
        return check
    _Line(list(commands), scope, check, normalized_words(command)).steps(steps, nested=False)
    return check


def blank(command: str, spans: Iterable[tuple[int, int]]) -> str:
    """`command` with each span turned into blanks, its newlines kept."""
    chars = list(command)
    for start, end in spans:
        for at in range(start, end):
            if chars[at] != "\n":
                chars[at] = " "
    return "".join(chars)


def reveal_reason(key: str) -> str:
    return (
        f"This command prints the value of {key} into the agent context. Allow it only when you "
        "asked to see that value."
    )


def run_reason(file: str) -> str:
    return (
        f"ah env run gives the command every key of {file}, a held file or the file of a "
        "protected environment. Allow it only when you asked for this run."
    )


def run_place_reason(file: str) -> str:
    return (
        f"ah env run reads {file}, and the guard cannot tell where that file is: a `cd`, an "
        "expansion or a substitution comes first. Allow it only when the file is an env file of "
        "this project."
    )


def outside_reason(file: str) -> str:
    return (
        f"ah env run reads {file}, which is outside the project. Run it only on an env file of "
        "this project."
    )


class _Line:
    def __init__(
        self, commands: list[str], scope: EnvScope, check: EnvCheck, line_words: list[str]
    ) -> None:
        self.commands = commands
        self.prefixes = [c.split() for c in commands if c.strip()]
        self.scope = scope
        self.check = check
        self.line_words = line_words

    def ask(self, reason: str) -> None:
        self.check.answers.append(Answer("ask", reason))

    def steps(self, steps: list[Step], nested: bool) -> None:
        cwd: Path | None = self.scope.cwd
        for step in steps:
            cwd = follow_cd(step, cwd)
            self.step(step, nested, cwd)
            for inner in step.nested:
                self.steps(inner, nested=True)

    def step(self, step: Step, nested: bool, cwd: Path | None) -> None:
        if not step.words:
            return
        match = self.engine(step.words)
        if match is None:
            inner = unwrap(step.words)
            if inner is not None:
                self.step(dataclasses.replace(step, words=inner), nested, cwd)
            else:
                self.other(step)
            return
        size, own = match
        _EngineStep(self, step.words, size, own and not nested, nested, cwd).check()

    def engine(self, words: list[Word]) -> tuple[int, bool] | None:
        """The number of engine command words, and True for a configured form."""
        texts = [w.text for w in words]
        for prefix in self.prefixes:
            size = len(prefix)
            if texts[:size] == prefix and not any(dynamic(w) for w in words[:size]):
                return size, True
        if len(texts) >= 2 and basename(texts[0]) == "ah" and texts[1] == "env":
            return 2, False
        if (
            len(texts) >= 3
            and re.fullmatch(r"python3?(\.\d+)?", basename(texts[0]))
            and basename(texts[1]) == "ah"
            and texts[2] == "env"
        ):
            return 3, False
        return None

    def other(self, step: Step) -> None:
        """A step that runs no engine in command position: data, unless the engine can hide in
        it. A command word with an expansion, or a wrapper or shell given the engine words,
        asks. A shell reads its heredoc body as commands."""
        head = step.words[0]
        if dynamic(head):
            if "env" in self.line_words:
                self.ask(reveal_reason("a key"))
            return
        program = basename(head.text)
        if program not in WRAPPERS | SHELLS:
            return
        text = " ".join(w.text for w in step.words[1:])
        if runs_env_engine(normalized_words(text), self.commands):
            self.ask(reveal_reason("a key"))
            return
        if program not in SHELLS:
            return
        for heredoc in step.heredocs:
            try:
                body = shell_steps.parse(heredoc.body)
            except ShellSyntaxError:
                if runs_env_engine(normalized_words(heredoc.body), self.commands):
                    self.ask(reveal_reason("a key"))
                continue
            self.steps(body, nested=True)


class _EngineStep:
    """One step that runs the engine: its global options, its subcommand and its arguments."""

    def __init__(
        self, line: _Line, words: list[Word], size: int, own: bool, nested: bool, cwd: Path | None
    ) -> None:
        self.line = line
        self.words = words
        self.own = own
        self.nested = nested
        self.cwd = cwd
        rest = words[size:]
        at = 0
        self.options: list[tuple[Word, Word]] = []
        while at + 1 < len(rest) and rest[at].text in ENGINE_GLOBALS:
            self.options.append((rest[at], rest[at + 1]))
            at += 2
        self.sub = rest[at] if at < len(rest) else None
        self.args = rest[at + 1 :]

    def check(self) -> None:
        if any(w.expands for pair in self.options for w in pair) or (
            self.sub is not None and (self.sub.expands or self.sub.hidden)
        ):
            self.line.ask(reveal_reason("a key"))
            return
        if self.sub is None or SUBCOMMAND.fullmatch(self.sub.text) is None:
            if any("reveal" in w.text for w in self.words):
                self.line.ask(reveal_reason("a key"))
            return
        if self.sub.text == "get":
            self.get()
        elif self.sub.text == "run":
            self.run()
        elif self.sub.text in QUIET_SUBCOMMANDS:
            self.exempt(self.words)

    def exempt(self, words: list[Word]) -> None:
        if self.own and self.globals_in_scope():
            self.line.check.exempt.extend((w.start, w.end) for w in words)

    def globals_in_scope(self) -> bool:
        if not self.options:
            return True
        if self.cwd is None or any(dynamic(value) for _, value in self.options):
            return False
        pairs = [(option.text, value.text) for option, value in self.options]
        return globals_in_scope(pairs, dataclasses.replace(self.line.scope, cwd=self.cwd))

    def get(self) -> None:
        """A get prints a masked value unless a word is `--reveal` or can turn into it. A word
        whose expansions sit inside double quotes beside a literal `/` cannot."""
        if any(w.expands or (w.hidden and "/" not in w.literal) for w in self.args):
            self.line.ask(reveal_reason("a key"))
            return
        reveals = [w for w in self.args if "reveal" in w.text]
        if reveals:
            positionals = [w.text for w in self.args if not w.text.startswith("-")]
            key = positionals[0] if positionals else "a key"
            plain = all(not w.hidden for w in self.args)
            exact = all(w.text == "--reveal" for w in reveals)
            if not (plain and exact and public_key(key, self.line.scope.public)):
                self.line.ask(reveal_reason(key))
        self.exempt(self.words)

    def run(self) -> None:
        texts = [w.text for w in self.args]
        if "--" not in texts or texts.index("--") == len(texts) - 1:
            return
        cut = texts.index("--")
        head, inner = self.args[:cut], self.args[cut + 1 :]
        if any(w.expands or w.hidden for w in head) or any(dynamic(v) for _, v in self.options):
            self.line.ask(run_place_reason("a file that an expansion names"))
            return
        files = [
            w.text
            for i, w in enumerate(head)
            if w.text != "--require" and texts[i - 1 : i] != ["--require"]
        ]
        if not files:
            return
        file = files[0]
        if self.nested or self.cwd is None:
            self.line.ask(run_place_reason(file))
            return
        if not self.inside_project(file):
            self.line.check.answers.append(Answer("deny", outside_reason(file)))
            return
        name = basename(file)
        environment = name.removeprefix(".env.") if name.startswith(".env.") else ""
        if self.line.scope.held(file) or environment in self.line.scope.protected:
            self.line.ask(run_reason(file))
        if runs_env_engine(normalized_words(" ".join(w.text for w in inner)), self.line.commands):
            self.line.ask(reveal_reason("a key"))
        self.exempt(self.words[: self.words.index(self.args[cut]) + 1])

    def inside_project(self, file: str) -> bool:
        """The engine reads a relative file from `--root`, else from the checkout it runs in:
        the project, unless a `cd` of the line moved out of it."""
        assert self.cwd is not None
        project = Path(os.path.realpath(self.line.scope.root))
        here = Path(os.path.realpath(self.cwd))
        moved = self.cwd != self.line.scope.cwd
        roots = [value.text for option, value in self.options if option.text == "--root"]
        if roots:
            base = Path(os.path.realpath(here / roots[0]))
        else:
            base = here if moved and not here.is_relative_to(project) else project
        return Path(os.path.realpath(base / file)).is_relative_to(project)


def unwrap(words: list[Word]) -> list[Word] | None:
    """The words of the command that a plain wrapper runs, such as `lw env check` of
    `timeout 300 lw env check`. None for another program, or when a wrapper word expands."""
    program = basename(words[0].text)
    rule = PLAIN_WRAPPERS.get(program)
    if rule is None:
        return None
    takes_value, operands = rule
    at = 1
    while at < len(words) and words[at].text.startswith("-") and words[at].text != "--":
        name = words[at].text.split("=", 1)[0]
        if name in NOT_PLAIN:
            return None
        at += 2 if name in takes_value and "=" not in words[at].text else 1
    if at < len(words) and words[at].text == "--":
        at += 1
    if program == "env":
        while at < len(words) and ASSIGNMENT.fullmatch(words[at].text):
            at += 1
    at += operands
    if at >= len(words) or any(dynamic(w) for w in words[:at]):
        return None
    return words[at:]


def dynamic(word: Word) -> bool:
    """True for a word whose text the guard cannot know: an expansion or a substitution."""
    return word.hidden or "$" in word.text or "`" in word.text


def follow_cd(step: Step, cwd: Path | None) -> Path | None:
    """The folder after `step`: a `cd` to a literal path moves it, any other move loses it."""
    if not step.words or step.words[0].text not in ("cd", "pushd", "popd"):
        return cwd
    words = step.words
    if words[0].text != "cd" or len(words) != 2 or cwd is None:
        return None
    target = words[1]
    if target.expands or dynamic(target) or target.text.startswith("-"):
        return None
    return Path(os.path.normpath(cwd / target.text))


def globals_in_scope(options: list[tuple[str, str]], scope: EnvScope) -> bool:
    """True when the guard can vouch for every global option value.

    `--root` comes at most once and resolves to the project root or a folder inside it.
    `--master` and `--declarations` resolve inside the project, from the `--root` value as the
    engine reads them, and name no credential file. A value that starts with `-` or holds `$`, a
    backtick, a quote or a backslash fails.
    """
    if any(value.startswith("-") or re.search(r"[$`'\"\\]", value) for _, value in options):
        return False
    roots = [value for option, value in options if option == "--root"]
    if len(roots) > 1:
        return False
    project = Path(os.path.realpath(scope.root))
    # Without `--root`, the engine works in the git checkout of the folder it runs in.
    base = Path(os.path.realpath(scope.cwd / roots[0] if roots else scope.cwd))
    if not base.is_relative_to(project):
        return False
    base = base if roots else project
    for option, value in options:
        if option == "--root":
            continue
        if scope.credential(value):
            return False
        if not Path(os.path.realpath(base / value)).is_relative_to(project):
            return False
    return True


def normalized_words(command: str) -> list[str]:
    """The words of `command` with every quote and backslash removed, split on blanks and on the
    shell operators. `--rev''eal`, `"--reveal"` and `--rev\\eal` each give `--reveal`."""
    return [w for w in WORD_END.split(re.sub(r"['\"\\]", "", command)) if w]


def runs_env_engine(words: list[str], commands: Iterable[str]) -> bool:
    """True when the normalized `words` hold an env engine command: a word named `ah` followed
    by `env`, or the words of one of `commands`."""
    for at in range(len(words) - 1):
        if basename(words[at]) == "ah" and words[at + 1] == "env":
            return True
    for prefix in commands:
        need = normalized_words(prefix)
        if need and any(words[at : at + len(need)] == need for at in range(len(words))):
            return True
    return False
