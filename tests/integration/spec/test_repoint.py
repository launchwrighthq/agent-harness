import subprocess
import sys
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs" / "specs" / "archive").mkdir(parents=True)
    git(tmp_path, "init", "--quiet", str(root))
    (root / "docs" / "specs" / "archive" / "a.md").write_text("# A\n", encoding="utf-8")
    (root / "README.md").write_text("See docs/specs/a.md for the design.\n", encoding="utf-8")
    (root / "notes.md").write_text("Nothing here.\n", encoding="utf-8")
    (root / "untracked.md").write_text("Also docs/specs/a.md.\n", encoding="utf-8")
    git(root, "add", "README.md", "notes.md", "docs")
    return root


def ah(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "spec", "repoint", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_lists_each_tracked_file_that_names_the_old_path(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    done = ah(root, "docs/specs/a.md", "docs/specs/archive/a.md")

    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "cites README.md\n1 files name docs/specs/a.md. Run again with --apply to repoint them.\n"
    )
    assert "docs/specs/a.md for" in (root / "README.md").read_text()


def test_writes_the_new_path_into_each_tracked_file_with_apply(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    done = ah(root, "docs/specs/a.md", "docs/specs/archive/a.md", "--apply")

    assert done.returncode == 0, done.stderr
    assert done.stdout == "repointed README.md\n"
    assert (root / "README.md").read_text() == "See docs/specs/archive/a.md for the design.\n"
    assert (root / "untracked.md").read_text() == "Also docs/specs/a.md.\n"


def test_repoints_several_path_pairs_in_one_call(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "notes.md").write_text("Plan: docs/specs/b.md.\n", encoding="utf-8")
    git(root, "add", "notes.md")

    done = ah(
        root,
        "docs/specs/a.md",
        "docs/specs/archive/a.md",
        "docs/specs/b.md",
        "docs/specs/archive/b.md",
        "--apply",
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout == "repointed README.md\nrepointed notes.md\n"
    assert (root / "notes.md").read_text() == "Plan: docs/specs/archive/b.md.\n"


def test_refuses_an_old_path_without_its_new_path(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    done = ah(root, "docs/specs/a.md", "docs/specs/archive/a.md", "docs/specs/b.md")

    assert done.returncode == 2
    assert done.stderr == "ah spec repoint: give pairs of two different paths\n"
