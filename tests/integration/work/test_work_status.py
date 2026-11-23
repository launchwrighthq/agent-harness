"""`ah work-status` against fake transcripts, spec files, saved audits and real git worktrees.

Each test builds its own `projects` tree and project folder under `tmp_path`, and passes the
project folder as the root, so no test reads the checkout it runs in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

from transcript import (
    AGENT_A,
    AGENT_B,
    AGENT_C,
    AGENT_D,
    AGENT_E,
    SID,
    SLUG,
    T0,
    WT_SLUG,
    Projects,
    Record,
    agent_spawn,
    assistant_text,
    attachment,
    compact,
    enqueued,
    human,
    notification,
    notification_text,
    peer_prompt,
    queued,
    rec,
    tool_result,
    tool_use,
)

from agent_harness.spec import store
from agent_harness.work import status as ws

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "ah"
SPECS = "docs/specs"
PLANS = "docs/superpowers/plans"
GIT_DATE = "2026-09-07T10:01:30Z"

SPEC_NEW = """# New

Date: 2026-09-07
Status: draft

## 1. What is true today

The queue reads a table. It has no gate.

### 3a. Refuse at the enqueue

Refuse when the row is missing. Nothing else changes.

## 4. Rollout

| Step | Action |
|---|---|
| one | ship |
"""

SPEC_OLD = "# Old\n\nDate: 2026-09-01\nStatus: implemented\n\n## 1. Old section\n\nOld text.\n"
SPEC_DRAFT = "# Draft\n\nDate: 2026-09-05\nStatus: draft\n\n## 1. Build it\n\nNot built.\n"


@pytest.fixture
def fx(tmp_path: Path) -> Projects:
    return Projects(tmp_path)


def digest(fx: Projects, **options: object) -> ws.Digest:
    return ws.build_digest(SID, projects_root=fx.projects, root=fx.proj, **options)  # type: ignore[arg-type]


def run_ah(fx: Projects, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("AH_HANDED_OVER", "AH_ROOT")}
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "work-status", *args, "--projects-root", str(fx.projects)],
        cwd=fx.proj,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def git(*args: str, cwd: Path) -> str:
    """Every commit is stamped inside the fixture's own clock, not the wall clock.

    `landed` reads the commits made during one agent's life, and an agent in these cases lives
    from 10:01 to 10:04 on a day in 2026.
    """
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_COMMITTER_DATE": GIT_DATE, "GIT_AUTHOR_DATE": GIT_DATE},
    ).stdout.strip()


def with_cwd(fx: Projects, records: list[Record]) -> list[Record]:
    return [{**record, "cwd": str(fx.proj)} for record in records]


def test_merges_a_split_session_in_timestamp_order(fx: Projects) -> None:
    wt = "/tmp/proj/.claude/worktrees/wt-a"
    fx.write(
        SLUG,
        [
            {"type": "ai-title", "aiTitle": "Early title", "sessionId": SID},
            human(0, "first"),
            assistant_text(1, "on it"),
            rec("relocated", 1, relocatedCwd=wt),
        ],
    )
    fx.write(
        WT_SLUG,
        [
            {"type": "custom-title", "customTitle": "rename-field", "sessionId": SID},
            {**human(2, "second"), "cwd": wt},
            compact(3),
            {**human(4, "third"), "cwd": wt},
        ],
    )

    d = digest(fx)

    assert [c.text for c in d.chat] == ["first", "second", "third"]
    assert (d.session.name, d.session.cwd, d.session.worktree) == ("rename-field", wt, wt)
    assert d.session.compactions == 1
    assert (d.session.started, d.session.last) == (T0, T0 + timedelta(minutes=4))


def test_exits_1_naming_the_path_of_a_missing_transcript(fx: Projects) -> None:
    done = run_ah(fx, "--session", "nope")

    assert done.returncode == 1
    assert "nope.jsonl" in done.stderr
    assert str(fx.projects) in done.stderr


def test_makes_a_queued_human_message_a_chat_row_typed_while_busy(fx: Projects) -> None:
    peer = {"kind": "peer", "name": "session-a", "body": "main moved to 1a2b3c4"}
    summary = "This session is being continued from a previous conversation"
    fx.write(
        SLUG,
        [
            human(0, "build the thing"),
            assistant_text(1, "working"),
            queued(2, "also rename X\nplease", {"kind": "human"}),
            queued(3, "<task-notification>...</task-notification>", None),
            queued(4, "peer text", peer),
            human(5, "<command-name>/model</command-name> <command-args></command-args>"),
            human(6, "<local-command-stdout>Set model</local-command-stdout>"),
            {**human(7, summary), "isCompactSummary": True},
            assistant_text(8, "resumed"),
            human(9, "<local-command-caveat>Caveat: ...</local-command-caveat>"),
            human(10, "<system-reminder>quoted file content</system-reminder>"),
        ],
    )

    d = digest(fx)

    assert [c.text for c in d.chat] == ["build the thing", "also rename X please"]
    first, typed = d.chat
    assert (first.typed_while_busy, first.answered) == (False, True)
    assert (typed.typed_while_busy, typed.answered) == (True, True)
    assert typed.at == T0 + timedelta(minutes=2)
    assert [(p.source, p.text, p.answered) for p in d.peers] == [
        ("peer:session-a", "main moved to 1a2b3c4", True)
    ]


def test_counts_no_loaded_skill_as_a_prompt(fx: Projects) -> None:
    fx.write(
        SLUG,
        [
            human(0, "use the skill"),
            {**human(3, "Base directory for this skill: x"), "isMeta": True},
            assistant_text(4, "loaded"),
        ],
    )

    d = digest(fx)

    assert [c.text for c in d.chat] == ["use the skill"]
    assert d.session.human_prompts == 1
    assert "Base directory" not in ws.render_markdown(d)


def test_keeps_the_text_of_a_queued_prompt_given_as_content_blocks(fx: Projects) -> None:
    source = {"type": "base64", "media_type": "image/png", "data": "AAAA"}
    image = {"type": "image", "source": source}
    fx.write(
        SLUG,
        [
            queued(1, [image, {"type": "text", "text": "see this screenshot"}], {"kind": "human"}),
            queued(2, [image], {"kind": "human"}),
        ],
    )

    d = digest(fx)

    assert [c.text for c in d.chat] == ["see this screenshot", "(no text)"]
    assert all(c.typed_while_busy for c in d.chat)
    assert "AAAA" not in ws.render_markdown(d) + ws.to_json(d)


def test_keeps_tool_result_bodies_out_of_the_digest(fx: Projects) -> None:
    inp = {"command": "env | grep TOKEN", "description": "List token names"}
    fx.write(
        SLUG,
        [
            human(0, "check the env"),
            tool_use(1, "t1", "Bash", inp),
            tool_result(1, "t1", "SECRET_TOKEN=abc123", {"stdout": "SECRET_TOKEN=abc123"}),
            assistant_text(2, "one token is set"),
        ],
    )

    d = digest(fx)

    out = ws.render_markdown(d) + ws.to_json(d)
    assert "abc123" not in out
    assert "grep TOKEN" not in out
    assert [(c.text, c.answered) for c in d.chat] == [("check the env", True)]


def test_keeps_the_shell_commands_of_the_user_and_their_output_out_of_the_chat(
    fx: Projects,
) -> None:
    fx.write(
        SLUG,
        [
            human(0, "check the build"),
            assistant_text(1, "green"),
            human(2, "<bash-input>printenv SERVICE_TOKEN</bash-input>"),
            human(3, "<bash-stdout>value-kilo-42</bash-stdout><bash-stderr></bash-stderr>"),
            human(4, "<bash-stderr>warning-text-11</bash-stderr>"),
            queued(5, "<bash-input>printenv OTHER_TOKEN</bash-input>", {"kind": "human"}),
        ],
    )

    d = digest(fx)

    out = ws.render_markdown(d) + ws.to_json(d)
    assert [(c.text, c.answered) for c in d.chat] == [("check the build", True)]
    assert d.session.human_prompts == 1
    for word in ("SERVICE_TOKEN", "value-kilo-42", "warning-text-11", "OTHER_TOKEN"):
        assert word not in out


def test_names_the_repository_of_a_session_that_ran_in_another_one(fx: Projects) -> None:
    git("init", "--quiet", cwd=fx.proj)
    other = fx.root / "other"
    (other / "src").mkdir(parents=True)
    git("init", "--quiet", cwd=other)
    fx.write(SLUG, [{**human(0, "go"), "cwd": str(other / "src")}])

    d = digest(fx)

    assert d.repository == str(other)
    assert (
        f"repository {other}: not the repository that this command runs in, so its worktrees"
        " and spec files are not read"
    ) in ws.render_markdown(d).splitlines()


def test_names_no_repository_for_a_session_in_a_worktree_of_this_one(fx: Projects) -> None:
    git("init", "--quiet", cwd=fx.proj)
    git("commit", "--quiet", "--allow-empty", "-m", "base", cwd=fx.proj)
    worktree = fx.root / "elsewhere-wt"
    git("worktree", "add", "--quiet", "-b", "wt", str(worktree), cwd=fx.proj)
    fx.write(SLUG, [{**human(0, "go"), "cwd": str(worktree)}])

    d = digest(fx)

    assert d.repository is None
    assert "repository " not in ws.render_markdown(d)


def test_reads_agent_states_sections_worktrees_and_reports(fx: Projects) -> None:
    spec = f"{SPECS}/2026-09-07-x-design.md"
    prompt_a = f"Work ONLY in /tmp/proj/.claude/worktrees/wt-a. Read {spec}, section 5.4 is yours."
    prompt_b = f"Review section 2 of {spec}. Do not edit."
    running = {
        "type": "task_status",
        "taskId": AGENT_A,
        "description": "Implement section 5.4",
        "status": "running",
        "deltaSummary": "editing repo.py",
    }
    fx.write(
        SLUG,
        [
            human(0, "build it"),
            *agent_spawn(0, "t1", AGENT_A, "Implement section 5.4", prompt_a),
            *agent_spawn(1, "t2", AGENT_B, "Review section 2", prompt_b, model="sonnet"),
            attachment(3, running),
            notification(5, AGENT_B, result="Two findings.\nDetails follow."),
            notification(6, AGENT_B, result="Nothing new."),
        ],
    )

    a, b = digest(fx).agents

    assert (a.task_id, a.kind, a.state, a.model, a.agent_type) == (
        AGENT_A,
        "agent",
        "running",
        "opus",
        "general-purpose",
    )
    assert (a.section, a.worktree, a.spec) == ("5.4", "/tmp/proj/.claude/worktrees/wt-a", spec)
    assert (a.report, a.last_seen) == ("editing repo.py", T0 + timedelta(minutes=3))
    assert (b.state, b.notifications, b.report, b.section) == ("reported", 2, "Nothing new.", "2")
    assert (b.worktree, b.last_seen) == (None, T0 + timedelta(minutes=6))


def test_reads_a_blocking_agent_a_subagent_file_a_stop_a_command_and_a_workflow(
    fx: Projects,
) -> None:
    explore = {"description": "Explore the repo", "prompt": "Find X", "subagent_type": "Explore"}
    command = {
        "command": "make verify > /tmp/x.log",
        "description": "Run the gate",
        "run_in_background": True,
    }
    launched = {
        "status": "async_launched",
        "taskId": "wt1",
        "workflowName": "task-loop",
        "summary": "Implement plan tasks in order",
    }
    found = [{"type": "text", "text": "Found X in repo.py\nand elsewhere"}]
    fx.write(
        SLUG,
        [
            human(0, "go"),
            tool_use(1, "t1", "Agent", explore),
            tool_result(1, "t1", found, {"status": "completed", "agentId": AGENT_C}),
            *agent_spawn(2, "t2", AGENT_D, "Implement part 3", "part 3 of the spec"),
            *agent_spawn(3, "t3", AGENT_E, "Implement part 4", "part 4 of the spec"),
            tool_use(4, "t4", "TaskStop", {"task_id": AGENT_E}),
            tool_use(5, "t5", "Bash", command),
            tool_result(5, "t5", "", {"stdout": "", "backgroundTaskId": "b1"}),
            notification(
                7,
                "b1",
                summary='Background command "Run the gate" completed (exit code 2)',
                result="FAILED tests/test_x.py",
            ),
            tool_use(6, "t6", "Workflow", {"script": "export const meta = {}"}),
            tool_result(6, "t6", "Workflow launched in background. Task ID: wt1", launched),
            notification(9, AGENT_E, result="stopped early"),
        ],
    )
    fx.subagent(SLUG, AGENT_D, "Implement part 3", "part 3", "Part 3 is done in wt-a.\n\n## Files")

    d = digest(fx)

    by_id = {row.task_id: row for row in d.agents + d.background}
    c, dd, e, bg, wf = (by_id[key] for key in (AGENT_C, AGENT_D, AGENT_E, "b1", "wt1"))
    assert (c.state, c.report, c.agent_type) == ("reported", "Found X in repo.py", "Explore")
    assert (dd.state, dd.report, dd.section) == ("running", "Part 3 is done in wt-a.", "3")
    assert e.state == "stopped"
    assert (bg.kind, bg.description, bg.state, bg.exit_code, bg.report) == (
        "background",
        "Run the gate",
        "failed",
        2,
        "FAILED tests/test_x.py",
    )
    assert (wf.kind, wf.description, wf.state) == (
        "workflow",
        "Implement plan tasks in order",
        "running",
    )
    assert [row.task_id for row in d.background] == ["b1", "wt1"]
    assert "/tmp/x.log" not in ws.render_markdown(d)


def test_takes_the_section_from_the_description_and_cuts_it_to_one_line(fx: Projects) -> None:
    fx.write(
        SLUG,
        [
            human(0, "go"),
            *agent_spawn(
                1,
                "t1",
                AGENT_A,
                "Review Task 4\n(spec + quality)",
                "## Part 1: Spec Compliance\nRead the plan.",
            ),
            *agent_spawn(2, "t2", AGENT_B, "Rewrite the loader", "Do section 2 of the plan."),
        ],
    )
    fx.subagent(SLUG, AGENT_C, "Explore the\n\ttree", "find it", "Found it.")

    d = digest(fx)

    by_id = {row.task_id: row for row in d.agents}
    assert (by_id[AGENT_A].section, by_id[AGENT_A].description) == (
        "4",
        "Review Task 4 (spec + quality)",
    )
    assert by_id[AGENT_B].section == "2"
    assert by_id[AGENT_C].description == "Explore the tree"
    rendered = ws.render_markdown(d).splitlines()
    for text in ("Review Task 4 (spec + quality)", "Explore the tree"):
        assert len([line for line in rendered if text in line]) == 1


def test_makes_a_row_from_a_notification_for_an_unknown_task(fx: Projects) -> None:
    summary = 'Agent "Rename a field" finished'
    fx.write(SLUG, [human(0, "continue"), notification(1, AGENT_A, summary=summary, result="Ok.")])

    rows = digest(fx).agents

    assert [(r.task_id, r.description, r.state, r.report) for r in rows] == [
        (AGENT_A, summary, "reported", "Ok.")
    ]


def test_marks_a_spawn_with_an_error_string_result_failed(fx: Projects) -> None:
    inp = {"description": "Implement part 1", "prompt": "part 1", "subagent_type": "nope"}
    fx.write(
        SLUG,
        [
            human(0, "spawn it"),
            tool_use(1, "t1", "Agent", inp),
            tool_result(1, "t1", "Error: unknown agent type", "Error: Exit code 1\nunknown type"),
        ],
    )

    rows = digest(fx).agents

    assert [(r.state, r.task_id, r.report) for r in rows] == [
        ("failed", None, "Error: Exit code 1")
    ]


def write_specs(fx: Projects) -> None:
    specs = fx.proj / SPECS
    specs.mkdir(parents=True)
    (specs / "2026-09-01-old-design.md").write_text(SPEC_OLD, encoding="utf-8")
    (specs / "2026-09-07-new-design.md").write_text(SPEC_NEW, encoding="utf-8")


def test_parses_the_named_spec_and_links_its_agents(fx: Projects) -> None:
    write_specs(fx)
    old, new = f"{SPECS}/2026-09-01-old-design.md", f"{SPECS}/2026-09-07-new-design.md"
    fx.write(
        SLUG,
        with_cwd(
            fx,
            [
                human(0, f"read {old}"),
                assistant_text(1, "read"),
                human(5, f"now write {new}"),
                assistant_text(6, "written"),
                *agent_spawn(7, "t1", AGENT_A, "Implement 3a", f"Implement section 3a of {new}"),
                human(8, f"the old one {old} is done"),
            ],
        ),
    )

    assert digest(fx).spec.path == old  # type: ignore[union-attr]
    spec = digest(fx, spec_override=new).spec
    assert spec is not None
    assert (spec.path, spec.status, spec.kind) == (new, "draft", "spec")
    assert spec.first_mention == T0 + timedelta(minutes=5)
    assert [(s.number, s.title, s.first_sentence) for s in spec.sections] == [
        ("1", "What is true today", "The queue reads a table."),
        ("3a", "Refuse at the enqueue", "Refuse when the row is missing."),
        ("4", "Rollout", ""),
    ]
    assert [s.agents for s in spec.sections] == [[], ["Implement 3a"], []]


def test_reads_the_latest_mentioned_spec_without_an_override(fx: Projects) -> None:
    write_specs(fx)
    new = f"{SPECS}/2026-09-07-new-design.md"
    records = [
        human(0, f"read {SPECS}/2026-09-01-old-design.md"),
        human(5, f"now {new}"),
        assistant_text(6, "ok"),
    ]
    fx.write(SLUG, with_cwd(fx, records))

    spec = digest(fx).spec

    assert spec is not None
    assert (spec.path, spec.first_mention) == (new, T0 + timedelta(minutes=5))


def test_keeps_the_path_of_a_missing_spec_with_no_sections(fx: Projects) -> None:
    fx.write(SLUG, with_cwd(fx, [human(0, f"see {SPECS}/2026-09-07-gone-design.md")]))

    spec = digest(fx).spec

    assert spec is not None
    assert (spec.path, spec.resolved, spec.sections) == (
        f"{SPECS}/2026-09-07-gone-design.md",
        None,
        [],
    )


def test_corrects_each_reported_agent_from_its_worktree_commits(fx: Projects) -> None:
    proj = fx.proj
    git("init", "-q", "-b", "main", cwd=proj)
    (proj / "a.txt").write_text("a\n", encoding="utf-8")
    git("add", "a.txt", cwd=proj)
    git("commit", "-q", "-m", "base", cwd=proj)
    wts = proj / ".claude" / "worktrees"
    wts.mkdir(parents=True)

    def branch(name: str, commit: bool = True) -> None:
        git("worktree", "add", "-q", str(wts / name), "-b", name, cwd=proj)
        if commit:
            (wts / name / f"{name}.txt").write_text("x\n", encoding="utf-8")
            git("add", f"{name}.txt", cwd=wts / name)
            git("commit", "-q", "-m", name, cwd=wts / name)

    # Each merge runs while it is still the merge it claims to be, and wt-merged goes last so
    # its HEAD is the tip of main at the end.
    branch("wt-empty", commit=False)
    branch("wt-dirty", commit=False)
    branch("wt-landed")
    branch("wt-ff-behind")
    git("merge", "-q", "--ff-only", "wt-ff-behind", cwd=proj)
    branch("wt-noff")
    git("merge", "-q", "--no-ff", "--no-edit", "wt-noff", cwd=proj)
    (proj / "b.txt").write_text("b\n", encoding="utf-8")
    git("add", "b.txt", cwd=proj)
    git("commit", "-q", "-m", "main moves on", cwd=proj)
    branch("wt-merged")
    git("merge", "-q", "--ff-only", "wt-merged", cwd=proj)
    branch("wt-late")
    (wts / "wt-dirty" / "d.txt").write_text("d\n", encoding="utf-8")
    records = [human(0, "go")]
    names = ("wt-landed", "wt-merged", "wt-empty", "wt-dirty", "wt-noff", "wt-ff-behind")
    for i, name in enumerate(names):
        aid = "a" + "6789ab"[i] * 16
        records += agent_spawn(1, f"t{i}", aid, f"Do {name}", f"Work in {wts / name}. Part {i}.")
        records.append(notification(2, aid))
    records += agent_spawn(1, "t9", AGENT_B, "Bare name", "Work in .claude/worktrees/wt-landed.")
    records.append(notification(2, AGENT_B))
    # Its worktree holds a commit made before this agent was spawned. The commits are somebody
    # else's, so the row keeps what its notification claimed.
    records += agent_spawn(20, "t10", AGENT_C, "Do wt-late", f"Work in {wts / 'wt-late'}.")
    records.append(notification(21, AGENT_C))
    fx.write(SLUG, with_cwd(fx, records))

    d = digest(fx)

    state = {row.description: (row.state, row.dirty) for row in d.agents}
    assert state == {
        "Do wt-landed": ("landed", False),
        "Do wt-merged": ("merged", False),
        "Do wt-empty": ("reported", False),
        "Do wt-dirty": ("reported", True),
        "Do wt-noff": ("merged", False),
        "Do wt-ff-behind": ("reported", False),
        "Bare name": ("landed", False),
        "Do wt-late": ("reported", False),
    }
    bare = next(row for row in d.agents if row.description == "Bare name")
    assert bare.worktree == str(wts / "wt-landed")
    rendered = ws.render_markdown(d).splitlines()
    assert "| reported | dirty |" in next(line for line in rendered if "Do wt-dirty" in line)
    assert "dirty" not in next(line for line in rendered if "Do wt-landed" in line)


@pytest.fixture
def window(fx: Projects) -> Projects:
    fx.write(
        SLUG,
        [
            human(0, "start"),
            assistant_text(1, "ok"),
            *agent_spawn(0, "t1", AGENT_D, "Old finished", "part 1"),
            *agent_spawn(0, "t2", AGENT_E, "Old running", "part 2"),
            notification(10, AGENT_D),
            queued(20, "never answered", {"kind": "human"}),
            human(600, "status?"),
        ],
    )
    return fx


def test_hides_and_counts_finished_rows_before_the_anchor(window: Projects) -> None:
    d = digest(window)

    assert d.anchor == T0 + timedelta(minutes=600) - ws.DEFAULT_WINDOW
    assert [row.description for row in d.agents] == ["Old running"]
    assert [c.text for c in d.chat] == ["never answered", "status?"]
    assert d.hidden_count == 2


def test_shows_every_row_with_all(window: Projects) -> None:
    d = digest(window, show_all=True)

    assert d.anchor is None
    assert (len(d.agents), len(d.chat), d.chat_total, d.hidden_count) == (2, 3, 3, 0)


def test_moves_the_anchor_with_since(window: Projects) -> None:
    d = digest(window, since="12h")

    assert d.anchor == T0 + timedelta(minutes=600) - timedelta(hours=12)
    assert d.hidden_count == 0


def test_parses_since_in_minutes_hours_and_days() -> None:
    assert ws.parse_since("90m") == timedelta(minutes=90)
    assert ws.parse_since("2d") == timedelta(days=2)
    with pytest.raises(ValueError, match="soon"):
        ws.parse_since("soon")


def test_never_lets_the_cap_drop_an_unanswered_row(fx: Projects) -> None:
    records = [human(0, "the oldest ask, never answered")]
    for minute in range(1, 71, 2):
        records += [human(minute, f"ask {minute}"), assistant_text(minute + 1, "done")]
    fx.write(SLUG, records)

    d = digest(fx, since="99d")

    total = 36
    assert (len(d.chat), d.chat_total, d.hidden_count) == (ws.CHAT_CAP + 1, total, 0)
    assert d.chat[0].text == "the oldest ask, never answered"
    assert all(c.answered for c in d.chat[1:])
    assert f"showing {ws.CHAT_CAP + 1} of {total}" in ws.render_markdown(d)
    assert len(digest(fx, show_all=True).chat) == total


def test_renders_every_heading_and_prints_the_digest_as_json(window: Projects) -> None:
    out = ws.render_markdown(digest(window))

    for heading in ("# work-status", "## Spec", "## Agents", "## Background", "## Chat"):
        assert heading in out
    for word in ("## Peers", "## Hidden", "2 finished rows before", "--all", "unanswered"):
        assert word in out
    assert "typed-while-busy" in out
    done = run_ah(window, "--session", SID, "--json", "--since", "12h")
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    assert (data["hidden_count"], len(data["agents"]), data["session"]["human_prompts"]) == (
        0,
        2,
        2,
    )


@pytest.mark.parametrize(
    ("met", "line"),
    [(None, "goal: ship it"), (True, "goal: ship it (met)"), (False, "goal: ship it (not met)")],
)
def test_prints_the_goal_verdict_only_when_one_exists(
    fx: Projects, met: bool | None, line: str
) -> None:
    goal: dict[str, object] = {"type": "goal_status", "condition": "ship it"}
    if met is not None:
        goal["met"] = met
    fx.write(SLUG, [human(0, "go"), attachment(1, goal)])

    out = ws.render_markdown(digest(fx)).splitlines()

    assert [row for row in out if row.startswith("goal:")] == [line]


def test_says_local_in_the_header_and_converts_the_times(fx: Projects) -> None:
    fx.write(SLUG, [human(0, "go")])

    out = ws.render_markdown(digest(fx))

    assert out.splitlines()[1].endswith("| times local")
    assert f"- {T0.astimezone().strftime('%H:%M')} [unanswered] go" in out


@pytest.mark.parametrize("bad", ["*", "?", "a[b", "../elsewhere", "a\\b"])
def test_refuses_a_glob_in_the_session_id(fx: Projects, bad: str) -> None:
    fx.write(SLUG, [human(0, "go")])

    done = run_ah(fx, "--session", bad)

    assert (done.returncode, done.stdout) == (2, "")
    assert "one session id" in done.stderr


def test_clears_a_bare_worktree_name_with_no_cwd(fx: Projects) -> None:
    spawn = agent_spawn(1, "t1", AGENT_A, "Do it", "Work in .claude/worktrees/wt-a only.")
    fx.write(SLUG, [{k: v for k, v in r.items() if k != "cwd"} for r in [human(0, "go"), *spawn]])

    d = digest(fx)

    assert (d.session.cwd, d.agents[0].worktree) == (None, None)


def test_turns_a_multi_line_summary_into_one_row(fx: Projects) -> None:
    launched = {"status": "async_launched", "taskId": "wt1", "summary": "Run the plan\nin order"}
    fx.write(
        SLUG,
        [
            human(0, "go"),
            notification(1, AGENT_A, summary="Agent finished\nwith a second line", result=""),
            tool_use(2, "t1", "Workflow", {"script": "export const meta = {}"}),
            tool_result(2, "t1", "Workflow launched in background. Task ID: wt1", launched),
        ],
    )

    d = digest(fx)

    assert d.agents[0].description == "Agent finished with a second line"
    assert d.background[0].description == "Run the plan in order"
    rows = [line for line in ws.render_markdown(d).splitlines() if line.startswith("- ")]
    assert len(rows) == 3


def test_keeps_the_newest_answered_rows_under_the_cap(fx: Projects) -> None:
    records: list[Record] = []
    for minute in range(0, 80, 2):
        records += [human(minute, f"ask {minute}"), assistant_text(minute + 1, "done")]
    fx.write(SLUG, records)

    d = digest(fx)

    assert (len(d.chat), d.chat_total, d.hidden_count) == (ws.CHAT_CAP, 40, 0)
    assert (d.chat[0].text, d.chat[-1].text) == ("ask 20", "ask 78")
    assert f"showing {ws.CHAT_CAP} of 40" in ws.render_markdown(d)


@pytest.fixture
def named(fx: Projects) -> Projects:
    """A git checkout whose spec folders hold a draft, a new spec, a plan and an archived spec.

    An audit is stale exactly when the commit it read is not the tip of main.
    """
    proj = fx.proj
    (proj / SPECS / "archive").mkdir(parents=True)
    (proj / PLANS).mkdir(parents=True)
    (proj / SPECS / "2026-09-07-new-design.md").write_text(SPEC_NEW, encoding="utf-8")
    (proj / SPECS / "2026-09-05-draft-design.md").write_text(SPEC_DRAFT, encoding="utf-8")
    archived = proj / SPECS / "archive" / "2026-09-01-old-design.md"
    archived.write_text(SPEC_OLD, encoding="utf-8")
    (proj / PLANS / "2026-09-06-plan.md").write_text(SPEC_DRAFT, encoding="utf-8")
    (proj / ".agents").mkdir()
    (proj / ".agents" / "harness.toml").write_text(
        f'[specs]\ndirs = ["{SPECS}", "{PLANS}"]\n', encoding="utf-8"
    )
    git("init", "-q", "-b", "main", cwd=proj)
    git("add", "docs", ".agents", cwd=proj)
    git("commit", "-q", "-m", "specs", cwd=proj)
    return fx


def write_named(fx: Projects, records: list[Record]) -> None:
    fx.write(SLUG, with_cwd(fx, records))


def audit(fx: Projects, stem: str, states: list[str], head: str | None = None) -> Path:
    rows = [
        {"id": f"R{i}", "requirement": "x", "state": s, "question": "which?"}
        for i, s in enumerate(states, 1)
    ]
    head = head or git("rev-parse", "main", cwd=fx.proj)
    path = store.audit_path(stem, fx.proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stem": stem, "main_commit": head, "rows": rows}), encoding="utf-8")
    return path


def spec_lines(d: ws.Digest) -> list[str]:
    out = ws.render_markdown(d).splitlines()
    start = out.index("## Specs named")
    return out[start + 1 : out.index("", start)]


def test_lists_every_spec_named_in_one_transcript(named: Projects) -> None:
    draft = f"{SPECS}/2026-09-05-draft-design.md"
    write_named(
        named,
        [
            human(0, f"read {draft} and {PLANS}/2026-09-06-plan.md"),
            assistant_text(1, f"the plan is {draft}"),
            human(5, f"now {SPECS}/2026-09-07-gone-design.md"),
            assistant_text(6, "ok"),
        ],
    )

    d = digest(named)

    assert [(s.basename, s.status, s.audit) for s in d.specs_named] == [
        ("2026-09-05-draft-design.md", "draft", "not audited"),
        ("2026-09-06-plan.md", "draft", "not audited"),
        ("2026-09-07-gone-design.md", "missing", "not audited"),
    ]
    first = d.specs_named[0]
    assert (first.first_mention, first.last_mention) == (T0, T0 + timedelta(minutes=1))
    lines = spec_lines(d)
    clock = (T0 + timedelta(minutes=5)).astimezone().strftime("%H:%M")
    assert lines[0].startswith("- 2026-09-05-draft-design.md | draft | ")
    assert lines[2] == f"- 2026-09-07-gone-design.md | missing | {clock} to {clock} | not audited"
    assert len(lines) == 3
    out = ws.render_markdown(d)
    assert out.index("## Spec\n") < out.index("## Specs named") < out.index("## Agents")
    assert d.spec is not None
    assert d.spec.path.endswith("2026-09-07-gone-design.md")
    data = json.loads(ws.to_json(d))
    assert [s["basename"] for s in data["specs_named"]] == [s.basename for s in d.specs_named]


def test_folds_a_settled_all_done_spec_into_a_count(named: Projects) -> None:
    write_named(
        named,
        [
            human(0, f"is {SPECS}/2026-09-01-old-design.md done?"),
            human(1, f"and {SPECS}/2026-09-07-new-design.md"),
            assistant_text(2, "yes"),
        ],
    )
    audit(named, "2026-09-01-old-design", ["done", "deferred-by-operator"])

    d = digest(named)

    old = d.specs_named[0]
    assert (old.status, old.audit) == ("implemented", "all done")
    assert str(old.resolved).endswith("archive/2026-09-01-old-design.md")
    lines = spec_lines(d)
    assert lines[-1] == "- 1 settled, all done or not audited; --all lists them"
    assert not any("old-design" in line for line in lines)
    assert any(line.startswith("- 2026-09-07-new-design.md") for line in lines)
    assert len(json.loads(ws.to_json(d))["specs_named"]) == 2
    unfolded = spec_lines(digest(named, show_all=True))
    assert any("old-design.md | implemented |" in line for line in unfolded)


def test_shows_an_unsettled_spec_older_than_the_anchor(named: Projects) -> None:
    write_named(
        named,
        [
            human(0, f"write {SPECS}/2026-09-05-draft-design.md"),
            assistant_text(1, "written"),
            human(600, f"now {SPECS}/2026-09-07-new-design.md"),
            assistant_text(601, "ok"),
        ],
    )

    d = digest(named)

    assert d.anchor == T0 + timedelta(minutes=600)
    assert d.specs_named[0].last_mention < d.anchor
    lines = spec_lines(d)
    assert len(lines) == 2
    assert lines[0].startswith("- 2026-09-05-draft-design.md | draft | ")


def test_reads_each_audit_state_of_a_named_spec(named: Projects) -> None:
    names = (
        "2026-09-05-draft-design",
        "2026-09-06-plan",
        "2026-09-07-new-design",
        "2026-09-01-old-design",
    )
    write_named(
        named,
        [
            human(0, f"read {SPECS}/{names[0]}.md"),
            human(1, f"read {PLANS}/{names[1]}.md"),
            human(2, f"read {SPECS}/{names[2]}.md"),
            human(3, f"read {SPECS}/{names[3]}.md"),
            human(4, f"read {SPECS}/2026-09-02-broken-design.md"),
        ],
    )
    audit(named, names[1], ["done"], head="0" * 40)
    audit(named, names[2], ["done", "partly", "missing", "blocked"])
    audit(named, names[3], ["done"])
    audit(named, "2026-09-02-broken-design", []).write_text("{half", encoding="utf-8")

    d = digest(named, show_all=True)

    by_name = {Path(s.basename).stem: s for s in d.specs_named}
    assert by_name[names[0]].audit == "not audited"
    assert by_name[names[1]].audit == "stale"
    opened = by_name[names[2]]
    assert (opened.audit, opened.open_ids, opened.blocked_ids) == (
        "2 open, 1 blocked",
        ["R2", "R3"],
        ["R4"],
    )
    assert by_name[names[3]].audit == "all done"
    # One unreadable audit names itself on its own row rather than ending the digest.
    assert by_name["2026-09-02-broken-design"].audit.startswith("unreadable: ")
    lines = spec_lines(d)
    assert "| stale" in next(line for line in lines if names[1] in line)
    assert next(line for line in lines if names[2] in line).endswith("| 2 open, 1 blocked")


def test_reads_a_spec_path_written_inside_the_archive_folder(named: Projects) -> None:
    archived = f"{SPECS}/archive/2026-09-01-old-design.md"
    write_named(named, [human(0, f"was {archived} settled?"), assistant_text(1, "yes")])

    d = digest(named, show_all=True)

    assert [(s.path, s.status) for s in d.specs_named] == [(archived, "implemented")]


def test_reads_a_notification_from_the_queue_and_from_a_queued_attachment(fx: Projects) -> None:
    first = notification_text(AGENT_A, result="Built.")
    fx.write(
        SLUG,
        [
            human(0, "go"),
            *agent_spawn(1, "t1", AGENT_A, "Build the loader", "build it"),
            *agent_spawn(2, "t2", AGENT_B, "Review the loader", "review it"),
            enqueued(3, first),
            queued(4, first, None),
            queued(5, notification_text(AGENT_B, status="failed", result="Red."), None),
        ],
    )

    by_id = {row.task_id: row for row in digest(fx).agents}

    a, b = by_id[AGENT_A], by_id[AGENT_B]
    assert (a.state, a.report, a.notifications) == ("reported", "Built.", 1)
    assert (b.state, b.report) == ("failed", "Red.")


def test_ends_an_agent_whose_subagent_file_ends_its_turn(fx: Projects) -> None:
    fx.write(SLUG, [human(0, "go"), *agent_spawn(1, "t1", AGENT_A, "Audit A", "read A")])
    fx.subagent(SLUG, AGENT_A, "Audit A", "read A", "SAFE", stop_reason="end_turn")
    fx.subagent(SLUG, AGENT_B, "Audit B", "read B", "No findings.", stop_reason="end_turn")
    fx.subagent(SLUG, AGENT_C, "Audit C", "read C", "Reading.", stop_reason="tool_use")

    by_id = {row.task_id: row for row in digest(fx).agents}

    assert [(by_id[i].state, by_id[i].report) for i in (AGENT_A, AGENT_B, AGENT_C)] == [
        ("reported", "SAFE"),
        ("reported", "No findings."),
        ("running", "Reading."),
    ]


def test_reads_a_peer_message_filed_as_a_meta_user_record(fx: Projects) -> None:
    fx.write(
        SLUG,
        [
            human(0, "go"),
            assistant_text(1, "working"),
            peer_prompt(2, "release", "Hold every landing."),
            assistant_text(3, "holding"),
        ],
    )

    d = digest(fx)

    assert [c.text for c in d.chat] == ["go"]
    assert [(p.source, p.text, p.answered) for p in d.peers] == [
        ("peer:release", "Hold every landing.", True)
    ]


def test_counts_a_reply_after_a_peer_message_for_the_prompt_before_it(fx: Projects) -> None:
    fx.write(
        SLUG,
        [
            human(0, "land it and push"),
            queued(1, "peer text", {"kind": "peer", "name": "release", "body": "Hold."}),
            assistant_text(2, "landed and pushed"),
            human(3, "next"),
        ],
    )

    chat = digest(fx).chat

    assert [(c.text, c.answered) for c in chat] == [("land it and push", True), ("next", False)]


def test_joins_a_prompt_cut_by_an_interrupt_to_the_one_after_it(fx: Projects) -> None:
    fx.write(
        SLUG,
        [
            human(0, "make the goal skill build in a worktree"),
            human(1, "[Request interrupted by user]"),
            human(2, "merge to local main after"),
            assistant_text(3, "done and merged"),
        ],
    )

    chat = digest(fx).chat

    assert [(c.text, c.answered) for c in chat] == [
        ("make the goal skill build in a worktree", True),
        ("merge to local main after", True),
    ]


def test_names_no_spec_from_the_body_of_an_edit_or_a_write(fx: Projects) -> None:
    body = f"fixture names {SPECS}/2026-09-07-gone-design.md"
    fx.write(
        SLUG,
        with_cwd(
            fx,
            [
                human(0, "go"),
                tool_use(1, "t1", "Read", {"file_path": f"{SPECS}/2026-09-05-draft-design.md"}),
                tool_use(
                    2, "t2", "Edit", {"file_path": "t.py", "old_string": "x", "new_string": body}
                ),
                tool_use(3, "t3", "Write", {"file_path": "u.py", "content": body}),
            ],
        ),
    )

    names = [s.basename for s in digest(fx).specs_named]

    assert names == ["2026-09-05-draft-design.md"]
