#!/usr/bin/env python3
"""Refuse a Bash command that reads a credential file.

`deny-env-reads.py` refuses the `guardedFiles` of the config to any command. This hook gates on
the reader: every other `.env*`, a session file, the key and certificate family and the
`secretFiles` of the config are refused to `cat`, `awk`, `sed` and the rest. A command that
names no secret gets no verdict, because an `allow` would bypass the permission rules. The hook
prints only the path that matched, never the contents of a file. A lone run of the env engine
passes, and a run with `--reveal` asks (docs/hooks.md, "The env engine exemption").
"""

from __future__ import annotations

import os
import re
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import hookio  # noqa: E402

HOOK = "bash-read-guard"

# A bracket expression can hold `[` or `--`, which Python's re reads as a possible nested set in
# a later version. The class means what bash means today, so the warning is noise on stderr.
warnings.simplefilter("ignore", FutureWarning)

# Commands that put a file's CONTENTS somewhere a reader can see them: on the terminal, in a copy,
# in an archive, on the clipboard or on the network. `ls`, `find` and `stat` print names and
# sizes, so they are deliberately absent.
READERS = {
    ".",
    "awk",
    "base64",
    "bat",
    "bzcat",
    "cat",
    "cmp",
    "column",
    "comm",
    "cp",
    "csplit",
    "curl",
    "cut",
    "dd",
    "diff",
    "ed",
    "egrep",
    "emacs",
    "ex",
    "expand",
    "fgrep",
    "fmt",
    "fold",
    "gawk",
    "gsed",
    "git",
    "grep",
    "gzip",
    "head",
    "hexdump",
    "iconv",
    "install",
    "join",
    "jq",
    "less",
    "look",
    "lua",
    "mawk",
    "more",
    "mv",
    "nano",
    "nawk",
    "nc",
    "ncat",
    "nl",
    "node",
    "od",
    "openssl",
    "paste",
    "pbcopy",
    "perl",
    "php",
    "pr",
    "python",
    "python3",
    "rev",
    "rg",
    "rsync",
    "Rscript",
    "ruby",
    "scp",
    "sed",
    "socat",
    "sort",
    "source",
    "split",
    "sqlite3",
    "strings",
    "tac",
    "tail",
    "tar",
    "tee",
    "tr",
    "unexpand",
    "uniq",
    "vi",
    "view",
    "vim",
    "wc",
    "wget",
    "wl-copy",
    "xclip",
    "xsel",
    "xxd",
    "xzcat",
    "yq",
    "zcat",
    "zip",
}

# The predicates of `find` whose argument is a name pattern that `find` matches, not a path.
FIND_PATTERNS = {
    "-name",
    "-iname",
    "-path",
    "-ipath",
    "-wholename",
    "-iwholename",
    "-regex",
    "-iregex",
    "-lname",
    "-ilname",
}

# The actions of `find` that run a command on each name, so the word after one is a command.
FIND_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir"}

# The short magic of a git pathspec that drops names (`:!x`, `:^x`, `:/!x`), and its long magic
# list (`:(top,exclude)x`). The list drops names only when one entry is exactly `exclude`.
EXCLUDE_SHORT_MAGIC = re.compile(r":/*[!^]")
LONG_MAGIC = re.compile(r":\(([^)]*)\)")
# The magic in front of a pathspec, long or short, so the name test reads the pattern alone.
MAGIC_PREFIX = re.compile(r"^:(?:\([^)]*\)|[/!^]*:?)")

# The global options of git that take the next word as their value.
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

# The one command whose quoted heredoc body skips the glob scan: `cat` or `tee` reads the body as
# data, writes at most one file, and a pipe feeds only a filter. Nothing else runs.
DATA_HEREDOC = re.compile(
    r"\s*(?:cat(?:\s*>>?\s*[\w./-]+)?|tee(?:\s+-a)?(?:\s+[\w./-]+)?)"
    r"\s+<<-?\s*(['\"])(\w+)\1"
    r"(?:\s*>>?\s*[\w./-]+)?"
    r"(?:\s*\|\s*(?:head|tail|grep|wc|cat)(?:\s+[\w.=-]+|\s+'[^']*')*)*\s*"
)
# The words of a program body that can expand a glob: a glob or match function, or a call that
# starts a program or a shell. A `python3 -` or `node -` body with none of them gets no glob scan.
PYTHON_GLOB_RUNNERS = re.compile(
    r"glob|fnmatch|match\(|full_match|system|popen|exec|spawn|subprocess|shell|getoutput|pty"
    r"|ctypes|import_module|__import__",
    re.IGNORECASE,
)
NODE_GLOB_RUNNERS = re.compile(
    r"glob|match|exec|spawn|child_process|shell|fork|require\(\s*[^'\"`\s]|import\(",
    re.IGNORECASE,
)
PROGRAM_GLOB_RUNNERS = {
    "python": PYTHON_GLOB_RUNNERS,
    "python3": PYTHON_GLOB_RUNNERS,
    "node": NODE_GLOB_RUNNERS,
}
# The interpreters that run a program they read.
INTERPRETERS = {
    *("python", "python3", "node", "deno", "bun", "perl", "ruby", "php", "lua", "Rscript"),
    *("awk", "gawk", "mawk", "nawk"),
}

# The commands of a command whose glob words the guard lists on disk, and the commands a pipe may
# feed there. No option of any of them runs a program or writes a file: `rg --pre`, `sort -o`,
# `uniq IN OUT`, `less` and `file -C` can, so they are not here.
LISTING_READERS = {
    *("ls", "wc", "cat", "head", "tail", "grep", "egrep", "fgrep", "stat", "du"),
    *("md5sum", "sha1sum", "sha256sum", "diff", "cmp", "nl"),
}
LISTING_FILTERS = {"head", "tail", "grep", "wc", "cat"}
# The commands that run no file that they name, so none of them can run a body written before.
INERT_COMMANDS = LISTING_READERS | {"echo", "printf", "true", "pwd"}

# The shell keywords after which the next word is a command: `while read f; do cat "$f"; done`.
KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until"}

# The operators of `find` that negate the next test.
FIND_NOT = {"-not", "!"}

# The operators of `find` that join two tests. With one, a negated test can pick a name.
FIND_JOINS = {"-o", "-or", "-a", "-and", ","}

# The actions of `find` that print, write, run or delete. An action before a negated test acts on
# every name, so the negation drops none.
FIND_OUTPUTS = {
    "-print",
    "-print0",
    "-printf",
    "-fprint",
    "-fprint0",
    "-fprintf",
    "-ls",
    "-fls",
    "-delete",
    *FIND_ACTIONS,
}

# An action that `find` adds after an expression with no action. At the end, it changes nothing.
FIND_DEFAULT_ACTIONS = {"-print", "-print0"}

# A command that can turn `dotglob` on: `shopt`, `GLOBIGNORE`, `set -o`, or a file that a shell
# runs first (`BASH_ENV`, `ENV`, `source`). zsh turns it on with `setopt` and the `GLOB_DOTS`
# option, which zsh reads in any case and with `_` anywhere, and csh and tcsh with `globdot`. A
# `(` after a glob character in the same word, or right after a `)`, can open a zsh glob
# qualifier, such as `*(D)`, `?env(.D)` or `(*.pem)(D)`, which turns it on for that glob. So does
# any group of parentheses with a `D` in it. The leading `.` command and the shells of
# `DOTGLOB_SHELLS` are tested on the command words.
DOTGLOB = re.compile(
    r"shopt|dotglob|GLOBIGNORE|\bset\s+-o|BASH_ENV|ENV=|\bsource\b|setopt"
    r"|(?i:g_*l_*o_*b_*d_*o_*t)|[*?\]][^\s()|;&<>]*\(|\)\(|\([^()\s]*D[^()\s]*\)"
)

# Commands that print file names. A reader that takes their names reads files the line never
# names, so a listing that no pattern bounds reads as the glob `*`, which can match a key name.
LISTERS = {"ls", "find", "fd", "fdfind", "locate", "plocate"}

# The commands after a pipe that hand each name they read to a command as a file.
NAME_TAKERS = {"xargs", "parallel", "while"}

# The options of a grep stage that print a count or nothing in place of the lines.
COUNT_OPTION = re.compile(r"--count|--quiet|--silent|-[A-Za-z]*[cq][A-Za-z]*")

# A shell whose own options or rc files can match a name that starts with `.` to any glob. Any
# shell with `-c` runs a command line that the guard reads only as text.
DOTGLOB_SHELLS = {"zsh", "ksh", "mksh", "fish", "csh", "tcsh"}
SHELLS = {"sh", "bash", "dash", "ash", "zsh", "ksh", "mksh", "fish", "csh", "tcsh", "busybox"}


@dataclass(frozen=True)
class ScriptOptions:
    """The options of a tool whose first operand is a script (a regex or a program), not a path.

    Each option is one of four kinds: a flag, a value, a script (its value is the script) or a
    script file (the script comes from a file, so no operand is the script). An option outside
    these sets makes the guard read no word of the stage as a script.
    """

    flags: str
    values: str
    scripts: str
    script_files: str
    long_flags: frozenset[str]
    long_values: frozenset[str]
    long_scripts: frozenset[str]
    long_script_files: frozenset[str]


def option_set(text: str) -> frozenset[str]:
    """The blank-separated option names of `text` as a set."""
    return frozenset(text.split())


GREP_OPTIONS = ScriptOptions(
    flags="EFGPivwxyclLoqsbHhnTZzrRaIUV",
    values="mABCdD",
    scripts="e",
    script_files="f",
    long_flags=option_set(
        "--extended-regexp --fixed-strings --basic-regexp --perl-regexp --ignore-case"
        " --no-ignore-case --invert-match --word-regexp --line-regexp --count"
        " --files-with-matches --files-without-match --only-matching --quiet --silent"
        " --no-messages --byte-offset --with-filename --no-filename --line-number --initial-tab"
        " --null --null-data --recursive --dereference-recursive --text --color --colour"
        " --no-group-separator --line-buffered"
    ),
    long_values=option_set(
        "--max-count --after-context --before-context --context --directories --devices --label"
        " --include --exclude --exclude-dir --exclude-from --binary-files --group-separator"
    ),
    long_scripts=option_set("--regexp"),
    long_script_files=option_set("--file"),
)
RG_OPTIONS = ScriptOptions(
    flags="iswxFPclLnNoqSuUvzaHIb0.",
    values="gtTmACBMrjdE",
    scripts="e",
    script_files="f",
    long_flags=option_set(
        "--ignore-case --smart-case --case-sensitive --word-regexp --line-regexp --fixed-strings"
        " --pcre2 --count --count-matches --files-with-matches --files-without-match"
        " --line-number --no-line-number --only-matching --quiet --hidden --no-ignore"
        " --invert-match --null --text --with-filename --no-filename --no-heading --heading"
        " --json --vimgrep --multiline --follow --unrestricted --no-messages --trim --column"
        " --byte-offset --search-zip --stats"
    ),
    long_values=option_set(
        "--glob --iglob --type --type-not --max-count --max-depth --context --after-context"
        " --before-context --replace --threads --max-columns --encoding --sort --sortr --color"
        " --colors --type-add --pre --pre-glob --max-filesize --engine --ignore-file"
    ),
    long_scripts=option_set("--regexp"),
    long_script_files=option_set("--file"),
)
# awk and sed are absent: `system()`, a `|` pipe, `getline` from a command and `print >` of
# awk, and the `e` command, the `e` flag and the `w` file of sed, run a shell or write a file. So
# the glob scan reads every awk program and every sed script in full.
SCRIPT_TOOLS = {
    "grep": GREP_OPTIONS,
    "egrep": GREP_OPTIONS,
    "fgrep": GREP_OPTIONS,
    "rg": RG_OPTIONS,
}
# `-O` of `git grep` opens the matched files in a pager, so it is no flag here.
GIT_GREP_OPTIONS = ScriptOptions(
    flags="aIivwhHEGFPnlLocqzWp0123456789",
    values="ABCm",
    scripts="e",
    script_files="f",
    long_flags=option_set(
        "--cached --no-index --untracked --no-exclude-standard --exclude-standard"
        " --recurse-submodules --text --textconv --no-textconv --ignore-case --invert-match"
        " --word-regexp --recursive --no-recursive --full-name --extended-regexp --basic-regexp"
        " --perl-regexp --fixed-strings --line-number --column --files-with-matches --name-only"
        " --files-without-match --only-matching --count --null --break --heading --show-function"
        " --function-context --quiet --all-match --and --or --not --no-color"
    ),
    long_values=option_set(
        "--max-depth --threads --after-context --before-context --context --max-count"
    ),
    long_scripts=frozenset(),
    long_script_files=frozenset(),
)

# The name filters of grep and rg. Each one drops files from a recursive search.
FILTER_OPTIONS = {"--include", "--exclude", "--exclude-dir", "-g", "--glob", "--iglob"}

# The git subcommands that match their operands against ref names.
REF_VERBS = {"branch", "tag", "for-each-ref", "show-ref"}

# The long flags of sed that take no value, run nothing and write nothing.
SED_LONG_FLAGS = {
    *("--quiet", "--silent", "--regexp-extended", "--posix", "--separate", "--null-data"),
    *("--unbuffered", "--debug", "--sandbox"),
}

SECRET_BASENAME = re.compile(r"(\.env(\..+)?|.+\.secret\.env|.+\.secret|\.netrc|\.git-credentials)")

# A key or certificate name. Script text says `e.key` and `cert.pem` as property access, and a
# search pattern says `id_rsa`, so a bare word counts only when it is a path (it holds a `/`) or
# a file in the command's directory.
KEY_BASENAME = re.compile(r"(.+\.(pem|key|p12|pfx)|id_rsa.*|id_ed25519.*)")

# Commands that read a file in another directory than the session's, or find one by name. With
# one of them, the existence test cannot see the file, so a key name counts by name alone.
MOVERS = {"cd", "pushd", "popd", "find", "fd", "locate", "xargs"}

# A bracket expression with its POSIX classes (`[[:alpha:]]`, `[[=a=]]`, `[[.a.]]`) kept whole.
BRACKET = re.compile(r"\[!?\]?(?:\[([:=.]).*?\1\]|[^\]])*\]")

# A provider session token. Matched on the directory: the basename is an ordinary `.json`.
SESSION_PATH = re.compile(r"(^|/)sessions/[^/]+\.json$")

# A word in command position starts again after any of these.
SEPARATORS = {"|", ";", "&", "(", ")", "{", "}", "<", ">", "\n", "!"}

# Runs another command, so the reader may sit further along the stage rather than at its head.
#
# Each one carries its own flags and arguments before the real command (`timeout 5 cat`,
# `nice -n 5 cat`), and `sh -c` hides the whole command inside one quoted word, so a wrapper
# makes every remaining word of that stage a candidate rather than only the next one. That
# over-refuses a contrived line; under-refusing hands a credential file to a reader.
WRAPPERS = {
    "builtin",
    "command",
    "doas",
    "env",
    "eval",
    "exec",
    "ionice",
    "nice",
    "nohup",
    "setsid",
    "stdbuf",
    "sudo",
    "time",
    "timeout",
    "watch",
    "xargs",
} | SHELLS

# `LC_ALL=C cat ...` keeps the command position: the assignment is not the command.
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")

# Punctuation that cannot be part of a path, so a fragment between two of them may be one.
NOT_PATH = re.compile(r"[^A-Za-z0-9_.@+~/-]+")

# The shell operators and blanks that end a word. A `$` stays, so the regex `^ *$` keeps `*$`.
SHELL_SPLIT = re.compile(r"[\s;|&()<>`]+")

# The punctuation of program text that can sit next to a glob word: a comma, a bracket, a brace,
# a parenthesis and a quote.
PROGRAM_SPLIT = re.compile(r"[,\[\](){}'\"]+")

# A special parameter that expands to a number: `$?`, `$#`, `$!` and `$$`.
NUMBER_PARAM = re.compile(r"\$[?#!$]")

# An expansion that can stand for any text: `$0` to `$9`, `$*`, `$@`, `$-`, `$name` and `${...}`.
TEXT_EXPANSION = re.compile(r"\$[0-9*@-]|\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*")

# A `${...}` with its name and subscript: the group is the text after them, such as `:-*.pem`.
PARAMETER = re.compile(
    r"\$\{[#!]?(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[*@#?$!-])(?:\[[^\]]*\])?([^}]*)\}"
)

# An ANSI-C quoted string, `$'...'`, whose backslash escapes bash decodes.
ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'")
ANSI_C_ESCAPE = re.compile(
    r"\\(x[0-9A-Fa-f]{1,2}|u[0-9A-Fa-f]{1,4}|U[0-9A-Fa-f]{1,8}|[0-7]{1,3}|c.|.)"
)

# The letter escapes of `$'...'`.
ANSI_C_LETTERS = {
    "a": "\x07",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}

# `$IFS` and `${IFS}` split words as a blank does.
IFS = re.compile(r"\$\{IFS[^}]*\}|\$IFS(?![A-Za-z0-9_])")

OPERATORS = {";", "|", "&", "(", ")", "{", "}", "<", ">", "\n"}

# The only characters the line that opens an exempt heredoc may hold.
LINE_CHARS = re.compile(r"[A-Za-z0-9_./ \t<>'-]*")

# A heredoc delimiter this guard is certain of.
DELIMITER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# A plain relative path: no glob, expansion, substitution, `~`, no leading `/` or `-`, and no
# segment that starts with `.`, so `.git/hooks/x`, `.claude/x` and `../x` are out.
RELATIVE_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*")

# A file name that a shell or an interpreter runs as a script.
SCRIPT_NAME = re.compile(r"\.(sh|bash|zsh|py|mjs|js|cjs|mts|ts|rb|pl|ps1|fish)$", re.IGNORECASE)

# The flags `git commit -F -` and `git tag -F -` may carry. Each one is a whole word: no
# abbreviation.
GIT_FLAGS = {
    "commit": {"-a", "--all", "--amend", "--allow-empty", "-q", "--quiet", "-s", "--signoff"},
    "tag": {"-a", "--annotate", "-s", "--sign", "-f", "--force"},
}

# The name `git tag` takes.
TAG_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")

# The tails a private key name carries: none, or a backup suffix. `.pub` is a public key.
KEY_TAILS = ["", ".old", ".bak", ".backup", ".orig", ".save", ".1", "~", "_old", "_backup", "-old"]

Token = str | Callable[[str], bool]


class Names:
    """The credential names of the project: the config entries and the glob targets."""

    def __init__(self, cfg: Any) -> None:
        extra = hookio.patterns(hookio.harness_value(cfg, "secretFiles"), HOOK) + hookio.patterns(
            hookio.harness_value(cfg, "guardedFiles"), HOOK
        )
        self.extra = [(entry, hookio.name_pattern(entry["pattern"])) for entry in extra]
        # Names a glob is tested against: a pattern that could expand to one of them reads it.
        self.glob_targets = [".env", ".env.local", ".env.production"] + [
            entry["pattern"] for entry in extra if not re.search(r"[*?]", entry["pattern"])
        ]
        configured = hookio.harness_value(cfg, "envCommand")
        self.env_command = configured if isinstance(configured, str) and configured else "ah env"
        self.env_commands = hookio.env_commands(cfg)


def bracket_at(glob: str, i: int) -> tuple[str, int] | None:
    """The bracket expression at `i` of a glob as a regex source for one character, and its last
    index, or None when `[` opens none.

    A POSIX class matches any character: the guard does not model the class, so it reads the
    widest set. So does a class with `*` or `?`, which a brace range inside the brackets
    (`[{m..o}]`) leaves, and a class with a reversed range such as `[e-a]` of program text
    (`["instance-agnostic"]`), which `re` cannot read. The widest set still stands for one
    character, so the word matches no longer name than before.
    """
    found = BRACKET.match(glob, i)
    if not found:
        return None
    body = found.group(0)[1:-1]
    if re.search(r"\[[:=.]|[*?]", body):
        source = "[^/]"
    else:
        inner = re.sub(r"^!", "^", body).replace("\\", "\\\\").replace("]", "\\]")
        # The Node guard built JavaScript classes: `[]` matches nothing and `[^]` matches any
        # character. Python's re refuses both, so they get their meaning spelled out.
        source = {"": "(?!)", "^": r"[\s\S]"}.get(inner, f"[{inner}]")
        try:
            re.compile(source)
        except re.error:
            source = "[^/]"
    return source, i + len(found.group(0)) - 1


def glob_tokens(glob: str) -> list[Token]:
    """The tokens of a glob basename: `*`, or a test of one character (a literal, `?` or a
    class)."""
    out: list[Token] = []
    i = 0
    while i < len(glob):
        c = glob[i]
        bracket = bracket_at(glob, i) if c == "[" else None
        if c == "*":
            out.append("*")
        elif c == "?":
            out.append(lambda x: x != "/")
        elif bracket:
            rx = re.compile(bracket[0])
            out.append(lambda x, rx=rx: rx.fullmatch(x) is not None)  # type: ignore[misc]
            i = bracket[1]
        else:
            out.append(lambda x, c=c: x == c)  # type: ignore[misc]
        i += 1
    return out


def brace_group(word: str) -> tuple[int, int, list[str]] | None:
    """The first brace group of `word` that bash expands, with its parts, or None.

    The scan balances the braces and splits on the top-level commas only, so `{.env,{x}}` gives
    `.env` and `{x}`. A group with `..` (a range such as `{j..l}` or `{+1..3}`) becomes `*`, which
    the glob check reads. Bash does not brace-expand a parameter expansion, so the scan steps over
    each `${...}` whole: `${x:-{a,b}}` stays one word.
    """
    open_at = next_brace(word, 0)
    while open_at >= 0:
        depth = 0
        start = open_at + 1
        parts: list[str] = []
        i = open_at
        while i < len(word):
            if word[i] == "{" and i > 0 and word[i - 1] == "$":
                i = parameter_end(word, i)
            elif word[i] == "{":
                depth += 1
            elif word[i] == "," and depth == 1:
                parts.append(word[start:i])
                start = i + 1
            elif word[i] == "}":
                depth -= 1
                if depth == 0:
                    body = word[open_at + 1 : i]
                    if parts:
                        return open_at, i, [*parts, word[start:i]]
                    if ".." in body:
                        return open_at, i, ["*"]
                    break
            i += 1
        open_at = next_brace(word, open_at + 1)
    return None


def parameter_end(word: str, open_at: int) -> int:
    """The index of the `}` that closes the `${` whose brace is at `open_at`, or the last index."""
    depth = 0
    for i in range(open_at, len(word)):
        if word[i] == "{":
            depth += 1
        elif word[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(word) - 1


def next_brace(word: str, start: int) -> int:
    """The index of the first `{` at or after `start` that can open a brace group, or -1."""
    at = word.find("{", start)
    while at > 0 and word[at - 1] == "$":
        at = word.find("{", parameter_end(word, at) + 1)
    return at


def expand_braces(word: str) -> list[str] | None:
    """Every word that bash makes of `word` by brace expansion. None past 256 words, which the
    caller refuses."""
    out = [word]
    for _ in range(64):
        changed = False
        nxt: list[str] = []
        for w in out:
            group = brace_group(w)
            if not group:
                nxt.append(w)
                continue
            changed = True
            open_at, close, parts = group
            nxt.extend(w[:open_at] + part + w[close + 1 :] for part in parts)
        out = nxt
        if len(out) > 256:
            return None
        if not changed:
            return out
    return None


def can_end_with(tokens: list[Token], suffix: str, longer: bool) -> bool:
    """True when some name that the tokens match ends with `suffix`.

    With `longer`, the name must also hold at least one character before the suffix. The search
    walks the tokens and the suffix from the right, and a `*` takes zero or more characters of
    the suffix.
    """
    seen: set[tuple[int, int]] = set()

    def walk(i: int, k: int) -> bool:
        if (i, k) in seen:
            return False
        seen.add((i, k))
        if k == 0:
            return not longer or i > 0
        if i == 0:
            return False
        token = tokens[i - 1]
        if token == "*":
            return walk(i - 1, k) or walk(i, k - 1)
        assert callable(token)
        return token(suffix[k - 1]) and walk(i - 1, k - 1)

    return walk(len(tokens), len(suffix))


def glob_matches_key(base: str) -> bool:
    """True when a glob basename can expand to a key name of `KEY_BASENAME`."""
    tokens = glob_tokens(base)
    if any(can_end_with(tokens, ext, True) for ext in (".pem", ".key", ".p12", ".pfx")):
        return True
    # A leading `*` can put `id_rsa` in front of any tail, so `*.ts` would match `id_rsa.ts`. Such
    # a glob counts when it matches a private key name: the bare name or a backup of it.
    if tokens and tokens[0] == "*":
        glob = glob_to_regex(base)
        return any(
            glob.fullmatch(name + tail) for name in ("id_rsa", "id_ed25519") for tail in KEY_TAILS
        )
    # A name that starts with a prefix is a reversed name that ends with the reversed prefix.
    reversed_tokens = list(reversed(tokens))
    return any(
        can_end_with(reversed_tokens, start[::-1], False) for start in ("id_rsa", "id_ed25519")
    )


def decode_escape(esc: str) -> str:
    """One escape of `$'...'` decoded. A code point past U+10FFFF or a surrogate gives U+FFFD."""
    if esc[0] in "xuU":
        point = int(esc[1:], 16)
        return "�" if point > 0x10FFFF or 0xD800 <= point <= 0xDFFF else chr(point)
    if esc[0] in "01234567":
        return chr(int(esc, 8) & 0xFF)
    if esc.startswith("c"):
        # A `\c` at the end of the string has no character to control. Node read it as NUL.
        return chr(ord(esc[1]) & 0x1F) if len(esc) > 1 else "\x00"
    return ANSI_C_LETTERS.get(esc, esc)


def decode_ansi_c(text: str) -> str:
    """The text of each `$'...'` with its escapes decoded, so `$'\\x2eenv'` reads as `.env`.

    A quote or a backslash in the decoded text is literal data to bash, so it becomes `_` and
    cannot change where the guard splits words.
    """

    def decode(found: re.Match[str]) -> str:
        body = ANSI_C_ESCAPE.sub(lambda m: decode_escape(m.group(1)), found.group(1))
        return re.sub(r"['\"\\]", "_", body)

    return ANSI_C.sub(decode, text)


def exempt_shape(args: list[str], target: str | None) -> bool:
    """True when the words, without the heredoc and its one stdout redirection, are an exempt
    shape: `cat` alone, or `git commit|tag ... -F -` with only the flags of `GIT_FLAGS` (and the
    tag name)."""
    if len(args) == 1 and args[0] == "cat":
        return True
    if len(args) < 2:
        return False
    git, verb, *rest = args
    if git != "git" or target is not None or verb not in GIT_FLAGS:
        return False
    if "-F" not in rest:
        return False
    at = rest.index("-F")
    if at + 1 >= len(rest) or rest[at + 1] != "-":
        return False
    others = rest[:at] + rest[at + 2 :]
    names = [a for a in others if a not in GIT_FLAGS[verb]]
    if verb == "commit":
        return not names
    return len(names) == 1 and TAG_NAME.fullmatch(names[0]) is not None


def scanned_text(command: str) -> str:
    """The text this guard scans: the whole command, or only its first line when the command is
    one exempt heredoc.

    A heredoc body is data: a scan of it would deny a commit whose message names `grep` and a
    file. The exemption is an allowlist of exact shapes, listed in docs/hooks.md ("Heredocs and
    key names in the read guard"). Anything else on the line gets every byte scanned. The body
    ends at the first line that equals the delimiter, and only blanks may follow it. An unquoted
    body with a `$` or a backtick is scanned, because bash expands it.
    """
    newline = command.find("\n")
    if newline < 0:
        return command
    line = command[:newline]
    if not LINE_CHARS.fullmatch(line):
        return command
    parts = [w for w in re.split(r"[ \t]+", line) if w != ""]
    args: list[str] = []
    delimiter: str | None = None
    quoted = False
    target: str | None = None
    k = 0
    while k < len(parts):
        part = parts[k]
        if part.startswith("<<"):
            if delimiter is not None:
                return command
            if len(part) > 2:
                word = part[2:]
            else:
                k += 1
                word = parts[k] if k < len(parts) else ""
            in_quotes = re.fullmatch(r"'([^']*)'", word)
            delimiter = in_quotes.group(1) if in_quotes else word
            quoted = in_quotes is not None
            if not DELIMITER.fullmatch(delimiter):
                return command
        elif part.startswith(">"):
            if target is not None:
                return command
            if len(part) > 1:
                target = part[1:]
            else:
                k += 1
                target = parts[k] if k < len(parts) else ""
            plain = RELATIVE_PATH.fullmatch(target) and not SCRIPT_NAME.search(target)
            if target != "/dev/null" and not plain:
                return command
        elif re.search(r"[<>']", part):
            return command
        else:
            args.append(part)
        k += 1
    if delimiter is None or not exempt_shape(args, target):
        return command
    lines = command[newline + 1 :].split("\n")
    if delimiter not in lines:
        return command
    end = lines.index(delimiter)
    if any(rest.strip() != "" for rest in lines[end + 1 :]):
        return command
    if not quoted and re.search(r"[$`]", "\n".join(lines[:end])):
        return command
    return line


def without_quoted_bodies(text: str) -> str:
    """`text` with the heredoc body blanked when the whole command is `DATA_HEREDOC`, or when
    the first line opens the body of a program that expands no glob (`program_body_end`).

    Bash expands nothing in a body with a quoted delimiter, so no glob in it reaches the shell.
    Only `cat` or `tee` reading the body as data, with at most one written file and a pipe into
    a filter, blanks it, and only when each command after the body is inert (`inert_tail`).
    Any other step could run the written file or read the body as file names
    (`git ... --pathspec-from-file=-`), so every other text stays whole.
    """
    lines = text.split("\n")
    shape = DATA_HEREDOC.fullmatch(lines[0])
    if shape is None:
        end = program_body_end(lines)
        return text if end is None else "\n".join([lines[0], *[""] * (end - 1), *lines[end:]])
    tabs = "<<-" in lines[0]
    end = next(
        (
            k
            for k in range(1, len(lines))
            if (lines[k].lstrip("\t") if tabs else lines[k]) == shape.group(2)
        ),
        None,
    )
    if end is None or not inert_tail("\n".join(lines[end + 1 :])):
        return text
    return "\n".join([lines[0], *[""] * (end - 1), *lines[end:]])


def inert_tail(text: str) -> bool:
    """True when each command of `text` is a bare name of `INERT_COMMANDS`, with no substitution,
    heredoc, group, loop or other word of `CONTROL_WORDS`. No such step runs a written file."""
    if re.search(r"\$\(|`|<\(|>\(|<<", text):
        return False
    spans = word_spans(text)
    if any(w.text in CONTROL_WORDS or (w.operator and w.text in "(){}") for w in spans):
        return False
    return all(
        not words or words[0].text in INERT_COMMANDS for words, _, _ in simple_commands(spans)
    )


def program_body_end(lines: list[str]) -> int | None:
    """The line of the closing delimiter when the first line ends in `python3 - [args] <<'D'` or
    `node - [args] <<'D'`, and the body holds no word of `PROGRAM_GLOB_RUNNERS`. Else None.

    The program reads its code from the body, and it expands a glob only through a glob or match
    function or through a shell that it starts. The literal name scan still reads the body, and
    the lines after the delimiter are commands like any other.
    """
    opening = re.search(r"<<\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1\s*$", lines[0])
    if opening is None or lines[0].count("<") != 2:
        return None
    head = word_spans(lines[0][: opening.start()])
    stage = head[max((k + 1 for k, w in enumerate(head) if w.operator), default=0) :]
    if len(stage) < 2 or stage[1].text != "-" or stage[0].text not in PROGRAM_GLOB_RUNNERS:
        return None
    if lines[0][stage[0].start : stage[0].end] != stage[0].text:
        return None
    delimiter = opening.group(2)
    end = next((k for k in range(1, len(lines)) if lines[k] == delimiter), None)
    if end is None or PROGRAM_GLOB_RUNNERS[stage[0].text].search("\n".join(lines[1:end])):
        return None
    return end


class Word(NamedTuple):
    """One word of the command line and where it sits in the raw text."""

    text: str
    start: int
    end: int
    # The raw word holds a `*`, `?` or `[` outside quotes and not escaped, which bash expands.
    open_glob: bool
    # A shell operator such as `|` or `;`, not a quoted or escaped `|` or `;`.
    operator: bool = False


def word_spans(text: str) -> list[Word]:
    """Every word of the command line, quoting honoured, each shell operator its own word."""
    out: list[Word] = []
    current = ""
    quoted = False
    open_glob = False
    start: int | None = None
    quote: str | None = None

    def flush(end: int) -> None:
        nonlocal current, quoted, open_glob, start
        if current != "" or quoted:
            out.append(Word(current, end if start is None else start, end, open_glob))
        current = ""
        quoted = False
        open_glob = False
        start = None

    i = 0
    while i < len(text):
        char = text[i]
        if quote is None and start is None and char not in OPERATORS and not char.isspace():
            start = i
        if quote is not None:
            if char == quote:
                quote = None
            elif quote == '"' and char == "\\" and i + 1 < len(text):
                i += 1
                current += text[i]
            else:
                current += char
        elif char in ("'", '"'):
            quote = char
            quoted = True
        elif char == "\\" and i + 1 < len(text):
            i += 1
            current += text[i]
        elif char in OPERATORS:
            flush(i)
            out.append(Word(char, i, i + 1, False, True))
        elif re.match(r"\s", char):
            flush(i)
        else:
            open_glob = open_glob or char in "*?["
            current += char
        i += 1
    flush(len(text))
    return out


def words(text: str) -> list[str]:
    """Every word of the command line, quoting honoured, each shell operator its own word."""
    return [word.text for word in word_spans(text)]


def script_operands(args: list[Word], options: ScriptOptions) -> list[Word]:
    """The words of `args` that a grep, sed or rg run reads as a script, not as a path.

    That is the value of each script option (`-e`), else the first operand. An unknown option
    gives no word, so a value that the guard cannot place is never read as a script.
    """
    scripts: list[Word] = []
    operands: list[Word] = []
    script_from_option = False
    options_end = False
    i = 0
    while i < len(args):
        word = args[i]
        text = word.text
        if options_end or text == "-" or not text.startswith("-"):
            operands.append(word)
        elif text == "--":
            options_end = True
        elif text.startswith("--"):
            name, inline, _ = text.partition("=")
            if name in options.long_flags:
                pass
            elif name in options.long_values | options.long_scripts | options.long_script_files:
                script_from_option = script_from_option or name not in options.long_values
                value = word if inline else (args[i + 1] if i + 1 < len(args) else None)
                if not inline:
                    i += 1
                if name in options.long_scripts and value is not None:
                    scripts.append(value)
            else:
                return []
        else:
            for at, letter in enumerate(text[1:], start=1):
                if letter in options.flags:
                    continue
                if letter not in options.values + options.scripts + options.script_files:
                    return []
                script_from_option = script_from_option or letter not in options.values
                attached = at + 1 < len(text)
                value = word if attached else (args[i + 1] if i + 1 < len(args) else None)
                if not attached:
                    i += 1
                if letter in options.scripts and value is not None:
                    scripts.append(value)
                break
        i += 1
    if not script_from_option and operands:
        scripts.append(operands[0])
    return scripts


def stage_patterns(stage: list[Word]) -> tuple[list[Word], list[str]]:
    """The words of one stage that its command reads as a pattern or a script, not as a path,
    and the name patterns that pick the files of a `find`.

    A `find` pattern after `-not` or `!` drops names, so it is a pattern, when the expression has
    no join, no group and no action but a trailing `-print`. Any other `find` pattern picks names
    that a pipe to `xargs cat` or a `-exec cat` reads, so it stays a pick.
    """
    while stage and ASSIGNMENT.match(stage[0].text):
        stage = stage[1:]
    if not stage:
        return [], []
    name, args = basename(stage[0].text), stage[1:]
    if name == "find":
        # A join, a group or an action can turn a dropped name into a read one.
        texts = [a.text for a in args]
        if texts and texts[-1] in FIND_DEFAULT_ACTIONS:
            texts.pop()
        plain = not any(t in FIND_JOINS or t in FIND_OUTPUTS or t in ("(", ")") for t in texts)
        patterns: list[Word] = []
        picks: list[str] = []
        for i in range(len(args) - 1):
            if args[i].text not in FIND_PATTERNS:
                continue
            negated = i > 0 and args[i - 1].text in FIND_NOT
            twice = i > 1 and args[i - 2].text in FIND_NOT
            if plain and negated and not twice:
                patterns.append(args[i + 1])
            else:
                # `-iname` and the other `-i` tests ignore case, and every credential name is
                # lower case.
                pick = args[i + 1].text
                picks.append(pick.lower() if args[i].text.startswith("-i") else pick)
        return patterns, picks
    if name == "git":
        verb = git_subcommand(args)
        if verb is not None and args[verb].text in REF_VERBS:
            return ref_patterns(args[verb + 1 :]), []
        excludes, picks = git_pathspecs(args)
        return excludes + search_scripts(stage), picks
    return search_scripts(stage), []


def search_scripts(stage: list[Word]) -> list[Word]:
    """The words of one stage that a grep, rg, `git grep` or sed run reads as its script.

    The tool matches a script against lines, so a name in it names no file. A sed script counts
    only when `sed_script_words` can vouch for it.
    """
    while stage and ASSIGNMENT.match(stage[0].text):
        stage = stage[1:]
    if not stage:
        return []
    name, args = basename(stage[0].text), stage[1:]
    if name == "git":
        verb = git_subcommand(args)
        if verb is None or args[verb].text != "grep":
            return []
        return script_operands(args[verb + 1 :], GIT_GREP_OPTIONS)
    if name == "sed":
        found = sed_script_words([a.text for a in args])
        return [] if found is None else [args[k] for k in found[0]]
    options = SCRIPT_TOOLS.get(name)
    return [] if options is None else script_operands(args, options)


def git_subcommand(args: list[Word]) -> int | None:
    """The index of the git subcommand in `args`, past the global options and their values."""
    k = 0
    while k < len(args) and args[k].text.startswith("-"):
        k += 2 if args[k].text in GIT_VALUE_OPTIONS else 1
    return k if k < len(args) else None


def ref_patterns(args: list[Word]) -> list[Word]:
    """The quoted words after `git branch`, `git tag` or `git for-each-ref`: ref patterns.

    A message file of `git tag -F` or `--file` is a path, so a stage with one gives no word.
    """
    if any(re.match(r"-F|--file", a.text) for a in args):
        return []
    return [a for a in args if not a.text.startswith("-")]


def name_filters(stage: list[Word], names: Names) -> list[Word]:
    """The name filter values of a grep or rg stage that the glob scan skips.

    A filter drops files from a recursive search, and the same search with no filter reads every
    file, so a filter reads nothing more. A filter that names a credential family by its literal
    part (`*.pem`, `.env*`) or the start of a guarded name (`vault*`), or that holds a bracket
    class, still counts. An unquoted `--include=*` is safe: every name that bash makes of it
    starts with `--include=`.
    """
    if not stage or stage[0].text not in SCRIPT_TOOLS:
        return []
    found: list[Word] = []
    args = stage[1:]
    for k, word in enumerate(args):
        name, inline, value = word.text.partition("=")
        if inline and name in FILTER_OPTIONS:
            found.extend([word] if broad_filter(value, names) else [])
        elif word.text in FILTER_OPTIONS and k + 1 < len(args):
            value_word = args[k + 1]
            if not value_word.open_glob and broad_filter(value_word.text, names):
                found.append(value_word)
    return found


def broad_filter(glob: str, names: Names) -> bool:
    """True when the name filter `glob` names no credential family by its literal part, and its
    literal start (`vault` of `vault*`) starts no guarded name."""
    start = re.split(r"[*?]", basename(glob))[0]
    if "[" in glob or (
        re.search(r"[A-Za-z0-9]", start)
        and any(basename(t).startswith(start) for t in names.glob_targets)
    ):
        return False
    for fill in (re.sub(r"[*?]", letter, glob) for letter in ("", "x")):
        base = basename(fill)
        if base.endswith(".example"):
            continue
        if SECRET_BASENAME.fullmatch(base) or KEY_BASENAME.fullmatch(base):
            return False
    return True


def sed_script_words(args: list[str]) -> tuple[list[int], bool] | None:
    """The indexes of the script words of a sed stage, and True when `-i` edits in place.

    None when an option is unknown, a script comes from a file (`-f`), or a script is not plain
    (see `plain_sed_script`).
    """
    scripts: list[int] = []
    operands: list[int] = []
    from_option = False
    in_place = False
    k = 0
    while k < len(args):
        text = args[k]
        if text == "--" or not text.startswith("-") or text == "-":
            operands.append(k)
        elif text in ("-e", "--expression"):
            from_option = True
            scripts.append(k + 1)
            k += 1
        elif text.startswith("--expression="):
            return None
        elif text.startswith("--in-place") or re.fullmatch(r"-[nErsuz]*i.*", text):
            in_place = True
        elif text not in SED_LONG_FLAGS and not re.fullmatch(r"-[nErsuz]+", text):
            return None
        k += 1
    operands = [k for k in operands if args[k] != "--"]
    if not from_option:
        scripts = operands[:1]
    if not scripts or any(k >= len(args) or not plain_sed_script(args[k]) for k in scripts):
        return None
    return scripts, in_place


def plain_sed_script(script: str) -> bool:
    """True when every sed command of `script` is `s`, `y`, `p`, `d`, `q`, `=`, `n` or `N`, with
    plain addresses and with `g`, `p`, `i`, `I`, `m`, `M` or a number as `s` flags.

    No such command runs a program, reads a file or writes one: `e`, `r`, `w`, the `e` and `w`
    flags of `s`, a block and a label are out.
    """
    at = 0
    while True:
        at = skip(script, at, " \t\n;")
        if at >= len(script):
            return True
        found = sed_address(script, at)
        if found is None:
            return False
        at = skip(script, found, " \t")
        if script.startswith("!", at):
            at = skip(script, at + 1, " \t")
        found = sed_command(script, at)
        if found is None:
            return False
        at = skip(script, found, " \t")
        if at < len(script) and script[at] not in ";\n":
            return False


def skip(text: str, at: int, chars: str) -> int:
    while at < len(text) and text[at] in chars:
        at += 1
    return at


def sed_address(script: str, at: int) -> int | None:
    """The end of the optional address range of a sed command that starts at `at`."""
    end = sed_one_address(script, at)
    if end is None or not script.startswith(",", end):
        return end
    step = re.match(r"[+~]\d+", script[end + 1 :])
    return end + 1 + len(step.group(0)) if step else sed_one_address(script, end + 1)


def sed_one_address(script: str, at: int) -> int | None:
    """The end of one sed address at `at`: a number, `$`, `/re/` or `\\%re%`, or no address."""
    number = re.match(r"\d+(~\d+)?|\$", script[at:])
    if number:
        return at + len(number.group(0))
    if script.startswith("\\", at) and at + 1 < len(script):
        end = sed_delimited(script, at + 2, script[at + 1])
    elif script.startswith("/", at):
        end = sed_delimited(script, at + 1, "/")
    else:
        return at
    return None if end is None else skip(script, end, "IM")


def sed_delimited(script: str, at: int, delimiter: str) -> int | None:
    """The index after the next `delimiter` from `at` that no backslash escapes."""
    while at < len(script):
        if script[at] == "\\":
            at += 2
            continue
        if script[at] == "\n":
            return None
        if script[at] == delimiter:
            return at + 1
        at += 1
    return None


def sed_command(script: str, at: int) -> int | None:
    """The end of one plain sed command at `at`, or None for any other command."""
    if at >= len(script):
        return None
    letter = script[at]
    if letter in "pdq=nN":
        return skip(script, at + 1, "0123456789") if letter == "q" else at + 1
    if letter not in "sy" or at + 1 >= len(script) or script[at + 1] in "\\\n":
        return None
    delimiter = script[at + 1]
    end = sed_delimited(script, at + 2, delimiter)
    end = None if end is None else sed_delimited(script, end, delimiter)
    if end is None:
        return None
    return skip(script, end, "gpiImM0123456789") if letter == "s" else end


def git_pathspecs(args: list[Word]) -> tuple[list[Word], list[str]]:
    """The exclude pathspecs of a git stage, and the quoted pathspecs that pick names.

    git matches a quoted pathspec itself, and its `*` and `?` also match a name that starts with
    `.`. An exclude pathspec drops names, so it reads no file. Without `--`, git reads each word
    after the subcommand that is no option as a pathspec, except the pattern of `git grep`.
    """
    texts = [a.text for a in args]
    excludes = [a for a in args if not a.open_glob and is_exclude_pathspec(a.text)]
    candidates = args[texts.index("--") + 1 :] if "--" in texts else git_operands(args)
    picks = [
        form
        for a in candidates
        if not a.open_glob and a not in excludes and re.search(r"[*?[]", a.text)
        for form in pathspec_forms(a.text)
    ]
    return excludes, picks


def pathspec_forms(text: str) -> list[str]:
    """The pattern of a pathspec without its magic, as written and in lower case.

    The `icase` magic, `--icase-pathspecs` and `GIT_ICASE_PATHSPECS` fold case, so the name
    test reads both forms: folding can change a class such as `[!A-Z]`.
    """
    pattern = MAGIC_PREFIX.sub("", text, count=1)
    return [pattern, pattern.lower()]


def is_exclude_pathspec(text: str) -> bool:
    """True for a pathspec with the short exclude magic, or with `exclude` in its long magic."""
    long = LONG_MAGIC.match(text)
    if long is not None:
        return "exclude" in (entry.strip() for entry in long.group(1).split(","))
    return EXCLUDE_SHORT_MAGIC.match(text) is not None


def git_operands(args: list[Word]) -> list[Word]:
    """The words after the git subcommand that are no option.

    The global options before the subcommand and their values are skipped. The pattern of
    `git grep` counts too: telling it from a pathspec needs every option form of git, and a
    pattern that is no glob of a credential name passes the name test anyway.
    """
    k = 0
    while k < len(args) and args[k].text.startswith("-"):
        k += 2 if args[k].text in GIT_VALUE_OPTIONS else 1
    return [a for a in args[k + 1 :] if not a.text.startswith("-")]


@dataclass
class Blanks:
    """The spans of a command that a scan skips, and the name patterns of each `find`.

    `glob` is for the glob scan and `named` for the literal name scan.
    """

    glob: list[tuple[int, int]]
    named: list[tuple[int, int]]
    picks: list[str]

    def shifted(self, by: int) -> Blanks:
        return Blanks(
            [(a + by, b + by) for a, b in self.glob],
            [(a + by, b + by) for a, b in self.named],
            self.picks,
        )


def blanked(text: str, spans: list[tuple[int, int]]) -> str:
    """`text` with every character of `spans` but a newline turned into a blank."""
    chars = list(text)
    for start, end in spans:
        for k in range(start, end):
            if chars[k] != "\n":
                chars[k] = " "
    return "".join(chars)


def span(word: Word) -> tuple[int, int]:
    return word.start, word.end


def without_patterns(text: str, names: Names) -> tuple[str, list[str]]:
    """`text` with each word that the glob scan skips blanked, and the name patterns of each
    `find` (see `command_blanks`)."""
    blanks = command_blanks(text, names)
    return blanked(text, blanks.glob), blanks.picks


def command_blanks(text: str, names: Names, inner: bool = False) -> Blanks:
    """The words of `text` that read no file by their name or their glob.

    A quoted pattern is a dropped `find` name, an exclude pathspec, a ref pattern of git or a
    script of grep, rg, `git grep` or a plain sed. Bash does not expand it, and the tool matches
    it against names or lines. A pattern with a glob character outside quotes stays, because
    bash expands it to file names first. A broad name filter of grep or rg and each word of a
    stage of `name_only_stages` are skipped too. A script and a stage of names also skip the
    literal name scan. A heredoc body is no command line, and a `$(...)` inside double quotes
    is one (`inner`).
    """
    masked = heredoc_masked(text)
    spans = word_spans(masked)
    listings = set() if inner else {w[0].start for w in name_only_stages(spans, masked)}
    out = Blanks([], [], [])
    stage: list[Word] = []
    for word in [*spans, None]:
        if word is not None and not word.operator:
            stage.append(word)
            continue
        if stage and stage[0].start in listings:
            keep = [w for w in stage[1:] if stage[0].text == "ls" or not w.open_glob]
            out.glob.extend(map(span, keep))
            out.named.extend(map(span, keep))
        else:
            patterns, picks = stage_patterns(stage)
            out.glob.extend(span(w) for w in patterns if not w.open_glob)
            out.glob.extend(map(span, name_filters(stage, names)))
            out.named.extend(span(w) for w in search_scripts(stage) if not w.open_glob)
            out.picks.extend(picks)
        stage = []
    for start, end in substitutions(masked):
        found = command_blanks(masked[start:end], names, inner=True).shifted(start)
        out.glob.extend(found.glob)
        out.named.extend(found.named)
        out.picks.extend(found.picks)
    return out


# A heredoc operator and its delimiter. `<<<` is a here string, and `$((1<<3))` a shift.
HEREDOC_OPEN = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)\\?([A-Za-z_][A-Za-z0-9_]*)\2")


def heredoc_masked(text: str) -> str:
    """`text` with each heredoc body and its closing delimiter blanked, newlines kept.

    A body line is data or a program, never a command line of this shell. A body that never
    closes blanks every line after it, which makes fewer words patterns.
    """
    out: list[str] = []
    pending: list[tuple[str, bool]] = []
    for line in text.split("\n"):
        if pending:
            delimiter, tabs = pending[0]
            if (line.lstrip("\t") if tabs else line) == delimiter:
                pending.pop(0)
            out.append(" " * len(line))
            continue
        out.append(line)
        pending.extend((m.group(3), m.group(1) == "-") for m in HEREDOC_OPEN.finditer(line))
    return "\n".join(out)


def substitutions(text: str, backticks: bool = False) -> list[tuple[int, int]]:
    """The inner span of each outermost `$(...)` of `text` that is not in single quotes, and of
    each backtick substitution with `backticks`."""
    out: list[tuple[int, int]] = []
    quote: str | None = None
    at = 0
    while at < len(text):
        char = text[at]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == "\\":
            at += 1
        elif char == "'" and quote is None:
            quote = "'"
        elif char == '"':
            quote = None if quote == '"' else '"'
        elif text.startswith("$(", at) and not text.startswith("$((", at):
            end = closing_paren(text, at + 2)
            if end is None:
                return out
            out.append((at + 2, end))
            at = end
        elif backticks and char == "`":
            end = text.find("`", at + 1)
            end = len(text) if end < 0 else end
            out.append((at + 1, end))
            at = end
        at += 1
    return out


def closing_paren(text: str, at: int) -> int | None:
    """The index of the `)` that closes a `$(` whose body starts at `at`, quotes honoured."""
    depth = 1
    quote: str | None = None
    while at < len(text):
        char = text[at]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == "\\":
            at += 1
        elif quote == '"':
            quote = None if char == '"' else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return at
        at += 1
    return None


# The words that make a later command see the output of a listing: a loop, a group, a condition,
# a function, `coproc`, and `exec` and `eval`, which redirect or rerun the rest of the line.
CONTROL_WORDS = {
    *("for", "while", "until", "select", "if", "case", "do", "done", "then", "else", "elif"),
    *("fi", "esac", "function", "coproc", "exec", "eval"),
}
# The redirections that leave the output of a listing on its way to the terminal.
QUIET_REDIRECTS = {"2>/dev/null", ">/dev/null", "1>/dev/null", "2>&1"}
# The actions of `find` that run, write or delete. Without one, `find` prints names.
FIND_WRITES = {"-fprint", "-fprint0", "-fprintf", "-fls", "-delete", *FIND_ACTIONS}


def name_only_stages(spans: list[Word], text: str) -> list[list[Word]]:
    """The `ls` and `find` stages whose names reach only the terminal.

    `ls` prints names, and `find` with no action of `FIND_WRITES` prints names. A stage counts
    when its output, and the output of each `head`, `tail`, `grep`, `wc` or `cat` that a pipe
    feeds it into, goes to the terminal or `/dev/null`. A substitution, a group, a loop, `$_`,
    `exec`, a redirection to a file and `--files0-from` can carry a name on to a reader, so any
    of them anywhere in the command gives no stage.
    """
    if re.search(r"\$\(|`|<\(|>\(|\$\{?_(?![A-Za-z0-9_])|&>|>\||<>", text):
        return []
    if any(w.text in CONTROL_WORDS or (w.operator and w.text in "(){}") for w in spans):
        return []
    commands = simple_commands(spans)
    found: list[list[Word]] = []
    for k, (words, _redirects, _joiner) in enumerate(commands):
        if not words or words[0].text not in ("ls", "find"):
            continue
        if words[0].text == "find" and any(w.text in FIND_WRITES for w in words):
            continue
        if names_reach_terminal(commands, k):
            found.append(words)
    return found


Command = tuple[list[Word], list[str], str]


def names_reach_terminal(commands: list[Command], at: int) -> bool:
    """True when the output of command `at` and of each filter a pipe feeds reach the terminal."""
    while True:
        words, redirects, joiner = commands[at]
        if any(r not in QUIET_REDIRECTS for r in redirects):
            return False
        filtered = joiner == "|" and at + 1 < len(commands)
        if not filtered:
            return joiner in ("", ";", "\n", "&&", "||", "&")
        at += 1
        words = commands[at][0]
        if not words or words[0].text not in LISTING_FILTERS:
            return False
        if any("files0" in w.text for w in words):
            return False


def simple_commands(spans: list[Word]) -> list[Command]:
    """The words, the redirections and the joiner after each simple command of `spans`.

    A redirection is one text such as `2>/dev/null` or `2>&1`. A joiner is `|`, `||`, `&&`,
    `|&`, `;`, `&`, a newline, or empty at the end.
    """
    out: list[Command] = []
    words: list[Word] = []
    redirects: list[str] = []
    k = 0
    while k < len(spans):
        word = spans[k]
        if not word.operator:
            words.append(word)
            k += 1
            continue
        if word.text in "<>":
            number = words and words[-1].text.isdigit() and words[-1].end == word.start
            redirect = words.pop().text if number else ""
            redirect += word.text
            k += 1
            while k < len(spans) and spans[k].operator and spans[k].text in "<>&":
                redirect += spans[k].text
                k += 1
            if k < len(spans) and not spans[k].operator:
                redirect += spans[k].text
                k += 1
            redirects.append(redirect)
            continue
        joiner = word.text
        k += 1
        if joiner in "|&" and k < len(spans) and spans[k].text in "|&" and spans[k].operator:
            joiner += spans[k].text
            k += 1
        out.append((words, redirects, joiner))
        words, redirects = [], []
    out.append((words, redirects, ""))
    return out


def basename(path: str) -> str:
    return path[path.rfind("/") + 1 :]


def glob_to_regex(glob: str) -> re.Pattern[str]:
    """A shell glob as a whole-name regex: `*`, `?` and a `[...]` class; everything else literal.

    Match it with `fullmatch`.
    """
    out = ""
    i = 0
    while i < len(glob):
        c = glob[i]
        bracket = bracket_at(glob, i) if c == "[" else None
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif bracket:
            out += bracket[0]
            i = bracket[1]
        else:
            out += re.escape(c)
        i += 1
    return re.compile(out)


def remove_quotes(raw: str) -> str:
    """The command after bash quote removal.

    A quote of the other kind inside quotes is a literal character, and so is a backslash
    inside single quotes. So `'"[^"]*"'` keeps its double quotes, and the class stays
    `[^"]`, which matches no credential name. Inside double quotes, a backslash escapes only
    `$`, a backtick, `"`, `\\` and a newline. With a quote that never closes, every quote and
    backslash goes, as the guard read the command before: that reads the widest set of names.
    """
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(raw):
        char = raw[i]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                out.append(char)
        elif quote == '"':
            if char == '"':
                quote = None
            elif char == "\\" and i + 1 < len(raw) and raw[i + 1] in '$`"\\\n':
                i += 1
                out.append(raw[i])
            else:
                out.append(char)
        elif char in ("'", '"'):
            quote = char
        elif char == "\\":
            if i + 1 < len(raw):
                i += 1
                out.append(raw[i])
        else:
            out.append(char)
        i += 1
    if quote is not None:
        return re.sub(r"['\"\\]", "", raw)
    return "".join(out)


def expanded_words(raw: str) -> tuple[list[tuple[str, bool]], str | None]:
    """The blank-separated words of `raw` after quote removal and brace expansion.

    Each word comes with True when a brace group made it. The second value is a word that
    expands past 256 words, which the caller refuses.

    Quotes and backslashes go first, so `'provid''ers.json'` reads as bash reads it. Braces are
    expanded next, so `box.{pem,x}` reads as `box.pem box.x`.
    """
    out: list[tuple[str, bool]] = []
    for word in re.split(r"\s+", remove_quotes(raw)):
        expanded = expand_braces(word)
        if expanded is None:
            return out, word
        out.extend((w, expanded != [word]) for w in expanded)
    return out, None


def names_a_secret(
    raw: str,
    named: str,
    cwd: str,
    keys_by_name: bool,
    names: Names,
    globbed: str,
    picks: list[str],
    dotglob: bool,
) -> str | None:
    """The first path in the command that names a credential file, or None.

    Split on punctuation rather than taken from the parsed words, so a path inside a `node -e`
    script body is seen too. A `.example` is the committed reference and is never a secret.
    The literal name scan reads `named`, the command without the words that `command_blanks`
    skips for it. The glob scan reads `globbed` (the command without its quoted patterns) as
    bash expands it, and each `find` pattern of `picks` as `find` matches it. With `dotglob`, a
    glob matches a name that starts with `.` as `find` does.
    """
    found, too_many = expanded_words(named)
    if too_many is not None:
        return too_many
    for word, from_braces in found:
        for fragment in NOT_PATH.split(word):
            # `curl -d @.env` names the file after an `@`.
            fragment = fragment.lstrip("@")
            if fragment == "":
                continue
            # A brace group is shell syntax, never script text, so its key name counts by name.
            keys = (
                keys_by_name
                or from_braces
                or "/" in fragment
                or os.path.exists(os.path.join(cwd, fragment))
            )
            if is_credential(fragment, names, keys):
                return fragment
    found, too_many = expanded_words(globbed)
    if too_many is not None:
        return too_many
    # The trust test reads the raw command: a pattern word that `globbed` blanks can still hold a
    # substitution that runs before the glob expands.
    clean = {} if dotglob or not listing_trusted(raw) else clean_globs(globbed, cwd, names)
    command = " ".join(word for word, _ in found)
    # Split on the shell operators too, so `*.pem;` and `$(cat *.pem)` give the glob alone. The
    # blank-split words stay too, so a bracket that holds an operator (`id_[r;]sa`) is read whole.
    # Program text puts punctuation around a glob word (`['cat *.pem'],`), and a shell that the
    # program starts expands the word alone, so the pieces without that punctuation count too.
    shell_pieces = [*re.split(r"\s+", command), *SHELL_SPLIT.split(command)]
    program_pieces = [p for s in shell_pieces for p in PROGRAM_SPLIT.split(s) if p != s]
    for piece in [*shell_pieces, *program_pieces]:
        # The `*` and `?` of `$*` and `$?` are no glob, so the test runs without the expansions.
        # In a glob word an expansion that stands for any text reads as `*`: `${p}id_rsa?` is
        # `*id_rsa?`. A `${...}` keeps the text after its name and subscript, so the glob of
        # `${x:-*.pem}` counts and the subscript of `${PIPESTATUS[0]}` does not.
        # `glob_reads_secret` tests the base name, so only a glob character there counts: a
        # bracket in a folder, as in `/\[x/,$p`, leaves the base name `,$p` with no glob. A
        # `${...}` turns into `*` there, and the `/` of its text, as in `${x:-*/}id_rsa`, does
        # not split it, so a piece with `${` is tested whole.
        probe = NUMBER_PARAM.sub("", piece)
        probe = re.sub(r"\$[0-9*@-]", "", probe)
        probe = PARAMETER.sub(r"\1", probe)
        probe = re.sub(r"\$\{([^}]*)\}", r"\1", probe)
        probe = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "", probe)
        if not re.search(r"[*?\[]", probe if "${" in piece else basename(probe)):
            continue
        if glob_reads_secret(piece, names, dotglob) and not clean.get(piece, False):
            return piece
    # `find` matches a name that starts with `.` to any pattern.
    return next((pick for pick in picks if glob_reads_secret(pick, names, True)), None)


def is_credential(fragment: str, names: Names, keys: bool) -> bool:
    """True when the path `fragment` names a credential file. A key name counts with `keys`."""
    if ".aws/credentials" in fragment or SESSION_PATH.search(fragment):
        return True
    base = basename(fragment)
    # A sentence can end in a `.example` name, and its full stop is no part of the name.
    if base.rstrip(".").endswith(".example"):
        return False
    if SECRET_BASENAME.fullmatch(base) or any(rx.fullmatch(base) for _, rx in names.extra):
        return True
    return keys and KEY_BASENAME.fullmatch(base) is not None


def glob_dirs(text: str, cwd: str) -> list[tuple[str, str | None]]:
    """Each unquoted glob word of `text`, with the directory that bash expands it in.

    The directory follows each step `cd <literal path>` that stands alone between `;`, `&&` and
    newlines. It is None after a `cd` that does not exist, and for every word when the text
    runs a `cd`, `pushd` or `popd` in another form, such as in a subshell.
    """
    moves = sum(1 for word in command_words(text) if basename(word) in ("cd", "pushd", "popd"))
    steps: list[tuple[list[Word], str]] = [([], "")]
    for word in word_spans(text):
        if not word.operator:
            if steps[-1][1]:
                steps.append(([], ""))
            steps[-1][0].append(word)
        else:
            steps[-1] = (steps[-1][0], steps[-1][1] + word.text)
    followed = 0
    directory: str | None = cwd
    out: list[tuple[str, str | None]] = []
    before = ""
    for step, after in steps:
        plain = all(re.fullmatch(r"(&&|[;\n])*", joiner) for joiner in (before, after))
        if plain and len(step) == 2 and step[0].text == "cd" and literal_path(step[1]):
            followed += 1
            path = os.path.normpath(os.path.join(directory, step[1].text)) if directory else None
            directory = path if path and os.path.isdir(path) else None
        out.extend((w.text, directory) for w in step if w.open_glob)
        before = after
    if followed != moves:
        return [(word, None) for word, _ in out]
    return out


def listing_trusted(text: str) -> bool:
    """True when no step of `text` can change a folder or run a program before a glob expands.

    Every step between `;`, `&&` and newlines is `cd <literal path>` or a command of
    `LISTING_READERS`, and each pipe feeds a command of `LISTING_FILTERS`. A substitution, a
    process substitution, a heredoc, a background `&`, `||`, a group, an assignment and any
    redirection but `>/dev/null` and `2>/dev/null` make it False.
    """
    if re.search(r"\$\(|`|<\(|>\(|<<", text):
        return False
    spans = word_spans(text)
    stage: list[str] = []
    filters = False
    k = 0
    while k <= len(spans):
        span = spans[k] if k < len(spans) else None
        if span is not None and not span.operator:
            stage.append(span.text)
            k += 1
            continue
        if stage and not stage_trusted(stage, filters):
            return False
        joiner = "" if span is None else span.text
        nxt = spans[k + 1] if k + 1 < len(spans) else None
        if joiner == ">":
            if nxt is None or nxt.operator or nxt.text != "/dev/null":
                return False
            if stage and stage[-1] == "2" and spans[k - 1].end == span.start:
                stage.pop()
            k += 2
            continue
        stage = []
        if joiner == "&" and nxt is not None and nxt.operator and nxt.text == "&":
            filters, k = False, k + 2
        elif joiner == "|" and not (nxt is not None and nxt.operator):
            filters, k = True, k + 1
        elif joiner in ("", ";", "\n"):
            filters, k = False, k + 1
        else:
            return False
    return True


def stage_trusted(words: list[str], filtered: bool) -> bool:
    """True for a `cd <literal path>` step, a sed run with a plain script and no `-i`, or a
    command of `LISTING_READERS`, or of `LISTING_FILTERS` when a pipe feeds it."""
    if words[0] == "cd" and not filtered:
        return len(words) == 2 and not re.match(r"[-~]|.*[$`*?[]", words[1])
    if words[0] == "sed" and not filtered:
        found = sed_script_words(words[1:])
        return found is not None and not found[1]
    # A bare name only: `/tmp/x/cat` or `X=1 cat` is another program or environment.
    return words[0] in (LISTING_FILTERS if filtered else LISTING_READERS)


def clean_globs(text: str, cwd: str, names: Names) -> dict[str, bool]:
    """Each unquoted glob word of `text`, True when it matches real files at every place it
    stands and none of them, or the target of a link among them, is a credential file.

    The listing is from the time of the hook, so the caller calls this only when `listing_trusted`
    says that no step of the raw command can change the folder before the glob runs.
    """
    clean: dict[str, bool] = {}
    for word, directory in glob_dirs(text, cwd):
        matches = real_matches(word, directory)
        safe = bool(matches) and not any(
            is_credential(path, names, True) or is_credential(os.path.realpath(path), names, True)
            for path in matches or []
        )
        clean[word] = clean.get(word, True) and safe
    return clean


def real_matches(word: str, directory: str | None) -> list[str] | None:
    """The paths that bash makes of the glob `word` in `directory` with `dotglob` off.

    None when the folder part holds a glob, an expansion or a `~`, when a relative word has no
    known directory, or when the folder cannot be listed.
    """
    folder, _, base = word.rpartition("/")
    if re.search(r"[$`{}~]", word) or re.search(r"[*?[]", folder) or not re.search(r"[*?[]", base):
        return None
    if word.startswith("/"):
        root = folder or "/"
    elif directory is None:
        return None
    else:
        root = os.path.join(directory, folder)
    try:
        glob = glob_to_regex(base)
        entries = os.listdir(root)
    except (OSError, re.error):
        return None
    dot = base.startswith(".")
    return [os.path.join(root, e) for e in entries if (dot or e[0] != ".") and glob.fullmatch(e)]


def literal_path(word: Word) -> bool:
    """True for a word that bash passes as written: no glob, expansion, `~` or option."""
    return word.text != "" and not word.open_glob and not re.match(r"[-~]|.*[$`]", word.text)


def glob_reads_secret(piece: str, names: Names, any_dot: bool) -> bool:
    """True when the glob `piece` can match a credential name.

    With `dotglob` off, bash matches a name that starts with `.` only to a glob that starts with
    a literal `.`. An expansion in front can give that `.`, so it counts as one. With `any_dot`,
    the glob matches such a name as `find` does.
    """
    word = TEXT_EXPANSION.sub("*", NUMBER_PARAM.sub("0", piece))
    base = basename(word)
    dot = any_dot or base.startswith(".") or (base.startswith("*") and "$" in piece)
    targets = [name for name in names.glob_targets if dot or not name.startswith(".")]
    try:
        glob = glob_to_regex(base)
        return any(glob.fullmatch(name) for name in targets) or glob_matches_key(base)
    except re.error as error:
        # A glob this cannot read is refused: failing open here would skip the guard.
        print(f"{HOOK}: cannot read the glob {piece} ({error})", file=sys.stderr)
        return True


def command_words(command: str) -> list[str]:
    """The words the command runs as commands, those of a command substitution inside a word too.

    Leading assignments are stepped over. After a wrapper (`env`, `sh -c`, `timeout 5`) every
    remaining word of that stage is a candidate, each blank-separated piece of it on its own.
    """
    out: list[str] = []
    expect_command = True
    in_wrapper = False
    for token in words(command):
        if token in SEPARATORS:
            expect_command = True
            in_wrapper = False
            continue
        # `find -exec cat {} +` runs the word after the action.
        if token in FIND_ACTIONS:
            expect_command = True
            continue
        # A command substitution inside one word, `"$(cat x)"` or `` `cat x` ``, runs a command.
        inner = re.search(r"\$\(|`", token)
        if inner:
            at = inner.start()
            out.extend(command_words(token[at + (1 if token[at] == "`" else 2) :]))
        if not expect_command and not in_wrapper:
            continue
        if ASSIGNMENT.match(token) or (expect_command and token in KEYWORDS):
            continue
        for piece in re.split(r"\s+", token) if in_wrapper else [token]:
            out.append(piece)
            if basename(piece) in WRAPPERS:
                in_wrapper = True
        expect_command = False
    return out


def reader_in(command: str) -> str | None:
    """A reader the command runs, or `<` when it feeds a file to something's stdin.

    A word in command position is matched by its BASENAME, so `/bin/cat` and `./cat` are the one
    reader `cat` is.
    """
    reader = next((name for name in map(basename, command_words(command)) if name in READERS), None)
    if reader is not None:
        return reader
    # `$(<file)` reads the file into the command line, also inside quotes.
    return "<" if "<" in words(command) or re.search(r"\$\(\s*<", command) else None


def deny(path: str, names: Names) -> None:
    base = basename(path)
    hint = next(
        (e.get("hint") for e, rx in names.extra if e.get("hint") and rx.fullmatch(base)), None
    )
    # A glob that the literal name test does not hold is refused for what it could expand to.
    what = "is" if is_credential(path, names, True) else "could match"
    hookio.deny(
        f"`{path}` {what} a credential file, and `.claude/rules/lw/secrets.md` forbids reading "
        "one. "
        + (
            hint
            or f"Read `.env.example` for the key names, or ask `{names.env_command}` about a key; "
            "it prints no value."
        )
    )


def deny_listing(listing: str, names: Names) -> None:
    hookio.deny(
        f"`{listing}` lists file names, and a reader reads each one. A listing can name a "
        "credential file, and `.claude/rules/lw/secrets.md` forbids reading one. Name the files, "
        f"or bound the listing with a pattern such as `find . -name '*.md'`. Ask "
        f"`{names.env_command}` about a key; it prints no value."
    )


def check(payload: dict[str, Any]) -> None:
    if payload.get("tool_name") != "Bash":
        return
    fields = payload.get("tool_input")
    command = fields.get("command") if isinstance(fields, dict) else None
    if not isinstance(command, str) or command == "":
        return
    cfg = hookio.hook_config(HOOK, __file__)
    names = Names(cfg)
    scope = hookio.env_scope(
        cfg,
        payload,
        lambda path: is_credential(path, names, True),
        lambda path: any(rx.fullmatch(basename(path)) for _, rx in names.extra),
    )
    verdict = hookio.env_run_verdict(command, names.env_commands, scope)
    if verdict is not None:
        hookio.answer_env_run(verdict)
        return
    key = hookio.env_reveal(command, names.env_commands, scope)
    inner = hookio.env_run_command(command, names.env_commands, scope)
    cwd = str(payload.get("cwd")) if isinstance(payload.get("cwd"), str) else os.getcwd()
    # A lone engine run is exempt, except the command that `run` starts.
    lone = hookio.lone_env_command(command, names.env_commands, scope)
    target = inner if lone else command
    secret = None if target is None else secret_read(target, cwd, names)
    listing = None if target is None or secret is not None else listing_read(target, cwd)
    # A deny wins. A reveal that no deny covers asks, in a lone run or not.
    if secret is not None:
        deny(secret, names)
    elif listing is not None:
        deny_listing(listing, names)
    elif key is not None:
        hookio.ask_env(command, names.env_commands, key)


def runs_dotglob_shell(command: str) -> bool:
    """True when the command runs a shell of `DOTGLOB_SHELLS`, or any shell with `-c`."""
    if any(basename(word) in DOTGLOB_SHELLS for word in command_words(command)):
        return True
    tokens = words(command)
    for at, token in enumerate(tokens):
        if basename(token) not in SHELLS:
            continue
        for option in tokens[at + 1 :]:
            if option in SEPARATORS:
                break
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", option):
                return True
    return False


def substitution_bodies(text: str) -> list[str]:
    """The body of each `$(...)` and backtick substitution of `text`, nested ones too."""
    out: list[str] = []
    for start, end in substitutions(text, backticks=True):
        body = text[start:end]
        out.extend([body, *substitution_bodies(body)])
    return out


def pipeline_stages(text: str) -> list[tuple[list[Word], bool]]:
    """The stages of `text`, each with True when a pipe feeds its output to the next stage.

    A brace does not end a stage, so the `{}` of `xargs -I{} cat {}` keeps `cat` in the `xargs`
    stage.
    """
    spans = [w for w in word_spans(text) if not (w.operator and w.text in "{}")]
    return [(words, joiner in ("|", "|&")) for words, _, joiner in simple_commands(spans)]


def stage_text(text: str, stage: list[Word]) -> str:
    return text[stage[0].start : stage[-1].end]


def unbounded_listing(stage: list[Word], cwd: str) -> bool:
    """True when the stage lists file names that no pattern of its own bounds.

    A glob operand of `ls` and a name test of `find` or `git ls-files` bound the names, and the
    glob scan reads each of them. A literal operand of `ls` bounds them only when it is a file.
    """
    while stage and ASSIGNMENT.match(stage[0].text):
        stage = stage[1:]
    if not stage:
        return False
    name, args = basename(stage[0].text), stage[1:]
    if name == "ls":
        texts = [a.text for a in args]
        cut = texts.index("--") if "--" in texts else len(args)
        operands = [a for a in args[:cut] if not a.text.startswith("-")] + args[cut + 1 :]
        return not operands or any(
            not a.open_glob and not os.path.isfile(os.path.join(cwd, a.text)) for a in operands
        )
    if name == "find":
        return not stage_patterns(stage)[1]
    if name == "git":
        return "ls-files" in (a.text for a in args) and not git_operands(args)
    return name in LISTERS


def takes_names(stage: list[Word], text: str) -> bool:
    """True when the stage hands each line it reads to a reader as a file name."""
    first = next((w.text for w in stage if not ASSIGNMENT.match(w.text)), "")
    if basename(first) not in NAME_TAKERS:
        return False
    return first == "while" or any(
        basename(w) in READERS for w in command_words(stage_text(text, stage))
    )


def runs_reader_on_each(stage: list[Word]) -> bool:
    """True for a `find` stage whose `-exec` or `-ok` action runs a reader or a wrapper."""
    texts = [w.text for w in stage]
    return any(
        t in FIND_ACTIONS and basename(after) in READERS | WRAPPERS for t, after in pairwise(texts)
    )


def counts(stage: list[Word]) -> bool:
    """True when the stage prints a count or nothing in place of the names it reads."""
    name = basename(stage[0].text)
    return name == "wc" or (
        name in SCRIPT_TOOLS and any(COUNT_OPTION.fullmatch(w.text) for w in stage[1:])
    )


def listed_names(text: str, cwd: str) -> str | None:
    """The first listing in `text` whose file names reach a reader, or None.

    A listing reaches one through a substitution whose output keeps the names, a pipe into
    `xargs` or a `while read` loop, or a `find` action. `cat $(ls -a | grep env)` reads `.env`.
    """
    for body in substitution_bodies(text):
        stages = [stage for stage, _ in pipeline_stages(body) if stage]
        if stages and not counts(stages[-1]):
            hit = next((s for s in stages if unbounded_listing(s, cwd)), None)
            if hit is not None:
                return stage_text(body, hit)
    source: str | None = None
    for stage, piped in pipeline_stages(text):
        if not stage:
            source = None
            continue
        if source is not None and takes_names(stage, text):
            return source
        if unbounded_listing(stage, cwd):
            if runs_reader_on_each(stage):
                return stage_text(text, stage)
            source = source or stage_text(text, stage)
        source = source if piped else None
    return None


def command_forms(command: str) -> list[str]:
    """The text this guard scans, as written and with its `$'...'` escapes decoded.

    A heredoc body is exempt only in the two shapes of `scanned_text`; otherwise every byte is
    scanned. Either form counts.
    """
    scanned = IFS.sub(" ", scanned_text(command))
    return [scanned, decode_ansi_c(scanned)]


def listing_read(command: str, cwd: str) -> str | None:
    """The listing whose file names a reader of `command` reads, or None."""
    forms = command_forms(command)
    if all(reader_in(text) is None for text in forms):
        return None
    hits = (listed_names(without_quoted_bodies(text), cwd) for text in forms)
    return next((hit for hit in hits if hit is not None), None)


def secret_read(command: str, cwd: str, names: Names) -> str | None:
    """The credential path that a reader of `command` reads, or None."""
    forms = command_forms(command)
    if all(reader_in(text) is None for text in forms):
        return None
    # `git -C`, `--git-dir`, `--work-tree`, `env --chdir` and a script's `chdir` read in another
    # directory too.
    keys_by_name = any(
        any(basename(w) in MOVERS for w in command_words(text))
        or "-C" in words(text)
        or re.search(r"chdir|--git-dir|--work-tree", text)
        for text in forms
    )
    # The glob scan and the dotglob test skip a heredoc body that bash does not expand.
    bodiless = [without_quoted_bodies(text) for text in forms]
    dotglob = any(
        DOTGLOB.search(text)
        or DOTGLOB.search(remove_quotes(text))
        or "." in command_words(text)
        or runs_dotglob_shell(text)
        for text in bodiless
    )
    for text, bare in zip(forms, bodiless, strict=True):
        globbed, picks = without_patterns(bare, names)
        named = blanked(text, command_blanks(text, names).named)
        secret = names_a_secret(text, named, cwd, keys_by_name, names, globbed, picks, dotglob)
        if secret is not None:
            return secret
    return None


def main() -> None:
    payload = hookio.read_payload(HOOK)
    if payload is None:
        return
    try:
        check(payload)
    except Exception as error:  # noqa: BLE001 - an unreadable command is refused, never let through
        hookio.deny(
            f"bash-read-guard cannot read this command ({error}), so it refuses it. Split it into "
            "simpler commands."
        )


if __name__ == "__main__":
    main()
