"""The repository of one `ah env` run: its `[env]` config and the resolution of env file
names."""

from __future__ import annotations

import glob
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent_harness import HarnessError, config
from agent_harness.env import declarations, envfile, layout, readers, stores


class Context:
    """The repository, its `[env]` config and the resolution of env file names."""

    def __init__(
        self,
        root: Path,
        settings: Mapping[str, Any],
        out: Callable[[str], None],
        err: Callable[[str], None],
        runner: stores.Runner,
        ask: Callable[[str], str] | None = None,
    ) -> None:
        self.root = root
        # Asks the user one question on the terminal. None when standard input is no terminal.
        self.ask = ask
        self.settings = settings
        self.out = out
        self.err = err
        self.runner = runner
        self.main_checkout = main_checkout_of(root)
        self._declarations: declarations.Declarations | None = None
        self._reads: list[readers.Read] | None = None

    @property
    def master(self) -> Path:
        return self.at(str(self.settings["master"]))

    def at(self, name: str) -> Path:
        """The path of an env file name.

        A relative live file that the root does not hold, and that the main checkout holds,
        resolves in the main checkout. A template always stays the root's own.
        """
        path = Path(name)
        if path.is_absolute():
            return path
        own = self.root / path
        if own.exists() or layout.is_template(path.name) or self.main_checkout is None:
            return own
        shared = self.main_checkout / path
        return shared if shared.exists() else own

    def show(self, path: Path) -> str:
        """A path as output names it: relative to the root, or to the main checkout."""
        for base in (self.root, self.main_checkout):
            if base is not None and path.is_relative_to(base):
                rel = path.relative_to(base).as_posix()
                return rel if base == self.root else f"{rel} (main checkout)"
        return str(path)

    def live_files(self) -> list[Path]:
        """The configured files, else every live env file beside the master."""
        configured = self.settings.get("files") or []
        if configured:
            return [self.at(str(name)) for name in configured]
        names: dict[str, Path] = {}
        folders = [self.master.parent]
        if self.main_checkout is not None and self.master.parent.is_relative_to(self.root):
            folders.append(self.main_checkout / self.master.parent.relative_to(self.root))
        for folder in folders:
            if not folder.is_dir():
                continue
            for name in layout.env_names(folder):
                if not layout.is_template(name):
                    names.setdefault(name, folder / name)
        return [names[name] for name in sorted(names)]

    def templates(self) -> list[Path]:
        folder = self.master.parent
        if not folder.is_dir():
            return []
        return [folder / n for n in layout.env_names(folder) if layout.is_template(n)]

    def declarations(self) -> declarations.Declarations:
        if self._declarations is None:
            names = self.settings.get("declarations") or []
            if isinstance(names, str):
                names = [names]
            paths = [Path(n) if Path(n).is_absolute() else self.root / n for n in names]
            self._declarations = declarations.load(paths)
        return self._declarations

    def for_folder(self, folder: str) -> Context:
        """The context of another folder of env files, with `<folder>/.env.example` as master."""
        settings = {**self.settings, "master": f"{folder}/{layout.MASTER}", "files": []}
        return Context(self.root, settings, self.out, self.err, self.runner, self.ask)

    def extra_masters(self) -> list[Path]:
        """The master of each folder of `[env] dirs`: `<dir>/.env.example`."""
        return [self.root / str(d) / layout.MASTER for d in self.settings.get("dirs") or []]

    def reads(self) -> list[readers.Read]:
        """The code lines that read an env key, from the `readers` globs of the config."""
        if self._reads is None:
            globs = [str(g) for g in self.settings.get("readers") or []]
            skip = [str(s) for s in self.settings.get("skip") or []]
            self._reads = readers.scan(self.root, globs, skip)
        return self._reads

    def own_template(self, path: Path) -> Path | None:
        """The pair template `<file>.example` when it, not the root master, is the master of
        `path`: always for a name of `layout.ALWAYS_PAIRED`, else when it shares no key with
        the root master."""
        master = self.master
        # A template is the root's own, also for a live file of the main checkout.
        pair = master.parent / (path.name + ".example")
        if not pair.is_file() or pair == master:
            return None
        if path.name in layout.ALWAYS_PAIRED or not master.is_file():
            return pair
        pair_keys = set(layout.shape_of_plain(pair.read_text(encoding="utf-8")).key_names())
        root_keys = set(layout.shape_of_master(master.read_text(encoding="utf-8")).key_names())
        return None if pair_keys & root_keys else pair

    def shape_for(self, path: Path) -> layout.Shape | None:
        """The master shape of a file: its pair master, the root master or the declarations."""
        if not self.settings.get("layout", True):
            return None
        pair = self.own_template(path)
        if pair is not None:
            return layout.shape_of_plain(pair.read_text(encoding="utf-8"))
        if self.master.is_file():
            return layout.shape_of_master(self.master.read_text(encoding="utf-8"))
        decl = self.declarations()
        return layout.shape_of_blocks(decl.blocks()) if decl.keys else None

    def master_keys(self, path: Path) -> tuple[Path | None, list[str]]:
        """The master of `path` and its keys in master order.

        The master is the pair template, else the root master, else the declarations (None for
        the path). Raises when there is none.
        """
        pair = self.own_template(path)
        source = pair if pair is not None else self.master
        if source.is_file():
            return source, envfile.read(source).key_names()
        decl = self.declarations()
        if decl.keys:
            return None, [key.name for key in decl.keys]
        raise HarnessError(
            f"no master: {self.show(self.master)} does not exist and no declarations are configured"
        )

    def writable(self, path: Path) -> Path:
        """`path` when a value may go into it: a live env file inside the repository."""
        if layout.is_template(path.name):
            raise HarnessError(
                f"{self.show(path)} is a template. Edit it in an editor, or run "
                "ah env template to write it from a live file."
            )
        return self.destination(path)

    def destination(self, path: Path) -> Path:
        """`path` when ah env may write it: an env file name, also after links are followed,
        that resolves inside the repository root or its main checkout."""
        real = path.resolve()
        for name in dict.fromkeys((path.name, real.name)):
            if not layout.is_env_name(name):
                raise HarnessError(
                    f"{self.show(path)} is not an env file. ah env writes only a file named .env "
                    "or .env.<name>, and refuses a backup, a temp file or a push marker."
                )
        bases = [base.resolve() for base in (self.root, self.main_checkout) if base is not None]
        roots = [
            Path(found).resolve()
            for pattern in self.settings.get("writeRoots") or []
            for found in glob.glob(os.path.expanduser(str(pattern)))
            if os.path.isdir(found)
        ]
        if not any(real.is_relative_to(base) for base in bases + roots):
            raise HarnessError(
                f"{self.show(path)} resolves to {real}, outside the repository root "
                f"{bases[0]} and the folders of env.writeRoots. ah env writes only inside the "
                "repository."
            )
        return path


def main_checkout_of(root: Path) -> Path | None:
    """The main checkout of a linked worktree, or None in a main checkout."""
    common = config.git_common_dir(root)
    if common is None or common.name != ".git":
        return None
    main = common.parent
    return None if main.resolve() == root.resolve() else main
