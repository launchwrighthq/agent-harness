import base64
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"
HEAD = "# A\n\nDate: 2026-01-01\nStatus: draft\n\n## 1. One\n\n"


def run_ah(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def write(root: Path, path: str, text: str) -> None:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text, encoding="utf-8")


def test_refuses_an_image_whose_file_is_missing_and_names_the_line(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/a.md", HEAD + "![Board](assets/boadr.svg)\n")
    write(tmp_path, "docs/specs/assets/board.svg", "<svg/>")

    done = run_ah(tmp_path, "spec", "render", "docs/specs/a.md")

    assert done.returncode == 1
    assert done.stderr == (
        "docs/specs/a.md:8: the image assets/boadr.svg has no file at docs/specs/assets/boadr.svg\n"
    )
    assert not (tmp_path / "docs/specs/dist/a.html").exists()


PLACEHOLDER = '[specs]\nmissingAssets = "placeholder"\n'
NO_FILE = "the image assets/boadr.svg has no file at docs/specs/assets/boadr.svg"


def test_renders_a_missing_image_as_a_box_and_warns_in_placeholder_mode(tmp_path: Path) -> None:
    write(tmp_path, ".agents/harness.toml", PLACEHOLDER)
    body = "![Board](assets/boadr.svg)\n\n![Ok](assets/ok.svg)\n"
    write(tmp_path, "docs/specs/a.md", HEAD + body)
    write(tmp_path, "docs/specs/assets/ok.svg", "<svg/>")

    done = run_ah(tmp_path, "spec", "render", "docs/specs/a.md")

    assert done.returncode == 0, done.stderr
    assert done.stderr == f"warning: docs/specs/a.md:8: {NO_FILE}; the page shows a placeholder\n"
    page = (tmp_path / "docs/specs/dist/a.html").read_text(encoding="utf-8")
    assert '<span class="missing-asset">image missing: assets/boadr.svg</span>' in page
    assert 'boadr.svg"' not in page
    assert '<img src="../assets/ok.svg"' in page


def test_refuses_a_missing_image_with_strict_in_placeholder_mode(tmp_path: Path) -> None:
    write(tmp_path, ".agents/harness.toml", PLACEHOLDER)
    write(tmp_path, "docs/specs/a.md", HEAD + "![Board](assets/boadr.svg)\n")

    done = run_ah(tmp_path, "spec", "render", "--strict", "docs/specs/a.md")

    assert done.returncode == 1
    assert done.stderr == f"docs/specs/a.md:8: {NO_FILE}\n"
    assert not (tmp_path / "docs/specs/dist/a.html").exists()


def test_refuses_a_missing_image_with_strict_in_refuse_mode(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/a.md", HEAD + "![Board](assets/boadr.svg)\n")

    done = run_ah(tmp_path, "spec", "render", "docs/specs/a.md", "--strict")

    assert done.returncode == 1
    assert done.stderr == f"docs/specs/a.md:8: {NO_FILE}\n"


def test_refuses_a_data_image_and_a_path_outside_assets_in_placeholder_mode(
    tmp_path: Path,
) -> None:
    write(tmp_path, ".agents/harness.toml", PLACEHOLDER)
    write(tmp_path, "docs/specs/a.md", HEAD + "![x](data:image/png;base64,AAAA)\n")
    write(tmp_path, "docs/specs/b.md", HEAD + "![x](../board.svg)\n")

    data = run_ah(tmp_path, "spec", "render", "docs/specs/a.md")
    outside = run_ah(tmp_path, "spec", "render", "docs/specs/b.md")

    assert data.returncode == 1
    assert data.stderr.startswith("docs/specs/a.md:8: an inline data: image")
    assert outside.returncode == 1
    assert outside.stderr == (
        "docs/specs/b.md:8: an image must be referenced as assets/<file>, not '../board.svg'\n"
    )


def test_warns_for_each_spec_with_a_missing_image_with_all_in_placeholder_mode(
    tmp_path: Path,
) -> None:
    write(tmp_path, ".agents/harness.toml", PLACEHOLDER)
    write(tmp_path, "docs/specs/a.md", HEAD + "![Board](assets/boadr.svg)\n")
    write(tmp_path, "docs/specs/b.md", HEAD + "Text.\n")

    done = run_ah(tmp_path, "spec", "render", "--all")
    strict = run_ah(tmp_path, "spec", "render", "--all", "--strict")

    assert done.returncode == 0, done.stderr
    assert done.stderr == f"warning: docs/specs/a.md:8: {NO_FILE}; the page shows a placeholder\n"
    assert "rendered 2 of 2 specs" in done.stdout
    assert strict.returncode == 1
    assert strict.stderr == f"docs/specs/a.md:8: {NO_FILE}\n"


def test_refuses_a_missing_assets_value_outside_the_two_modes(tmp_path: Path) -> None:
    write(tmp_path, ".agents/harness.toml", '[specs]\nmissingAssets = "skip"\n')
    write(tmp_path, "docs/specs/a.md", HEAD + "Text.\n")

    done = run_ah(tmp_path, "spec", "render", "docs/specs/a.md")

    assert done.returncode != 0
    assert "specs.missingAssets must be one of refuse, placeholder, got 'skip'" in done.stderr
    assert not (tmp_path / "docs/specs/dist/a.html").exists()


def test_finds_the_image_of_an_archived_spec_in_the_folder_assets(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/archive/a.md", HEAD + "![Board](assets/board.svg)\n")
    write(tmp_path, "docs/specs/assets/board.svg", "<svg/>")

    done = run_ah(tmp_path, "spec", "render", "docs/specs/archive/a.md")

    assert done.returncode == 0, done.stderr
    assert '<img src="../../assets/board.svg"' in (
        tmp_path / "docs/specs/dist/archive/a.html"
    ).read_text(encoding="utf-8")


def test_renders_every_spec_of_every_spec_folder_with_all(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/a.md", HEAD + "Text.\n")
    write(tmp_path, "docs/specs/archive/b.md", HEAD + "Text.\n")
    write(tmp_path, "docs/superpowers/plans/c.md", HEAD + "Text.\n")
    write(tmp_path, "docs/specs/dist/old.md", "not a source\n")
    two_folders = '[specs]\ndirs = ["docs/specs", "docs/superpowers/plans"]\n'
    write(tmp_path, ".agents/harness.toml", two_folders)

    done = run_ah(tmp_path, "spec", "render", "--all")

    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "wrote docs/specs/dist/a.html (1 sections)\n"
        "wrote docs/specs/dist/archive/b.html (1 sections)\n"
        "wrote docs/superpowers/plans/dist/c.html (1 sections)\n"
        "rendered 3 of 3 specs\n"
    )


def test_renders_the_rest_and_exits_1_when_one_spec_fails_with_all(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/a.md", "no title\n")
    write(tmp_path, "docs/specs/b.md", HEAD + "Text.\n")

    done = run_ah(tmp_path, "spec", "render", "--all")

    assert done.returncode == 1
    assert done.stderr == "docs/specs/a.md: missing a '# H1' at the top of the file\n"
    assert done.stdout == "wrote docs/specs/dist/b.html (1 sections)\nrendered 1 of 2 specs\n"


def test_renders_a_report_into_the_page_folder_of_its_own_kinds_folder(tmp_path: Path) -> None:
    write(
        tmp_path,
        ".agents/harness.toml",
        '[specs]\ndirs = ["docs/specs"]\n[specs.kinds."docs/specs/assets"]\nkind = "qa"\n'
        'label = "QA REPORT"\nstatuses = ["tested"]\n',
    )
    write(tmp_path, "docs/specs/assets/r.md", "# R\n\nDate: 2026-01-01\nStatus: tested\n")

    done = run_ah(tmp_path, "spec", "render", "--all")

    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "wrote docs/specs/assets/dist/r.html (0 sections)\nrendered 1 of 1 specs\n"
    )
    assert "QA REPORT" in (tmp_path / "docs/specs/assets/dist/r.html").read_text(encoding="utf-8")


def test_names_the_source_of_a_page_it_cannot_write_and_renders_the_rest(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/a.md", HEAD + "Text.\n")
    write(tmp_path, "docs/specs/b.md", HEAD + "Text.\n")
    (tmp_path / "docs/specs/dist/a.html").mkdir(parents=True)

    done = run_ah(tmp_path, "spec", "render", "--all")

    assert done.returncode == 1
    assert done.stderr.startswith("docs/specs/a.md: cannot write docs/specs/dist/a.html: ")
    assert "\n\n" not in done.stderr
    assert done.stdout == "wrote docs/specs/dist/b.html (1 sections)\nrendered 1 of 2 specs\n"


def test_skips_the_readme_of_a_spec_folder_and_of_its_archive_with_all(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/a.md", HEAD + "Text.\n")
    write(tmp_path, "docs/specs/README.md", "# Specs\n\nThe index.\n")
    write(tmp_path, "docs/specs/archive/README.md", "# Archive\n\nThe index.\n")

    done = run_ah(tmp_path, "spec", "render", "--all")

    assert done.returncode == 0, done.stderr
    assert done.stdout == "wrote docs/specs/dist/a.html (1 sections)\nrendered 1 of 1 specs\n"


PNG = b"\x89PNG\r\n\x1a\nfake"
LINKING = '<svg><image href="shot.png" width="4" height="4"/></svg>'


def board_in(page: str) -> str:
    head = '<img src="data:image/svg+xml;base64,'
    start = page.index(head) + len(head)
    return base64.b64decode(page[start : page.index('"', start)]).decode("utf-8")


def test_shows_the_picture_that_a_board_links_and_keeps_the_board_file(tmp_path: Path) -> None:
    body = "![Board](assets/board.svg)\n\n![Ok](assets/ok.svg)\n"
    write(tmp_path, "docs/specs/a.md", HEAD + body)
    write(tmp_path, "docs/specs/assets/board.svg", LINKING)
    write(tmp_path, "docs/specs/assets/ok.svg", "<svg/>")
    (tmp_path / "docs/specs/assets/shot.png").write_bytes(PNG)

    done = run_ah(tmp_path, "spec", "render", "docs/specs/a.md")

    assert done.returncode == 0, done.stderr
    page = (tmp_path / "docs/specs/dist/a.html").read_text(encoding="utf-8")
    picture = f"data:image/png;base64,{base64.b64encode(PNG).decode()}"
    assert board_in(page) == LINKING.replace("shot.png", picture)
    assert '<img src="../assets/ok.svg"' in page
    assert (tmp_path / "docs/specs/assets/board.svg").read_text(encoding="utf-8") == LINKING


def test_shows_the_picture_that_a_board_links_on_an_archived_page(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/archive/a.md", HEAD + "![Board](assets/board.svg)\n")
    write(tmp_path, "docs/specs/assets/board.svg", LINKING)
    (tmp_path / "docs/specs/assets/shot.png").write_bytes(PNG)

    done = run_ah(tmp_path, "spec", "render", "docs/specs/archive/a.md")

    assert done.returncode == 0, done.stderr
    page = (tmp_path / "docs/specs/dist/archive/a.html").read_text(encoding="utf-8")
    assert f"data:image/png;base64,{base64.b64encode(PNG).decode()}" in board_in(page)


NO_LINKED = (
    "the board assets/board.svg links shot.png, which has no file at docs/specs/assets/shot.png"
)


BOARD_WARNING = f"warning: docs/specs/a.md: {NO_LINKED}; the page shows the board file as it is\n"


@pytest.mark.parametrize("config", ["", PLACEHOLDER])
def test_warns_of_a_missing_linked_picture_and_keeps_the_plain_board_image(
    tmp_path: Path, config: str
) -> None:
    write(tmp_path, ".agents/harness.toml", config)
    write(tmp_path, "docs/specs/a.md", HEAD + "![Board](assets/board.svg)\n")
    write(tmp_path, "docs/specs/assets/board.svg", LINKING)

    done = run_ah(tmp_path, "spec", "render", "docs/specs/a.md")

    assert done.returncode == 0, done.stderr
    assert done.stderr == BOARD_WARNING
    page = (tmp_path / "docs/specs/dist/a.html").read_text(encoding="utf-8")
    assert '<img src="../assets/board.svg" alt="Board"' in page
    assert 'missing-asset">' not in page


def test_warns_once_for_a_board_that_a_page_shows_twice(tmp_path: Path) -> None:
    body = "![Board](assets/board.svg)\n\n![Again](assets/board.svg)\n"
    write(tmp_path, "docs/specs/a.md", HEAD + body)
    write(tmp_path, "docs/specs/assets/board.svg", LINKING)

    done = run_ah(tmp_path, "spec", "render", "docs/specs/a.md")

    assert (done.returncode, done.stderr) == (0, BOARD_WARNING)


def test_warns_of_a_board_problem_and_renders_every_spec_with_all(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/a.md", HEAD + "![Board](assets/board.svg)\n")
    write(tmp_path, "docs/specs/assets/board.svg", LINKING)

    done = run_ah(tmp_path, "spec", "render", "--all")

    assert (done.returncode, done.stderr) == (0, BOARD_WARNING)
    assert done.stdout == "wrote docs/specs/dist/a.html (1 sections)\nrendered 1 of 1 specs\n"


def test_refuses_a_missing_linked_picture_with_strict_in_placeholder_mode(tmp_path: Path) -> None:
    write(tmp_path, ".agents/harness.toml", PLACEHOLDER)
    write(tmp_path, "docs/specs/a.md", HEAD + "![Board](assets/board.svg)\n")
    write(tmp_path, "docs/specs/assets/board.svg", LINKING)

    done = run_ah(tmp_path, "spec", "render", "--strict", "docs/specs/a.md")

    assert done.returncode == 1
    assert done.stderr == f"docs/specs/a.md:8: {NO_LINKED}\n"


def test_refuses_a_missing_linked_picture_with_strict_in_refuse_mode(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/a.md", HEAD + "![Board](assets/board.svg)\n")
    write(tmp_path, "docs/specs/assets/board.svg", LINKING)

    done = run_ah(tmp_path, "spec", "render", "--strict", "docs/specs/a.md")

    assert done.returncode == 1
    assert done.stderr == f"docs/specs/a.md:8: {NO_LINKED}\n"
    assert not (tmp_path / "docs/specs/dist/a.html").exists()


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        (
            {"assets/board.svg": '<svg><image href="../secret.png"/></svg>', "secret.png": "png"},
            "the board assets/board.svg links ../secret.png, which is outside docs/specs/assets",
        ),
        (
            {"assets/board.svg": '<svg><image href="notes.txt"/></svg>', "assets/notes.txt": "t"},
            "the board assets/board.svg links notes.txt, which is not a PNG, JPEG, GIF, WebP, AVIF"
            " or SVG file",
        ),
        (
            {
                "assets/board.svg": '<svg><image href="b.svg"/></svg>',
                "assets/b.svg": '<svg><image href="board.svg"/></svg>',
            },
            "the board assets/b.svg links board.svg, which links it back",
        ),
    ],
)
def test_refuses_a_board_that_the_page_cannot_hold_with_strict(
    tmp_path: Path, files: dict[str, str], reason: str
) -> None:
    write(tmp_path, "docs/specs/a.md", HEAD + "![Board](assets/board.svg)\n")
    for name, text in files.items():
        write(tmp_path, f"docs/specs/{name}", text)

    done = run_ah(tmp_path, "spec", "render", "--strict", "docs/specs/a.md")

    assert done.returncode == 1
    assert done.stderr == f"docs/specs/a.md:8: {reason}\n"
    assert not (tmp_path / "docs/specs/dist/a.html").exists()


def test_shows_the_picture_of_a_board_that_another_board_links(tmp_path: Path) -> None:
    inner = '<svg><image href="shot.png"/></svg>'
    write(tmp_path, "docs/specs/a.md", HEAD + "![Board](assets/board.svg)\n")
    write(tmp_path, "docs/specs/assets/board.svg", '<svg><image href="inner.svg"/></svg>')
    write(tmp_path, "docs/specs/assets/inner.svg", inner)
    (tmp_path / "docs/specs/assets/shot.png").write_bytes(PNG)

    done = run_ah(tmp_path, "spec", "render", "--strict", "docs/specs/a.md")

    assert (done.returncode, done.stderr) == (0, "")
    picture = f"data:image/png;base64,{base64.b64encode(PNG).decode()}"
    inner_page = inner.replace("shot.png", picture).encode("utf-8")
    page = (tmp_path / "docs/specs/dist/a.html").read_text(encoding="utf-8")
    assert board_in(page) == (
        f'<svg><image href="data:image/svg+xml;base64,{base64.b64encode(inner_page).decode()}"/>'
        "</svg>"
    )
