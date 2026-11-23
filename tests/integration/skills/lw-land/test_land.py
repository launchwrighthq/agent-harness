"""land.sh reads the base branch and the gate from the config and renders the landed spec."""

import json
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


PATTERN = (
    "[harness.land]\ngreenPattern = '^gate: green( in [0-9.]+s| \\(carried [0-9]+ steps\\))$'\n"
)


def test_refuses_a_gate_that_exits_0_without_a_line_that_matches_the_green_pattern(
    tmp_path: Path,
) -> None:
    repo, tree = make_repo(tmp_path, "echo gate: files changed in the run", extra=PATTERN)

    done = land(tree)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "no line of the gate log matches ^gate: green" in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


def test_lands_when_a_line_of_the_gate_log_matches_the_green_pattern(tmp_path: Path) -> None:
    for number, line in enumerate(("gate: green in 123.4s", "gate: green (carried 3 steps)")):
        folder = tmp_path / str(number)
        folder.mkdir()
        repo, tree = make_repo(folder, f"echo '{line}'", extra=PATTERN)

        done = land(tree)

        assert done.returncode == 0, done.stdout + done.stderr
        assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")


def test_needs_the_green_token_and_the_green_pattern_when_both_are_set(tmp_path: Path) -> None:
    both = GREEN + "greenPattern = '^gate: green in [0-9.]+s$'\n"
    repo, tree = make_repo(tmp_path, "echo gate: green in 1.0s", extra=both)

    done = land(tree)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "no line of the gate log is verify: green" in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


def test_refuses_a_green_pattern_that_is_not_a_regular_expression(tmp_path: Path) -> None:
    extra = "[harness.land]\ngreenPattern = 'green ('\n"
    repo, tree = make_repo(tmp_path, "touch gate-ran", extra=extra)
    (repo / "base.txt").write_text("a base move\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "--quiet", "-m", "base move")
    head = git(tree, "rev-parse", "HEAD")

    done = land(tree)

    assert done.returncode == 5, done.stdout + done.stderr
    assert done.stderr.endswith(
        "harness.land.greenPattern is not an extended regular expression (grep -E): green (\n"
    )
    assert git(tree, "rev-parse", "HEAD") == head
    assert not (tree / "gate-ran").exists()


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


# Each gate appends its mode name to the file that OUT names.
def test_prints_each_warning_of_the_page_render_after_the_landing(tmp_path: Path) -> None:
    extra = '[specs]\nmissingAssets = "placeholder"\n'
    repo, tree = make_repo(tmp_path, "true", extra=extra)
    commit(tree, SPEC, (tree / SPEC).read_text(encoding="utf-8") + "\n![Board](assets/b.svg)\n")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    warnings = [line for line in done.stdout.splitlines() if line.startswith("land: page: warn")]
    assert len(warnings) == 1, done.stdout
    assert warnings[0].startswith(f"land: page: warning: {repo / SPEC}:")
    assert warnings[0].endswith(
        f"the image assets/b.svg has no file at {repo}/docs/specs/assets/b.svg;"
        " the page shows a placeholder"
    )


MODES = '[harness.land.gates]\nchanged = "echo changed >> $OUT"\n'
FULL_GATE = "echo full >> $OUT"


@pytest.mark.parametrize(
    ("extra", "env", "mode"),
    [
        (MODES, {"LAND_GATE": "changed"}, "changed"),
        (MODES, {}, "full"),
        ('[harness.land]\ndefaultGate = "changed"\n' + MODES, {}, "changed"),
        ('[harness.land]\ndefaultGate = "changed"\n' + MODES, {"LAND_GATE": "full"}, "full"),
    ],
)
def test_runs_the_gate_command_of_the_picked_mode(
    tmp_path: Path, extra: str, env: dict[str, str], mode: str
) -> None:
    repo, tree = make_repo(tmp_path, FULL_GATE, extra=extra)
    out = tmp_path / "out.txt"

    done = land(tree, OUT=str(out), **env)

    assert done.returncode == 0, done.stdout + done.stderr
    assert out.read_text(encoding="utf-8") == f"{mode}\n"
    assert f"land: gate: mode {mode}\n" in done.stdout
    assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")


def test_gives_the_gate_the_merged_base_and_the_worktree_root(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "echo $LAND_MERGED_BASE $LAND_ROOT > $OUT")
    out = tmp_path / "out.txt"
    trunk = git(repo, "rev-parse", "trunk")

    done = land(tree, OUT=str(out))

    assert done.returncode == 0, done.stdout + done.stderr
    assert out.read_text(encoding="utf-8") == f"{trunk} {tree}\n"


def test_stops_on_an_unknown_gate_mode_and_lists_the_modes(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, FULL_GATE, extra=MODES)
    out = tmp_path / "out.txt"

    done = land(tree, OUT=str(out), LAND_GATE="quick")

    assert done.returncode == 5, done.stdout + done.stderr
    assert (
        "land: the gate mode quick (LAND_GATE=quick) is unknown. "
        "The modes are: full, none, changed.\n"
    ) in done.stderr
    assert not out.exists()
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


@pytest.mark.parametrize("name", ["full", "none"])
def test_refuses_a_gates_table_that_names_a_reserved_mode(tmp_path: Path, name: str) -> None:
    extra = f'[harness.land.gates]\n{name} = "true"\n'
    repo, tree = make_repo(tmp_path, "true", extra=extra)

    done = land(tree)

    assert done.returncode == 5, done.stdout + done.stderr
    assert f"land: harness.land.gates holds the mode {name}." in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


BAD_GATE_CONFIGS = [
    (
        '[harness.land]\ndefaultGate = "none"\n',
        "land: harness.land.defaultGate is none. Only LAND_GATE=0 skips the gate."
        " Set a mode that runs a command.\n",
    ),
    (
        '[harness.land.gates]\n"quick!" = "true"\n',
        "land: harness.land.gates holds the mode quick!. A mode name starts with a letter and"
        " holds letters, digits, - and _.\n",
    ),
    (
        '[harness.land]\ndefaultGate = "changed"\n[harness.land.gates]\nchanged = ""\n',
        "land: harness.land.gates.changed is empty. Set the command of the mode.\n",
    ),
    (
        "[harness.land.gates]\nchanged = 1\n",
        "It is a table of mode names and commands.\n",
    ),
]


@pytest.mark.parametrize(("extra", "message"), BAD_GATE_CONFIGS)
def test_stops_before_the_merge_on_a_gate_config_that_names_no_command(
    tmp_path: Path, extra: str, message: str
) -> None:
    repo, tree = make_repo(tmp_path, FULL_GATE, extra=extra)
    out = tmp_path / "out.txt"

    done = land(tree, OUT=str(out))

    assert done.returncode == 5, done.stdout + done.stderr
    assert message in done.stderr
    assert not out.exists()
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


def test_says_that_the_result_record_of_a_red_gate_names_no_failed_step(tmp_path: Path) -> None:
    steps = {"lint": {"code": 0, "attributable": False, "failedFiles": []}}
    repo, tree = make_repo(tmp_path, "sh gate.sh")
    commit(repo, "gate.sh", f"echo 'verify: failures {json.dumps(steps)}'\nexit 1\n")

    done = land(tree)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "land: gate: the result record names no failed step\n" in done.stderr
    assert "land: gate: red at these steps" not in done.stderr


@pytest.mark.parametrize("dial", ["0", "none"])
def test_skips_the_gate_in_the_mode_none(tmp_path: Path, dial: str) -> None:
    repo, tree = make_repo(tmp_path, FULL_GATE, extra=MODES)
    out = tmp_path / "out.txt"

    done = land(tree, OUT=str(out), LAND_GATE=dial)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "land: gate: mode none\n" in done.stdout
    assert (
        f"land: gate: SKIPPED by LAND_GATE={dial}. The report must say the gate was not run.\n"
    ) in done.stdout
    assert not out.exists()
    assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("first", "second", "runs"),
    [
        ("changed", "full", "changed\nfull\n"),
        ("full", "changed", "full\n"),
        ("changed", "changed", "changed\n"),
    ],
)
def test_reads_the_gate_mark_of_a_tree_per_mode(
    tmp_path: Path, first: str, second: str, runs: str
) -> None:
    repo, tree = make_repo(tmp_path, FULL_GATE, extra=MODES)
    out = tmp_path / "out.txt"
    assert land(tree, OUT=str(out), LAND_GATE=first).returncode == 0
    # A second commit of the same tree.
    git(tree, "commit", "--quiet", "--allow-empty", "-m", "again")

    done = land(tree, OUT=str(out), LAND_GATE=second)

    assert done.returncode == 0, done.stdout + done.stderr
    assert out.read_text(encoding="utf-8") == runs
    assert git(repo, "rev-parse", "trunk") == git(tree, "rev-parse", "HEAD")


def test_carries_the_gate_over_with_the_mode_of_the_gate(tmp_path: Path) -> None:
    extra = (
        '[harness.land]\noverlapExclude = ["index.txt"]\n'
        '[harness.land.gates]\nchanged = "sh gate.sh"\n'
    )
    repo, tree = make_repo(tmp_path, "false", extra=extra)
    commit(repo, "index.txt", INDEX)
    commit(repo, "gate.sh", MOVE_BASE)
    git(tree, "merge", "--quiet", "trunk")
    commit(tree, "index.txt", INDEX.replace("a\n", "A\n"))

    done = land(tree, MARK=str(tmp_path / "moved"), CHECKOUT=str(repo), LAND_GATE="changed")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "touched no file this branch changed, so the gate carries over" in done.stdout
    landed = git(tree, "rev-parse", "HEAD^{tree}")
    marks = repo / ".git" / "land-gates"
    assert (marks / f"{landed}.changed").is_file()
    assert not (marks / landed).exists()


RED_STEPS = {
    "lint": {"code": 0, "attributable": False, "failedFiles": []},
    "test": {"code": 1, "attributable": True, "failedFiles": ["t/old.sh", "t/new.sh"]},
    "typecheck": {"code": 2, "attributable": False, "failedFiles": []},
}
RED_GATE = f"echo gate output\necho 'verify: failures {json.dumps(RED_STEPS)}'\nexit 1\n"


def test_prints_each_failed_step_of_a_red_gate_without_a_probe(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "sh gate.sh")
    commit(repo, "gate.sh", RED_GATE)

    done = land(tree)

    assert done.returncode == 2, done.stdout + done.stderr
    assert (
        "land:   test: exit 1, failed files: t/old.sh, t/new.sh\n"
        "land:   typecheck: exit 2, no failed file\n"
    ) in done.stderr
    assert "land:   lint" not in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")


def test_says_that_the_log_of_a_red_gate_has_no_result_record(tmp_path: Path) -> None:
    _repo, tree = make_repo(tmp_path, "echo gate output; exit 1")

    done = land(tree)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "land: gate: the log has no result record\n" in done.stderr


def test_prints_the_explain_output_between_the_steps_and_the_log_tail(tmp_path: Path) -> None:
    extra = '[harness.land]\nexplainCommand = "echo explained $LAND_GATE_LOG in $PWD"\n'
    repo, tree = make_repo(tmp_path, "sh gate.sh", extra=extra)
    commit(repo, "gate.sh", RED_GATE)

    done = land(tree)

    assert done.returncode == 2, done.stdout + done.stderr
    explained = re.search(
        rf"^explained \S+/land-gates/[0-9a-f]{{40}}-[0-9]+\.log in {re.escape(str(tree))}$",
        done.stderr,
        re.MULTILINE,
    )
    assert explained is not None, done.stderr
    steps = done.stderr.index("land:   typecheck: exit 2, no failed file\n")
    tail = done.stderr.index("\ngate output\n")
    assert steps < explained.start() < tail


def test_keeps_the_exit_code_of_the_landing_when_the_explain_command_fails(
    tmp_path: Path,
) -> None:
    extra = '[harness.land]\nexplainCommand = "echo explained; exit 7"\n'
    repo, tree = make_repo(tmp_path, "sh gate.sh", extra=extra)
    commit(repo, "gate.sh", RED_GATE)

    done = land(tree)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "explained\n" in done.stderr
    assert "land: explain: the command failed (exit 7). The gate verdict stays.\n" in done.stderr


REMOVE_WORKTREE = "land.removeWorktree = true\n"


def has_branch(repo: Path, name: str) -> bool:
    return git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False) != ""


def test_removes_the_clean_worktree_and_the_landed_branch_as_its_last_step(
    tmp_path: Path,
) -> None:
    repo, tree = make_repo(tmp_path, "true", extra=REMOVE_WORKTREE)
    landed = git(tree, "rev-parse", "HEAD")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert git(repo, "rev-parse", "trunk") == landed
    assert not tree.exists()
    assert not has_branch(repo, "feat")
    assert done.stdout.endswith(
        f"land: worktree: removed {tree} and deleted the branch feat\nland: continue in {repo}\n"
    )


def test_keeps_the_worktree_and_the_branch_without_the_remove_worktree_key(
    tmp_path: Path,
) -> None:
    repo, tree = make_repo(tmp_path, "true")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert tree.is_dir()
    assert has_branch(repo, "feat")
    assert "land: worktree:" not in done.stdout


def test_keeps_a_worktree_that_holds_an_untracked_file_and_says_why(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "true", extra=REMOVE_WORKTREE)
    (tree / "notes.txt").write_text("not committed\n", encoding="utf-8")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert (tree / "notes.txt").is_file()
    assert has_branch(repo, "feat")
    assert done.stdout.endswith(
        f"land: worktree: {tree} stays, because it holds 1 changed or untracked files\n"
    )


def test_keeps_a_worktree_that_holds_an_ignored_file_no_tool_rebuilds(tmp_path: Path) -> None:
    repo, tree = make_repo(tmp_path, "true", extra=REMOVE_WORKTREE)
    (tree / "dist").mkdir()
    (tree / "dist" / "run.bin").write_text("measured\n", encoding="utf-8")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert (tree / "dist" / "run.bin").is_file()
    assert has_branch(repo, "feat")
    assert done.stdout.endswith(
        f"land: worktree: {tree} stays, because it holds ignored files that no tool rebuilds,"
        " such as dist/\n"
    )


def test_removes_a_worktree_whose_ignored_files_are_rebuildable_or_spec_pages(
    tmp_path: Path,
) -> None:
    repo, tree = make_repo(tmp_path, "true", extra=REMOVE_WORKTREE)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("node_modules/\n", encoding="utf-8")
    (tree / "node_modules" / "pkg").mkdir(parents=True)
    (tree / "node_modules" / "pkg" / "index.js").write_text("x\n", encoding="utf-8")
    (tree / "docs" / "specs" / "dist").mkdir()
    (tree / "docs" / "specs" / "dist" / "2026-01-01-a.html").write_text("page\n", encoding="utf-8")

    done = land(tree)

    assert done.returncode == 0, done.stdout + done.stderr
    assert not tree.exists()
    assert not has_branch(repo, "feat")
    assert f"land: continue in {repo}\n" in done.stdout


def test_never_removes_the_main_checkout_when_the_landing_runs_from_it(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path, "true", extra=REMOVE_WORKTREE)
    git(repo, "switch", "--quiet", "-c", "side")
    commit(repo, "side.txt", "side\n")
    base = repo / ".claude" / "worktrees" / "base"
    git(repo, "worktree", "add", "--quiet", str(base), "trunk")

    done = land(repo)

    assert done.returncode == 0, done.stdout + done.stderr
    assert git(repo, "rev-parse", "trunk") == git(repo, "rev-parse", "side")
    assert (base / "side.txt").is_file()
    assert has_branch(repo, "side")
    assert done.stdout.endswith(
        f"land: worktree: {repo} is the main checkout, so it stays, and so does the branch side\n"
    )


def test_refuses_a_remove_worktree_value_that_is_no_boolean_before_the_merge(
    tmp_path: Path,
) -> None:
    repo, tree = make_repo(tmp_path, "true", extra='land.removeWorktree = "always"\n')

    done = land(tree)

    assert done.returncode == 5, done.stdout + done.stderr
    assert "land: harness.land.removeWorktree is always. Set it to true or false.\n" in done.stderr
    assert git(repo, "rev-parse", "trunk") != git(tree, "rev-parse", "HEAD")
    assert tree.is_dir()
