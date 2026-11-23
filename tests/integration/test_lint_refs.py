"""`ah lint --changed` and `ah lint --refs` over the files of a git checkout."""

import subprocess
from collections.abc import Callable
from pathlib import Path

Ah = Callable[..., subprocess.CompletedProcess[str]]

IDENTITY = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def git(root: Path, *args: str) -> None:
    subprocess.run([*IDENTITY, "-C", str(root), *args], check=True, capture_output=True)


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def base_with_old_references(repo: Path) -> None:
    git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    write(repo, "src/queue.mts", "// N1 old reference\nexport const a = 1;\n")
    write(repo, "docs/notes.md", "Row N1 in a document.\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "feat")


def test_reports_the_references_of_the_added_lines_only(repo: Path, ah: Ah) -> None:
    base_with_old_references(repo)
    write(repo, "src/queue.mts", "// N1 old reference\nexport const a = 1;\n// R5 new one\n")
    write(repo, "src/fresh.py", '"""Ported from the old service."""\nx = 1\n')
    write(repo, "src/gap.py", "# lw-gap N5: remove when the API returns ids\ny = 2\n")
    write(repo, "docs/notes.md", "Row N1 in a document.\nRow N2 too.\n")

    done = ah("lint", "--changed")

    assert done.returncode == 1, done.stderr
    lines = done.stdout.splitlines()
    assert [line.split(" ")[0] for line in lines] == ["src/fresh.py:1:4", "src/queue.mts:3:4"]
    assert all(" spec-ref " in line for line in lines)


def test_passes_a_branch_with_no_new_reference(repo: Path, ah: Ah) -> None:
    base_with_old_references(repo)
    write(repo, "src/queue.mts", "// N1 old reference\nexport const a = 2;\n")

    done = ah("lint", "--changed")

    assert (done.returncode, done.stdout) == (0, "")


def test_sweeps_every_line_of_the_named_paths(repo: Path, ah: Ah) -> None:
    base_with_old_references(repo)

    done = ah("lint", "--refs", "src", "docs")

    assert done.returncode == 1, done.stderr
    assert done.stdout.splitlines()[0].startswith("src/queue.mts:1:4 spec-ref spec-id \"N1\"")
    assert len(done.stdout.splitlines()) == 1


def test_refuses_a_path_with_changed_and_no_path_with_refs(repo: Path, ah: Ah) -> None:
    assert ah("lint", "--changed", "src").returncode == 2
    assert ah("lint", "--refs").returncode == 2
