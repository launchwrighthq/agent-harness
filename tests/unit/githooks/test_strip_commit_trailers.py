import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "githooks" / "strip_commit_trailers.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("strip_commit_trailers", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sct = load()
AUTHOR = "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
CREDIT = "\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)"


def test_removes_a_co_author_trailer_and_the_credit_line() -> None:
    message = f"feat: add x\n\nBody text.\n\n{CREDIT}\n\n{AUTHOR}\n"

    assert sct.strip(message) == (
        "feat: add x\n\nBody text.\n",
        ["Co-Authored-By", "a generated-with credit"],
    )


def test_leaves_a_clean_message_byte_identical() -> None:
    message = "fix: keep  spacing \n\n\n\nBody   \n"

    assert sct.strip(message) == (message, [])


def test_keeps_a_subject_that_names_the_banned_trailer() -> None:
    message = "docs: the Co-Authored-By: ban\n"

    assert sct.strip(message) == (message, [])


def test_keeps_a_body_line_about_a_model_outside_the_trailer_block() -> None:
    message = "fix: y\n\nDefault: claude-opus-5\n\nmore text\n"

    assert sct.strip(message) == (message, [])
    assert sct.carries(message) is None


def test_removes_a_trailer_naming_claude_inside_the_trailer_block() -> None:
    message = "fix: y\n\nbody\n\nReviewed-By: a person\nRun-Id: https://claude.ai/code/abc\n"

    stripped, removed = sct.strip(message)

    assert stripped == "fix: y\n\nbody\n\nReviewed-By: a person\n"
    assert removed == ["a trailer naming Claude"]


def test_takes_a_folded_trailer_out_of_a_squashed_sentence() -> None:
    message = f"squash: a {AUTHOR} b\n"

    # The rule may not start inside a word, so the blank before the trailer stays.
    assert sct.strip(message) == ("squash: a  b\n", ["Co-Authored-By"])


@pytest.mark.parametrize(
    ("message", "rule"),
    [
        (f"x\n\n{AUTHOR}\n", "Co-Authored-By"),
        ("x\n\nClaude-Session: https://claude.ai/code/1\n", "a Claude- or Anthropic- trailer"),
        ("x\n\nGenerated-By: an agent\n", "an authorship trailer"),
        ("x\n\nsee https://claude.ai/share/1\n", "a Claude link"),
    ],
)
def test_names_the_first_rule_a_message_carries(message: str, rule: str) -> None:
    assert sct.carries(message) == rule


def test_rewrites_the_message_file_in_place(tmp_path: Path) -> None:
    file = tmp_path / "COMMIT_EDITMSG"
    file.write_text(f"feat: x\n\n{AUTHOR}\n", encoding="utf-8")

    removed = sct.strip_file(file)

    assert (file.read_text(encoding="utf-8"), removed) == ("feat: x\n", ["Co-Authored-By"])
