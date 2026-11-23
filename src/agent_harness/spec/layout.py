"""Where the specs of a repository live, read from the `[specs]` section of the config.

`dirs` lists the spec folders, by default `docs/specs` only. A project can add `docs/plans`.
A folder whose last path part is `plans` holds plans. Every other folder holds specifications.
`kinds` gives a folder another kind (`spec`, `plan` or `qa`), badge word and status vocabulary.
The kind decides the badge on a rendered page, and the vocabulary the statuses it accepts.
A `kinds` folder under a spec folder is a folder of its own, and the longest folder wins.

Each folder can hold an `archive` folder for settled documents, and an `out` folder (`dist`)
for the rendered pages. Both names come from the config. A `qa` folder holds disposable QA
reports: it has no archive, and the audit and the reconcile commands leave it out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from agent_harness import HarnessError, config

ASSETS = "assets"
# The index of a folder describes the folder, so it is no document.
INDEX_NAME = "README.md"
# The kinds the page template has a style for, each with its badge word and its statuses.
# No statuses means the vocabulary of the spec-format rule.
KIND_DEFAULTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "spec": ("SPECIFICATION", ()),
    "plan": ("PLAN", ()),
    "qa": ("QA REPORT", ("untested", "tested")),
}
KINDS = tuple(KIND_DEFAULTS)
# The kind whose documents are never archived.
QA = "qa"
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

    @property
    def settles(self) -> bool:
        """Whether a settled document of this folder moves into its archive."""
        return self.kind != QA


@dataclass(frozen=True)
class Layout:
    """The spec folders of one repository, with the archive and the page folder names."""

    dirs: tuple[str, ...]
    archive: str
    out: str
    kinds: tuple[tuple[str, Domain], ...] = ()
    # The page texts of `specs.wordmark` and `specs.footer`.
    wordmark: str = config.SECTION_DEFAULTS["specs"]["wordmark"]
    footer: str = config.SECTION_DEFAULTS["specs"]["footer"]

    @property
    def domain_dirs(self) -> tuple[str, ...]:
        """The spec folders, then each `kinds` folder that sits under one of them."""
        return (*self.dirs, *(folder for folder, _ in self.kinds if folder not in self.dirs))

    @property
    def domains(self) -> tuple[Domain, ...]:
        named = dict(self.kinds)
        return tuple(named.get(folder) or domain_for(folder) for folder in self.domain_dirs)

    def longest_first(self) -> list[str]:
        """The folders of `domain_dirs`, longest first, so a regex alternation or a prefix
        search takes the most specific folder of a path."""
        return sorted(self.domain_dirs, key=len, reverse=True)

    def inner(self, folder: str) -> tuple[str, ...]:
        """`<other>/` for each folder of `domain_dirs` under `folder`: its files are not
        `folder`'s own."""
        return tuple(f"{other}/" for other in self.domain_dirs if other.startswith(f"{folder}/"))

    def folders(self) -> list[str]:
        """Each spec folder, then its archive folder, in config order. A `qa` folder has none."""
        found: list[str] = []
        for domain in self.domains:
            found.append(domain.dir)
            if domain.settles:
                found.append(f"{domain.dir}/{self.archive}")
        return found

    def active(self, kind: str) -> list[str]:
        """The spec folders of one kind, without their archives."""
        return [domain.dir for domain in self.domains if domain.kind == kind]

    def settling(self) -> list[str]:
        """The spec folders whose settled documents move into an archive: every kind but `qa`."""
        return [domain.dir for domain in self.domains if domain.settles]

    def folder_re(self) -> re.Pattern[str]:
        """A spec folder inside a path, at its start or after a slash. Group 1 is the folder. The
        alternation is longest first, so a `kinds` folder under a spec folder wins."""
        names = "|".join(re.escape(folder) for folder in self.longest_first())
        return re.compile(rf"(?:^|/)({names})/")

    def domain_at(self, path: str) -> Domain | None:
        """The spec folder that `path` runs through, or None. The longest folder wins."""
        match = self.folder_re().search(path.replace("\\", "/"))
        return next((d for d in self.domains if match and d.dir == match.group(1)), None)

    def sources(self, root: Path) -> list[str]:
        """Each Markdown source under the spec folders and their archives, as a path from
        `root`. The page folder and `assets/` hold no source, and a `README.md` is no source."""
        found: list[str] = []
        for folder in self.domain_dirs:
            base = root / folder
            inner = self.inner(folder)
            names = (
                path.relative_to(root).as_posix()
                for path in base.rglob("*.md")
                if path.name != INDEX_NAME
                and not {self.out, ASSETS} & set(path.relative_to(base).parts[:-1])
            )
            found += sorted(name for name in names if not (inner and name.startswith(inner)))
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


def _page_text(value: object, key: str) -> str:
    if not isinstance(value, str) or "\n" in value or "\r" in value:
        raise HarnessError(f"{key} must be one line of text, got {value!r}")
    return value


def _kind(folder: str, value: object) -> Domain:
    key = f"specs.kinds.{folder}"
    if not isinstance(value, dict):
        raise HarnessError(f"{key} must be a table, got {value!r}")
    kind = value.get("kind", domain_for(folder).kind)
    if kind not in KINDS:
        raise HarnessError(f"{key}.kind must be one of {', '.join(KINDS)}, got {kind!r}")
    label, statuses = KIND_DEFAULTS[kind]
    label = value.get("label", label)
    if not isinstance(label, str) or not LABEL_RE.match(label):
        raise HarnessError(f"{key}.label must match {LABEL_RE.pattern}, got {label!r}")
    words = value.get("statuses", list(statuses))
    if not isinstance(words, list) or not all(isinstance(v, str) and v for v in words):
        raise HarnessError(f"{key}.statuses must be a list of words, got {words!r}")
    return Domain(folder.strip("/"), kind, label, tuple(words))


def from_config(cfg: config.Config) -> Layout:
    kinds = cfg.get("specs.kinds")
    if not isinstance(kinds, dict):
        raise HarnessError(f"specs.kinds must be a table, got {kinds!r}")
    dirs = _strings(cfg.get("specs.dirs"), "specs.dirs")
    named = tuple((f.strip("/"), _kind(f, v)) for f, v in kinds.items())
    for folder, _ in named:
        if not any(folder == d or folder.startswith(f"{d}/") for d in dirs):
            raise HarnessError(
                f'specs.kinds."{folder}" names a folder that is neither in specs.dirs nor under'
                f" one of them ({', '.join(dirs)}). Add it to specs.dirs, or move the key."
            )
    return Layout(
        dirs=dirs,
        archive=_name(cfg.get("specs.archive"), "specs.archive"),
        out=_name(cfg.get("specs.out"), "specs.out"),
        kinds=named,
        wordmark=_page_text(cfg.get("specs.wordmark"), "specs.wordmark"),
        footer=_page_text(cfg.get("specs.footer"), "specs.footer"),
    )


@cache
def for_root(root: Path) -> Layout:
    """The layout of the repository at `root`, read once per process."""
    return from_config(config.load(root))
