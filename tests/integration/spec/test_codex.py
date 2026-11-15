import subprocess
from pathlib import Path

from agent_harness.spec.codex import link_dependencies


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


def test_links_the_venv_and_each_package_node_modules_into_the_review_tree(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / "web").mkdir(parents=True)
    git(tmp_path, "init", "--quiet", str(root))
    (root / "package.json").write_text("{}", encoding="utf-8")
    (root / "web" / "package.json").write_text("{}", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "packages")
    for folder in ("node_modules", "web/node_modules", ".venv"):
        (root / folder).mkdir()
    tree = tmp_path / "tree"
    git(root, "worktree", "add", "--quiet", "--detach", str(tree), "HEAD")

    link_dependencies(root, tree)

    for folder in ("node_modules", "web/node_modules", ".venv"):
        assert (tree / folder).resolve() == (root / folder).resolve()
