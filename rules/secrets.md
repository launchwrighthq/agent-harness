# Secrets

- **Never read a credential file.** That is every `.env*` except a `.example`, a key, a
  certificate, a session file, `.netrc`, `.git-credentials`, and each file that
  `.agents/harness.toml` names in `harness.guardedFiles` and `harness.secretFiles`. The
  `deny-env-reads` and `bash-read-guard` hooks refuse the common forms. They are accident guards,
  not a sandbox, so do not look for a way around them.
- **Read and change an env file only with the env engine:**
  `python3 .claude/agent-harness/bin/ah env <subcommand>`, or `harness.envCommand` of the config.
  Run it alone: the hooks refuse it with a pipe, `;`, `&`, `$`, `<`, `>`, a newline, a double
  quote or a backslash outside single quotes. Read `.env.example` for the key names.
- **`ah env get <KEY> <file>` masks the value.** It prints set or empty, the length and a short
  sha256. Add `--reveal` only when the user asked to see that value, and only for that key.
- **Write a value with `--stdin`**, not `--value`. A value in argv stays in the shell history and
  the transcript.
- **Never print, log or commit a secret value.** A test uses a fake value or a recorded fixture.
- **Never write a credential backup into the tree**, such as `.env.production.bak` or a
  `.local/secrets/` folder. Secrets go to the secret store through the env command.
- **Compare a signature or a token in constant time** (`safeEqual`, `safe_equal`), never with `===`.
- At session start, the `env-drift` hook says when a local env file holds edits that a secret
  store has not received. It names the environment, the store and the push command, and no value.
