#!/usr/bin/env python3
"""Render a spec to its HTML page, whatever wrote the Markdown.

PostToolUse on Write and Edit renders the page when the file appears. A matcher on tool names
cannot see a heredoc, a `sed -i` or a script, so Stop compares each changed spec with its page
and renders a missing or stale page. Stop also holds the turn (exit 2) when a page link of the
reply opens nothing, and names the checkout that holds the page: the page folder stays out of
git. The renderer is the engine beside this hook, so the payload never picks the code that runs.
A render goes only into `CLAUDE_PROJECT_DIR` or one of its `.claude/worktrees/`. A failed render
is reported, so a spec with no `Date:` or `Status:` line is named on the first write. The spec
folders and the page folder come from `[specs]`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_harness import HarnessError  # noqa: E402
from agent_harness.spec import layout, render  # noqa: E402

# A folder's own README describes the folder. It has no header block and is not a spec.
NOT_A_SPEC = frozenset({"README.md"})
WORKTREES = f"{os.sep}.claude{os.sep}worktrees{os.sep}"
# How many transcript lines `session_started` scans for the first timestamp.
HEAD_LINES = 32
HEAD_CHUNK = 64 * 1024
# Where `read_head` gives up and filters nothing, instead of reading a large file whole.
HEAD_CEILING = 4 * 1024 * 1024
# How much of the transcript's end `last_reply` reads when the payload carries no reply.
TAIL_BYTES = 1024 * 1024


def repo_root_for(path: Path) -> Path | None:
    """The checkout that holds `path`: the nearest folder with a `.git` entry.

    A worktree has `.git` as a file, so the walk stops at the worktree.
    """
    folder = path.parent
    while True:
        if (folder / ".git").exists():
            return folder
        if folder.parent == folder:
            return None
        folder = folder.parent


def read_head(path: Path) -> str:
    """The first `HEAD_LINES` lines of a file, read in chunks and decoded once.

    One fixed read is not enough: a transcript can open with a record larger than 64 KiB, and a
    cut record parses as nothing.
    """
    chunks: list[bytes] = []
    seen = 0
    read = 0
    with path.open("rb") as handle:
        while seen < HEAD_LINES and read < HEAD_CEILING:
            chunk = handle.read(HEAD_CHUNK)
            if not chunk:
                break
            seen += chunk.count(b"\n")
            chunks.append(chunk)
            read += len(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def session_started(payload: dict[str, Any]) -> float | None:
    """The epoch seconds of this session's first transcript record, or None.

    The Stop pass asks `git status`, which describes the working tree, not the session. Several
    agents share one checkout, so the pass takes only files written after this session started.
    None means no filter: a missing transcript costs a repeated link, never a missing page.
    """
    raw = payload.get("transcript_path")
    if not isinstance(raw, str):
        return None
    try:
        head = read_head(Path(raw))
    except OSError:
        return None
    for line in head.split("\n")[:HEAD_LINES]:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        stamp = record.get("timestamp") if isinstance(record, dict) else None
        if not isinstance(stamp, str):
            continue
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return None


def trusted_project_root() -> Path | None:
    """`CLAUDE_PROJECT_DIR`, resolved. None when it is unset, relative or not a folder."""
    raw = os.environ.get("CLAUDE_PROJECT_DIR")
    if not raw or not os.path.isabs(raw) or not os.path.isdir(raw):
        return None
    return Path(os.path.realpath(raw))


def spec_source(payload: dict[str, Any]) -> Path | None:
    """The spec Markdown this tool call wrote, or None when it wrote something else."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw.endswith(".md") or Path(raw).name in NOT_A_SPEC:
        return None
    source = Path(os.path.abspath(raw))
    if not source.is_file():
        return None
    root = repo_root_for(source)
    if root is None:
        return None
    posix = source.as_posix()
    if not any(f"/{folder}/" in posix for folder in layout.for_root(root).dirs):
        return None
    return source


def page_for(source: Path, root: Path) -> str:
    return render.dist_for(source.as_posix(), layout.for_root(root))


def render_line(root: Path, source: Path) -> str:
    """Render `source` with the engine beside this hook, and give the line for the model."""
    where = layout.for_root(root)
    try:
        render.render_file(str(source), where, cwd=root)
    except render.SpecError as error:
        return f"The spec did not render, so there is no page yet: {error}"
    page = render.dist_for(str(source), where)
    note = ""
    if WORKTREES in str(root):
        note = (
            "\nThis page exists in this worktree only. "
            "After a landing, give the link that land.sh prints."
        )
    return f"Rendered the spec. Hand this back on a line of its own:\nfile://{page}{note}"


def safe_render(project_root: Path, root: Path, source: Path) -> str | None:
    """Render only when `root` is the trusted root or one of its worktrees, and `source` is in it.

    Both paths are resolved first. Each prefix check carries a trailing separator, so a sibling
    such as `<root>-evil` cannot pass. None means the render was skipped.
    """
    try:
        real_root = Path(os.path.realpath(root, strict=True))
        real_source = Path(os.path.realpath(source, strict=True))
    except OSError:
        return None
    worktrees = str(project_root / ".claude" / "worktrees") + os.sep
    if real_root != project_root and not str(real_root).startswith(worktrees):
        return None
    if not str(real_source).startswith(str(real_root) + os.sep):
        return None
    try:
        return render_line(real_root, real_source)
    except (OSError, HarnessError, UnicodeError) as error:
        return f"The spec renderer could not be run: {error}"


def unrendered(root: Path, since: float | None) -> list[Path]:
    """Spec Markdown this working tree changed whose page is missing or older.

    `git status` instead of a walk, so a fresh checkout does not render every spec on its first
    turn. `-uall`, because git folds a wholly untracked folder into one entry. A QA folder is
    often gitignored, so `--ignored` lists its files too. An ignored file elsewhere is skipped.
    """
    where = layout.for_root(root)
    present = [folder for folder in where.dirs if (root / folder).is_dir()]
    if not present:
        return []
    try:
        listed = subprocess.run(
            ["git", "status", "--porcelain", "-uall", "--ignored", "-z", "--", *present],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if listed.returncode != 0:
        return []
    changed: list[Path] = []
    for entry in listed.stdout.split("\0"):
        # Porcelain v1 is two status letters, a space, then the path. A rename carries the old
        # path in a second record, which this skips by taking the name as written.
        name = entry[3:] if len(entry) > 3 else ""
        if not name.endswith(".md") or Path(name).name in NOT_A_SPEC:
            continue
        domain = where.domain_at(name)
        if entry.startswith("!!") and (domain is None or domain.settles):
            continue
        source = root / name
        try:
            source_stat = source.stat()
        except OSError:
            continue
        if not source.is_file() or source_stat.st_mtime < (since or 0):
            continue
        page = Path(render.dist_for(source.as_posix(), where))
        try:
            # A page that PostToolUse already wrote is newer than its source, so Stop skips it.
            stale = page.stat().st_mtime < source_stat.st_mtime
        except OSError:
            stale = True
        if stale:
            changed.append(source)
    return changed


def text_of(content: object) -> str:
    """The words of one transcript message, with every other block left behind."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def last_reply(payload: dict[str, Any]) -> str:
    """The reply that ends this turn, from the payload, else from the transcript's end."""
    message = payload.get("last_assistant_message")
    if isinstance(message, str):
        return message
    raw = payload.get("transcript_path")
    if not isinstance(raw, str):
        return ""
    try:
        with open(raw, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - TAIL_BYTES))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    for line in reversed(tail.split("\n")):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        text = text_of((record.get("message") or {}).get("content"))
        if text.strip():
            return text
    return ""


def checkouts(project_root: Path) -> list[Path]:
    """The main checkout of the trusted root, then each worktree under `.claude/worktrees/`."""
    text = str(project_root)
    at = text.find(WORKTREES)
    main = Path(text if at == -1 else text[:at])
    folder = main / ".claude" / "worktrees"
    if not folder.is_dir():
        return [main]
    return [main, *(folder / name for name in sorted(os.listdir(folder)))]


def page_link_re(where: layout.Layout) -> re.Pattern[str]:
    """An absolute page path, with or without `file://`. It ends at the first character that
    cannot be part of a Markdown link target or a terminal word."""
    names = "|".join(re.escape(folder) for folder in where.longest_first())
    out = re.escape(where.out)
    stop = r"[^\s)\]>'\"`]"
    return re.compile(rf"(?:file://)?(/{stop}*/(?:{names})/{out}/{stop}+\.html)")


def broken_links(project_root: Path, reply: str) -> list[str]:
    """One line for each page link in `reply` that opens nothing.

    A landing writes the spec source into the main checkout, but the page stays in the worktree,
    so a link to the main checkout opens nothing. A missing page whose source is in a trusted
    checkout is rendered first. Any other missing page is reported with the checkout that holds
    it, when one does.
    """
    where = layout.for_root(project_root)
    names = "|".join(re.escape(folder) for folder in where.longest_first())
    in_out = re.compile(rf"/(?:{names})/{re.escape(where.out)}/")
    lines: list[str] = []
    seen: set[str] = set()
    for match in page_link_re(where).finditer(reply):
        page = unquote(match.group(1))
        if page in seen:
            continue
        seen.add(page)
        if os.path.exists(page):
            continue

        source = Path(render.source_for(page, where))
        root = repo_root_for(source) if source.exists() else None
        rendered: str | None = None
        if root is not None:
            rendered = safe_render(project_root, root, source)
            if os.path.exists(page):
                continue

        found_at = in_out.search(page)
        relative = page[found_at.start() + 1 :] if found_at else page
        found = next(
            (
                candidate
                for candidate in (tree / relative for tree in checkouts(project_root))
                if str(candidate) != page and candidate.exists()
            ),
            None,
        )
        if found is not None:
            hint = (
                f"The page exists at file://{found}. Link that page, or land the branch and "
                "link the page that land.sh prints."
            )
        elif rendered is not None:
            hint = rendered
        elif root is not None:
            hint = (
                "Its source is outside this project and its worktrees, "
                "so the hook did not render it."
            )
        else:
            hint = (
                "No checkout of this project holds that page or its source. "
                "Render the spec, or remove the link."
            )
        lines.append(f"file://{page} does not exist. {hint}")
    return lines


def stop(payload: dict[str, Any], project_root: Path) -> tuple[int, str | None]:
    """The exit code and the added context of the Stop pass."""
    cwd = Path(os.path.abspath(str(payload.get("cwd") or ".")))
    # A made-up child, so `repo_root_for` tests `cwd` itself and not only its parents.
    root = repo_root_for(cwd / "x")
    lines: list[str] = []
    for source in [] if root is None else unrendered(root, session_started(payload)):
        line = safe_render(project_root, root, source) if root is not None else None
        if line is not None:
            lines.append(line)
    # After the renders, so a link to a page this turn wrote counts as working. There is no early
    # return on `stop_hook_active`: a corrected reply passes, and goal-spec holds turns too.
    broken = broken_links(project_root, last_reply(payload))
    if broken:
        held = [
            *lines,
            "A spec link in your reply opens nothing:",
            *broken,
            "Correct each link, then end the turn.",
        ]
        sys.stderr.write("\n".join(held) + "\n")
        return 2, None
    return 0, "\n".join(lines) if lines else None


def post_tool_use(payload: dict[str, Any], project_root: Path) -> str | None:
    source = spec_source(payload)
    if source is None:
        return None
    root = repo_root_for(source)
    if root is None:
        return None
    return safe_render(project_root, root, source)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    # The one trusted anchor. With no valid CLAUDE_PROJECT_DIR there is nothing to trust.
    project_root = trusted_project_root()
    if project_root is None:
        return 0
    event = payload.get("hook_event_name")
    if event == "Stop":
        code, context = stop(payload, project_root)
        if code:
            return code
    else:
        context = post_tool_use(payload, project_root)
    if context is None:
        return 0
    output = {
        "hookSpecificOutput": {
            "hookEventName": event or "PostToolUse",
            "additionalContext": context,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
