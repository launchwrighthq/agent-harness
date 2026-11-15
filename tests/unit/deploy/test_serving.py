import json
from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.deploy.serving import DOCKER_PS, Done, Fetched, read_target

DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


class FakeCommands:
    """Answers each command by its program and target, and keeps every call."""

    def __init__(self, answers: dict[str, Done]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], cwd: Path) -> Done:
        self.calls.append(argv)
        key = next((k for k in self.answers if k in argv), None)
        if key is None:
            raise HarnessError(f"{argv[0]} is not on PATH")
        return self.answers[key]


def lw_status(problems: list[str]) -> str:
    container = {"service": "web", "state": "running", "health": "healthy", "digest": DIGEST}
    nodes = [
        {
            "node": "node-1",
            "release": "r7",
            "digest": DIGEST,
            "healthy": True,
            "containers": [container],
        },
        {
            "node": "node-2",
            "release": "r6",
            "digest": OTHER,
            "healthy": False,
            "containers": [container],
            "detail": "web unhealthy",
        },
    ]
    last = {"id": "r7", "digest": DIGEST, "at": "2026-11-12T09:11:00Z", "status": "applied"}
    return json.dumps(
        {
            "target": "production",
            "nodes": nodes,
            "last": last,
            "ok": not problems,
            "problems": problems,
        }
    )


def test_reads_nodes_and_problems_from_lw_deploy_status(tmp_path: Path) -> None:
    problems = ["node-2 is not healthy: web unhealthy", "the nodes run 2 digests"]
    commands = FakeCommands({"status": Done(1, lw_status(problems), "")})

    reading = read_target("production", {"read": "lw"}, tmp_path, commands)

    assert commands.calls == [["lw", "deploy", "status", "production", "--json"]]
    assert [(r.node, r.version, r.state) for r in reading.rows] == [
        ("node-1", "r7", "healthy"),
        ("node-2", "r6", "unhealthy"),
    ]
    assert reading.problems == problems
    assert reading.notes == [f"release log: last applied r7 {DIGEST} at 2026-11-12T09:11:00Z"]


def test_names_the_failure_when_lw_prints_no_json(tmp_path: Path) -> None:
    commands = FakeCommands(
        {"status": Done(2, "", "deploy.targets.missing: no deploy/targets.toml")}
    )

    reading = read_target("production", {"read": "lw", "lw": "npx lw"}, tmp_path, commands)

    assert commands.calls[0][:2] == ["npx", "lw"]
    assert reading.problems == [
        "npx lw deploy status production --json exited 2: "
        "deploy.targets.missing: no deploy/targets.toml"
    ]


def test_reads_roles_versions_and_digests_from_docker_ps(tmp_path: Path) -> None:
    out = "\n".join(
        [
            f"app-web-1f2e ghcr.io/o/app:1f2e ghcr.io/o/app@{DIGEST}",
            f"app-worker-1f2e ghcr.io/o/app:1f2e ghcr.io/o/app@{DIGEST}",
            "caddy caddy:2 -",
        ]
    )
    commands = FakeCommands(
        {
            "deploy@box-1": Done(0, out, ""),
            "deploy@box-2": Done(255, "", "ssh: connect to host box-2: timed out"),
        }
    )
    spec = {
        "read": "ssh-docker",
        "nodes": ["box-1", "box-2"],
        "user": "deploy",
        "roles": {"web": "app-web-", "worker": "app-worker-"},
    }

    reading = read_target("production", spec, tmp_path, commands)

    assert commands.calls[0][-2:] == ["deploy@box-1", DOCKER_PS]
    assert [(r.node, r.role, r.version, r.digest) for r in reading.rows] == [
        ("box-1", "web", "1f2e", DIGEST),
        ("box-1", "worker", "1f2e", DIGEST),
    ]
    assert reading.problems == ["box-2 did not answer: ssh: connect to host box-2: timed out"]


def test_names_a_role_that_a_node_does_not_run(tmp_path: Path) -> None:
    out = f"app-web-1 ghcr.io/o/app:v2 ghcr.io/o/app@{DIGEST}"
    commands = FakeCommands({"box-1": Done(0, out, "")})
    spec = {
        "read": "ssh-docker",
        "nodes": ["box-1"],
        "roles": {"web": "app-web-", "worker": "app-worker-"},
    }

    reading = read_target("production", spec, tmp_path, commands)

    assert reading.problems == ["box-1 runs no worker container"]


def test_reads_the_node_list_from_a_nodes_command(tmp_path: Path) -> None:
    out = f"app-web-v3 ghcr.io/o/app:v3 ghcr.io/o/app@{DIGEST}"
    commands = FakeCommands(
        {
            "tools/nodes.sh": Done(0, "box-1\n\nbox-2\n", ""),
            "box-1": Done(0, out, ""),
            "box-2": Done(0, out, ""),
        }
    )
    spec = {"read": "ssh-docker", "nodesCommand": "tools/nodes.sh", "roles": {"web": "app-web-"}}

    reading = read_target("production", spec, tmp_path, commands)

    assert commands.calls[0] == ["tools/nodes.sh"]
    assert [(r.node, r.role, r.version) for r in reading.rows] == [
        ("box-1", "web", "v3"),
        ("box-2", "web", "v3"),
    ]
    assert reading.problems == []


def test_names_no_problem_for_an_optional_role_that_a_node_lacks(tmp_path: Path) -> None:
    out = f"app-web-1 ghcr.io/o/app:v2 ghcr.io/o/app@{DIGEST}"
    commands = FakeCommands({"box-1": Done(0, out, "")})
    spec = {
        "read": "ssh-docker",
        "nodes": ["box-1"],
        "roles": {"web": "app-web-", "worker": "app-worker-"},
        "optionalRoles": ["worker"],
    }

    reading = read_target("production", spec, tmp_path, commands)

    assert [(r.node, r.role) for r in reading.rows] == [("box-1", "web")]
    assert reading.problems == []


def test_reads_a_version_field_from_an_http_body(tmp_path: Path) -> None:
    urls: list[str] = []

    def fetch(url: str) -> Fetched:
        urls.append(url)
        return Fetched(json.dumps({"build": {"number": 1234}}))

    spec = {"read": "http", "url": "https://app.example.com/version", "field": "build.number"}
    reading = read_target("production", spec, tmp_path, fetch=fetch)

    assert urls == ["https://app.example.com/version"]
    assert [(r.node, r.version) for r in reading.rows] == [
        ("https://app.example.com/version", "1234")
    ]


def test_names_a_missing_version_header_as_a_problem(tmp_path: Path) -> None:
    spec = {"read": "http", "url": "https://app.example.com/healthz", "header": "X-Release"}

    reading = read_target("api", spec, tmp_path, fetch=lambda url: Fetched("ok", {}))

    assert reading.problems == ["GET https://app.example.com/healthz has no header X-Release"]


def test_reads_json_lines_from_a_command(tmp_path: Path) -> None:
    out = "\n".join(
        [
            json.dumps({"node": "edge", "role": "workers", "version": "v9", "state": "live"}),
            json.dumps({"problem": "site beta runs v8"}),
            "not json",
        ]
    )
    commands = FakeCommands({"tools/serving.sh": Done(0, out, "")})

    reading = read_target(
        "edge", {"read": "command", "command": ["tools/serving.sh"]}, tmp_path, commands
    )

    assert [(r.node, r.version) for r in reading.rows] == [("edge", "v9")]
    assert reading.problems == ["site beta runs v8", "tools/serving.sh line 3 is not JSON"]


def test_rejects_an_unknown_read_method(tmp_path: Path) -> None:
    with pytest.raises(HarnessError, match=r"deploy\.targets\.production\.read must be one of"):
        read_target("production", {"read": "kubectl"}, tmp_path)
