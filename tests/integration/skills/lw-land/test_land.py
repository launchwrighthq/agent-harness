"""land.sh reads the base branch and the gate from the config and renders the landed spec."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[4]
LAND = PACKAGE / "skills" / "lw-land" / "land.sh"
GITHOOKS = PACKAGE / "githooks"
# A dispatcher copied out of its folder finds no harness hook beside it, so it stands for one
# that refuses.
DISPATCHER = "#!/bin/sh\necho 'the dispatcher ran alone' >&2\nexit 1\n"
SPEC = "docs/specs/2026-01-01-a.md"
NOREPLY_DOMAIN = "users.noreply.github.com"
IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(root: Path, *args: str, check: bool = True, email: str = "t@example.com") -> str:
    identity = {**IDENTITY, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email}
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **identity},
        check=check,
    )
    return done.stdout.strip()


def make_repo(
    tmp_path: Path, gate: str, hooks: Path = GITHOOKS, extra: str = ""
) -> tuple[Path, Path]:
    """A repository on `trunk` and a worktree on `feat` that adds one spec.

    `extra` holds more TOML lines of the `[harness]` table.
    """
    repo = Path(os.path.realpath(tmp_path)) / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=trunk")
    (repo / ".agents").mkdir()
    (repo / ".agents" / "harness.toml").write_text(
        f'[harness]\nbaseBranch = "trunk"\ngateCommand = "{gate}"\n{extra}', encoding="utf-8"
    )
    (repo / ".gitignore").write_text("dist/\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "init")
    git(repo, "config", "receive.denyCurrentBranch", "updateInstead")
    git(repo, "config", "core.hooksPath", str(hooks))
    tree = repo / ".claude" / "worktrees" / "wt"
    git(repo, "worktree", "add", "--quiet", "-b", "feat", str(tree))
    (tree / "docs" / "specs").mkdir(parents=True)
    (tree / SPEC).write_text(
        "# A\n\nDate: 2026-01-01\nStatus: draft\n\n## 1. Scope\n\nText.\n", encoding="utf-8"
    )
    git(tree, "add", SPEC)
    git(tree, "commit", "--quiet", "-m", "spec")
    return repo, tree


def land(tree: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LAND)],
        cwd=tree,
        capture_output=True,
        text=True,
        env={**os.environ, **IDENTITY, **env},
        check=False,
    )


def commit(tree: Path, rel: str, text: str) -> None:
    path = tree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(tree, "add", "-f", rel)
    git(tree, "commit", "--quiet", "-m", rel)


def test_lands_the_branch_on_the_configured_base_and_renders_the_spec(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "true")

    done = land(tree)

    assert done.returncode == 0, done.stderr
    assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")
    assert (repo / SPEC).is_file()
    page = repo / "docs" / "specs" / "dist" / "2026-01-01-a.html"
    assert f"land: page: file://{page}\n" in done.stdout
    assert page.is_file()
    assert "land: gate: true, logging to " in done.stdout


def test_refuses_to_land_without_a_configured_gate(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "")

    done = land(tree)

    assert done.returncode == 5
    assert "harness.gateCommand is empty" in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


def test_refuses_a_relative_hooks_path_and_names_the_absolute_one(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "true")
    shutil.copytree(GITHOOKS, repo / ".githooks")
    git(repo, "config", "core.hooksPath", ".githooks")

    done = land(tree)

    assert done.returncode == 5, done.stdout + done.stderr
    assert f"Run: git -C {repo} config core.hooksPath {repo}/.githooks\n" in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


def test_runs_the_hook_from_head_on_the_landing_that_brings_it(tmp_path: Path) -> None:
    checkout_hooks = Path(os.path.realpath(tmp_path)) / "repo" / ".githooks"
    repo, tree = make_repo(tmp_path, "true", hooks=checkout_hooks)
    commit(tree, ".githooks/push-to-checkout", (GITHOOKS / "push-to-checkout").read_text())
    (tree / ".githooks" / "push-to-checkout").chmod(0o755)
    git(tree, "commit", "--quiet", "--amend", "-a", "--no-edit")
    # A peer's file outside the landing: git's own updateInstead refuses over it, the hook does not.
    (repo / "notes.txt").write_text("peer\n", encoding="utf-8")
    git(repo, "add", "notes.txt")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")
    assert (repo / ".githooks" / "push-to-checkout").is_file()
    assert git(repo, "diff", "--cached", "--name-only") == "notes.txt"


def test_runs_the_harness_hook_from_head_when_head_holds_the_dispatcher(tmp_path: Path) -> None:
    checkout_hooks = Path(os.path.realpath(tmp_path)) / "repo" / ".githooks"
    repo, tree = make_repo(tmp_path, "true", hooks=checkout_hooks)
    commit(tree, ".githooks/push-to-checkout", DISPATCHER)
    commit(tree, ".githooks/harness/push-to-checkout", (GITHOOKS / "push-to-checkout").read_text())
    (tree / ".githooks" / "push-to-checkout").chmod(0o755)
    (tree / ".githooks" / "harness" / "push-to-checkout").chmod(0o755)
    git(tree, "commit", "--quiet", "--amend", "-a", "--no-edit")
    (repo / "notes.txt").write_text("peer\n", encoding="utf-8")
    git(repo, "add", "notes.txt")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")
    assert git(repo, "diff", "--cached", "--name-only") == "notes.txt"


def test_clears_an_identical_untracked_leftover_without_waiting(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "true")
    (repo / SPEC).parent.mkdir(parents=True)
    (repo / SPEC).write_bytes((tree / SPEC).read_bytes())

    done = land(tree, LAND_PEER_WAIT="0", LAND_PEER_TRIES="3")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "waiting" not in done.stdout
    assert f"land: leftover: removed {SPEC} from the checkout" in done.stdout


def test_stops_at_once_on_an_untracked_file_that_differs(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "true")
    (repo / SPEC).parent.mkdir(parents=True)
    (repo / SPEC).write_text("peer\n", encoding="utf-8")

    done = land(tree, LAND_PEER_WAIT="0", LAND_PEER_TRIES="3")

    assert done.returncode == 4, done.stdout + done.stderr
    assert "waiting" not in done.stdout
    assert (repo / SPEC).read_text(encoding="utf-8") == "peer\n"


def test_keeps_an_ignored_file_that_the_landing_would_overwrite(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "true")
    commit(tree, "dist/app.js", "branch\n")
    (repo / "dist").mkdir()
    (repo / "dist" / "app.js").write_text("peer\n", encoding="utf-8")

    done = land(tree, LAND_PEER_WAIT="0")

    assert done.returncode == 4, done.stdout + done.stderr
    assert "land: leftover: dist/app.js in the checkout DIFFERS" in done.stdout + done.stderr
    assert (repo / "dist" / "app.js").read_text(encoding="utf-8") == "peer\n"


GREEN = '[harness.land]\ngreenToken = "verify: green"\n'


def test_refuses_a_gate_that_exits_0_without_the_green_token(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "echo verify: done", extra=GREEN)

    done = land(tree)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "no line of the gate log is verify: green" in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


def test_lands_when_the_gate_log_holds_the_green_token(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "echo verify: green", extra=GREEN)

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")


def failures(step: str, attributable: str, files: str) -> str:
    """A shell line that prints a `verify: failures` record of one failed step."""
    return (
        'printf \'verify: failures {"%s": {"code": 1, "attributable": %s, '
        f"\"failedFiles\": [%s]}}}}\\n' {step} {attributable} '{files}'\n"
    )


PROBE = '[harness.land]\nprobeCommand = "sh probe.sh"\n'
OLD_FAILURE = failures("test", "true", '"t/old.sh"')
# The probe prints a record that names the files it was given, and exits 1.
PROBE_RED = (
    'files=$(printf \'"%s",\' "$@")\n'
    'printf \'verify: failures {"test": {"code": 1, "attributable": true, '
    '"failedFiles": [%s]}}\\n\' "${files%,}"\n'
    "exit 1\n"
)


def make_red_repo(tmp_path: Path, gate_body: str, probe_body: str) -> tuple[Path, Path]:
    repo, tree = make_repo(tmp_path, "sh gate.sh", extra=PROBE)
    commit(repo, "gate.sh", gate_body + "exit 1\n")
    commit(repo, "probe.sh", probe_body)
    return repo, tree


def test_lands_when_every_failure_is_red_on_the_merged_base_too(tmp_path: Path) -> None:
    repo, tree = make_red_repo(tmp_path, OLD_FAILURE, PROBE_RED)

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "land:   t/old.sh (red on the merged base too)\n" in done.stdout
    assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")


def test_stops_when_a_failure_passes_on_the_merged_base(tmp_path: Path) -> None:
    repo, tree = make_red_repo(tmp_path, OLD_FAILURE, "exit 0\n")

    done = land(tree)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "land:   t/old.sh\n" in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


def test_stops_on_a_failed_step_that_names_no_file(tmp_path: Path) -> None:
    repo, tree = make_red_repo(tmp_path, failures("typecheck", "false", ""), PROBE_RED)

    done = land(tree)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "land:   typecheck\n" in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


def test_amends_the_after_merge_output_into_the_merge_commit(tmp_path: Path) -> None:
    extra = '[harness.land]\nafterMerge = "ls docs/specs > index.txt"\n'
    repo, tree = make_repo(tmp_path, "true", extra=extra)
    commit(repo, "index.txt", "\n")
    branch_tip = git(tree, "rev-parse", "HEAD")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert git(repo, "show", "trunk:index.txt") == Path(SPEC).name
    assert git(repo, "rev-parse", "trunk^1") == branch_tip
    assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""


INDEX = "a\nb\nc\nd\ne\nf\ng\nh\ni\n"
# The gate moves the base once, on a line of index.txt far from the line the branch changed.
MOVE_BASE = (
    'if [ ! -f "$MARK" ]; then\n'
    '  touch "$MARK"\n'
    '  sed "s/^i$/I/" "$CHECKOUT/index.txt" > "$MARK.txt"\n'
    '  cp "$MARK.txt" "$CHECKOUT/index.txt"\n'
    '  git -C "$CHECKOUT" commit --quiet -am move\n'
    "fi\n"
)


def test_carries_the_gate_over_a_base_move_on_an_excluded_path(tmp_path: Path) -> None:
    extra = '[harness.land]\noverlapExclude = ["index.txt"]\n'
    repo, tree = make_repo(tmp_path, "sh gate.sh", extra=extra)
    commit(repo, "index.txt", INDEX)
    commit(repo, "gate.sh", MOVE_BASE)
    git(tree, "merge", "--quiet", "trunk")
    commit(tree, "index.txt", INDEX.replace("a\n", "A\n"))

    done = land(tree, MARK=str(tmp_path / "moved"), CHECKOUT=str(repo))

    assert done.returncode == 0, done.stdout + done.stderr
    assert "touched no file this branch changed, so the gate carries over" in done.stdout
    landed = INDEX.replace("a\n", "A\n").replace("i\n", "I\n")
    assert git(repo, "show", "trunk:index.txt") == landed.strip()


def test_logs_the_gate_per_tree_and_process(tmp_path: Path) -> None:
    _repo, tree = make_repo(tmp_path, "true")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert re.search(r"logging to \S+/land-gates/[0-9a-f]{40}-[0-9]+\.log\n", done.stdout)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("LAND_ATTEMPTS", "0", "LAND_ATTEMPTS must be 1 to 99."),
        ("LAND_PEER_TRIES", "x", "LAND_PEER_TRIES must be 1 to 99."),
        ("LAND_PEER_WAIT", "-1", "LAND_PEER_WAIT must be a number of seconds."),
    ],
)
def test_refuses_a_dial_out_of_range(tmp_path: Path, name: str, value: str, message: str) -> None:
    _repo, tree = make_repo(tmp_path, "true")

    done = land(tree, **{name: value})

    assert done.returncode == 5, done.stdout + done.stderr
    assert f"land: {message}\n" in done.stderr


def test_lands_on_the_branch_that_land_target_names(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "true")
    trunk = git(repo, "rev-parse", "trunk")
    git(repo, "worktree", "add", "--quiet", "-b", "next", str(repo.parent / "next"))

    done = land(tree, LAND_TARGET="next")

    assert done.returncode == 0, done.stdout + done.stderr
    assert git(repo, "rev-parse", "next") == git(tree, "rev-parse", "HEAD")
    assert git(repo, "rev-parse", "trunk") == trunk


def make_unborn_repo(tmp_path: Path, root_files: dict[str, str]) -> tuple[Path, Path]:
    """A checkout on an unborn `trunk` that holds `.agents/harness.toml`, and a worktree whose
    root commit holds `root_files`, then the spec."""
    repo = Path(os.path.realpath(tmp_path)) / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=trunk")
    config = '[harness]\nbaseBranch = "trunk"\ngateCommand = "true"\n'
    (repo / ".agents").mkdir()
    (repo / ".agents" / "harness.toml").write_text(config, encoding="utf-8")
    git(repo, "config", "receive.denyCurrentBranch", "updateInstead")
    git(repo, "config", "core.hooksPath", str(GITHOOKS))
    tree = repo.parent / "wt"
    git(repo, "worktree", "add", "--quiet", "--orphan", "-b", "feat", str(tree))
    for rel, text in root_files.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text, encoding="utf-8")
    git(tree, "add", *root_files)
    git(tree, "commit", "--quiet", "-m", "root")
    commit(tree, SPEC, "# A\n")
    return repo, tree


def test_starts_an_unborn_base_from_a_root_commit_that_matches_its_checkout(
    tmp_path: Path,
) -> None:
    config = '[harness]\nbaseBranch = "trunk"\ngateCommand = "true"\n'
    repo, tree = make_unborn_repo(tmp_path, {".agents/harness.toml": config})

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")
    assert (repo / SPEC).read_text(encoding="utf-8") == "# A\n"


def test_keeps_an_unborn_base_whose_checkout_differs_from_the_root_commit(
    tmp_path: Path,
) -> None:
    other = '[harness]\nbaseBranch = "trunk"\ngateCommand = "false"\n'
    repo, tree = make_unborn_repo(tmp_path, {".agents/harness.toml": other})

    done = land(tree)

    assert done.returncode == 4, done.stdout + done.stderr
    assert "does not match the files of the trunk checkout" in done.stderr
    assert git(repo, "rev-parse", "--verify", "--quiet", "trunk", check=False) == ""


NOREPLY = 'enable = ["noreply-identity"]\n'


def test_refuses_a_branch_commit_without_the_noreply_address(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "true", extra=NOREPLY)
    git(repo, "config", "user.name", "t")
    # The package's post-commit hook would rewrite the address; this commit runs no hook.
    no_hooks = ("-c", "core.hooksPath=/dev/null")
    git(tree, *no_hooks, "commit", "--quiet", "--amend", "--no-edit", "--reset-author")
    spec = git(tree, "rev-parse", "--short", "HEAD")

    done = land(tree)

    assert done.returncode == 5, done.stdout + done.stderr
    assert f"land:   {spec}  t <t@example.com> / t <t@example.com>\n" in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


def test_lands_with_the_noreply_address_on_its_own_merge_commit(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "true", extra=NOREPLY)
    git(repo, "config", "user.name", "t")
    noreply = "t@" + NOREPLY_DOMAIN
    git(tree, "commit", "--quiet", "--amend", "--no-edit", "--reset-author", email=noreply)
    commit(repo, "base.txt", "base\n")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert git(repo, "log", "-1", "--format=%ae %ce", "trunk") == f"{noreply} {noreply}"
