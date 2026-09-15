# Hermes Agent Architecture

Status: **design document only**. Nothing in this document has been
activated, installed, or deployed. It records (1) a read-only audit of
what exists today, (2) the target architecture for turning Hermes into
a real multi-step agent, and (3) recommendations for the pieces that
are not built yet (AI Brain, Claude execution, Browser, Gmail). No new
API was enabled, no credential was created, and no paid service was
turned on while writing it. The scrap-metal/recyclables price feature
is explicitly out of scope and not referenced anywhere below except as
a deferred TODO already noted in README.md.

## 1. Audit — what exists today (read-only, verified on the VPS)

| Component | Role | Operational? |
|---|---|---|
| `hermes.py` | Whitelist-only action API (`/health`, `/status`, `/task`). 4 actions total: `system_status`, `list_containers`, `container_status`, `restart_container` (target-locked to `hermes-test-target`). No generic shell execution anywhere. | Yes — `systemctl --user` service active, `/health` returns `{"status":"ok"}`. |
| `hermes_monitor.py` | 5-minute systemd timer; polls Hermes's own `/status`, alerts to Telegram on real threshold breaches (Persian by default), dedup/cooldown logic. | Yes — timer active, last check `2026-09-15T09:15:38Z`, 0 open problems. |
| `hermes_daily_report.py` | Daily systemd timer; Persian summary sent to Telegram (uptime/CPU/RAM/disk/containers/24h action counts/monitor+Guard alert counts). | Yes — a real (non-dry-run) send is logged (`2026-09-14T23:00:39Z`), not just the dry-run tests from earlier phases. |
| `hermes_policy_gate.py` | The two absolute locks (DESTRUCTIVE, FINANCIAL). `require_gate()`/`evaluate()` — no approve path exists anywhere in the code. | Built and tested (simulated block scenarios) in the previous phase; **not yet wired to any real action**, because no real destructive/financial-capable action exists yet. |
| `hermes_tasks.py` | `run_gated()` — the mandatory chokepoint every future action must call. Logs to `logs/hermes_tasks.log` (all tasks) and `logs/hermes_pending_approvals.log` (blocked ones). | Built and tested; not yet called by any live Telegram-triggered action (same reason as above). |
| `hermes_i18n.py` | fa/en/ja message catalog, fa default. | In active use by monitor, daily report, and (once deployed) the Telegram Bridge Format Reply nodes. |
| `hermes_security_test.py` | 31 checks against the live Hermes API/audit contract. | 31/31 passing as of this audit. |
| **n8n - `Hermes Telegram Bridge (Live)`** (`hermesTelegramBridgeWf01`) | Telegram Trigger -> Security Gate (chat allowlist + rate limit + language detect) -> Call Hermes -> Format Reply (Persian default, en/ja override) -> Send Reply. | Active; real `/status` and `/containers` messages verified end-to-end with Persian replies. |
| **n8n - `Telegram In/Out`** (`04mikT9mhO9GKjfu`) | Pre-existing, unrelated bot/workflow. Never modified by any Hermes work. | Active, untouched. |
| **n8n - `Hermes Executor`** (`hermesExecutorWf01`) | An earlier attempt at a reusable Execute-Workflow-based sub-workflow pattern. | **Not functional** - Execute Workflow nodes require the target workflow to be active, which itself needs a restart to take effect in this n8n's single-main deployment. Left in place as a documented dead end, not a live component. |
| **n8n - `hermesStatusTestWf01`, `hermesActionTestWf01`, `hermesE2EReadWf01`, `hermesE2EWriteWf01`** | Superseded test workflows from early phases. | Inactive, harmless, kept only because n8n's CLI has no delete command. |
| **n8n - other 25 workflows** (`OpenAI Simple Test`, `Gemini AI Test`, `Telegram Voice Test...`, `Daily USD Free-Market Price`, `Ohata Daily Scrap Prices`, `Ctronics...`, etc.) | Pre-existing, unrelated to Hermes. | All inactive except the two listed above; never touched by any Hermes phase. |
| **n8n AI credentials** | `OpenAI account` (`openAiApi`) and `Google Gemini(PaLM) Api account` (`googlePalmApi`) already exist in this n8n instance, used by the unrelated `OpenAI Simple Test` / `Gemini AI Test` workflows above. | Exist, but **not** Hermes's to use without a fresh decision - they were set up by the user for other experiments, and reusing them for Hermes would need the user's explicit go-ahead even though no new credential object would technically be created. |
| **Claude Code CLI** | N/A | **Not installed anywhere on the VPS** (host or containers). Node.js itself is also absent from the VPS host (it only exists inside the `n8n` container, for n8n's own runtime). Verified via `which node`, `which claude` - both empty. |
| **Browser automation** (Playwright/Selenium/Chromium) | N/A | **Not installed anywhere on the VPS.** Verified via `pip3 list`, `dpkg -l`, `which chromium`/`google-chrome` - all empty. |
| **Gmail / OAuth** | N/A | **No Google Cloud project, OAuth client, or n8n Gmail credential exists.** Verified via the n8n credentials table (6 total: Gemini, Hermes API Token, OpenAI, Postgres, Hermes Telegram Bot, Telegram account - no Gmail entry). |
| Guard AI (7 systemd timers) | Independent security monitoring, own direct-Telegram alerting. | All 7 active; never modified by any Hermes phase. |

**Bottom line of the audit**: the Coordinator/Policy/Audit layer (Hermes + Policy Gate + task log) and the Telegram front door are real and operational today. The AI Brain, the Executor, Browser, and Gmail are all currently **empty slots** - nothing is silently half-wired; each genuinely needs a fresh decision plus, in most cases, a new credential or a new package installed on the VPS.

## 2. Target architecture

```
User
  |
  v
Telegram  (existing bot @HermesControl2026Bot, existing chat allowlist)
  |
  v
n8n  (Hermes Telegram Bridge: security gate, rate limit, language detect)
  |
  v
Hermes  = Coordinator / Policy Gate / Audit
  - POST /task keeps being the single API surface
  - hermes_policy_gate.require_gate() / run_gated() wraps EVERY action
    below before it's allowed to execute for real
  - hermes_tasks.log_task() / log_pending_approval() records everything
  |
  +---------------------------+
  |                           |
  v                           v
AI Brain (Tier 1)       Executor (Tier 2)
fast, free, cheap        Claude Code (headless), invoked ONLY when
- intent triage           Tier 1 decides the request needs real
- simple replies           multi-step tool use (browser, Gmail,
- routes already-          registration, multi-step VPS work)
  whitelisted Hermes
  actions (status,
  containers, ...)
  |                           |
  |                           v
  |                     Tools: Browser (Playwright, DOM-first) /
  |                     Gmail (OAuth, API-first, no password) /
  |                     VPS (same whitelist Hermes already enforces)
  |                           |
  +-------------+-------------+
                |
                v
         Result, always through Hermes's audit log
                |
                v
         Telegram reply (Persian default, en/ja on request)
```

Why two tiers instead of routing every message straight to Claude:
a cheap/fast free-tier model can answer "what's the status" or
"list containers" in under a second without ever invoking the heavier
Executor; the Executor is reserved for genuinely multi-step work
("go sign up on site X, find the confirmation email, tell me the
result"), which is both slower and - even at effectively zero
marginal cost under a Claude subscription - not something you want
triggered by every trivial message. This also keeps the blast radius
small: Tier 1 can only ever call the small set of actions it's given,
Tier 2 is the only path that can reach Browser/Gmail/multi-step VPS
work, and *both* tiers still go through the same Policy Gate no matter
which one initiated the action.

## 3. AI Brain (Tier 1) - recommendation, nothing activated

Re-confirming the comparison from the prior research pass, scored
against this phase's exact criteria:

| | Persian | Tool calling | Context | Reliability | n8n compatibility | Free limits |
|---|---|---|---|---|---|---|
| **Gemini Free (Flash)** | Very strong (best proprietary in benchmarks) | Native function calling; ⚠️ n8n community reports some friction between Gemini and n8n's "Tools Agent" node specifically | Large (1M-token class models) | Google-grade uptime | Official `Google Gemini Chat Model` node; a Gemini credential already exists in this n8n instance (though it's the user's, not Hermes's, to reuse without asking) | ~15 RPM / ~1500 RPD (Flash) |
| **Groq Free** | Good, especially when hosting Qwen models (token-efficient for Persian script) | Depends on hosted model; Llama-3.x/Qwen support function calling | Model-dependent | Fastest inference (LPU hardware), generous daily cap | Official `Groq Chat Model` node | 30 RPM / ~14,400 RPD - most generous real daily cap of the three |
| **OpenRouter Free** | Good via Qwen-family `:free` models | Dedicated tool-calling filter + free-models router; `qwen3-coder:free` recommended by OpenRouter itself for tool use | Model-dependent | Model roster rotates (models graduate to paid or vanish) | Official `OpenRouter Chat Model` node | 20 RPM but only 50 RPD with no top-up (1000 RPD needs a $10 charge - a real cost) |

**Recommendation for the first real test, once you decide to proceed**:
**Groq Free** as the primary Tier-1 brain (best real daily volume,
fastest response time - important for a chat-feel Telegram bot, has an
official n8n node), with **Gemini Free** as a natural second choice if
Groq's model roster ever lacks a good-enough Persian/tool-calling
model for a given task. OpenRouter Free is best kept as a manual
fallback rather than the primary, because its no-cost daily cap (50
requests) is too low for routine use.

This is a recommendation only - **no API key was requested or
created**, and switching this on is a separate decision.

## 4. Claude execution (Tier 2) - read-only feasibility, nothing installed

**Finding**: Claude Code is not installed anywhere on the VPS today,
and neither is Node.js on the host (verified: `which node`, `which
claude` both empty; Node only exists inside the `n8n` Docker
container, for n8n itself). Standing this up would require:

1. Installing Node.js on the VPS host (a real system package change).
2. Installing and authenticating the Claude Code CLI (ties to the
   user's own Claude subscription - headless `claude -p "..."` runs
   draw from that subscription's usage pool rather than a separate
   metered API key, per the prior research pass, *if* authenticated
   that way rather than via `ANTHROPIC_API_KEY`).
3. A new, narrowly-scoped Hermes action (e.g. `invoke_executor`) that
   is the *only* thing allowed to shell out to the `claude` binary -
   with a fixed prompt template, `--allowedTools`/`--permission-mode`
   locked down in code (not left to the caller), and `run_gated()`
   wrapping it exactly like every other future action. This is a
   meaningful hermes.py change and a new whitelisted capability - both
   fall under "new high-risk authority," which this project's standing
   rule requires stopping for before building, not just before
   activating.

**None of the above was done.** This section is the answer to "can it
be done safely" (yes, with the three steps above, each gated behind a
fresh explicit go-ahead) rather than an implementation.

## 5. Browser - recommendation, nothing deployed

Comparing the four options named in the request, for exactly this
project's shape of task (open a site, click, type, fill a form,
register for a free account, follow a multi-step flow):

- **Playwright (DOM-driven, deterministic)**: fast, free, ~4x fewer
  tokens than screenshot-driven approaches, and the most reliable
  choice specifically for stable multi-step flows like sign-up/login/
  order forms - this is the documented sweet spot for exactly the
  "free site registration" use case named in the request.
- **Claude Computer Use (vision-driven)**: handles unpredictable/
  canvas/image-heavy UIs and can adapt when a page's structure is
  unknown or shifts, and can flag a CAPTCHA for human review instead
  of attempting to bypass it (matches the project's explicit
  CAPTCHA/2FA rule) - but is slower and token-heavier than DOM
  automation.
- **Claude in Chrome / Claude Cowork's own browser surface**: these
  are built for *this Claude Code session* (or a Cowork session)
  driving a browser a human is watching - not something Hermes, as an
  independent VPS service reacting to Telegram messages with nobody
  watching, can invoke on its own.

**Recommendation**: a **hybrid Playwright-first, Computer-Use-fallback**
design - Playwright handles the deterministic majority of registration/
form-filling flows (the documented ~92%-reliability, low-cost path),
and only escalates to Computer Use when a page's structure defeats DOM
automation or the UI is genuinely visual. This matches the current
industry-recommended architecture for agentic browser work and keeps
routine registrations cheap and fast.

**Not deployed**: no browser automation package exists on the VPS yet;
standing this up is a real system change (new packages, more resource
use, a materially larger attack surface) that needs a go-ahead first,
exactly like the Claude-execution question above.

## 6. Gmail - recommendation, nothing connected

**Recommendation: Gmail API via OAuth2, scoped as narrowly as the task
needs, through n8n's official Gmail node** (or an equivalent direct
API call from the Executor) - never IMAP-with-password, never any flow
that could put the account password in front of the Agent.

- For **reading mail / finding a confirmation email**: `gmail.readonly`
  scope is sufficient on its own.
- For **sending/replying** (only if a future task genuinely needs it):
  add `gmail.send`; avoid the broader `gmail.modify`/full-mailbox
  scopes unless a specific task actually requires them - least
  privilege, not a standing "give Hermes my whole inbox" grant.
- The OAuth consent screen and client must be created by the user in
  their own Google Cloud project - this is a new credential, which
  this project's standing rule requires a fresh go-ahead for before
  creating, regardless of how narrow the scope is.
- Once connected, "find the verification email and use the link/code"
  is a normal Tier-2 (Executor) task, not something Tier-1 needs to
  handle - it's exactly the kind of multi-step, judgment-requiring
  work the architecture in section 2 routes to Claude.

**Not connected**: no Google Cloud project or OAuth client exists for
Hermes today (verified against the n8n credentials table).

## 7. Policy Gate - already built, now placed in the full architecture

The two absolute locks already exist in code (`hermes_policy_gate.py`,
built and tested in the previous phase) and this document does not
change their design - it places them correctly in the fuller picture:

- **A) DELETE / DESTRUCTIVE** - deleting a file, folder, email,
  account, cloud data, container/volume, or any other irreversible
  reset. Matched via broad bilingual (fa/en) keyword patterns against
  the action name + description + args, deliberately over-inclusive so
  a false positive just means "ask first," never "skip asking."
- **B) MONEY / PAYMENT** - purchase, payment, checkout, paid
  subscription/renewal, transfer, order, or any action creating a
  financial commitment. Same matching approach; the blocked-action
  notice always carries what/amount/where/why before anything runs.

**Enforcement, concretely**: `hermes_tasks.run_gated()` is the *only*
function through which a future action (Browser click sequence, Gmail
send, VPS script, anything Tier 2 wants to do) is allowed to execute.
It calls the gate before ever invoking the caller's function - a
blocked action's function is literally never called, not "called but
told not to proceed." There is no `approve()` function anywhere in
`hermes_policy_gate.py` or `hermes_tasks.py`, and no config flag,
Telegram command, website content, or prompt text can substitute for
one - the only way to resolve a block is a fresh, separate instruction
from the user, outside whatever triggered the match. Every future
action (Tier 1 or Tier 2, Browser or Gmail or VPS) must be written to
call `run_gated()` - this is a coding convention this document is
recording as mandatory, not something enforced by a runtime sandbox,
so any future action added to Hermes needs to actually follow it.

## 8. Non-destructive autonomy

For everything that is *not* a DESTRUCTIVE/FINANCIAL match, the
target design lets the Agent (Tier 1 for simple cases, Tier 2 for
multi-step ones) run end-to-end without a per-step confirmation:
plan -> pick the tool -> execute -> handle an error and retry/adjust
within the same run -> report the final result once, in Persian, via
Telegram. The example in the request - "go sign up on site X, find the
confirmation email, tell me the result" - is squarely this category:
registering for a free account and reading a confirmation email are
neither destructive nor financial, so once Tier 2/Browser/Gmail exist
for real, that whole flow runs as one Telegram round trip, not a
click-by-click conversation. This is architecture, not a live
capability yet - it depends on section 4-6 actually being built.

## 9. Security - unchanged, restated for completeness

All of the following were already true before this document and
remain the baseline any future addition must preserve:
- Secrets are never displayed - in Telegram replies, in this document,
  or in any report.
- Passwords are never sent through Telegram and never handed to the
  Agent (see section 6 - Gmail access is OAuth-scoped, not
  password-based, for exactly this reason).
- Private keys are never displayed.
- No token/secret is ever committed to Git - `hermes.env` stays outside
  Git and mode 600; `logs/`, `state/`, heartbeat files, and
  `__pycache__` stay outside Git (all already covered by `.gitignore`).
- No permission or security control is loosened "for the Agent's
  convenience" - the Policy Gate's broad, over-inclusive matching in
  section 7 is a deliberate example of erring toward asking rather than
  relaxing a check to reduce friction.

## What this phase did NOT do

No new API was enabled. No credential was created. No package was
installed on the VPS. No n8n workflow or setting was changed. No
production service was restarted. The scrap-metal/recyclables price
feature remains untouched (see the existing TODO in `README.md`).
Everything above is investigation and design, ready for you to pick a
starting point from Tier 1 (AI Brain), Tier 2 (Claude execution),
Browser, or Gmail whenever you decide to proceed with one.
