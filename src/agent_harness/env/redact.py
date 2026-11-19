"""The redaction of `ah env run`: every value of the loaded file becomes `***` in the output.

A value counts when it has 6 characters or more, and so does each such line of a value that
spans lines. The password of a URL value counts at any length. A longer value goes first, so a
URL becomes one `***` and not a URL with a `***` inside.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import IO

from agent_harness.env.masking import SCHEME, raw_userinfo

MIN_LENGTH = 6
MASK = b"***"


class Redaction:
    def __init__(self, values: Iterable[str]) -> None:
        found: set[str] = set()
        for value in values:
            found.update(part for part in [value, *value.splitlines()] if len(part) >= MIN_LENGTH)
            userinfo = raw_userinfo(value) if SCHEME.match(value) else None
            password = (userinfo or "").partition(":")[2]
            if password:
                found.add(password)
        ordered = sorted(found, key=len, reverse=True)
        self.secrets = [secret.encode("utf-8") for secret in ordered]

    def line(self, data: bytes) -> bytes:
        for secret in self.secrets:
            data = data.replace(secret, MASK)
        return data


def pump(source: IO[bytes], sink: IO[bytes], redaction: Redaction) -> None:
    """Copy `source` to `sink` one line at a time through `redaction`, up to the end."""
    for data in iter(source.readline, b""):
        sink.write(redaction.line(data))
        sink.flush()
