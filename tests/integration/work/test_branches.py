"""`ah branch-audit` against the cases in `tests/fixtures/branch-audit/cases.json`.

A behaviour lives in the case file, not here. Each case builds its scratch repository from its
setup ops, runs `ah branch-audit --json` with the case's args, and checks `expect`. The ops and
the matching rules are the file's own `ops` and `notes`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

PACKAGE = Path(__file__).resolve().parents[3]
LAUNCHER = PACKAGE / "bin" / "ah"
FILE: dict[str, Any] = json.loads(
    (PACKAGE / "tests" / "fixtures" / "branch-audit" / "cases.json").read_text(encoding="utf-8")
)
KEYS = {"worktrees": "name", "branches": "ref", "remotes": "ref", "strays": "path", "plan": "id"}
DROPPED_ENV = ("AH_HANDED_OVER", "AH_ROOT", "NO_COLOR")
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in DROPPED_ENV},
    "TZ": "UTC",
    "NO_COLOR": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Audit Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "Audit Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.com",
}
START_TIME = (
    "import sys; t = open(f'/proc/{sys.argv[1]}/stat').read(); "
    "print(t[t.rindex(')') + 2:].split(' ')[19])"
)


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    return json.dumps(value)


def toml_text(config: dict[str, dict[str, object]]) -> str:
    out = []
    for section, values in config.items():
        out.append(f"[{section}]")
        out += [f"{key} = {toml_value(value)}" for key, value in values.items()]
        out.append("")
    return "\n".join(out)


class Scratch:
    """One case's world: its root, its repository, and every process it started."""

    def __init__(self, root: Path) -> None:
        self.root = os.path.realpath(root)
        self.repo = os.path.join(self.root, "repo")
        self.children: list[subprocess.Popen[bytes]] = []
        self.config: dict[str, dict[str, object]] = {
            section: dict(values) for section, values in FILE["config"].items()
        }
        self.sleep = os.path.join(self.root, "bin", "sleep")

    def env(self, at: object = None) -> dict[str, str]:
        stamp = f"{int(self.time(at))} +0000"
        return {**ENV, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}

    def time(self, at: object = None) -> float:
        hours = at if isinstance(at, int | float) else -240
        return time.time() + hours * 3600

    def git(self, args: list[str], cwd: str | None = None, at: object = None) -> str:
        done = subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            env=self.env(at),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"git {' '.join(args)} failed in {cwd}: {done.stderr}"
        return done.stdout.strip()

    def worktree(self, name: str) -> str:
        return os.path.join(self.repo, ".claude", "worktrees", name)

    def checkout(self, name: object) -> str:
        return self.worktree(name) if isinstance(name, str) else self.repo

    def write(self, folder: str, path: str, text: str, at: object = None) -> str:
        full = os.path.join(folder, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        Path(full).write_text(text, encoding="utf-8")
        stamp = self.time(at)
        os.utime(full, (stamp, stamp))
        return full

    def write_config(self) -> None:
        self.write(self.repo, ".agents/harness.toml", toml_text(self.config))

    def holder(self, branch: str) -> str | None:
        path = None
        for line in self.git(["worktree", "list", "--porcelain"]).split("\n"):
            if line.startswith("worktree "):
                path = line[9:]
            if line == f"branch refs/heads/{branch}":
                return path
        return None

    def sleeper(self, cwd: str, program: str | None = None) -> int:
        """A process that waits until teardown stops it, started from a copy inside `$ROOT`."""
        if not os.path.exists(self.sleep):
            os.makedirs(os.path.dirname(self.sleep), exist_ok=True)
            shutil.copy2(shutil.which("sleep") or "/bin/sleep", self.sleep)
        child = subprocess.Popen(
            [program or self.sleep, "600"],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.children.append(child)
        return child.pid

    def cleanup(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                child.wait()
        for top, dirs, _ in os.walk(self.root):
            for name in dirs:
                path = os.path.join(top, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o755)


def transcript_name(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", lambda m: "-" * (2 if ord(m[0]) > 0xFFFF else 1), path)


def start_time(pid: int) -> int:
    done = subprocess.run(
        [sys.executable, "-c", START_TIME, str(pid)], capture_output=True, text=True, check=True
    )
    return int(done.stdout)


def merge_config(s: Scratch, values: dict[str, dict[str, object]]) -> None:
    for section, keys in values.items():
        s.config.setdefault(section, {}).update(keys)


def run_step(s: Scratch, step: dict[str, Any]) -> None:
    op, at = step["op"], step.get("at")
    handler = STEPS.get(op)
    assert handler is not None, f"unknown op {op}: the case file's `ops` map lists every op"
    handler(s, step, at)


def op_use(s: Scratch, step: dict[str, Any], at: object) -> None:
    for inner in FILE["fixtures"][step["fixture"]]:
        run_step(s, inner)


def op_init(s: Scratch, step: dict[str, Any], at: object) -> None:
    os.mkdir(s.repo)
    s.git(["init", "-q", "-b", "main", "."])
    files = step.get("files") or {"src/a.txt": "base\n"}
    for path, text in files.items():
        s.write(s.repo, path, text, at)
    s.git(["add", *files])
    s.git(["commit", "-qm", "base"], at=at)
    s.write_config()


def op_config(s: Scratch, step: dict[str, Any], at: object) -> None:
    if step.get("remove"):
        os.remove(os.path.join(s.repo, ".agents", "harness.toml"))
        return
    merge_config(s, step["values"])
    s.write_config()


def op_commit(s: Scratch, step: dict[str, Any], at: object) -> None:
    branch = step["branch"]
    folder = s.holder(branch)
    if folder is None:
        s.git(["checkout", "-q", branch], at=at)
        folder = s.repo
    files: dict[str, str | None] = dict(step.get("files") or {})
    if "many" in step:
        many = step["many"]
        width = len(str(many["count"] - 1))
        files |= {
            f"{many['prefix']}{str(i).zfill(width)}": many["text"] for i in range(many["count"])
        }
    added = []
    for path, text in files.items():
        if text is None:
            s.git(["rm", "-q", path], folder, at)
        else:
            s.write(folder, path, text, at)
            added.append(path)
    if added:
        s.git(["add", "--", *added], folder, at)
    s.git(["commit", "-qm", step.get("message", f"commit on {branch}")], folder, at)
    if folder == s.repo and branch != "main":
        s.git(["checkout", "-q", "main"], at=at)


def op_land(s: Scratch, step: dict[str, Any], at: object) -> None:
    branch, how = step["branch"], step["how"]
    if how == "merge":
        s.git(["merge", "-q", "--no-ff", "--no-edit", branch], at=at)
    elif how == "rebase":
        s.git(["cherry-pick", f"{s.git(['merge-base', 'main', branch])}..{branch}"], at=at)
    else:
        assert how == "cherry-pick", f"land: unknown how {how}"
        s.git(["cherry-pick", branch], at=at)


def op_worktree(s: Scratch, step: dict[str, Any], at: object) -> None:
    path = s.worktree(step["name"])
    if "branch" in step:
        s.git(["worktree", "add", "-q", path, step["branch"]], at=at)
    elif "new_branch" in step:
        from_ref = step.get("from", "main")
        s.git(["worktree", "add", "-q", "-b", step["new_branch"], path, from_ref], at=at)
    else:
        s.git(["worktree", "add", "-q", "--detach", path, step["detach"]], at=at)


def op_write(s: Scratch, step: dict[str, Any], at: object) -> None:
    text = step["text"] * step.get("repeat", 1)
    full = s.write(s.checkout(step.get("in")), step["path"], text, at)
    if "mode" in step:
        os.chmod(full, int(step["mode"], 8))


def op_folder(s: Scratch, step: dict[str, Any], at: object) -> None:
    os.makedirs(s.worktree(step["name"]), exist_ok=True)
    for path, text in step["files"].items():
        s.write(s.worktree(step["name"]), path, text, at)


def op_lock(s: Scratch, step: dict[str, Any], at: object) -> None:
    owner = step["owner"]
    if owner == "dead":
        done = subprocess.Popen([sys.executable, "-c", "pass"])
        done.wait()
        pid, start = done.pid, 1
    else:
        pid = s.sleeper(s.root)
        start = start_time(pid) + (1 if owner == "reused" else 0)
    name = step["worktree"]
    reason = f"claude session {name} (pid {pid} start {start})"
    s.git(["worktree", "lock", "--reason", reason, s.worktree(name)])


def op_hold(s: Scratch, step: dict[str, Any], at: object) -> None:
    if "exe" not in step:
        s.sleeper(s.worktree(step["worktree"]))
        return
    program = os.path.join(s.worktree(step["worktree"]), step["exe"])
    os.makedirs(os.path.dirname(program), exist_ok=True)
    shutil.copy2(shutil.which("sleep") or "/bin/sleep", program)
    s.sleeper(s.root, program)


def op_transcript(s: Scratch, step: dict[str, Any], at: object) -> None:
    folder = transcript_name(s.worktree(step["worktree"]))
    s.write(os.path.join(s.root, "claude-projects", folder), "session.jsonl", "{}\n", at)


def op_remote(s: Scratch, step: dict[str, Any], at: object) -> None:
    name = step.get("name", "origin")
    bare = os.path.join(s.root, f"{name}.git")
    s.git(["init", "-q", "--bare", "-b", "main", bare], s.root)
    s.git(["remote", "add", name, bare])
    s.git(["push", "-q", name, *step["push"]], at=at)
    s.git(["fetch", "-q", name], at=at)


def remove_tree(folder: str) -> None:
    """`shutil.rmtree` by full paths: the read guard takes a name relative to a folder
    descriptor for a path in the test's own folder."""
    for top, dirs, files in os.walk(folder, topdown=False):
        for name in files:
            os.remove(os.path.join(top, name))
        for name in dirs:
            path = os.path.join(top, name)
            os.remove(path) if os.path.islink(path) else os.rmdir(path)
    os.rmdir(folder)


def with_root(value: Any, root: str) -> Any:
    if isinstance(value, str):
        return value.replace("$ROOT", root)
    if isinstance(value, list):
        return [with_root(x, root) for x in value]
    if isinstance(value, dict):
        return {k: with_root(x, root) for k, x in value.items()}
    return value


STEPS = {
    "use": op_use,
    "init": op_init,
    "config": op_config,
    "branch": lambda s, st, at: s.git(["branch", st["name"], st.get("from", "main")], at=at),
    "commit": op_commit,
    "land": op_land,
    "worktree": op_worktree,
    "detach": lambda s, st, at: s.git(
        ["checkout", "-q", "--detach", st.get("rev", "HEAD")], s.worktree(st["worktree"]), at
    ),
    "write": op_write,
    "folder": op_folder,
    "remove_folder": lambda s, st, at: remove_tree(s.worktree(st["worktree"])),
    "lock": op_lock,
    "hold": op_hold,
    "transcript": op_transcript,
    "remote": op_remote,
    "git": lambda s, st, at: s.git(with_root(st["args"], s.root), s.checkout(st.get("in")), at),
    "link": lambda s, st, at: os.symlink(
        with_root(st["target"], s.root), os.path.join(s.checkout(st.get("in")), st["path"])
    ),
    "fifo": lambda s, st, at: os.mkfifo(os.path.join(s.root, st["path"])),
    "chmod": lambda s, st, at: os.chmod(
        os.path.join(s.checkout(st.get("in")), st["path"]), int(st["mode"], 8)
    ),
}


def js_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    return "string" if isinstance(value, str) else "object"


def match(actual: object, want: object, where: str, root: str) -> None:
    """A partial match: null is absent or null, a `$` object is a matcher, `files` is rows."""
    if want is None:
        assert actual is None, f"{where}: {actual!r} is not null"
    elif isinstance(want, dict) and want and all(k.startswith("$") for k in want):
        for key, bound in want.items():
            if key == "$gte":
                assert isinstance(actual, int | float), f"{where}: {actual!r}"
                assert actual >= bound, f"{where}: {actual!r} < {bound}"
            elif key == "$lte":
                assert isinstance(actual, int | float), f"{where}: {actual!r}"
                assert actual <= bound, f"{where}: {actual!r} > {bound}"
            else:
                assert key == "$type", f"{where}: unknown matcher {key}"
                assert js_type(actual) == bound, f"{where}: {actual!r} is not a {bound}"
    elif isinstance(want, dict):
        assert isinstance(actual, dict), f"{where}: {actual!r} is not an object"
        for key, value in want.items():
            got = actual.get(key)
            if key == "files" and isinstance(got, list):
                rows(got, value, "path", f"{where}.files", root)
            else:
                match(got, value, f"{where}.{key}", root)
    else:
        expected = with_root(want, root)
        same_kind = isinstance(actual, bool) == isinstance(expected, bool)
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"
        assert same_kind, f"{where}: {actual!r} is not a {type(expected).__name__}"


def row_name(row: dict[str, Any], key: str, section: str) -> str:
    return os.path.basename(row[key]) if section == "strays" else str(row[key])


def rows(
    found: list[dict[str, Any]], want: dict[str, Any], key: str, where: str, root: str
) -> None:
    for name, values in want.items():
        row = next((r for r in found if row_name(r, key, where) == name), None)
        if values is None:
            assert row is None, f"{where}[{name}] should be absent: {row}"
        else:
            names = [row_name(r, key, where) for r in found]
            assert row is not None, f"{where}[{name}] is missing from {names}"
            match(row, values, f"{where}[{name}]", root)


def has_ref(s: Scratch, spec: str) -> bool:
    folder, ref = spec.split(":", 1)
    done = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=os.path.join(s.root, folder),
        env=ENV,
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


def check_sections(s: Scratch, expect: dict[str, Any], report: dict[str, Any]) -> None:
    for section, want in expect.items():
        if section in ("exit", "after", "stdout"):
            continue
        if section == "order":
            for sec, names in want.items():
                got = [row_name(r, KEYS[sec], sec) for r in report[sec]]
                assert [n for n in got if n in names] == names, f"order.{sec}: {got}"
        elif section == "names":
            for sec, names in want.items():
                got = sorted(row_name(r, KEYS[sec], sec) for r in report[sec])
                assert got == sorted(names), f"names.{sec}: {got}"
        elif section in KEYS:
            rows(report[section], want, KEYS[section], section, s.root)
        else:
            match(report.get(section), want, section, s.root)


def check_after(s: Scratch, after: dict[str, Any]) -> None:
    paths, refs = after.get("paths", {}), after.get("refs", {})
    for path in paths.get("exist", []):
        assert os.path.lexists(os.path.join(s.root, path)), f"{path} should exist"
    for path in paths.get("gone", []):
        assert not os.path.lexists(os.path.join(s.root, path)), f"{path} should be gone"
    for ref in refs.get("exist", []):
        assert has_ref(s, ref), f"{ref} should exist"
    for ref in refs.get("gone", []):
        assert not has_ref(s, ref), f"{ref} should be gone"
    if after.get("processes_alive") is True:
        assert all(c.poll() is None for c in s.children), "a process of the setup was stopped"


# Runs `branches.main` with a `before_apply` function that runs the shell command of the case, so
# a case can stand in for a peer that writes while the plan waits.
PEER_RUNNER = """import subprocess, sys
sys.path.insert(0, sys.argv[1])
from agent_harness.work import branches
step = sys.argv[2]
def peer(repo: str) -> None:
    subprocess.run(["sh", "-c", step], cwd=repo, check=True)
sys.exit(branches.main(sys.argv[3:], before_apply=peer))
"""


def command(s: Scratch, case: dict[str, Any]) -> tuple[list[str], str]:
    projects = os.path.join(s.root, "claude-projects")
    shape = [] if case.get("text") else ["--json"]
    argv = [sys.executable, str(LAUNCHER), "branch-audit", *shape, "--projects-root", projects]
    if "before_apply" in case:
        runner = os.path.join(s.root, "peer-runner.py")
        Path(runner).write_text(PEER_RUNNER, encoding="utf-8")
        step = with_root({"step": case["before_apply"]}, s.root)["step"]
        argv = [sys.executable, runner, str(PACKAGE / "src"), step, *argv[3:]]
    argv += case["args"]
    cwd = s.checkout(case.get("cwd"))
    if "shell_in" in case:
        script = os.path.join(s.root, "run-from.sh")
        Path(script).write_text('(cd "$1" && shift && "$@"); exit $?\n', encoding="utf-8")
        argv = ["bash", script, cwd, *argv]
        cwd = s.worktree(case["shell_in"])
    return argv, cwd


@pytest.fixture
def scratch(tmp_path: Path) -> Iterator[Scratch]:
    world = Scratch(tmp_path)
    yield world
    world.cleanup()


def test_names_every_op_the_case_file_uses() -> None:
    steps = [st for f in FILE["fixtures"].values() for st in f]
    steps += [st for c in FILE["cases"] for st in c["setup"]]

    assert sorted({st["op"] for st in steps} - set(FILE["ops"])) == []
    assert sorted(set(FILE["ops"]) - set(STEPS)) == []


@pytest.mark.parametrize("case", FILE["cases"], ids=[c["name"] for c in FILE["cases"]])
def test_holds_each_branch_audit_case(scratch: Scratch, case: dict[str, Any]) -> None:
    for step in case["setup"]:
        run_step(scratch, step)
    argv, cwd = command(scratch, case)
    env = {**scratch.env(), **with_root(case.get("env", {}), scratch.root)}

    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)

    assert done.returncode == case["expect"].get("exit", 0), done.stderr
    for line in case["expect"].get("stdout", []):
        assert line in done.stdout, f"stdout lacks {line!r}:\n{done.stdout}"
    text = done.stdout.strip() and not case.get("text")
    check_sections(scratch, case["expect"], json.loads(done.stdout) if text else {})
    check_after(scratch, case["expect"].get("after") or {})
