import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# The `ah` fixture of conftest.py: run a launcher in the test repository.
Ah = Callable[..., subprocess.CompletedProcess[str]]

FROM = "--from"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def commands(registry: dict[str, list[dict[str, Any]]]) -> list[str]:
    return [
        f"{event} {group.get('matcher', '-')} {group['hooks'][0]['command']}"
        for event, groups in registry.items()
        for group in groups
    ]


def git_config(repo: Path, key: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get", key],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def snapshot(root: Path) -> dict[str, bytes | str]:
    out: dict[str, bytes | str] = {}
    for folder, _dirs, files in os.walk(root):
        if "/.git" in folder or folder.endswith("/.git") or "__pycache__" in folder:
            continue
        for name in files:
            path = Path(folder) / name
            out[str(path.relative_to(root))] = path.read_bytes()
    for link in (root / ".agents" / "skills").iterdir():
        out[str(link.relative_to(root))] = os.readlink(link)
    return out


def test_installs_checks_and_selftests_a_plain_repository(
    repo: Path, package: Path, ah: Ah
) -> None:
    installed = ah("install", "--project", FROM, str(package))
    assert installed.returncode == 0, installed.stderr

    checked = ah("check")
    selftest = ah("selftest", engine=repo / ".claude/agent-harness/bin/ah")

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert selftest.returncode == 0, selftest.stdout + selftest.stderr
    assert selftest.stdout.splitlines()[-1] == "ah selftest: 7 of 7 fixture runs pass"
    assert "ok   rm-guard codex Bash rm -rf build" in selftest.stdout


def test_writes_the_engine_skills_rules_git_hooks_and_lockfile(
    repo: Path, package: Path, ah: Ah
) -> None:
    ah("install", "--project", FROM, str(package))

    for rel in (
        ".claude/agent-harness/bin/ah",
        ".claude/agent-harness/src/agent_harness/cli.py",
        ".claude/agent-harness/hooks/rm_guard.py",
        ".claude/agent-harness/codex/run-hook.py",
        ".claude/agent-harness/harness.toml",
        ".claude/skills/lw-demo/SKILL.md",
        ".claude/rules/lw/plain.md",
        ".githooks/commit-msg",
        ".githooks/pre-push",
        ".githooks/harness/commit-msg",
    ):
        assert (rel, (repo / rel).is_file()) == (rel, True)
    lock = read_json(repo / ".claude/agent-harness/lock.json")
    rule = (repo / ".claude/rules/lw/house-style.md").read_text()
    assert lock["mode"] == "project"
    assert lock["manifests"] == [
        {"name": "agent-harness", "file": ".claude/agent-harness/harness.toml"}
    ]
    assert ".claude/skills/lw-demo/tool.sh" in lock["executable"]
    assert rule.startswith('---\npaths:\n  - "docs/**"\n---\n# House style\n')
    assert os.readlink(repo / ".agents/skills/lw-demo") == "../../.claude/skills/lw-demo"
    assert (repo / ".githooks/commit-msg").stat().st_mode & 0o100
    assert git_config(repo, "core.hooksPath") == f"{repo.resolve()}/.githooks\n"


def test_turns_a_relative_hooks_path_into_the_absolute_one(
    repo: Path, package: Path, ah: Ah
) -> None:
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", ".githooks"], check=True)

    done = ah("install", "--project", FROM, str(package))

    assert done.returncode == 0, done.stderr
    assert git_config(repo, "core.hooksPath") == f"{repo.resolve()}/.githooks\n"
    assert f"set core.hooksPath {repo.resolve()}/.githooks\n" in done.stdout


def test_points_each_hook_command_at_the_installed_copy(repo: Path, package: Path, ah: Ah) -> None:
    ah("install", "--project", FROM, str(package))

    claude = read_json(repo / ".claude/settings.json")["hooks"]
    codex = read_json(repo / ".codex/hooks.json")["hooks"]

    adapter = 'python3 "$(git rev-parse --show-toplevel)/.claude/agent-harness/codex/run-hook.py"'
    assert commands(claude) == [
        'SessionStart - bash "$CLAUDE_PROJECT_DIR/.claude/agent-harness/hooks/session_note.sh"'
        " SessionStart",
        "UserPromptSubmit - python3"
        ' "$CLAUDE_PROJECT_DIR/.claude/agent-harness/hooks/flag_directive.py" UserPromptSubmit',
        'PreToolUse Bash python3 "$CLAUDE_PROJECT_DIR/.claude/agent-harness/hooks/rm_guard.py"'
        " PreToolUse",
    ]
    assert commands(codex)[2] == (
        f"PreToolUse Bash {adapter} .claude/agent-harness/hooks/rm_guard.py PreToolUse"
    )


def test_fails_the_check_and_names_a_skill_edited_by_one_byte(
    repo: Path, package: Path, ah: Ah
) -> None:
    ah("install", "--project", FROM, str(package))
    skill = repo / ".claude/skills/lw-demo/SKILL.md"
    skill.write_text(skill.read_text() + "x")

    checked = ah("check")

    assert checked.returncode == 1
    assert checked.stdout == ".claude/skills/lw-demo/SKILL.md edited\n"
    assert "run ah install --project" in checked.stderr


def test_names_a_missing_an_extra_and_a_registry_part(repo: Path, package: Path, ah: Ah) -> None:
    ah("install", "--project", FROM, str(package))
    (repo / ".claude/rules/lw/plain.md").unlink()
    (repo / ".claude/rules/lw/mine.md").write_text("# Mine\n")
    (repo / ".codex/hooks.json").write_text('{"hooks": {}}\n')

    checked = ah("check")

    assert checked.returncode == 1
    assert checked.stdout.splitlines() == [
        ".claude/rules/lw/mine.md extra",
        ".claude/rules/lw/plain.md missing",
        ".codex/hooks.json differs",
    ]


def test_reinstalls_with_no_change_and_keeps_the_codex_entry_order(
    repo: Path, package: Path, ah: Ah
) -> None:
    (repo / ".agents").mkdir()
    (repo / ".agents/harness.toml").write_text('[harness]\nenable = ["edit-check"]\n')
    ah("install", "--project", FROM, str(package))
    first = snapshot(repo)

    again = ah("install", "--project", FROM, str(package))

    assert again.returncode == 0, again.stderr
    assert again.stdout.splitlines() == [
        "ah install: 4 hooks, 2 rules, 1 skills, 1 git hooks; nothing changed"
    ]
    assert snapshot(repo) == first
    codex = read_json(repo / ".codex/hooks.json")["hooks"]
    assert codex["PostToolUse"][0]["matcher"] == "Write|Edit|apply_patch"


def test_rewrites_only_the_hooks_key_of_the_settings(repo: Path, package: Path, ah: Ah) -> None:
    head = '{\n  "$schema": "x",\n  "permissions": {"allow": ["Bash(ls)"]},\n  "hooks": '
    tail = ',\n  "env": {"A": "1"}\n}\n'
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.json").write_text(head + '{"Stop": []}' + tail)

    ah("install", "--project", FROM, str(package))

    text = (repo / ".claude/settings.json").read_text()
    assert text.startswith(head)
    assert text.endswith(tail)
    assert list(json.loads(text)["hooks"]) == ["SessionStart", "UserPromptSubmit", "PreToolUse"]


def test_removes_the_files_and_entries_of_a_disabled_item(
    repo: Path, package: Path, ah: Ah
) -> None:
    ah("install", "--project", FROM, str(package))
    (repo / ".agents/harness.toml").write_text(
        '[harness]\ndisable = ["lw-demo", "plain", "rm-guard"]\n'
    )

    stale = ah("check")
    ah("install", "--project", FROM, str(package))

    assert stale.returncode == 1
    assert ".claude/settings.json differs" in stale.stdout.splitlines()
    assert not (repo / ".claude/skills/lw-demo").exists()
    assert not (repo / ".agents/skills/lw-demo").is_symlink()
    assert not (repo / ".claude/rules/lw/plain.md").exists()
    assert "rm_guard" not in (repo / ".claude/settings.json").read_text()
    assert "lw-demo" not in (repo / "AGENTS.md").read_text()
    assert ah("check").returncode == 0


def test_writes_a_starter_config_once_and_never_rewrites_it(
    repo: Path, package: Path, ah: Ah
) -> None:
    ah("install", "--project", FROM, str(package))
    starter = (repo / ".agents/harness.toml").read_text()
    (repo / ".agents/harness.toml").write_text('[harness]\nbaseBranch = "trunk"\n')

    ah("install", "--project", FROM, str(package))

    assert starter.startswith("# The project facts of agent-harness.")
    assert (repo / ".agents/harness.toml").read_text() == '[harness]\nbaseBranch = "trunk"\n'


def test_keeps_the_agents_text_outside_the_marked_block(repo: Path, package: Path, ah: Ah) -> None:
    (repo / "AGENTS.md").write_text("# Project rules\n\nKeep it simple.\n")

    ah("install", "--project", FROM, str(package))

    agents = (repo / "AGENTS.md").read_text()
    assert agents.startswith("# Project rules\n\nKeep it simple.\n\n<!-- lw-harness:start -->\n")
    assert "| House style | `docs/**` | `.claude/rules/lw/house-style.md` |" in agents
    assert agents.endswith("<!-- lw-harness:end -->\n")


def test_installs_an_extra_manifest_from_its_own_folder(
    repo: Path, package: Path, ah: Ah, tmp_path: Path
) -> None:
    extra = tmp_path / "modules" / "audit"
    (extra / "hooks").mkdir(parents=True)
    (extra / "rules").mkdir()
    (extra / "harness.toml").write_text(
        '[[hook]]\nid = "audit-guard"\nsummary = "Refuse the audit write verbs"\n'
        'events = ["PreToolUse"]\nmatcher = "Bash"\nscript = "hooks/audit-guard.mjs"\n\n'
        '[[rule]]\nid = "audit-rule"\nsummary = "Audit rule"\nfile = "rules/audit.md"\n'
    )
    (extra / "hooks/audit-guard.mjs").write_text("process.exit(0);\n")
    (extra / "rules/audit.md").write_text("# Audit\n")

    done = ah("install", "--project", FROM, str(package), "--manifest", str(extra / "harness.toml"))

    assert done.returncode == 0, done.stderr
    base = ".claude/agent-harness/manifests/audit"
    assert (repo / base / "hooks/audit-guard.mjs").is_file()
    assert (repo / base / "harness.toml").is_file()
    assert (repo / ".claude/rules/lw/audit.md").is_file()
    pre = read_json(repo / ".claude/settings.json")["hooks"]["PreToolUse"]
    assert pre[-1]["hooks"][0]["command"] == (
        f'node "$CLAUDE_PROJECT_DIR/{base}/hooks/audit-guard.mjs" PreToolUse'
    )
    assert read_json(repo / ".claude/agent-harness/lock.json")["manifests"][1] == {
        "name": "audit",
        "file": f"{base}/harness.toml",
    }
    assert ah("check").returncode == 0


def test_refuses_a_node_hook_in_the_package_manifest(repo: Path, package: Path, ah: Ah) -> None:
    manifest = package / "harness.toml"
    manifest.write_text(manifest.read_text().replace("hooks/rm_guard.py", "hooks/guard.mjs"))
    (package / "hooks/guard.mjs").write_text("process.exit(0);\n")

    done = ah("install", "--project", FROM, str(package))

    assert done.returncode == 1
    assert "hook rm-guard needs Node; the package runs none" in done.stderr


def test_passes_the_check_outside_a_git_repository(tmp_path: Path, package: Path, ah: Ah) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    installed = ah("install", "--project", FROM, str(package), cwd=plain)
    checked = ah("check", cwd=plain)

    assert installed.returncode == 0, installed.stderr
    assert "is no git repository, so core.hooksPath stays unset" in installed.stdout
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_installs_into_the_folder_that_ah_root_names(repo: Path, package: Path) -> None:
    demo = repo / "demo"
    demo.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "AH_HANDED_OVER"}
    env["AH_ROOT"] = str(demo)
    launcher = package / "bin" / "ah"

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(launcher), *args],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    installed = run("install", "--project", FROM, str(package))

    assert installed.returncode == 0, installed.stderr
    assert (demo / ".claude/skills/lw-demo/SKILL.md").is_file()
    assert not (repo / ".claude").exists()
    assert run("check").returncode == 0


def test_points_the_codex_adapter_of_a_subfolder_project_at_its_own_copy(
    repo: Path, package: Path
) -> None:
    demo = repo / "apps" / "demo"
    demo.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k != "AH_HANDED_OVER"}
    env["AH_ROOT"] = str(demo)

    installed = subprocess.run(
        [sys.executable, str(package / "bin" / "ah"), "install", "--project", FROM, str(package)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stderr
    codex = read_json(demo / ".codex/hooks.json")["hooks"]
    assert commands(codex)[2] == (
        'PreToolUse Bash python3 "$(git rev-parse --show-toplevel)/apps/demo/.claude/agent-harness'
        '/codex/run-hook.py" .claude/agent-harness/hooks/rm_guard.py PreToolUse'
    )


def test_refuses_a_subfolder_project_whose_folder_the_codex_command_cannot_quote(
    repo: Path, package: Path
) -> None:
    demo = repo / "apps" / "my $demo"
    demo.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k != "AH_HANDED_OVER"}
    env["AH_ROOT"] = str(demo)

    installed = subprocess.run(
        [sys.executable, str(package / "bin" / "ah"), "install", "--project", FROM, str(package)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert installed.returncode != 0
    assert (
        "cannot install into the project folder 'apps/my $demo' of"
        f" {repo.resolve()}: it holds a character outside A-Z, a-z, 0-9, '.', '_', '/' and '-'"
    ) in installed.stderr
    assert list(demo.iterdir()) == []


def test_installs_the_package_itself_and_passes_every_fixture(repo: Path, ah: Ah) -> None:
    installed = ah("install", "--project")
    assert installed.returncode == 0, installed.stderr

    checked = ah("check")
    selftest = ah("selftest", engine=repo / ".claude/agent-harness/bin/ah")

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert selftest.returncode == 0, selftest.stdout + selftest.stderr


def test_refuses_to_install_over_a_project_copy_and_replaces_it_on_request(
    repo: Path, package: Path, ah: Ah
) -> None:
    copy = repo / ".claude/skills/lw-demo"
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_text("# An old project copy\n")
    (copy / "helper.py").write_text("print('old')\n")

    refused = ah("install", "--project", FROM, str(package))
    refused_files = sorted(p.name for p in copy.iterdir())
    locked_after_refusal = (repo / ".claude/agent-harness/lock.json").exists()
    replaced = ah("install", "--project", "--replace", FROM, str(package))
    checked = ah("check")

    assert refused.returncode == 1
    assert "  .claude/skills/lw-demo/SKILL.md differs\n" in refused.stderr
    assert "  .claude/skills/lw-demo/helper.py extra\n" in refused.stderr
    assert refused_files == ["SKILL.md", "helper.py"]
    assert locked_after_refusal is False
    assert replaced.returncode == 0, replaced.stderr
    assert "replaced .claude/skills/lw-demo/SKILL.md: a project copy" in replaced.stdout
    assert "removed .claude/skills/lw-demo/helper.py: a file of a project copy" in replaced.stdout
    assert not (copy / "helper.py").exists()
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_passes_the_check_after_the_project_adds_a_skill_of_its_own(
    repo: Path, package: Path, ah: Ah
) -> None:
    ah("install", "--project", FROM, str(package))
    own = repo / ".claude/skills/my-own"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("# My own skill\n")

    checked = ah("check")
    ah("install", "--project", FROM, str(package))
    agents = (repo / "AGENTS.md").read_text()
    checked_again = ah("check")

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert os.readlink(repo / ".agents/skills/my-own") == "../../.claude/skills/my-own"
    assert "`my-own`" in agents
    assert checked_again.returncode == 0, checked_again.stdout + checked_again.stderr


def test_lets_a_push_update_the_checkout_of_the_base_branch(
    repo: Path, package: Path, ah: Ah
) -> None:
    done = ah("install", "--project", FROM, str(package))

    assert done.returncode == 0, done.stderr
    assert git_config(repo, "receive.denyCurrentBranch") == "updateInstead\n"
    assert "set receive.denyCurrentBranch updateInstead\n" in done.stdout


PROJECT_HOOK = {"type": "command", "command": "bash scripts/own-guard.sh", "timeout": 3}


def with_project_hook(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_keeps_a_hand_written_hook_in_both_registries_after_the_harness_entries(
    repo: Path, package: Path, ah: Ah
) -> None:
    own = {"matcher": "Bash", "hooks": [PROJECT_HOOK]}
    stop = {"hooks": [PROJECT_HOOK]}
    for rel in (".claude/settings.json", ".codex/hooks.json"):
        with_project_hook(repo / rel, json.dumps({"hooks": {"PreToolUse": [own], "Stop": [stop]}}))

    installed = ah("install", "--project", FROM, str(package))
    first = snapshot(repo)
    again = ah("install", "--project", FROM, str(package))
    checked = ah("check")

    assert installed.returncode == 0, installed.stderr
    for rel in (".claude/settings.json", ".codex/hooks.json"):
        hooks = read_json(repo / rel)["hooks"]
        assert (rel, hooks["PreToolUse"][-1]) == (rel, own)
        assert (rel, len(hooks["PreToolUse"])) == (rel, 2)
        assert ".claude/agent-harness/" in hooks["PreToolUse"][0]["hooks"][0]["command"]
        assert (rel, hooks["Stop"]) == (rel, [stop])
    assert again.stdout.splitlines()[-1].endswith("nothing changed")
    assert snapshot(repo) == first
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_drops_a_harness_entry_from_a_project_group_and_keeps_its_own_command(
    repo: Path, package: Path, ah: Ah
) -> None:
    stale = 'python3 "$CLAUDE_PROJECT_DIR/.claude/agent-harness/hooks/gone.py" PreToolUse'
    mixed = {"matcher": "Bash", "hooks": [{"type": "command", "command": stale}, PROJECT_HOOK]}
    with_project_hook(
        repo / ".claude/settings.json", json.dumps({"hooks": {"PreToolUse": [mixed]}})
    )

    ah("install", "--project", FROM, str(package))

    pre = read_json(repo / ".claude/settings.json")["hooks"]["PreToolUse"]
    assert pre[-1] == {"matcher": "Bash", "hooks": [PROJECT_HOOK]}
    assert "gone.py" not in (repo / ".claude/settings.json").read_text()


def test_names_each_project_hook_in_the_check_and_keeps_the_exit_code(
    repo: Path, package: Path, ah: Ah
) -> None:
    ah("install", "--project", FROM, str(package))
    settings = read_json(repo / ".claude/settings.json")
    settings["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [PROJECT_HOOK]})
    (repo / ".claude/settings.json").write_text(json.dumps(settings))
    codex = read_json(repo / ".codex/hooks.json")
    codex["hooks"]["Stop"] = [{"hooks": [PROJECT_HOOK]}]
    (repo / ".codex/hooks.json").write_text(json.dumps(codex))

    checked = ah("check")

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert checked.stdout.splitlines() == [
        ".claude/settings.json PreToolUse Bash: a project hook that no manifest declares:"
        " bash scripts/own-guard.sh",
        ".codex/hooks.json Stop -: a project hook that no manifest declares:"
        " bash scripts/own-guard.sh",
        "ah check: the installed files match the lockfile",
    ]


def write_project_manifest(repo: Path) -> Path:
    folder = repo / "tools" / "agents"
    (folder / "hooks").mkdir(parents=True)
    (folder / "harness.toml").write_text(
        '[[hook]]\nid = "own-guard"\nsummary = "A guard of the project"\n'
        'events = ["PreToolUse"]\nmatcher = "Bash"\nscript = "hooks/own_guard.py"\n'
    )
    (folder / "hooks/own_guard.py").write_text("import sys\nsys.stdin.read()\n")
    (repo / ".agents").mkdir()
    (repo / ".agents/harness.toml").write_text(
        '[harness]\nmanifests = ["tools/agents/harness.toml"]\n'
    )
    return folder / "harness.toml"


def test_installs_the_project_manifests_that_the_config_names(
    repo: Path, package: Path, ah: Ah
) -> None:
    manifest = write_project_manifest(repo)

    installed = ah("install", "--project", FROM, str(package))
    first = snapshot(repo)
    again = ah("install", "--project", FROM, str(package), "--manifest", str(manifest))

    assert installed.returncode == 0, installed.stderr
    assert (repo / ".claude/agent-harness/manifests/agents/hooks/own_guard.py").is_file()
    assert "own_guard.py" in read_json(repo / ".claude/settings.json")["hooks"]["PreToolUse"][-1][
        "hooks"
    ][0]["command"]
    assert again.returncode == 0, again.stderr
    assert again.stdout.splitlines()[-1].endswith("nothing changed")
    assert snapshot(repo) == first
    assert ah("check").returncode == 0


def test_refuses_a_config_manifest_that_does_not_exist(repo: Path, package: Path, ah: Ah) -> None:
    (repo / ".agents").mkdir()
    (repo / ".agents/harness.toml").write_text('[harness]\nmanifests = ["tools/none.toml"]\n')

    done = ah("install", "--project", FROM, str(package))

    assert done.returncode == 1
    assert "harness.manifests names tools/none.toml, which is no file" in done.stderr


def test_keeps_a_project_skill_that_was_there_before_the_install(
    repo: Path, package: Path, ah: Ah
) -> None:
    own = repo / ".claude/skills/release-notes"
    (own / "scripts").mkdir(parents=True)
    (own / "SKILL.md").write_text("---\nname: release-notes\ndescription: Notes.\n---\n# Notes\n")
    (own / "scripts/collect.sh").write_text("echo notes\n")
    before = {p.name: p.read_bytes() for p in own.rglob("*") if p.is_file()}

    installed = ah("install", "--project", FROM, str(package))
    again = ah("install", "--project", FROM, str(package))
    checked = ah("check")

    assert installed.returncode == 0, installed.stderr
    assert {p.name: p.read_bytes() for p in own.rglob("*") if p.is_file()} == before
    assert os.readlink(repo / ".agents/skills/release-notes") == (
        "../../.claude/skills/release-notes"
    )
    assert "release-notes" not in read_json(repo / ".claude/agent-harness/lock.json")["skills"]
    assert "`release-notes`" in (repo / "AGENTS.md").read_text()
    assert again.stdout.splitlines()[-1].endswith("nothing changed")
    assert checked.returncode == 0, checked.stdout + checked.stderr


def write_rule_adapter(repo: Path, rule_id: str, text: str) -> Path:
    path = repo / "harness/adapters/rules" / f"{rule_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_appends_the_project_notes_of_a_rule_adapter(repo: Path, package: Path, ah: Ah) -> None:
    write_rule_adapter(repo, "plain", "Use the words of the glossary.\n")

    installed = ah("install", "--project", FROM, str(package))
    checked = ah("check")

    assert installed.returncode == 0, installed.stderr
    assert (repo / ".claude/rules/lw/plain.md").read_text() == (
        "# Plain rule\n\n## Project notes\n\nUse the words of the glossary.\n"
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_replaces_the_body_of_a_rule_and_keeps_its_paths(repo: Path, package: Path, ah: Ah) -> None:
    write_rule_adapter(repo, "house-style", "---\nreplace: true\n---\n# Our style\n")

    ah("install", "--project", FROM, str(package))

    assert (repo / ".claude/rules/lw/house-style.md").read_text() == (
        '---\npaths:\n  - "docs/**"\n---\n# Our style\n'
    )


def test_reports_an_edited_rule_adapter_until_the_next_install(
    repo: Path, package: Path, ah: Ah
) -> None:
    adapter = write_rule_adapter(repo, "plain", "One note.\n")
    ah("install", "--project", FROM, str(package))
    adapter.write_text("Two notes.\n", encoding="utf-8")

    stale = ah("check")
    ah("install", "--project", FROM, str(package))
    adapter.unlink()
    removed = ah("check")

    assert stale.returncode == 1
    assert stale.stdout.splitlines() == ["harness/adapters/rules/plain.md stale"]
    assert removed.stdout.splitlines() == ["harness/adapters/rules/plain.md stale"]


def test_refuses_a_rule_adapter_with_an_unknown_front_matter_key(
    repo: Path, package: Path, ah: Ah
) -> None:
    write_rule_adapter(repo, "plain", "---\nmode: replace\n---\nText.\n")

    done = ah("install", "--project", FROM, str(package))

    assert done.returncode == 1
    assert "harness/adapters/rules/plain.md: unknown front matter line 'mode: replace'" in (
        done.stderr
    )


def test_prints_the_size_of_each_installed_rule_and_the_total(
    repo: Path, package: Path, ah: Ah
) -> None:
    ah("install", "--project", FROM, str(package))

    done = ah("rules")

    house = (repo / ".claude/rules/lw/house-style.md").read_text()
    plain = (repo / ".claude/rules/lw/plain.md").read_text()
    lines = [house.count("\n"), plain.count("\n")]
    chars = [len(house), len(plain)]
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [
        f".claude/rules/lw/house-style.md {lines[0]} lines {chars[0]} chars",
        f".claude/rules/lw/plain.md {lines[1]} lines {chars[1]} chars",
        f"total {sum(lines)} lines {sum(chars)} chars",
    ]
