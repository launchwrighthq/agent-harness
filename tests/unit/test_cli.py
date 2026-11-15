from pathlib import Path

import pytest

from agent_harness import __version__, cli


def make_project_install(repo: Path) -> Path:
    engine = repo / ".claude" / "agent-harness" / "bin" / "ah"
    engine.parent.mkdir(parents=True)
    engine.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return engine


def test_hands_over_from_a_launcher_outside_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    engine = make_project_install(repo)
    global_root = tmp_path / "home" / ".claude" / "agent-harness"
    global_root.mkdir(parents=True)

    assert cli.handover_target(global_root, repo, handed_over=False) == engine


def test_runs_here_when_the_repository_has_no_project_install(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    assert cli.handover_target(tmp_path / "global", repo, handed_over=False) is None


def test_runs_here_when_the_launcher_sits_inside_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    engine = make_project_install(repo)

    assert cli.handover_target(engine.parent.parent, repo, handed_over=False) is None
    assert cli.handover_target(repo / "agent-harness", repo, handed_over=False) is None


def test_never_hands_over_twice(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_project_install(repo)

    assert cli.handover_target(tmp_path / "global", repo, handed_over=True) is None


def test_exits_two_with_the_usage_for_an_unknown_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.dispatch(["nope"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("ah: unknown command nope")
    assert "deploy-status" in err


def test_exits_two_with_the_usage_when_no_command_is_given(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.dispatch([]) == 2
    assert capsys.readouterr().err.startswith("usage: ah <command>")


def test_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.dispatch(["--version"]) == 0
    assert capsys.readouterr().out == f"{__version__}\n"
