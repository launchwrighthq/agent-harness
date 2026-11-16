import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from agent_harness import HarnessError
from agent_harness.deploy.serving import USER_AGENT, execute, read_target

PACKAGE = Path(__file__).resolve().parents[3]
LAUNCHER = PACKAGE / "bin" / "ah"
FIVE_PHASE = PACKAGE / "fixtures" / "deploy" / "github" / "five-phase-workflow"
DIGEST = "sha256:" + "a" * 64

# Serves `gh run list` and `gh api .../jobs` from the recorded fixtures and logs each call.
FAKE_GH = """#!/usr/bin/env python3
import os, re, sys
from pathlib import Path
data = Path(os.environ["FAKE_GH_DIR"])
args = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as log:
    log.write("gh " + " ".join(args) + "\\n")
if args[:2] == ["auth", "status"]:
    sys.exit(0)
if args[0] == "api" and args[1].startswith("repos/") and "/actions/" not in args[1]:
    print('{"full_name": "example-org/example-app"}')
    sys.exit(0)
if args[:2] == ["run", "list"]:
    print((data / "runs.json").read_text())
    sys.exit(0)
found = re.search(r"/runs/(\\d+)/jobs", args[1]) if args[0] == "api" else None
if found:
    print((data / f"jobs-{found.group(1)}.json").read_text())
    sys.exit(0)
print("fake gh: unknown call " + " ".join(args), file=sys.stderr)
sys.exit(1)
"""

# Prints what `lw deploy status <target> --json` prints, and exits 1 for its problem.
FAKE_LW = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_CALLS"], "a") as log:
    log.write("lw " + " ".join(sys.argv[1:]) + "\\n")
digest = "sha256:" + "a" * 64
node = {"node": "node-1", "release": "r7", "digest": digest, "healthy": False,
        "containers": [{"service": "web", "state": "running", "health": "unhealthy",
                        "digest": digest}], "detail": "web unhealthy"}
print(json.dumps({"target": sys.argv[3], "nodes": [node], "last": None, "ok": False,
                  "problems": ["node-1 is not healthy: web unhealthy"]}))
sys.exit(1)
"""

# Prints `docker ps` lines for box-1 and fails for every other host.
FAKE_SSH = """#!/usr/bin/env python3
import os, sys
with open(os.environ["FAKE_CALLS"], "a") as log:
    log.write("ssh " + sys.argv[-2] + "\\n")
if sys.argv[-2] != "box-1":
    print("ssh: connect to host " + sys.argv[-2] + " port 22: Connection refused", file=sys.stderr)
    sys.exit(255)
print("app-web-v42 ghcr.io/o/app:v42 ghcr.io/o/app@%s" % ("sha256:" + "a" * 64))
"""

CONFIG = """
[deploy]
records = "deploy/releases"

[deploy.ci]
workflows = ["deploy.yml"]

[deploy.environments.production]
match = { runName = "deploy production" }

[deploy.phases]
noop = ["migrate"]

[deploy.phases.moves]
build = "image"
migrate = "database"
apply = "nodes"

[deploy.phases.serving]
apply = "nodes"

[deploy.targets.production]
read = "lw"

[deploy.targets.boxes]
read = "ssh-docker"
env = "production"
nodes = ["box-1", "box-2"]
roles = { web = "app-web-" }
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".agents").mkdir()
    (root / ".agents" / "harness.toml").write_text(CONFIG)
    fakes = tmp_path / "bin"
    fakes.mkdir()
    for name, text in (("gh", FAKE_GH), ("lw", FAKE_LW), ("ssh", FAKE_SSH)):
        (fakes / name).write_text(text)
        (fakes / name).chmod(0o755)
    return root


def run_ah(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "AH_HANDED_OVER"}
    env["PATH"] = f"{root.parent / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_GH_DIR"] = str(FIVE_PHASE)
    env["FAKE_CALLS"] = str(root.parent / "calls.log")
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "deploy-status", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_prints_the_recorded_verdicts_and_the_serving_targets(project: Path) -> None:
    done = run_ah(project, "--history", "3650d")

    assert done.returncode == 0, done.stderr
    for verdict in ("failed at migrate", "nothing to deploy", "deployed"):
        assert f"│ {verdict} " in done.stdout
    assert "  problem: node-1 is not healthy: web unhealthy\n" in done.stdout
    assert (
        "  problem: box-2 did not answer: ssh: connect to host box-2 port 22: Connection refused\n"
        in done.stdout
    )
    assert "│ box-1 │ web  │ v42" in done.stdout


def test_makes_only_read_calls(project: Path) -> None:
    run_ah(project, "--json")

    calls = (project.parent / "calls.log").read_text().splitlines()
    assert calls[:2] == [
        "gh api repos/{owner}/{repo} --jq .full_name",
        "gh run list --workflow=deploy.yml --limit 200 --json attempt,conclusion,createdAt,"
        "databaseId,displayTitle,event,headBranch,headSha,startedAt,status,updatedAt,workflowName",
    ]
    reads = (
        "gh api repos/{owner}/{repo} --jq .full_name",
        "gh run list ",
        "gh api /repos/{owner}/{repo}/actions/runs/",
        "lw deploy status ",
        "ssh ",
    )
    assert [c for c in calls if not c.startswith(reads)] == []
    assert "lw deploy status production --json" in calls


def test_gives_the_failed_migrate_as_the_last_deploy_in_json(project: Path) -> None:
    done = run_ah(project, "--json")

    assert done.returncode == 0, done.stderr
    (env,) = json.loads(done.stdout)["environments"]
    assert env["latest"]["verdict"] == "failed at migrate"
    assert env["servingFromCi"][0]["run"] == "19000000101"
    assert [t["target"] for t in env["targets"]] == ["production", "boxes"]


def test_fails_with_the_install_hint_when_gh_is_missing(project: Path) -> None:
    (project.parent / "bin" / "gh").unlink()
    env = {**os.environ, "PATH": str(project.parent / "bin")}
    env.pop("AH_HANDED_OVER", None)
    done = subprocess.run(
        [sys.executable, str(LAUNCHER), "deploy-status"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 1
    assert "gh is not on PATH. Install the GitHub CLI" in done.stderr


class VersionHandler(BaseHTTPRequestHandler):
    agents: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        VersionHandler.agents.append(self.headers.get("User-Agent", ""))
        body = json.dumps({"version": {"build": "1234"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-App-Release", "0f1e2d3c")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def version_url() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), VersionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/version"
    server.shutdown()
    server.server_close()


def test_reads_a_version_from_a_loopback_http_server(tmp_path: Path, version_url: str) -> None:
    reading = read_target(
        "web", {"read": "http", "url": version_url, "field": "version.build"}, tmp_path
    )

    assert [(r.node, r.version, r.state) for r in reading.rows] == [
        (version_url, "1234", "answered")
    ]
    assert reading.problems == []


def test_sends_a_user_agent_of_its_own_on_an_http_check(tmp_path: Path, version_url: str) -> None:
    VersionHandler.agents.clear()

    read_target("web", {"read": "http", "url": version_url}, tmp_path)

    assert VersionHandler.agents == [USER_AGENT]


def test_reads_the_live_version_from_a_response_header(tmp_path: Path, version_url: str) -> None:
    spec = {"read": "http", "url": version_url, "header": "x-app-release"}

    reading = read_target("web", spec, tmp_path)

    assert [(r.node, r.version) for r in reading.rows] == [(version_url, "0f1e2d3c")]


def test_stops_a_command_that_runs_past_its_timeout(tmp_path: Path) -> None:
    argv = [sys.executable, "-c", "import threading; threading.Event().wait()"]

    with pytest.raises(HarnessError, match=r"gave no answer in 0\.5 s"):
        execute(argv, tmp_path, 0.5)
