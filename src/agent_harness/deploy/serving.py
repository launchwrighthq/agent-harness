"""The serving half: what each target of `[deploy] targets` runs now.

Each target names a read method in `read`:

- `lw`: `lw deploy status <target> --json`, the containers of each node and the release log.
- `ssh-docker`: `docker ps` over SSH on each host of `nodes` or of `nodesCommand`.
- `http`: a version URL. `field` picks a dotted key of a JSON body, `header` a response header.
- `command`: a command that prints one JSON object per line.

Every method only reads. A node that does not answer gives a problem line with the reason.
docs/deploy.md lists every key.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness import HarnessError

READ_METHODS = ("lw", "ssh-docker", "http", "command")

# Seconds that one node or URL may take to answer.
READ_TIMEOUT = 20

# A CDN such as Cloudflare refuses the default `Python-urllib/<version>` agent with HTTP 403.
USER_AGENT = "agent-harness-deploy-status"

# Prints `<name> <image> <repo digest>` per running container. Only `docker ps` and
# `docker image inspect` run, so the script changes nothing on the node.
DOCKER_PS = (
    "docker ps --format '{{.Names}} {{.Image}}' | while read -r n i; do "
    'printf \'%s %s %s\\n\' "$n" "$i" '
    '"$(docker image inspect --format \'{{index .RepoDigests 0}}\' "$i" 2>/dev/null || echo -)"; '
    "done"
)


@dataclass(frozen=True)
class Serve:
    """One serving unit: a node and role, or a URL, with the version and digest it runs."""

    node: str
    role: str
    version: str
    digest: str
    state: str
    detail: str = ""


@dataclass
class Reading:
    """What one target serves, and each problem found while reading it."""

    target: str
    method: str
    env: str
    rows: list[Serve] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Done:
    code: int
    out: str
    err: str


# Runs one command in a folder with a timeout. A test passes its own.
Execute = Callable[[list[str], Path], Done]


def execute(argv: list[str], cwd: Path) -> Done:
    """Runs `argv` with its program resolved on PATH, or against `cwd` for a path with a folder."""
    program = Path(argv[0])
    if program.is_absolute():
        binary: str | None = argv[0]
    elif len(program.parts) > 1:
        binary = str(cwd / program)
    else:
        binary = shutil.which(argv[0])
    if binary is None:
        raise HarnessError(f"{argv[0]} is not on PATH")
    try:
        done = subprocess.run(
            [binary, *argv[1:]],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=READ_TIMEOUT,
        )
    except subprocess.TimeoutExpired as error:
        raise HarnessError(f"{argv[0]} gave no answer in {READ_TIMEOUT} s") from error
    except OSError as error:
        raise HarnessError(f"{argv[0]} could not start: {error}") from error
    return Done(done.returncode, done.stdout, done.stderr)


@dataclass(frozen=True)
class Fetched:
    """The answer of one GET. `headers` has lower-case names."""

    body: str
    headers: Mapping[str, str] = field(default_factory=dict)


# Runs one GET. A test passes its own.
Fetch = Callable[[str], Fetched]


def fetch_url(url: str) -> Fetched:
    """The body and the headers of a GET on `url`."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT) as response:
            body: bytes = response.read()
            headers = {name.lower(): value for name, value in response.headers.items()}
    except (urllib.error.URLError, TimeoutError) as error:
        raise HarnessError(f"GET {url} failed: {error}") from error
    return Fetched(body.decode("utf-8", errors="replace"), headers)


def read_targets(
    section: Mapping[str, Any],
    root: Path,
    run: Execute = execute,
    fetch: Fetch = fetch_url,
) -> list[Reading]:
    """Every target of a `[deploy]` section, in the order of the config."""
    targets = section.get("targets") or {}
    if not isinstance(targets, Mapping):
        raise HarnessError("deploy.targets must be a table")
    out: list[Reading] = []
    for name, spec in targets.items():
        if not isinstance(spec, Mapping):
            raise HarnessError(f"deploy.targets.{name} must be a table")
        out.append(read_target(str(name), spec, root, run, fetch))
    return out


def read_target(
    name: str,
    spec: Mapping[str, Any],
    root: Path,
    run: Execute = execute,
    fetch: Fetch = fetch_url,
) -> Reading:
    key = f"deploy.targets.{name}"
    method = spec.get("read")
    if method not in READ_METHODS:
        raise HarnessError(f"{key}.read must be one of {', '.join(READ_METHODS)}, not {method!r}")
    reading = Reading(name, str(method), str(spec.get("env") or name))
    if method == "lw":
        _read_lw(reading, spec, root, run, key)
    elif method == "ssh-docker":
        _read_ssh_docker(reading, spec, root, run, key)
    elif method == "http":
        _read_http(reading, spec, fetch, key)
    else:
        _read_command(reading, spec, root, run, key)
    return reading


def _argv(value: object, key: str, default: Sequence[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return list(value)
    raise HarnessError(f"{key} must be a command string or a list of strings")


def _read_lw(reading: Reading, spec: Mapping[str, Any], root: Path, run: Execute, key: str) -> None:
    """`lw deploy status <target> --json`. It exits 1 with JSON when it finds a problem."""
    lw = _argv(spec.get("lw"), f"{key}.lw", ["lw"])
    target = str(spec.get("target") or reading.target)
    argv = [*lw, "deploy", "status", target, "--json"]
    try:
        done = run(argv, root)
    except HarnessError as error:
        reading.problems.append(f"{' '.join(argv)} did not run: {error}")
        return
    try:
        body = json.loads(done.out)
    except json.JSONDecodeError:
        last = (done.err.strip().splitlines() or ["no output"])[-1]
        reading.problems.append(f"{' '.join(argv)} exited {done.code}: {last}")
        return
    if not isinstance(body, Mapping):
        reading.problems.append(f"{' '.join(argv)} printed JSON that is not an object")
        return
    for node in body.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        services = [
            str(c.get("service"))
            for c in node.get("containers") or []
            if isinstance(c, Mapping) and c.get("service")
        ]
        reading.rows.append(
            Serve(
                node=str(node.get("node") or "-"),
                role=", ".join(services) or "-",
                version=str(node.get("release") or "-"),
                digest=str(node.get("digest") or "-"),
                state="healthy" if node.get("healthy") else "unhealthy",
                detail=str(node.get("detail") or ""),
            )
        )
    release = body.get("last")
    if isinstance(release, Mapping):
        reading.notes.append(
            f"release log: last applied {release.get('id', '-')} {release.get('digest', '-')} "
            f"at {release.get('at', '-')}"
        )
    else:
        reading.notes.append("release log: no applied release")
    for problem in body.get("problems") or []:
        reading.problems.append(str(problem))
    if not reading.rows and not reading.problems:
        reading.problems.append("lw deploy status reports no node")


def _ssh_nodes(
    reading: Reading, spec: Mapping[str, Any], root: Path, run: Execute, key: str
) -> list[str]:
    """The hosts of `nodes`, or the lines that `nodesCommand` prints."""
    if spec.get("nodesCommand") is None:
        nodes = spec.get("nodes")
        if not isinstance(nodes, list) or not all(isinstance(n, str) for n in nodes) or not nodes:
            raise HarnessError(
                f"{key}.nodes must be a list of host names, or set {key}.nodesCommand"
            )
        return list(nodes)
    argv = _argv(spec.get("nodesCommand"), f"{key}.nodesCommand", [])
    try:
        done = run(argv, root)
    except HarnessError as error:
        reading.problems.append(f"{' '.join(argv)} did not run: {error}")
        return []
    hosts = [line.strip() for line in done.out.splitlines() if line.strip()]
    if done.code != 0 or not hosts:
        last = (done.err.strip().splitlines() or ["no host"])[-1]
        reading.problems.append(f"{' '.join(argv)} exited {done.code}: {last}")
    return hosts if done.code == 0 else []


def _read_ssh_docker(
    reading: Reading, spec: Mapping[str, Any], root: Path, run: Execute, key: str
) -> None:
    roles = spec.get("roles")
    if not isinstance(roles, Mapping) or not roles:
        raise HarnessError(f"{key}.roles must be a table of role to container name prefix")
    optional = spec.get("optionalRoles") or []
    if not isinstance(optional, list) or not set(optional) <= set(roles):
        raise HarnessError(f"{key}.optionalRoles must be a list of roles of {key}.roles")
    user = spec.get("user")
    ssh = _argv(spec.get("ssh"), f"{key}.ssh", ["ssh"])
    for node in _ssh_nodes(reading, spec, root, run, key):
        host = f"{user}@{node}" if isinstance(user, str) and user else node
        argv = [*ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, DOCKER_PS]
        try:
            done = run(argv, root)
        except HarnessError as error:
            reading.problems.append(f"{node} did not answer: {error}")
            continue
        if done.code != 0:
            last = (done.err.strip().splitlines() or [f"ssh exited {done.code}"])[-1]
            reading.problems.append(f"{node} did not answer: {last}")
            continue
        found = _docker_rows(reading, node, done.out, roles)
        for role in roles:
            if role not in found and role not in optional:
                reading.problems.append(f"{node} runs no {role} container")
    for role in roles:
        versions = sorted({r.version for r in reading.rows if r.role == role})
        if len(versions) > 1:
            reading.problems.append(f"the nodes run {len(versions)} {role} versions: {versions}")


def _docker_rows(reading: Reading, node: str, out: str, roles: Mapping[str, Any]) -> set[str]:
    """Adds a row per container of a known role, and gives the roles found."""
    found: set[str] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, image = parts[0], parts[1]
        repo_digest = parts[2] if len(parts) > 2 else "-"
        role = next((r for r, prefix in roles.items() if name.startswith(str(prefix))), None)
        if role is None:
            continue
        found.add(role)
        tag = image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else "-"
        digest = repo_digest.split("@", 1)[1] if "@" in repo_digest else "-"
        reading.rows.append(Serve(node, role, tag, digest, "running", name))
    return found


def _read_http(reading: Reading, spec: Mapping[str, Any], fetch: Fetch, key: str) -> None:
    url = spec.get("url")
    if not isinstance(url, str) or not url:
        raise HarnessError(f"{key}.url must be a URL")
    header = spec.get("header")
    path = spec.get("field")
    if header is not None and (not isinstance(header, str) or not header or path is not None):
        raise HarnessError(f"{key}.header must be a header name, and it excludes {key}.field")
    try:
        answer = fetch(url)
    except HarnessError as error:
        reading.problems.append(str(error))
        return
    body = answer.body
    if isinstance(header, str):
        found = answer.headers.get(header.lower())
        if not found:
            reading.problems.append(f"GET {url} has no header {header}")
            return
        reading.rows.append(Serve(url, "-", found.strip(), "-", "answered"))
        return
    version = body.strip()
    if isinstance(path, str) and path:
        try:
            node: object = json.loads(body)
        except json.JSONDecodeError:
            reading.problems.append(f"GET {url} did not answer JSON, so {key}.field cannot apply")
            return
        for part in path.split("."):
            if not isinstance(node, Mapping) or part not in node:
                reading.problems.append(f"GET {url} has no key {path}")
                return
            node = node[part]
        version = str(node)
    reading.rows.append(Serve(url, "-", version or "-", "-", "answered"))


def _read_command(
    reading: Reading, spec: Mapping[str, Any], root: Path, run: Execute, key: str
) -> None:
    """A command that prints JSON lines: `node`, `role`, `version`, `digest`, `state`, `problem`."""
    argv = _argv(spec.get("command"), f"{key}.command", [])
    if not argv:
        raise HarnessError(f"{key}.command must name a command")
    try:
        done = run(argv, root)
    except HarnessError as error:
        reading.problems.append(f"{' '.join(argv)} did not run: {error}")
        return
    if done.code != 0:
        last = (done.err.strip().splitlines() or ["no error text"])[-1]
        reading.problems.append(f"{' '.join(argv)} exited {done.code}: {last}")
    for number, line in enumerate(done.out.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            reading.problems.append(f"{' '.join(argv)} line {number} is not JSON")
            continue
        if not isinstance(row, Mapping):
            reading.problems.append(f"{' '.join(argv)} line {number} is not a JSON object")
            continue
        if row.get("problem"):
            reading.problems.append(str(row["problem"]))
        if row.get("node") or row.get("version"):
            reading.rows.append(
                Serve(
                    node=str(row.get("node") or "-"),
                    role=str(row.get("role") or "-"),
                    version=str(row.get("version") or "-"),
                    digest=str(row.get("digest") or "-"),
                    state=str(row.get("state") or "-"),
                    detail=str(row.get("detail") or ""),
                )
            )
