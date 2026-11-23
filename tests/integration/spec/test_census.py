import json
import subprocess
import sys
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"


def doc(status: str) -> str:
    return f"# T\n\nDate: 2026-01-01\nStatus: {status}\n"


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    files = {
        "docs/specs/a.md": doc("draft"),
        "docs/specs/b.md": doc("in-progress - R2 open, branch orders, 2026-01-02"),
        "docs/specs/c.md": "# C\n\nNo status here.\n",
        "docs/specs/archive/a.md": doc("implemented - `a.py` runs it."),
        "docs/specs/archive/d.md": "# D\n\n<span>**Status:** implemented &middot; x</span>\n",
        "docs/specs/archive/e.md": doc("superseded-by docs/specs/a.md"),
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    (root / "docs/specs/untracked.md").write_text(doc("draft"), encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", *files], check=True)
    return root


def census(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "spec", "census", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_counts_each_folder_by_status_and_lists_the_findings(tmp_path: Path) -> None:
    done = census(make_repo(tmp_path))

    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "docs/specs: 3 documents: draft 1, in-progress 1, none 1\n"
        "docs/specs/archive: 3 documents: implemented 2, superseded-by 1\n"
        "\n"
        "In an active folder and its archive: docs/specs/a.md, docs/specs/archive/a.md\n"
        "Settled with no evidence: docs/specs/archive/d.md (implemented)\n"
        "No status: docs/specs/c.md\n"
    )


def test_prints_the_census_as_json(tmp_path: Path) -> None:
    done = census(make_repo(tmp_path), "--json")

    found = json.loads(done.stdout)
    assert found["folders"]["docs/specs/archive"] == {"implemented": 2, "superseded-by": 1}
    assert found["duplicates"] == [["docs/specs/a.md", "docs/specs/archive/a.md"]]
    assert found["bare"] == ["docs/specs/archive/d.md"]
    assert found["missing"] == ["docs/specs/c.md"]


NESTED_CONFIG = """[specs]
dirs = ["docs/specs"]

[specs.kinds."docs/specs/assets"]
kind = "qa"
label = "QA REPORT"
statuses = ["untested", "tested"]
"""


def test_counts_a_report_under_its_own_kinds_folder(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    files = {
        ".agents/harness.toml": NESTED_CONFIG,
        "docs/specs/a.md": doc("draft"),
        "docs/specs/assets/r.md": doc("tested"),
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", *files], check=True)

    done = census(root)

    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "docs/specs: 1 documents: draft 1\n"
        "docs/specs/archive: 0 documents\n"
        "docs/specs/assets: 1 documents: tested 1\n"
    )


def test_counts_a_qa_folder_with_no_archive_row(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / ".agents").mkdir()
    (root / ".agents" / "harness.toml").write_text(
        '[specs]\ndirs = ["docs/specs", "docs/qa"]\n\n[specs.kinds."docs/qa"]\nkind = "qa"\n',
        encoding="utf-8",
    )
    (root / "docs" / "qa").mkdir()
    (root / "docs" / "qa" / "r.md").write_text(doc("untested"), encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "docs/qa/r.md"], check=True)

    found = json.loads(census(root, "--json").stdout)

    assert list(found["folders"]) == ["docs/specs", "docs/specs/archive", "docs/qa"]
    assert found["folders"]["docs/qa"] == {"untested": 1}
