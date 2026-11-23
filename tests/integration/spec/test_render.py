import subprocess
import sys
from pathlib import Path

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
