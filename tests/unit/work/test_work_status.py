import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_harness.spec.layout import Layout
from agent_harness.work import status

AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
DESIGN = Layout(dirs=("design", "work/plans"), archive="archive", out="dist").mention_re()


def prompt(text: str, at: datetime = AT) -> dict[str, Any]:
    return {
        "type": "user",
        "userType": "external",
        "message": {"content": text},
        "_ts": at,
    }


def agent_spawn(prompt_text: str) -> dict[str, Any]:
    block = {
        "type": "tool_use",
        "id": "t1",
        "name": "Agent",
        "input": {"description": "Build section 2", "prompt": prompt_text},
    }
    return {"type": "assistant", "message": {"content": [block]}, "_ts": AT}


def test_finds_spec_paths_in_the_configured_folders_only() -> None:
    records = [prompt("build design/a.md, not docs/specs/b.md, then work/plans/c.md")]

    found = status.spec_mentions(records, DESIGN)

    assert sorted(found) == ["design/a.md", "work/plans/c.md"]


def test_reads_the_spec_of_an_agent_prompt_with_the_configured_pattern() -> None:
    rows = status.task_rows([agent_spawn("Read design/a.md, part 2.")], [], DESIGN)

    assert rows[0].spec == "design/a.md"
    assert rows[0].section == "2"


def test_puts_the_configured_base_branch_before_main_and_master(tmp_path: Path) -> None:
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "harness.toml").write_text('[harness]\nbaseBranch = "trunk"\n')

    assert status.base_refs(tmp_path) == ["trunk", "main", "master"]


def test_lists_main_once_when_the_config_names_main(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    assert status.base_refs(tmp_path) == ["main", "master"]


def test_keeps_the_default_pattern_free_of_archive_paths() -> None:
    pattern: re.Pattern[str] = Layout(
        dirs=("docs/specs",), archive="archive", out="dist"
    ).mention_re()

    assert pattern.findall("docs/specs/archive/a.md and docs/specs/b.md") == ["docs/specs/b.md"]
