import fcntl
import os
from pathlib import Path

import pytest

from agent_harness import HarnessError, config
from agent_harness.work import sweep

NOW = 1_800_000_000.0


def loaded(root: Path, text: str) -> config.Config:
    file = root / ".agents" / "harness.toml"
    file.parent.mkdir(parents=True)
    file.write_text(text, encoding="utf-8")
    return config.load(root)


def test_turns_the_sweep_off_by_default(tmp_path: Path) -> None:
    assert sweep.settings(config.load(tmp_path)) == sweep.Settings(0.0, 30.0)


def test_reads_the_stale_window_and_the_interval_of_the_sweep(tmp_path: Path) -> None:
    cfg = loaded(tmp_path, "[branches.autoApply]\nstaleHours = 36\neveryMinutes = 0.5\n")

    assert sweep.settings(cfg) == sweep.Settings(36.0, 0.5)


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        ("staleHours = -1", "staleHours must be a number of 0 or more, not -1"),
        ("staleHours = true", "staleHours must be a number of 0 or more, not True"),
        ('staleHours = "24"', "staleHours must be a number of 0 or more, not '24'"),
        ("staleHours = inf", "staleHours must be a number of 0 or more, not inf"),
        ("everyMinutes = -5", "everyMinutes must be a number of 0 or more, not -5"),
        ("everyMinutes = [30]", "everyMinutes must be a number of 0 or more, not [30]"),
    ],
)
def test_refuses_a_sweep_value_that_is_no_number_of_0_or_more(
    tmp_path: Path, line: str, shown: str
) -> None:
    cfg = loaded(tmp_path, f"[branches.autoApply]\n{line}\n")

    with pytest.raises(HarnessError) as refused:
        sweep.settings(cfg)

    assert str(refused.value) == f"config key branches.autoApply.{shown}"


def test_refuses_a_sweep_config_that_is_no_table(tmp_path: Path) -> None:
    cfg = loaded(tmp_path, "[branches]\nautoApply = 24\n")

    with pytest.raises(HarnessError) as refused:
        sweep.settings(cfg)

    assert str(refused.value) == "config key branches.autoApply must be a table, not 24"


def test_claims_the_first_sweep_and_writes_its_time_to_the_stamp(tmp_path: Path) -> None:
    lock = sweep.claim(tmp_path / "state", 30, NOW)

    assert lock is not None
    os.close(lock)
    assert (tmp_path / "state" / "stamp").read_text(encoding="utf-8") == f"{NOW}\n"


def test_claims_no_sweep_inside_the_interval_and_one_after_it(tmp_path: Path) -> None:
    first = sweep.claim(tmp_path, 30, NOW)
    assert first is not None
    os.close(first)

    inside = sweep.claim(tmp_path, 30, NOW + 29 * 60)
    after = sweep.claim(tmp_path, 30, NOW + 30 * 60)

    assert inside is None
    assert after is not None
    os.close(after)
    assert (tmp_path / "stamp").read_text(encoding="utf-8") == f"{NOW + 30 * 60}\n"


def test_claims_no_sweep_while_another_holder_has_the_lock(tmp_path: Path) -> None:
    with open(tmp_path / "lock", "w", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)

        assert sweep.claim(tmp_path, 0, NOW) is None


def test_claims_a_sweep_over_a_stamp_that_holds_no_time(tmp_path: Path) -> None:
    (tmp_path / "stamp").write_text("not a time\n", encoding="utf-8")

    lock = sweep.claim(tmp_path, 30, NOW)

    assert lock is not None
    os.close(lock)


def test_reads_the_owner_session_of_a_worktree_and_no_other_text(tmp_path: Path) -> None:
    assert sweep.owner_session(str(tmp_path)) is None
    (tmp_path / "lw-owner").write_text("session-1\n", encoding="utf-8")
    assert sweep.owner_session(str(tmp_path)) == "session-1"
    (tmp_path / "lw-owner").write_text("../other folder\n", encoding="utf-8")
    assert sweep.owner_session(str(tmp_path)) is None
