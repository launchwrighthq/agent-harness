from pathlib import Path

import pytest

from agent_harness import HarnessError
from agent_harness.env import readers

NODE = """const url = process.env.DATABASE_URL;
const key = process.env["API_KEY"] ?? process.env['OTHER_KEY'];
const mode = import.meta.env.VITE_MODE;
// process.env.lowercase_is_fine_too
"""

PYTHON = """import os
from os import environ

TOKEN = os.environ["API_TOKEN"]
REGION = os.environ.get("AWS_REGION", "eu")
LEVEL = os.getenv('LOG_LEVEL')
HOME = environ["HOME_DIR"]
"""

SETTINGS = """from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_")

    database_url: str
    pool_size: int = 5
    token: str = Field(alias="API_TOKEN")
    _private: str = "x"


class Worker(Settings):
    queue: str = "jobs"


class Plain:
    not_a_setting: str = "x"
"""

LEGACY = """import pydantic


class Settings(pydantic.BaseSettings):
    redis_url: str

    class Config:
        env_prefix = "CACHE_"
"""


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def found(root: Path, globs: tuple[str, ...] = ("*.py", "*.ts")) -> list[tuple[str, str, int]]:
    reads = readers.scan(root, globs, ("node_modules", ".venv", "dist", "build"))
    return [(r.key, r.path.relative_to(root).as_posix(), r.line) for r in reads]


def test_finds_each_node_reader_form_with_its_line(tmp_path: Path) -> None:
    write(tmp_path, "src/app.ts", NODE)

    assert found(tmp_path) == [
        ("DATABASE_URL", "src/app.ts", 1),
        ("API_KEY", "src/app.ts", 2),
        ("OTHER_KEY", "src/app.ts", 2),
        ("VITE_MODE", "src/app.ts", 3),
        ("lowercase_is_fine_too", "src/app.ts", 4),
    ]


def test_finds_each_python_reader_form_with_its_line(tmp_path: Path) -> None:
    write(tmp_path, "app/config.py", PYTHON)

    assert found(tmp_path) == [
        ("API_TOKEN", "app/config.py", 4),
        ("AWS_REGION", "app/config.py", 5),
        ("LOG_LEVEL", "app/config.py", 6),
        ("HOME_DIR", "app/config.py", 7),
    ]


def test_reads_the_fields_of_a_settings_class_with_prefix_and_alias(tmp_path: Path) -> None:
    write(tmp_path, "app/settings.py", SETTINGS)
    write(tmp_path, "app/legacy.py", LEGACY)

    assert found(tmp_path) == [
        ("CACHE_REDIS_URL", "app/legacy.py", 5),
        ("APP_DATABASE_URL", "app/settings.py", 8),
        ("APP_POOL_SIZE", "app/settings.py", 9),
        ("API_TOKEN", "app/settings.py", 10),
        ("APP_QUEUE", "app/settings.py", 15),
    ]


def test_skips_the_configured_folders_and_files_outside_the_globs(tmp_path: Path) -> None:
    write(tmp_path, "node_modules/lib/index.ts", NODE)
    write(tmp_path, "pkg/dist/out.ts", NODE)
    write(tmp_path, "docs/notes.md", NODE)
    write(tmp_path, "src/main.ts", "process.env.KEPT\n")

    assert found(tmp_path) == [("KEPT", "src/main.ts", 1)]


def test_skips_a_path_entry_from_the_root_and_a_dot_folder_by_name(tmp_path: Path) -> None:
    write(tmp_path, "app/vendor/lib.ts", NODE)
    write(tmp_path, ".venv/lib/site.py", "import os\nos.getenv('VENV_KEY')\n")
    write(tmp_path, "src/vendor/own.ts", "process.env.KEPT\n")

    reads = readers.scan(tmp_path, ("*.py", "*.ts"), (".venv", "app/vendor"))

    assert [(r.key, r.path.relative_to(tmp_path).as_posix()) for r in reads] == [
        ("KEPT", "src/vendor/own.ts")
    ]


def test_names_a_python_file_that_does_not_parse(tmp_path: Path) -> None:
    write(tmp_path, "app/broken.py", "class Settings(BaseSettings):\n  x: int = (\n")

    with pytest.raises(HarnessError) as caught:
        found(tmp_path)

    assert str(caught.value) == (
        "app/broken.py: not valid Python, so its settings class is not read. Fix the file, or "
        "leave it out of [env] readers."
    )
