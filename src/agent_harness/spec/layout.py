"""Where the specs of a repository live, read from the `[specs]` section of the config.

`dirs` lists the spec folders, for example `docs/specs` and `docs/superpowers/plans`. A folder
whose last path part is `plans` holds plans. Every other folder holds specifications. `kinds`
gives a folder another kind (`spec`, `plan` or `qa`), badge word and status vocabulary. The kind
decides the badge on a rendered page, and the vocabulary the statuses the renderer accepts.

Each folder can hold an `archive` folder for settled documents, and an `out` folder (`dist`)
for the rendered pages. Both names come from the config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from agent_harness import HarnessError, config

ASSETS = "assets"
# The kinds the page template has a style for.
KINDS = ("spec", "plan", "qa")
# The badge word reaches the page, so it takes plain characters only.
LABEL_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,40}$")


@dataclass(frozen=True)
class Domain:
    """One spec folder: its path under the repository root, its kind, its badge word and its
    statuses. No statuses means the vocabulary of the spec-format rule."""

    dir: str
    kind: str
    label: str
    statuses: tuple[str, ...] = ()


@dataclass(frozen=True)
class Layout:
    """The spec folders of one repository, with the archive and the page folder names."""

    dirs: tuple[str, ...]
    archive: str
    out: str
    kinds: tuple[tuple[str, Domain], ...] = ()

    @property
    def domains(self) -> tuple[Domain, ...]:
        named = dict(self.kinds)
        return tuple(named.get(folder) or domain_for(folder) for folder in self.dirs)

    def folders(self) -> list[str]:
        """Each spec folder, then its archive folder, in config order."""
        found: list[str] = []
        for folder in self.dirs:
            found += [folder, f"{folder}/{self.archive}"]
        return found

    def active(self, kind: str) -> list[str]:
        """The spec folders of one kind, without their archives."""
        return [domain.dir for domain in self.domains if domain.kind == kind]

    def sources(self, root: Path) -> list[str]:
        """Each Markdown source under the spec folders and their archives, as a path from
        `root`. The page folder and `assets/` hold no source."""
        found: list[str] = []
        for folder in self.dirs:
            base = root / folder
            found += sorted(
                path.relative_to(root).as_posix()
                for path in base.rglob("*.md")
                if not {self.out, ASSETS} & set(path.relative_to(base).parts[:-1])
            )
        return found

    def mention_re(self) -> re.Pattern[str]:
        """A spec path as a transcript writes it: a spec folder, then one `.md` file name."""
        names = "|".join(re.escape(folder) for folder in self.dirs)
        return re.compile(rf"(?:{names})/[\w.-]+\.md")


def domain_for(folder: str) -> Domain:
    if folder.rstrip("/").rsplit("/", 1)[-1] == "plans":
        return Domain(folder, "plan", "PLAN")
    return Domain(folder, "spec", "SPECIFICATION")


def _strings(value: object, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise HarnessError(f"{key} must be a list of folder paths, got {value!r}")
    return tuple(v.strip("/") for v in value)


def _name(value: object, key: str) -> str:
    if not isinstance(value, str) or not value or "/" in value:
        raise HarnessError(f"{key} must be one folder name, got {value!r}")
    return value


def _kind(folder: str, value: object) -> Domain:
    key = f"specs.kinds.{folder}"
    if not isinstance(value, dict):
        raise HarnessError(f"{key} must be a table, got {value!r}")
    found = domain_for(folder)
    kind = value.get("kind", found.kind)
    if kind not in KINDS:
        raise HarnessError(f"{key}.kind must be one of {', '.join(KINDS)}, got {kind!r}")
    label = value.get("label", found.label)
    if not isinstance(label, str) or not LABEL_RE.match(label):
        raise HarnessError(f"{key}.label must match {LABEL_RE.pattern}, got {label!r}")
    statuses = value.get("statuses", [])
    if not isinstance(statuses, list) or not all(isinstance(v, str) and v for v in statuses):
        raise HarnessError(f"{key}.statuses must be a list of words, got {statuses!r}")
    return Domain(folder.strip("/"), kind, label, tuple(statuses))


def from_config(cfg: config.Config) -> Layout:
    kinds = cfg.get("specs.kinds")
    if not isinstance(kinds, dict):
        raise HarnessError(f"specs.kinds must be a table, got {kinds!r}")
    return Layout(
        dirs=_strings(cfg.get("specs.dirs"), "specs.dirs"),
        archive=_name(cfg.get("specs.archive"), "specs.archive"),
        out=_name(cfg.get("specs.out"), "specs.out"),
        kinds=tuple((f.strip("/"), _kind(f, v)) for f, v in kinds.items()),
    )


@cache
def for_root(root: Path) -> Layout:
    """The layout of the repository at `root`, read once per process."""
    return from_config(config.load(root))
