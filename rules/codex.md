# Codex

## Instructions and skills

Codex reads `AGENTS.md` and does not expand `@` imports, so read each path an `@` line names.
Codex does not load `.claude/rules/`: the block that `ah install --project` writes in
`AGENTS.md` lists every rule file. Read the memory index when the start hook did not inject it:
`python3 .claude/agent-harness/bin/ah config get harness.memoryIndex` prints its path.
The links in `.agents/skills/` load `.claude/skills/`. Run a skill as `$lw-land`, not `/lw-land`.

## Hooks

`.codex/hooks.json` registers the same hooks as `.claude/settings.json`. Both files come from
`ah install --project`. The adapter `.claude/agent-harness/codex/run-hook.py` gives each hook the
git root, the Codex session id and the payload shape the hooks read. It turns `exec_command`
into `Bash` and each `apply_patch` file into one `Edit` event.

Codex runs a project hook only after you trust it in `/hooks`. The trust key holds the position
of the entry, so `ah install --project` keeps the order stable, and an install with no manifest
change keeps every entry trusted. A changed entry needs trust again. Cloud Codex runs no local hook:
there, follow the same rules and do not claim that a guard ran.

The guards are accident guards, not a sandbox. A script or shell expansion can get past a
command check. The `push-to-checkout` git hook protects the landing for both clients.

## lw-work-status

`ah work-status` reads Claude Code transcripts. In Codex, use the current task history, the live
agent list when there is one, and `git worktree list --porcelain`. Report the history of a peer
session as unknown. An empty Claude digest is no proof that no Codex work remains.

## lw-goal-spec

Pass explicit spec paths and the Codex session id:

```bash
python3 .claude/agent-harness/hooks/goal-spec.py start --session "$CODEX_THREAD_ID" --level merged <spec>
```

The Codex registration runs the Stop hook with `session_id` from the payload. On a surface with
no local hooks, run `status` yourself and continue until the verdict meets the level.

## lw-spec-audit

Supply the spec paths. In Codex, the fresh auditor is a separate
`codex exec --sandbox read-only --json` process in a detached worktree under `.claude/worktrees/`
at the audited commit. Give it the digest, the commit and the audit rules of the skill, and not
the builder's conclusions. Find its rollout under `$CODEX_HOME/sessions` by the session id in its
`thread.started` event, then record the rows with `--auditor <rollout.jsonl>`. A missing or
mismatched rollout is not a finished audit.
