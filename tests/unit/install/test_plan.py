from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.install.plan import PlannedFile, build_plan, interpreter_for
from agent_harness.manifest import Manifest, parse_manifest

PACKAGE = """
[[hook]]
id = "first"
summary = "s"
events = ["PreToolUse"]
script = "hooks/first.py"

[[hook]]
id = "second"
summary = "s"
events = ["PreToolUse"]
script = "hooks/second.sh"
default = "off"

[[rule]]
id = "style"
summary = "Style"
file = "rules/style.md"
paths = ["docs/**"]

[[skill]]
id = "lw-demo"
dir = "skills/lw-demo"
"""


def manifest(tmp_path: Path, name: str, text: str, *, extra: bool) -> Manifest:
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return parse_manifest(
        text,
        name=name,
        dir=folder,
        file=folder / "harness.toml",
        extra=extra,
        check_files=False,
    )


def extra_hook(hook_id: str) -> str:
    return f'[[hook]]\nid = "{hook_id}"\nsummary = "s"\nevents = ["Stop"]\nscript = "h.mjs"\n'


def test_orders_the_package_first_then_extras_by_name(tmp_path: Path) -> None:
    package = manifest(tmp_path, "agent-harness", PACKAGE, extra=False)
    zeta = manifest(tmp_path, "zeta", extra_hook("zeta-hook"), extra=True)
    alpha = manifest(tmp_path, "alpha", extra_hook("alpha-hook"), extra=True)

    plan = build_plan([zeta, package, alpha])

    assert [h.script for h in plan.hooks] == [
        ".claude/agent-harness/hooks/first.py",
        ".claude/agent-harness/manifests/alpha/h.mjs",
        ".claude/agent-harness/manifests/zeta/h.mjs",
    ]


def test_leaves_out_a_disabled_item_and_adds_an_enabled_one(tmp_path: Path) -> None:
    package = manifest(tmp_path, "agent-harness", PACKAGE, extra=False)

    plan = build_plan([package], disable=["first", "lw-demo"], enable=["second"])

    assert [h.id for h in plan.hooks] == ["second"]
    assert plan.skills == []
    assert plan.known == ["first", "second", "style", "lw-demo"]


def test_refuses_a_config_entry_that_names_no_item(tmp_path: Path) -> None:
    package = manifest(tmp_path, "agent-harness", PACKAGE, extra=False)

    with pytest.raises(HarnessError, match="names frist, which no manifest"):
        build_plan([package], disable=["frist"])


def test_accepts_a_project_skill_in_the_disable_list(tmp_path: Path) -> None:
    package = manifest(tmp_path, "agent-harness", PACKAGE, extra=False)

    plan = build_plan([package], disable=["notes"], project_skills=["notes"])

    assert [s.id for s in plan.skills] == ["lw-demo"]


def test_refuses_one_id_in_two_manifests(tmp_path: Path) -> None:
    one = manifest(tmp_path, "one", extra_hook("same"), extra=True)
    two = manifest(tmp_path, "two", extra_hook("same"), extra=True)

    with pytest.raises(HarnessError, match="id same is defined by"):
        build_plan([one, two])


def test_refuses_two_rules_with_one_file_name(tmp_path: Path) -> None:
    rule = '[[rule]]\nid = "{id}"\nsummary = "s"\nfile = "style.md"\n'
    one = manifest(tmp_path, "one", rule.format(id="a"), extra=True)
    two = manifest(tmp_path, "two", rule.format(id="b"), extra=True)

    with pytest.raises(HarnessError, match=r"\.claude/rules/lw/style\.md is written by two items"):
        build_plan([one, two])


def test_refuses_a_node_hook_of_the_package(tmp_path: Path) -> None:
    package = manifest(tmp_path, "agent-harness", extra_hook("nodey"), extra=False)

    with pytest.raises(HarnessError, match="hook nodey needs Node"):
        build_plan([package])


def test_maps_each_hook_suffix_to_its_interpreter() -> None:
    assert [interpreter_for(s) for s in ("a.py", "b.sh", "c.mjs")] == ["python3", "bash", "node"]
    with pytest.raises(HarnessError, match=r"no interpreter is known for d\.rb"):
        interpreter_for("d.rb")


def test_writes_the_paths_front_matter_over_the_source_front_matter(tmp_path: Path) -> None:
    source = tmp_path / "rule.md"
    source.write_text('---\npaths:\n  - "old/**"\n---\n# Rule\n')

    file = PlannedFile(".claude/rules/lw/rule.md", source, paths=("**/tests/**",))

    assert file.content() == b'---\npaths:\n  - "**/tests/**"\n---\n# Rule\n'


def test_ends_a_rule_with_its_generated_line_after_the_project_notes(tmp_path: Path) -> None:
    source = tmp_path / "rule.md"
    source.write_text("# Rule\n\n- One.\n")
    notes = tmp_path / "notes.md"
    notes.write_text("A note.\n")

    file = PlannedFile(".claude/rules/lw/rule.md", source, notes=notes, line="A generated line.")

    assert file.content() == (
        b"# Rule\n\n- One.\n\n## Project notes\n\nA note.\n\nA generated line.\n"
    )


def test_plans_a_rule_with_the_paths_and_the_line_that_the_caller_gives(tmp_path: Path) -> None:
    package = manifest(tmp_path, "agent-harness", PACKAGE, extra=False)

    plan = build_plan(
        [package], rule_paths={"style": ["web/**"]}, rule_lines={"style": "A line.", "none": "x"}
    )

    rule = next(f for f in plan.files if f.target == ".claude/rules/lw/style.md")
    assert plan.rules[0].paths == ("web/**",)
    assert (rule.paths, rule.line) == (("web/**",), "A line.")


def test_refuses_paths_for_an_id_that_is_no_rule(tmp_path: Path) -> None:
    package = manifest(tmp_path, "agent-harness", PACKAGE, extra=False)

    with pytest.raises(HarnessError, match="names lw-demo, which no manifest defines as a rule"):
        build_plan([package], rule_paths={"lw-demo": ["web/**"]})
