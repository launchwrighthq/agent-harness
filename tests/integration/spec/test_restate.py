import subprocess
import sys
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs" / "specs" / "archive").mkdir(parents=True)
    git(tmp_path, "init", "--quiet", str(root))
    (root / "docs" / "specs" / "a.md").write_text("# A\n\nStatus: draft\n", encoding="utf-8")
    (root / "docs" / "specs" / "archive" / "b.md").write_text(
        "# B\n\nStatus: answered - yes.\n", encoding="utf-8"
    )
    git(root, "add", "docs")
    git(root, "commit", "--quiet", "-m", "specs")
    return root


def ah(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "spec", "restate", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_refuses_to_archive_a_spec_with_an_unsettled_status(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    done = ah(root, "docs/specs/a.md", "in-progress - R2 open", "--archive")

    assert done.returncode == 1
    assert done.stderr == (
        "ah spec: --archive takes a settled status (implemented, superseded-by, abandoned, "
        "answered), not 'in-progress'\n"
    )
    assert (root / "docs" / "specs" / "a.md").read_text() == "# A\n\nStatus: draft\n"


def test_refuses_to_archive_a_spec_that_is_already_in_the_archive(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    done = ah(root, "docs/specs/archive/b.md", "abandoned - no owner", "--archive")

    assert done.returncode == 1
    assert done.stderr == "ah spec: docs/specs/archive/b.md is already in archive/\n"
    assert not (root / "docs" / "specs" / "archive" / "archive").exists()


def test_stages_the_new_status_at_the_archive_path(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    done = ah(root, "docs/specs/a.md", "implemented - `a.py` runs it.", "--archive")

    assert done.returncode == 0, done.stderr
    staged = git(root, "show", ":docs/specs/archive/a.md")
    assert staged == "# A\n\nStatus: implemented - `a.py` runs it.\n"
    assert git(root, "diff", "--name-only") == ""


def test_refuses_to_archive_a_qa_report(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / ".agents").mkdir()
    (root / ".agents" / "harness.toml").write_text(
        '[specs]\ndirs = ["docs/specs", "docs/qa"]\n\n[specs.kinds."docs/qa"]\nkind = "qa"\n',
        encoding="utf-8",
    )
    (root / "docs" / "qa").mkdir()
    (root / "docs" / "qa" / "r.md").write_text("# R\n\nStatus: tested\n", encoding="utf-8")

    done = ah(root, "docs/qa/r.md", "implemented - done.", "--archive")

    assert done.returncode == 1
    assert done.stderr == (
        "ah spec: docs/qa/r.md: docs/qa/ holds QA reports, which are never archived. "
        "Delete a report that is done.\n"
    )
    assert (root / "docs" / "qa" / "r.md").read_text() == "# R\n\nStatus: tested\n"
