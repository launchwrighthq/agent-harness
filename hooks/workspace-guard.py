#!/usr/bin/env python3
"""Refuse Git mutations that overwrite another session's checkout or shared state.

Reads `harness.landCommand`, `harness.workspace.worktreesOnly` and
`harness.workspace.gitConfigAllow` from `.agents/harness.toml` of the project that holds this
file. With `worktreesOnly`, a mutation in the main checkout is refused too, for projects where
every session works in its own worktree.

The guard stops accidents by cooperating agents. It is not a sandbox. It does not follow every
shell trick case by case: a command that involves git must have one of a few simple shapes, which
the guard reads completely, and every other form is refused with one message that names them.

    cd <literal path> && LANG=C git -C <literal path> <literal args> | head -5; echo "rc=$?"

A reader step (`echo`, `ls`, `grep`, `diff`, ...) writes no file and no variable, so it may stand
beside git.

The project root is the checkout that holds this file, never a directory that the payload names.
"""

from __future__ import annotations

import fnmatch
import functools
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import hookio  # noqa: E402

READ_ONLY = {
    "status",
    "diff",
    "log",
    "show",
    "rev-parse",
    "ls-files",
    "ls-tree",
    "grep",
    "merge-base",
    "show-ref",
    "check-ignore",
    "check-attr",
    "describe",
    "cat-file",
    "for-each-ref",
    "blame",
    "version",
    "help",
}
# Flags that make a git subcommand run a program or write a file, per subcommand. A long flag also
# matches its abbreviation and its `=value` form. A short flag also matches inside a bundle such
# as `-qu`, and with an attached value such as `-ux`.
REFUSED_FLAGS = {
    "fetch": ("--upload-pack", "--exec", "-u"),
    "pull": ("--upload-pack", "--exec", "-u"),
    "ls-remote": ("--upload-pack", "--exec", "-u"),
    "clone": ("--upload-pack", "-u"),
    "grep": ("--open-files-in-pager", "-O"),
    "log": ("--output", "--ext-diff"),
    "diff": ("--output", "--ext-diff"),
    "show": ("--output", "--ext-diff"),
    "whatchanged": ("--output", "--ext-diff"),
}
# The `git worktree` commands that change one worktree. Each may name only the caller's own.
WORKTREE_CHANGES = {"remove", "move", "lock", "unlock", "repair"}
# The name of a new worktree folder.
WORKTREE_NAME = re.compile(r"[A-Za-z0-9._-]+")
# The `git config` options that read, that write, and that take a value.
CONFIG_READS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list"}
CONFIG_WRITES = (
    "--add",
    "--unset",
    "--unset-all",
    "--replace-all",
    "--rename-section",
    "--remove-section",
    "--edit",
)
CONFIG_VALUES = ("--file", "--blob", "--type", "--default", "--comment")
# A config key: a section, an optional subsection and a name. Since git 2.46, a word with no `.`
# can be a subcommand such as `edit` or `unset`.
CONFIG_KEY = re.compile(r"^[A-Za-z][\w-]*(\.[^=\s]+)?\.[\w-]+$")
# The `git clone` flags that are refused outright. `clone_refusal` checks `-c` and `--config`.
CLONE_REFUSED = ("--template", "--separate-git-dir", "--reference", "--reference-if-able")
# The `git remote` forms that only read: no argument, `-v`, `show [name]` and `get-url <name>`.
REMOTE_READS = re.compile(
    r"^((-v|--verbose)|show( -n)?( [^-\s]\S*)?|get-url( --push| --all)* [^-\s]\S*)?$"
)
# The keys `git -c` may set when `workspace.gitConfigAllow` is not set. A `*` matches any rest of
# the key. Git compares section and key names without case, so both sides are lowercased. `diff`
# and `format` are named key by key: `diff.orderFile` and `format.signatureFile` read a file.
DEFAULT_CONFIG_ALLOW = (
    "user.name",
    "user.email",
    "color.*",
    "core.pager",
    "core.quotepath",
    "advice.*",
    "diff.algorithm",
    "diff.colormoved",
    "diff.colormovedws",
    "diff.context",
    "diff.indentheuristic",
    "diff.interhunkcontext",
    "diff.mnemonicprefix",
    "diff.noprefix",
    "diff.relative",
    "diff.renamelimit",
    "diff.renames",
    "diff.statgraphwidth",
    "log.*",
    "format.pretty",
    "format.numbered",
    "format.subjectprefix",
    "format.signoff",
    "merge.conflictstyle",
    "pull.rebase",
    "init.defaultbranch",
)
# Keys that run a program. No allowlist pattern admits them: `diff.external=git stash` would turn
# the read-only `git diff` into a stash.
COMMAND_KEY = re.compile(r"^diff\.external$|\.(command|textconv|cmd)$")
# The pagers `core.pager` may name: a pager is a program too.
SAFE_PAGER = re.compile(r"^(cat|more|less(\s+-[A-Za-z]+)*)?$")
# The only variables that may stand in front of git, plus `LC_*`.
ENV_ALLOW = frozenset(
    {
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
        "LANG",
        "TZ",
        "NO_COLOR",
        "TERM",
        "GIT_TERMINAL_PROMPT",
    }
)
LOCALE = re.compile(r"^LC_[A-Z_]+$")
# Characters that split a de-quoted word into the pieces a shell script could run.
PIECE = re.compile(r"[\s;&|()<>=`]+")
ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)\+?=")
# A word bash expands to other words: a glob, or a brace list or range.
EXPANDS = re.compile(r"[*?\[]|\{[^{}]*(,|\.\.)[^{}]*\}")
# Programs that run the script text they are given. A word with a piece `git` counts as git when
# one of them is in the command, and each later word is read as a command of its own.
SCRIPT_TAKERS = {"bash", "sh", "dash", "zsh", "ksh", "fish", "eval", "watch"}
# Programs that run the command named in their arguments.
RUNNERS = SCRIPT_TAKERS | {
    "env",
    "xargs",
    "nice",
    "timeout",
    "sudo",
    "doas",
    "nohup",
    "command",
    "exec",
    "builtin",
    "time",
    "stdbuf",
    "ionice",
    "setsid",
    "chroot",
    "flock",
    "strace",
    "nsenter",
    "unshare",
    "su",
    "runuser",
    "parallel",
}
# Words that open or close a compound command.
RESERVED = {
    "if",
    "then",
    "elif",
    "else",
    "fi",
    "do",
    "done",
    "while",
    "until",
    "for",
    "in",
    "case",
    "esac",
    "select",
    "function",
    "coproc",
    "time",
    "!",
    "{",
    "}",
    "[[",
    "]]",
}
# The programs the output of git may be piped into.
FILTERS = {"head", "tail", "grep", "rg", "wc", "sort", "uniq", "cat", "less", "jq", "sed", "awk"}
# The programs that may run as a step of their own beside git. They read and print, and write no
# file and no variable, so they cannot change what a later git step runs.
READERS = FILTERS | {
    "echo",
    "printf",
    "ls",
    "pwd",
    "test",
    "[",
    "true",
    "false",
    "md5sum",
    "sha1sum",
    "sha256sum",
    "cmp",
    "diff",
    "stat",
    "du",
    "basename",
    "dirname",
    "realpath",
    "readlink",
    "cut",
    "tr",
}
# The exit status of the step before. It expands to a number, so a reader may print it.
STATUS = "$?"
# The `sed -n` scripts allowed: print a line range or the lines that match a pattern.
SED_SCRIPT = re.compile(r"^((\d+|\$)(,(\d+|\$))?|/[^/\n]*/(,/[^/\n]*/)?)p$")
# Text in an awk program that runs a program, writes a file or loads code.
AWK_UNSAFE = re.compile(r"system|[|>]|@load|@include")
# Shell operators, longest first.
OPERATORS = (
    "&>>",
    "<<<",
    "<<-",
    "&&",
    "||",
    ";;",
    ";&",
    "|&",
    "&>",
    "<<",
    "<>",
    "<&",
    ">>",
    ">&",
    ">|",
    ";",
    "&",
    "|",
    "(",
    ")",
    "<",
    ">",
    "\n",
)
# Operators that join two steps of an allowed command.
JOINERS = {"&&", ";", "\n"}
# Operators that end a simple command. Every other operator is a redirection.
SEPARATORS = JOINERS | {"||", ";;", ";&", "&", "|", "|&", "(", ")"}
# A heredoc delimiter: quoted, backslashed or plain.
DELIMITER = re.compile(r"""[ \t]*(?:'([^'\n]*)'|"([^"\n]*)"|(\\?)([A-Za-z0-9_.\-]+))""")
# `$` starts an expansion only before one of these.
EXPANSION_START = re.compile(r"[A-Za-z0-9_{(@*#?$!-]")

SHAPES = (
    "A command that involves git may hold only these steps, joined by `&&`, `;` or a newline: "
    "`cd <literal path>`, `git <literal arguments>` (with `-C <literal path>`, and allowlisted "
    "variables such as `LANG=C` in front), and a reader step such as `echo`, `ls`, `grep`, "
    "`diff` or `md5sum` with literal arguments and `$?`. Each step may pipe into `head`, `tail`, "
    "`grep`, `rg`, `wc`, `sort`, `uniq`, `cat`, `less`, `jq`, `sed -n` or `awk`. Runners (`env`, "
    "`xargs`, `timeout`, `sudo`, `bash -c`, `eval`, ...), other programs, compound commands "
    "(`if`, `for`, `{ }`), subshells, functions, `$( )`, backticks, variables, globs, `||`, `&` "
    "and redirection to a file are refused. Send a commit message with "
    "`git commit -F - <<'EOF'`, and run every other command in a Bash call of its own."
)


class Refused(ValueError):
    """A git command this guard refuses. Its message is the whole reason."""


class ParseError(ValueError):
    """A command the tokenizer cannot read."""


def shape(detail: str) -> Refused:
    return Refused(f"workspace-guard refused {detail} in a command that involves git. {SHAPES}")


CONFIG = hookio.hook_config("workspace-guard", __file__)
LAND = hookio.harness_value(CONFIG, "landCommand") or "the land command"
WORKSPACE = hookio.harness_value(CONFIG, "workspace") or {}
WORKTREES_ONLY = bool(WORKSPACE.get("worktreesOnly"))
CONFIG_ALLOW = tuple(
    str(p).lower()
    for p in (
        WORKSPACE["gitConfigAllow"]
        if isinstance(WORKSPACE.get("gitConfigAllow"), list)
        else DEFAULT_CONFIG_ALLOW
    )
)


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    return done.stdout.strip()


def checkout(cwd: Path) -> Path:
    return Path(git(cwd, "rev-parse", "--show-toplevel")).resolve()


def project_root() -> Path:
    """The checkout that holds this hook: three levels above `.claude/agent-harness/hooks/`.

    `CLAUDE_PROJECT_DIR` and the payload cannot move it. A copy outside the installed layout
    (the package source) takes the top of the Git checkout it sits in.
    """
    here = Path(os.path.abspath(__file__)).parent
    if here.parts[-3:] == hookio.INSTALLED_HOOKS:
        return here.parents[2].resolve()
    return checkout(here)


# --- Tokens ----------------------------------------------------------------------------------


@dataclass
class Token:
    """A word (`op` is None) or an operator. A heredoc operator holds its delimiter as `text`."""

    op: str | None = None
    # The word as bash reads it, quotes removed, expansions left as written.
    text: str = ""
    # The unquoted characters of the word; each quoted character is a NUL.
    bare: str = ""
    # True when bash substitutes part of the word: `$`, a backtick, `$( )`, `${ }`, `$'...'`. For
    # a heredoc: an unquoted delimiter and a body with `$` or a backtick.
    dynamic: bool = False
    # A heredoc body as written. A shell that reads it runs it as a script.
    body: str = ""


def literal(word: Token) -> bool:
    """True for a word bash passes on as written: no expansion, glob, brace list or leading `~`."""
    return not word.dynamic and not EXPANDS.search(word.bare) and not word.bare.startswith("~")


def computed(word: Token) -> bool:
    """True for a command word whose program the guard cannot read: `$cmd`, a glob, a brace list."""
    return word.dynamic or (bool(EXPANDS.search(word.bare)) and word.text not in ("[", "[["))


def closing(text: str, start: int, open_: str, close: str) -> int:
    """The index of the `close` that matches `text[start]`, skipping quotes and escapes."""
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char in "'\"":
            end = i + 1
            while end < len(text) and text[end] != char:
                end += 2 if char == '"' and text[end] == "\\" else 1
            i = end + 1
            continue
        if char == open_:
            depth += 1
        elif char == close:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ParseError(f"an unclosed `{open_}`")


def parse(command: str) -> tuple[list[Token], list[str]]:
    """The tokens of the command, and the text of each substitution in it.

    Quotes, backslashes, `$( )`, `${ }`, backticks, `<( )`, comments and heredoc bodies are read
    as bash reads them. A substitution stays inside its word. Raises ParseError on an unclosed
    quote or substitution.
    """
    tokens: list[Token] = []
    subs: list[str] = []
    pending: list[tuple[Token, str, bool, bool]] = []
    n = len(command)
    word: Token | None = None

    def put(text: str, quoted: bool = False, dynamic: bool = False) -> None:
        nonlocal word
        if word is None:
            word = Token()
        word.text += text
        word.bare += "\0" * len(text) if quoted else text
        word.dynamic |= dynamic

    def flush() -> None:
        nonlocal word
        if word is not None:
            tokens.append(word)
            word = None

    def dollar(i: int) -> int:
        following = command[i + 1 : i + 2]
        if following in ("(", "{"):
            end = closing(command, i + 1, following, ")" if following == "(" else "}")
            subs.append(command[i + 2 : end])
            put(command[i : end + 1], dynamic=True)
            return end + 1
        put("$", dynamic=bool(EXPANSION_START.match(following)))
        return i + 1

    def backtick(i: int) -> int:
        end = i + 1
        while end < n and command[end] != "`":
            end += 2 if command[end] == "\\" else 1
        if end >= n:
            raise ParseError("an unclosed backtick")
        subs.append(command[i + 1 : end])
        put(command[i : end + 1], dynamic=True)
        return end + 1

    def double(i: int) -> int:
        put("", quoted=True)
        while i < n:
            char = command[i]
            if char == '"':
                return i + 1
            if char == "\\" and i + 1 < n and command[i + 1] in '$`"\\\n':
                if command[i + 1] != "\n":
                    put(command[i + 1], quoted=True)
                i += 2
            elif char == "$":
                i = dollar(i)
            elif char == "`":
                i = backtick(i)
            else:
                put(char, quoted=True)
                i += 1
        raise ParseError("an unclosed double quote")

    def bodies(i: int) -> int:
        for token, delimiter, quoted, strip in pending:
            lines: list[str] = []
            while i < n:
                end = command.find("\n", i)
                end = n if end < 0 else end
                line = command[i:end]
                i = end + 1
                if (line.lstrip("\t") if strip else line) == delimiter:
                    break
                lines.append(line)
            body = "\n".join(lines)
            token.body = body
            if not quoted and ("$" in body or "`" in body):
                token.dynamic = True
                subs.append(body)
        pending.clear()
        return min(i, n)

    i = 0
    while i < n:
        char = command[i]
        if char in " \t\r":
            flush()
            i += 1
        elif char == "#" and word is None:
            while i < n and command[i] != "\n":
                i += 1
        elif char == "\\":
            if command[i + 1 : i + 2] == "\n":
                i += 2
            else:
                put(command[i + 1 : i + 2] or "\\", quoted=True)
                i += 2
        elif char == "'":
            end = command.find("'", i + 1)
            if end < 0:
                raise ParseError("an unclosed single quote")
            put(command[i + 1 : end], quoted=True)
            i = end + 1
        elif char == "$" and command[i + 1 : i + 2] == "'":
            end = i + 2
            while end < n and command[end] != "'":
                end += 2 if command[end] == "\\" else 1
            if end >= n:
                raise ParseError("an unclosed $' quote")
            put(command[i : end + 1], dynamic=True)
            i = end + 1
        elif char == '"':
            i = double(i + 1)
        elif char == "$":
            i = dollar(i)
        elif char == "`":
            i = backtick(i)
        elif char in "<>" and command[i + 1 : i + 2] == "(":
            end = closing(command, i + 1, "(", ")")
            subs.append(command[i + 2 : end])
            put(command[i : end + 1], dynamic=True)
            i = end + 1
        else:
            op = next((o for o in OPERATORS if command.startswith(o, i)), None)
            if op is None:
                put(char)
                i += 1
                continue
            prefix = ""
            if op[0] in "<>" and word is not None and word.bare.isdigit():
                prefix, word = word.text, None
            flush()
            i += len(op)
            if op in ("<<", "<<-"):
                found = DELIMITER.match(command, i)
                if not found:
                    raise ParseError("a heredoc delimiter it cannot read")
                plain = found.group(4) is not None and found.group(3) == ""
                delimiter = next(
                    g for g in (found.group(1), found.group(2), found.group(4)) if g is not None
                )
                token = Token(op=prefix + op, text=delimiter)
                tokens.append(token)
                pending.append((token, delimiter, not plain, op == "<<-"))
                i = found.end()
            else:
                tokens.append(Token(op=prefix + op))
                if op == "\n" and pending:
                    i = bodies(i)
    flush()
    return tokens, subs


def is_heredoc(token: Token) -> bool:
    return token.op is not None and token.op.lstrip("0123456789") in ("<<", "<<-")


def simple_commands(tokens: list[Token]) -> list[list[Token]]:
    """The words of each simple command. A redirection target is not a word of its command."""
    out: list[list[Token]] = [[]]
    target = False
    for token in tokens:
        if token.op is None:
            if target:
                target = False
            else:
                out[-1].append(token)
        elif token.op in SEPARATORS:
            out.append([])
        elif not is_heredoc(token):
            target = True
    return [simple for simple in out if simple]


def command_word(words: list[Token]) -> int | None:
    """The index of the word that names the program, after assignments and compound keywords."""
    for i, word in enumerate(words):
        if ASSIGNMENT.match(word.bare) or (literal(word) and word.text in RESERVED):
            continue
        return i
    return None


def unquote(text: str) -> str:
    return re.sub(r"[\"'\\]", "", text)


def involves_git(tokens: list[Token], subs: list[str], depth: int = 0) -> bool:
    """True when the command may run git.

    That is when a de-quoted word has the basename `git`, when a substitution holds `git`, or when
    a command word is computed (`$cmd`, a glob, a brace list, `$cmd` after a runner). A word with a
    piece `git` counts when a shell, `eval` or `watch` is in the command, and the words after one
    are read as commands too.
    """
    words = [token for token in tokens if token.op is None]
    if any(Path(word.text).name == "git" for word in words):
        return True
    if any("git" in unquote(sub) for sub in subs):
        return True
    if any(Path(word.text).name in SCRIPT_TAKERS for word in words) and (
        any(
            Path(piece).name == "git" for word in words for piece in PIECE.split(word.text) if piece
        )
        or any("git" in unquote(token.body) for token in tokens if is_heredoc(token))
    ):
        return True
    for simple in simple_commands(tokens):
        head = command_word(simple)
        if head is None:
            continue
        if computed(simple[head]):
            return True
        if Path(simple[head].text).name in RUNNERS and any(
            w.text.startswith(("$", "`")) for w in simple[head + 1 :]
        ):
            return True
        for k, word in enumerate(simple):
            if Path(word.text).name not in SCRIPT_TAKERS or depth >= 3:
                continue
            for script in simple[k + 1 :]:
                if script.text.startswith("-"):
                    continue
                try:
                    inner = parse(script.text)
                except ParseError:
                    if "git" in script.text:
                        return True
                    continue
                if involves_git(*inner, depth=depth + 1):
                    return True
    return False


# --- Shapes ----------------------------------------------------------------------------------


def steps(tokens: list[Token]) -> list[tuple[list[Token], str]]:
    """The tokens split at `&&`, `;` and newlines, each part with the joiner after it."""
    out: list[tuple[list[Token], str]] = []
    current: list[Token] = []
    for token in tokens:
        if token.op in JOINERS:
            out.append((current, token.op))
            current = []
        else:
            current.append(token)
    out.append((current, ";"))
    return out


def text_of(tokens: list[Token]) -> str:
    return " ".join(token.text if token.op is None else token.op for token in tokens)


def stage_words(stage: list[Token], first: bool) -> list[Token]:
    """The words of one pipeline stage. Raises Refused for a redirection the shapes do not allow.

    Allowed: `N>&M`, output to `/dev/null`, a `<<<` here-string, and, on the git stage only, a
    heredoc whose body bash does not expand.
    """
    words: list[Token] = []
    k = 0
    while k < len(stage):
        token = stage[k]
        k += 1
        if token.op is None:
            words.append(token)
            continue
        if is_heredoc(token):
            if not first:
                raise shape("a heredoc after a pipe")
            if token.dynamic:
                raise shape(
                    f"a heredoc body that bash expands (quote the delimiter: <<'{token.text}')"
                )
            continue
        target = stage[k] if k < len(stage) and stage[k].op is None else None
        k += 1
        op = token.op.lstrip("0123456789")
        if target is None or not literal(target):
            raise shape(f"the redirection `{token.op}` without a literal target")
        if op in (">&", "<&") and re.fullmatch(r"\d+-?|-", target.text):
            continue
        if op in (">", ">>", "&>", "&>>", ">|") and target.text == "/dev/null":
            continue
        if op == "<<<":
            continue
        raise shape(f"the redirection `{token.op} {target.text}` (redirect only to /dev/null)")
    return words


def reader_word(word: Token) -> bool:
    """True for a word a reader may take: a literal word, `[`, or one whose only expansion is
    `$?`."""
    if word.text == "[" and not word.dynamic:
        return True
    if literal(word):
        return True
    rest = word.text.replace(STATUS, "")
    return (
        not any(mark in rest for mark in ("$", "`", "<(", ">("))
        and not EXPANDS.search(word.bare)
        and not word.bare.startswith("~")
    )


def filter_refusal(words: list[Token], programs: set[str] = FILTERS) -> str | None:
    """Why these words are refused as a reader, or None for an allowed reader.

    `programs` names the readers allowed: FILTERS after a pipe, READERS for a step of its own.
    """
    name = words[0].text
    if words[0].dynamic or name not in programs or not reader_word(words[0]):
        return f"the pipe into `{name}`"
    if bad := next((w for w in words[1:] if not reader_word(w)), None):
        return f"the computed argument `{bad.text}` of `{name}`"
    args = [w.text for w in words[1:]]
    if name == "printf" and any(a.startswith("-v") for a in args):
        return "`printf -v`, which sets a variable"
    if name == "sed":
        options = [a for a in args if a.startswith("-")]
        scripts = [a for a in args if not a.startswith("-")]
        if "-n" not in options or any(o not in ("-n", "-E", "-r") for o in options):
            return "`sed` without `-n`, or with an option other than `-n`, `-E` or `-r`"
        if not scripts or not SED_SCRIPT.match(scripts[0]):
            return f"the `sed` script `{scripts[0] if scripts else ''}` (only `N,Mp` and `/re/p`)"
    if name == "awk":
        rest = iter(args)
        program = None
        for arg in rest:
            if arg in ("-F", "-v"):
                next(rest, None)
            elif arg.startswith(("-F", "-v")) and len(arg) > 2:
                continue
            elif arg.startswith("-"):
                return f"the `awk` option `{arg}` (only `-F` and `-v`)"
            else:
                program = arg
                break
        if program is None or AWK_UNSAFE.search(program):
            return "an `awk` program that is missing or holds `system`, `|`, `>` or `@load`"
    if name == "rg" and any(a.startswith("--pre") for a in args):
        return "`rg --pre`, which runs a program"
    if name == "sort":
        for arg in args:
            option = arg.split("=", 1)[0]
            if (
                len(option) > 2
                and ("--output".startswith(option) or "--compress-program".startswith(option))
            ) or (re.match(r"^-[A-Za-z]*o", arg) and not arg.startswith("--")):
                return f"`sort {arg}`, which writes a file or runs a program"
    if name == "uniq" and any(not a.startswith("-") for a in args):
        return "`uniq` with a file argument (it writes the second one)"
    if name == "less" and any(not re.fullmatch(r"-[RSFXN]+", a) for a in args):
        return "`less` with an option other than `-R`, `-S`, `-F`, `-X` or `-N`"
    return None


def config_key_refusal(setting: str) -> str | None:
    """Why `git -c <setting>` is refused, or None for an allowlisted key."""
    key, _, value = setting.partition("=")
    key = key.lower()
    allowed = any(fnmatch.fnmatchcase(key, pattern) for pattern in CONFIG_ALLOW)
    if COMMAND_KEY.search(key) or (key == "core.pager" and not SAFE_PAGER.match(value.strip())):
        allowed = False
    if allowed:
        return None
    return (
        f"Do not set the Git config key `{key}` in an agent command. `git -c` and "
        "`git clone --config` may set only the keys in `harness.workspace.gitConfigAllow` of the "
        "config, and never a key that runs a program."
    )


def env_allowed(name: str) -> bool:
    return name in ENV_ALLOW or bool(LOCALE.match(name))


def env_refusal(name: str) -> str:
    return (
        f"Do not set {name} in a command that runs git. Such a command may set only "
        f"{', '.join(sorted(ENV_ALLOW))} and LC_*: any other variable can make Git, or a "
        "program it starts, run or read something else."
    )


def git_step(words: list[Token]) -> tuple[list[str], list[str]]:
    """The git arguments after the global flags, and each `-C` path, of a step that is git.

    Raises Refused for a variable off the allowlist, a computed argument, a refused `-c` key and a
    flag that moves git to another repository or another set of programs.
    """
    i = 0
    while i < len(words) and (found := ASSIGNMENT.match(words[i].bare)):
        if not env_allowed(found.group(1)):
            raise Refused(env_refusal(found.group(1)))
        if not literal(words[i]):
            raise shape(f"the computed value of `{words[i].text}`")
        i += 1
    args = [word.text for word in words[i + 1 :]]
    if bad := next((w for w in words[i + 1 :] if not literal(w)), None):
        raise shape(f"the computed git argument `{bad.text}`")
    moves: list[str] = []
    while args and args[0].startswith("-"):
        flag = args.pop(0)
        if flag in ("-C", "-c"):
            if not args:
                raise shape(f"`git {flag}` without a value")
            value = args.pop(0)
            if flag == "-C":
                moves.append(value)
            elif refusal := config_key_refusal(value):
                raise Refused(refusal)
        elif flag.startswith("-c"):
            if refusal := config_key_refusal(flag[2:]):
                raise Refused(refusal)
        elif flag.startswith("--config-env"):
            raise Refused("Do not set Git configuration through --config-env in an agent command.")
        elif flag.startswith("-C"):
            moves.append(flag[2:])
        elif flag.startswith(("--git-dir", "--work-tree", "--bare", "--namespace")):
            raise Refused("Use a worktree path with git -C, not an alternate Git directory.")
        elif flag.startswith("--exec-path"):
            raise Refused(
                "Do not set --exec-path in an agent command: it makes Git run programs from "
                "another folder."
            )
    return args, moves


def pipe_refusal(stages: list[list[Token]]) -> str | None:
    """Why the stages after the first are refused, or None when each one is a plain filter."""
    for stage in stages[1:]:
        filtered = stage_words(stage, first=False)
        if not filtered:
            return "an empty pipe stage"
        if refusal := filter_refusal(filtered):
            return refusal
    return None


def read_step(step: list[Token]) -> str | tuple[list[str], list[str]] | None:
    """The path of a `cd` step, the `git_step` result of a git step, or None for a reader step.

    Raises Refused for every other step.
    """
    if blocked := next((t for t in step if t.op in SEPARATORS and t.op != "|"), None):
        names = {
            "||": "`||`",
            "&": "a background `&`",
            "|&": "`|&`",
            "(": "a subshell or function",
            ")": "a subshell or function",
            ";;": "a `case` command",
            ";&": "a `case` command",
        }
        raise shape(names.get(blocked.op or "", f"`{blocked.op}`"))
    stages: list[list[Token]] = [[]]
    for token in step:
        if token.op == "|":
            stages.append([])
        else:
            stages[-1].append(token)
    words = stage_words(stages[0], first=True)
    head = command_word(words)
    if head is None:
        raise shape(f"the step `{text_of(step)}`, which runs no command")
    name = words[head].text
    if len(words) and words[0].text == "cd" and literal(words[0]):
        if (
            len(stages) > 1
            or len(words) != 2
            or not literal(words[1])
            or words[1].text in ("", "-")
            or words[1].text.startswith("-")
        ):
            raise shape(f"`{text_of(step)}`, which is not `cd` with one literal path")
        return words[1].text
    if head == 0 and name in READERS:
        if refusal := filter_refusal(words, READERS) or pipe_refusal(stages):
            raise shape(refusal)
        return None
    if (
        name != "git"
        or not literal(words[head])
        or any(literal(w) and w.text in RESERVED for w in words[:head])
    ):
        if any(literal(w) and w.text in RESERVED for w in words[: head + 1]):
            why = "a shell keyword such as `if`, `{`, `!` or `time`"
        elif computed(words[head]):
            why = f"workspace-guard cannot tell what `{name}` runs, so name the command literally"
        elif Path(name).name in RUNNERS:
            why = f"`{name}` is a runner that starts another command"
        else:
            why = "it is not `cd` or `git`"
        raise shape(f"the step `{text_of(step)}` ({why})")
    if refusal := pipe_refusal(stages):
        raise shape(refusal)
    return git_step(words)


@functools.cache
def common_dir(directory: Path) -> Path:
    """The absolute common Git directory of the repository at `directory`."""
    return Path(git(directory, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()


def require_shared(directory: Path, root: Path) -> None:
    """Raise Refused unless git at `directory` uses the repository of `root`, the project root.

    The directory must sit in the shared checkout (the main checkout and its worktrees), and its
    common Git directory must be the one of `root`, so a nested repository is refused too.
    """
    common = common_dir(root)
    shared = common.parent
    why = (
        "A repository there can hold configuration that runs a program. "
        "Run git only inside this checkout and its worktrees."
    )
    if not directory.is_relative_to(shared):
        raise Refused(f"git would run in {directory}, outside this checkout ({shared}). {why}")
    try:
        theirs = common_dir(directory)
    except (subprocess.SubprocessError, OSError) as error:
        raise Refused(
            f"git would run in {directory}, which is not a directory of this repository. {why}"
        ) from error
    if theirs != common:
        raise Refused(
            f"git would run in {directory}, a nested repository ({theirs}), not this one "
            f"({common}). {why}"
        )


def read_only(args: list[str]) -> bool:
    """True for a git command that changes no state: a READ_ONLY subcommand, a remote read or a
    config read."""
    if not args:
        return True
    sub, *rest = args
    if sub == "remote":
        return bool(REMOTE_READS.match(" ".join(rest)))
    if sub == "config":
        return config_read(rest)
    return sub in READ_ONLY


def config_read(rest: list[str]) -> bool:
    """True for a `git config` that only reads: `get`, `list`, a read option, or one key alone.

    A write option, abbreviated or not, makes it a write even beside a read option.
    """
    positional: list[str] = []
    reads = False
    words = iter(rest)
    for arg in words:
        if long_flag(arg, CONFIG_WRITES) or re.match(r"^-[A-Za-z]*e", arg):
            return False
        if arg in CONFIG_READS:
            reads = True
        elif arg == "-f" or (long_flag(arg, CONFIG_VALUES) and "=" not in arg):
            next(words, None)
        elif not arg.startswith("-"):
            positional.append(arg)
    if positional[:1] in (["get"], ["list"]):
        return True
    return reads or (len(positional) == 1 and CONFIG_KEY.match(positional[0]) is not None)


def long_flag(arg: str, flags: tuple[str, ...]) -> str | None:
    """The flag of `flags` that `arg` names in full, abbreviated or with `=value`, or None."""
    name = arg.split("=", 1)[0]
    if not arg.startswith("--") or len(name) <= 2:
        return None
    return next((f for f in flags if f.startswith(name)), None)


def clone_refusal(rest: list[str]) -> str | None:
    """Why the arguments of `git clone` are refused, or None.

    `--template` copies hooks into the new repository, and `--separate-git-dir` and `--reference`
    move its Git directory or objects to another place. Each `-c` and `--config` key must pass the
    allowlist of `git -c`. A short `-c` may stand in a bundle (`-qc`) and hold its value (`-ckey`).
    """
    words = iter(rest)
    for arg in words:
        if hit := long_flag(arg, CLONE_REFUSED):
            return (
                f"Do not pass `{arg}` to `git clone` in an agent command: `{hit}` puts hooks, the "
                "Git directory or the objects of the new repository in another place."
            )
        setting = None
        if long_flag(arg, ("--config",)):
            setting = arg.split("=", 1)[1] if "=" in arg else next(words, None)
        elif found := re.match(r"^-[A-Za-z0-9]*c(.*)$", arg):
            setting = found.group(1) or next(words, None)
        else:
            continue
        if setting is None:
            return f"Do not pass `{arg}` to `git clone` without a config value."
        if refusal := config_key_refusal(setting):
            return refusal
    return None


def flag_refusal(args: list[str]) -> str | None:
    """Why a flag in REFUSED_FLAGS of the subcommand, or a `git clone` flag, is refused, or None.

    A `--` can be the value of the option before it (`git grep -e -- -O`), so the words after it
    are checked too. A path that looks like a refused flag is refused as well.
    """
    if not args:
        return None
    sub, *rest = args
    refused = REFUSED_FLAGS.get(sub, ())
    for arg in rest:
        hit = long_flag(arg, refused)
        if hit is None and arg.startswith("-") and not arg.startswith("--"):
            hit = next(
                (f for f in refused if len(f) == 2 and re.match(rf"^-[A-Za-z0-9]*{f[1]}", arg)),
                None,
            )
        if hit:
            return (
                f"Do not pass `{arg}` to `git {sub}` in an agent command: `{hit}` makes Git run "
                "a program or write a file. workspace-guard checks every argument, also after "
                "`--`, so a path with this name is refused too."
            )
    if sub == "clone":
        return clone_refusal(rest)
    return None


def reason(args: list[str], cwd: Path, own: Path, after_other: bool) -> str | None:
    sub, *rest = args
    if read_only(args) or sub == "fetch":
        return None
    if sub == "stash":
        return "Never stash: every worktree shares the stash stack."
    if sub == "add" and any(
        x in ("-A", "--all", ".", ":/", "*") or (x.startswith("-") and "A" in x) for x in rest
    ):
        return "Stage the named files you changed; never stage the whole checkout."
    target = checkout(cwd)
    common = Path(git(target, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    shared = common.parent
    if sub == "worktree" and rest and rest[0] == "add":
        values = iter(rest[1:])
        for value in values:
            if value in ("-b", "-B"):
                next(values)
            elif not value.startswith("-"):
                path = (cwd / value).resolve()
                if not path.is_relative_to(shared / ".claude/worktrees"):
                    return "Create worktrees under the shared checkout .claude/worktrees directory."
                return None
        return "Name the new worktree path."
    if sub == "worktree" and (not rest or rest[0] in ("list", "prune")):
        return None
    if (
        sub == "worktree"
        and rest[0] in WORKTREE_CHANGES
        and (refusal := worktree_refusal(rest, cwd, target, shared, after_other))
    ):
        return refusal
    if sub == "branch" and not any(
        x.startswith("-") and any(c in x for c in "fmdDM") for x in rest
    ):
        return None
    if sub == "config":
        return "Do not change shared Git configuration from an agent command."
    if sub == "update-ref" or (sub == "branch" and any(x in rest for x in ("-f", "-M", "--force"))):
        return f"Never move a branch ref alone. Run `{LAND}` from your worktree."
    if sub == "push" and any(x in rest for x in (".", str(shared))):
        return f"Run `{LAND}` to update the local base branch; do not push into a checkout by hand."
    if target != own or (WORKTREES_ONLY and target == shared):
        return f"Mutate only your own worktree. Run `{LAND}` to update the local base branch."
    if sub in ("restore", "checkout", "clean") and any(
        (cwd / path).is_dir() for path in rest if not path.startswith("-")
    ):
        return "Restore only named files you own, never a directory."
    return None


def worktree_refusal(
    rest: list[str], cwd: Path, own: Path, shared: Path, after_other: bool
) -> str | None:
    """Why a `git worktree` change is refused, or None when each path it names is `own`.

    `after_other` is True when a step that is not git runs before this one in the command.
    """
    verb, *args = rest
    paths: list[str] = []
    values = iter(args)
    for arg in values:
        if arg == "--reason":
            next(values, None)
        elif not arg.startswith("-"):
            paths.append(arg)
    if verb == "move":
        if len(paths) != 2:
            return "Name the worktree and its new path in `git worktree move`."
        if refusal := move_target_refusal(paths.pop(), cwd, shared, after_other):
            return refusal
    for path in paths:
        if not exact_path(path, cwd, own):
            return (
                f"`git worktree {verb} {path}` does not name your own worktree by its exact path. "
                f"Write `git worktree {verb} {own}`: no `..`, no link and no short name."
            )
    return None


def exact_path(arg: str, cwd: Path, own: Path) -> bool:
    """True when `arg` is the path of `own` as written: absolute or from `cwd`, with no `..`,
    no link in any part, and nothing that resolving would change."""
    lexical = Path(os.path.normpath(cwd / arg))
    return (
        ".." not in Path(arg).parts
        and lexical == own
        and lexical == lexical.resolve()
        and not any(part.is_symlink() for part in (lexical, *lexical.parents))
    )


def move_target_refusal(target: str, cwd: Path, shared: Path, after_other: bool) -> str | None:
    """Why `git worktree move` may not put a worktree at `target`, or None.

    The target is a new folder right in `.claude/worktrees` of the shared checkout, written with
    no `..`, and no part of that folder is a link. A step before the move could create the
    target or a link, so the move runs first or after git steps only.
    """
    folder = shared / ".claude" / "worktrees"
    home = folder.resolve()
    lexical = Path(os.path.normpath(cwd / target))
    parent = lexical.parent
    if after_other:
        return "Run `git worktree move` before any step that is not git in its command."
    if (shared / ".claude").is_symlink() or folder.is_symlink():
        return f"Do not move a worktree into {folder}: a part of that folder is a link."
    if (
        ".." in Path(target).parts
        or parent != home
        or parent.resolve() != home
        or not WORKTREE_NAME.fullmatch(lexical.name)
        or os.path.lexists(lexical)
        or str(lexical) != os.path.join(os.path.realpath(parent), lexical.name)
    ):
        return (
            f"Move a worktree only to a new folder right in {home}, written with no `..`, with a "
            "name of letters, digits, `.`, `_` and `-`."
        )
    return None


def check_shapes(tokens: list[Token], cwd: Path, root: Path) -> str | None:
    """Why the command is refused, or None. Each git step is checked in every directory it may
    run in.

    A `cd` that fails leaves the directory as it was, and `&&` then skips to the next `;`. So the
    directories after a `;` are those after the last step plus each one a failed `cd` left.
    """
    dirs = frozenset({cwd})
    stopped: set[Path] = set()
    own: Path | None = None
    after_other = False
    for step, joiner in steps(tokens):
        if step:
            allowed = read_step(step)
            if allowed is None:
                after_other = True
            elif isinstance(allowed, str):
                after_other = True
                stopped |= dirs
                dirs = frozenset((d / allowed).resolve() for d in dirs)
            else:
                args, moves = allowed
                for start in sorted(dirs):
                    directory = start
                    for move in moves:
                        directory = (directory / move).resolve()
                    require_shared(directory, root)
                    if refusal := flag_refusal(args):
                        return refusal
                    if not read_only(args):
                        own = own or checkout(cwd)
                        if denied := reason(args, directory, own, after_other):
                            return denied
        if joiner != "&&":
            dirs = dirs | stopped
            stopped = set()
    return None


def check_command(command: str, cwd: Path) -> str | None:
    """Why the command is refused when it runs in `cwd`, or None."""
    if "GIT_CONFIG" in unquote(command):
        return "Do not set Git configuration through GIT_CONFIG* variables in an agent command."
    try:
        tokens, subs = parse(command)
    except ParseError as error:
        if "git" not in unquote(command):
            return None
        return (
            f"workspace-guard cannot parse this command ({error}), so it cannot check its Git use. "
            "Balance its quotes, or put the text in a file and pass the file."
        )
    if not involves_git(tokens, subs):
        return None
    root = project_root()
    if not cwd.is_relative_to(root):
        return (
            f"git would run from {cwd}, outside this project ({root}), the checkout that holds "
            "this hook. Run git only inside this checkout and its .claude/worktrees/ worktrees."
        )
    return check_shapes(tokens, cwd, root)


def check(payload: dict[str, Any]) -> str | None:
    if payload.get("tool_name") not in ("Bash", "exec_command", "shell_command"):
        return None
    fields = payload.get("tool_input") or {}
    command = fields.get("command", fields.get("cmd", ""))
    cwd = Path(fields.get("workdir") or payload.get("cwd") or Path.cwd()).resolve()
    return check_command(command, cwd)


def main() -> int:
    try:
        denied = check(json.load(sys.stdin))
    except Refused as error:
        denied = str(error)
    except (ValueError, IndexError, StopIteration, OSError, subprocess.SubprocessError) as error:
        denied = f"Cannot check Git workspace ownership: {error}"
    if denied:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": denied,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
