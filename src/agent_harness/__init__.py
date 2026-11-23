"""agent-harness: agent skills, hooks, rules and an installer for any git repository."""

__version__ = "0.4.0"


class HarnessError(Exception):
    """An error that the `ah` launcher prints as one message and exits 1 on."""
