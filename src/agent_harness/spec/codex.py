"""Run Codex as the second reviewer of a spec, and save what it found.

    ah spec codex run <spec> [--base <commit>] [--base-branch <branch>] [--reply <file>]
    ah spec codex task <spec> --worktree <path> --base <commit> --ids R1,R2 [--reply <file>]

`run` reviews the base branch in a throwaway worktree and saves the verdict the `goal-spec` Stop
hook reads. `task` reviews one task's commits in the task's own worktree, as advice nothing waits
on. Both refuse a pass that changed the tree it read, and exit 0 on approved, 1 on not approved,
2 with no verdict. `run` alone exits 3 once the spec has had `store.PASS_CAP` passes, because
past that the operator decides rather than Codex. Each pass keeps its prompt, schema, log and
answer in the spec store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_harness import config
from agent_harness.spec import audit, store
from agent_harness.work import status as ws

EXIT_APPROVED, EXIT_NOT_APPROVED, EXIT_NO_VERDICT, EXIT_CAPPED = 0, 1, 2, 3
# Far above the length of a normal review, so the cap stops only a hung run.
CAP_SECONDS = 30 * 60
DEPENDENCY_FOLDERS = (".venv", "node_modules")

# Every property is required, because a strict structured output accepts no optional one.
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": list(store.VERDICTS)},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "kind", "severity", "state", "where", "finding", "quote"],
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string", "enum": list(store.KINDS)},
                    "severity": {"type": "string", "enum": list(store.SEVERITIES)},
                    "state": {"type": "string", "enum": list(store.FINDING_STATES)},
                    "where": {"type": "string"},
                    "finding": {"type": "string"},
                    "quote": {"type": "string"},
                },
            },
        },
    },
}

RUN_HEAD = """\
You review one spec against {branch}. An independent auditor checks the same spec on its own.
You did not build it, and you trust no report of it. Both reviewers must approve.

Spec: {spec}
Branch: {branch} at {head}, checked out at {tree}
Goal range: {base}..{head}"""

TASK_HEAD = """\
You review one task of a spec before it merges. Your review is advice to the builder. You did
not build it, and you trust no report of it.

Spec: {spec}
Requirement IDs this task covers: {ids}
Task commits: {base}..{head}, checked out at {tree}"""

RULES = """\
Rules:
1. Change no tracked file. Never commit, stash, reset or check out. You may run a focused
   test of the files in the range from {tree}. Never run the whole gate ({gate}), because
   the local services it uses are shared with other sessions.
2. Every finding carries a `kind`. Only the first three may be blocking, and the runner
   demotes a severity that disagrees:
   - unmet-requirement: {unmet}.
   - defect: a wrong result, a swallowed error, a test that cannot fail, or a race, in
     the range.
   - crossed-rule: a rule in AGENTS.md and its imported rules that the range crosses.
   - acceptance-precision: a `Done means` row whose check is weaker than the row.
   - spec-wording: the behaviour the row asks for holds, and only the shape differs.
   - style: naming, comments, prose, formatting.
3. Review the requested features and supported user journeys. Keep looking for real logic,
   integration, UI, layout, interaction, localization, text and design failures. A blocker
   must materially prevent the requested behaviour, mislead a user, or threaten security,
   privacy or data integrity. An explicit requirement remains required even if used rarely.
4. For every blocking defect, put the reachable trigger, concrete code evidence or focused
   reproduction, and meaningful impact in `finding`. Do not invent blockers from unsupported
   inputs, manually corrupted internal state, or chains of unlikely failures without evidence
   that they can occur in supported use. A rare security or data-loss bug still matters when
   its trigger is credible. Optional hardening and polish are notes, or omit them.
   The requirement is the behaviour. Different file layout or an adequate alternative design
   is not a defect. Do not demand extra tests merely to rule out imaginable implementations.
5. A blocked row is the operator's to answer. It is never a finding.
6. Every finding names a file and line in `where`, or the search that found nothing.
7. Make the first review comprehensive for material issues; group findings with one root cause.
   Aim for one substantive review and one verification pass. With a previous review, verify
   the fixes and their direct regressions, mark earlier findings addressed or open with
   evidence, and keep their ids. A new blocker must meet the same materiality and evidence
   threshold and sit in {since}. Do not restart a broad edge-case hunt, split an addressed
   finding into new demands, or raise the acceptance bar between passes.
   If an earlier blocker lacks that evidence, explain why and reclassify it as a note;
   do not keep it blocking merely because a previous pass raised it.
8. A builder reply is a claim. Check it. It never closes a finding by itself.
9. waived-by-operator needs the operator's words from the digest in `quote`. Otherwise
   `quote` is "".
10. The verdict is approved when no blocking finding is open, and not-approved when one is.
    Findings are not a quota. Approve when the requested behaviour holds; optional notes
    require no fix or further review pass.
11. Answer with the JSON the schema describes, and nothing else."""


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=120
    )
    if done.returncode != 0:
        raise SystemExit(
            f"ah spec codex: git {' '.join(args)} failed in {cwd}: {done.stderr.strip()}"
        )
    return done.stdout.strip()


def _gate_command() -> str:
    """`harness.gateCommand` of the main checkout, or a plain name when it sets none."""
    return str(config.load(_main_root()).get("harness.gateCommand") or "the gate")


def _main_root() -> Path:
    return Path(_git(Path.cwd(), "rev-parse", "--path-format=absolute", "--git-common-dir")).parent


def _passes(stem: str) -> Path:
    return store.passes_dir(stem)


def _tree_state(tree: Path) -> tuple[str, str]:
    """HEAD, and a hash of what `git diff HEAD` prints. Untracked test caches do not count."""
    diff = _git(tree, "diff", "HEAD")
    return _git(tree, "rev-parse", "HEAD"), hashlib.sha256(diff.encode()).hexdigest()


def _next_pass(folder: Path) -> int:
    numbers = [
        int(path.name.removeprefix("pass-"))
        for path in folder.glob("pass-*")
        if path.name[5:].isdigit()
    ]
    return max(numbers, default=0) + 1


def _reviewed(folder: Path) -> int:
    """Passes that reached a verdict. A crashed pass leaves its folder, and exit 2 asks again."""
    return len(list(folder.glob("pass-*/review.json")))


@dataclass(frozen=True, slots=True)
class Pass:
    """One Codex pass: what it reads, where its files go, and what came before it."""

    spec: Path
    tree: Path
    base: str
    head: str
    folder: Path
    number: int
    previous: dict[str, Any] | None
    ids: str | None
    branch: str


def _blocked_rows(stem: str) -> str:
    audit = store.load_audit(stem) or {}
    rows = [row for row in audit.get("rows", []) if row.get("state") == store.BLOCKED]
    return "\n".join(f"- {row['id']}: {row.get('question', '')}" for row in rows) or "none"


def _digest(spec: Path, session: str | None, projects_root: Path, branch: str) -> str:
    named = session if session and ws.find_transcripts(session, projects_root) else None
    args = argparse.Namespace(
        specs=[str(spec)], session=named, open=False, projects_root=projects_root, base=branch
    )
    built = audit.build_digest(args)
    built["specs"] = [entry for entry in built["specs"] if entry["stem"] == spec.stem]
    return audit.render_digest(built)


def prompt(one: Pass, digest: str, reply: str | None) -> str:
    fields = {
        "spec": one.spec,
        "tree": one.tree,
        "base": one.base,
        "head": one.head,
        "ids": one.ids,
        "branch": one.branch,
    }
    head = (TASK_HEAD if one.ids else RUN_HEAD).format(**fields)
    since = f"{one.previous['head']}..{one.head}" if one.previous else f"{one.base}..{one.head}"
    unmet = (
        f"a requirement among {one.ids} the task commits do not satisfy"
        if one.ids
        else f"a spec requirement {one.branch} does not satisfy"
    )
    previous = "none"
    if one.previous:
        earlier = {key: one.previous.get(key) for key in ("head", "verdict", "findings")}
        previous = json.dumps(earlier, indent=2, ensure_ascii=False)
    return "\n".join(
        [
            head,
            f"Blocked rows:\n{_blocked_rows(one.spec.stem)}",
            f"Digest:\n{digest}",
            f"Previous review:\n{previous}",
            f"Builder replies:\n{reply or 'none'}",
            "",
            RULES.format(gate=_gate_command(), tree=one.tree, unmet=unmet, since=since),
            "",
        ]
    )


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def find_rollout(tree: Path, started: float) -> Path | None:
    """The session file Codex wrote for a pass run in `tree`, newest first."""
    sessions = _codex_home() / "sessions"
    fresh = [
        path for path in sessions.glob("*/*/*/rollout-*.jsonl") if path.stat().st_mtime >= started
    ]
    for path in sorted(fresh, key=lambda each: each.stat().st_mtime, reverse=True):
        try:
            with path.open(encoding="utf-8") as lines:
                meta = json.loads(lines.readline() or "{}").get("payload") or {}
        except (OSError, ValueError):
            continue  # another Codex session's file, mid-write; this pass's first line is whole
        if meta.get("cwd") and Path(meta["cwd"]).resolve() == tree.resolve():
            return path
    return None


def codex_command(binary: str, one: Pass) -> list[str]:
    command = [binary, "exec", "--sandbox", "read-only"]
    # The install default effort is `none`, which finds too few blockers on a large branch.
    command += ["-c", 'model_reasoning_effort="high"']
    command += ["-C", str(one.tree), "--output-schema", str(one.folder / "schema.json")]
    return [*command, "-o", str(one.folder / "last.json"), "-"]


def link_dependencies(root: Path, tree: Path) -> None:
    """Link the dependency folders of each package of `root` into the review tree, so Codex can
    run a focused test there. A link copies nothing."""
    listed = _git(root, "ls-files", "--", "*package.json", "*pyproject.toml").splitlines()
    packages = sorted({".", *(str(Path(name).parent) for name in listed)})
    for package in packages:
        for name in DEPENDENCY_FOLDERS:
            source, target = root / package / name, tree / package / name
            if source.is_dir() and target.parent.is_dir() and not os.path.lexists(target):
                target.symlink_to(source)


def run_codex(one: Pass, text: str) -> tuple[str | None, Path | None]:
    """Run Codex once, and return its last message and its session file, each None when missing."""
    binary = shutil.which("codex")
    if binary is None:
        raise SystemExit("ah spec codex: the codex CLI is not on PATH")
    one.folder.mkdir(parents=True, exist_ok=True)
    (one.folder / "prompt.md").write_text(text, encoding="utf-8")
    (one.folder / "schema.json").write_text(json.dumps(SCHEMA, indent=2) + "\n", encoding="utf-8")
    last = one.folder / "last.json"
    command = codex_command(binary, one)
    started = time.time()
    with (
        (one.folder / "prompt.md").open(encoding="utf-8") as stdin,
        (one.folder / "pass.log").open("w") as log,
    ):
        try:
            subprocess.run(
                command,
                stdin=stdin,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=CAP_SECONDS,
            )
        except subprocess.TimeoutExpired:
            # A killed pass is no verdict, even when Codex wrote an answer before the kill.
            print(
                f"ah spec codex: the pass ran past {CAP_SECONDS} s and was killed", file=sys.stderr
            )
            return None, None
    answer = last.read_text(encoding="utf-8") if last.is_file() else None
    return answer, find_rollout(one.tree, started)


def review_once(one: Pass, text: str) -> dict[str, Any] | None:
    """Run the pass and check it. None, with the reason printed, when it left no usable verdict."""
    before = _tree_state(one.tree)
    answer, rollout = run_codex(one, text)
    if _tree_state(one.tree) != before:
        print(f"ah spec codex: Codex changed HEAD or a tracked file in {one.tree}", file=sys.stderr)
        return None
    if answer is None:
        print(
            f"ah spec codex: no verdict: Codex wrote no {one.folder / 'last.json'}", file=sys.stderr
        )
        return None
    if rollout is None:
        print(f"ah spec codex: no Codex session file names {one.tree}", file=sys.stderr)
        return None
    try:
        verdict = store.normalise_answer(json.loads(answer))
    except ValueError as error:
        print(f"ah spec codex: no verdict in {one.folder / 'last.json'}: {error}", file=sys.stderr)
        return None
    meta, _ = store.rollout_answer(rollout)
    review = {
        "stem": one.spec.stem,
        "spec": str(one.spec),
        "base": one.branch,
        "base_commit": one.base,
        "head": one.head,
        "pass": one.number,
        **verdict,
        "rollout": str(rollout),
        "cli_version": meta.get("cli_version"),
        "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    return review


def table(review: dict[str, Any]) -> str:
    blocking = ", ".join(store.open_blocking_ids(review)) or "none"
    lines = [
        f"Codex review of {review['stem']}: {review['verdict']}. Pass {review['pass']}, "
        f"head {str(review['head'])[:9]}. Open blocking: {blocking}.",
        "",
        "| ID | Kind | Severity | State | Where | Finding |",
        "|---|---|---|---|---|---|",
    ]
    cell = audit.cell
    lines += [
        f"| {cell(f['id'])} | {cell(f.get('kind'))} | {cell(f['severity'])} | {cell(f['state'])} | "
        f"{cell(f.get('where'))} | {cell(f.get('finding'))} |"
        for f in review["findings"]
    ]
    return "\n".join(lines)


def _open_notes(review: dict[str, Any]) -> list[str]:
    return [f["id"] for f in review["findings"] if f["severity"] == "note" and f["state"] == "open"]


def _exit(review: dict[str, Any] | None) -> int:
    if review is None:
        return EXIT_NO_VERDICT
    print(table(review))
    if review["verdict"] != store.APPROVED:
        return EXIT_NOT_APPROVED
    if notes := _open_notes(review):
        print(
            f"Open notes: {', '.join(notes)}. "
            "Each becomes an A row of the spec if the operator wants it."
        )
    return EXIT_APPROVED


def _capped(spec: Path, number: int, previous: dict[str, Any] | None) -> int:
    """Refuse the pass and hand the findings to the operator, who is the only one who waives."""
    blocking = ", ".join(store.open_blocking_ids(previous or {})) or "none"
    print(
        f"ah spec codex: {spec.stem} is at the {store.PASS_CAP}-pass cap, so pass {number} "
        f"does not run. Open blocking: {blocking}. The operator decides."
    )
    if previous is not None:
        print(table(previous))
    return EXIT_CAPPED


def _spec(arg: str) -> Path:
    spec = store.resolve_spec(arg)
    if spec is None:
        raise SystemExit(f"ah spec codex: no spec matches {arg!r}")
    return spec


def _goal(stem: str, session: str | None) -> dict[str, Any]:
    """The session's goal for `stem`, or an empty one."""
    goal = store.read_json(store.goal_path(session)) if session else None
    return goal if goal and stem in goal.get("stems", []) else {}


def run(args: argparse.Namespace) -> int:
    spec = _spec(args.spec)
    goal = _goal(spec.stem, args.session)
    base = args.base or goal.get("base_commit")
    if not base:
        raise SystemExit(f"ah spec codex: no goal of this session names {spec.stem}; pass --base")
    branch = args.base_branch or (store.base_of(goal) if goal else store.default_base())
    root = _main_root()
    head = store.branch_commit(branch, root)
    if head is None:
        raise SystemExit(f"ah spec codex: the branch {branch!r} does not resolve")
    folder = _passes(spec.stem)
    number = _next_pass(folder)
    previous = store.load_review(spec.stem)
    if _reviewed(folder) >= store.PASS_CAP:
        return _capped(spec, number, previous)
    tree = root / ".claude" / "worktrees" / f"codex-review-{spec.stem}-{number}"
    one = Pass(spec, tree, base, head, folder / f"pass-{number}", number, previous, None, branch)
    text = prompt(one, _digest(spec, args.session, args.projects_root, branch), _reply(args.reply))
    _git(root, "worktree", "add", "--quiet", "--detach", str(tree), head)
    try:
        link_dependencies(root, tree)
        review = review_once(one, text)
    finally:
        removed = subprocess.run(
            ["git", "worktree", "remove", "--force", str(tree)],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if removed.returncode != 0:
            print(
                f"ah spec codex: {tree} was not removed: {removed.stderr.strip()}", file=sys.stderr
            )
    if review is not None:
        # `reviews` counts the passes that reached a verdict, this one included, so the Stop
        # hook caps on the number this file counts rather than on the folder number.
        review = {**review, "reviews": _reviewed(folder) + 1}
        store.write_json(one.folder / "review.json", review)
        store.write_review(review)
    return _exit(review)


def task(args: argparse.Namespace) -> int:
    spec = _spec(args.spec)
    tree = Path(args.worktree).resolve()
    head = _git(tree, "rev-parse", "HEAD")
    branch = _git(tree, "rev-parse", "--abbrev-ref", "HEAD")
    label = f"detached-{head[:9]}" if branch == "HEAD" else branch.replace("/", "-")
    tasks = _passes(spec.stem) / "tasks" / label
    number = _next_pass(tasks)
    # The newest earlier pass that saved a review: a pass with no verdict saves none.
    saved = (store.read_json(tasks / f"pass-{n}" / "review.json") for n in range(number - 1, 0, -1))
    previous = next((review for review in saved if review is not None), None)
    goal = _goal(spec.stem, args.session)
    base_branch = store.base_of(goal) if goal else store.default_base()
    one = Pass(
        spec,
        tree,
        args.base,
        head,
        tasks / f"pass-{number}",
        number,
        previous,
        args.ids,
        base_branch,
    )
    text = prompt(
        one, _digest(spec, args.session, args.projects_root, base_branch), _reply(args.reply)
    )
    review = review_once(one, text)
    if review is not None:
        store.write_json(one.folder / "review.json", review)
    return _exit(review)


def _reply(path: str | None) -> str | None:
    return Path(path).read_text(encoding="utf-8") if path else None


def main(argv: list[str] | None = None) -> int:
    doc = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(prog="ah spec codex", description=doc)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, helped in (
        ("run", "review the base branch for the goal's spec"),
        ("task", "review one task's commits"),
    ):
        sub = commands.add_parser(name, help=helped)
        sub.add_argument("spec", help="a spec path, basename or stem")
        sub.add_argument(
            "--reply", help="a file with the builder's reply to earlier findings, by ID"
        )
        sub.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID") or None)
        sub.add_argument("--projects-root", type=Path, default=ws.PROJECTS_ROOT)
        if name == "run":
            sub.add_argument("--base", help="the commit the goal started from; default the goal's")
            sub.add_argument(
                "--base-branch",
                help="the branch to review; default the goal's, else harness.baseBranch",
            )
        else:
            sub.add_argument("--worktree", required=True, help="the task's worktree")
            sub.add_argument("--base", required=True, help="the commit the task branched from")
            sub.add_argument(
                "--ids", required=True, help="the requirement IDs the task covers, e.g. R1,R2"
            )
    args = parser.parse_args(argv)
    return run(args) if args.command == "run" else task(args)
