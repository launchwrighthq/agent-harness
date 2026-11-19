"""Cases for the masked form of an env value: connection strings, other URLs and literals."""

import re

import pytest

from agent_harness.env import masking

DIGEST = re.compile(r"^set, \d+ chars, sha256:[0-9a-f]{8}$")

SAMPLES = [
    "hunter2-password",
    "fakeheader.fakepayload.fake_signature-0123456789",
    "postgres://myuser:mypassword@dbhost.example:5432/dbname?sslmode=require&x=1",
    "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9",
    "abc#def",
    "apikey:not-a-real-key-0123456789",
    "redis:6379",
    "mailto:secret-owner@example.com",
    "note:plain-text-value",
]

KNOWN_LEAK_REPROS = [
    "postgres://user:12/34@host.example.com/db",
    "postgres://user:pa/ss@host.example.com/db",
    "postgres://user:/foo@host.example.com/db",
    "postgres://user:////@host.example.com/db",
    "postgres://a:////@host.example.com/db",
    "postgres://a://@host.example.com/db",
    "postgres://a:/@@host.example.com/db",
    "postgres://a:?q@host.example.com/db",
]

URL_SHAPES = [
    ("postgres://user:pass@host.example/db", "postgres://***:***@host.example/db"),
    (
        "postgres://myuser:mypassword@dbhost.example:5432/dbname?sslmode=require&x=1",
        "postgres://***:***@dbhost.example:5432/dbname?2 params",
    ),
    (
        "postgres://u:p@db.example:5432/app?sslmode=require",
        "postgres://***:***@db.example:5432/app?1 params",
    ),
    ("redis://cache.example:6379", "redis://cache.example:6379"),
    ("rediss://:pw-x9@cache.example:6380/0", "rediss://***:***@cache.example:6380/0"),
    (
        "mongodb+srv://app:pw-x9@cluster.example.net/app",
        "mongodb+srv://***:***@cluster.example.net/app",
    ),
    ("amqp://mq-user:pw-x9@mq.example:5672/jobs", "amqp://***:***@mq.example:5672/jobs"),
    ("MySQL://app:pw-x9@db.example/shop", "mysql://***:***@db.example/shop"),
]

# A scheme that is no connection string can carry its token in the path or the query.
DIGEST_SCHEMES = [
    "https://hooks.example.com/services/T0/B0/abc123",
    "https://x.example/?token=SEKRIT9value",
    "http://user:pw@proxy.example:3128",
    "zzcustom://user:pw@host.example/db",
    "ftp://files.example/drop",
]


def test_never_prints_the_raw_value_it_masks() -> None:
    assert [v for v in SAMPLES if v in masking.mask(v)] == []


@pytest.mark.parametrize(("value", "shape"), URL_SHAPES)
def test_prints_a_connection_string_as_host_port_and_database(value: str, shape: str) -> None:
    assert masking.mask(value) == shape


@pytest.mark.parametrize("value", DIGEST_SCHEMES)
def test_masks_a_url_of_any_other_scheme_to_the_digest(value: str) -> None:
    assert DIGEST.match(masking.mask(value))


@pytest.mark.parametrize("value", KNOWN_LEAK_REPROS)
def test_falls_back_to_the_digest_when_the_parser_misreads_the_userinfo(value: str) -> None:
    assert DIGEST.match(masking.mask(value))


def test_strips_control_characters_before_the_leak_check() -> None:
    leading = masking.mask(" postgres://user:12/34@host.example/app")
    case = masking.mask("\rpostgres://zqUser1:#x@db.example/app")
    middle = masking.mask("postgres://user:pa\rss@host.example.com/db")

    assert "12/34" not in leading
    assert "host.example" not in leading
    assert "zquser1" not in case.lower()
    assert "pass" not in middle
    assert "pa\rss" not in middle


def url_cases() -> list[tuple[str, str, str]]:
    """Every scheme, username, password shape, host, port, path and query combined."""
    specials = ["/", "?", "#", "@", ":", "%", "\\", "[", "]", " "]
    passwords = [
        "SEKRIT9plain",
        *[f"{c}SEKRIT9" for c in specials],
        "%2FSEKRIT9",
        "%40SEKRIT9",
        "pre/SEKRIT9",
        "pre@SEKRIT9",
        "pre:SEKRIT9",
        *["/", "//", "////", "/@", "?q", "#f", "::", ""],
    ]
    cases = []
    for scheme in ["postgres", "redis", "https", "mongodb+srv", "zzcustom"]:
        for user in ["zqUser1", "zqAdmin2", "a", ""]:
            for password in passwords:
                for host in ["host-a.example.com", "db-node.example"]:
                    for port in ["5432", "notaport", ""]:
                        for path in ["/db", "/"]:
                            for query in ["", "?sslmode=require", "?a=1&b=2"]:
                                at = f"{host}:{port}" if port else host
                                value = f"{scheme}://{user}:{password}@{at}{path}{query}"
                                cases.append((value, user, password))
    return cases


def test_never_leaks_a_username_or_password_of_a_generated_url() -> None:
    cases = url_cases()
    leaks = []
    shapes = set()

    for value, user, password in cases:
        masked = masking.mask(value).lower()
        shapes.add("digest" if masked.startswith("set, ") else "url")
        pieces = [user, password] + [password[i : i + 4] for i in range(len(password) - 3)]
        if any(len(p) >= 2 and p.lower() in masked for p in pieces):
            leaks.append(value)

    assert len(cases) == 17280
    assert shapes == {"digest", "url"}
    assert leaks == []


def test_prints_a_short_literal_only_for_a_key_marked_plain() -> None:
    plain = [masking.mask(v, plain=True) for v in ["", "true", "false", "3000"]]
    masked = [masking.mask(v) for v in ["true", "123456"]]

    assert plain == ["empty", "true", "false", "3000"]
    assert [bool(DIGEST.match(m)) for m in masked] == [True, True]
    assert DIGEST.match(masking.mask("1234567", plain=True))


def test_describes_any_other_value_by_length_and_digest() -> None:
    assert masking.mask("abc#def") == "set, 7 chars, " + masking.digest("abc#def")
    assert len(masking.digest("x")) == len("sha256:") + 8


@pytest.mark.parametrize(
    ("value", "shape"),
    [
        ("https://user:pw-x@host.example/x", "a URL with a password"),
        ("-----BEGIN " + "CERTIFICATE-----\nabc", "a PEM block"),
        ("0123456789abcdef0123456789abcdef", "32 or more base64 or hex characters"),
        ("QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=", "32 or more base64 or hex characters"),
        ("fake_token-0123456789-abcdefghij_KLMNO", "32 or more base64 or hex characters"),
    ],
)
def test_names_the_credential_shape_of_a_value(value: str, shape: str) -> None:
    assert masking.credential_shape(value) == shape


@pytest.mark.parametrize(
    "value",
    [
        "s3",
        "eu-west-1",
        "https://0123456789abcdef0123456789abcdef.r2.example.com",
        "https://user@host.example",
        "0123456789abcdef0123456789abcde",
        "",
    ],
)
def test_finds_no_credential_shape_in_a_config_value(value: str) -> None:
    assert masking.credential_shape(value) is None


def test_matches_a_whole_key_name_against_the_public_key_patterns() -> None:
    keys = ["STORAGE_DRIVER", "NODE_ENV", "DRIVER", "NODE_ENV_X", "storage_driver"]

    got = [masking.public_key(key, ["*_DRIVER", "NODE_ENV"]) for key in keys]

    assert got == [True, True, False, False, False]
