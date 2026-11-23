"""The picture files that a board links.

A board is an SVG under `assets/` of a spec folder. A browser loads no file for an SVG that a
page shows through `<img>`, so an `<image>` element that names a file stays empty on the review
page. `page_source` gives the board as a `data:` URI with each linked file written into it. The
renderer puts that URI in the page, and the source asset stays as it is.
docs/spec.md ("Pictures that a board links") has the rules.
"""

from __future__ import annotations

import base64
import html
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote

from agent_harness.spec.layout import ASSETS

# An XML comment, or an `<image>` element in the group `image`. A comment matches first, so an
# element inside one is no link.
IMAGE_RE = re.compile(r"<!--.*?-->|(?P<image><image\b[^>]*>)", re.DOTALL)
HREF_RE = re.compile(r"""(\s(?:xlink:)?href\s*=\s*)(["'])(.*?)\2""", re.DOTALL)
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
}
SVG = "image/svg+xml"


class BoardError(Exception):
    """A linked file that the page cannot show. The message names the board and the link."""


def file_of(href: str) -> str | None:
    """The file path that an href names, with its percent escapes decoded. None for a `data:`
    URI, a URL or a `#fragment`."""
    path = href.partition("#")[0]
    if not path or SCHEME_RE.search(path) or path.startswith(("//", "\\\\")):
        return None
    return unquote(path)


def linked_files(svg: str) -> list[str]:
    """The path of each file that an `<image>` element of `svg` links (see `file_of`)."""
    found: list[str] = []
    for element in IMAGE_RE.finditer(svg):
        for match in HREF_RE.finditer(element.group("image") or ""):
            path = file_of(html.unescape(match.group(3)))
            if path is not None:
                found.append(path)
    return found


def _data_uri(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _inlined(root: Path, folder: str, name: str, chain: tuple[str, ...]) -> tuple[str, bool]:
    """The text of the board `name` (a path under `assets/`) with each linked file written in,
    and whether it links one. `chain` holds the boards that led here."""
    assets = f"{folder}/{ASSETS}"
    text = (root / assets / name).read_text(encoding="utf-8", errors="replace")
    changed = False

    def fail(href: str, reason: str) -> BoardError:
        return BoardError(f"the board {ASSETS}/{name} links {href}, which {reason}")

    def href(match: re.Match[str]) -> str:
        nonlocal changed
        written = html.unescape(match.group(3))
        path = file_of(written)
        if path is None:
            return match.group(0)
        target = posixpath.normpath(posixpath.join(posixpath.dirname(name), path))
        if target == ".." or target.startswith(("../", "/")):
            raise fail(path, f"is outside {assets}")
        file = root / assets / target
        if not file.is_file():
            raise fail(path, f"has no file at {assets}/{target}")
        mime = MIME.get(posixpath.splitext(target)[1].lower())
        if mime is None:
            raise fail(path, "is not a PNG, JPEG, GIF, WebP, AVIF or SVG file")
        if mime == SVG:
            if target in chain:
                raise fail(path, "links it back")
            data = _inlined(root, folder, target, (*chain, target))[0].encode("utf-8")
        else:
            data = file.read_bytes()
        changed = True
        fragment = written.partition("#")[2]
        uri = _data_uri(mime, data) + (f"#{fragment}" if fragment else "")
        return f"{match.group(1)}{match.group(2)}{uri}{match.group(2)}"

    def element(match: re.Match[str]) -> str:
        return HREF_RE.sub(href, match.group(0)) if match.group("image") else match.group(0)

    out = IMAGE_RE.sub(element, text)
    return out, changed


def page_source(root: Path, folder: str, url: str) -> str | None:
    """The `src` that shows the board `url` (`assets/<file>.svg` of the spec folder `folder`)
    with its linked files, or None for a board that links no file.

    Raises `BoardError` for a linked file that is missing, outside `assets/`, of an unknown
    picture type, or a board that links back to a board before it.
    """
    name = url.split("/", 1)[1]
    text, changed = _inlined(root, folder, name, (posixpath.normpath(name),))
    return _data_uri(SVG, text.encode("utf-8")) if changed else None
