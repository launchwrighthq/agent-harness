from pathlib import Path

from agent_harness.spec.codex import Pass, codex_command


def test_pins_the_reasoning_effort_and_the_read_only_sandbox(tmp_path: Path) -> None:
    one = Pass(tmp_path / "a.md", tmp_path / "tree", "b", "h", tmp_path / "p", 1, None, None, "m")

    command = codex_command("/bin/codex", one)

    assert command == [
        "/bin/codex",
        "exec",
        "--sandbox",
        "read-only",
        "-c",
        'model_reasoning_effort="high"',
        "-C",
        str(tmp_path / "tree"),
        "--output-schema",
        str(tmp_path / "p" / "schema.json"),
        "-o",
        str(tmp_path / "p" / "last.json"),
        "-",
    ]
