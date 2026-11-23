"""The drawing library of spec mockups. Read `SKILL.md` beside this file first.

A spec's board script imports this module, builds one `Board` per picture and saves SVG:
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[3]          # docs/specs/assets/<file>.py
    sys.path.insert(0, str(ROOT / ".claude/skills/lw-spec-mockup"))
    from boards import Board, tokens, render

Colours come from the token files at run time. The skill adapter names them, the icon source and
the token name of each role in `ROLES`.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

# .claude/skills/lw-spec-mockup/boards.py: the project root is three folders up.
ROOT = Path(__file__).resolve().parents[3]

FONT = "Inter, ui-sans-serif, system-ui, 'Segoe UI', Arial, sans-serif"

# The board around the frames. One look for every spec, so the boards read as one set.
BOARD_BG = "#f2f2f0"
BOARD_INK = "#18181b"
BOARD_MUTED = "#71717a"
BOARD_LINE = "#dcdcd8"
PIN_FILL = "#f5c842"
PIN_INK = "#18181b"

PHONE_W, PHONE_H = 390, 844
DESKTOP_W, DESKTOP_H = 1440, 900
GUTTER = 48

# The font files that `embed_font` reads, each with its CSS `format()` name.
FONT_FORMATS = {"woff2": "woff2", "woff": "woff", "ttf": "truetype", "otf": "opentype"}


# The colour roles of the base pieces (`button`, `field`, `dialog` and the rest), each mapped to
# the token that holds it. A project maps its own token names with `Board(roles=...)`.
ROLES = {
    "surface": "surface",
    "ink": "ink",
    "muted": "muted",
    "line": "line",
    "accent": "accent",
    "on-accent": "on-accent",
    "control": "control",
    "scrim": "scrim",
    "danger": "danger",
    "success": "success",
}


def _main_checkout() -> Path | None:
    done = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    return Path(done.stdout.strip()).parent if done.returncode == 0 else None


def node_module(rel: str) -> Path:
    """A path under `node_modules`. A worktree often has none, and the main checkout's serves."""
    main = _main_checkout()
    for base in (ROOT, *([main] if main else [])):
        found = base / "node_modules" / rel
        if found.exists():
            return found.resolve()
    raise FileNotFoundError(f"no node_modules/{rel} here or in the main checkout: install first")


def _path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == "node_modules" and not (ROOT / p).exists():
        return node_module(str(Path(*p.parts[1:])))
    return ROOT / p


CONFIG_FILE = ".agents/harness.toml"


def library_dir(root: Path = ROOT) -> Path | None:
    """The folder that `specs.mockupLibrary` names, or None when the key is empty or absent."""
    file = root / CONFIG_FILE
    if not file.is_file():
        return None
    specs = tomllib.loads(file.read_text(encoding="utf-8")).get("specs", {})
    value = specs.get("mockupLibrary", "") if isinstance(specs, dict) else ""
    if value == "":
        return None
    if not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError(
            f"specs.mockupLibrary must be a folder path relative to the repository root, "
            f"with no `..` part, got {value!r}. Fix {file}."
        )
    folder = root / value
    if not folder.is_dir():
        raise FileNotFoundError(
            f"specs.mockupLibrary names {value}, which is no folder of {root}. "
            f"Make the folder or fix {file}."
        )
    return folder


def add_library(root: Path = ROOT) -> Path | None:
    """Puts the folder of `specs.mockupLibrary` at the end of the import path, so a project
    module never hides this library or a standard module. Gives the folder."""
    folder = library_dir(root)
    if folder is not None and str(folder) not in sys.path:
        sys.path.append(str(folder))
    return folder


# Runs at import, so the next line of a board script can import a module of the project.
add_library()


# Tokens


def _blocks(css: str) -> list[tuple[str, str, bool]]:
    """Every `selector { body }` of a CSS text, with whether it sits inside an @media block."""
    out = []
    stack: list[tuple[str, int]] = []
    for m in re.finditer(r"([^{}]+)\{|\}", css):
        if m.group(0) == "}":
            if stack:
                sel, start = stack.pop()
                if not sel.startswith("@"):
                    out.append(
                        (sel, css[start : m.start()], any(s.startswith("@media") for s, _ in stack))
                    )
            continue
        sel = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S).strip()
        stack.append((sel, m.end()))
    return out


def _decls(body: str) -> dict[str, str]:
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return {k: " ".join(v.split()) for k, v in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body)}


class Tokens(dict):
    """`t["accent"]` is the resolved value of `--accent`. A missing token raises KeyError."""

    def __missing__(self, key: str) -> str:
        raise KeyError(f"no token --{key}")

    def resolve(self, value: str, seen: frozenset = frozenset()) -> str:
        def sub(m: re.Match) -> str:
            name, fallback = m.group(1), m.group(2)
            if name in seen:
                raise ValueError(f"token cycle at --{name}")
            if name in self:
                return self.resolve(dict.__getitem__(self, name), seen | {name})
            if fallback is not None:
                return self.resolve(fallback.strip(), seen)
            raise KeyError(f"no token --{name}")

        return re.sub(r"var\(--([\w-]+)\s*(?:,\s*([^()]*))?\)", sub, value)

    def __getitem__(self, key: str) -> str:
        return self.resolve(dict.__getitem__(self, key), frozenset({key}))


def tokens(
    files: str | Path | list[str | Path],
    theme: str = "light",
    base: tuple[str, ...] = (":root",),
    dark: tuple[str, ...] = (".dark", "[data-theme='dark']", '[data-theme="dark"]'),
    overlay: str | Path | None = None,
    sets: Mapping[str, tuple[str, ...]] | None = None,
) -> Tokens:
    """The custom properties of the project's token files, in order, outside @media blocks.

    `base` names the selectors of the light values. With `theme="dark"`, the `dark` selectors
    add their values on top. `sets` names more token sets, each with its selectors, such as
    `{"contrast": (".contrast",)}` for `theme="contrast"`. Another `theme` raises. `overlay` is a
    CSS file of the same shape whose values replace the others: a proposed restyle, drawn
    before it ships.
    """
    named = {"dark": dark, **(sets or {})}
    if theme != "light" and theme not in named:
        raise ValueError(f"unknown token set {theme!r}: light, {', '.join(named)}")
    extra = set(named[theme]) if theme != "light" else set()
    paths = [_path(f) for f in (files if isinstance(files, list) else [files])]
    blocks = [blk for p in paths for blk in _blocks(p.read_text())]

    def selectors(s: str) -> set[str]:
        return {x.strip() for x in s.split(",")}

    light = [b for s, b, media in blocks if not media and selectors(s) & set(base)]
    arm = [b for s, b, media in blocks if not media and selectors(s) & extra]
    if not light or (theme != "light" and not arm):
        raise ValueError(f"no {theme} values in {', '.join(str(p) for p in paths)}")
    t = Tokens()
    for body in light + arm:
        t.update(_decls(body))
    if overlay is not None:
        for s, body, media in _blocks(_path(overlay).read_text()):
            if not media and selectors(s) & (set(base) | extra):
                t.update(_decls(body))
    return t


# Glyphs


def _lucide(folder: Path, name: str) -> str:
    """The SVG nodes of one lucide-react icon module (`<folder>/<name>.mjs` or `.js`)."""
    src_file = next(
        (folder / f"{name}{ext}" for ext in (".mjs", ".js") if (folder / f"{name}{ext}").is_file()),
        None,
    )
    if src_file is None:
        raise FileNotFoundError(f"no lucide icon {name!r} in {folder}")
    src = src_file.read_text()
    alias = re.search(r"export \{ default \} from '\./([\w-]+)\.m?js'", src)
    if alias:
        return _lucide(folder, alias.group(1))
    node = re.search(r"node:\s*\[(.*?)\]\s*\}\s*;", src, re.S) or re.search(
        r"=\s*\[(\s*\[.*?\])\s*\]\s*;", src, re.S
    )
    if not node:
        raise ValueError(f"lucide icon {name!r} has no node list")
    parts = []
    for tag, attrs in re.findall(r'\[\s*"(\w+)",\s*\{(.*?)\}\s*\]', node.group(1), re.S):
        kv = [(k, v) for k, v in re.findall(r'(\w+):\s*"([^"]*)"', attrs) if k != "key"]
        parts.append(f"<{tag} " + " ".join(f'{k}="{v}"' for k, v in kv) + "/>")
    return "".join(parts)


# A glyph: its SVG nodes, its viewBox, and whether it is filled (True) or stroked.
Glyph = tuple[str, str, bool]
SVG_FILE_RE = re.compile(r"<svg\b([^>]*)>(.*)</svg>", re.S)
VIEWBOX_RE = re.compile(r'viewBox="([^"]+)"')


def _svg_file(folder: Path, name: str) -> Glyph:
    """One icon of a folder of `.svg` files, such as Material Symbols or Android drawables."""
    found = SVG_FILE_RE.search((folder / f"{name}.svg").read_text())
    if not found:
        raise ValueError(f"{folder / name}.svg holds no <svg> element")
    box = VIEWBOX_RE.search(found.group(1))
    return found.group(2), box.group(1) if box else "0 0 24 24", 'fill="none"' not in found.group(1)


def _json_paths(table: Path, name: str) -> Glyph:
    """One icon of a JSON file: `{name: "d"}` or `{name: {"d", "viewBox", "stroke"}}`."""
    entry = json.loads(table.read_text()).get(name)
    if entry is None:
        raise KeyError(f"no icon {name!r} in {table}")
    if isinstance(entry, str):
        entry = {"d": entry}
    nodes = entry.get("svg") or f'<path d="{entry["d"]}"/>'
    return nodes, entry.get("viewBox", "0 0 24 24"), not entry.get("stroke", False)


# The icon sources by kind. A project adds its own loader here from its helper module.
ICON_LOADERS: dict[str, Callable[[Path, str], Glyph]] = {
    "lucide": lambda folder, name: (_lucide(folder, name), "0 0 24 24", False),
    "svg": _svg_file,
    "json": _json_paths,
}


def icon_kind(source: Path) -> str:
    """`json` for a file, `svg` for a folder of `.svg` files, else `lucide`."""
    if source.is_file():
        return "json"
    return "svg" if any(source.glob("*.svg")) else "lucide"


# Text measure


def text_width(s: str, size: float, weight: int = 400) -> float:
    """An estimate, good enough to wrap and to cut. Look at the render to be sure."""
    return len(s) * size * (0.56 if weight >= 600 else 0.52)


def wrap(s: str, width: float, size: float, weight: int = 400) -> list[str]:
    lines, cur = [], ""
    for w in s.split():
        cand = f"{cur} {w}".strip()
        if cur and text_width(cand, size, weight) > width:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    return lines + ([cur] if cur else [])


def ellipsis(s: str, width: float, size: float, weight: int = 400) -> str:
    if text_width(s, size, weight) <= width:
        return s
    while s and text_width(s + "...", size, weight) > width:
        s = s[:-1]
    return s.rstrip() + "..."


# The board


@dataclass
class Frame:
    """A device on the board. Draw the screen between `top` and `bottom`, right of `left`."""

    x: float
    y: float
    w: float
    h: float
    top: float
    bottom: float
    left: float = 0

    @property
    def cx(self) -> float:
        return self.x + self.left + (self.w - self.left) / 2


class Board:
    """One picture. `icons` is the icon source, relative to the project root, and `icon_source`
    its kind in `ICON_LOADERS` (found from the path when None). `roles` maps a role of `ROLES` to
    the token name of the project."""

    def __init__(
        self,
        w: int,
        h: int,
        bg: str = BOARD_BG,
        font: str = FONT,
        icons: str | Path | None = None,
        icon_source: str | None = None,
        roles: Mapping[str, str] | None = None,
    ):
        self.w, self.h, self.bg, self.font = w, h, bg, font
        self.icons = _path(icons) if icons is not None else None
        self.icon_source = icon_source or (icon_kind(self.icons) if self.icons else None)
        self.roles = {**ROLES, **(roles or {})}
        self.parts: list[str] = []
        self._defs: list[str] = []
        self._clip = 0

    def role(self, t: Mapping[str, str], name: str) -> str:
        """The colour of a role from the tokens `t`. A missing token raises."""
        return t[self.roles[name]]

    def add(self, svg: str) -> None:
        self.parts.append(svg)

    def rect(self, x, y, w, h, fill, r=0, stroke=None, sw=1, dash=None, opacity=None):
        a = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        a += f' stroke-dasharray="{dash}"' if dash else ""
        a += f' opacity="{opacity}"' if opacity is not None else ""
        self.add(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" '
            f'fill="{fill}"{a}/>'
        )

    def circle(self, cx, cy, r, fill, stroke=None, sw=1):
        a = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        self.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}"{a}/>')

    def line(self, x1, y1, x2, y2, stroke, sw=1, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" '
            f'stroke-width="{sw}"{d}/>'
        )

    def path(self, d, fill="none", stroke=None, sw=1.5):
        a = (
            f' stroke="{stroke}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"'
            if stroke
            else ""
        )
        self.add(f'<path d="{d}" fill="{fill}"{a}/>')

    def text(
        self, x, y, s, size=14, fill=BOARD_INK, weight=400, anchor="start", font=None, max_w=None
    ):
        if max_w:
            s = ellipsis(s, max_w, size, weight)
        f = f' font-family="{escape(font)}"' if font else ""
        self.add(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" '
            f'text-anchor="{anchor}"{f}>{escape(s)}</text>'
        )

    def spans(self, x, y, parts, size=14, anchor="start", font=None):
        """One line in runs of `(text, weight, fill)`. The renderer lays them out, so no gap
        opens."""
        f = f' font-family="{escape(font)}"' if font else ""
        inner = "".join(
            f'<tspan font-weight="{w}" fill="{c}">{escape(s)}</tspan>' for s, w, c in parts
        )
        self.add(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}"{f} '
            f'xml:space="preserve">{inner}</text>'
        )

    def lines(self, x, y, rows, size=14, fill=BOARD_INK, weight=400, gap=1.4, anchor="start"):
        for i, r in enumerate(rows):
            self.text(x, y + i * size * gap, r, size, fill, weight, anchor)

    def _clip_rect(self, x, y, w, h, r) -> str:
        if not r:
            return ""
        self._clip += 1
        cid = f"clip{self._clip}"
        self._defs.append(
            f'<clipPath id="{cid}"><rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'rx="{r}"/></clipPath>'
        )
        return f' clip-path="url(#{cid})"'

    def clip(self, x, y, w, h, r=0) -> None:
        """Paint what follows only inside the box, such as a list under a header. `clip_end`
        closes it."""
        self._clip += 1
        cid = f"clip{self._clip}"
        self._defs.append(
            f'<clipPath id="{cid}"><rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'rx="{r}"/></clipPath>'
        )
        self.add(f'<g clip-path="url(#{cid})">')

    def clip_end(self) -> None:
        self.add("</g>")

    def gradient(self, stops: list[tuple], x2=1, y2=0, radial=False) -> str:
        """A fill of `[(offset, colour), ...]`, left to right unless `x2`, `y2` say. A third value
        in a stop is its opacity: `(1, t["bg"], 0)` fades a token out. Gives `url(#id)`."""
        self._clip += 1
        gid = f"grad{self._clip}"
        body = "".join(
            f'<stop offset="{s[0]}" stop-color="{s[1]}"'
            + (f' stop-opacity="{s[2]}"' if len(s) > 2 else "")
            + "/>"
            for s in stops
        )
        if radial:
            self._defs.append(f'<radialGradient id="{gid}">{body}</radialGradient>')
        else:
            self._defs.append(
                f'<linearGradient id="{gid}" x1="0" y1="0" x2="{x2}" y2="{y2}">{body}'
                "</linearGradient>"
            )
        return f"url(#{gid})"

    def image(self, path: str | Path, x, y, w, h, r=0, fit="slice"):
        """Real art from the repository, embedded as base64. Never a stand-in."""
        p = _path(path)
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        data = base64.b64encode(p.read_bytes()).decode()
        mode = "xMidYMid slice" if fit == "slice" else "xMidYMid meet"
        clip = self._clip_rect(x, y, w, h, r)
        self.add(
            f'<image href="data:{mime};base64,{data}" x="{x}" y="{y}" width="{w}" height="{h}" '
            f'preserveAspectRatio="{mode}"{clip}/>'
        )

    def embed_font(self, family: str, path: str | Path, weight: str = "100 900") -> str:
        """Embeds a font file (`.woff2`, `.woff`, `.ttf` or `.otf`) as an `@font-face`, so a
        brand face renders the same on every machine. Gives the family for `font=`."""
        p = _path(path)
        kind = p.suffix.lower().lstrip(".")
        if kind not in FONT_FORMATS:
            raise ValueError(f"{p.name}: embed_font reads {', '.join(FONT_FORMATS)} only")
        data = base64.b64encode(p.read_bytes()).decode()
        self._defs.append(
            f"<style>@font-face{{font-family:'{family}';font-weight:{weight};"
            f"src:url(data:font/{kind};base64,{data}) format('{FONT_FORMATS[kind]}')}}</style>"
        )
        return f"'{family}'"

    def crop(self, path: str | Path, x, y, w, h, sx, sy, sw, sh, r=0):
        """A region of a real PNG capture, in the capture's pixels, drawn into a box."""
        raw = _path(path).read_bytes()
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"{Path(path).name}: crop reads PNG only")
        iw, ih = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        data = base64.b64encode(raw).decode()
        clip = self._clip_rect(x, y, w, h, r)
        self.add(
            f'<g{clip}><svg x="{x}" y="{y}" width="{w}" height="{h}" viewBox="{sx} {sy} {sw} {sh}" '
            f'preserveAspectRatio="xMidYMin slice"><image href="data:image/png;base64,{data}" '
            f'width="{iw}" height="{ih}"/></svg></g>'
        )

    def icon(self, name: str, x, y, size=24, color=BOARD_INK, sw=2):
        """A glyph of the board's icon source with its top left at (x, y)."""
        if self.icons is None or self.icon_source is None:
            raise ValueError("this board has no icon source: pass icons= to Board")
        nodes, box, filled = ICON_LOADERS[self.icon_source](self.icons, name)
        paint = (
            'fill="currentColor"'
            if filled
            else f'fill="none" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round" '
            'stroke-linejoin="round"'
        )
        self.add(
            f'<svg x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" viewBox="{box}" '
            f'{paint} style="color:{color}">{nodes}</svg>'
        )

    def glyph(self, d: str, x, y, size, color, sw=2):
        """A stroked path in a 24 x 24 box at (x, y): a mark that a component draws inline."""
        self.add(
            f'<svg x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'fill="none">'
            f'<path d="{d}" stroke="{color}" stroke-width="{sw}" stroke-linecap="round" '
            f'stroke-linejoin="round"/></svg>'
        )

    def title(self, head: str, sub: str = "") -> None:
        """The heading of the board, top left, and one muted line under it."""
        self.text(GUTTER, 52, head, 22, BOARD_INK, 800)
        if sub:
            self.text(GUTTER, 78, sub, 14, BOARD_MUTED)

    def column_head(self, frame: Frame, label: str) -> None:
        """`Today`, `Target` or `Reference` over a frame."""
        self.text(frame.x, frame.y - 16, label.upper(), 12, BOARD_MUTED, 700)

    def row_label(self, x, y, label: str) -> None:
        """The state of a row: `Empty`, `Loading`, `Error`, `Long text`."""
        self.text(x, y, label, 16, BOARD_INK, 700)

    def caption(self, frame: Frame, head: str, sub: str = "") -> None:
        """A bold line under a frame, and at most one muted line."""
        y = frame.y + frame.h + 30
        self.text(frame.x, y, head, 15, BOARD_INK, 700, max_w=frame.w)
        if sub:
            self.text(frame.x, y + 22, sub, 13, BOARD_MUTED, max_w=frame.w)

    def pin(self, x, y, n: int) -> None:
        """A numbered pin on the element that changes. The legend explains it."""
        self.circle(x, y, 12, PIN_FILL, stroke="#ffffff", sw=2)
        self.text(x, y + 4.5, str(n), 12, PIN_INK, 800, anchor="middle", font=FONT)

    def legend(self, x, y, items: list[tuple], width: float, cols: int = 3) -> float:
        """Numbered notes `(n, head, body, spec_id)` under a frame. Gives the y below them.

        A body runs to two lines at most. A longer note belongs in the spec prose.
        """
        col_w = (width - (cols - 1) * 32) / cols
        row_h, bottom = 86, y
        for i, item in enumerate(items):
            n, head, body = item[0], item[1], item[2]
            ref = item[3] if len(item) > 3 else ""
            cx, cy = x + (i % cols) * (col_w + 32), y + (i // cols) * row_h
            self.pin(cx + 12, cy + 12, n)
            self.text(
                cx + 34,
                cy + 17,
                head + (f"  ({ref})" if ref else ""),
                15,
                BOARD_INK,
                700,
                max_w=col_w - 34,
            )
            rows = wrap(body, col_w - 34, 13)
            if len(rows) > 2:
                raise ValueError(f"legend note {n} runs to {len(rows)} lines; keep it to two")
            self.lines(cx + 34, cy + 39, rows, 13, BOARD_MUTED)
            bottom = cy + row_h
        return bottom

    def phone(
        self,
        x,
        y,
        bg: str,
        ink: str,
        surface: str = "web",
        url: str = "example.com",
        bar: str | None = None,
    ) -> Frame:
        """A 390 x 844 phone. `surface` is `web` (a browser with a URL bar) or `app`. Gives the
        frame."""
        self.rect(x, y, PHONE_W, PHONE_H, bg, r=44, stroke=BOARD_LINE)
        self._statusbar(x, y + 14, ink)
        top = y + 54
        if surface == "web":
            self.rect(x + 16, top + 2, PHONE_W - 32, 30, bar or BOARD_LINE, r=15)
            self.text(x + PHONE_W / 2, top + 22, url, 13, ink, 600, anchor="middle", font=FONT)
            top += 40
        elif surface != "app":
            raise ValueError(f"unknown phone surface {surface!r}: web or app")
        self.rect(x + PHONE_W / 2 - 67, y + PHONE_H - 14, 134, 5, ink, r=2.5)
        return Frame(x, y, PHONE_W, PHONE_H, top, y + PHONE_H - 22)

    def screenshot(
        self, path: str | Path, x, y, w: int = PHONE_W, h: int = PHONE_H, r: int = 44
    ) -> Frame:
        """A real capture of the shipped screen, for the `Today` column. It is cropped, never
        stretched."""
        self.rect(x, y, w, h, "#000000", r=r)
        self.image(path, x, y, w, h, r=r, fit="slice")
        return Frame(x, y, w, h, y, y + h)

    def desktop(
        self,
        x,
        y,
        bg: str,
        url: str = "example.com",
        w: int = DESKTOP_W,
        h: int = DESKTOP_H,
        chrome: str = "dark",
    ) -> Frame:
        """A browser window. `chrome` is `dark` or `light`. Draw the page between `top` and
        `bottom`."""
        bar, pill, ink = {
            "dark": ("#1f1f22", "#2c2c30", "#a1a1aa"),
            "light": ("#e8e8ec", "#ffffff", "#6e6e73"),
        }[chrome]
        self.rect(x, y, w, h, bg, r=12, stroke=BOARD_LINE)
        self.rect(x, y, w, 40, bar, r=12)
        self.rect(x, y + 28, w, 12, bar)
        for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            self.circle(x + 20 + i * 18, y + 20, 6, c)
        self.rect(x + w / 2 - 220, y + 8, 440, 24, pill, r=12)
        self.text(x + w / 2, y + 25, url, 12, ink, 500, anchor="middle", font=FONT)
        return Frame(x, y, w, h, y + 40, y + h)

    def sidebar_app(
        self,
        x,
        y,
        bg: str,
        side_bg: str,
        ink: str,
        muted: str,
        active_bg: str,
        nav: list,
        active: str,
        brand: str,
        w: int = DESKTOP_W,
        h: int = DESKTOP_H,
    ) -> Frame:
        """An app with a left sidebar, such as a console. `nav` lists labels, or `(heading, None)`
        for a group."""
        side = 232
        self.rect(x, y, w, h, bg, r=12, stroke=BOARD_LINE)
        self.rect(x, y, side, h, side_bg, r=12)
        self.rect(x + side - 12, y, 12, h, side_bg)
        self.text(x + 22, y + 36, brand, 15, ink, 700, font=FONT)
        cy = y + 76
        for item in nav:
            if isinstance(item, tuple):
                cy += 8
                self.text(x + 22, cy, item[0].upper(), 11, muted, 700, font=FONT)
                cy += 22
                continue
            if item == active:
                self.rect(x + 12, cy - 21, side - 24, 32, active_bg, r=8)
            self.text(
                x + 22,
                cy,
                item,
                14,
                ink if item == active else muted,
                600 if item == active else 400,
                font=FONT,
            )
            cy += 34
        return Frame(x, y, w, h, y + 32, y + h, left=side)

    def console_phone(
        self,
        x,
        y,
        bg: str,
        ink: str,
        bar: str,
        page: str,
        url: str = "example.com",
        line: str = BOARD_LINE,
    ) -> Frame:
        """A console in a phone browser: the URL pill, then a 48 px top bar with the menu mark and
        the page name. Draw the page between `top` and `bottom`."""
        frame = self.phone(x, y, bg, ink, "web", url, bar)
        self.line(x, frame.top + 48, x + PHONE_W, frame.top + 48, line)
        self.glyph("M4 6h16M4 12h16M4 18h16", x + 16, frame.top + 14, 20, ink)
        self.text(x + 50, frame.top + 30, page, 16, ink, 600, font=FONT, max_w=PHONE_W - 66)
        return Frame(x, y, PHONE_W, PHONE_H, frame.top + 48, frame.bottom)

    # The base pieces. Each one draws from the role tokens, so it never invents a colour.

    def scrim(self, x, y, w, h, t: Mapping[str, str], r=0) -> None:
        """The shade behind a dialog or a sheet."""
        self.rect(x, y, w, h, self.role(t, "scrim"), r=r)

    def spinner(self, cx, cy, r, t: Mapping[str, str], sw=2.5) -> None:
        """A loading ring: the track, and a quarter arc in the accent."""
        self.circle(cx, cy, r, "none", stroke=self.role(t, "line"), sw=sw)
        self.path(
            f"M{cx:.1f} {cy - r:.1f} A{r} {r} 0 0 1 {cx + r:.1f} {cy:.1f}",
            stroke=self.role(t, "accent"),
            sw=sw,
        )

    def button(
        self,
        x,
        y,
        w,
        label: str,
        t: Mapping[str, str],
        variant: str = "primary",
        h: int = 44,
        pending: bool = False,
        disabled: bool = False,
    ) -> None:
        """`primary` (accent), `secondary` (control) or `text`. `pending` shows the spinner in
        place of the label."""
        fill, ink = {
            "primary": ("accent", "on-accent"),
            "secondary": ("control", "ink"),
            "text": (None, "accent"),
        }[variant]
        if disabled:
            fill, ink = ("control" if fill else None), "muted"
        if fill:
            self.rect(x, y, w, h, self.role(t, fill), r=h / 2)
        if pending:
            self.spinner(x + w / 2, y + h / 2, 9, t)
            return
        self.text(
            x + w / 2, y + h / 2 + 5, label, 15, self.role(t, ink), 600, "middle", max_w=w - 24
        )

    def field(
        self,
        x,
        y,
        w,
        t: Mapping[str, str],
        value: str = "",
        placeholder: str = "",
        label: str | None = None,
        focus: bool = False,
        h: int = 44,
    ) -> float:
        """A text input with an optional label above it. `focus` draws the accent ring. Gives
        the y under the input."""
        if label:
            self.text(x, y + 14, label, 13, self.role(t, "muted"), 600)
            y += 22
        ring = self.role(t, "accent" if focus else "line")
        self.rect(x, y, w, h, self.role(t, "control"), r=10, stroke=ring, sw=2 if focus else 1)
        shown, ink = (value, "ink") if value else (placeholder, "muted")
        if shown:
            self.text(x + 14, y + h / 2 + 5, shown, 15, self.role(t, ink), max_w=w - 28)
        return y + h

    def toggle(self, x, y, on: bool, t: Mapping[str, str]) -> None:
        """A 44 x 26 switch."""
        track = self.role(t, "accent" if on else "control")
        self.rect(x, y, 44, 26, track, r=13, stroke=None if on else self.role(t, "line"))
        self.circle(x + (31 if on else 13), y + 13, 10, self.role(t, "surface"))

    def link(
        self, x, y, label: str, t: Mapping[str, str], size=14, weight=400, anchor="start"
    ) -> None:
        """An underlined text link in the accent, with its baseline at `y`."""
        self.add(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{self.role(t, "accent")}" '
            f'font-weight="{weight}" text-anchor="{anchor}" text-decoration="underline">'
            f"{escape(label)}</text>"
        )

    def tabbar(
        self, x, y, w, tabs: list[str], active: str, t: Mapping[str, str], h: int = 44
    ) -> float:
        """A row of tabs over a line. The `active` tab has the ink and an accent bar. Gives the
        y under the row."""
        if active not in tabs:
            raise ValueError(f"tabbar: the active tab {active!r} is not one of {tabs}")
        self.line(x, y + h, x + w, y + h, self.role(t, "line"))
        cx = x
        for tab in tabs:
            ink, weight = ("ink", 600) if tab == active else ("muted", 400)
            tw = text_width(tab, 14, weight)
            self.text(cx + 16, y + h / 2 + 5, tab, 14, self.role(t, ink), weight)
            if tab == active:
                self.rect(cx + 16, y + h - 2, tw, 2, self.role(t, "accent"), r=1)
            cx += tw + 32
        if cx - x > w:
            raise ValueError(f"tabbar: the tabs need {cx - x:.0f} px and the row has {w}")
        return y + h

    def toast(self, cx, y, label: str, t: Mapping[str, str], tone: str = "success") -> None:
        """A one-line notice centred on `cx`: `success`, `danger` or `info` (the accent)."""
        w = text_width(label, 14, 600) + 56
        self.rect(cx - w / 2, y, w, 44, self.role(t, "surface"), r=12, stroke=self.role(t, "line"))
        dot = {"success": "success", "danger": "danger", "info": "accent"}[tone]
        self.circle(cx - w / 2 + 22, y + 22, 5, self.role(t, dot))
        self.text(cx - w / 2 + 38, y + 27, label, 14, self.role(t, "ink"), 600)

    def dialog(
        self,
        x,
        y,
        w,
        t: Mapping[str, str],
        head: str,
        body: str = "",
        actions: tuple[str, ...] = (),
        r: int = 20,
    ) -> float:
        """A dialog card: the head, the body, and the actions at the right, the last one primary.
        Draw `scrim` first. Gives the y under the card."""
        rows = wrap(body, w - 48, 15) if body else []
        h = 24 + 26 + len(rows) * 21 + (28 + 44 if actions else 0) + 24
        self.rect(x, y, w, h, self.role(t, "surface"), r=r, stroke=self.role(t, "line"))
        self.text(x + 24, y + 44, head, 18, self.role(t, "ink"), 700, max_w=w - 48)
        self.lines(x + 24, y + 74, rows, 15, self.role(t, "muted"), gap=1.4)
        right = x + w - 24
        for i, label in enumerate(reversed(actions)):
            bw = text_width(label, 15, 600) + 40
            right -= bw
            variant = "primary" if i == 0 else "secondary"
            self.button(right, y + h - 24 - 44, bw, label, t, variant)
            right -= 12
        return y + h

    def _statusbar(self, x, y, ink):
        self.text(x + 52, y + 20, "9:41", 15, ink, 700, anchor="middle", font=FONT)
        for i, h in enumerate((4, 7, 10, 13)):
            self.rect(x + 268 + i * 5, y + 17 - h, 3, h, ink, r=1)
        self.rect(x + 322, y + 5, 24, 12, "none", r=3.5, stroke=ink, sw=1.2)
        self.rect(x + 324, y + 7, 19, 8, ink, r=2)

    def svg(self) -> str:
        defs = f"<defs>{''.join(self._defs)}</defs>" if self._defs else ""
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" font-family="{escape(self.font)}">{defs}'
            f'<rect width="{self.w}" height="{self.h}" '
            f'fill="{self.bg}"/>{"".join(self.parts)}</svg>'
        )

    def save(self, path: str | Path) -> Path:
        p = _path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.svg())
        return p


def render(svg: str | Path, png: str | Path, w: int, h: int) -> Path:
    """Takes a screenshot of an SVG with headless Chrome. READ the PNG after: that is the check."""
    chrome = (
        shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    if not chrome:
        raise RuntimeError("no google-chrome or chromium on PATH")
    svg, png = Path(svg).resolve(), Path(png).resolve()
    png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            f"--screenshot={png}",
            f"--window-size={w},{h}",
            svg.as_uri(),
        ],
        cwd=png.parent,
        check=True,
        capture_output=True,
    )
    return png
