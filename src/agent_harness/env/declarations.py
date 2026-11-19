"""Optional key declarations: TOML files that say which keys exist and which apps read them.

A file holds `[keys.<NAME>]`, `[families."<NAME>"]` and `[environments.<env>]` tables. The key
fields are those of docs/env.md ("Declarations"), and other fields stay unread. An environment
table maps a key to a one-line value for `slice --environment`. Several files merge in order,
and a later file wins for each field and each value. A key that fits a family is declared,
member or not. A problem names the file, the key and the field, never a value.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness import HarnessError

INT = re.compile(r"^-?[0-9]+$")
# The number types: `decimal` has no exponent, `float` may have one.
DECIMAL = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
DECIMAL_VALUE = re.compile(rf"^{DECIMAL}$")
FLOAT_VALUE = re.compile(rf"^{DECIMAL}(?:[eE][+-]?[0-9]+)?$")
TYPES = ("string", "int", "bool", "url", "enum", "json", "float", "decimal", "path")
# UPPER_SNAKE_CASE with one `<PART>` placeholder, for example `LLM_<NAME>_API_KEY`.
FAMILY_NAME = re.compile(r"^([A-Z][A-Z0-9_]*)?<[A-Z]+>([A-Z0-9_]*)$")
# What replaces the placeholder.
MEMBER = re.compile(r"^[A-Z0-9][A-Z0-9_]*$")
URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^\x00-\x20\x7f]+$")
ENV_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass
class Key:
    name: str
    file: str
    block: str = "Other"
    apps: list[str] | None = None
    secret: bool = False
    # True only when the declaration says `secret = false`. A masked value may then show a short
    # literal.
    plain: bool = False
    type: str = "string"
    enum: list[str] = field(default_factory=list)
    # The family that this key is a member of, for example `API_KEY_<NAME>`.
    family: str | None = None
    # The known member parts, on a family only.
    members: list[str] = field(default_factory=list)
    required: bool = False
    summary: str = ""
    default: str = ""
    # The environments whose env file carries the key. None: every environment.
    environments: list[str] | None = None

    def read_by(self, app: str) -> bool:
        """A key without `apps` goes to every app."""
        return self.apps is None or app in self.apps

    def applies_to(self, environment: str) -> bool:
        """A key without `environments` goes to every environment."""
        return self.environments is None or environment in self.environments


@dataclass
class Declarations:
    keys: list[Key] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    environments: dict[str, dict[str, str]] = field(default_factory=dict)
    families: list[Key] = field(default_factory=list)

    def find(self, name: str) -> Key | None:
        """The declaration of `name`: a key, or a member of a family that the list lacks."""
        found = next((key for key in self.keys if key.name == name), None)
        if found is not None:
            return found
        family = next((f for f in self.families if family_member(f.name, name)), None)
        return None if family is None else member_key(family, name)

    def apps(self) -> list[str]:
        return sorted({app for key in self.keys for app in key.apps or []})

    def keys_of(self, app: str) -> list[Key]:
        return [key for key in self.keys if key.read_by(app)]

    def blocks(self) -> list[tuple[str, str]]:
        return [(key.name, key.block) for key in self.keys]


def matches_type(key: Key, value: str) -> bool:
    """True when a value parses as the type of `key`. An empty value always matches."""
    if value == "" or key.type in ("string", "path"):
        return True
    if key.type == "int":
        return INT.match(value) is not None
    if key.type == "float":
        return FLOAT_VALUE.match(value) is not None
    if key.type == "decimal":
        return DECIMAL_VALUE.match(value) is not None
    if key.type == "bool":
        return value in ("true", "false")
    if key.type == "url":
        return URL.match(value) is not None
    if key.type == "enum":
        return value in key.enum
    if key.type == "json":
        try:
            json.loads(value)
        except ValueError:
            return False
        return True
    return True


def family_member(family: str, name: str) -> str | None:
    """The member part of `name` in `family`, or None when the key is no member."""
    found = FAMILY_NAME.match(family)
    if found is None:
        return None
    prefix, suffix = found.group(1) or "", found.group(2) or ""
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    if len(name) <= len(prefix) + len(suffix):
        return None
    part = name[len(prefix) : len(name) - len(suffix)]
    return part if MEMBER.match(part) else None


def member_key(family: Key, name: str) -> Key:
    """The key `name` with the fields of `family`."""
    apps = None if family.apps is None else list(family.apps)
    environments = None if family.environments is None else list(family.environments)
    return Key(
        name,
        family.file,
        block=family.block,
        apps=apps,
        secret=family.secret,
        type=family.type,
        enum=list(family.enum),
        family=family.name,
        plain=family.plain,
        required=family.required,
        summary=family.summary,
        default=family.default,
        environments=environments,
    )


def _expand(keys: list[Key], families: list[Key], environments: dict[str, dict[str, str]]) -> None:
    """Put the known members of each family into `keys`, sorted, after the last key of the
    family's block, or at the end. A declared key of the same name wins over a member."""
    taken = {key.name for key in keys}
    for family in families:
        names = {re.sub(r"<[A-Z]+>", part, family.name, count=1) for part in family.members}
        names |= {
            n for values in environments.values() for n in values if family_member(family.name, n)
        }
        members = [member_key(family, n) for n in sorted(names - taken)]
        taken |= {m.name for m in members}
        at = max((i for i, key in enumerate(keys) if key.block == family.block), default=-1)
        place = at + 1 if at >= 0 else len(keys)
        keys[place:place] = members


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def load(paths: Sequence[Path]) -> Declarations:
    """Merge the files in order. Raises HarnessError with every problem."""
    merged: dict[str, Key] = {}
    families: dict[str, Key] = {}
    environments: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    for path in paths:
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except OSError:
            problems.append(f"{path}: cannot read the file")
            continue
        except tomllib.TOMLDecodeError:
            # The decoder message can quote a line that holds a value, so it stays out.
            problems.append(f"{path}: not valid TOML")
            continue
        tables = document.get("keys", {})
        if not isinstance(tables, dict):
            problems.append(f"{path}: 'keys' must be a table")
            continue
        for name, fields in tables.items():
            if not isinstance(fields, dict):
                problems.append(f"{path}: key {name} must be a table")
                continue
            key = merged.setdefault(name, Key(name, str(path)))
            _apply(key, fields, path, problems)
        _families(document.get("families", {}), path, families, problems)
        _environments(document.get("environments", {}), path, environments, problems)
    for env, values in environments.items():
        for name in values:
            if name not in merged and not any(family_member(f, name) for f in families):
                problems.append(f"environment {env}: key {name} is declared in no file")
    if problems:
        raise HarnessError("env declarations:\n" + "\n".join(problems))
    keys = list(merged.values())
    _expand(keys, list(families.values()), environments)
    return Declarations(keys, [str(p) for p in paths], environments, list(families.values()))


def _families(tables: Any, path: Path, out: dict[str, Key], problems: list[str]) -> None:
    """Merge the `[families."<NAME>"]` tables of one file into `out`."""
    if not isinstance(tables, dict):
        problems.append(f"{path}: 'families' must be a table")
        return
    for name, fields in tables.items():
        if not FAMILY_NAME.match(name):
            problems.append(f"{path}: family name '{name}' is not UPPER_SNAKE_CASE with one <PART>")
            continue
        if not isinstance(fields, dict):
            problems.append(f"{path}: family {name} must be a table")
            continue
        family = out.setdefault(name, Key(name, str(path)))
        _apply(family, fields, path, problems)
        if "members" in fields:
            members = fields["members"]
            if _strings(members) and all(MEMBER.match(m) for m in members):
                family.members = list(members)
            else:
                problems.append(f"{path}: key {name}: field 'members' must be a list of parts")


def _environments(
    tables: Any, path: Path, out: dict[str, dict[str, str]], problems: list[str]
) -> None:
    """Merge the `[environments.<env>]` tables of one file into `out`."""
    if not isinstance(tables, dict):
        problems.append(f"{path}: 'environments' must be a table")
        return
    for env, values in tables.items():
        if not isinstance(values, dict):
            problems.append(f'{path}: environment {env} must be a table of KEY = "value"')
            continue
        merged = out.setdefault(env, {})
        for name, value in values.items():
            if isinstance(value, str) and "\n" not in value and "\r" not in value:
                merged[name] = value
            else:
                problems.append(f"{path}: environment {env}: key {name}: value must be one line")


def _apply(key: Key, fields: dict[str, Any], path: Path, problems: list[str]) -> None:
    where = f"{path}: key {key.name}"
    if "block" in fields:
        if isinstance(fields["block"], str) and fields["block"].strip():
            key.block = fields["block"]
        else:
            problems.append(f"{where}: field 'block' must be a title")
    if "apps" in fields:
        if _strings(fields["apps"]):
            key.apps = list(fields["apps"])
        else:
            problems.append(f"{where}: field 'apps' must be a list of app names")
    if "secret" in fields:
        if isinstance(fields["secret"], bool):
            key.secret = fields["secret"]
            key.plain = not fields["secret"]
        else:
            problems.append(f"{where}: field 'secret' must be a boolean")
    if "type" in fields:
        if fields["type"] in TYPES:
            key.type = fields["type"]
        else:
            problems.append(f"{where}: field 'type' must be one of {', '.join(TYPES)}")
    if "enum" in fields:
        if _strings(fields["enum"]):
            key.enum = list(fields["enum"])
        else:
            problems.append(f"{where}: field 'enum' must be a list of strings")
    _apply_doc(key, fields, where, problems)


def _one_line(value: Any) -> bool:
    return isinstance(value, str) and "\n" not in value and "\r" not in value


def _apply_doc(key: Key, fields: dict[str, Any], where: str, problems: list[str]) -> None:
    """The fields `required`, `summary`, `default` and `environments`."""
    if "required" in fields:
        if isinstance(fields["required"], bool):
            key.required = fields["required"]
        else:
            problems.append(f"{where}: field 'required' must be a boolean")
    if "summary" in fields:
        if _one_line(fields["summary"]):
            key.summary = fields["summary"].strip()
        else:
            problems.append(f"{where}: field 'summary' must be one line of text")
    if "default" in fields:
        if _one_line(fields["default"]):
            key.default = fields["default"]
        else:
            problems.append(f"{where}: field 'default' must be a one-line string")
    if "environments" in fields:
        names = fields["environments"]
        if _strings(names) and all(ENV_NAME.match(n) for n in names):
            key.environments = list(names)
        else:
            problems.append(f"{where}: field 'environments' must be a list of environment names")
