import pytest

from agent_harness.work.branch_files import is_credential


@pytest.mark.parametrize(
    "path",
    ["cacert.pem", ".venv/lib/site-packages/certs/cacert.pem", "config/.env.example"],
)
def test_reads_a_public_file_as_no_credential(path: str) -> None:
    assert is_credential(path) is False


@pytest.mark.parametrize(
    "path",
    ["server.pem", "certs/my-cacert.pem", "cacert.pem.key", "cacert.key", ".env.local"],
)
def test_keeps_every_other_key_and_certificate_name_a_credential(path: str) -> None:
    assert is_credential(path) is True
