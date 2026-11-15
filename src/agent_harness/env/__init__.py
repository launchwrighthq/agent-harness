"""The env engine and its store backends: `ah env <subcommand>` (docs/env.md)."""

from __future__ import annotations

from agent_harness import config

config.register_defaults(
    "env",
    {
        "master": ".env.example",
        # Empty: every live `.env*` file beside the master.
        "files": [],
        # TOML files of [keys.<NAME>] and [environments.<env>] tables, merged in order.
        "declarations": [],
        "layout": True,
        # More folders that hold env files with a master of their own, `<dir>/.env.example`.
        "dirs": [],
        # The files and folders that `ah env where` searches for a key name.
        "scan": [
            ".github/workflows",
            "infra",
            "deploy",
            "terraform",
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        ],
        # The code files whose env reads `where` shows and `check` compares with the declarations.
        "readers": [
            *("*.py", "*.js", "*.jsx", "*.mjs", "*.cjs"),
            *("*.ts", "*.tsx", "*.mts", "*.cts", "*.vue", "*.svelte"),
        ],
        # Folder names that the reader scan and the stray file check leave out at any depth.
        "skip": [
            *("node_modules", ".venv", "venv", "dist", "build", ".claude", "__pycache__"),
            *(".next", ".nuxt", ".turbo", "coverage", ".tox", ".mypy_cache"),
        ],
        # Keys that code reads and no project declares: the shell and the runtime set them.
        "ignore": [
            *("CI", "HOME", "LANG", "NODE_ENV", "PATH"),
            *("PWD", "SHELL", "TERM", "TMPDIR", "TZ"),
        ],
        "store": {"kind": "", "marker": "store", "file": ".env.{env}"},
        # Named stores, each a table with `name` and the keys of `store`. `--store NAME` picks one.
        "stores": [],
        # The environments whose push needs `--yes --confirm <env>`, and whose `run` always asks.
        "protected": [],
        # The bare program names that `ah env run` may start without a question from the guards.
        "runCommands": [],
    },
)


def main(argv: list[str]) -> int:
    from agent_harness.env import command

    return command.main(argv)
