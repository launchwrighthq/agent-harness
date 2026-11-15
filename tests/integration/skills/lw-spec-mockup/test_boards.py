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
