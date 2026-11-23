import subprocess
import sys
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def doc(status: str, body: str = "") -> str:
    return f"# T\n\nDate: 2026-01-01\nStatus: {status}\n\n{body}"


def make_repo(tmp_path: Path, files: dict[str, str], config: str = "") -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(tmp_path, "init", "--quiet", str(root))
    if config:
        files = {**files, ".agents/harness.toml": config}
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    git(root, "add", *files)
    return root


def check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "spec", "check"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_passes_a_tree_that_keeps_every_rule(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "docs/specs/a.md": doc("draft", "![Board](assets/board.svg)\n"),
            "docs/specs/assets/board.svg": "<svg/>",
            "docs/specs/archive/b.md": doc("superseded-by docs/specs/a.md"),
            "README.md": "See `docs/specs/a.md` and [b](docs/specs/archive/b.md).\n",
        },
    )

    done = check(root)

    assert done.returncode == 0, done.stdout
    assert done.stdout == "spec check: 2 documents, no finding\n"


def test_reports_a_settled_spec_outside_the_archive_and_an_unsettled_one_inside(
    tmp_path: Path,
) -> None:
    root = make_repo(
        tmp_path,
        {
            "docs/specs/a.md": doc("implemented - `a.py` runs it."),
            "docs/superpowers/plans/archive/b.md": doc("draft"),
        },
    )

    done = check(root)

    assert done.returncode == 1
    assert done.stdout == (
        "docs/specs/a.md: status implemented is settled, so it belongs in docs/specs/archive/\n"
        "docs/superpowers/plans/archive/b.md: status draft is not settled, so it belongs in "
        "docs/superpowers/plans/\n"
        "spec check: 2 findings in 2 documents\n"
    )


def test_reports_a_superseded_by_path_that_does_not_resolve(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path, {"docs/specs/archive/c.md": doc("superseded-by `docs/specs/gone.md`.")}
    )

    done = check(root)

    assert done.returncode == 1
    assert done.stdout.splitlines()[0] == (
        "docs/specs/archive/c.md: superseded-by docs/specs/gone.md does not resolve"
    )


def test_reports_a_pointer_to_a_missing_spec_outside_the_archive(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "docs/specs/a.md": doc("draft", "Next: `docs/specs/<name>.md`.\n"),
            "docs/specs/archive/old.md": doc("abandoned - no owner", "`docs/specs/gone.md`\n"),
            "notes/plan.md": "Read [it](docs/specs/gone.md#part) and `docs/specs/a.md`.\n",
        },
    )

    done = check(root)

    assert done.returncode == 1
    assert done.stdout == (
        "notes/plan.md: names docs/specs/gone.md, which does not exist\n"
        "spec check: 1 findings in 2 documents\n"
    )


def test_accepts_a_pointer_to_a_page_whose_source_exists(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "docs/specs/a.md": doc("draft"),
            "README.md": "Open `docs/specs/dist/a.html`, not `docs/specs/dist/b.html`.\n",
        },
    )

    done = check(root)

    assert done.stdout.splitlines()[0] == (
        "README.md: names docs/specs/dist/b.html, which does not exist"
    )


def test_reports_an_asset_that_no_document_of_its_folder_names(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "docs/specs/a.md": doc("draft", "![Board](assets/board.svg)\n"),
            "docs/specs/assets/board.svg": "<svg/>",
            "docs/specs/assets/old.png": "png",
        },
    )

    done = check(root)

    assert done.returncode == 1
    assert done.stdout.splitlines()[0] == (
        "docs/specs/assets/old.png: no document of docs/specs names old.png"
    )


def test_runs_only_the_checks_that_the_config_names(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "docs/specs/a.md": doc("implemented - done."),
            "docs/specs/assets/old.png": "png",
        },
        config='[specs]\nchecks = ["assets"]\n',
    )

    done = check(root)

    assert done.stdout == (
        "docs/specs/assets/old.png: no document of docs/specs names old.png\n"
        "spec check: 1 findings in 1 documents\n"
    )


NESTED_CONFIG = """[specs]
dirs = ["docs/specs"]

[specs.kinds."docs/specs/assets"]
kind = "qa"
label = "QA REPORT"
statuses = ["untested", "tested"]
"""


def test_judges_a_report_by_its_own_kinds_folder_and_not_as_an_asset(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {"docs/specs/a.md": doc("draft"), "docs/specs/assets/r.md": doc("tested")},
        NESTED_CONFIG,
    )

    done = check(root)

    assert done.returncode == 0, done.stdout
    assert done.stdout == "spec check: 2 documents, no finding\n"


QA_CONFIG = '[specs]\ndirs = ["docs/specs", "docs/qa"]\n\n[specs.kinds."docs/qa"]\nkind = "qa"\n'


def test_lets_a_kept_reference_spec_stay_settled_in_its_folder(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "docs/specs/app.md": doc("implemented - the app runs it."),
            "docs/specs/old.md": doc("implemented - `a.py` runs it."),
        },
        config='[specs]\nkeep = ["docs/specs/app.md"]\n',
    )

    done = check(root)

    assert done.returncode == 1
    assert done.stdout == (
        "docs/specs/old.md: status implemented is settled, so it belongs in docs/specs/archive/\n"
        "spec check: 1 findings in 2 documents\n"
    )


def test_reports_a_kept_path_that_names_no_document(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {"docs/specs/a.md": doc("draft")},
        config='[specs]\nkeep = ["docs/specs/gone.md"]\n',
    )

    done = check(root)

    assert done.returncode == 1
    assert done.stdout == (
        "specs.keep: docs/specs/gone.md is no document of an active spec folder\n"
        "spec check: 1 findings in 1 documents\n"
    )


def test_leaves_a_qa_folder_out_of_the_archive_check(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {"docs/specs/a.md": doc("draft"), "docs/qa/r.md": doc("tested")},
        config=QA_CONFIG,
    )

    done = check(root)

    assert done.returncode == 0, done.stdout
    assert done.stdout == "spec check: 2 documents, no finding\n"


def test_passes_a_picture_that_only_a_board_of_the_folder_links(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "docs/specs/a.md": doc("draft", "![Board](assets/board.svg)\n"),
            "docs/specs/assets/board.svg": '<svg><image href="art/shot.png"/></svg>',
            "docs/specs/assets/art/shot.png": "png",
        },
        config='[specs]\nchecks = ["assets"]\n',
    )

    done = check(root)

    assert done.returncode == 0, done.stdout


def test_passes_a_picture_that_a_board_links_with_a_percent_escape(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "docs/specs/a.md": doc("draft", "![Board](assets/board.svg)\n"),
            "docs/specs/assets/board.svg": '<svg><image href="my%20shot.png"/></svg>',
            "docs/specs/assets/my shot.png": "png",
        },
        config='[specs]\nchecks = ["assets"]\n',
    )

    done = check(root)

    assert done.returncode == 0, done.stdout


def test_reports_a_picture_that_only_a_comment_of_a_board_links(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "docs/specs/a.md": doc("draft", "![Board](assets/board.svg)\n"),
            "docs/specs/assets/board.svg": '<svg><!-- <image href="shot.png"/> --></svg>',
            "docs/specs/assets/shot.png": "png",
        },
        config='[specs]\nchecks = ["assets"]\n',
    )

    done = check(root)

    assert done.returncode == 1
    assert "docs/specs/assets/shot.png: no document of docs/specs names shot.png" in done.stdout
