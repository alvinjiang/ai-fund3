# SPEC-MARKETDATA — Market-data service interface contract

**Phase:** 2.3 (was CHORE; **reclassified SPEC+CHORE** — the port is mechanical, the
interface is not) · **Branch:** `spec-marketdata`
**Status:** ready for BUILD (build alongside/before SPEC-RUNNER's price-pin gate)
**Depends on:** SPEC-CORE (config layout), v2 `/home/alvin/aicode/ai-fund/data_sources/`
(port source)
**Consumed by:** SPEC-RUNNER (price-pin gate §6.1.4), SPEC-INITIATION (valuation
validators §3.2), SPEC-MONITORING (ticks §2, calendars), SPEC-TRACKREC (scoring §2 —
adjustment factors, dividends, halts; earnings sweep §5.1), sandboxed harness agents
(the "one price truth" doctrine rule as infrastructure).

**Why this spec exists.** Phase 2.3 was filed as "port v2 `data_sources/` and expose GET
endpoints", but the later specs load requirements onto this service that **v2 does not
provide**: v2 serves a *live last price* (no per-trading-date pinned close), FX with *no
date parameter* (doctrine requires FX checked against stated dates), *unadjusted* history
with no adjustment factors or dividends (SPEC-TRACKREC scoring needs both), no halt/
delisting signal, and no earnings calendar outside J-Quants. Without this contract the
CHORE silently becomes design work mid-port, done by a small model. This spec closes the
gap and makes the port mechanical again.

Self-contained: implementable without reading `design/`.

---

## 1. Service shape

A single localhost FastAPI process (`ai-fund-marketdata` systemd unit, own module tree
`core/marketdata/` served by `marketdata/main.py`), read-only, no auth beyond loopback +
the sandbox egress allowlist (SPEC-RUNNER §5.1 — it is one of the three endpoints a
sandbox may reach). It holds the **provider keys** (J-Quants) itself via
pydantic-settings; **agents never see a provider key**. It is the *only* module that
knows provider symbologies; everything else speaks `(exchange, ticker)` — the coverage
identity (identity-key ownership).

Providers, ported from v2: **J-Quants** (JP; adjusted closes with real `as_of` dates,
`/v1/fins/announcements`), **yfinance** (everything else + JP fallback + FX), **SEC
EDGAR** (US financials/filings), **pandas_market_calendars** (sessions/holidays — v2
used it in `scheduler/market_scheduler.py`; it moves here). Provider order per exchange
is **config** (`fund.yaml exchanges.<x>.price_providers`), not code.

---

## 2. Endpoints (the contract)

All responses carry `source`, `provider_chain`, `freshness_status`, `freshness_note`
(ported vocabulary) and never fabricate: a value the provider cannot supply is `null`
with a reason, not an estimate.

### 2.1 `GET /price/{exchange}/{ticker}?date=YYYY-MM-DD&cross_check=false`

The **pinned close**. Omitting `date` = the last *completed* session for that exchange
(per the calendar — never an intraday last; see §2.2).

```json
{"value": "2745.000000", "currency": "JPY", "as_of": "2026-06-09",
 "source": "jquants", "provider_chain": ["jquants"], "pinned_at": "2026-06-10T01:12:03Z",
 "adjustment_note": null, "freshness_status": "fresh", "freshness_note": "..."}
```

- **Decision — pins are persisted.** First successful fetch for `(exchange, ticker,
  trading_date)` writes a service-owned `price_pins` row (add to SPEC-DOMAIN §12
  registry: `price_pins(exchange, ticker, trading_date, value, currency, source,
  pinned_at)`, unique on the first three). Subsequent requests serve the stored pin. A
  provider that later *revises* a close cannot silently change what a report was
  reconciled against or re-score history; re-pinning is an explicit operator action
  (`fund marketdata repin <exchange> <ticker> <date>`, audited). This makes "one price
  truth" durable, not just uniform.
- `as_of` **is the trading date the close belongs to** (J-Quants `Date`; for yfinance,
  the bar date from history — never `fast_info.last_price`, which is a live quote with
  no session identity). If the requested date has no bar and the calendar says the
  session happened, the response is `{"value": null, "reason": "no_bar",
  "sessions_missing": N}` — the runner's price-pin gate then fails honestly instead of
  passing a stale number.
- `cross_check=true` → the response adds `cross_check: {value, source, delta_pct}` from
  the *next* provider in the chain. Delta beyond `marketdata.cross_check_tolerance_pct`
  (config, default 0.5) → `freshness_status: "conflict"`. This operationalizes
  doctrine's "cross-checked across two sources" for the verifier's re-pin: the last
  verifier's `task.md` tells it to request `cross_check=true`.

### 2.2 `GET /quote/{exchange}/{ticker}` — intraday last (display only)

The live price (v2's `get_price` semantics). **Monitors may show it in digests; nothing
may pin, reconcile, or score against it.** Kept as a separate endpoint precisely so the
distinction is structural, not conventional.

### 2.3 `GET /history/{exchange}/{ticker}?start=&end=`

Daily bars, **raw plus adjustment metadata**:

```json
{"bars": [{"date": "2026-06-09", "open": ..., "high": ..., "low": ..., "close": ...,
           "volume": ..., "adj_factor_cum": "1.000000", "dividend": "0.00"}],
 "adjustment_confidence": "provider" | "derived" | "none",
 "source": "jquants", "provider_chain": ["jquants"]}
```

- `adj_factor_cum` = cumulative split/consolidation factor **relative to the most recent
  bar** (so `registered_value / adj_factor_cum(as_of_registration)` and today's close
  compare directly — the exact operation SPEC-TRACKREC §2.2 performs). JP: from
  J-Quants' adjustment fields (`provider`). Others: derived from yfinance's
  `Stock Splits` actions (`derived`). No split data at all → factors are `1` with
  `adjustment_confidence: "none"` — scoring proceeds but flags the prediction's
  `outcome_notes` (never a silent wrong adjustment).
- `dividend` = per-share cash dividend with **ex-date on that bar** (yfinance actions
  for all exchanges, including JP). SPEC-TRACKREC total return sums them; if
  `adjustment_confidence` for dividends is `none`, `realized_return` falls back to price
  return with a note — stated, not fabricated.
- History is served from providers and **backfilled into `price_pins`** for closes, so
  scoring and gates read one store. v2's 504-day cap becomes config
  (`marketdata.max_history_days`, default 1260 — five years covers every horizon bucket).

### 2.4 `GET /fx/{base}/{quote}?date=YYYY-MM-DD`

Dated FX (new — v2 had current-rate-only). `date` omitted = latest. Implementation:
yfinance `{base}{quote}=X` daily history close for that date; weekends/holidays roll
back ≤ `marketdata.fx_max_roll_days` (default 3) with `rolled_from` named in the
response. Same-currency = `1.0`, `source: "identity"`. The v2 1-hour cache and per-pair
fetch-coalescing locks port as-is for the dateless path.

### 2.5 `GET /calendar/{exchange}?from=&to=`

Trading sessions from `pandas_market_calendars` (calendar code per exchange in
`fund.yaml exchanges.<x>.pmc_calendar` — ported from v2's registry): each session with
date, open/close times (half-days included), and `is_open(now)`. SPEC-CORE's
`session_tick` and SPEC-MONITORING's "no tick on a closed session" consume this; so does
the price-pin "was there a session?" check (§2.1).

### 2.6 `GET /earnings/{exchange}/{ticker}?since=`

Earnings/results announcement dates, past and (where known) upcoming:

```json
{"events": [{"date": "2026-08-07", "fiscal_period": "FY2026Q1",
             "kind": "results", "confidence": "provider", "source": "jquants"}],
 "calendar_confidence": "provider" | "filing_derived" | "manual" | "none"}
```

**Decision — per-exchange capability is explicit, and the quarterly sweep is the
backstop.** JP: J-Quants `/v1/fins/announcements` (`provider`). US: EDGAR filing dates
(`filing_derived` — 10-Q/10-K arrival, good enough to *trigger a review after* results;
yfinance's calendar may supplement upcoming dates). HK/SG/UK/AU: **no reliable free
source** — `calendar_confidence: "none"` unless the operator supplies dates in
`fund.yaml earnings.manual: {<slug>: ["2026-08-07", ...]}` (`manual`). SPEC-TRACKREC's
`earnings_sweep` treats `none` coverage as "quarterly sweep only" and the daily desk
roll-up lists active coverage with no earnings calendar — an honest "the fund does not
know when results land for these names" line, instead of a silently missing trigger.

### 2.7 `GET /status/{exchange}/{ticker}`

Listing status for scoring's halt/delisting path: `{"status": "active" | "stale" |
"delisted_suspected" | "unknown", "last_bar": "2026-07-10", "sessions_without_bar": N}`.
Pure inference from bars vs calendar (`stale` at ≥ `marketdata.halt_sessions` missed
sessions, default 5; `delisted_suspected` at ≥ 20) — the service states evidence, and
SPEC-TRACKREC's "expired with a reason, never a fabricated price" rule consumes it.

### 2.8 `GET /financials/{exchange}/{ticker}`, `GET /disclosures/{exchange}/{ticker}`, `GET /company/{exchange}/{ticker}`, `GET /health`

Straight ports (EDGAR/J-Quants/yfinance per v2 router). `/health`: per-provider probe
(v2 `probe_v2` pattern), **J-Quants plan tier and its data delay**, cache and pin-store
stats. Consumed by `fund checkconfig`.

---

## 3. Decisions that were open, now made

1. **Pinned close ≠ live quote, structurally** (§2.1/§2.2). Gates, valuation, and
   scoring can only reach session closes with real dates; the live quote is a separate,
   display-only endpoint. This is the doctrine's price-pinning rule made physical.
2. **Pins persist; providers don't get to rewrite history** (§2.1). Re-pin is an
   explicit audited operator action.
3. **The J-Quants free-plan trap is a `checkconfig` failure, not a pilot surprise.**
   v2's free plan delays data up to **12 weeks**; the price-pin gate
   (`max_price_age_days`) would fail every JP stage — and the likely 2.4 pilot ticker is
   JP (the Yakult bar). Rule: `checkconfig --strict` **FAILs** if any JP coverage is
   `active`/`watch` while the probed J-Quants plan delay exceeds `max_price_age_days`,
   unless `fund.yaml exchanges.tse.price_providers` puts `yfinance` first (then J-Quants
   is the cross-check source). The operator chooses paid-plan-primary or
   yfinance-primary **before** the pilot, knowingly.
4. **Symbology lives here only.** v2 keyed by ticker *suffix* (`2267.T`); v3 speaks
   `(exchange, ticker)`. The suffix/local-code derivation (`yfinance_suffix`, J-Quants
   local code) is private to this service; nothing else may construct a provider symbol.
5. **Earnings calendars are tiered with a stated floor** (§2.6): provider (JP) →
   filing-derived (US) → manual (config) → none, with `none` degrading to the quarterly
   sweep *visibly* (desk roll-up line).
6. **Adjustment and dividend data carry confidence labels** and scoring's behavior at
   each level is defined (§2.3) — no silent wrong adjustment, no fabricated total
   return.

## 4. Config (`fund.yaml`, additions)

```yaml
exchanges:
  tse:  { currency: JPY, country: jp, pmc_calendar: XTKS, yfinance_suffix: ".T",
          price_providers: [jquants, yfinance], sessions: [...] }   # order = decision 3
marketdata:
  cross_check_tolerance_pct: 0.5
  fx_max_roll_days: 3
  max_history_days: 1260
  halt_sessions: 5
  cache_ttl_s: { quote: 300, calendar: 86400, earnings: 86400 }
earnings:
  manual: {}          # slug → [dates]; the HK/SG escape hatch
```

Secrets (`.env`): `JQUANTS_API_KEY` only. No key for yfinance/EDGAR (EDGAR wants a
`User-Agent` string — config, not secret).

## 5. Test plan (TDD)

`tests/unit/marketdata/` with **recorded provider fixtures** (JSON captured from real
responses, redacted) behind the `NewsSource`-style protocol; autouse socket guard on.
Per endpoint: the happy path, the `null`-with-reason path, and the confidence
degradations. Specifically: pin persistence (second fetch serves the stored row even
when the fake provider changes its answer; `repin` updates it and audits); `as_of`
correctness for a date-holding provider vs a bar-derived one; a 1:3 split fixture
producing `adj_factor_cum` that makes SPEC-TRACKREC's split test pass end-to-end; dated
FX rolling over a weekend with `rolled_from` set; calendar half-day handled; earnings
tiers incl. the `manual` override and the `none` desk line; `/status` inference at 5 and
20 missed sessions; **cross-check conflict** marks `freshness_status: conflict`; the
J-Quants free-plan probe fixture makes `checkconfig --strict` fail with the §3.3 rule.
Live-provider smoke tests are `@pytest.mark.integration`, opt-in, keyed.

## 6. Spec Authoring Checklist

- **Side-effect cost.** No LLM calls, ever. Provider HTTP volume is bounded by caches
  (quote TTL, calendar/earnings daily TTL, the pin store making repeat close lookups
  free) and the ported per-provider rate budgets. New sustained load vs v2: the daily
  scoring history scan (~1 call/ticker/day, mostly served from pins after day one) and
  dated-FX lookups (cached per pair-date). DB writes: `price_pins` rows (~1/ticker/day).
- **Concurrency model.** The pin store is the shared state, guarded by the DB unique key
  `(exchange, ticker, trading_date)` + `ON CONFLICT DO NOTHING` (first writer wins;
  concurrent readers get one truth). In-process: the ported FX cache keeps its existing
  lock + per-pair fetch-coalescing; new caches copy that pattern. The service is
  single-process; nothing else shares its memory.
- **LLM-as-filter threat model.** N/A in the strict sense (no LLM in the path), but this
  service is the **authority LLM output is checked against** (price-pin gate,
  reconciliation, scoring), so its own integrity rules are the backstop: no fabricated
  values (null + reason), provider revisions cannot rewrite pins, conflicts are flagged
  not averaged, and sandboxed agents reach it read-only through the egress allowlist
  with no provider keys exposed.
- **Identity-key ownership.** This service owns `(exchange, ticker, trading_date)` for
  pins and *all* provider symbology (decision 4). It borrows `coverage.dossier_slug`
  never — callers pass `(exchange, ticker)` from the coverage row. `fiscal_period`
  strings in earnings events are provider-owned labels passed through opaquely (they key
  `deep_review` `trigger_ref`s, so they must be stable per provider — noted for the
  builder).
- **Test isolation.** All unit tests run on recorded fixtures behind provider protocols;
  the socket guard catches a ported module constructing a real HTTP client at import
  (v2's `yfinance` import pattern — the port must move imports behind the protocol,
  same rule as the news fetchers).
