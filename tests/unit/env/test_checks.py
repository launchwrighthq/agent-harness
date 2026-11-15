from pathlib import Path

import pytest

from agent_harness.env import command, stores

MASTER = "# Generated top block.\n\n# App\nAPP_URL=\nAPP_TOKEN=\n\n# Mail\nMAIL_KEY=\n"
DECLARED = (
    '[keys.APP_URL]\nblock = "App"\n'
    '[keys.APP_TOKEN]\nblock = "App"\nrequired = true\nsecret = true\n'
    '[keys.MAIL_KEY]\nblock = "Mail"\nrequired = true\nenvironments = ["production"]\n'
)


class Git:
    """A fake `git` that lists `tracked` for `ls-files` and records each call."""

    def __init__(self, tracked: list[str], code: int = 0) -> None:
        self.tracked = tracked
        self.code = code
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], stdin: str | None) -> stores.Done:
        self.calls.append(argv)
        return stores.Done(self.code, "\0".join(self.tracked) + "\0", "not a git repository")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".env.example").write_text(MASTER, encoding="utf-8")
    (root / "env.toml").write_text(DECLARED, encoding="utf-8")
    return root


def check(repo: Path, git: Git, *extra: str) -> tuple[int, list[str]]:
    out: list[str] = []
    code = command.main(
        ["--root", str(repo), *extra, "check"], out=out.append, err=out.append, runner=git
    )
    return code, out


def test_passes_a_repository_with_every_key_declared_and_set(repo: Path) -> None:
    (repo / ".env.production").write_text(
        "# App\nAPP_TOKEN=token-value-x1\n\n# Mail\nMAIL_KEY=mail-value-x2\n", encoding="utf-8"
    )
    (repo / "app.py").write_text("import os\nos.getenv('APP_URL')\nos.getenv('HOME')\n")

    code, out = check(repo, Git([".env.example"]), "--declarations", "env.toml")

    assert (code, out) == (0, ["env check: no finding"])


def test_names_each_finding_by_file_key_and_line(repo: Path) -> None:
    (repo / ".env.production").write_text("# App\nAPP_TOKEN=\nSTRAY_KEY=x\n", encoding="utf-8")
    (repo / ".env.local").write_text("# App\nAPP_TOKEN=token-value-x3\n", encoding="utf-8")
    (repo / "apps" / "api").mkdir(parents=True)
    (repo / "apps" / "api" / ".env").write_text("A=1\n", encoding="utf-8")
    (repo / "node_modules" / "x").mkdir(parents=True)
    (repo / "node_modules" / "x" / ".env").write_text("A=1\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "main.ts").write_text("const a = process.env.NEW_KEY;\n", encoding="utf-8")
    git = Git([".env.example", ".env.local", "src/main.ts"])

    code, out = check(repo, git, "--declarations", "env.toml")

    assert code == 1
    assert out == [
        "src/main.ts:1 NEW_KEY: code reads it, and no master or declaration names it",
        ".env.production: STRAY_KEY is in no master and no declaration",
        ".env.production: required key APP_TOKEN is empty",
        ".env.production: required key MAIL_KEY is missing",
        "apps/api/.env: an env file outside the master folder",
        ".env.local: a live env file that git tracks. Run git rm --cached .env.local and add "
        "it to .gitignore.",
        "6 findings",
    ]
    assert git.calls == [["git", "-C", str(repo), "ls-files", "-z"]]
    assert "token-value-x3" not in "\n".join(out)


def test_accepts_an_env_file_in_a_folder_of_the_dirs_setting(repo: Path) -> None:
    (repo / ".agents").mkdir()
    (repo / ".agents" / "harness.toml").write_text('[env]\ndirs = ["apps/api"]\n')
    (repo / "apps" / "api").mkdir(parents=True)
    (repo / "apps" / "api" / ".env.example").write_text("# Api\nA=\n", encoding="utf-8")

    assert check(repo, Git([])) == (0, ["env check: no finding"])


def test_says_when_git_cannot_list_the_tracked_files(repo: Path) -> None:
    code, out = check(repo, Git([], code=128))

    assert (code, out) == (
        1,
        [
            "git ls-files failed (exit 128): not a git repository",
            "1 findings",
        ],
    )
