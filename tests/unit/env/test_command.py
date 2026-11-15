from pathlib import Path

import pytest

from agent_harness.env import command, envfile

MASTER = (
    "# Generated top block.\n#\n# Every other file follows it.\n\n"
    "# App\n# The public URL.\nAPP_URL=\nAPP_TOKEN=\n\n"
    "# Mail\nMAIL_KEY=\n"
)
TOKEN = "token-value-lima-58"
MAIL = "mail-value-oscar-17"
LOCAL = f"# App\nAPP_TOKEN={TOKEN}\n\n# Mail\nMAIL_KEY={MAIL}\n"


class Run:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.out: list[str] = []
        self.err: list[str] = []

    def __call__(self, *argv: str, stdin: str = "") -> int:
        self.out, self.err = [], []
        return command.main(
            ["--root", str(self.root), *argv],
            out=self.out.append,
            err=self.err.append,
            stdin=lambda: stdin,
        )

    def text(self) -> str:
        return "\n".join(self.out + self.err)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".env.example").write_text(MASTER, encoding="utf-8")
    (root / ".env.local").write_text(LOCAL, encoding="utf-8")
    return root


def test_prints_a_masked_value_and_reveals_it_only_on_request(repo: Path) -> None:
    run = Run(repo)

    assert run("get", "APP_TOKEN", ".env.local") == 0
    assert run.out == [
        f"APP_TOKEN in .env.local: set, {len(TOKEN)} chars, sha256:" + run.out[0][-8:]
    ]
    assert TOKEN not in run.text()
    assert run("get", "APP_TOKEN", ".env.local", "--reveal") == 0
    assert run.out == [TOKEN]


def test_prints_a_connection_string_without_its_userinfo(repo: Path) -> None:
    (repo / ".env.local").write_text(
        "APP_URL=postgres://app:pw-lima-58@db.example:5432/app\n", encoding="utf-8"
    )
    run = Run(repo)

    assert run("get", "APP_URL", ".env.local") == 0

    assert run.out == ["APP_URL in .env.local: postgres://***:***@db.example:5432/app"]


def test_prints_a_short_number_only_for_a_key_declared_plain(repo: Path) -> None:
    (repo / "env.toml").write_text(
        '[keys.PORT]\nsecret = false\n[keys.CODE]\nsecret = true\n[keys.SIZE]\nblock = "App"\n',
        encoding="utf-8",
    )
    (repo / ".env.local").write_text(
        "PORT=3000\nCODE=4242\nSIZE=12\nPIN=123456\n", encoding="utf-8"
    )
    run = Run(repo)

    shown = []
    for key in ("PORT", "CODE", "SIZE", "PIN"):
        run("--declarations", "env.toml", "get", key, ".env.local")
        shown.append(run.out[0].split(": ", 1)[1])

    assert shown[0] == "3000"
    assert [s.startswith("set, ") for s in shown[1:]] == [True, True, True]


def test_sets_a_value_from_standard_input_in_its_master_place(repo: Path) -> None:
    run = Run(repo)

    assert run("set", "APP_URL", ".env.local", "--stdin", stdin="https://app.test\n") == 0

    assert run.out == ["set APP_URL in .env.local (added)"]
    assert (repo / ".env.local").read_text(encoding="utf-8") == (
        f"# App\nAPP_URL=https://app.test\nAPP_TOKEN={TOKEN}\n\n# Mail\nMAIL_KEY={MAIL}\n"
    )


def test_refuses_to_write_a_value_into_a_template(repo: Path) -> None:
    run = Run(repo)

    assert run("set", "APP_URL", ".env.example", "--value", "x-value") == 1

    assert run.err[0].startswith("ah env: .env.example is a template.")
    assert (repo / ".env.example").read_text(encoding="utf-8") == MASTER


def test_exits_2_on_an_unknown_option_without_repeating_any_argument(repo: Path) -> None:
    run = Run(repo)

    assert run("set", "APP_URL", ".env.local", "--valu=secret-arg-value") == 2

    assert run.err[0] == "ah env: unknown option --valu"
    assert "secret-arg-value" not in run.text()


def test_resolves_a_live_file_of_the_main_checkout_from_a_worktree(tmp_path: Path) -> None:
    main = tmp_path / "main"
    git_dir = main / ".git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    (main / ".env.local").write_text(LOCAL, encoding="utf-8")
    worktree = main / ".claude" / "worktrees" / "feature"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
    (worktree / ".env.example").write_text(MASTER, encoding="utf-8")
    run = Run(worktree)

    assert run("set", "APP_URL", ".env.local", "--value", "https://wt.test") == 0

    assert run.out == ["set APP_URL in .env.local (main checkout) (added)"]
    assert "APP_URL=https://wt.test" in (main / ".env.local").read_text(encoding="utf-8")
    assert not (worktree / ".env.local").exists()


def test_reports_the_status_of_each_file_against_the_master(repo: Path) -> None:
    run = Run(repo)

    assert run("status") == 0

    assert run.out == [
        ".env.example: 3 keys (the master)",
        ".env.local: 2 keys, 0 empty, 1 missing against the master, 0 not in the master",
        "  missing: APP_URL",
    ]


def test_reports_a_file_with_its_own_master_against_that_master(repo: Path) -> None:
    (repo / ".env.ci.example").write_text("", encoding="utf-8")
    (repo / ".env.ci").write_text("", encoding="utf-8")
    (repo / ".env.seed.example").write_text("# Seed\nSEED_KEY=\n", encoding="utf-8")
    (repo / ".env.seed").write_text("# Seed\nSEED_KEY=s1\n", encoding="utf-8")
    run = Run(repo)

    assert run("status", ".env.ci", ".env.seed") == 0

    assert run.out == [
        ".env.example: 3 keys (the master)",
        ".env.ci: 0 keys, 0 empty, 0 missing against .env.ci.example, "
        "0 not in .env.ci.example",
        ".env.seed: 1 keys, 0 empty, 0 missing against .env.seed.example, "
        "0 not in .env.seed.example",
    ]


def test_prints_a_key_by_file_matrix_with_the_apps_of_each_key(repo: Path) -> None:
    (repo / "env.toml").write_text(
        '[keys.APP_URL]\nblock = "App"\n[keys.APP_TOKEN]\nblock = "App"\napps = ["api", "web"]\n',
        encoding="utf-8",
    )
    (repo / ".env.staging").write_text("# App\nAPP_URL=\n\n# Other\nEXTRA_KEY=x1\n")
    run = Run(repo)

    assert run("--declarations", "env.toml", "status", "--matrix") == 0

    assert run.out == [
        "KEY        APP      .env.local  .env.staging",
        "APP_URL    all      -           empty",
        "APP_TOKEN  api,web  set         -",
        "MAIL_KEY   -        set         -",
        "EXTRA_KEY  -        -           set",
    ]
    assert TOKEN not in run.text()


def test_merges_only_the_declared_secret_keys_into_a_new_environment(repo: Path) -> None:
    (repo / "env.toml").write_text(DECLARED, encoding="utf-8")
    (repo / ".env.local").write_text(
        f"# App\nAPP_URL=https://local.test\nAPP_TOKEN={TOKEN}\n\n# Mail\nMAIL_KEY={MAIL}\n"
    )
    run = Run(repo)

    code = run("--declarations", "env.toml", "merge", ".env.local", ".env.qa", "--secrets-only")
    refused = run("merge", ".env.local", ".env.qa2", "--secrets-only")

    assert code == 0
    assert (repo / ".env.qa").read_text(encoding="utf-8") == (
        f"# App\nAPP_TOKEN={TOKEN}\n\n# Mail\nMAIL_KEY={MAIL}\n"
    )
    assert refused == 1
    assert run.err == [
        "ah env: --secrets-only reads the secret field of the declarations, and none are configured"
    ]
    assert TOKEN not in run.text()


def test_refuses_a_rename_onto_a_key_that_exists(repo: Path) -> None:
    run = Run(repo)

    assert run("rename", "APP_TOKEN", "MAIL_KEY") == 1

    assert run.err == ["ah env: MAIL_KEY is already in .env.example; nothing renamed"]
    assert (repo / ".env.local").read_text(encoding="utf-8") == LOCAL


def test_finds_a_key_in_files_declarations_and_workflows(repo: Path) -> None:
    (repo / "env.toml").write_text(
        '[keys.APP_TOKEN]\nblock = "App"\napps = ["api"]\n', encoding="utf-8"
    )
    workflow = repo / ".github" / "workflows" / "deploy.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("env:\n  APP_TOKEN: ${{ secrets.APP_TOKEN }}\n", encoding="utf-8")
    out: list[str] = []

    code = command.main(
        ["--root", str(repo), "--declarations", "env.toml", "where", "APP_TOKEN"], out=out.append
    )

    assert code == 0
    assert out == [
        "APP_TOKEN",
        "  empty in .env.example",
        "  set in .env.local",
        f"  declared in {repo / 'env.toml'}, read by api",
        "  named in .github/workflows/deploy.yml:2",
    ]


def test_shows_each_code_line_that_reads_a_key(repo: Path) -> None:
    (repo / "src").mkdir()
    (repo / "src" / "main.ts").write_text(
        "export const t = process.env.APP_TOKEN;\n", encoding="utf-8"
    )
    (repo / "worker.py").write_text("import os\n\nos.getenv('APP_TOKEN')\n", encoding="utf-8")
    run = Run(repo)

    assert run("where", "APP_TOKEN") == 0

    assert run.out[-2:] == ["  read in src/main.ts:1", "  read in worker.py:3"]


def test_lists_the_keys_an_app_reads_from_the_declarations(repo: Path) -> None:
    (repo / "env.toml").write_text(
        '[keys.APP_TOKEN]\nblock = "App"\napps = ["api"]\nsecret = true\n'
        '[keys.MAIL_KEY]\nblock = "Mail"\napps = ["worker"]\n',
        encoding="utf-8",
    )
    out: list[str] = []

    code = command.main(
        ["--root", str(repo), "--declarations", "env.toml", "keys", "api"], out=out.append
    )

    assert code == 0
    assert out == [f"APP_TOKEN secret {repo / 'env.toml'}"]


def test_refuses_a_push_that_drops_a_key_only_the_store_holds(repo: Path) -> None:
    (repo / ".agents").mkdir()
    (repo / ".agents" / "harness.toml").write_text(
        '[env.store]\nkind = "file"\npath = "vault/.env.{env}"\nfile = ".env.local"\n',
        encoding="utf-8",
    )
    (repo / "vault").mkdir()
    (repo / "vault" / ".env.production").write_text("OLD_KEY=old-store-value\n", encoding="utf-8")
    run = Run(repo)

    assert run("push", "production", "--yes") == 1
    assert "the store holds keys that .env.local lacks: OLD_KEY" in run.err[0]
    assert run("push", "production", "--yes", "--drop", "OLD_KEY") == 0

    stored = (repo / "vault" / ".env.production").read_text(encoding="utf-8")
    assert stored == f"APP_TOKEN={TOKEN}\nMAIL_KEY={MAIL}\n"
    marker = repo / ".env.local.pushed-store"
    assert marker.read_text(encoding="utf-8") == envfile.content_digest(LOCAL) + "\n"
    assert TOKEN not in run.text()


def test_writes_a_template_from_the_master_and_keeps_an_own_pair_template(repo: Path) -> None:
    (repo / ".env.ci").write_text("CI_TOKEN=ci-value-kilo-41\n", encoding="utf-8")
    (repo / ".env.ci.example").write_text("# Ci\nCI_TOKEN=\n", encoding="utf-8")
    run = Run(repo)

    assert run("template") == 0

    assert run.out == [
        "kept .env.ci.example: it is the master of .env.ci",
        "wrote .env.local.example from .env.example: 3 keys, values empty",
    ]
    assert (repo / ".env.local.example").read_text(encoding="utf-8") == (
        "# App\nAPP_URL=\nAPP_TOKEN=\n\n# Mail\nMAIL_KEY=\n"
    )
    assert (repo / ".env.ci.example").read_text(encoding="utf-8") == "# Ci\nCI_TOKEN=\n"


def test_creates_a_file_from_the_master_and_refuses_an_existing_one(repo: Path) -> None:
    run = Run(repo)

    assert run("init", ".env.staging") == 0
    assert run("init", ".env.local") == 1

    assert (repo / ".env.staging").read_text(encoding="utf-8") == (
        "# App\nAPP_URL=\nAPP_TOKEN=\n\n# Mail\nMAIL_KEY=\n"
    )
    assert run.err[0].startswith("ah env: .env.local exists, and init creates a new file.")
    assert (repo / ".env.local").read_text(encoding="utf-8") == LOCAL


DECLARED = (
    '[keys.APP_URL]\nblock = "App"\ntype = "url"\n'
    '[keys.APP_TOKEN]\nblock = "App"\napps = ["api"]\nsecret = true\n'
    '[keys.MAIL_KEY]\nblock = "Mail"\napps = ["api"]\nsecret = true\n'
    '[keys.WORKERS]\nblock = "Queue"\ntype = "int"\n'
    '[environments.dev]\nWORKERS = "2"\n'
)


def test_refuses_to_set_an_undeclared_key_a_wrong_type_or_into_a_stray_file(
    repo: Path,
) -> None:
    (repo / "env.toml").write_text(DECLARED, encoding="utf-8")
    (repo / ".env.stray").write_text("STRAY=1\n", encoding="utf-8")
    run = Run(repo)
    declared = ("--declarations", "env.toml", "set")

    results = []
    for argv, stdin in [
        (("NOPE", ".env.local", "--value", "x"), ""),
        (("WORKERS", ".env.local", "--value", "many"), ""),
        (("APP_URL", ".env.local", "--stdin"), "https://a.test\nb\n"),
        (("WORKERS", ".env.stray", "--value", "3"), ""),
    ]:
        results.append((run(*declared, *argv, stdin=stdin), run.err))

    assert results == [
        (1, ["ah env: NOPE is not declared in any declaration file"]),
        (1, ["ah env: the value of WORKERS does not match type 'int'"]),
        (1, ["ah env: the value of APP_URL must be one line"]),
        (
            1,
            [
                "ah env: .env.stray holds keys that no declaration file declares: STRAY. "
                "Declare or remove them first."
            ],
        ),
    ]
    assert (repo / ".env.local").read_text(encoding="utf-8") == LOCAL
    assert (repo / ".env.stray").read_text(encoding="utf-8") == "STRAY=1\n"


def test_slices_an_app_with_the_declared_values_of_an_environment(repo: Path) -> None:
    (repo / "env.toml").write_text(DECLARED, encoding="utf-8")
    (repo / ".env.example").unlink()
    run = Run(repo)
    declared = ("--declarations", "env.toml", "slice")

    listed = run(*declared, "client", ".env.local", "--environment", "dev")
    listed_out = run.out
    written = run(*declared, "api", ".env.local", "--to", ".env.api", "--environment", "dev")

    assert (listed, listed_out) == (0, ["WORKERS"])
    assert (written, run.out) == (0, ["wrote 3 keys of app api to .env.api"])
    assert (repo / ".env.api").read_text(encoding="utf-8") == (
        f"# App\nAPP_TOKEN={TOKEN}\n\n# Mail\nMAIL_KEY={MAIL}\n\n# Queue\nWORKERS=2\n"
    )
    assert TOKEN not in run.text()


STRAY = "APP_TOKEN=stray-value-papa-33\n"
NOT_ENV_NAMES = [
    (("set", "APP_URL", "notes.txt", "--value", "x-value"), "notes.txt"),
    (("copy", "APP_TOKEN", ".env.local", "config.py"), "config.py"),
    (("move", "APP_TOKEN", ".env.local", ".env.local.bak"), ".env.local.bak"),
    (("merge", ".env.local", "settings.json"), "settings.json"),
    (("unset", "APP_TOKEN", "stray.cfg"), "stray.cfg"),
    (("init", "app.cfg"), "app.cfg"),
    (("template", "stray.cfg"), "stray.cfg.example"),
    (("rename", "APP_TOKEN", "NEW_TOKEN", "stray.cfg"), "stray.cfg"),
    (
        ("--declarations", "env.toml", "slice", "api", ".env.local", "--to", "out.txt"),
        "out.txt",
    ),
    (
        ("set", "APP_URL", ".env.local.pushed-store", "--value", "x-value"),
        ".env.local.pushed-store",
    ),
]


@pytest.mark.parametrize(("argv", "target"), NOT_ENV_NAMES)
def test_refuses_to_write_a_file_that_is_not_an_env_file(
    repo: Path, argv: tuple[str, ...], target: str
) -> None:
    (repo / "env.toml").write_text(DECLARED, encoding="utf-8")
    (repo / "stray.cfg").write_text(STRAY, encoding="utf-8")
    run = Run(repo)

    assert run(*argv) == 1

    assert run.err == [
        f"ah env: {target} is not an env file. ah env writes only a file named .env or "
        ".env.<name>, and refuses a backup, a temp file or a push marker."
    ]
    assert (repo / "stray.cfg").read_text(encoding="utf-8") == STRAY
    assert (repo / ".env.local").read_text(encoding="utf-8") == LOCAL
    assert not (repo / target).exists() or target == "stray.cfg"


OUTSIDE = [
    ("set", "APP_URL", "{out}/.env.out", "--value", "x-value"),
    ("copy", "APP_TOKEN", ".env.local", "{out}/.env.out"),
    ("move", "APP_TOKEN", ".env.local", "{out}/.env.out"),
    ("merge", ".env.local", "{out}/.env.out"),
    ("unset", "APP_TOKEN", "{out}/.env.out"),
    ("init", "{out}/.env.new"),
    ("template", "{out}/.env.out"),
    ("rename", "APP_TOKEN", "NEW_TOKEN", "{out}/.env.out"),
    ("--declarations", "env.toml", "slice", "api", ".env.local", "--to", "{out}/.env.new"),
    ("set", "APP_URL", ".env.link", "--value", "x-value"),
    ("copy", "APP_TOKEN", ".env.local", "../outside/.env.out"),
]


@pytest.mark.parametrize("argv", OUTSIDE)
def test_refuses_to_write_a_file_that_resolves_outside_the_repository(
    repo: Path, tmp_path: Path, argv: tuple[str, ...]
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".env.out").write_text(STRAY, encoding="utf-8")
    (repo / ".env.link").symlink_to(outside / ".env.out")
    (repo / "env.toml").write_text(DECLARED, encoding="utf-8")
    run = Run(repo)

    assert run(*(word.replace("{out}", str(outside)) for word in argv)) == 1

    assert len(run.err) == 1
    assert f"outside the repository root {repo.resolve()}" in run.err[0]
    assert run.err[0].endswith("ah env writes only inside the repository.")
    assert sorted(p.name for p in outside.iterdir()) == [".env.out"]
    assert (outside / ".env.out").read_text(encoding="utf-8") == STRAY
    assert (repo / ".env.local").read_text(encoding="utf-8") == LOCAL


def test_slices_a_family_member_that_no_list_names_for_the_apps_of_its_family(
    repo: Path,
) -> None:
    (repo / "env.toml").write_text(
        '[keys.APP_TOKEN]\nblock = "App"\n\n'
        '[families."API_KEY_<NAME>"]\nblock = "Keys"\napps = ["worker"]\n',
        encoding="utf-8",
    )
    (repo / ".env.local").write_text("APP_TOKEN=a\nAPI_KEY_GAMMA=b\n", encoding="utf-8")
    run = Run(repo)

    worker = run("--declarations", "env.toml", "slice", "worker", ".env.local")
    worker_out = run.out
    api = run("--declarations", "env.toml", "slice", "api", ".env.local")

    assert (worker, worker_out) == (0, ["APP_TOKEN", "API_KEY_GAMMA"])
    assert (api, run.out) == (0, ["APP_TOKEN"])


API_MASTER = "# Generated api block.\n\n# Api\nAPI_A=\nAPI_B=\n"
API_LOCAL = "# Api\nAPI_B=b-value-1\nAPI_A=a-value-2\n"


def api_folder(repo: Path) -> Path:
    folder = repo / "apps" / "api"
    folder.mkdir(parents=True)
    (folder / ".env.example").write_text(API_MASTER, encoding="utf-8")
    (folder / ".env.local").write_text(API_LOCAL, encoding="utf-8")
    return folder


def test_checks_the_layout_of_each_folder_against_its_own_master(repo: Path) -> None:
    api_folder(repo)
    run = Run(repo)

    assert run("layout", ".", "apps/api") == 1

    assert run.out == ["apps/api/.env.local:3 L4 key API_A is out of the master's key order"]


def test_shows_the_reorder_of_a_dry_run_and_writes_nothing(repo: Path) -> None:
    folder = api_folder(repo)
    run = Run(repo)

    code = run("layout", "apps/api", "--fix", "--dry-run")

    assert (code, run.out[0]) == (1, "would reorder apps/api/.env.local to its master")
    assert (folder / ".env.local").read_text(encoding="utf-8") == API_LOCAL
    assert run("layout", "--dry-run") == 2
    assert run.err[0] == "ah env: --dry-run shows what --fix would write; pass --fix too"


def test_checks_the_folders_of_the_dirs_setting_by_default(repo: Path) -> None:
    folder = api_folder(repo)
    (repo / ".agents").mkdir()
    (repo / ".agents" / "harness.toml").write_text('[env]\ndirs = ["apps/api"]\n')
    run = Run(repo)

    fixed = run("layout", "--fix")

    assert (fixed, run.out) == (
        0,
        ["reordered apps/api/.env.local to its master", "4 env files follow the layout"],
    )
    assert (folder / ".env.local").read_text(encoding="utf-8") == (
        "# Api\nAPI_A=a-value-2\nAPI_B=b-value-1\n"
    )
