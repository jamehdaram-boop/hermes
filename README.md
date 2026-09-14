# Hermes - Phase 1

Operational/execution layer for AI Home. Runs as a systemd --user service
(no root required), exposing a local-only HTTP API so other systems (n8n,
the AI Home coordinator, or a future Claude session) can query VPS state
and trigger a small whitelisted set of safe, non-destructive actions -
without needing raw SSH/shell access for routine checks.

Stdlib only (Python 3, http.server) - zero cost, zero new dependencies.
Bound to 127.0.0.1 only: not reachable from the internet, no new firewall
rule needed, no new attack surface on public ports.

## Files
- hermes.py     - the service (stdlib only)
- hermes.env    - HERMES_TOKEN (bearer auth secret) + HERMES_PORT (mode 600)
- logs/hermes.log - one JSON line per /task call (action, args, client, ts)
- heartbeat.json  - overwritten every 60s; proves Hermes is alive

## API (http://127.0.0.1:8787)
- GET  /health            - no auth; {"status": "ok"}
- GET  /status             - auth required; system_status (cpu/ram/disk/containers)
- POST /task               - auth required; body {"action": "...", "args": {...}}
  Actions:
    - system_status                        - same as GET /status
    - list_containers                      - docker ps -a summary
    - container_status {"name": "<name>"}  - state/health for one whitelisted
      container: apiserver, apiserver-pricedb, n8n, wireguard, sftp,
      guard-honeypot, postgres

Auth: `Authorization: Bearer <HERMES_TOKEN>` header (token in hermes.env).

## How to observe Hermes is running
- systemctl --user status hermes.service
- journalctl --user -u hermes.service -n 20 --no-pager
- cat ~/hermes/heartbeat.json
- tail -f ~/hermes/logs/hermes.log
- curl http://127.0.0.1:8787/health

## Scope / non-goals (Phase 1)
Read-only status + a fixed whitelist of read-only task actions only. No
arbitrary shell execution over HTTP, no container restart/stop actions, no
new open ports, no changes to n8n/PostgreSQL/nginx/WireGuard/SFTP/guard-ai.
Coordinates with other systems (e.g. n8n) by being reachable on the same
Docker/localhost network - no n8n workflow was created on Hermes's behalf
since that requires n8n's own admin credentials, which this session does
not have.

## Known pre-existing condition (not caused by Hermes)
guard-ai's alerts.log shows repeated "CPU usage 87-93% > 85%" entries
going back before Hermes existed. Actual load average is low (<0.2), so
this looks like a bug in guard-ai's own CPU % sampling, not real load.
Left untouched - out of scope for this task.

## n8n integration (added 2026-09-13)
Hermes now also listens on the n8n_default Docker bridge gateway IP
(172.18.0.1:8787, alongside 127.0.0.1:8787) so containers on that network -
i.e. the n8n container - can reach it without any new public port. Set via
HERMES_BIND_ADDRS in hermes.env (comma-separated bind IPs). The n8n
container itself was never modified/restarted for this.

n8n side:
- Credential "Hermes API Token" (httpHeaderAuth: `Authorization: Bearer
  <token>`) stored encrypted in n8n's own credential store, id
  `hermesApiToken01`, reusing the existing HERMES_TOKEN (no new secret
  created).
- Workflow "Hermes VPS Status (Test)" (id `hermesStatusTestWf01`), inactive,
  trigger = Execute Workflow Trigger (only callable via CLI or from another
  workflow - not reachable via any webhook/HTTP route). One node calls
  GET http://172.18.0.1:8787/status with the credential above.
- Verified end-to-end via `docker exec n8n n8n execute --id=hermesStatusTestWf01`:
  real VPS status (load/mem/disk/container list) returned successfully.
  This CLI execute needs `-e N8N_RUNNERS_BROKER_PORT=<free port>` to avoid
  colliding with the live server's own task broker on 5679 - the running
  n8n server process is never touched/restarted by this.

To reuse from a real workflow: add an HTTP Request node, generic auth type
"Header Auth", credential "Hermes API Token", URL
`http://172.18.0.1:8787/status` (or `/task` with a POST body, see the API
section above for whitelisted actions).

## Phase 2 - Action Layer (added 2026-09-13)
POST /task now dispatches through ACTION_SPECS: every action declares its
exact allowed arg keys, and any other key in the request is rejected
before the action function runs (no arbitrary params reach any action).

New write action:
- `restart_container` {"name": "...", "confirm": true} - the ONLY
  container name accepted is in RESTART_ALLOWED_CONTAINERS, a separate,
  much narrower list than KNOWN_CONTAINERS (used by the read-only
  container_status). It currently contains exactly one entry:
  `hermes-test-target` - a dedicated, isolated nginx:alpine container
  created solely for exercising this action, no ports published, not
  used by anything else. n8n, apiserver, apiserver-pricedb, postgres,
  wireguard, sftp and guard-honeypot can NEVER be restarted through
  Hermes, even with a valid token and confirm:true - verified by test
  (restart_container against "n8n" is rejected with "target not
  allowed"). `confirm: true` is required on top of the allowlist, as a
  deliberate second guard against an accidental call.
- Still no arbitrary shell/command execution anywhere - every action is a
  fixed Python function with fixed subprocess argv, not a passthrough.

Audit logging: every /task call is now logged, not just successful ones -
unauthorized attempts, unknown actions, unknown params, and rejected
targets all get a `task_rejected` line (reason + action, no secrets);
executed actions get a `task_executed` line with action, args, client IP,
write:true/false, success:true/false and duration_ms. HTTP status is 200
on success, 500 on an action-level failure (e.g. disallowed target),
400/401 on request-level rejection.

## n8n action-layer test (added 2026-09-13)
Workflow "Hermes Action Layer (Test)" (id `hermesActionTestWf01`),
inactive, same Execute Workflow Trigger pattern as the status-test
workflow, reuses the same "Hermes API Token" credential. Its HTTP Request
node POSTs to /task with `{"action":"restart_container","args":{"name":
"hermes-test-target","confirm":true}}`. Verified end-to-end via
`docker exec -e N8N_RUNNERS_BROKER_PORT=<free port> n8n n8n execute
--id=hermesActionTestWf01` - Hermes received the call from the n8n
container's own IP (172.18.0.2) and actually restarted
hermes-test-target (confirmed via changed `docker inspect` StartedAt).

## Security Test Mode (added 2026-09-13)
`hermes_security_test.py` - an on-demand adversarial test suite (not a
systemd service) against Hermes's OWN security controls (auth,
action/target allowlists, parameter validation, audit logging). Deliberate
scope, per explicit decision: measures Hermes's own defenses only, not
Guard AI - Guard AI's detection is wired to real host log sources
(/var/log/auth.log, nginx logs, a fixed production-container watch list)
that hermes-test-target was never added to, so a test confined to that
sandbox container would produce zero Guard AI signal either way.

Every request targets only `hermes-test-target` or a deliberately
disallowed name string (shell-injection- and path-traversal-flavored
strings included) - these can never reach a shell since Hermes has none;
they only ever fail the allowlist check. n8n, apiserver,
apiserver-pricedb, postgres, wireguard, sftp and guard-honeypot are
explicitly asserted to be rejected by restart_container even with a
correct token and confirm:true.

Run manually: `python3 ~/hermes/hermes_security_test.py`
Reads HERMES_TOKEN from hermes.env itself - never printed, never passed
as a CLI argument.

Last run: 31/31 checks passed (auth bypass attempts, allowlist bypass
attempts incl. injection-flavored names against every protected real
service, type-confusion on confirm, unknown-parameter rejection,
malformed JSON/action, a 20-request burst, the one real restart of the
allowed sandbox target, and full audit-log/no-secret-leak verification).
Guard AI was unaffected (checked before/after: same pre-existing
unrelated cpu_high pattern, zero new alerts, zero Telegram sends).

## Phase 3 - Result Contract, Action Registry, Coordinator (2026-09-13)

### Result Contract
Every POST /task response (success, action-level failure, or a request
rejected before any action runs) now has this exact shape:
```
{
  "success": bool,
  "action": str,
  "target": str|null,      # args["name"] when the action has one
  "timestamp": ISO8601,    # when the request was received
  "duration_ms": float,    # time spent running the action
  "result": object|null,
  "error": str|null,       # set only when success is false
  "audit_id": str          # matches "audit_id" in the same call's line
                           # in logs/hermes.log - cross-reference exactly
}
```
Backward compatible with earlier test workflows (the "result" and
"success" keys kept the same shape/position they always had).

### Action Registry
Unchanged action set: system_status, list_containers, container_status,
restart_container (allowlisted target only, confirm:true required).
Adding a future action = one function + one ACTION_SPECS entry with its
own `allowed_args`; a write action needing a target allowlist gets its
own separate *_ALLOWED_* list, per the restart_container pattern - no
action is ever a passthrough to the shell.

### Coordinator / Executor
- "Hermes Executor" (n8n workflow, id `hermesExecutorWf01`, inactive):
  a reusable Execute-Workflow-Trigger wrapper around the Hermes call,
  intended to be called from other workflows via n8n's "Execute
  Workflow" node. **Known limitation**: n8n (single-main deployment,
  this instance) requires calling a sub-workflow via that node to be
  *active*, and activating a workflow here requires restarting n8n
  (out of scope - n8n must not be restarted). So today this workflow
  documents the intended pattern but cannot actually be invoked as a
  sub-workflow; each real test workflow inlines the same HTTP Request
  node directly instead.
- "Hermes E2E Test - Read Request" (`hermesE2EReadWf01`) and
  "Hermes E2E Test - Write Request" (`hermesE2EWriteWf01`): self-contained,
  inactive, Execute-Workflow-Trigger workflows that build a request and
  call Hermes directly - verified end-to-end via `n8n execute` (see
  below), including the exact Result Contract in the response.

### AI-Council / Executor Worker - not reachable from the VPS (by design)
Audited (read-only) `%LocalAppData%\ai-council-worker\worker-policy.json`
on the local Windows machine: `"ip_allowlist": ["127.0.0.1", "::1",
"::ffff:127.0.0.1"]` - a deliberate, hard-coded safety ceiling, confirmed
live in `worker.log` ("listening on 127.0.0.1:5680 ... source-IP
allowlisted"). This worker will refuse any connection that isn't from
its own machine's loopback, by design - it is not a gap, it is the
intended boundary. No WireGuard client or other tunnel exists on that
Windows machine today, so there is no existing secure network path from
the VPS to it either. Per standing instruction, AI-Council's
configuration/activation was not touched. Consequence: "Request -> n8n
-> Hermes -> VPS -> Result" is implemented and proven today; a future
AI-Council integration would have to go through n8n's own webhook layer
(already public via nginx/certbot) rather than calling Hermes or the
Worker directly - building that is future work, not part of this phase.

### E2E verification (2026-09-13)
- Read: `docker exec -e N8N_RUNNERS_BROKER_PORT=<port> n8n n8n execute
  --id=hermesE2EReadWf01` -> real `system_status` result, full envelope,
  `audit_id` cross-referenced in hermes.log.
- Write: same for `hermesE2EWriteWf01` -> `restart_container` on
  `hermes-test-target` only; confirmed via changed `docker inspect`
  StartedAt; client IP in the audit log was n8n's own container IP
  (172.18.0.2).
- Full `hermes_security_test.py` re-run after the Result Contract change:
  31/31 still passing (auth bypass, allowlist bypass incl. injection
  strings against every real service, confirm type-confusion, unknown
  param/action, malformed JSON, burst stability, audit completeness, no
  token in logs).

## Phase 4 - Telegram interface (2026-09-13, logic built + CLI-verified; NOT yet live)

### Findings (read-only audit)
Three separate Telegram bots exist on this VPS: Guard AI's own alert bot
(telegram_config.json), apiserver's own price-notification bot (.env),
and n8n's "Telegram account" credential used by the currently ACTIVE
"Telegram In/Out" AI-chat workflow. None were built for Hermes. Outbound
HTTPS to api.telegram.org already works (proven repeatedly by Guard AI).
Inbound already works too, for the one active bot, via n8n's existing
public webhook (nginx/certbot) - no new port needed for that path.

**Real constraint**: any workflow with a live Telegram Trigger must be
*active* to receive real messages, and (same limitation hit in Phase 3)
activating/reactivating a workflow in this single-main n8n deployment
only takes effect after a restart - which must not happen without your
own explicit action. Editing the currently-active "Telegram In/Out"
workflow via our import mechanism would also deactivate it. Per your
decision, this phase built and CLI-verified the full logic; **you** will
handle real activation later (UI login or a restart at a time of your
choosing) - not done here.

### What was built
"Hermes Telegram Bridge (Test)" (n8n workflow, id
`hermesTelegramBridgeWf01`, inactive): Execute-Workflow-Trigger (stands
in for a real Telegram Trigger) -> Security Gate + Route (Code node) ->
Call Hermes -> Format Reply -> Send Reply (real Telegram API call, via
n8n's existing "Telegram account" credential - outbound send only, no
webhook registration, so no conflict with the active bot).

Security Gate + Route enforces, in order:
1. Chat ID allowlist (`ALLOWED_CHAT_IDS`) - anyone else gets **no reply
   at all** (silent reject - confirmed empty output, Send node never
   even runs).
2. Rate limit - max 10 requests/60s per chat, via n8n workflow static
   data (`$getWorkflowStaticData`).
3. Command -> action allowlist, **read-only actions only**:
   `/status` -> system_status, `/containers` -> list_containers,
   `/container <name>` -> container_status. Anything else, including any
   attempt at a write action, is rejected with a reply (chat is
   authorized, just told the command is unknown) - restart_container is
   not reachable from Telegram at all in this phase.
Every allowed request still goes through Hermes's own full Result
Contract + audit_id + hermes.log audit trail (client IP 172.18.0.2,
n8n's own address) - confirmed for every test call.

### CLI verification performed
- Allowed command (`/status`) from the allowlisted chat: full chain ran,
  Hermes returned real VPS data, and **a real Telegram message was
  delivered** (message_id confirmed in the API response).
- Unknown command (`/foo bar`) from the allowlisted chat: rejected with
  a reply, never reached Hermes.
- Non-allowlisted chat ID: zero reply sent (execution ends with an empty
  item set at the rejection-formatting step - the Send node never runs).
- Rate limiting: logic reviewed and is standard/correct, but **could not
  be observed triggering via CLI `n8n execute`** - each CLI invocation
  is a separate process and workflow static-data changes do not appear
  to persist back to the DB between separate `execute` calls (confirmed
  by temporarily lowering the threshold to 2 and still not tripping it
  after several calls). This is a real limitation of CLI-based testing,
  not a claim that the code is verified correct under real
  (webhook-triggered) execution - re-verify once the bot is live.
- No secret leak: `hermes.log` contains zero occurrences of HERMES_TOKEN
  across all of this phase's calls (26 new log lines, checked).

### Not done in this phase
Real activation (webhook live and listening for actual Telegram
messages) - deliberately deferred to you, per your decision.

## Phase 5: Daily Operations (health monitoring, alerting, daily report)

Goal: turn Hermes from a ready system into a daily operational
assistant, without expanding its authority. `restart_container` still
only accepts `hermes-test-target`; no automatic remediation of any kind
was added; no new public port or credential was created.

### hermes_monitor.py (systemd --user timer, every 5 minutes)
Calls Hermes's own `/status` over localhost (same token, same source of
truth as everything else) and evaluates:
- CPU: `load_avg[0] / core_count` ratio, WARN >=0.85, CRITICAL >=1.5
- RAM used %: WARN >=85, CRITICAL >=95
- Disk used %: WARN >=85, CRITICAL >=95
- Every container in `KNOWN_CONTAINERS`: CRITICAL if missing, not `Up`,
  or reported unhealthy
Thresholds were chosen only after baselining the real VPS (load ~0.08,
RAM ~35%, disk ~7% at the time of measurement) - comfortably below WARN
on all axes under normal operation.

Alerts go **directly** to the Hermes Telegram Bot API (same bot/token
already authorized for `@HermesControl2026Bot`, same allowlisted chat),
bypassing n8n on purpose: an "n8n is down" alert must not depend on n8n
being up to be delivered. Each alert includes type, target, severity,
observed value, timestamp, and a fresh `event_id`. A separate
`hermes_unreachable` alert fires if Hermes's own `/health` stops
responding.

Deduplication: a problem key only re-alerts when it's new, when its
severity changes (e.g. WARNING -> CRITICAL), or after a 1-hour cooldown
if still open. A one-time recovery message fires when a previously-open
problem clears. State lives in `state/monitor_state.json`.

All checks and alerts are logged to `logs/hermes_monitor.log` as JSON
lines (event, key/severity/value/detail, telegram_sent, event_id, ts) -
no secrets. `monitor-heartbeat.json` records the last check outcome.

### hermes_daily_report.py (systemd --user timer, daily at 08:00 local)
Built as a systemd timer, not an n8n workflow - this avoids the n8n
"activation needs a full restart" limitation entirely and matches the
same direct-Telegram resilience pattern as the monitor. Each run:
- pulls current uptime/load/RAM/disk/container health from Hermes
  `/status`
- counts the last 24h of `hermes.log` (actions executed, failed or
  rejected)
- counts the last 24h of `hermes_monitor.log` alert events
- counts the last 24h of Guard AI's own `defense-alerts.log` +
  `incident-alerts.log` (read-only - Guard AI itself untouched)
- sends one concise Telegram message with all of the above plus an
  overall OK/ATTENTION status, and logs the send to
  `logs/hermes_daily_report.log` with its own `event_id`

### Tests run this phase (synthetic data only - production never touched)
1. Health check: real `/status` pulled, baseline confirmed healthy
   (load 0.08, RAM 35%, disk 7.2%, 8/8 containers healthy) -> 0 problems.
2. Threshold logic: 9 synthetic status payloads (WARN/CRITICAL for
   mem/disk/cpu, a down container, a missing container, and the real
   healthy baseline) fed straight into `evaluate()` - every case matched
   the expected severity, zero false positives on the real baseline.
3. Telegram delivery: one real synthetic test alert sent and confirmed
   delivered (`sent_ok=True`).
4. Deduplication: isolated test (temp state file, faked
   `send_telegram`/`call_hermes_status`) proved new-alert-sends,
   repeat-within-cooldown-suppresses, severity-escalation-resends, and
   recovery-fires-once, in that order.
5. Audit: `hermes_monitor.log` entries checked for schema completeness
   and grepped (with `hermes.log`) for the bot token pattern - zero
   matches.
6. Daily report: dry-run generated and inspected; content and the
   OK/ATTENTION logic both correct given real + test-inflated 24h data.
7. Low-risk action: `restart_container` on `hermes-test-target` executed
   for real (container restart timestamp confirmed changed) and the
   same action against `n8n` was confirmed rejected
   (`"error": "target not allowed"`).

### Final security checklist (this phase)
- Arbitrary shell: still impossible - no new action added to
  `ACTION_SPECS`, monitor/report scripts only call Hermes's existing
  read action and Telegram's HTTPS API.
- Unauthorized targets: rejected (`restart_container` against `n8n`
  proven rejected above).
- Telegram: reuses the existing authorized bot/chat/path only - no new
  bot, no new webhook.
- Credentials: `hermes.env` stayed mode 600 throughout; grep across both
  audit logs for the token pattern returned nothing.
- No new public port: `ss -tlnp` after this phase shows the same ports
  as before (8787 on loopback + n8n bridge only, plus the pre-existing
  22/80/443).
- Guard AI: zero writes issued against `guard-ai/` this phase (read-only
  log reads for the daily report's alert count); all 7 timers still
  active.
- Production: all 8 containers stayed Up throughout except the one
  intentional, whitelisted `hermes-test-target` restart; both n8n
  workflows (`Telegram In/Out`, `Hermes Telegram Bridge (Live)`)
  confirmed still the only two active workflows.

### Known limitations
- CPU check uses `load_avg` (the only CPU metric Hermes's `/status`
  exposes), not instantaneous CPU%. Guard AI's own health feed samples a
  separate `cpu.used_pct` metric and already alerts on it independently
  - no duplicate monitoring was added here to avoid overlapping with
  Guard AI's own detection.
- The daily report's "actions failed/rejected (24h)" count is a raw
  count from `hermes.log` and does not distinguish real incidents from
  intentional test/security-suite calls - on a day with heavy testing
  (like today) this number will look worse than actual production
  health. A human reading the report should factor that in.
- No automatic remediation exists or was added - every alert is
  notify-only, matching this phase's explicit scope.

## Phase 6: Persian-default language policy

Goal: every user-facing Hermes message (Telegram replies, monitor
alerts, daily reports) is in Persian by default, with an explicit
per-message override to English or Japanese. `hermes.py`'s own API
error strings (e.g. `"unauthorized"`, `"target not allowed"`) were
deliberately left untouched - they are the Result Contract, asserted on
exactly by `hermes_security_test.py` and consumed by n8n. Translating
them would break the security suite and the API contract; presentation
happens one layer up, where a reply is actually built for a human.

### `hermes_i18n.py` (new)
Centralized message catalog: `MESSAGES["fa"|"en"|"ja"]`, `t(key, locale)`,
`severity_label(severity, locale)`. Default locale is `"fa"`. Holds only
display text (labels/titles) - timestamps, event_id, percentages,
container names, commit hashes, and severity codes used internally for
state tracking are never stored here and always pass through unchanged.

### `hermes_monitor.py` (updated)
`format_alert()` / `format_recovery()` now build Persian text via
`hermes_i18n`, with severity shown as a Persian word for display only
(the internal severity code used for dedup/state stays `WARNING`/
`CRITICAL`, unaffected). Added `send_test_alert()` / `--send-test-alert`
CLI flag: sends one real, clearly-marked TEST alert through the exact
same `format_alert()` code path used for real alerts, so a test can
never be mistaken for - or silently diverge from - a real incident.

### `hermes_daily_report.py` (updated)
`build_report()` now builds the Persian report via `hermes_i18n`.
The internal summary dict logged to `hermes_daily_report.log` still
records `overall` as `"OK"`/`"ATTENTION"` (English) for audit-log
consistency; only the human-facing message text changed.

### Telegram Bridge (n8n) - PREPARED, NOT YET LIVE
The interactive `/status`, `/containers`, `/container <name>` replies,
and the rejection messages (rate-limited, unknown command), are built
entirely in two n8n Code nodes (`Format Reply (Hermes result)`,
`Format Reply (Rejection)`) inside the `Hermes Telegram Bridge (Live)`
workflow - not in `hermes.py`. A Persian-default version of both nodes,
plus a small per-message language-override added to `Security Gate +
Route` (trailing word `en`/`english`/`ja`/`jp`/`japanese`/`fa`/`farsi`/
`persian` on a command switches that one reply's language; no override
= Persian), was designed and fully tested offline (`node --check` +
executing the actual extracted node code against 8 scenarios covering
the language default, the override, unknown-command help text, the
error-translation path, and - critically - that the existing chat
allowlist and rate-limit security behavior are completely unchanged)
via `hermes_telegram_bridge_i18n_test.js`. It is **not deployed**: per
this project's established finding that an n8n workflow content change
only reliably takes live effect after a full `n8n` container restart,
and per the standing project rule to stop and report before any
production service restart, this change is staged but not activated.
