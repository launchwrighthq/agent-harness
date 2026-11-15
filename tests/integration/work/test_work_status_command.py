"""`ah work-status` reads the spec folders from the config of the repository it runs in."""

import json
import subprocess
import sys
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"


def test_reports_the_spec_a_session_named_in_a_configured_folder(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    (repo / ".agents").mkdir()
    (repo / ".agents" / "harness.toml").write_text(
        '[specs]\ndirs = ["design"]\nauditDir = "audits"\n', encoding="utf-8"
    )
    (repo / "design").mkdir()
    (repo / "design" / "a.md").write_text(
        "# A\n\nStatus: draft\n\n## 1. Scope\n\nThe scope sentence. More.\n", encoding="utf-8"
    )
    prompt = {
        "type": "user",
        "userType": "external",
        "cwd": str(repo),
        "timestamp": "2026-01-01T12:00:00Z",
        "message": {"content": "build design/a.md and docs/specs/b.md"},
    }
    transcript = tmp_path / "projects" / "slug" / "s1.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps(prompt) + "\n", encoding="utf-8")

    done = subprocess.run(
        [
            sys.executable,
            str(LAUNCHER),
            "work-status",
            "--session",
            "s1",
            "--projects-root",
            str(tmp_path / "projects"),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    report = done.stdout.split("\n")
    spec_at = report.index("## Spec")
    assert report[spec_at + 1].startswith("design/a.md (draft) first mentioned ")
    assert report[spec_at + 2] == "- 1. Scope | The scope sentence. | agents: none"
    named_at = report.index("## Specs named")
    assert report[named_at + 1].startswith("- a.md | draft | ")
    assert report[named_at + 1].endswith("| not audited")
    assert report[named_at + 2] == ""
