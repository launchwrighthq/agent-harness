"""Renderer parity: `ah spec render` writes the golden page of each fixture.

A change to the page template regenerates the goldens in `fixtures/spec-render/` with
`ah spec render`, never by hand.
"""

import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[3]
LAUNCHER = PACKAGE / "bin" / "ah"
FIXTURES = PACKAGE / "tests" / "fixtures" / "spec-render"

CASES = [
    ("constructs", "docs/specs/constructs.md", "docs/specs/dist/constructs.html", 5),
    (
        "2026-01-02-archived-plan",
        "docs/superpowers/plans/archive/2026-01-02-archived-plan.md",
        "docs/superpowers/plans/dist/archive/2026-01-02-archived-plan.html",
        2,
    ),
]
# The images each fixture shows. The renderer refuses an image whose file is missing.
ASSETS = {
    "constructs": ("docs/specs/assets/drawing.png", "docs/specs/assets/session.webm",
                   "docs/specs/assets/icon.svg"),
    "2026-01-02-archived-plan": ("docs/superpowers/plans/assets/one.png",),
}


def write_assets(root: Path, case: str) -> None:
    for asset in ASSETS[case]:
        (root / asset).parent.mkdir(parents=True, exist_ok=True)
        (root / asset).write_bytes(b"")


def run_ah(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args], cwd=cwd, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize(("case", "source", "page", "sections"), CASES)
def test_writes_the_golden_page_of_each_fixture(
    tmp_path: Path, case: str, source: str, page: str, sections: int
) -> None:
    (tmp_path / source).parent.mkdir(parents=True)
    (tmp_path / source).write_bytes((FIXTURES / f"{case}.md").read_bytes())
    write_assets(tmp_path, case)
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents/harness.toml").write_text(
        '[specs]\ndirs = ["docs/specs", "docs/superpowers/plans"]\n', encoding="utf-8"
    )

    done = run_ah(tmp_path, "spec", "render", source)

    assert done.returncode == 0, done.stderr
    assert done.stdout == f"wrote {page} ({sections} sections)\n"
    assert (tmp_path / page).read_bytes() == (FIXTURES / f"{case}.html").read_bytes()


def test_writes_the_page_into_the_folder_that_out_names(tmp_path: Path) -> None:
    source = "docs/specs/constructs.md"
    (tmp_path / source).parent.mkdir(parents=True)
    (tmp_path / source).write_bytes((FIXTURES / "constructs.md").read_bytes())
    write_assets(tmp_path, "constructs")

    done = run_ah(tmp_path, "spec", "render", source, "--out", "docs/specs/dist/review")

    assert done.returncode == 0, done.stderr
    assert (tmp_path / "docs/specs/dist/review/constructs.html").is_file()


def test_reads_the_spec_folders_and_out_name_from_the_config(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "harness.toml").write_text(
        '[specs]\ndirs = ["design"]\nout = "pages"\n', encoding="utf-8"
    )
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "a.md").write_text(
        "# A\n\nDate: 2026-01-01\nStatus: draft\n\n## 1. One\n\nText.\n", encoding="utf-8"
    )

    done = run_ah(tmp_path, "spec", "render", "design/a.md")

    assert done.returncode == 0, done.stderr
    assert done.stdout == "wrote design/pages/a.html (1 sections)\n"
    assert (
        'class="rail-sub kind-spec">SPECIFICATION<'
        in (tmp_path / "design/pages/a.html").read_text()
    )


def test_refuses_to_overwrite_a_page_that_git_tracks(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / "docs/specs/dist").mkdir(parents=True)
    (tmp_path / "docs/specs/a.md").write_text("# A\n\nDate: 2026-01-01\nStatus: draft\n")
    (tmp_path / "docs/specs/dist/a.html").write_text("<p>by hand</p>\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs/specs/dist/a.html"], check=True)

    done = run_ah(tmp_path, "spec", "render", "docs/specs/a.md")

    assert done.returncode == 1
    assert done.stderr.startswith("docs/specs/dist/a.html: already tracked by git")
    assert (tmp_path / "docs/specs/dist/a.html").read_text() == "<p>by hand</p>\n"
