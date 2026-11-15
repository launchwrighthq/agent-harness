# Instance-agnostic code

Write code that is correct with two or more replicas. A deployed app runs N processes behind a
load balancer, and an autoscaler changes N. Never assume one process.

- **Shared state lives in the database.** A value that requests or replicas must agree on goes
  in a table or a shared cache, never in a module-level variable or map. Process memory holds
  only a cache that a replica can rebuild, where a stale copy on another replica changes no
  answer.
- **Per-user and per-session state lives in the database.** The next request of a user can land
  on another replica. Never key a module-level map by user id.
- **Scheduled work runs once per interval.** Register it as a schedule that takes a leader lock,
  so one replica runs it. A bare timer runs on every replica and does the work N times. Work
  that every replica must do, such as a local cache refresh, says so in a comment at its timer.
- **No state on local disk.** Another replica cannot read the file. A local file is for one
  request, a build or a dev run only. An embedded database and a data folder are for local dev
  and tests.
- **A single-consumer transport needs one consumer.** Telegram long polling refuses a second
  poller with HTTP 409. With more than one replica, use the webhook transport.
- **An alert, a mail or a payment step is safe to run twice.** Record what was sent in the
  database, and check the record in the same transaction that claims the work.

Run the check of a design with two replicas in mind: what does the second one see, and what
does it do twice?
