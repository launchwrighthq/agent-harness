from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.install.lock import (
    Content,
    Lock,
    apply_files,
    file_differences,
    read_lock,
    sha256,
)


def lock_of(files: dict[str, bytes]) -> Lock:
    return Lock(version="1", mode="project", files={k: sha256(v) for k, v in files.items()})


def test_writes_new_files_and_removes_stale_ones_with_empty_folders(tmp_path: Path) -> None:
    old = tmp_path / "eng" / "old" / "gone.py"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x")
    lines: list[str] = []

    changes = apply_files(
        tmp_path,
        {"eng/new.sh": Content(b"echo\n", executable=True)},
        lock_of({"eng/old/gone.py": b"x"}),
        lines.append,
    )

    assert changes == 2
    assert lines == ["wrote eng/new.sh", "removed eng/old/gone.py"]
    assert not (tmp_path / "eng" / "old").exists()
    assert (tmp_path / "eng" / "new.sh").stat().st_mode & 0o100


def test_writes_nothing_when_every_file_matches(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"a")

    assert apply_files(tmp_path, {"a.txt": Content(b"a", False)}, None, print) == 0


def test_finds_missing_edited_mode_and_extra_files(tmp_path: Path) -> None:
    files = {"own/a": b"a", "own/b": b"b", "own/c": b"c"}
    lock = lock_of(files)
    lock.executable = ["own/c"]
    (tmp_path / "own").mkdir()
    (tmp_path / "own" / "b").write_bytes(b"B")
    (tmp_path / "own" / "c").write_bytes(b"c")
    (tmp_path / "own" / "d").write_bytes(b"d")
    (tmp_path / "own" / "__pycache__").mkdir()
    (tmp_path / "own" / "__pycache__" / "x.pyc").write_bytes(b"")

    found = file_differences(tmp_path, lock, ["own"])

    assert sorted(found) == [
        ("own/a", "missing"),
        ("own/b", "edited"),
        ("own/c", "mode"),
        ("own/d", "extra"),
    ]


def test_reads_back_the_lock_it_writes(tmp_path: Path) -> None:
    lock = Lock(version="1", mode="global", files={"a": "f"}, skills=["lw-a"])
    (tmp_path / "lock.json").write_text(lock.to_text())

    assert read_lock(tmp_path / "lock.json") == lock
    assert read_lock(tmp_path / "none.json") is None


def test_refuses_a_lock_that_is_not_json(tmp_path: Path) -> None:
    (tmp_path / "lock.json").write_text("{")

    with pytest.raises(HarnessError, match="is not valid JSON"):
        read_lock(tmp_path / "lock.json")
