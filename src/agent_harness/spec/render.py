"""Render a spec Markdown file into the HTML review page.

    ah spec render docs/specs/<name>.md [--out docs/specs/dist/review]

docs/spec.md ("The renderer") lists the constructs that it reads. Anything else fails with a
`SpecError` that names the file and the 1-based line, so no literal Markdown mark reaches a page.
The output equals the earlier Node renderer byte for byte, so the regexes copy JavaScript: `\\s`
is the JavaScript whitespace set, `.` stops at the four JavaScript line terminators, `$` matches
only at the end, `\\d`, `\\w` and `\\b` are ASCII, and `js_trim` is `String.prototype.trim`. The
page goes to the `out` folder (`dist`) of the spec folder, and any `archive/` is mirrored there.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from agent_harness.spec.layout import Domain, Layout

TEMPLATE = Path(__file__).resolve().parent / "spec-template.html"

# The statuses that `.claude/rules/lw/spec-format.md` defines: the active ones, then the settled.
STATUS_VALUES = (
    "draft",
    "approved",
    "in-progress",
    "analysis",
    "blocked",
    "implemented",
    "superseded-by",
    "abandoned",
    "answered",
)

# The chip of these statuses reads as live. The others read as not final. `tested` is a QA
# report whose pass is done.
LIVE_STATUSES = frozenset({"approved", "in-progress", "implemented", "tested"})

# The JavaScript whitespace set (`\s` and `trim`) and its line terminators (what `.` stops at).
_WS_CHARS = (
    "\t\n\x0b\x0c\r \u00a0\u1680"
    + "".join(chr(c) for c in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
WS = r"[\t\n\x0b\x0c\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]"
DOT = r"[^\n\r\u2028\u2029]"


def _re(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern.replace(r"\s", WS).replace("<DOT>", DOT), re.ASCII | flags)


def js_trim(text: str) -> str:
    return text.strip(_WS_CHARS)


def js_trim_start(text: str) -> str:
    return text.lstrip(_WS_CHARS)


HEADER_FIELD_RE = _re(r"^(Date|Status|Subject|Range):\s*(<DOT>*)\Z")
# `Status:` on a clean field line, in a sentence with prose after it, or in an HTML comment.
STATUS_TOKEN_RE = _re(r"\bStatus:\s*([a-z-]+)([^\n]*)", re.IGNORECASE)
STATUS_WORD_RE = _re(r"\bStatus:", re.IGNORECASE)
COMMENT_LINE_RE = _re(r"^\s*<!--<DOT>*-->\s*\Z")
TAIL_COMMENT_RE = _re(r"-->\s*\Z")
DATE_PREFIX_RE = _re(r"^(\d{4}-\d{2}-\d{2})-")
TITLE_RE = _re(r"^#\s")
IMAGE_RE = _re(r"!\[([^\]]*)\]\(([^)]*)\)")
MD_LINK_RE = _re(r"\[([^\]]*)\]\(([^)]*)\)")
# A paragraph whose whole content is one image or one video.
LONE_IMAGE_RE = _re(r"^<(?:img [^>]*>|video [^>]*>[^<]*</video>)\Z")
VIDEO_RE = _re(r"\.(?:mp4|webm)\Z", re.IGNORECASE)
DATA_URI_RE = _re(r"^data:", re.IGNORECASE)
ASSET_RE = _re(r"^assets/[^/]")
# The schemes a link may name. A link with no scheme is a relative path, a fragment or assets/.
LINK_SCHEMES = ("http", "https", "mailto")
SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
# The characters a browser drops from a URL before it reads the scheme.
LINK_NOISE_RE = re.compile(r"[\x00-\x20\x7f]")
# `//host` or `\\host`: a browser reads it as a link to another host.
NETWORK_PATH_RE = re.compile(r"^[/\\]{2}")
NUMBERED_RE = _re(r"^(\d+(?:\.\d+)*)(\.?)\s+(<DOT>*)\Z")
PARA_STOP_RE = _re(r"^(```|#|\||- |\d+\. |>)")
QUOTE_MARK_RE = _re(r"^>\s?")
UL_RE = _re(r"^- ")
OL_RE = _re(r"^\d+\. ")
NESTED_UL_RE = _re(r"^\s*- ")
NESTED_OL_RE = _re(r"^\s*\d+\. ")
ANY_ITEM_RE = _re(r"^\s*(- |\d+\. )")
LEADING_WS_RE = _re(r"^\s+")
BOLD_RE = _re(r"\*\*(<DOT>+?)\*\*")
STRIKE_RE = _re(r"~~(<DOT>+?)~~")
ITALIC_RE = _re(r"\*(<DOT>+?)\*")
UNDERSCORE_RE = _re(r"(?<![A-Za-z0-9_])_(<DOT>+?)_(?![A-Za-z0-9_])")
PLAIN_CODE_RE = _re(r"`([^`]+)`")
PLAIN_BOLD_RE = _re(r"\*\*([^*]+)\*\*")
SLUG_RE = _re(r"[^a-z0-9]+")
SLUG_EDGE_RE = _re(r"^-+|-+\Z")
EXTENSION_RE = _re(r"\.[^./]*\Z")
LINK_MARK_RE = _re("\x01(\\d+)\x01")
CODE_MARK_RE = _re("\x00(\\d+)\x00")

PLACEHOLDERS = (
    "{{NAV}}",
    "{{BODY}}",
    "{{TITLE}}",
    "{{DATE}}",
    "{{STATUS}}",
    "{{SUBJECT}}",
    "{{RANGE}}",
    "{{STANDFIRST}}",
    "{{KIND}}",
    "{{KIND_CLASS}}",
    "{{SOURCE}}",
)
STATUS_SLOT = '<li class="chip">{{STATUS}}</li>'
SUBJECT_SLOT = '<li class="chip">{{SUBJECT}}</li>'
RANGE_SLOT = '<li class="chip">{{RANGE}}</li>'
# One pass over the template, so a value that holds literal `{{X}}` text reaches the page as it
# is. The three chip alternatives come first, so they take the whole `<li>` at that position.
PLACEHOLDER_RE = re.compile(
    "|".join(re.escape(slot) for slot in (STATUS_SLOT, SUBJECT_SLOT, RANGE_SLOT))
    + r"|\{\{(\w+)\}\}",
    re.ASCII,
)


FENCE_LEAVES_ITEM = (
    "a fenced code block in a list item has a line at or left of the item marker. Indent every"
    " line of the block, the closing fence too, under the item text, or end the list with a"
    " blank line and start the fence at the left margin below it"
)


class SpecError(Exception):
    """A refusal. The message names the file, and the line where there is one."""


@dataclass(frozen=True)
class Head:
    title: str
    date: str
    status: str
    status_text: str
    subject: str | None
    range: str | None
    standfirst: str


@dataclass(frozen=True)
class TocEntry:
    anchor: str
    eyebrow: str
    title: str


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def _basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def domain_of(source: str, layout: Layout) -> Domain:
    """The spec folder of `source`: the longest folder of the layout that holds it. A source
    outside every folder has no kind, so it fails."""
    found = layout.domain_at(source)
    if found is None:
        listed = ", ".join(f"{folder}/" for folder in layout.dirs)
        raise SpecError(f"{source}: not under {listed}, so it has no document kind")
    return found


def dist_for(source: str, layout: Layout) -> str:
    """The page of `source`: `docs/specs/a.md` gives `docs/specs/dist/a.html`."""
    domain_of(source, layout)
    inserted = layout.folder_re().sub(lambda m: f"{m.group(0)}{layout.out}/", _posix(source), 1)
    return EXTENSION_RE.sub("", inserted, 1) + ".html"


def source_for(page: str, layout: Layout) -> str:
    """The source of a page: the inverse of `dist_for` for a `.md` source."""
    names = "|".join(re.escape(folder) for folder in layout.longest_first())
    out = re.escape(layout.out)
    page = re.sub(rf"(/(?:{names})/){out}/", r"\1", page, count=1)
    return re.sub(r"\.html\Z", ".md", page, count=1)


def inside_out(dst: str, layout: Layout) -> bool:
    """Whether `dst` sits inside an `out` folder. The renderer writes nowhere else."""
    return re.search(rf"(?:^|/){re.escape(layout.out)}/", _posix(dst)) is not None


def asset_prefix_for(source: str, layout: Layout) -> str:
    """The `../` walk from the page back to the folder's `assets/`."""
    domain = domain_of(source, layout)
    posix = _posix(source)
    relative = posix[posix.find(f"{domain.dir}/") + len(domain.dir) + 1 :]
    return "../" * len(relative.split("/"))


def _image_problem(url: str) -> str | None:
    if DATA_URI_RE.search(url):
        return (
            "an inline data: image belongs in the domain's assets/, where it can be seen and reused"
        )
    if not ASSET_RE.search(url):
        return f"an image must be referenced as assets/<file>, not '{url}'"
    return None


def _link_problem(url: str) -> str | None:
    """Why a link href is refused, or None for a relative path, a `#fragment`, an `assets/` path
    or an `http:`, `https:` or `mailto:` URL.

    A browser drops whitespace and control characters from a URL, so `java\tscript:` runs as
    `javascript:`. The check reads the href without them.
    """
    bare = LINK_NOISE_RE.sub("", url)
    scheme = SCHEME_RE.search(bare)
    if scheme and scheme.group(1).lower() not in LINK_SCHEMES:
        return (
            f"a link must be a relative path, a #fragment, an assets/ path or an http:, https: "
            f"or mailto: URL, not '{url}'"
        )
    if NETWORK_PATH_RE.search(bare):
        return f"a link to another host must name http: or https:, not '{url}'"
    return None


# Why an `assets/` image has no file, or None. Only `render_file` knows the disk.
AssetCheck = Callable[[str], str | None]


def asset_folder(source: str, layout: Layout) -> str:
    """The spec folder whose `assets/` the images of `source` live in."""
    domain = domain_of(source, layout)
    posix = _posix(source)
    return posix[: posix.find(f"{domain.dir}/") + len(domain.dir)]


def _check_images(
    line: str, fail: Callable[[str], NoReturn], asset_check: AssetCheck | None = None
) -> None:
    """Fail on the first image or link of the line that `_image_problem`, `asset_check` or
    `_link_problem` refuses."""
    for match in IMAGE_RE.finditer(line):
        problem = _image_problem(match.group(2))
        if problem is None and asset_check is not None:
            problem = asset_check(match.group(2))
        if problem:
            fail(problem)
    for match in MD_LINK_RE.finditer(IMAGE_RE.sub("", line)):
        problem = _link_problem(match.group(2))
        if problem:
            fail(problem)


def esc(text: str, quote: bool = False) -> str:
    out = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return out.replace('"', "&quot;").replace("'", "&#x27;") if quote else out


def slug(text: str) -> str:
    return SLUG_EDGE_RE.sub("", SLUG_RE.sub("-", text.lower()))


def plain(text: str) -> str:
    """Strip inline marks for a place that cannot show them, such as a nav label."""
    return PLAIN_BOLD_RE.sub(r"\1", PLAIN_CODE_RE.sub(r"\1", text))


def emphasis(text: str) -> str:
    """Bold, strikethrough and italic over escaped text with no code span left in it.

    Bold runs first and its inner text goes through this function again, so the outer pair of
    `**bold *and italic* text**` stays one `<strong>`. An `_` opens italic only when no word
    character touches it, so `snake_case_names` stay plain.
    """
    out = BOLD_RE.sub(lambda m: f"<strong>{emphasis(m.group(1))}</strong>", text)
    out = STRIKE_RE.sub(r"<s>\1</s>", out)
    out = ITALIC_RE.sub(r"<em>\1</em>", out)
    return UNDERSCORE_RE.sub(r"<em>\1</em>", out)


@dataclass(frozen=True)
class Piece:
    code: bool
    text: str


def split_code_spans(text: str) -> list[Piece]:
    """Split a line into code spans and the text between them, the longest backtick run first.

    A run of N backticks opens a span that only a run of exactly N closes, so a span in two
    backticks can hold one. An unclosed run stays text, and the scan goes on after it.
    """
    pieces: list[Piece] = []
    buffer = ""
    i = 0
    size = len(text)

    def run_length(at: int) -> int:
        n = 0
        while at + n < size and text[at + n] == "`":
            n += 1
        return n

    while i < size:
        if text[i] != "`":
            buffer += text[i]
            i += 1
            continue
        opened = run_length(i)
        content_start = i + opened
        j = content_start
        close = -1
        while j < size:
            if text[j] != "`":
                j += 1
                continue
            run = run_length(j)
            if run == opened:
                close = j
                break
            j += run
        if close == -1:
            buffer += text[i:content_start]
            i = content_start
            continue
        if buffer:
            pieces.append(Piece(False, buffer))
            buffer = ""
        # One leading and one trailing space go, as in CommonMark, so a span can start or end
        # with a backtick.
        content = text[content_start:close]
        if (
            len(content) > 1
            and content.startswith(" ")
            and content.endswith(" ")
            and js_trim(content)
        ):
            content = content[1:-1]
        pieces.append(Piece(True, content))
        i = close + opened
    if buffer:
        pieces.append(Piece(False, buffer))
    return pieces


def without_code_spans(text: str) -> str:
    return "".join(piece.text for piece in split_code_spans(text) if not piece.code)


def inline(text: str, asset_prefix: str = "") -> str:
    """Escape, then apply the inline marks. Code spans, images and links are kept apart first."""
    codes: list[str] = []
    links: list[str] = []

    staged = ""
    for piece in split_code_spans(text):
        if piece.code:
            codes.append(piece.text)
            staged += f"\x00{len(codes) - 1}\x00"
        else:
            staged += piece.text

    def image(match: re.Match[str]) -> str:
        alt, url = match.group(1), match.group(2)
        problem = _image_problem(url)
        if problem:
            raise SpecError(problem)
        src = esc(asset_prefix + url, True)
        if VIDEO_RE.search(url):
            links.append(
                f'<video src="{src}" controls playsinline preload="metadata">{esc(alt)}</video>'
            )
        else:
            links.append(f'<img src="{src}" alt="{esc(alt, True)}" loading="lazy">')
        return f"\x01{len(links) - 1}\x01"

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        problem = _link_problem(url)
        if problem:
            raise SpecError(problem)
        # A link into `assets/` takes the same walk as an image, because the page sits one or
        # two folders below the source.
        href = asset_prefix + url if ASSET_RE.search(url) else url
        links.append(f'<a href="{esc(href, True)}">{emphasis(esc(label))}</a>')
        return f"\x01{len(links) - 1}\x01"

    def stored(items: list[str], index: str) -> str:
        n = int(index)
        # JavaScript prints `undefined` for a missing array item.
        return items[n] if n < len(items) else "undefined"

    staged = IMAGE_RE.sub(image, staged)
    staged = MD_LINK_RE.sub(link, staged)
    staged = emphasis(esc(staged))
    staged = LINK_MARK_RE.sub(lambda m: stored(links, m.group(1)), staged)
    return CODE_MARK_RE.sub(lambda m: f"<code>{esc(stored(codes, m.group(1)))}</code>", staged)


def parse_head(
    lines: list[str], source: str, layout: Layout, asset_check: AssetCheck | None = None
) -> tuple[Head, int]:
    """The H1, the fields and the standfirst, and the index of the first body line.

    The header is everything above the first `##`. The fields are searched across all of it,
    because a spec can write `Status:` above its title.
    """
    domain = domain_of(source, layout)
    asset_prefix = asset_prefix_for(source, layout)
    end = next((n for n, line in enumerate(lines) if line.startswith("##")), len(lines))
    header = lines[:end]

    title_index = next((n for n, line in enumerate(header) if TITLE_RE.search(line)), -1)
    if title_index == -1:
        raise SpecError(f"{source}: missing a '# H1' at the top of the file")
    title = js_trim(header[title_index][1:])

    fields: dict[str, str] = {}
    consumed = {title_index}
    for n, line in enumerate(header):
        match = HEADER_FIELD_RE.search(line)
        if not match:
            continue
        fields.setdefault(match.group(1), js_trim(match.group(2)))
        consumed.add(n)

    date = fields.get("Date")
    if date is None:
        prefix = DATE_PREFIX_RE.search(_basename(source))
        date = prefix.group(1) if prefix else None
    if date is None:
        raise SpecError(
            f"{source}: missing 'Date:' below the H1, and '{_basename(source)}' has no "
            "YYYY-MM-DD- prefix"
        )

    status_match = (
        STATUS_TOKEN_RE.search(f"Status: {fields['Status']}") if "Status" in fields else None
    )
    if not status_match:
        status_match = STATUS_TOKEN_RE.search("\n".join(header))
        for n, line in enumerate(header):
            if STATUS_WORD_RE.search(line) and COMMENT_LINE_RE.search(line):
                consumed.add(n)
    if not status_match:
        raise SpecError(f"{source}: missing 'Status:' below the H1")
    token = status_match.group(1).lower()
    allowed = domain.statuses or STATUS_VALUES
    if token not in allowed:
        raise SpecError(
            f"{source}: Status '{token}' is not one of {', '.join(allowed)} for a document "
            f"in {domain.dir}/"
        )
    # `superseded-by` keeps the path after it. Any other status is one word.
    tail = js_trim(TAIL_COMMENT_RE.sub("", status_match.group(2), 1))
    status_text = f"{token} {tail}" if token == "superseded-by" and tail else token

    paragraphs: list[str] = []
    current: list[str] = []
    for n, line in enumerate(header):
        if n in consumed:
            continue

        def fail(message: str, n: int = n) -> NoReturn:
            raise SpecError(f"{source}:{n + 1}: {message}")

        _check_images(line, fail, asset_check)
        if js_trim(line):
            current.append(js_trim(line))
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))

    head = Head(
        title=title,
        date=date,
        status=token,
        status_text=status_text,
        subject=fields.get("Subject"),
        range=fields.get("Range"),
        standfirst=inline(" ".join(paragraphs), asset_prefix),
    )
    return head, end


def _indent_of(line: str) -> int:
    return len(line) - len(LEADING_WS_RE.sub("", line, 1))


def _opens_block(line: str) -> bool:
    """True for the first line of a fenced code block or a blockquote, at any indent."""
    return js_trim_start(line).startswith(("```", ">"))


def _as_block(html: str) -> str:
    """A lone image or video takes the wide figure track. Any other paragraph keeps the measure."""
    return (
        f'<figure class="figure">{html}</figure>'
        if LONE_IMAGE_RE.search(html)
        else f"<p>{html}</p>"
    )


class Renderer:
    """The body below the header, block by block."""

    def __init__(
        self,
        lines: list[str],
        source: str,
        start: int,
        asset_prefix: str,
        asset_check: AssetCheck | None = None,
    ) -> None:
        self.lines = lines
        self.asset_check = asset_check
        self.source = source
        self.start = start
        self.asset_prefix = asset_prefix
        self.i = 0
        self.out: list[str] = []
        self.toc: list[TocEntry] = []
        self.anchors: set[str] = set()

    def peek(self) -> str | None:
        return self.lines[self.i] if self.i < len(self.lines) else None

    def run(self) -> str:
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not js_trim(line):
                self.i += 1
            elif line.startswith("```"):
                self.out.append(self.code())
            elif line.startswith(">"):
                self.out.append(self.quote())
            elif line.startswith("#"):
                self.heading()
            elif line.startswith("| "):
                self.table()
            elif UL_RE.search(line):
                self.ulist(0)
            elif OL_RE.search(line):
                self.olist(0)
            else:
                self.para()
        return "\n".join(self.out)

    def fail(self, index: int, message: str) -> NoReturn:
        raise SpecError(f"{self.source}:{self.start + index + 1}: {message}")

    def check_line(self, line: str, index: int) -> None:
        _check_images(
            without_code_spans(line), lambda message: self.fail(index, message), self.asset_check
        )

    def inline(self, text: str) -> str:
        return inline(text, self.asset_prefix)

    def anchor(self, text: str) -> str:
        """A slug unique in this page: a repeated heading gets `-2`, `-3` and so on."""
        base = slug(text)
        anchor = base
        n = 2
        while anchor in self.anchors:
            anchor = f"{base}-{n}"
            n += 1
        self.anchors.add(anchor)
        return anchor

    def heading(self) -> None:
        index = self.i
        line = self.lines[self.i]
        self.i += 1
        self.check_line(line, index)
        level = len(line) - len(line.lstrip("#"))
        text = js_trim(line[level:])
        numbered = NUMBERED_RE.search(text)
        anchor = self.anchor(text)
        eyebrow = numbered.group(1) if numbered else ""
        dot = numbered.group(2) if numbered else ""
        title = numbered.group(3) if numbered else text
        if level == 2:
            self.toc.append(TocEntry(anchor, eyebrow, title))
        # The heading text spells the source line, so a copied selection pastes as one line.
        eb = f'<span class="eyebrow">{eyebrow}{dot} </span>' if eyebrow else ""
        no_eyebrow = "" if eyebrow else " no-eyebrow"
        self.out.append(
            f'<h{level} id="{anchor}" class="h{level}{no_eyebrow}">'
            f"{eb}{self.inline(title)}</h{level}>"
        )

    def para(self) -> None:
        # The first line is taken before any test, so this always moves on.
        self.check_line(self.lines[self.i], self.i)
        buffer = [js_trim(self.lines[self.i])]
        self.i += 1
        while True:
            line = self.peek()
            if line is None or not js_trim(line) or PARA_STOP_RE.search(line):
                break
            self.check_line(line, self.i)
            buffer.append(js_trim(line))
            self.i += 1
        self.out.append(_as_block(self.inline(" ".join(buffer))))

    def quote(self, marker: int = -1) -> str:
        """A blockquote. In a list item, `marker` is the column of the item marker: each line
        sits right of it, and its indent is stripped."""
        buffer: list[str] = []
        while True:
            line = self.peek()
            if line is None or (marker >= 0 and _indent_of(line) <= marker):
                break
            text = js_trim_start(line) if marker >= 0 else line
            if not text.startswith(">"):
                break
            self.check_line(line, self.i)
            stripped = js_trim(QUOTE_MARK_RE.sub("", text, 1))
            if stripped:
                buffer.append(stripped)
            self.i += 1
        return f"<blockquote><p>{self.inline(' '.join(buffer))}</p></blockquote>"

    def code(self, marker: int = -1) -> str:
        """A fenced code block. In a list item, `marker` is the column of the item marker. Each
        body line loses the indent of the opening fence, as in CommonMark, and a line at or left
        of the marker is refused."""
        opening = self.lines[self.i]
        width = _indent_of(opening) if marker >= 0 else 0
        lang = js_trim(opening[width + 3 :]) or "text"
        self.i += 1
        body: list[str] = []
        while True:
            line = self.peek()
            if line is None:
                break
            if marker >= 0 and js_trim(line) and _indent_of(line) <= marker:
                self.fail(self.i, FENCE_LEAVES_ITEM)
            text = line[min(width, _indent_of(line)) :]
            if (js_trim_start(text) if marker >= 0 else text).startswith("```"):
                break
            body.append(text)
            self.i += 1
        self.i += 1
        return (
            f'<div class="code" data-lang="{esc(lang, True)}"><pre><code>'
            f"{esc(chr(10).join(body))}</code></pre></div>"
        )

    def table(self) -> None:
        rows: list[list[str]] = []
        while True:
            line = self.peek()
            if line is None or not line.startswith("|"):
                break
            self.check_line(line, self.i)
            body = re.sub(r"\|\Z", "", re.sub(r"^\|", "", js_trim(line), count=1), count=1)
            rows.append([js_trim(cell) for cell in body.split("|")])
            self.i += 1
        th = "".join(f"<th>{self.inline(cell)}</th>" for cell in rows[0])
        trs = "".join(
            "<tr>" + "".join(f"<td>{self.inline(cell)}</td>" for cell in row) + "</tr>"
            for row in rows[2:]
        )
        self.out.append(
            f'<div class="scroller"><table><thead><tr>{th}</tr></thead>'
            f"<tbody>{trs}</tbody></table></div>"
        )

    def item_text(self, marker: re.Pattern[str]) -> str:
        """One list item's own text with the lines it wraps onto. Any list marker ends it."""
        index = self.i
        line = self.lines[self.i]
        self.check_line(line, index)
        text = marker.sub("", js_trim(line), 1)
        self.i += 1
        cont: list[str] = []
        while True:
            following = self.peek()
            if following is None or not js_trim(following):
                break
            if ANY_ITEM_RE.search(following):
                break
            if following.startswith(("#", "|")) or _opens_block(following):
                break
            self.check_line(following, self.i)
            cont.append(js_trim(following))
            self.i += 1
        return " ".join([text, *cont])

    def item_blocks(self, indent: int) -> str:
        """The nested lists, fenced code blocks, blockquotes and continuation paragraphs that
        belong to an open item.

        A line indented deeper than the item's marker belongs to the item and keeps the list
        open, so an ordered list does not restart its numbers.
        """
        html = ""
        while True:
            j = self.i
            while j < len(self.lines) and not js_trim(self.lines[j]):
                j += 1
            if j >= len(self.lines):
                break
            line = self.lines[j]
            cur = _indent_of(line)
            if cur <= indent:
                break
            self.i = j
            if js_trim_start(line).startswith("```"):
                html += self.code(indent)
            elif js_trim_start(line).startswith(">"):
                html += self.quote(indent)
            elif NESTED_UL_RE.search(line):
                html += self.ulist(cur)
            elif NESTED_OL_RE.search(line):
                html += self.olist(cur)
            else:
                html += self.item_paragraph(indent)
        return html

    def item_paragraph(self, indent: int) -> str:
        buffer: list[str] = []
        while True:
            line = self.peek()
            if line is None or not js_trim(line) or _indent_of(line) <= indent:
                break
            if buffer and (ANY_ITEM_RE.search(line) or _opens_block(line)):
                break
            self.check_line(line, self.i)
            buffer.append(js_trim(line))
            self.i += 1
        return f"<p>{self.inline(' '.join(buffer))}</p>"

    def _list(self, indent: int, nested: re.Pattern[str], marker: re.Pattern[str], tag: str) -> str:
        """A list at `indent`. A nested list returns its markup, a top-level one appends it."""
        items: list[str] = []
        while True:
            line = self.peek()
            if line is None:
                break
            if not js_trim(line):
                following = self.lines[self.i + 1] if self.i + 1 < len(self.lines) else ""
                if not nested.search(following) or _indent_of(following) != indent:
                    break
                self.i += 1
                continue
            if not nested.search(line) or _indent_of(line) != indent:
                break
            text = self.inline(self.item_text(marker))
            items.append(f"<li>{text}{self.item_blocks(indent)}")
        chunk = f"<{tag}>" + "".join(f"{item}</li>" for item in items) + f"</{tag}>"
        if indent:
            return chunk
        self.out.append(chunk)
        return ""

    def ulist(self, indent: int) -> str:
        return self._list(indent, NESTED_UL_RE, UL_RE, "ul")

    def olist(self, indent: int) -> str:
        return self._list(indent, NESTED_OL_RE, OL_RE, "ol")


def render_with_template(
    markdown: str, source: str, shell: str, layout: Layout, asset_check: AssetCheck | None = None
) -> tuple[str, int]:
    """The page for `markdown` in `shell`, and its number of `##` sections."""
    missing = [slot for slot in PLACEHOLDERS if slot not in shell]
    if missing:
        raise SpecError(f"{TEMPLATE}: missing {', '.join(missing)} placeholder(s)")

    domain = domain_of(source, layout)
    lines = markdown.split("\n")
    head, start = parse_head(lines, source, layout, asset_check)
    renderer = Renderer(lines[start:], source, start, asset_prefix_for(source, layout), asset_check)
    body = renderer.run()
    nav = "".join(
        f'<li><a href="#{entry.anchor}"><span class="n">{entry.eyebrow}</span>'
        f"{esc(plain(entry.title))}</a></li>"
        for entry in renderer.toc
    )
    values = {
        "NAV": nav,
        "BODY": body,
        "TITLE": esc(head.title),
        "DATE": esc(head.date),
        "STATUS": esc(head.status_text),
        "SUBJECT": "" if head.subject is None else esc(head.subject),
        "RANGE": "" if head.range is None else esc(head.range),
        "STANDFIRST": head.standfirst,
        "KIND": esc(domain.label),
        "KIND_CLASS": f"kind-{domain.kind}",
        "SOURCE": esc(source),
    }
    chip = "chip is-live" if head.status in LIVE_STATUSES else "chip"
    chips = {
        STATUS_SLOT: f'<li class="{chip}">{values["STATUS"]}</li>',
        SUBJECT_SLOT: "" if head.subject is None else f'<li class="chip">{values["SUBJECT"]}</li>',
        RANGE_SLOT: "" if head.range is None else f'<li class="chip">{values["RANGE"]}</li>',
    }

    def fill(match: re.Match[str]) -> str:
        whole = match.group(0)
        if whole in chips:
            return chips[whole]
        name = match.group(1)
        if name not in values:
            raise SpecError(f"{TEMPLATE}: {{{{{name}}}}} has no substitution")
        return values[name]

    return PLACEHOLDER_RE.sub(fill, shell), len(renderer.toc)


def render(
    markdown: str, source: str, layout: Layout, asset_check: AssetCheck | None = None
) -> tuple[str, int]:
    shell = TEMPLATE.read_text(encoding="utf-8")
    return render_with_template(markdown, source, shell, layout, asset_check)


def _tracked(dst: str, cwd: Path | None) -> bool:
    """Whether git already tracks `dst`, which makes it a hand-written page."""
    try:
        done = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", dst],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return done.returncode == 0


def render_file(
    src: str, layout: Layout, dst: str | None = None, cwd: Path | None = None
) -> tuple[str, int]:
    """Render `src` to `dst`, or to its derived page. Paths resolve against `cwd`.

    Returns the page path as given or derived, and the number of sections.
    """
    page = dst if dst is not None else dist_for(src, layout)
    if not inside_out(page, layout):
        raise SpecError(
            f"{page}: a rendered page belongs under its domain's {layout.out}/, and this path is "
            f"outside one. Pass no destination and it is derived, or name one inside a "
            f"{layout.out}/."
        )
    if _tracked(page, cwd):
        raise SpecError(
            f"{page}: already tracked by git; rendering would overwrite a hand-written page. "
            "If it should now be generated, `git rm` it first; "
            "otherwise render to a different path."
        )
    base = cwd or Path.cwd()
    try:
        markdown = (base / src).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise SpecError(f"{src}: {error.strerror or error}") from error
    folder = asset_folder(src, layout)

    def asset_check(url: str) -> str | None:
        if (base / folder / url).is_file():
            return None
        return f"the image {url} has no file at {folder}/{url}"

    html, sections = render(markdown, src, layout, asset_check)
    target = base / page
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as out:
            out.write(html)
    except OSError as error:
        raise SpecError(f"{src}: cannot write {page}: {error.strerror or error}") from error
    return page, sections


USAGE = "usage: ah spec render <file.md> [--out <dir>] | --all"


def refusal_line(src: str, error: SpecError) -> str:
    """The line of one refusal of `--all`: it names `src` and never comes out empty."""
    text = str(error).strip() or f"{type(error).__name__} gave no reason"
    return text if text.startswith(src) else f"{src}: {text}"


def render_all(layout: Layout, cwd: Path) -> int:
    """Render every source of every spec folder. A refused source does not stop the rest."""
    found = layout.sources(cwd)
    written = 0
    for src in found:
        try:
            page, sections = render_file(src, layout, cwd=cwd)
        except SpecError as error:
            print(refusal_line(src, error), file=sys.stderr)
            continue
        written += 1
        print(f"wrote {page} ({sections} sections)")
    print(f"rendered {written} of {len(found)} specs")
    return 0 if written == len(found) else 1


def main(argv: list[str], layout: Layout) -> int:
    args = list(argv)
    if args == ["--all"]:
        return render_all(layout, Path.cwd())
    out_dir: str | None = None
    if "--out" in args:
        at = args.index("--out")
        if at + 1 >= len(args):
            print(USAGE, file=sys.stderr)
            return 2
        out_dir = args[at + 1]
        del args[at : at + 2]
    if len(args) != 1 or args[0].startswith("-"):
        print(USAGE, file=sys.stderr)
        return 2
    src = args[0]
    dst: str | None = None
    if out_dir is not None:
        stem = EXTENSION_RE.sub("", _basename(src))
        dst = f"{out_dir.rstrip('/')}/{stem}.html"
    try:
        page, sections = render_file(src, layout, dst)
    except SpecError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"wrote {page} ({sections} sections)")
    return 0
