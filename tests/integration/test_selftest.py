import subprocess
from collections.abc import Callable
from pathlib import Path

from agent_harness import config
from agent_harness.install.plan import DISPATCHER, ENGINE_DIR
from agent_harness.install.project import installed_manifests, plan_for, require_lock
from agent_harness.selftest import run_global_hooks, skill_links

# The `ah` fixture of conftest.py: run a launcher in the test repository.
Ah = Callable[..., subprocess.CompletedProcess[str]]

LAX = """
[[hook]]
id = "lax-guard"
summary = "A guard that allows everything"
events = ["PreToolUse"]
matcher = "Bash"
script = "hooks/lax.sh"

[[hook.fixture]]
tool = "Bash"
command = "rm -rf build"
expect = "deny"
"""

OLD_DIRECTIVE = """
[[hook]]
id = "old-directive"
summary = "A directive that rewrites the prompt"
events = ["UserPromptSubmit"]
script = "hooks/old_directive.py"

[[hook.fixture]]
prompt = "fix it -u"
expect = "context"
"""


def add_hook(package: Path, manifest: str, script: str, text: str) -> None:
    (package / "harness.toml").write_text((package / "harness.toml").read_text() + manifest)
    (package / script).write_text(text)


def test_fails_a_guard_that_lets_its_fixture_through(repo: Path, package: Path, ah: Ah) -> None:
    add_hook(package, LAX, "hooks/lax.sh", "cat >/dev/null\nexit 0\n")
    ah("install", "--project", "--from", str(package))

    done = ah("selftest")

    assert done.returncode == 1
    failed = [line for line in done.stdout.splitlines() if line.startswith("FAIL")]
    assert [line.split(":")[0] for line in failed] == [
        "FAIL lax-guard claude Bash rm -rf build",
        "FAIL lax-guard codex Bash rm -rf build",
    ]
    assert "expected a deny, got exit 0 and no output" in failed[0]


def test_fails_a_directive_that_prints_a_prompt(repo: Path, package: Path, ah: Ah) -> None:
    script = 'import json, sys\nprint(json.dumps({"prompt": json.load(sys.stdin)["prompt"]}))\n'
    add_hook(package, OLD_DIRECTIVE, "hooks/old_directive.py", script)
    ah("install", "--project", "--from", str(package))

    done = ah("selftest")

    assert done.returncode == 1
    assert (
        'FAIL old-directive claude prompt "fix it -u": printed {"prompt": ...}, which the'
        " client ignores" in done.stdout.splitlines()
    )


def test_refuses_to_run_before_an_install(repo: Path, ah: Ah) -> None:
    done = ah("selftest")

    assert done.returncode == 1
    assert "has no project install; run ah install --project" in done.stderr


def test_refuses_to_run_over_an_edited_hook(repo: Path, package: Path, ah: Ah) -> None:
    ah("install", "--project", "--from", str(package))
    (repo / ".claude/agent-harness/hooks/rm_guard.py").write_text("print('{}')\n")

    done = ah("selftest")

    assert done.returncode == 1
    assert "so run ah install --project first" in done.stderr


def test_runs_the_guards_from_a_worktree_subfolder_for_both_clients(repo: Path, ah: Ah) -> None:
    ah("install", "--project")

    done = ah("selftest", engine=repo / ".claude/agent-harness/bin/ah")

    lines = [line for line in done.stdout.splitlines() if " portability " in line]
    assert [line for line in lines if line.startswith("FAIL")] == []
    prefix = "workspace-guard from a worktree subfolder:"
    for client in ("claude", "codex"):
        assert (
            f"ok   portability {client} {prefix} git -C <main checkout> reset --hard HEAD" in lines
        )
        assert f"ok   portability {client} {prefix} git status --short" in lines
        assert (
            f"ok   portability {client} worktree-path-guard from a worktree subfolder:"
            " git worktree add ../outside" in lines
        )
    assert "ok   portability codex skill lw-spec-mockup through .agents/skills" in lines


def test_skips_each_deny_fixture_that_names_a_plain_env_file_of_the_project(
    repo: Path, ah: Ah
) -> None:
    (repo / ".agents").mkdir()
    (repo / ".agents/harness.toml").write_text('[harness]\nplainEnvFiles = [".env.local"]\n')
    ah("install", "--project")

    done = ah("selftest", engine=repo / ".claude/agent-harness/bin/ah")

    lines = done.stdout.splitlines()
    skip = "names a plain env file of this project"
    ran = [line for line in lines if line.startswith(("ok   ", "FAIL "))]
    assert [line for line in lines if line.startswith("FAIL")] == []
    assert f"SKIP bash-read-guard Bash cat .env.local: {skip}" in lines
    assert "ok   bash-read-guard claude Bash cat .env.local.bak" in lines
    assert lines[-1] == f"ah selftest: {len(ran)} of {len(ran)} fixture runs pass"
    assert done.returncode == 0


def test_fails_a_skill_whose_link_points_at_another_skill(repo: Path, ah: Ah) -> None:
    ah("install", "--project")
    link = repo / ".agents/skills/lw-land"
    link.unlink()
    link.symlink_to("../../.claude/skills/lw-spec-write")
    plan = plan_for(repo, config.load(repo), installed_manifests(repo, require_lock(repo)))

    failed = [r for r in skill_links(plan, repo) if not r.ok]

    assert [(r.fixture, r.detail.split(" points at ")[0]) for r in failed] == [
        ("skill lw-land through .agents/skills", ".agents/skills/lw-land")
    ]


GLOBAL_HOOK_CASES = [
    "a plain global hook with run",
    "a plain global hook with skip",
    "a global hook that runs .git/hooks with run",
    "a global hook that runs .git/hooks with skip",
]


def test_counts_one_run_of_each_global_hook_with_run_and_none_with_skip(
    repo: Path, package: Path, ah: Ah
) -> None:
    ah("install", "--project", "--from", str(package))

    done = ah("selftest")

    assert done.returncode == 0, done.stdout + done.stderr
    lines = [line for line in done.stdout.splitlines() if " global-hooks " in line]
    assert lines == [f"ok   global-hooks git {case}" for case in GLOBAL_HOOK_CASES]


def test_fails_a_dispatcher_that_does_not_read_the_global_hooks_choice(
    repo: Path, package: Path, ah: Ah
) -> None:
    ah("install", "--project", "--from", str(package))
    dispatcher = repo / ENGINE_DIR / DISPATCHER
    dispatcher.write_text(dispatcher.read_text().replace("harness.globalHooks", "harness.none"))
    plan = plan_for(repo, config.load(repo), installed_manifests(repo, require_lock(repo)))

    results = run_global_hooks(plan, repo)

    wanted = "commit-msg 1, post-commit 1, pre-commit 1, pre-push 1, prepare-commit-msg 1"
    assert [(r.fixture, r.detail) for r in results] == [
        (GLOBAL_HOOK_CASES[0], ""),
        (
            GLOBAL_HOOK_CASES[1],
            f"the global hooks ran: {wanted}, push-to-checkout 1; expected none",
        ),
        (GLOBAL_HOOK_CASES[2], ""),
        (
            GLOBAL_HOOK_CASES[3],
            f"the global hooks ran: {wanted}, push-to-checkout 1; expected none",
        ),
    ]


def test_runs_no_global_hooks_case_for_a_plan_with_no_git_hook(
    repo: Path, package: Path, ah: Ah
) -> None:
    (repo / ".agents").mkdir()
    (repo / ".agents/harness.toml").write_text('[harness]\ndisable = ["commit-msg-demo"]\n')
    ah("install", "--project", "--from", str(package))

    done = ah("selftest")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "global-hooks" not in done.stdout
