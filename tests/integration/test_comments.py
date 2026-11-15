"""`ah comments` and `comment_findings` over the files of a git checkout."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_harness import HarnessError, comments, config

Ah = Callable[..., subprocess.CompletedProcess[str]]

IDENTITY = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
BUDGET = comments.budget_of(config.Config(Path("."), {}, None))


def ts_file(block: int, code: int) -> str:
    """A TS file with one comment block of `block` lines and `code` code lines."""
    lines = ["/**", *(f" * line {i}" for i in range(block - 2)), " */"]
    lines += [f"export const v{i} = {i};" for i in range(code)]
    return "\n".join([*lines, ""])


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(root: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [*IDENTITY, "-C", str(root), "commit", "-q", "--allow-empty", "-m", message],
        check=True,
    )


@pytest.fixture
def main_repo(repo: Path) -> Path:
    subprocess.run(["git", "-C", str(repo), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
    return repo


def test_holds_a_new_file_to_the_tight_cap_and_an_old_file_to_the_long_one(
    main_repo: Path,
) -> None:
    write(main_repo, "src/old.mts", ts_file(20, 10))
    commit(main_repo, "base")
    write(main_repo, "src/new.mts", ts_file(13, 10))
    write(main_repo, "src/tests/skipped.mts", ts_file(40, 10))

    found = comments.comment_findings(main_repo, BUDGET, "main")

    assert found == [f"src/new.mts has a 13-line comment block, cap {BUDGET['maxBlock']}"]


def test_limits_the_comment_share_of_a_new_file_with_enough_code_lines(main_repo: Path) -> None:
    commit(main_repo, "base")
    # Six blocks of 10 lines and one of 3, apart; 6 separator lines and 60 code lines.
    blocks = "".join(f"{ts_file(10, 0)}const gap = 0;\n" for _ in range(6))
    write(main_repo, "src/chatty.mts", f"{blocks}{ts_file(3, 60)}")
    lines = 6 * 10 + 3
    share = comments.percent(lines / (lines + 6 + 60))

    found = comments.comment_findings(main_repo, BUDGET, "main", ["src"])

    assert found == [f"src/chatty.mts is {share}% comments, cap {comments.percent(0.35)}%"]


def test_refuses_a_path_outside_the_checkout(main_repo: Path) -> None:
    with pytest.raises(HarnessError, match="is outside the checkout"):
        comments.comment_findings(main_repo, BUDGET, "main", ["/"])


def test_prints_each_finding_and_exits_1_and_exits_0_within_the_budget(
    main_repo: Path, ah: Ah
) -> None:
    commit(main_repo, "base")
    write(main_repo, "a.mts", ts_file(26, 1))

    red = ah("comments")
    write(main_repo, "a.mts", ts_file(5, 20))
    green = ah("comments")

    assert (red.returncode, red.stdout.splitlines()[0]) == (
        1,
        f"a.mts has a 26-line comment block, cap {BUDGET['maxBlock']}",
    )
    assert (green.returncode, green.stdout) == (
        0,
        "ah comments: every file is within the comment budget\n",
    )


def test_reads_the_caps_from_the_config(main_repo: Path, ah: Ah) -> None:
    write(main_repo, ".agents/harness.toml", "[harness]\ncomments.maxBlock = 30\n")
    commit(main_repo, "base")
    write(main_repo, "a.mts", ts_file(26, 1))

    assert ah("comments").returncode == 0
