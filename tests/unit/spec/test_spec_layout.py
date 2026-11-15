from pathlib import Path

import pytest

from agent_harness import HarnessError, config
from agent_harness.spec import layout


def write_config(root: Path, text: str) -> None:
    (root / ".agents").mkdir()
    (root / ".agents" / "harness.toml").write_text(text, encoding="utf-8")


def test_reads_the_spec_folders_from_the_config(tmp_path: Path) -> None:
    write_config(tmp_path, '[specs]\ndirs = ["design", "work/plans"]\narchive = "old"\n')

    found = layout.from_config(config.load(tmp_path))

    assert found.dirs == ("design", "work/plans")
    assert found.folders() == ["design", "design/old", "work/plans", "work/plans/old"]


def test_gives_a_plans_folder_the_plan_kind() -> None:
    assert layout.domain_for("docs/superpowers/plans").kind == "plan"
    assert layout.domain_for("docs/specs").label == "SPECIFICATION"


def test_matches_a_spec_path_in_a_configured_folder_only() -> None:
    found = layout.Layout(dirs=("design",), archive="archive", out="dist")

    assert found.mention_re().findall("see design/a.md and docs/specs/b.md") == ["design/a.md"]


def test_refuses_an_archive_name_with_a_slash(tmp_path: Path) -> None:
    write_config(tmp_path, '[specs]\narchive = "a/b"\n')

    with pytest.raises(HarnessError, match=r"specs\.archive must be one folder name"):
        layout.from_config(config.load(tmp_path))


QA_CONFIG = """[specs]
dirs = ["docs/specs", "docs/qa"]

[specs.kinds."docs/qa"]
kind = "qa"
label = "QA REPORT"
statuses = ["untested", "tested"]
"""


def test_reads_the_kind_label_and_statuses_of_a_folder_from_the_config(tmp_path: Path) -> None:
    write_config(tmp_path, QA_CONFIG)

    found = layout.from_config(config.load(tmp_path))

    assert found.domains == (
        layout.Domain("docs/specs", "spec", "SPECIFICATION"),
        layout.Domain("docs/qa", "qa", "QA REPORT", ("untested", "tested")),
    )


def test_refuses_a_kind_that_the_page_has_no_style_for(tmp_path: Path) -> None:
    write_config(tmp_path, '[specs.kinds."docs/specs"]\nkind = "memo"\n')

    with pytest.raises(HarnessError, match=r"specs\.kinds\.docs/specs\.kind must be one of"):
        layout.from_config(config.load(tmp_path))


def test_refuses_a_label_that_holds_markup(tmp_path: Path) -> None:
    write_config(tmp_path, '[specs.kinds."docs/qa"]\nlabel = "<script>alert(1)</script>"\n')

    with pytest.raises(HarnessError, match=r"specs\.kinds\.docs/qa\.label must match"):
        layout.from_config(config.load(tmp_path))
