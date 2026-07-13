# SPEC-ADAPTER — Mattermost adapter (thin translation over the core API)

**Phase:** 5.1 (SPEC) · **Implements:** PROMPTS.md 5.1 · **Branch:** `spec-adapter`
**Status:** ready for BUILD (**preliminary pending PM gates 2.4/3.3**, §11)
**Depends on:** SPEC-CORE (the API is the only way to change state), SPEC-DOMAIN (outbox),
SPEC-INITIATION (gate payloads), SPEC-MONITORING (digest payloads)
**Consumed by:** PM gate 5.3 (drive a full initiation + decision + a monitor escalation
entirely from Mattermost)

The adapter is **translation only**. It holds no state beyond its connections: chat in →
core API calls; core events → posts. Everything Mattermost can do, the CLI can do, because
both are thin clients of the same API. This reverses v2, where chat *was* the system
(channel membership was the watchlist, and slash commands failed silently).

Self-contained: implementable without reading `design/`.

---

## 1. Scope

- ws/webhook → core-API translation (slash commands, mentions, DMs, interactive buttons).
- Bot identities: **one per house** + `desk` (system).
- **Coverage-driven channel lifecycle** (channels are derived state, never the source).
- Slash commands and mentions mapped to API routes: propose, decide, review, analyze,
  track-record, portfolio, risk, cost, trade confirm, levels, gates, runs.
- **`pm_query` flow** (lead house's light model, dossier-cited answers).
- **Delivery of run outputs** (PDF + summary + gate messages) via the core outbox.
- **`reconcile`** command (mined from v2 `scripts/bootstrap_integrations.py`).
- **Error surfacing**: every failed command answers in-channel — **no silent failures**.

Out of scope: any business logic. If the adapter needs a decision, that decision belongs
in core behind an API route.

**Ported from v2** (`mattermost/`): `poster.py` (post/upload/threading with retries),
`bots.py` (`BotRegistry`), `auth.py` (allowlist authorization), `channel_naming.py`
(exchange→channel derivation). **Mined only**: `router.py` — its skip/routing rules
(§4.3); its thread-ownership, watch-modes, and persona machinery is retired.
**Retired**: `redis_streams.py`, `dispatcher.py`, `slash_processors.py` (handlers become
thin API calls), `thread_ownership.py`.

---

## 2. Process and identities

One process (`adapter/main.py`), three loops:

| Loop | Purpose |
|---|---|
| **ws** | Mattermost websocket (desk bot): mentions, DMs, reactions |
| **http** | localhost server: Mattermost slash-command webhooks + interactive-button callbacks |
| **outbox** | polls the core API for undelivered events → posts them (§5) |

### 2.1 Bots

- **One bot per house** (`gpt`, `gemini`, `deepseek`, `glm`, `qwen`, `claude`): the posting
  identity for *that house's* work. A report authored by GPT is posted by the GPT bot; a
  cross-check by Gemini is posted by the Gemini bot. The PM sees who said what without
  reading metadata.
- **`desk`**: the system identity — digests, gates, alerts, errors, run failures.
- Tokens come from `MATTERMOST_BOT_TOKENS` (a JSON map keyed by house key + `desk`) via
  pydantic-settings. **No token is ever logged, echoed, or posted** (AGENTS.md Rules 2, 4).
- `BotRegistry` (ported): `register_all_from_settings()`, `get_driver(name)`,
  `get_user_id(name)`, `is_bot(user_id)`. A missing bot token for an **enabled** house is a
  `checkconfig` FAIL, not a runtime surprise.

### 2.2 Authorization

Ported `auth.py` allowlist, simplified to one question: **is this user the PM?**
(`settings.pm_user_ids`). Mutating commands from anyone else get a refusal in-channel
naming the reason. The adapter re-checks locally *and* core re-checks on every mutating
route (defense in depth — the adapter is not a trusted client).

---

## 3. Channels are derived state

### 3.1 Naming (ported `channel_naming.py`, de-hardcoded)

Channel name is derived **once**, at creation, from the coverage:

```
channel_name  = f"{country}_{symbol}"      # e.g. jp_2267, hk_0700, us_aapl
display_name  = f"{COUNTRY}: {SYMBOL} {name}"   # "JP: 2267 Yakult Honsha"
```

The `exchange → country` map (`tse→jp`, `hkex→hk`, `sgx→sg`, `nyse|nasdaq→us`, …) moves
from v2's hardcoded `EXCHANGE_SUFFIXES` into `fund.yaml exchanges.<x>.country`. The v2
convention is kept deliberately: the PM's existing channels keep working across cutover.

**Identity-key ownership:** the derived name is minted once and **stored** by core (§3.2);
every later action (post, archive, reconcile) reads the stored row. Nothing re-derives a
channel name from a ticker at post time — that is how v2 drifted.

### 3.2 Channel registry (new, core-owned)

**Amendment to SPEC-DOMAIN:** add table `mm_channels`:

```python
class MmChannel(Base):
    __tablename__ = "mm_channels"
    coverage_id: FK coverage.id PRIMARY KEY
    team_id: String(64) NOT NULL
    channel_id: String(64) NOT NULL UNIQUE
    channel_name: String(64) NOT NULL UNIQUE
    archived_at: datetime | None
    created_at, updated_at
```

and `mm_posts` (delivery bookkeeping, small):

```python
class MmPost(Base):
    __tablename__ = "mm_posts"
    id: Uuid pk
    ref_type: String(24) NOT NULL      # outbox_event | gate | run | event | digest
    ref_id: String(64) NOT NULL
    channel_id: String(64) NOT NULL
    post_id: String(64) NOT NULL
    root_id: String(64) | None
    created_at
    UniqueConstraint("ref_type", "ref_id", name="uq_mm_posts_ref")
```

Exposed through core (the adapter never touches the DB directly — see §5.1):
`GET/POST /coverage/{slug}/channel`, `POST /mm/posts`.

### 3.3 Lifecycle (driven by coverage transitions, never by membership)

| Outbox event | Adapter action |
|---|---|
| `coverage.state_changed → active`/`watch` (from `decision_pending`) | create the channel if absent (idempotent by name), set header/purpose (stance · TP · horizon · lead · `as_of`), invite the PM + all house bots + desk, record `mm_channels`, then deliver the initiation report (§5.2) |
| `coverage.state_changed → exited`/`rejected` | post a closing note (final stance, why, link to the dossier commit) and **archive** the channel (`fund.yaml adapter.archive_on_exit`, default true) |
| `coverage.lead_changed` | post a handover note as `desk`, update the header |
| `coverage.state_changed → active` (promote from watch) | post the cadence change + the auto-spawned `deep_review` run link |

**Inviting a bot to a channel does nothing.** Coverage is the only source of truth; a bot
in a channel with no coverage is drift, and `reconcile` (§7) reports it. (In v2 this was
the watchlist — the single finickiest flow in the system.)

---

## 4. Chat → core API

### 4.1 Slash commands

Each is a thin call to one route. **Every command answers**, always — success or failure.

| Command | Route |
|---|---|
| `/propose <ticker> [name=…] [ccy=…] [lead=<house>]` | `POST /coverage` (+ `POST /coverage/{slug}/initiate`) |
| `/decide <ticker> active\|watch\|reject [notes…]` | `POST /gates/{open_initiation_gate}/answer` |
| `/promote <ticker>` · `/demote <ticker>` · `/exit <ticker> [--force] <notes>` | `POST /coverage/{slug}/{promote,demote,exit}` |
| `/review <ticker> [--reason …]` | `POST /runs {type: deep_review}` |
| `/analyze <ticker> <question>` | `POST /runs {type: event_analysis, params:{question}}` |
| `/lead <ticker> <house> [--rationale …]` | `POST /coverage/{slug}/lead` (or answers an open `lead_change` gate) |
| `/levels <ticker> entry=… target=… stop=…` | `POST /coverage/{slug}/levels` |
| `/track-record [house\|ticker]` | `GET /track-record` |
| `/portfolio` · `/risk` · `/cost [--by house] [--since 7d]` | `GET /portfolio` · `GET /risk` · `GET /costs` |
| `/trade confirm <id>` · `/trade reject <id>` | `POST /trades/confirm/{id}` (phase 7.2; **the PM confirms; no LLM books a trade**) |
| `/gates` · `/runs [ticker]` · `/run <id>` | `GET /gates` · `GET /runs` · `GET /runs/{id}` |
| `/reconcile [--apply]` | adapter-local (§7) |

Ticker resolution: the argument may be a slug (`tse_2267`), a channel-style name
(`jp_2267`), or bare (`2267` inside a ticker channel). Resolution goes through core
(`GET /coverage?q=`), never through a local parse table — one resolver, one truth.

Responses are **ephemeral** for lookups and errors, **in-channel** for state changes (the
desk should see what the PM did).

### 4.2 Mentions and DMs → `pm_query`

A mention of a house bot in a ticker channel (`@gpt what does the China JV do to the
thesis?`) or a DM to `desk` with a ticker → `POST /queries {coverage, question, house?}`
→ a `pm_query` run (lead house's light model; the mentioned house if it is the lead or a
contributor, else a note that it does not cover this name).

The answer arrives via the outbox and is posted **by that house's bot, in the same
thread**, and must cite dossier state (stance, TP, `as_of`) — including saying the dossier
is stale rather than improvising (the role prompt enforces the content; the adapter just
renders it). `pm_query` **never spawns heavy work**; if the answer offers a `deep_review`,
the PM triggers it with a command.

Notable exchanges are curated into `dossiers/<slug>/queries/` only when the PM reacts with
a configured emoji (`fund.yaml adapter.keep_reaction`, default `bookmark`) → `POST
/queries/{id}/keep`.

### 4.3 Routing rules (mined from v2 `router.py`)

Ignore, in this order, before any work: posts from **bots** (`BotRegistry.is_bot`, incl.
our own — the classic bot-loop), system messages, edits/deletes, empty or whitespace-only
messages, posts in channels with **no coverage** (unless a slash command, which carries its
own ticker), and messages from users over the per-user rate limit
(`adapter.rate_limit_per_min`, ported bounded-token-bucket). Everything else is either a
slash command, a mention, or noise.

What is **not** ported: thread ownership, watch modes, persona routing, channel-membership
watchlists — all retired concepts.

### 4.4 Interactive buttons

Where Mattermost supports them, gate messages carry buttons (`Active` / `Watch` / `Reject`;
`Raise cap` / `Cancel`) whose action payload is `{gate_id, answer}` signed by the adapter's
token. A click → `POST /gates/{id}/answer` with an `Idempotency-Key` derived from
`(gate_id, answer)` — a double-click cannot double-answer. Buttons are a convenience; the
`/decide` command is always available and is what the message text spells out (v2 lesson:
never depend on an interactive feature the server may disable).

**Buttons are only ever constructed by code from a core gate payload — never from model
text** (§6).

---

## 5. Core events → posts (the outbox consumer)

### 5.1 Transport

Core owns the outbox (SPEC-DOMAIN §4.22) and exposes it over the API so the adapter needs
**no DB credentials** and core remains the only DB writer (refinement of SPEC-DOMAIN §4.22,
where the `SKIP LOCKED` claim now happens inside core):

```
POST /outbox/claim   {worker_id, limit, lease_seconds} → [events]      # SELECT … FOR UPDATE SKIP LOCKED
POST /outbox/{id}/ack   {post_id?}
POST /outbox/{id}/nack  {error}                                        # backoff via available_at
```

Delivery is **at-least-once**. Duplicate-post protection: every post carries
`props.ai_fund_event_id = <outbox id>`; before posting, the adapter scans the target
channel's recent posts (bounded lookback) for that marker and skips if present. A crash
between "posted" and "acked" therefore cannot double-post a gate or a report.

### 5.2 Event → post map

| Outbox `kind` | Bot | Channel | Content |
|---|---|---|---|
| `coverage.state_changed` | desk | ticker (create/archive per §3.3) | state, actor, cause |
| `gate.opened` (`initiation_decision`) | desk | ticker | **PDF upload** + workbook + `pm-summary.md` rendered inline (recommendation, TP + horizon + upside, entry, scenarios, confidence, corrections count by house, cost) + the decision prompt: `/decide <ticker> active\|watch\|reject` (+ buttons) |
| `gate.opened` (`budget_cap`) | desk | ticker | spend vs cap, stages done/remaining, `raise_cap` / `cancel` |
| `gate.opened` (`lead_change` / `doctrine_amendment`) | desk | ticker / desk | ballots or the doctrine diff + approve/reject |
| `gate.answered` | desk | same thread | the answer, who, when (the original message is updated to remove buttons) |
| `run.finished` (`event_analysis`) | **lead house's bot** | ticker | the event note + workings; if a material disagreement exists, **both positions side by side** (lead's, then the challenger's, posted by the challenger's bot in-thread) |
| `run.finished` (`pm_query`) | mentioned house's bot | in-thread | the dossier-cited answer |
| `run.finished` (`deep_review`) | lead house's bot | ticker | what changed since the last review (dossier diff summary), new TP/stance, PDF |
| `run.finished` (failed/cancelled) | desk | ticker + desk | **the failure, visibly**: run id, stage, last validation errors, cost spent, `fund run retry <id>` |
| `digest.ready` | desk | ticker | the monitor digest (§SPEC-MONITORING 6); nothing is posted on a quiet session |
| `digest.daily` | desk | desk channel | fund-wide roll-up, incl. stale dossiers and spend |
| `risk.alert` / `escalation.capped` / `monitor.degraded` / `error.raised` | desk | desk channel | one-liners with a run/coverage ref |

Artifacts (PDF, workbook) are fetched from `GET /runs/{id}/artifacts/{kind}` and uploaded;
the post links the dossier commit so "why do we hold this?" is one click from the channel.

---

## 6. Rendering safety (model text is untrusted input to the chat surface)

Model-authored text (reports, event notes, `pm_query` answers) is rendered into a channel
the PM trusts. Before posting **any** model-authored string, the adapter:

1. **Strips broadcast mentions** (`@channel`, `@all`, `@here`) and neutralizes user
   mentions (`@someone` → `` `@someone` ``) — a prompt-injected dossier must not be able to
   ping the team or impersonate a person.
2. **Escapes leading slashes** on any line (`/decide …` inside model text becomes plain
   text) — model output must never *look like* a command the PM might trust or a client
   might auto-run.
3. **Never builds interactive props from model text.** Buttons, actions, and their payloads
   are constructed **only** from core's typed gate payload (gate id + a closed answer set).
4. **Truncates** to the channel limit with an artifact link for the rest (no silent
   truncation of a gate's decision text — that always fits by construction).
5. **Passes everything through the secret redactor** before posting (AGENTS.md Rule 2): a
   model that echoes a key-shaped string cannot publish it into a chat log.

Bot posts are marked with `props.from_bot` and the house key, so `is_bot` skipping (§4.3)
holds even across restarts.

---

## 7. `reconcile` (mined from v2 `scripts/bootstrap_integrations.py`)

`/reconcile [--apply]` (PM only) and `fund adapter reconcile [--apply]`. **Dry-run by
default**: reports drift, changes nothing.

Checks:
1. Every enabled house has a bot account, a valid token, and team membership; `desk` too.
2. Every `active`/`watch` coverage has a channel (`mm_channels` row → the channel actually
   exists on the server), with the PM, the house bots, and desk as members, and the header
   matching current dossier state.
3. Every `exited`/`rejected` coverage's channel is archived.
4. Every slash command in §4.1 is registered on the team, pointing at this adapter's
   webhook URL, with a valid token.
5. **Orphans**: ticker-shaped channels with no coverage → reported (never auto-deleted).
6. Undelivered outbox backlog and its age (a stuck adapter is visible).

`--apply` fixes 2, 3, and 4 (creates channels, invites bots, archives, re-registers
commands), reports 1 and 5 for the operator to resolve. Every applied change is
audit-logged through core. **The reconcile command never deletes a channel or a post.**

---

## 8. Error surfacing (the v2 bug this design exists to kill)

- Every command path ends in a reply. A core 4xx/5xx becomes an ephemeral message:
  `❌ /exit tse_2267 — 409 coverage_has_open_position: 300 shares held in book main. Use
  --force with a note. (request 4f2a…)`.
- An adapter-side exception becomes `❌ internal error (request 4f2a…)` plus a desk alert;
  the traceback is logged **redacted**, never posted.
- A command the PM is not authorized for gets a refusal naming the reason, not silence.
- A core outage: the adapter replies "core unavailable, nothing was changed (request …)"
  and retries nothing that mutates (mutations carry an `Idempotency-Key`, so a PM retry is
  safe).
- The outbox backlog age is reported by `/reconcile` and by the desk watchdog — a silent
  adapter is a detectable state.

---

## 9. Module layout

```
adapter/main.py                    # three loops + shutdown (ported infra/shutdown)
adapter/bots.py                    # ported BotRegistry
adapter/poster.py                  # ported poster (post, upload, thread, retry, rate limit)
adapter/channel_naming.py          # ported; exchange→country from fund.yaml
adapter/routing.py                 # mined skip/routing rules
adapter/commands/*.py              # one thin module per slash command → core client call
adapter/outbox.py                  # claim → render → post → ack
adapter/render/{gate,digest,report,query,error}.py   # payload → markdown (+ sanitize)
adapter/reconcile.py               # §7
adapter/client.py                  # typed core-API client (shares the CLI's client)
```

---

## 10. Test plan (TDD)

`tests/unit/adapter/`. **`FakeMattermost`** (a driver double: records posts, uploads,
channel creates/archives, memberships, reactions; can raise 429/500) replaces the real
server; the **core API runs in-process** via `httpx.ASGITransport` against the real FastAPI
app on SQLite, so command→route→state is proven end to end **without a socket**. Autouse
socket guard on.

### 10.1 Commands

- Every command in §4.1 hits exactly the expected route with the expected body (table-driven),
  and **every one replies** — a test asserts no command path returns without a reply
  (parametrized over success, 4xx, 5xx, and an adapter exception).
- A non-PM user's mutating command → refusal in-channel naming the reason; core is **not**
  called.
- `/decide tse_2267 active` answers the open gate; a second identical `/decide` (or a
  double button click) is idempotent (same `Idempotency-Key`) and posts no second state
  change; a *different* answer to an answered gate → a clear 409 message.
- `/exit` on a coverage with an open position → the core 409 is surfaced verbatim with the
  `--force` hint.
- Ticker resolution: slug, channel-style name, bare symbol inside a ticker channel, and an
  unknown ticker (→ "not covered" reply, no crash).

### 10.2 Routing

- Bot posts (including our own), system messages, edits, empty messages, and posts in
  non-coverage channels are ignored (no core call). A bot-loop scenario (our post triggering
  our own handler) terminates.
- Rate limit: 20 messages in a minute from one user → later ones get one throttle notice,
  not silence, and no core calls.

### 10.3 Channel lifecycle

- `coverage.state_changed → active` creates the channel **once** (a redelivered event does
  not create a second), invites PM + house bots + desk, records `mm_channels`, and sets the
  header from dossier state.
- `exited` archives the channel and posts a closing note; a re-proposed ticker **reuses the
  archived channel** (unarchive) rather than creating `jp_2267_2`.
- Inviting a bot to a random channel changes **nothing** in core (the anti-v2 test).

### 10.4 Outbox delivery

- Claim → render → post → ack; a crash after posting but before ack → redelivery finds the
  `props.ai_fund_event_id` marker and **does not double-post** (the gate-duplication test).
- A Mattermost 500 → `nack` with backoff; the event is retried and eventually delivered.
- A `gate.opened` post carries the PDF upload, the summary, the `/decide` line, and buttons
  whose payload is `{gate_id, answer}` — and the buttons survive a re-render.
- `gate.answered` updates the original post to remove the buttons.
- `run.finished` for a failed run posts the failure **with** the last validation errors and
  the retry command (no silent failures).
- A material disagreement renders **both** positions, each under its own house's bot.
- A quiet monitor session produces **zero** posts.

### 10.5 Rendering safety (the injection tests)

- A `pm_query` answer containing `@channel` and `@everyone` → stripped; the recorded post
  contains neither.
- Model text containing a line starting with `/decide tse_2267 active` → escaped; **no
  command is executed** and the rendered post shows it as literal text.
- Model text containing a fake button/action JSON blob → rendered as text; the post's
  `props` contain no actions (actions exist only on code-built gate messages).
- Model text containing a key-shaped string (`sk-test-…`) → redacted before posting; the
  fake server never receives it.
- A 100 KB model answer → truncated with an artifact link; the post is within the limit.

### 10.6 `reconcile`

- Missing channel for an `active` coverage → reported; `--apply` creates it and invites the
  bots.
- A ticker-shaped channel with no coverage → reported as an orphan and **not** deleted.
- A slash command registered against a stale URL → reported; `--apply` re-registers it.
- Dry-run makes **zero** mutating calls to the fake server.

### 10.7 Integration (`@pytest.mark.integration`, opt-in — the 5.3 gate rehearsal)

Against a **staging** Mattermost with test bots: create a channel, post + upload a PDF,
answer a gate via a button, run one `pm_query`, and reconcile. Skipped without
`MATTERMOST_*` staging settings. This is exactly the flow PM gate 5.3 grades (a full
initiation + decision + a monitor escalation driven entirely from chat).

---

## 11. Preliminary — pending PM gates

1. **Buttons** depend on the Mattermost server's interactive-message support; the `/decide`
   text path is the guaranteed one. If 5.3 shows buttons are flaky, drop them — no core
   change.
2. **One channel per ticker, archived on exit** matches v2 habits. If the PM prefers a
   single "coverage" channel with threads per ticker, that is a `fund.yaml` switch plus a
   renderer change (adapter-local).
3. **House-bot-per-post** assumes the PM wants attribution visible. If the channel gets
   noisy with five bot identities, the alternative (desk posts everything, with the house
   named in the message) is a renderer change.
4. **Mention → `pm_query` on the mentioned house** (not always the lead) is an extension of
   SPEC-CORE §3.2 (`params.house`); if the PM finds contributor answers confusing, restrict
   it to the lead.
5. **Cutover naming**: keeping v2's `country_symbol` channel convention means existing
   channels keep working when the bots are repointed (design/06 cutover). If the PM would
   rather start clean, the derivation is one config map away.

---

## 12. Spec Authoring Checklist

- **Side-effect cost.** The adapter makes **zero** LLM, market-data, news, search, or
  embedding calls — it calls the core API and Mattermost. Its cost profile is Mattermost
  API quota and one core `POST /outbox/claim` per poll interval (default 2 s → ~43k/day,
  all localhost, cheap; a long-poll variant is available if that proves noisy). It *causes*
  cost only by triggering runs the PM asked for (`/propose`, `/review`, `/analyze`, a
  mention → `pm_query` at < $0.20), and every one of those is capped by core's per-run and
  per-house budgets — the adapter cannot spawn a run the API would refuse, and it cannot
  raise a cap. DB writes: two small rows per delivered event (`mm_posts`, outbox ack).
  Deliberate avoidance: model output is **rendered**, never re-summarized by another model
  (no "summarize this for chat" call).
- **Concurrency model.** The adapter holds no domain state: its only in-process state is
  the ws connection, the `BotRegistry` (built once at startup; read-only thereafter), and a
  per-user rate-limit token bucket (process-local, and correct because the adapter is a
  single process — if it is ever scaled out, the bucket must move to the DB, which is noted
  in the code). Concurrency safety comes from durable keys, not locks: outbox claims use
  `FOR UPDATE … SKIP LOCKED` **inside core** with a lease (so two adapter instances cannot
  deliver the same event), delivery dedupe is the `props.ai_fund_event_id` marker plus the
  `mm_posts` unique `(ref_type, ref_id)`, and every mutating call carries an
  `Idempotency-Key` so a retried command, a double-click, or a redelivered webhook applies
  once. The three loops (ws, http, outbox) share nothing but the core client, which is
  stateless. Channel creation is idempotent by name and recorded in `mm_channels`, so two
  concurrent events for one coverage cannot create two channels.
- **LLM-as-filter threat model.** No LLM decision gates anything the adapter does — but the
  adapter is where **model-authored text meets a trusted human surface**, which is its own
  injection channel. Backstops (§6): broadcast mentions stripped; leading slashes escaped so
  model text can never impersonate a command; **interactive buttons built only from core's
  typed gate payload**, never from model text, so a report cannot render a fake "Approve"
  button; secret redaction before posting; length caps. Authorization is enforced twice
  (adapter allowlist *and* core's PM-only routes), so even a compromised adapter cannot
  make a mutation core would refuse. Gate answers come only from a PM user id with a closed
  answer set. The one path where model text drives an action is nil by construction: the
  adapter parses **PM messages**, never bot posts (`is_bot` skip), so a model cannot issue a
  slash command by posting one.
- **Identity-key ownership.** The channel name is derived **once** by
  `adapter/channel_naming.py` from `coverage.exchange` + `ticker` and then **stored** in
  `mm_channels` (core-owned); every later post, archive, or reconcile reads that row rather
  than re-deriving — the fix for exactly the v2 drift where channel identity was
  re-computed in several places and where *channel membership itself* was the watchlist key.
  Delivery identity is `outbox_events.id` (core-minted), carried into
  `props.ai_fund_event_id` and `mm_posts.(ref_type, ref_id)` — the adapter mints no key of
  its own except the `Idempotency-Key` for a PM command, which is
  `sha256(command|user_id|channel_id|normalized_args)` — **deterministic and adapter-owned**,
  deliberately *not* Mattermost's `trigger_id` (which is per-interaction and changes on
  retry — v2's idempotency bug). Mattermost's `post_id`/`channel_id` are stored as opaque
  foreign identifiers, never parsed for meaning.
- **Test isolation.** No unit test touches a Mattermost server, a socket, or a provider:
  `FakeMattermost` records every call and can inject failures, and the **core API runs
  in-process** via `httpx.ASGITransport` on SQLite, so a command test proves the whole
  chain (chat → route → domain state → outbox → post) with zero network. The ws feed is a
  fake async iterator of recorded events; the slash-command webhook is exercised through the
  adapter's own ASGI app in-process. There are no LLM calls to mock because the adapter
  makes none — but `FakeLLM`-produced text (including injection payloads and key-shaped
  strings) is fed through the renderers to test §6. The autouse socket guard catches the
  miss this layer invites: a ported v2 module (`poster.py`, `bots.py`) constructing a real
  `mattermostdriver` client at import time — the port must take an injected driver factory,
  which the `BotRegistry` signature already allows. The staging-server flow is
  `@pytest.mark.integration` and skips without staging settings.
