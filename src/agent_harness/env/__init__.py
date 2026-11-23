"""The env engine and its store backends: `ah env <subcommand>` (docs/env.md)."""

from __future__ import annotations

import os
import tempfile

from agent_harness import config

config.register_defaults(
    "env",
    {
        "master": ".env.example",
        # Empty: every live `.env*` file beside the master.
        "files": [],
        # TOML files of [keys.<NAME>] and [environments.<env>] tables, merged in order.
        "declarations": [],
        # A shell command that prints one declared key name per line. Empty: no command.
        "declarationsCommand": "",
        "layout": True,
        # Live files that always take their own `<file>.example` as master.
        "paired": [".env.ci"],
        # Values that count as no value for a declared secret, in any case.
        "placeholders": ["changeme", "xxx"],
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
        # Folders that the reader scan and the stray file check leave out: a name at any depth,
        # or a path from the root when the entry holds a `/`, such as "app/vendor".
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
        # Key name patterns of config, not secrets: `get --reveal` of one asks nothing.
        "publicKeys": [
            *("*_DRIVER", "*_DRIVERS", "*_REGION", "*_BUCKET", "*_HOST", "*_PORT"),
            *("*_MODE", "*_LEVEL", "*_ENDPOINT_URL", "NODE_ENV", "APP_ENV"),
        ],
        # Folders beside the repository where `copy` and `set` may write an env file: by default
        # the agent scratch folders, where a session stages a key for a tool it runs.
        "writeRoots": [os.path.join(tempfile.gettempdir(), "claude-*")],
    },
)


def main(argv: list[str]) -> int:
    from agent_harness.env import command

    return command.main(argv)
