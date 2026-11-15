"""A worked board: a settings page on desktop and on a phone, with pins and a legend.

    python3 .claude/skills/lw-spec-mockup/example.py <out folder> [--tokens <css file>] [--render]

Without `--tokens`, the example uses a small token file that it writes itself. A spec script
reads the project's own token file, which the adapter names.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boards import DESKTOP_H, DESKTOP_W, GUTTER, PHONE_W, Board, render, tokens

DEMO_TOKENS = """:root {
  --bg: #ffffff; --side: #f4f4f5; --ink: #18181b; --muted: #71717a;
  --accent: #2563eb; --active: #e4e4e7;
}
.dark { --bg: #09090b; --side: #18181b; --ink: #fafafa; --muted: #a1a1aa; --active: #27272a; }
"""


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    out = Path(argv[0]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = (
        Path(argv[argv.index("--tokens") + 1]) if "--tokens" in argv else out / "demo-tokens.css"
    )
    if "--tokens" not in argv:
        source.write_text(DEMO_TOKENS)
    t = tokens(source.resolve())

    w = GUTTER * 3 + DESKTOP_W + PHONE_W
    h = 160 + DESKTOP_H + 160
    b = Board(w, h)
    b.title("Settings: the empty state", "Today has no settings page. Target adds one.")
    desk = b.sidebar_app(
        GUTTER,
        160,
        t["bg"],
        t["side"],
        t["ink"],
        t["muted"],
        t["active"],
        [("Account", None), "Profile", "Settings"],
        "Settings",
        "Example",
    )
    b.column_head(desk, "Target")
    b.text(desk.x + desk.left + 48, desk.top + 40, "Settings", 24, t["ink"], 700)
    b.text(
        desk.x + desk.left + 48,
        desk.top + 80,
        "No setting yet. A module adds its own.",
        14,
        t["muted"],
    )
    b.pin(desk.x + desk.left + 24, desk.top + 34, 1)
    phone = b.phone(
        GUTTER * 2 + DESKTOP_W, 160, t["bg"], t["ink"], url="app.example.com", bar=t["side"]
    )
    b.text(phone.x + 44, phone.top + 40, "Settings", 20, t["ink"], 700)
    b.pin(phone.x + 24, phone.top + 34, 2)
    b.legend(
        GUTTER,
        160 + DESKTOP_H + 60,
        [
            (1, "The page title", "One line, the name of the page.", "R1"),
            (2, "The phone layout", "The sidebar folds into a menu.", "R2"),
        ],
        w - 2 * GUTTER,
    )
    svg = b.save(out / "desktop-empty.svg")
    print(svg)
    if "--render" in argv:
        print(render(svg, out / "desktop-empty.png", w, h))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
