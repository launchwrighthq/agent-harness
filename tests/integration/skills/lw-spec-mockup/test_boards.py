"""The lw-spec-mockup library draws the worked board and resolves the token files."""

import json
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[4]
SKILL = PACKAGE / "skills" / "lw-spec-mockup"


def python(code: str, skill: Path = SKILL) -> subprocess.CompletedProcess[str]:
    prelude = (
        f"import sys; sys.path.insert(0, {json.dumps(str(skill))}); "
        "from boards import Board, node_module, tokens; "
    )
    return subprocess.run(
        [sys.executable, "-B", "-c", prelude + code],
        capture_output=True,
        text=True,
        check=False,
    )


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


ROLES = (
    '{"surface": "#ffffff", "ink": "#111111", "muted": "#666666", "line": "#dddddd", '
    '"accent": "#2563eb", "on-accent": "#fefefe", "control": "#f4f4f5", '
    '"scrim": "rgba(0,0,0,0.5)", "danger": "#dc2626", "success": "#16a34a"}'
)


def test_draws_the_worked_board_as_svg_with_the_token_colours_and_the_legend(
    tmp_path: Path,
) -> None:
    subprocess.run(
        [sys.executable, "-B", str(SKILL / "example.py"), str(tmp_path)],
        capture_output=True,
        check=True,
    )

    svg = (tmp_path / "desktop-empty.svg").read_text(encoding="utf-8")

    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert ">Settings: the empty state</text>" in svg
    assert 'fill="#f4f4f5"' in svg
    assert ">The page title  (R1)</text>" in svg


def test_refuses_a_token_that_the_token_file_does_not_hold(tmp_path: Path) -> None:
    css = tmp_path / "tokens.css"
    css.write_text(":root { --bg: #fff; }\n", encoding="utf-8")

    done = python(f'tokens({json.dumps(str(css))})["ink"]')

    assert done.returncode == 1
    assert "KeyError: 'no token --ink'" in done.stderr


def test_resolves_a_token_that_points_at_another_one(tmp_path: Path) -> None:
    css = tmp_path / "tokens.css"
    css.write_text(
        ":root { --blue: #2563eb; --accent: var(--blue); }\n.dark { --blue: #60a5fa; }\n",
        encoding="utf-8",
    )
    f = json.dumps(str(css))

    done = python(f'print(tokens({f})["accent"], tokens({f}, "dark")["accent"])')

    assert done.stdout.strip() == "#2563eb #60a5fa"


def test_finds_node_modules_in_the_main_checkout_from_a_worktree(tmp_path: Path) -> None:
    main = tmp_path / "main"
    skill = main / ".claude" / "skills" / "lw-spec-mockup"
    skill.mkdir(parents=True)
    git(tmp_path, "init", "--quiet", str(main))
    (skill / "boards.py").write_bytes((SKILL / "boards.py").read_bytes())
    git(main, "add", ".")
    git(main, "commit", "--quiet", "-m", "skill")
    (main / "node_modules" / "icons").mkdir(parents=True)
    tree = main / ".claude" / "worktrees" / "w"
    git(main, "worktree", "add", "--quiet", "--detach", str(tree), "HEAD")

    done = python(
        'print(node_module("icons"))', skill=tree / ".claude" / "skills" / "lw-spec-mockup"
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str((main / "node_modules" / "icons").resolve())


def test_fills_with_a_gradient_and_clips_to_a_public_region() -> None:
    done = python(
        "b = Board(100, 100); g = b.gradient([(0, '#000'), (1, '#fff', 0)], x2=0, y2=1); "
        "b.clip(0, 0, 50, 50, r=8); b.rect(0, 0, 100, 100, g); b.clip_end(); print(b.svg())"
    )

    svg = done.stdout
    assert done.returncode == 0, done.stderr
    assert '<linearGradient id="grad1" x1="0" y1="0" x2="0" y2="1">' in svg
    assert '<stop offset="1" stop-color="#fff" stop-opacity="0"/>' in svg
    assert (
        '<g clip-path="url(#clip2)"><rect x="0.0" y="0.0" width="100.0" height="100.0" rx="0" '
        'fill="url(#grad1)"/></g>'
    ) in svg


def test_draws_the_base_pieces_from_the_role_tokens() -> None:
    done = python(
        f"t = {ROLES}; b = Board(400, 400); b.scrim(0, 0, 400, 400, t); "
        "b.button(10, 10, 120, 'Save', t); b.toggle(10, 70, True, t); "
        "b.field(10, 110, 200, t, placeholder='Name'); b.spinner(300, 300, 12, t); "
        "b.toast(200, 350, 'Saved', t, tone='success'); "
        "y = b.dialog(40, 150, 320, t, 'Delete the order?', 'It cannot come back.', "
        "('Cancel', 'Delete')); print(round(y)); print(b.svg())"
    )

    assert done.returncode == 0, done.stderr
    bottom, svg = done.stdout.split("\n", 1)
    assert int(bottom) > 150
    assert 'fill="rgba(0,0,0,0.5)"' in svg
    assert 'rx="22.0" fill="#2563eb"' in svg
    assert ">Save</text>" in svg
    assert ">Name</text>" in svg
    assert ">Delete the order?</text>" in svg
    assert 'fill="#16a34a"' in svg


def test_maps_a_role_to_the_token_name_of_the_project() -> None:
    done = python(
        "b = Board(10, 10, roles={'accent': 'brand'}); "
        "b.button(0, 0, 80, 'Go', {'brand': '#ff0000', 'on-accent': '#fff'}); print(b.svg())"
    )

    assert done.returncode == 0, done.stderr
    assert 'fill="#ff0000"' in done.stdout


def test_draws_a_console_page_in_a_phone_browser() -> None:
    done = python(
        "b = Board(500, 900); "
        "f = b.console_phone(0, 0, '#ffffff', '#111111', '#f4f4f5', 'Orders', url='admin.test'); "
        "print(f.top); print(b.svg())"
    )

    top, svg = done.stdout.split("\n", 1)
    assert done.returncode == 0, done.stderr
    assert float(top) == 54 + 40 + 48
    assert ">admin.test</text>" in svg
    assert ">Orders</text>" in svg


def test_draws_icons_from_a_folder_of_svg_files_and_from_a_json_of_paths(tmp_path: Path) -> None:
    (tmp_path / "svg").mkdir()
    (tmp_path / "svg" / "star.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -960 960 960">'
        '<path d="M1 1h2"/></svg>',
        encoding="utf-8",
    )
    (tmp_path / "icons.json").write_text(
        json.dumps({"dot": {"d": "M2 2h4", "viewBox": "0 0 16 16"}}), encoding="utf-8"
    )
    folder, table = json.dumps(str(tmp_path / "svg")), json.dumps(str(tmp_path / "icons.json"))

    done = python(
        f"a = Board(50, 50, icons={folder}); a.icon('star', 0, 0, 24, '#123456'); "
        f"b = Board(50, 50, icons={table}); b.icon('dot', 0, 0, 24, '#123456'); "
        "print(a.svg()); print(b.svg())"
    )

    assert done.returncode == 0, done.stderr
    assert 'viewBox="0 -960 960 960" fill="currentColor"' in done.stdout
    assert '<path d="M1 1h2"/>' in done.stdout
    assert 'viewBox="0 0 16 16" fill="currentColor"' in done.stdout
    assert '<path d="M2 2h4"/>' in done.stdout


def test_draws_a_row_of_tabs_with_one_active_and_an_underlined_link() -> None:
    done = python(
        f"t = {ROLES}; b = Board(400, 100); "
        "y = b.tabbar(0, 10, 400, ['Orders', 'Refunds'], 'Refunds', t); "
        "b.link(20, 80, 'Open the order', t); print(y); print(b.svg())"
    )

    assert done.returncode == 0, done.stderr
    bottom, svg = done.stdout.split("\n", 1)
    assert float(bottom) == 10 + 44
    assert 'fill="#666666" font-weight="400" text-anchor="start">Orders</text>' in svg
    assert 'fill="#111111" font-weight="600" text-anchor="start">Refunds</text>' in svg
    assert svg.count('height="2.0" rx="1" fill="#2563eb"') == 1
    assert (
        'fill="#2563eb" font-weight="400" text-anchor="start" text-decoration="underline">'
        "Open the order</text>"
    ) in svg


def test_refuses_an_active_tab_that_the_row_does_not_hold() -> None:
    done = python(f"t = {ROLES}; Board(400, 100).tabbar(0, 0, 400, ['Orders'], 'Refunds', t)")

    assert done.returncode == 1
    assert "tabbar: the active tab 'Refunds' is not one of ['Orders']" in done.stderr


def test_refuses_tabs_that_are_wider_than_the_row() -> None:
    done = python(
        f"t = {ROLES}; Board(400, 100).tabbar(0, 0, 60, ['Orders', 'Refunds'], 'Orders', t)"
    )

    assert done.returncode == 1
    assert "tabbar: the tabs need" in done.stderr
    assert "and the row has 60" in done.stderr


def test_embeds_a_font_file_as_a_font_face_in_the_format_of_its_suffix(tmp_path: Path) -> None:
    (tmp_path / "brand.ttf").write_bytes(b"font")
    (tmp_path / "brand.woff2").write_bytes(b"font")
    ttf, woff2 = json.dumps(str(tmp_path / "brand.ttf")), json.dumps(str(tmp_path / "brand.woff2"))

    done = python(
        f"b = Board(10, 10); print(b.embed_font('Brand', {ttf}, '400')); "
        f"b.embed_font('Brand', {woff2}); print(b.svg())"
    )

    assert done.returncode == 0, done.stderr
    family, svg = done.stdout.split("\n", 1)
    assert family == "'Brand'"
    assert (
        "<style>@font-face{font-family:'Brand';font-weight:400;"
        "src:url(data:font/ttf;base64,Zm9udA==) format('truetype')}</style>"
    ) in svg
    assert "src:url(data:font/woff2;base64,Zm9udA==) format('woff2')" in svg


def test_refuses_a_font_file_of_another_suffix(tmp_path: Path) -> None:
    (tmp_path / "brand.eot").write_bytes(b"font")

    done = python(f"Board(10, 10).embed_font('Brand', {json.dumps(str(tmp_path / 'brand.eot'))})")

    assert done.returncode == 1
    assert "brand.eot: embed_font reads woff2, woff, ttf, otf only" in done.stderr


SETS_CSS = (
    ":root { --ink: #111111; --bg: #ffffff; }\n"
    ".dark { --ink: #fafafa; }\n"
    ".contrast { --ink: #000000; }\n"
)


def test_reads_a_named_token_set_on_top_of_the_light_values(tmp_path: Path) -> None:
    css = tmp_path / "tokens.css"
    css.write_text(SETS_CSS, encoding="utf-8")
    f = json.dumps(str(css))

    done = python(
        f"t = tokens({f}, 'contrast', sets={{'contrast': ('.contrast',)}}); "
        f"print(t['ink'], t['bg'], tokens({f})['ink'], tokens({f}, 'dark')['ink'])"
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "#000000 #ffffff #111111 #fafafa"


def test_refuses_a_token_set_that_no_argument_names(tmp_path: Path) -> None:
    css = tmp_path / "tokens.css"
    css.write_text(SETS_CSS, encoding="utf-8")

    done = python(f"tokens({json.dumps(str(css))}, 'contrast')")

    assert done.returncode == 1
    assert "ValueError: unknown token set 'contrast': light, dark" in done.stderr


def project(tmp_path: Path, config: str) -> Path:
    """A project with the library installed and `config` as its harness config. Gives the
    skill folder."""
    skill = tmp_path / ".claude" / "skills" / "lw-spec-mockup"
    skill.mkdir(parents=True)
    (skill / "boards.py").write_bytes((SKILL / "boards.py").read_bytes())
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "harness.toml").write_text(config, encoding="utf-8")
    return skill


def test_imports_a_project_module_from_the_folder_of_the_config_key(tmp_path: Path) -> None:
    skill = project(tmp_path, '[specs]\nmockupLibrary = "docs/specs/boardlib"\n')
    library = tmp_path / "docs" / "specs" / "boardlib"
    library.mkdir(parents=True)
    (library / "console_pieces.py").write_text("LABEL = 'Orders'\n", encoding="utf-8")

    done = python("import console_pieces; print(console_pieces.LABEL); print(sys.path[-1])", skill)

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == ["Orders", str(library.resolve())]


def test_keeps_the_shipped_library_ahead_of_a_project_module_of_the_same_name(
    tmp_path: Path,
) -> None:
    skill = project(tmp_path, '[specs]\nmockupLibrary = "boardlib"\n')
    (tmp_path / "boardlib").mkdir()
    (tmp_path / "boardlib" / "boards.py").write_text("Board = None\n", encoding="utf-8")

    done = python("import importlib, boards; print(importlib.reload(boards).__file__)", skill)

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str(skill / "boards.py")


def test_adds_no_folder_when_the_config_names_no_library(tmp_path: Path) -> None:
    skill = project(tmp_path, '[specs]\nout = "dist"\n')

    done = python("import boards; print(boards.library_dir())", skill)

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "None"


def test_refuses_a_library_path_that_leaves_the_repository(tmp_path: Path) -> None:
    skill = project(tmp_path, '[specs]\nmockupLibrary = "../boardlib"\n')

    done = python("pass", skill)

    assert done.returncode == 1
    assert (
        "ValueError: specs.mockupLibrary must be a folder path relative to the repository root, "
        "with no `..` part, got '../boardlib'."
    ) in done.stderr


def test_refuses_a_library_path_that_is_no_folder(tmp_path: Path) -> None:
    skill = project(tmp_path, '[specs]\nmockupLibrary = "docs/boardlib"\n')

    done = python("pass", skill)

    assert done.returncode == 1
    assert "FileNotFoundError: specs.mockupLibrary names docs/boardlib, which is no folder" in (
        done.stderr
    )
