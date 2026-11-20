import json
from pathlib import Path

import pytest

from agent_harness import HarnessError, config


def write_config(root: Path, text: str) -> None:
    file = root / ".agents" / "harness.toml"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")


def write_origin_head(git_dir: Path, branch: str) -> None:
    head = git_dir / "refs" / "remotes" / "origin" / "HEAD"
    head.parent.mkdir(parents=True)
    head.write_text(f"ref: refs/remotes/origin/{branch}\n", encoding="utf-8")


def test_returns_the_spec_defaults_without_a_config_file(tmp_path: Path) -> None:
    loaded = config.load(tmp_path)

    assert loaded.file is None
    assert loaded.get("specs.dirs") == ["docs/specs", "docs/superpowers/plans"]
    assert loaded.get("specs.archive") == "archive"
    assert loaded.get("specs.out") == "dist"
    assert loaded.get("harness.workspace.worktreesOnly") is False


def test_keeps_the_project_files_of_the_harness_in_claude_by_default(tmp_path: Path) -> None:
    assert config.load(tmp_path).get("harness.adapterDir") == ".claude/adapters"
    assert config.load(tmp_path).get("harness.manifests") == []

    manifest = tmp_path / ".claude" / "githooks" / "harness.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("", encoding="utf-8")

    assert config.load(tmp_path).get("harness.manifests") == [".claude/githooks/harness.toml"]


def test_defaults_the_base_branch_to_main_without_an_origin_head(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    assert config.get("harness.baseBranch", tmp_path) == "main"


def test_reads_the_base_branch_from_the_origin_head_ref(tmp_path: Path) -> None:
    write_origin_head(tmp_path / ".git", "trunk")

    assert config.get("harness.baseBranch", tmp_path) == "trunk"


def test_reads_the_origin_head_of_a_linked_worktree_from_the_common_dir(tmp_path: Path) -> None:
    common = tmp_path / "main" / ".git"
    own = common / "worktrees" / "side"
    own.mkdir(parents=True)
    (own / "commondir").write_text("../..\n", encoding="utf-8")
    write_origin_head(common, "develop")
    checkout = tmp_path / "side"
    checkout.mkdir()
    (checkout / ".git").write_text(f"gitdir: {own}\n", encoding="utf-8")

    assert config.get("harness.baseBranch", checkout) == "develop"


def test_puts_a_file_value_over_the_default(tmp_path: Path) -> None:
    write_origin_head(tmp_path / ".git", "trunk")
    write_config(tmp_path, '[harness]\nbaseBranch = "release"\n')

    assert config.get("harness.baseBranch", tmp_path) == "release"


def test_keeps_the_default_keys_beside_a_file_value_in_one_table(tmp_path: Path) -> None:
    write_config(tmp_path, "[harness.workspace]\nworktreesOnly = true\n")
    loaded = config.load(tmp_path)

    assert loaded.get("harness.workspace.worktreesOnly") is True
    assert "user.name" in loaded.get("harness.workspace.gitConfigAllow")


def test_returns_a_key_that_only_the_file_holds(tmp_path: Path) -> None:
    write_config(tmp_path, '[deploy.ci]\nprovider = "github"\n')

    assert config.get("deploy.ci.provider", tmp_path) == "github"


def test_refuses_an_unknown_key_and_names_the_known_keys(tmp_path: Path) -> None:
    with pytest.raises(HarnessError, match=r"unknown config key specs\.nope\. .*archive"):
        config.get("specs.nope", tmp_path)


def test_refuses_a_key_below_a_plain_value(tmp_path: Path) -> None:
    with pytest.raises(HarnessError, match=r"specs\.out is a value, not a table"):
        config.get("specs.out.deeper", tmp_path)


def test_names_the_file_when_the_toml_is_invalid(tmp_path: Path) -> None:
    write_config(tmp_path, "[harness\n")

    with pytest.raises(HarnessError, match=r"harness\.toml is not valid TOML"):
        config.load(tmp_path)


def test_returns_a_copy_of_a_default_list(tmp_path: Path) -> None:
    first = config.get("specs.dirs", tmp_path)
    first.append("changed")

    assert "changed" not in config.get("specs.dirs", tmp_path)


def test_serves_the_defaults_that_an_area_registers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "SECTION_DEFAULTS", dict(config.SECTION_DEFAULTS))
    config.register_defaults("probe", {"master": ".env.example", "store": {"kind": "file"}})
    config.register_defaults("probe", {"store": {"path": "secrets"}})

    assert config.get("probe.master", tmp_path) == ".env.example"
    assert config.get("probe.store", tmp_path) == {"kind": "file", "path": "secrets"}


def test_prints_a_string_value_as_it_is(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_config(tmp_path, '[specs]\nout = "pages"\n')

    assert config.main(["get", "specs.out"], tmp_path) == 0
    assert capsys.readouterr().out == "pages\n"


def test_prints_a_list_value_as_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_config(tmp_path, '[specs]\ndirs = ["specs"]\n')

    assert config.main(["get", "specs.dirs"], tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == ["specs"]


def test_exits_one_with_a_message_for_an_unknown_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert config.main(["get", "harness.nope"], tmp_path) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ah config: unknown config key harness.nope.")


def test_exits_two_with_the_usage_for_a_wrong_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert config.main(["set", "specs.out"], tmp_path) == 2
    assert capsys.readouterr().err.startswith("usage: ah config get <key>")


def test_takes_the_project_root_from_ah_root_when_no_start_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "outer" / "demo"
    project.mkdir(parents=True)
    monkeypatch.setenv(config.ROOT_ENV, str(project))

    assert config.find_root() == project.resolve()
