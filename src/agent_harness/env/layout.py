"""The env-file layout rules L1 to L7, the shape of a master and key placement by that shape.

docs/env.md ("Layout") states the rules. A finding names a key, a line number, or a header title
of a committed `.example` file. It never holds a value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent_harness.env.envfile import OTHER_HEADER, Block, Entry, EnvFile

MASTER = ".env.example"
KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
RULE_CHARS = re.compile(r"[─═=]|-{2,}")
INLINE_COMMENT = re.compile(r"\s#")
# These live files take their own `.example` as master even when the root master holds a key of
# theirs. `env.paired` of the config replaces the set.
ALWAYS_PAIRED = frozenset({".env.ci"})
# A backup, a temp file of a write and the marker of a push sit beside a live file. None is an
# env file.
NOT_ENV_SUFFIXES = (".bak", ".pushed", ".tmp")
PUSH_MARKER = ".pushed-"


def is_env_name(name: str) -> bool:
    return (
        (name == ".env" or name.startswith(".env."))
        and not name.endswith(NOT_ENV_SUFFIXES)
        and PUSH_MARKER not in name
    )


def is_template(name: str) -> bool:
    return name.endswith(".example")


@dataclass
class LayoutBlock:
    header: str | None
    header_line: int
    keys: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.rule} {self.message}"


def split_lines(text: str) -> tuple[list[str], bool]:
    """The lines without their newlines, and whether the text ends in one."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text == "":
        return [], True
    lines = text.split("\n")
    return (lines[:-1], True) if text.endswith("\n") else (lines, False)


def title_problem(line: str) -> str | None:
    if not line.startswith("# "):
        return "a header is `# ` then a title"
    title = line[2:]
    if title.strip() == "":
        return "the header title is empty"
    if title != title.strip() or "  " in title:
        return "the header title has extra spaces"
    if RULE_CHARS.search(title) or "-" in title.split():
        return "the header title has rule characters"
    if title.endswith(":"):
        return "the header title ends in a colon"
    if not 1 <= len(title.split()) <= 3:
        return "the header title is not 1 to 3 words"
    return None


class FileCheck:
    """The layout findings of one file's text."""

    def __init__(self, path: str, text: str, *, master: bool) -> None:
        self.path = path
        self.master = master
        # Only a committed example may have its text printed.
        self.public = is_template(path)
        self.lines, self.ends_in_newline = split_lines(text)
        self.findings: list[Finding] = []
        self.blocks: list[LayoutBlock] = []
        self.run()

    def add(self, line: int, rule: str, message: str) -> None:
        self.findings.append(Finding(self.path, line, rule, message))

    def name_header(self, block: LayoutBlock) -> str:
        if self.public and block.header:
            return f"'{block.header[2:]}'"
        return f"on line {block.header_line}"

    def run(self) -> None:
        if not self.lines:
            return
        self.check_blank_lines()
        body_start = self.check_top_block() if self.master else 0
        self.parse_blocks(body_start)

    def check_blank_lines(self) -> None:
        if not self.ends_in_newline:
            self.add(len(self.lines), "L2", "the file does not end in a newline")
        if self.lines[0] == "":
            self.add(1, "L2", "the file starts with an empty line")
        for number, (before, line) in enumerate(
            zip(["x", *self.lines], self.lines, strict=False), start=1
        ):
            if line == "" and before == "":
                self.add(number, "L2", "more than one empty line between blocks")
            elif line != "" and line.strip() == "":
                self.add(number, "L2", "a blank line holds whitespace")
        if self.lines[-1] == "" and len(self.lines) > 1:
            self.add(len(self.lines), "L2", "the file ends in an empty line")

    def check_top_block(self) -> int:
        """Check L5 and return the index of the first line after the top block's empty line."""
        if not self.lines[0].startswith("#"):
            self.add(1, "L5", "the master does not open with a generated `#` block")
            return 0
        if "" not in self.lines:
            self.add(len(self.lines), "L5", "no empty line follows the generated top block")
            return len(self.lines)
        end = self.lines.index("")
        for index in range(end):
            if not self.lines[index].startswith("#"):
                self.add(
                    index + 1, "L5", "the generated top block holds a line that is not a comment"
                )
                break
        return end + 1

    def parse_blocks(self, start: int) -> None:
        index = start
        while index < len(self.lines):
            if self.lines[index] == "":
                index += 1
                continue
            end = index
            while end < len(self.lines) and self.lines[end] != "":
                end += 1
            self.parse_block(index, end, after_gap=bool(self.blocks) or index > start)
            index = end

    def parse_block(self, start: int, end: int, *, after_gap: bool) -> None:
        first = self.lines[start]
        if first.startswith("#"):
            block = LayoutBlock(first, start + 1)
            if problem := title_problem(first):
                self.add(start + 1, "L1", problem)
            rest = range(start + 1, end)
        else:
            block = LayoutBlock(None, start + 1)
            if after_gap:
                self.add(start, "L2", "an empty line inside a block: the next line is not a header")
            else:
                self.add(start + 1, "L1", "the block has no header")
            rest = range(start, end)
        for index in rest:
            self.classify(block, index, end)
        if not block.keys:
            self.add(
                block.header_line, "L3", f"the block {self.name_header(block)} has no key line"
            )
        self.blocks.append(block)

    def classify(self, block: LayoutBlock, index: int, end: int) -> None:
        line, number = self.lines[index], index + 1
        if match := KEY_LINE.match(line):
            block.keys.append((match.group(1), number))
            if INLINE_COMMENT.search(match.group(2)):
                self.add(number, "L7", f"key {match.group(1)} carries an inline comment")
        elif line.startswith("#"):
            if not self.master:
                self.add(number, "L3", "a comment line in a plain file")
            elif index + 1 >= end or not KEY_LINE.match(self.lines[index + 1]):
                self.add(number, "L6", "a key comment is not directly above its key line")
        elif line.strip():
            self.add(number, "L3", "the line is not a header, a comment or a key line")


def check_order(plain: FileCheck, master: FileCheck) -> None:
    """L4: the plain file's headers and keys follow the master's, and add none."""
    headers = {block.header: rank for rank, block in enumerate(master.blocks) if block.header}
    home: dict[str, tuple[str, int]] = {}
    for block in master.blocks:
        for rank, (key, _) in enumerate(block.keys):
            if block.header:
                home.setdefault(key, (block.header, rank))
    last_header = -1
    for block in plain.blocks:
        if block.header is not None:
            if block.header not in headers:
                plain.add(
                    block.header_line,
                    "L4",
                    f"the header {plain.name_header(block)} is not in the master",
                )
            elif headers[block.header] <= last_header:
                plain.add(
                    block.header_line,
                    "L4",
                    f"the header {plain.name_header(block)} is out of the master's order",
                )
            else:
                last_header = headers[block.header]
        last_key = -1
        for key, number in block.keys:
            if key not in home:
                plain.add(number, "L4", f"key {key} is not in the master")
                continue
            header, rank = home[key]
            if header != block.header:
                plain.add(number, "L4", f"key {key} belongs under '{header[2:]}' in the master")
            elif rank <= last_key:
                plain.add(number, "L4", f"key {key} is out of the master's key order")
            else:
                last_key = rank


def key_names(check: FileCheck) -> set[str]:
    return {key for block in check.blocks for key, _ in block.keys}


def is_pair_master(
    name: str,
    checks: Mapping[str, FileCheck],
    root_keys: set[str],
    paired: Iterable[str] = ALWAYS_PAIRED,
) -> bool:
    """`.env.<n>.example` whose keys the root master does not hold, or the example of a file of
    `paired`."""
    if name not in checks or name == MASTER or not is_template(name):
        return False
    return name.removesuffix(".example") in set(paired) or not key_names(checks[name]) & root_keys


def env_names(directory: Path) -> list[str]:
    return sorted(
        path.name for path in directory.iterdir() if is_env_name(path.name) and path.is_file()
    )


def check_texts(
    texts: Mapping[str, str], paths: Mapping[str, str], paired: Iterable[str] = ALWAYS_PAIRED
) -> list[Finding]:
    """The findings of the env files of one folder, by file name. `paths` names each in output."""
    checks = {
        name: FileCheck(paths[name], texts[name], master=name == MASTER) for name in sorted(texts)
    }
    master = checks.get(MASTER)
    root_keys = key_names(master) if master else set()
    for name, check in checks.items():
        example = f"{name}.example"
        if name == MASTER or is_pair_master(name, checks, root_keys, paired):
            continue  # a pair master is checked for L1 to L3 and L7 only
        if is_pair_master(example, checks, root_keys, paired):
            check_order(check, checks[example])
        elif master is not None:
            check_order(check, master)
    found = [finding for check in checks.values() for finding in check.findings]
    return sorted(found, key=lambda finding: (finding.path, finding.line))


# The shape of a master: its headers in order, each with its keys in order.


@dataclass
class Shape:
    blocks: list[tuple[str, list[str]]] = field(default_factory=list)

    def home(self, key: str) -> tuple[int, int] | None:
        """The block rank and key rank of `key`, or None when the master does not name it."""
        for block_rank, (_, keys) in enumerate(self.blocks):
            if key in keys:
                return block_rank, keys.index(key)
        return None

    def header_rank(self, header: str) -> int | None:
        for rank, (name, _) in enumerate(self.blocks):
            if name == header:
                return rank
        return None

    def key_names(self) -> list[str]:
        return [key for _, keys in self.blocks for key in keys]


def shape_of_master(text: str) -> Shape:
    """The blocks of a master after its top block. A block without a header is left out."""
    check = FileCheck(MASTER, text, master=True)
    shape = Shape()
    for block in check.blocks:
        if block.header is not None:
            shape.blocks.append((block.header, [key for key, _ in block.keys]))
    return shape


def shape_of_plain(text: str) -> Shape:
    """The blocks of a pair master, which is a plain file with no top block."""
    check = FileCheck(".example", text, master=False)
    return Shape([(b.header, [k for k, _ in b.keys]) for b in check.blocks if b.header])


def shape_of_blocks(blocks: Sequence[tuple[str, str]]) -> Shape:
    """A shape from `(key, block title)` pairs in declaration order."""
    shape = Shape()
    for key, title in blocks:
        header = f"# {title}"
        rank = shape.header_rank(header)
        if rank is None:
            shape.blocks.append((header, [key]))
        elif key not in shape.blocks[rank][1]:
            shape.blocks[rank][1].append(key)
    return shape


def _has_headers(env: EnvFile) -> bool:
    return any(block.header is not None for block in env.blocks)


def put(env: EnvFile, key: str, lines: list[str], shape: Shape | None) -> str:
    """Set the entry of `key` to `lines`. Returns `replaced` or `added`.

    An existing entry changes in place. A new key goes into its master block, after the keys
    that the master puts before it. A new key that the master does not name goes into a last
    `# Other` block. Without a shape, or in a file with no header, it goes at the end.
    """
    found = env.find(key)
    if found is not None:
        _, entry = found
        entry.lines = list(lines)
        return "replaced"
    new = Entry(key, list(lines))
    home = shape.home(key) if shape is not None else None
    if shape is None or not (_has_headers(env) or not env.blocks):
        if not env.blocks:
            env.blocks.append(Block())
        env.blocks[-1].items.append(new)
        return "added"
    if home is None:
        other = next((b for b in env.blocks if b.header == OTHER_HEADER), None)
        if other is None:
            other = Block([OTHER_HEADER])
            env.blocks.append(other)
        other.items.append(new)
        return "added"
    block_rank, key_rank = home
    header = shape.blocks[block_rank][0]
    block = next((b for b in env.blocks if b.header == header), None)
    if block is None:
        block = Block([header])
        at = len(env.blocks)
        for index, other_block in enumerate(env.blocks):
            rank = shape.header_rank(other_block.header) if other_block.header else None
            if rank is not None and rank > block_rank:
                at = index
                break
            if other_block.header == OTHER_HEADER:
                at = index
                break
        env.blocks.insert(at, block)
    master_keys = shape.blocks[block_rank][1]
    at = len(block.items)
    for index, item in enumerate(block.items):
        if (
            isinstance(item, Entry)
            and item.key in master_keys
            and master_keys.index(item.key) > key_rank
        ):
            at = index
            break
    block.items.insert(at, new)
    return "added"


def reorder(env: EnvFile, shape: Shape) -> tuple[EnvFile, list[str]]:
    """The entries of `env` in the master's blocks and order, with header and key lines only.

    A key that the master does not name goes into a last `# Other` block. Returns the new file
    and those keys.
    """
    last: dict[str, Entry] = {}
    for entry in env.entries():
        last[entry.key] = entry
    out = EnvFile()
    placed: set[str] = set()
    for header, keys in shape.blocks:
        items: list[str | Entry] = [last[k] for k in keys if k in last and k not in placed]
        placed.update(k for k in keys if k in last)
        if items:
            out.blocks.append(Block([header, *items]))
    extra = [key for key in last if key not in placed]
    if extra:
        out.blocks.append(Block([OTHER_HEADER, *[last[k] for k in extra]]))
    return out, extra


def template_of(keys: Iterable[str], shape: Shape | None) -> EnvFile:
    """A plain template: the given keys with empty values, in the master's layout."""
    env = EnvFile()
    for key in keys:
        put(env, key, [f"{key}="], shape)
    return reorder(env, shape)[0] if shape is not None else env
