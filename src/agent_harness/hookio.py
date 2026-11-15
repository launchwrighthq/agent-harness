"""Shared by the hook scripts: the payload, the deny answer, file name patterns and the config.

A hook script computes ROOT, the folder above its own `hooks/` folder, puts ROOT/src on
`sys.path` and imports this module. The same hook file then works in the source tree and in an
installed copy at `<project>/.claude/agent-harness/hooks/`.

The project of a hook is the checkout that holds the hook file, never a folder that the payload
names. The hook reads `[harness]` of that project's `.agents/harness.toml`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_harness import HarnessError, config

# The folder of the installed hooks, relative to the project root.
INSTALLED_HOOKS = (".claude", "agent-harness", "hooks")

# The env engine command forms that every project accepts, beside `harness.envCommand`.
ENGINE_COMMANDS = (
    "python3 .claude/agent-harness/bin/ah env",
    "python3 ~/.claude/agent-harness/bin/ah env",
    "ah env",
)

# The only characters that a lone env engine run holds after the engine command, outside single
# quotes. Each other character can start a second command (`;`, `|`, `&`, a newline), expand
# (`$`, a backtick, `*`, `?`, `[`, `{`, `~`) or hide where a quote ends (`"`, `\`, `'`). Bash
# reads single-quoted text literally, so the test removes it first.
PLAIN_ARGUMENTS = re.compile(r"[A-Za-z0-9_./:=,@%+ \t-]*")

# The first word after the env command and its global options: a subcommand such as `get` or
# `set`, never a path.
SUBCOMMAND = re.compile(r"[a-z][a-z-]*")

# The global options of the engine. Each takes one value and comes before the subcommand.
ENGINE_GLOBALS = ("--root", "--master", "--declarations")

# A character outside single quotes that can make a word that the guard does not see, such as
# `--reveal`: an expansion, a substitution, a glob or a brace group.
HIDDEN_WORD = re.compile(r"[$`*?\[{]")

# The shell operators and blanks that end a word.
WORD_END = re.compile(r"[\s;|&()<>`]+")


def project_root(hook_file: str | Path) -> Path:
    """The checkout that holds `hook_file`.

    An installed hook sits three levels below the project root. A copy outside the installed
    layout (the package source) takes the top of the git checkout it sits in.
    """
    here = Path(os.path.abspath(hook_file)).parent
    if here.parts[-3:] == INSTALLED_HOOKS:
        return here.parents[2]
    return config.find_root(here)


def hook_config(hook: str, hook_file: str | Path) -> config.Config:
    """The config of the project that holds `hook_file`, or the defaults when it is not TOML.

    The reason goes to stderr, which the client shows as a hook error, so a broken config file
    is reported and the hook still guards with its defaults.
    """
    root = project_root(hook_file)
    try:
        return config.load(root)
    except HarnessError as error:
        print(
            f"{hook}: cannot read .agents/harness.toml ({error}); using the defaults",
            file=sys.stderr,
        )
        return config.Config(root, {}, None)


def harness_value(cfg: config.Config, key: str) -> Any:
    """The value of `harness.<key>`. Every key of `[harness]` has a default."""
    return cfg.get(f"harness.{key}")


def read_payload(hook: str) -> dict[str, Any] | None:
    """The payload object on stdin, or None. A payload that is not JSON says why on stderr."""
    try:
        payload = json.load(sys.stdin)
    except ValueError as error:
        print(f"{hook}: the payload is not JSON ({error}); no verdict", file=sys.stderr)
        return None
    return payload if isinstance(payload, dict) else None


def deny(reason: str) -> None:
    """Print a PreToolUse deny.

    An `allow` would bypass the permission rules, so a guard denies, asks or stays silent.
    """
    decide("deny", reason)


def ask(reason: str) -> None:
    """Print a PreToolUse ask. The client shows `reason`, and the user allows or refuses it."""
    decide("ask", reason)


def decide(decision: str, reason: str) -> None:
    """Print the PreToolUse answer `decision` with `reason`, compact."""
    answer = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(answer, separators=(",", ":"), ensure_ascii=False))


def context(event: str, text: str) -> None:
    """Print added context for the model on `event`."""
    answer = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    print(json.dumps(answer, separators=(",", ":"), ensure_ascii=False))


def name_pattern(pattern: str) -> re.Pattern[str]:
    """A file name pattern (`*` and `?` inside one name) as a regex over a whole base name.

    Match it with `fullmatch`. The match ignores case.
    """
    body = "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in pattern)
    return re.compile(body, re.IGNORECASE)


def patterns(entries: Any, hook: str) -> list[dict[str, str]]:
    """The `{pattern, hint}` entries of a config list, from strings or tables.

    An entry of another shape is left out, and the reason goes to stderr.
    """
    if not isinstance(entries, list):
        return []
    out: list[dict[str, str]] = []
    for entry in entries:
        if isinstance(entry, str) and entry:
            out.append({"pattern": entry})
        elif isinstance(entry, Mapping) and isinstance(entry.get("pattern"), str):
            out.append({str(k): str(v) for k, v in entry.items()})
        else:
            print(
                f"{hook}: the config entry {entry!r} is no file pattern; it is left out",
                file=sys.stderr,
            )
    return out


def basename(path: str) -> str:
    """The last segment of `path`, trailing slashes ignored, as Node's `path.basename` gives it."""
    return path.rstrip("/").rsplit("/", 1)[-1]


def env_commands(cfg: config.Config) -> list[str]:
    """Every command form of the env engine: the fixed forms, the launcher of the project by its
    absolute path, and `harness.envCommand`."""
    configured = harness_value(cfg, "envCommand")
    extra = [configured.strip()] if isinstance(configured, str) and configured.strip() else []
    own = f"python3 {cfg.root.resolve()}/.claude/agent-harness/bin/ah env"
    return [*ENGINE_COMMANDS, own, *extra]


def _never(path: str) -> bool:
    return False


@dataclass(frozen=True)
class EnvScope:
    """What a guard checks a lone engine run against: the project root, the folder the command
    runs in, the guard's own test for a credential path, its test for a file whose `run` always
    asks (`held`), `[env] runCommands` and `[env] protected`."""

    root: Path
    cwd: Path
    credential: Callable[[str], bool]
    held: Callable[[str], bool] = _never
    run_commands: frozenset[str] = frozenset()
    protected: frozenset[str] = frozenset()


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
        _names(cfg.get("env.runCommands")),
        _names(cfg.get("env.protected")),
    )


def _names(value: Any) -> frozenset[str]:
    return (
        frozenset(v for v in value if isinstance(v, str))
        if isinstance(value, list)
        else frozenset()
    )


def lone_env_command(command: str, commands: Iterable[str], scope: EnvScope | None = None) -> bool:
    """True when `command` is one run of the env engine and nothing else.

    The command starts with one of `commands`, a space and a subcommand. After the engine
    command, outside single quotes, only the characters of `PLAIN_ARGUMENTS` may follow. Any
    other character voids the exemption: a second command, an expansion, a glob, a brace, a `~`,
    a double quote or a backslash. Single-quoted text passes, because bash reads it literally.
    A global option before the subcommand passes only when `scope` accepts its value (see
    `globals_in_scope`).
    """
    return env_arguments(command, commands, scope) is not None


def env_arguments(
    command: str, commands: Iterable[str], scope: EnvScope | None = None
) -> list[str] | None:
    """The words from the subcommand on when `command` is one lone run of the env engine (see
    `lone_env_command`), single quotes removed as bash removes them. None otherwise."""
    parts = _lone_parts(command, commands, scope)
    return None if parts is None else parts[1]


def _lone_parts(
    command: str, commands: Iterable[str], scope: EnvScope | None
) -> tuple[list[tuple[str, str]], list[str]] | None:
    """The global options and the words from the subcommand on of a lone engine run."""
    trimmed = command.strip()
    for prefix in commands:
        if prefix and trimmed.startswith(f"{prefix} "):
            rest = trimmed[len(prefix) :]
            if not PLAIN_ARGUMENTS.fullmatch(re.sub(r"'[^']*'", "", rest)):
                return None
            # Only plain text and closed single quotes are left, so shlex splits the words as
            # bash does.
            words = shlex.split(rest)
            options: list[tuple[str, str]] = []
            while len(words) > 1 and words[0] in ENGINE_GLOBALS:
                options.append((words[0], words[1]))
                words = words[2:]
            if options and (scope is None or not globals_in_scope(options, scope)):
                return None
            if not words or SUBCOMMAND.fullmatch(words[0]) is None:
                return None
            return options, words
    return None


def globals_in_scope(options: list[tuple[str, str]], scope: EnvScope) -> bool:
    """True when the hook can vouch for every global option value.

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


def env_run_command(
    command: str, commands: Iterable[str], scope: EnvScope | None = None
) -> str | None:
    """The command that a lone `run` of the env engine starts, the words after `--`, or None.

    The engine puts the keys of an env file in that command's environment, so a guard checks it
    as any other command.
    """
    words = env_arguments(command, commands, scope)
    if not words or words[0] != "run" or "--" not in words:
        return None
    inner = words[words.index("--") + 1 :]
    return shlex.join(inner) if inner else None


# A shell, an interpreter or a wrapper runs any code, so it can print the environment of a
# `run` even when `[env] runCommands` lists it.
RUN_BLOCKED = {
    *("sh", "bash", "zsh", "dash", "ksh", "fish", "python", "python3", "node", "deno", "bun"),
    *("perl", "ruby", "php", "env", "printenv", "xargs", "command", "exec", "nice", "sudo"),
    "timeout",
}
# Options that hand a program code to run.
CODE_OPTIONS = {"-c", "-e", "-p", "--eval"}


def env_run_verdict(
    command: str, commands: Iterable[str], scope: EnvScope
) -> tuple[str, str] | None:
    """`("deny", file)`, `("ask", file)` or None for a lone `run` of the env engine.

    A file outside the project is denied. A file that the guard holds (`scope.held`), a file of
    an environment in `[env] protected`, and a command that `run_allowed` refuses ask, so the
    user confirms. The test fails closed: it never parses the command further.
    """
    parts = _lone_parts(command, commands, scope)
    if parts is None or parts[1][0] != "run" or "--" not in parts[1]:
        return None
    options, words = parts
    at = words.index("--")
    head = words[1:at]
    positionals = [
        w for i, w in enumerate(head) if w != "--require" and head[i - 1 : i] != ["--require"]
    ]
    if not positionals:
        return None
    file = positionals[0]
    project = Path(os.path.realpath(scope.root))
    roots = [value for option, value in options if option == "--root"]
    base = Path(os.path.realpath(scope.cwd / roots[0])) if roots else project
    if not Path(os.path.realpath(base / file)).is_relative_to(project):
        return ("deny", file)
    name = basename(file)
    environment = name.removeprefix(".env.") if name.startswith(".env.") else ""
    if scope.held(file) or environment in scope.protected:
        return ("ask", file)
    return None if run_allowed(words[at + 1 :], scope.run_commands) else ("ask", file)


def run_allowed(inner: list[str], allowed: frozenset[str]) -> bool:
    """True for a bare program of `allowed` that is no shell, interpreter or wrapper, with no
    word that can expand, read `/proc` or hand it code."""
    if not inner or "/" in inner[0] or inner[0] not in allowed or inner[0] in RUN_BLOCKED:
        return False
    return not any(
        re.search(r"[$`*?\[]", word) or "/proc" in word or word in CODE_OPTIONS for word in inner
    )


def answer_env_run(verdict: tuple[str, str]) -> None:
    """Print the deny or the ask of an `env_run_verdict`."""
    kind, file = verdict
    if kind == "deny":
        deny(
            f"ah env run reads {file}, which is outside the project. Run it only on an env file "
            "of this project."
        )
    else:
        ask_reveal(f"every key of {file}")


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


def env_reveal(command: str, commands: Iterable[str], scope: EnvScope | None = None) -> str | None:
    """The key that a run of the env engine may print with a reveal, or None.

    A reveal puts a value in the agent context, so the guards ask the user to confirm each one.
    The test fails closed. It counts a reveal when the command runs the env engine and either a
    normalized word holds `reveal`, or a character outside single quotes can make a hidden word
    (`$`, a backtick, `*`, `?`, `[`, `{`). The answer is the key after `get` of a lone run, else
    `a key`.
    """
    words = normalized_words(command)
    if not runs_env_engine(words, commands):
        return None
    hidden = HIDDEN_WORD.search(re.sub(r"'[^']*'", "", command)) is not None
    if not hidden and not any("reveal" in word for word in words):
        return None
    lone = env_arguments(command, commands, scope)
    rest = [w for w in (lone or [])[1:] if "reveal" not in w]
    return rest[0] if lone and lone[0] == "get" and rest else "a key"


# The subcommands of the engine that never print a value: they print key names, counts, file
# names, line numbers, layout findings or a short digest. `get`, `run`, `push` and `pull` are
# not here: `get --reveal` prints a value, `run` starts a command with the values in its
# environment, and a store command can echo what its tool prints.
QUIET_SUBCOMMANDS = frozenset(
    {
        *("keys", "status", "check", "where", "layout", "diff", "template", "init"),
        *("set", "unset", "copy", "move", "rename", "merge", "slice"),
    }
)


def engine_subcommands(words: list[str], commands: Iterable[str]) -> list[str]:
    """The word after each env engine command and its global options in the normalized
    `words`: the subcommand of each engine run of the line."""
    starts: list[int] = []
    for at in range(len(words) - 1):
        if basename(words[at]) == "ah" and words[at + 1] == "env":
            starts.append(at + 2)
    for prefix in commands:
        need = normalized_words(prefix)
        for at in range(len(words)):
            if need and words[at : at + len(need)] == need:
                starts.append(at + len(need))
    found: list[str] = []
    for at in sorted(set(starts)):
        while at + 1 < len(words) and words[at] in ENGINE_GLOBALS:
            at += 2
        found.append(words[at] if at < len(words) else "")
    return found


def ask_env(command: str, commands: Iterable[str], key: str) -> None:
    """Print the PreToolUse ask for an `env_reveal` answer, with the reason that applies.

    When every engine run of the line has a subcommand of `QUIET_SUBCOMMANDS` and no word holds
    `reveal`, the ask comes from an expansion elsewhere in the line, so the reason says that.
    """
    words = normalized_words(command)
    subcommands = engine_subcommands(words, commands)
    quiet = bool(subcommands) and all(sub in QUIET_SUBCOMMANDS for sub in subcommands)
    if not quiet or any("reveal" in word for word in words):
        ask_reveal(key)
        return
    shown = ", ".join(f"`env {sub}`" for sub in dict.fromkeys(subcommands))
    ask(
        f"The env engine prints no value for {shown}. The env command does not run alone, so "
        "the guard cannot clear the whole line: another step holds a `$`, a backtick, a glob "
        "or a brace. Run the env command alone in its own Bash call, then the other steps."
    )


def ask_reveal(key: str) -> None:
    """Print the PreToolUse ask for a `--reveal` of `key`."""
    ask(
        f"This command prints the value of {key} into the agent context. Allow it only when you "
        "asked to see that value."
    )
