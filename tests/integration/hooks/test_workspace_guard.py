"""workspace-guard allows only simple git command shapes inside its own project.

The cases are the ones the hook passed in its earlier home, so the move keeps its behavior.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import hook_project

HOOK = "workspace-guard.py"

SIMPLE_SHAPES = [
    "git status",
    "git",
    "git log --oneline -5",
    "git log --format='%h %s' -- '*.ts'",
    "git -C . status",
    "git -C .claude status",
    "cd sub && git status",
    "cd sub; git log",
    "cd sub\ngit log\n",
    "git add a.txt && git commit -m x",
    "LANG=C LC_ALL=C TZ=UTC NO_COLOR=1 TERM=dumb GIT_TERMINAL_PROMPT=0 git log",
    "GIT_AUTHOR_NAME=x GIT_AUTHOR_EMAIL=y GIT_COMMITTER_DATE=now git log",
    "git log | head -5",
    "git log | tail -n 2",
    "git log | grep -E 'fix|feat'",
    "git log | rg -c fix",
    "git status --porcelain | wc -l",
    "git log | sort -r",
    "git log | uniq -c",
    "git log | cat",
    "git log | less -R",
    "git log --format=%H | jq -R .",
    "git diff --stat | sed -n '1,5p'",
    "git log | sed -n '/fix/p'",
    "git log | awk -F: '{print $1}'",
    "git status 2>&1 | head",
    "git status 2>/dev/null",
    "git status >/dev/null 2>&1",
    "git commit -F - <<'EOF'\nfix: $(x) and `y`\nEOF",
    "git commit -F - <<EOF\nfix: plain text\nEOF",
]
READER_STEPS = [
    'git merge-base --is-ancestor HEAD HEAD; echo "exit $?"',
    "git status --short | head; git log --oneline -3; grep -n 'export' a.txt",
    "git status && md5sum a.txt b.txt",
    "ls -la .git | grep -i push; git config --get core.hooksPath",
    "[ -f a.txt ] && git status",
    "test -d .git; git status",
    "cd sub && ls && git status",
    "diff a.txt b.txt; git status",
    "printf '%s\\n' x; git status",
    "git log | head | tail -2",
    "git log --oneline | grep fix | wc -l",
    "git commit -F - <<'EOF'\nfix: it's quoted\nEOF",
]
WRITING_READERS = [
    "echo $HOME; git log",
    "echo ${X}; git log",
    "echo $?$X; git log",
    "echo $'\\x41'; git log",
    "ls *.ts; git status",
    "ls ~; git status",
    "echo x > .git/config; git log",
    "echo x >> f; git log",
    "printf -v GIT_PAGER sh; git log",
    "sort -o out x; git log",
    "LANG=C echo x; git log",
    "echo x | sh; git log",
    "echo x | tee f; git log",
    "cp a .git/config; git log",
    "grep x f | sed 's/a/b/'; git log",
    "echo x & git log",
    "echo x || git log",
]
NO_GIT = [
    "npm test",
    "echo $HOME",
    "ls *.ts",
    "cd ~ && ls",
    'rg "git stash" docs',
    'gh pr create --body "use git stash"',
    "echo $((1<<3))",
    "[ -f x ] && echo y",
    "HOME=/tmp npm test",
    "env FOO=1 bash -c 'echo $HOME'",
    "cat <<'EOF' > notes.md\nit's $(git) text\nEOF",
    'echo "unclosed',
]
RUNNERS = [
    "env git status",
    "env -C /tmp git status",
    "env -u GIT_PAGER git log",
    "xargs git log",
    "echo log | xargs git",
    "nice git log",
    "nice -n 5 git log",
    "timeout 5 git log",
    "sudo git status",
    "doas git status",
    "nohup git log",
    "command git log",
    "exec git log",
    "builtin git log",
    "eval git log",
    "eval 'git stash'",
    "bash -c 'git log'",
    "sh -c 'git stash'",
    "LANG=C bash -c 'git stash'",
    "zsh -c 'git stash'",
    "echo 'git stash' | sh",
    "bash <<'EOF'\ngit stash\nEOF",
    "sh <<EOF\ngit stash\nEOF",
    "time git log",
    "stdbuf -o0 git log",
    "setsid git log",
    "watch 'git log'",
]
COMPOUNDS = [
    "if git diff --quiet; then echo x; fi",
    "if true; then git log; fi",
    "{ git log; }",
    "for b in a; do git log; done",
    "while git log; do :; done",
    "until git log; do :; done",
    "! git diff --quiet",
    "[[ -n x ]] && git log",
    "f() { git log; }; f",
    "case x in x) git log;; esac",
    "(git status)",
    "(cd sub && git status)",
    "coproc git log",
]
COMPUTED = [
    "$(echo git) status",
    "echo $(git rev-parse HEAD)",
    "echo `git rev-parse HEAD`",
    'echo "$(git log)"',
    "sha=$(git rev-parse HEAD)",
    "x=$(echo gi''t)",
    "git log $(echo --all)",
    "git log `echo --all`",
    'git log "$REF"',
    "git log ${REF}",
    "git log $'--all'",
    "cat <(git log)",
    "git diff <(echo x)",
    "$g stash",
    "timeout 5 $g stash",
    "bash -c '$g stash'",
    "g* stash",
    "/usr/bin/gi? stash",
    "[g]it stash",
    "{git,x} stash",
    "git add *.ts",
    "git log ~user/x",
    "GIT_AUTHOR_NAME=$USER git log",
    "git commit -F - <<EOF\n$(id)\nEOF",
    "git commit -F - <<EOF\n`id`\nEOF",
    'git log "unclosed',
]
REDIRECTS = [
    "git log > out.txt",
    "git log >> out.txt",
    "git log 2> err.txt",
    "git log &> out.txt",
    "git log >| out.txt",
    "git log >&out.txt",
    "git apply < x.patch",
    "git fetch &",
    "git status || true",
    "git log |& head",
    "git status && npm test",
    "export X=1; git log",
    "X=1; git log",
    "true # x\ngit stash",
    "git log | head > x",
]
PIPES = [
    "git log | tee x",
    "git log | sh",
    "git log | xargs rm",
    "git log | sed 's/a/b/'",
    "git log | sed -n 'w x'",
    "git log | sed -n -e '1p'",
    "git log | awk '{system(\"x\")}'",
    "git log | awk '{print > \"x\"}'",
    "git log | awk '{print | \"sh\"}'",
    "git log | awk -f prog.awk",
    "git log | rg --pre sh x",
    "git log | sort -o out.txt",
    "git log | sort --output=out.txt",
    "git log | sort --compress-program=sh",
    "git log | uniq - out.txt",
    "git log | less -o log.txt",
    "git log | /usr/bin/head",
]
CD_SHAPES = [
    "cd $D && git log",
    'cd "$D" && git log',
    "cd && git log",
    "cd - && git log",
    "cd ~user/x && git log",
    "cd -P sub && git log",
    "cd sub other && git log",
    "pushd sub && git log",
    "cd sub | true; git log",
]
VARIABLES = [
    "LESSOPEN='|sh x' git log",
    "HOME=/tmp git status",
    "XDG_CONFIG_HOME=/tmp git log",
    "GIT_DIR=/tmp/x git status",
    "GIT_PAGER=sh git log",
    "GIT_PAGER=sh g''it log",
    "GIT_PAGER=sh g\\it log",
    'GIT_PAGER=sh "git" log',
    "GIT_SSH_COMMAND=x git fetch",
    "LD_PRELOAD=/tmp/x.so git log",
    "PAGER=sh git log",
]
SHARED_STATE = [
    "git stash",
    "LANG=C git stash list",
    "git add -A",
    "git add .",
    "git update-ref refs/heads/main HEAD",
    "git branch -f main HEAD",
    "git config core.hooksPath /tmp",
]
SHARED_STATE_ALLOWED = ["git config --get user.name", "git branch feature"]
BYPASSES = [
    "env -C /tmp git stash",
    "xargs git stash",
    "echo stash | xargs git",
    "if git stash; then :; fi",
    "{ git stash; }",
    "$(echo git) stash",
    "echo `git stash`",
    "echo `git -C /tmp log`",
]
CONFIG_ALLOWED = [
    "git -c user.name=x log",
    "git -c Color.UI=always log",
    "git -c advice.detachedHead=false status",
    "git -c core.pager=cat log",
    "git -c core.pager='less -R' log",
    "git -c diff.renames=true diff",
    "git -c format.pretty=oneline log",
]
CONFIG_REFUSED = [
    *(
        f"git -c {key} status"
        for key in [
            "Core.HooksPath=/tmp/x",
            "ALIAS.st=status",
            "include.path=/tmp/x",
            "includeIf.gitdir:/x.path=/tmp/x",
            "core.sshCommand=x",
            "core.fsmonitor=x",
            "Receive.DenyCurrentBranch=ignore",
            "diff.orderFile=/tmp/x",
            "format.signatureFile=/tmp/x",
        ]
    ),
    "git -cinclude.path=/tmp/x status",
    "git -c diff.external=x diff",
    "git -c diff.foo.textconv=x diff",
    "git -c core.pager='git stash; cat' log",
    "git --config-env=core.hooksPath=HOME commit -m x",
    "git --config-env core.hooksPath=HOME status",
    "git --exec-path=/tmp status",
    "git --git-dir=/tmp/x status",
    "git --work-tree=/tmp status",
    "git --namespace=x log",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/tmp git commit -m x",
    'G"IT_CONFIG_COUNT"=1 git status',
    "GIT\\_CONFIG_COUNT=1 git status",
    "declare -x GIT_CONFIG_GLOBAL=/tmp/x",
    "echo $GIT_CONFIG_COUNT",
]
PROGRAM_FLAGS = [
    "git fetch --upload-pack='sh x' origin",
    "git fetch --upload-pack=x origin",
    "git fetch --upl=x origin",
    "git fetch --exec=x origin",
    "git fetch -u x origin",
    "git fetch -qu x origin",
    "git pull --upload-pack=x origin",
    "git pull -ux origin",
    "git ls-remote --upload-pack=x origin",
    "git ls-remote --exec=x origin",
    "git ls-remote -u x origin",
    "git clone -u x origin dir",
    "git clone --upload-pack=x origin dir",
    "git grep -O x",
    "git grep -Ox x",
    "git grep -iO x",
    "git grep --open-files-in-pager=x x",
    "git grep --open x",
    "git log --output=x",
    "git log --output x",
    "git log -p --ext-diff",
    "git diff --ext-diff",
    "git diff --output=x",
    "git show --ext-diff HEAD",
    "git show --outp=x HEAD",
    "git whatchanged --output=x",
    "git -C . log --output=x",
    "git grep -e -- -O",
    "git grep -e x -- -O",
    "git fetch -o -- --upload-pack=x origin",
    "git log -- --output",
]
CLONE_FLAGS = [
    "git clone --template=x origin dir",
    "git clone --template x origin dir",
    "git clone --templ=x origin dir",
    "git clone -c core.hooksPath=x origin dir",
    "git clone -ccore.hooksPath=x origin dir",
    "git clone -qc core.hooksPath=x origin dir",
    "git clone --config core.hooksPath=x origin dir",
    "git clone --config=core.hooksPath=x origin dir",
    "git clone --conf=include.path=x origin dir",
    "git clone -c diff.external=x origin dir",
    "git clone --separate-git-dir=x origin dir",
    "git clone --separate x origin dir",
    "git clone --reference x origin dir",
    "git clone --reference-if-able=x origin dir",
    "git clone -c",
]
CLONE_FLAGS_ALLOWED = [
    "git clone origin dir",
    "git clone -c user.name=x origin dir",
    "git clone --config=color.ui=always origin dir",
    "git clone -qc user.email=x origin dir",
]
PROGRAM_FLAGS_ALLOWED = [
    "git fetch origin",
    "git fetch --prune origin",
    "git grep -n x",
    "git log --oneline",
    "git log --no-ext-diff -p",
    "git diff --textconv",
    "git show --textconv HEAD",
    "git log --output-indicator-new=x -p",
]

CASES = {
    "SIMPLE_SHAPES": (SIMPLE_SHAPES, "silent"),
    "READER_STEPS": (READER_STEPS, "silent"),
    "WRITING_READERS": (WRITING_READERS, "deny"),
    "NO_GIT": (NO_GIT, "silent"),
    "RUNNERS": (RUNNERS, "deny"),
    "COMPOUNDS": (COMPOUNDS, "deny"),
    "COMPUTED": (COMPUTED, "deny"),
    "REDIRECTS": (REDIRECTS, "deny"),
    "PIPES": (PIPES, "deny"),
    "CD_SHAPES": (CD_SHAPES, "deny"),
    "VARIABLES": (VARIABLES, "deny"),
    "SHARED_STATE": (SHARED_STATE, "deny"),
}
MORE_CASES = {
    "SHARED_STATE_ALLOWED": (SHARED_STATE_ALLOWED, "silent"),
    "BYPASSES": (BYPASSES, "deny"),
    "CONFIG_ALLOWED": (CONFIG_ALLOWED, "silent"),
    "CONFIG_REFUSED": (CONFIG_REFUSED, "deny"),
    "PROGRAM_FLAGS": (PROGRAM_FLAGS, "deny"),
    "PROGRAM_FLAGS_ALLOWED": (PROGRAM_FLAGS_ALLOWED, "silent"),
    "CLONE_FLAGS": (CLONE_FLAGS, "deny"),
    "CLONE_FLAGS_ALLOWED": (CLONE_FLAGS_ALLOWED, "silent"),
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = hook_project.make_project(tmp_path / "project")
    hook_project.git_init(root / "nested")
    (root / "sub").mkdir()
    return root


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    return hook_project.git_init(tmp_path / "outside")


def check(project: Path, group: dict[str, tuple[list[str], str]], name: str) -> None:
    commands, expected = group[name]

    got = hook_project.verdicts(project, HOOK, commands)

    assert list(zip(commands, got, strict=True)) == [(c, expected) for c in commands]


@pytest.mark.parametrize("name", list(CASES))
def test_answers_each_command_shape_of_the_group(name: str, project: Path) -> None:
    check(project, CASES, name)


@pytest.mark.parametrize("name", list(MORE_CASES))
def test_answers_each_state_and_config_case_of_the_group(name: str, project: Path) -> None:
    check(project, MORE_CASES, name)


def test_checks_the_effective_directory_of_each_git_step(project: Path, outside: Path) -> None:
    refused = [
        f"cd {outside} && git log",
        f"cd {outside}; git status",
        f"git -C {outside} status",
        f"git -C{outside} log",
        "cd /nonexistent; git log",
        f"cd {outside} && cd {project}; git log",
        "git -C nested status",
        "cd nested && git status",
        "git -C missing status",
    ]
    allowed = [f"cd {project} && git status"]

    got = hook_project.verdicts(project, HOOK, refused + allowed)

    assert got == ["deny"] * len(refused) + ["silent"]


def test_refuses_a_payload_directory_outside_the_project_of_the_hook(
    project: Path, outside: Path, tmp_path: Path
) -> None:
    def at(command: str, cwd: Path) -> str:
        return hook_project.verdicts(project, HOOK, [command], cwd)[0]

    got = [
        at("git status", outside),
        at(f"cd {project} && git status", outside),
        at("git status", tmp_path),
        at("ls", outside),
        at("git status", project / "sub"),
    ]

    assert got == ["deny", "deny", "deny", "silent", "silent"]


def test_names_the_configured_land_command_in_a_ref_move_refusal(tmp_path: Path) -> None:
    config = '[harness]\nlandCommand = "make land"\n'
    root = hook_project.make_project(tmp_path / "project", config)

    done = hook_project.run_hook(
        root, HOOK, "PreToolUse", hook_project.bash_payload("git branch -f main HEAD", root)
    )

    reason = json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Run `make land` from your worktree." in reason


def test_refuses_a_mutation_in_the_main_checkout_with_worktrees_only(tmp_path: Path) -> None:
    config = "[harness.workspace]\nworktreesOnly = true\n"
    root = hook_project.make_project(tmp_path / "project", config)

    got = hook_project.verdicts(root, HOOK, ["git commit -m x", "git status"])

    assert got == ["deny", "silent"]


def test_names_the_flag_and_the_subcommand_in_a_program_flag_refusal(project: Path) -> None:
    done = hook_project.run_hook(
        project, HOOK, "PreToolUse", hook_project.bash_payload("git fetch --upl=x origin", project)
    )

    reason = json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Do not pass `--upl=x` to `git fetch`" in reason
    assert "`--upload-pack`" in reason


def test_says_that_a_path_after_the_separator_is_checked_too(project: Path) -> None:
    done = hook_project.run_hook(
        project, HOOK, "PreToolUse", hook_project.bash_payload("git log -- --output", project)
    )

    reason = json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "also after `--`" in reason


def test_reads_only_the_listed_remote_forms_and_treats_the_rest_as_mutations(
    tmp_path: Path,
) -> None:
    config = "[harness.workspace]\nworktreesOnly = true\n"
    root = hook_project.make_project(tmp_path / "project", config)
    reads = [
        "git remote",
        "git remote -v",
        "git remote --verbose",
        "git remote show",
        "git remote show origin",
        "git remote show -n origin",
        "git remote get-url origin",
        "git remote get-url --push origin",
    ]
    mutations = [
        "git remote add evil x",
        "git remote set-url origin x",
        "git remote remove origin",
        "git remote rename origin x",
        "git remote prune origin",
        "git remote update",
        "git remote show origin extra",
        "git remote get-url",
        "git remote -v add evil x",
    ]

    got = hook_project.verdicts(root, HOOK, reads + mutations)

    assert got == ["silent"] * len(reads) + ["deny"] * len(mutations)


def test_allows_a_remote_mutation_in_its_own_checkout(project: Path) -> None:
    got = hook_project.verdicts(project, HOOK, ["git remote add mirror x"])

    assert got == ["silent"]


def test_allows_each_read_form_of_git_config_and_refuses_each_write(project: Path) -> None:
    reads = [
        "git config user.name",
        f"git -C {project} config user.name",
        "git config --global user.email",
        "git config --get-all user.name",
        "git config --get-regexp user",
        "git config -l",
        "git config --list --show-origin",
        "git config --type=bool core.bare",
        "git config --get user.name",
        "git config get user.name",
        "git config list",
    ]
    writes = [
        "git config user.name x",
        "git config --add user.name x",
        "git config --unset user.name",
        "git config --unset-a user.name",
        "git config --replace-all user.name x",
        "git config --remove-section user",
        "git config --rename-section user x",
        "git config -e",
        "git config --edit",
        "git config set user.name x",
        "git config unset user.name",
        "git config --get user.name --unset",
        "git config edit",
        "git config --global edit",
        "git config rename-section a b",
        "git config remove-section a",
    ]

    got = hook_project.verdicts(project, HOOK, reads + writes)

    expected = [(c, "silent") for c in reads] + [(c, "deny") for c in writes]
    assert list(zip(reads + writes, got, strict=True)) == expected


@pytest.fixture
def worktrees(project: Path) -> tuple[Path, Path]:
    """Two worktrees of the project: the caller's own and a peer's."""
    identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
    subprocess.run(
        ["git", "-C", str(project), *identity, "commit", "--allow-empty", "-q", "-m", "init"],
        check=True,
    )
    paths = (project / ".claude/worktrees/own", project / ".claude/worktrees/peer")
    for path in paths:
        subprocess.run(
            ["git", "-C", str(project), "worktree", "add", "-q", "-b", path.name, str(path)],
            check=True,
        )
    return paths


def test_lets_a_session_in_a_worktree_run_git_in_a_sibling_worktree(
    project: Path, worktrees: tuple[Path, Path], outside: Path
) -> None:
    own, peer = worktrees
    hook_project.install_hooks(own)

    got = [
        hook_project.verdicts(own, HOOK, ["git status"], peer)[0],
        hook_project.verdicts(own, HOOK, [f"git -C {peer} log -1"], own)[0],
        hook_project.verdicts(own, HOOK, ["git status"], project)[0],
        hook_project.verdicts(own, HOOK, ["git status"], outside)[0],
    ]

    assert got == ["silent", "silent", "silent", "deny"]


def test_lets_git_run_in_a_repository_that_the_config_lists(tmp_path: Path, outside: Path) -> None:
    other = hook_project.git_init(tmp_path / "other")
    config = f'[harness.workspace]\nrepos = ["{outside}"]\n'
    root = hook_project.make_project(tmp_path / "project", config)

    got = [
        hook_project.verdicts(root, HOOK, ["git status"], outside)[0],
        hook_project.verdicts(root, HOOK, [f"git -C {outside} log -1"], root)[0],
        hook_project.verdicts(root, HOOK, [f"cd {outside} && git status"], root)[0],
        hook_project.verdicts(root, HOOK, ["git status"], other)[0],
        hook_project.verdicts(root, HOOK, [f"git -C {other} status"], root)[0],
    ]

    assert got == ["silent", "silent", "silent", "deny", "deny"]


def test_lets_a_mutation_run_in_the_main_checkout_of_a_listed_repository(
    tmp_path: Path, outside: Path
) -> None:
    root = hook_project.make_project(
        tmp_path / "project", f'[harness.workspace]\nrepos = ["{outside}"]\n'
    )

    got = hook_project.verdicts(
        root,
        HOOK,
        [
            f"git -C {outside} commit -m x a.txt",
            f"cd {outside} && git switch -c fix/x && git merge --ff-only - && git branch -d fix/x",
            f"git -C {outside} config core.hooksPath x",
        ],
    )

    assert got == ["silent", "silent", "deny"]


def test_takes_a_glob_entry_as_a_pattern_for_the_main_checkout(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    deep = hook_project.git_init(repos / "group" / "deep")
    vendored = hook_project.git_init(deep / "vendor" / "lib")
    elsewhere = hook_project.git_init(tmp_path / "elsewhere")
    root = hook_project.make_project(
        tmp_path / "project", f'[harness.workspace]\nrepos = ["{repos}/*"]\n'
    )

    got = hook_project.verdicts(
        root,
        HOOK,
        [
            f"git -C {deep} commit -m x a.txt",
            f"git -C {vendored} commit -m x a.txt",
            f"git -C {elsewhere} commit -m x a.txt",
        ],
    )

    assert got == ["silent", "deny", "deny"]


def test_trusts_each_repository_under_projects_in_home_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    other = hook_project.git_init(tmp_path / "Projects" / "other")
    root = hook_project.make_project(tmp_path / "Projects" / "project")

    got = hook_project.verdicts(
        root,
        HOOK,
        [
            "cd ~/Projects/other && git commit -m x a.txt",
            "git -C ~/Projects/other commit -m x a.txt",
            "cd ~ && git status",
            f"git -C {other} log -1",
            "git -C ~/elsewhere status",
            "git -C ~user/x status",
        ],
    )

    assert got == ["silent", "silent", "deny", "silent", "deny", "deny"]


def test_lets_a_worktree_command_name_only_the_callers_own_worktree(
    project: Path, worktrees: tuple[Path, Path]
) -> None:
    own, peer = worktrees
    allowed = [
        f"git worktree lock --reason x {own}",
        "git worktree unlock .",
        "git worktree repair",
        f"git worktree repair {own}",
        f"git worktree move {own} {project}/.claude/worktrees/moved",
        "git worktree list",
    ]
    refused = [
        f"git worktree remove {peer}",
        f"git worktree remove --force {peer}",
        f"git worktree lock {peer}",
        f"git worktree unlock {peer}",
        f"git worktree repair {own} {peer}",
        f"git worktree move {peer} {project}/.claude/worktrees/moved",
        f"git worktree move {own} {project.parent}/elsewhere",
        f"git worktree remove {project}",
        f"git worktree remove {project}/missing",
    ]

    got = hook_project.verdicts(project, HOOK, allowed + refused, own)

    expected = [(c, "silent") for c in allowed] + [(c, "deny") for c in refused]
    assert list(zip(allowed + refused, got, strict=True)) == expected


def test_takes_only_the_exact_path_of_the_own_worktree_and_a_new_move_target(
    project: Path, worktrees: tuple[Path, Path]
) -> None:
    own, _ = worktrees
    home = project / ".claude" / "worktrees"
    allowed = [
        f"git worktree lock {own}",
        f"git worktree remove {own}",
        "git worktree lock .",
        f"git worktree move {own} {home}/new-name",
    ]
    refused = [
        "git worktree lock own",
        "git worktree remove peer",
        f"git worktree lock {own}/../own",
        f"git worktree move {own} {home}/../worktrees/new-name",
        f"git worktree move {own} {home}/peer",
        f"git worktree move {own} {home}/a/b",
        f"git worktree move {own} {home}/bad:name",
        f"ls && git worktree move {own} {home}/new-name",
        f"cd {own} && git worktree move {own} {home}/new-name",
    ]
    from_main = [f"git worktree remove {own}", "git worktree remove peer"]

    got = hook_project.verdicts(project, HOOK, allowed + refused, own)
    got_main = hook_project.verdicts(project, HOOK, from_main, project)

    expected = [(c, "silent") for c in allowed] + [(c, "deny") for c in refused]
    assert list(zip(allowed + refused, got, strict=True)) == expected
    assert got_main == ["deny", "deny"]


def test_refuses_a_worktree_path_or_move_target_through_a_link(
    project: Path, worktrees: tuple[Path, Path]
) -> None:
    folder = project / ".claude" / "worktrees"
    real = project / ".claude" / "real"
    folder.rename(real)
    folder.symlink_to(real)
    commands = [
        f"git worktree move {real}/own {folder}/new-name",
        f"git worktree lock {folder}/own",
        f"git worktree lock {real}/own",
    ]

    got = hook_project.verdicts(project, HOOK, commands, worktrees[0])

    assert got == ["deny", "deny", "silent"]


def test_lets_a_session_change_a_worktree_that_it_owns_from_another_checkout(
    tmp_path: Path, project: Path, worktrees: tuple[Path, Path]
) -> None:
    own, peer = worktrees
    marker = Path(
        subprocess.run(
            ["git", "-C", str(own), "rev-parse", "--absolute-git-dir"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    (marker / "lw-owner").write_text("session-a\n", encoding="utf-8")
    other = hook_project.make_project(tmp_path / "other")
    commits = [
        f"git -C {own} commit --allow-empty -m x",
        f"git -C {peer} commit --allow-empty -m x",
    ]

    got = [
        hook_project.verdicts(project, HOOK, commits, project, "session-a"),
        hook_project.verdicts(project, HOOK, commits[:1], project, "session-b"),
        hook_project.verdicts(project, HOOK, commits[:1], project),
        hook_project.verdicts(other, HOOK, [f"git -C {project} commit -m x"], other, "session-a"),
    ]

    assert got == [["silent", "deny"], ["deny"], ["deny"], ["deny"]]
