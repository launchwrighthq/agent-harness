import subprocess
import sys
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"
OLD = (
    "<html><head><title>Orders</title></head><body><nav>Orders Scope</nav>"
    "<h1>Orders</h1><p>Status: draft</p><h2>1. Scope</h2><p>The cache keeps {words}.</p>"
    "</body></html>"
)
NEW = "# Orders\n\nDate: 2026-01-01\nStatus: draft\n\n## 1. Scope\n\nThe cache keeps {words}.\n"


def verify(root: Path, old: str, new: str) -> subprocess.CompletedProcess[str]:
    (root / "old.html").write_text(old, encoding="utf-8")
    (root / "docs/specs").mkdir(parents=True, exist_ok=True)
    (root / "docs/specs/orders.md").write_text(new, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "spec", "verify", "old.html", "docs/specs/orders.md"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_passes_a_conversion_that_keeps_every_word(tmp_path: Path) -> None:
    done = verify(tmp_path, OLD.format(words="ten rows"), NEW.format(words="ten rows"))

    assert done.returncode == 0, done.stderr
    assert done.stdout == "no words lost, no words invented\n"


def test_fails_and_lists_each_word_the_rendered_page_lost(tmp_path: Path) -> None:
    done = verify(tmp_path, OLD.format(words="ten hot rows"), NEW.format(words="ten rows"))

    assert done.returncode == 1
    assert done.stdout == "1 words are in old.html and nowhere in the rendered page:\n  hot\n"


def test_lists_an_invented_word_and_still_passes(tmp_path: Path) -> None:
    done = verify(tmp_path, OLD.format(words="ten rows"), NEW.format(words="ten new rows"))

    assert done.returncode == 0
    assert done.stdout == "1 words are in orders.md and not in old.html:\n  new\n"


def test_fails_when_the_markdown_does_not_render(tmp_path: Path) -> None:
    done = verify(tmp_path, OLD.format(words="x"), "no title\n")

    assert done.returncode == 1
    assert done.stderr == (
        "ah spec: docs/specs/orders.md: did not render: "
        "docs/specs/orders.md: missing a '# H1' at the top of the file\n"
    )
