---
name: lw-spec-mockup
description: Use when a spec or a plan needs a picture of a screen, a state, a dialog, a console page or a flow, when the user asks "where is the design", "draw it" or "show every state", and before you commit or change a board in docs/specs/assets. Draws generated SVG boards with one library. Not for the spec text, which is lw-spec-write.
---

# Spec mockups

This skill says how to draw. `lw-spec-write` says where the spec goes and how the page renders.

## Product facts

The adapter folder comes from the config. Read it first:

```bash
ah config get harness.adapterDir
```

Then read `<adapterDir>/spec-mockup/`. The project owns it. It names:

| Fact | Example |
|---|---|
| The token files of each app, in order | `apps/console/src/styles/tokens.css` |
| The selectors of the light and the dark values | `:root`, `.dark` |
| The icon source: a lucide folder, a folder of `.svg` files or a JSON file of paths | `node_modules/lucide-react/dist/esm/icons` |
| The token name of each role of `ROLES` that differs | `accent` is `color-accent-default` |
| The navigation of each app with a sidebar, and where it comes from | the route table of the console |
| The source of real copy | the `i18n` catalogue of the app |
| How to capture Today | the command that takes a screenshot of the shipped screen |
| Reference pictures, each with its verdict | `references/`: accepted, or refused and why |

When the adapter is missing, ask the user for the token file before you draw. Never invent a
colour. Read each reference picture and its verdict before you draw: a refused one shows a
mistake made before.

A prose description of a screen is not a design, and flat grey boxes are a wireframe. The user
asked for a picture of the product as it will look, with its tokens, its copy and its art.

## The format

Generated SVG, drawn with one library: [boards.py](boards.py) beside this file. Each spec has
one script, `docs/specs/assets/<date>-<topic>-boards.py`. It imports the library and writes into
`docs/specs/assets/<date>-<topic>/`. Never edit a generated SVG by hand: change the script and run
it again. When the library lacks a piece, add it to the project's own helper module beside the
script, so the next spec has it too.

[example.py](example.py) is a worked board: a page on desktop and on a phone, with pins and a
legend. Run it to see the shape:

```sh
python3 .claude/skills/lw-spec-mockup/example.py <scratchpad>/mock --render
```

A spec script starts like this:

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".claude/skills/lw-spec-mockup"))
from boards import GUTTER, Board, render, tokens

t = tokens("apps/console/src/styles/tokens.css", "light")
b = Board(1440 + 2 * GUTTER, 1200, icons="node_modules/lucide-react/dist/esm/icons")
b.title("Dashboard: the empty state")
f = b.sidebar_app(
    GUTTER,
    160,
    t["bg"],
    t["sidebar"],
    t["ink"],
    t["muted"],
    t["active"],
    ["Dashboard"],
    "Dashboard",
    "Console",
)
b.pin(f.x + f.left + 24, f.top + 30, 1)
b.legend(
    GUTTER, 1100, [(1, "The empty state", "No widget until the product names one.", "R1")], 1440
)
b.save("docs/specs/assets/<date>-<topic>/desktop-empty.svg")
```

| Call | Draws |
|---|---|
| `tokens(files, theme, base=, dark=, overlay=)` | The resolved `--*` values of the token files. `t["accent"]`. A missing token raises. `overlay` is a proposed restyle |
| `phone(x, y, bg, ink, surface, url)`, `desktop(x, y, bg, url)` | A phone (`web` or `app`), a browser window |
| `sidebar_app(x, y, ...)` | An app with a left sidebar, such as a console. Copy `nav` from the app's route table |
| `screenshot(path, x, y)`, `crop(path, ...)` | A real capture of the shipped screen, or a region of it, for the `Today` column |
| `icon(name, x, y, size, color)` | A glyph of the board's `icons` source: lucide, `.svg` files or JSON paths. `ICON_LOADERS` takes a project loader |
| `node_module(rel)` | A path under `node_modules`, from the main checkout when the worktree has none |
| `console_phone(x, y, bg, ink, bar, page, url)` | A console in a phone browser, with its top bar |
| `button`, `field`, `toggle`, `toast`, `spinner`, `scrim`, `dialog` | The base pieces. Each takes the tokens `t` and draws from the roles of `ROLES`. `Board(roles={...})` maps a role to the project's token name |
| `gradient(stops, x2, y2, radial)` | A gradient fill. Gives `url(#id)` for a `fill` |
| `clip(x, y, w, h, r)`, `clip_end()` | Paint only inside a box, such as a list under a header |
| `text(..., max_w=)`, `wrap`, `ellipsis` | Text that ends in `...` past its width, and lines wrapped to a width |
| `image(path, ...)`, `embed_font(family, path)` | Real art and a brand face from the repository |
| `pin`, `legend`, `caption`, `column_head`, `row_label`, `title` | The notes. A legend note longer than two lines raises |
| `render(svg, png, w, h)` | The headless Chrome screenshot for the check below |

### Fidelity

- **Colours are tokens**, through `tokens()`. A hex value in a spec script is wrong, except for
  art that the token file does not hold.
- **Copy is real.** Use the string of the app's catalogue when it exists. A new string is short:
  a label, a value, an empty state, an error or a one-line hint.
- **Data is realistic.** Use plausible values, and a long one where one can occur.

### Canvas

| Surface | Frame |
|---|---|
| Phone | 390 x 844, 48 px gutters |
| Desktop | 1440 x 900 |
| Board | as wide as its frames, as high as its rows need |

### What a spec draws

1. **Today, then target.** "Today" is a real screenshot of the shipped screen (`today-*.png`).
   "Target" is the SVG. Put them side by side in a Markdown table. When a design file exists,
   add a "Reference" column with its export, `figma-<frame>.png`.
2. **One row per state** that applies: default, empty, loading, error, long text, the smallest
   and the largest content. A flow draws one frame per step.
3. **A removal** draws Today with a pin on what goes, and the legend says what goes.
4. **Pins and a legend.** A numbered pin on each element that changes. Under the frame, a
   numbered legend: a bold head, two lines at most, and the spec ID it serves.
5. **A component list** in the spec beside the boards: each element, the component it reuses,
   and each new component with its reason.

### Names

```
docs/specs/assets/<YYYY-MM-DD>-<topic>-boards.py
docs/specs/assets/<YYYY-MM-DD>-<topic>/<surface>-<state>.svg
docs/specs/assets/<YYYY-MM-DD>-<topic>/today-<surface>-<state>.png
```

The spec names the basename of every file it holds in `assets/`. `ah spec check` fails on an
asset that no document names.

## Look at every board before you commit it

A defect of a board is invisible in its source and clear in the picture. Render each board and
read the PNG with the Read tool. Write the PNG to the scratchpad, not to `docs/`.

- [ ] No text runs past its frame, and no pin covers a value.
- [ ] Each string in the drawn UI is a label, a value, an empty state, an error or a hint.
- [ ] No placeholder art, and no colour picked by eye.
- [ ] Each state and surface that the spec touches has its frame.
- [ ] Today and target sit side by side.

`render` needs `google-chrome` or `chromium` on `PATH`.

## After the build

The build must match the approved board. Capture the built screen and put it beside the board.
A difference is a defect of the build, not a reason to draw the board again.
