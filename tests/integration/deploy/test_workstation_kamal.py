"""The workstation example of docs/deploy.md: job records and Kamal nodes, with no engine change."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[3]
LAUNCHER = PACKAGE / "bin" / "ah"
FIXTURE = PACKAGE / "fixtures" / "deploy" / "workstation-kamal"
EXAMPLE_HEADING = "### A workstation deploy with Kamal nodes"
SHA = "e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0"

# GitHub Actions starts no run, so `gh run list` answers an empty list. The repository check
# (`gh api repos/...`) sees the repository.
FAKE_GH = """#!/usr/bin/env python3
import os, sys
from pathlib import Path
with open(os.environ["FAKE_CALLS"], "a") as log:
    log.write("gh " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["auth", "status"]:
    sys.exit(0)
if sys.argv[1] == "api" and sys.argv[2].startswith("repos/"):
    print("owner/app")
    sys.exit(0)
if sys.argv[1:3] == ["run", "list"]:
    print((Path(os.environ["FAKE_DIR"]) / "runs.json").read_text())
    sys.exit(0)
print("fake gh: unknown call", file=sys.stderr)
sys.exit(1)
"""

# Prints the recorded `docker ps` lines of the host that the last but one argument names.
FAKE_SSH = """#!/usr/bin/env python3
import os, sys
from pathlib import Path
host = sys.argv[-2]
with open(os.environ["FAKE_CALLS"], "a") as log:
    log.write("ssh " + host + "\\n")
print((Path(os.environ["FAKE_DIR"]) / "docker-ps" / (host.split("@")[-1] + ".txt")).read_text())
"""


def example_config() -> str:
    """The toml block under the example heading, so the test reads what the docs show."""
    text = (PACKAGE / "docs" / "deploy.md").read_text(encoding="utf-8")
    section = text[text.index(EXAMPLE_HEADING) :]
    found = re.search(r"```toml\n(.*?)```", section, re.S)
    assert found is not None
    return found.group(1)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".agents").mkdir()
    (root / ".agents" / "harness.toml").write_text(example_config(), encoding="utf-8")
    shutil.copytree(FIXTURE / "infra", root / "infra")
    shutil.copytree(FIXTURE / "deploys", root / ".claude" / "state" / "deploys")
    fakes = tmp_path / "bin"
    fakes.mkdir()
    for name, text in (("gh", FAKE_GH), ("ssh", FAKE_SSH)):
        (fakes / name).write_text(text, encoding="utf-8")
        (fakes / name).chmod(0o755)
    return root


def run_ah(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "AH_HANDED_OVER"}
    env["PATH"] = f"{root.parent / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_DIR"] = str(FIXTURE)
    env["FAKE_CALLS"] = str(root.parent / "calls.log")
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "deploy-status", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_reads_the_workstation_record_as_the_last_deploy(project: Path) -> None:
    done = run_ah(project, "--json")

    assert done.returncode == 0, done.stderr
    (env,) = json.loads(done.stdout)["environments"]
    latest = env["latest"]
    assert (latest["run"], latest["commit"], latest["verdict"]) == ("workstation", SHA, "deployed")
    assert latest["version"] == "10610"
    serving = {row["surface"]: row["run"] for row in env["servingFromCi"]}
    assert serving == dict.fromkeys(("origin", "box nodes", "workers"), "workstation")


def test_reads_each_kamal_role_of_each_node_of_the_fleet_file(project: Path) -> None:
    done = run_ah(project, "--json")

    (env,) = json.loads(done.stdout)["environments"]
    (boxes,) = env["targets"]
    rows = [(r["node"], r["role"], r["version"][:8]) for r in boxes["rows"]]
    assert rows == [
        ("box-1", "web", "e1f2a3b4"),
        ("box-1", "worker", "e1f2a3b4"),
        ("box-1", "renderer", "0a1b2c3d"),
        ("box-1", "social", "0a1b2c3d"),
        ("box-2", "web", "e1f2a3b4"),
        ("box-2", "worker", "e1f2a3b4"),
        ("box-2", "renderer", "0a1b2c3d"),
    ]
    assert boxes["problems"] == ["box-2 runs no social container"]
    calls = (project.parent / "calls.log").read_text().splitlines()
    assert [c for c in calls if c.startswith("ssh ")] == ["ssh root@box-1", "ssh root@box-2"]
