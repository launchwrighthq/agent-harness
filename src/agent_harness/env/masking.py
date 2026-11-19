"""The one form of a value that a command prints without `--reveal`.

A connection string of `CONNECTION_SCHEMES` prints as `scheme://***:***@host:port/database?<n>
params`. Any other URL, http and https included, can carry its token in the path or the query,
so it prints as its length and a short digest, like every other value. `true`, `false` and a
number of 1 to 6 digits print as they are only for a key that the declarations mark plain.

The URL form prints only when the parser read the userinfo exactly as a reader sees it: the text
between the scheme and the last `@`. A password with `/`, `?` or `#` makes the parser read part
of it as the host or the path, and the value then falls back to the digest form.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from urllib.parse import SplitResult, urlsplit

LITERAL = re.compile(r"true|false|[0-9]{1,6}")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
EDGE_CONTROLS = re.compile(r"^[\x00-\x20]+|[\x00-\x20]+$")
CONNECTION_SCHEMES = {
    *("postgres", "postgresql", "mysql", "mariadb", "redis", "rediss"),
    *("mongodb", "mongodb+srv", "amqp", "amqps"),
}


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def mask(value: str, plain: bool = False) -> str:
    """`plain` is True only for a key that the declarations mark `secret = false`."""
    if value == "":
        return "empty"
    if plain and LITERAL.fullmatch(value):
        return value
    clean = re.sub(r"[\t\r\n]", "", EDGE_CONTROLS.sub("", value))
    shape = url_shape(clean)
    if shape is not None:
        return shape
    return f"set, {len(value)} chars, {digest(value)}"


def url_shape(value: str) -> str | None:
    """The masked connection string, or None for any other value."""
    if not SCHEME.match(value):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in CONNECTION_SCHEMES or not parts.hostname or parts.fragment:
        return None
    userinfo = raw_userinfo(value)
    parsed = parts.netloc.rpartition("@")[0] if "@" in parts.netloc else None
    if userinfo != parsed:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    shown = (
        f"{parts.scheme}://{'***:***@' if userinfo is not None else ''}{host}"
        f"{f':{port}' if port is not None else ''}{parts.path}{_params(parts)}"
    )
    return shown


def _params(parts: SplitResult) -> str:
    count = len([pair for pair in parts.query.split("&") if pair])
    return f"?{count} params" if count else ""


def raw_userinfo(value: str) -> str | None:
    """The text between the scheme and the last `@` of the whole value, or None."""
    rest = SCHEME.sub("", value, count=1)
    return rest[: rest.rindex("@")] if "@" in rest else None


# A whole value of base64, base64url or hex characters, as a token or a key is.
TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")


def public_key(key: str, patterns: Iterable[str]) -> bool:
    """True when a pattern of `[env] publicKeys` matches the whole key name. `*` matches any
    text, and case counts."""
    return any(
        re.fullmatch(".*".join(re.escape(part) for part in pattern.split("*")), key)
        for pattern in patterns
    )


def credential_shape(value: str) -> str | None:
    """The credential shape of `value`, or None. The engine masks a public key that has one."""
    if SCHEME.match(value):
        userinfo = raw_userinfo(value)
        if userinfo is not None and userinfo.partition(":")[2]:
            return "a URL with a password"
    if "-----BEGIN" in value:
        return "a PEM block"
    if TOKEN.fullmatch(value):
        return "32 or more base64 or hex characters"
    return None
