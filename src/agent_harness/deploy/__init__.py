"""deploy-status: the verdict of the last deploy from the CI half and the serving half.

The `[deploy]` section of `.agents/harness.toml` names the deploy workflows, how a run maps to an
environment, which jobs move or serve something, and how to read each serving target. Every key
has a default, so the command runs in a repository with no config. With no phase map, it never
says "deployed". The command only reads: it changes nothing on GitHub, on a node or on disk.
"""

from __future__ import annotations

from agent_harness import config

config.register_defaults(
    "deploy",
    {
        "ci": {
            "provider": "github",
            "workflows": ["deploy.yml"],
            # The prefix that a calling workflow puts in front of each job of a called workflow,
            # for example "deploy / ".
            "jobPrefix": "",
            # `<owner>/<name>`. Empty means the repository of the checkout.
            "repo": "",
        },
        # No environment means one view over every run of the workflows.
        "environments": {},
        "phases": {"moves": {}, "serving": {}, "noop": [], "deployed": []},
        "version": "git-count",
        "versionCommand": [],
        "records": "",
        # The dotted key of the digest in a record, for example "digests.app".
        "recordDigest": "digest",
        # The dotted key of a version in a record, for example "buildNumber". Empty reads none.
        "recordVersion": "",
        "targets": {},
        "logPhases": {},
        "logSummary": {},
    },
)


def main(argv: list[str]) -> int:
    from agent_harness.deploy.status import main as status_main

    return status_main(argv)
