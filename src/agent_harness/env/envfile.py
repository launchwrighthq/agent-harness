"""One env file as blocks of lines, and the rules that read and write a value.

Nothing in this module prints or logs a value. An error names a file, a key or a line number,
never the text of a line, because the line can hold a value.

A file is a list of blocks. One empty line ends a block. A block holds comment lines and
entries. An entry is one `KEY=value` line plus the lines of a quoted value that spans lines,
for example a PEM key. The entry keeps its lines as they are written, so a write that does not
touch an entry gives back its bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agent_harness import HarnessError

KEY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")

PEM = re.compile(r"^-----BEGIN [A-Z0-9 ]+-----\n[\s\S]*\n-----END [A-Z0-9 ]+-----\n?$")
FOLDED_PEM = re.compile(r"^-----BEGIN [A-Z0-9 ]+-----\\n[\s\S]*\\n-----END [A-Z0-9 ]+-----(\\n)?$")

# A header line of a block that holds keys the master does not name.
OTHER_HEADER = "# Other"


class EnvFileError(HarnessError):
    """A file that cannot be read, parsed or written. The message never holds a value."""


@dataclass
class Entry:
    key: str
    lines: list[str]

    @property
    def value(self) -> str:
        return value_of(self.lines)


@dataclass
class Block:
    """One block: comment lines and entries in file order, with no empty line inside."""

    items: list[str | Entry] = field(default_factory=list)

    @property
    def header(self) -> str | None:
        first = self.items[0] if self.items else None
        return first if isinstance(first, str) and first.startswith("#") else None

    def entries(self) -> list[Entry]:
        return [item for item in self.items if isinstance(item, Entry)]


@dataclass
class EnvFile:
    blocks: list[Block] = field(default_factory=list)

    def entries(self) -> list[Entry]:
        return [entry for block in self.blocks for entry in block.entries()]

    def values(self) -> dict[str, str]:
        """Every key and its value. A later entry of a key wins, as a loader reads the file."""
        return {entry.key: entry.value for entry in self.entries()}

    def key_names(self) -> list[str]:
        """The key names in file order, each once."""
        return list(dict.fromkeys(entry.key for entry in self.entries()))

    def find(self, key: str) -> tuple[Block, Entry] | None:
        """The last entry of `key` and its block."""
        found = None
        for block in self.blocks:
            for entry in block.entries():
                if entry.key == key:
                    found = (block, entry)
        return found

    def render(self) -> str:
        parts = []
        for block in self.blocks:
            lines: list[str] = []
            for item in block.items:
                lines.extend([item] if isinstance(item, str) else item.lines)
            if lines:
                parts.append("\n".join(lines))
        return "\n\n".join(parts) + "\n" if parts else ""


def _open_quote(line: str) -> str | None:
    """The quote that the value of `line` opens and does not close on the same line."""
    value = line[line.index("=") + 1 :]
    quote = value[:1]
    if quote not in ('"', "'"):
        return None
    return quote if value.find(quote, 1) == -1 else None


def value_of(lines: list[str]) -> str:
    """The value of an entry: the text after the first `=`, one matching pair of quotes removed.

    A PEM key that a write folded onto one line, with `\\n` for each newline, reads back whole.
    """
    first = lines[0]
    raw = "\n".join([first[first.index("=") + 1 :], *lines[1:]])
    if len(raw) >= 2 and raw[0] in ('"', "'") and raw.endswith(raw[0]):
        raw = raw[1:-1]
    return raw.replace("\\n", "\n") if FOLDED_PEM.match(raw) else raw


def parse(text: str, name: str = "the file") -> EnvFile:
    """The blocks of an env file. A stray line before any key raises with its line number."""
    env = EnvFile()
    block: Block | None = None
    open_quote: str | None = None
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for number, line in enumerate(lines, start=1):
        if open_quote is not None and block is not None:
            entry = block.items[-1]
            assert isinstance(entry, Entry)
            entry.lines.append(line)
            if open_quote in line:
                open_quote = None
            continue
        if line.strip() == "":
            block = None
            continue
        if block is None:
            block = Block()
            env.blocks.append(block)
        match = ASSIGNMENT.match(line)
        if match:
            block.items.append(Entry(match.group(1), [line]))
            open_quote = _open_quote(line)
        elif line.lstrip().startswith("#"):
            block.items.append(line)
        elif block.items and isinstance(block.items[-1], Entry):
            block.items[-1].lines.append(line)
        else:
            raise EnvFileError(
                f"{name}: line {number} is neither blank, a comment nor a KEY=value line"
            )
    if open_quote is not None:
        raise EnvFileError(f"{name}: a quoted value opens and never closes")
    return env


def read(path: Path) -> EnvFile:
    if not path.is_file():
        raise EnvFileError(f"{path} does not exist")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise EnvFileError(f"cannot read {path}: {type(error).__name__}") from error
    return parse(text, str(path))


def read_or_empty(path: Path) -> EnvFile:
    return read(path) if path.is_file() else EnvFile()


def needs_quotes(value: str) -> bool:
    return (
        value != value.strip()
        or value[:1] in ('"', "'")
        or re.search(r"\s#", value) is not None
        or "\n" in value
    )


def lines_for(key: str, value: str) -> list[str]:
    """The lines of one entry. A value that a reader would misread gets quotes.

    A PEM key folds onto one line. A value that holds both quote characters and needs quotes
    is refused, because no quote would read back the same value.
    """
    if not KEY_NAME.match(key):
        raise HarnessError(f"{key} is not a valid env key name")
    if PEM.match(value) and "\\" not in value:
        return [f'{key}="' + value.replace("\n", "\\n") + '"']
    if not needs_quotes(value):
        return [f"{key}={value}"]
    quote = '"' if '"' not in value else "'" if "'" not in value else None
    if quote is None:
        raise HarnessError(
            f"the value of {key} needs quotes and holds both quote characters. "
            "Edit that key in an editor."
        )
    first, *rest = f"{quote}{value}{quote}".split("\n")
    return [f"{key}={first}", *rest]


def content_digest(text: str) -> str:
    """The sha256 of the sorted key and value pairs of an env file text, as `values` reads them.

    A header, a comment, an empty line or the key order leaves it as it is, so a push marker
    that holds it moves only on a value edit.
    """
    pairs = sorted(parse(text).values().items())
    return hashlib.sha256(json.dumps(pairs).encode("utf-8")).hexdigest()


def write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Write `text` beside `path`, then rename it over, so a failed write keeps the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(temp, mode)
        os.replace(temp, path)
    except OSError as error:
        Path(temp).unlink(missing_ok=True)
        raise EnvFileError(f"cannot write {path}: {type(error).__name__}") from error


def save(path: Path, env: EnvFile, expected: Mapping[str, str], mode: int = 0o600) -> None:
    """Write `env` to `path` after a check that its text reads back as `expected`.

    The check runs on the text before the write, so a file that would not read back the same is
    never written.
    """
    text = env.render()
    back = parse(text, str(path)).values()
    if back != dict(expected):
        wrong = sorted(k for k in set(back) | set(expected) if back.get(k) != expected.get(k))
        raise EnvFileError(
            f"{path} would not read back the same values for {', '.join(wrong)}; nothing written"
        )
    write_text(path, text, mode)


def remove_entries(env: EnvFile, key: str) -> int:
    """Remove every entry of `key`. A block left with no entry goes too. Returns the count."""
    count = 0
    emptied: list[Block] = []
    for block in env.blocks:
        kept = [i for i in block.items if not (isinstance(i, Entry) and i.key == key)]
        if len(kept) == len(block.items):
            continue
        count += len(block.items) - len(kept)
        block.items = kept
        if not block.entries():
            emptied.append(block)
    env.blocks = [block for block in env.blocks if all(block is not e for e in emptied)]
    return count
