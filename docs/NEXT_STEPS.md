# Next steps, model assignments, and Fable checkpoints

Status as of 2026-07-18. This is the PM's standing reference for "what happens next,
who runs it, and when to bring the strongest model back in". Update it whenever a
phase completes or a decision lands (dated `NOTES.md` entry as always).
Companions: `PROMPTS.md` (the execution file itself), `docs/BUILD_REVIEW_CHECKLISTS.md`
(per-branch review criteria), `docs/PROPOSED_DOCTRINE_EDITS.md` (awaiting PM).

## Where things stand

- **Phase 0** complete (scaffolding, doctrine seeded, role prompts placed + audited).
- **All specs written and Fable-reviewed** (2026-07-13 spec session; 2026-07-17/18
  Fable review + corrections): `SPEC-DOMAIN`, `SPEC-CORE`, `SPEC-RUNNER`,
  `SPEC-INITIATION`, `SPEC-MONITORING`, `SPEC-ADAPTER`, `SPEC-TRACKREC`,
  `SPEC-DISTILLATION`, plus `SPEC-MARKETDATA` (new — Phase 2.3 reclassified).
- **Phase 1.3 BUILD is in progress** on branch `spec-domain` (started 2026-07-18).
- No other code exists. v2 keeps running at `/home/alvin/aicode/ai-fund` until cutover.

## Decisions locked (operator/PM)

| Decision | Answer | Recorded in |
|---|---|---|
| pgvector | **dropped**; measured reversal path | SPEC-MONITORING §7 |
| JP price provider order | **yfinance-primary** (no paid J-Quants; free plan = delayed cross-check only; fresh JP pins single-source, stated) | SPEC-MARKETDATA §3.3 |
| Doctrine repo layout | **Mode A** — standalone repo outside the deploy tree; **`fund bootstrap` creates and seeds it** (nothing to do by hand; available after 1.3 builds) | SPEC-DISTILLATION §3, SPEC-CORE §6 |
| Coverage lock through `waiting_pm` | keep; revisit only with 2.4 evidence | SPEC-DOMAIN §11.4 |

## Open PM/operator actions (chronological)

1. **Now:** approve/decline the three staged doctrine diffs in
   `docs/PROPOSED_DOCTRINE_EDITS.md` (the `pm_query.md` one blocks pm_query runs;
   the two `distiller.md` ones matter only by Phase 7).
2. **When 1.3 lands:** populate `/etc/ai-fund/` (`.env`, `houses.yaml`, `fund.yaml`,
   `pricing.yaml` from the `config/*.example` templates) and run
   `fund checkconfig --strict`, then `fund bootstrap`.
3. **Phase 2.1 — the first-house decision** (context, since you asked): Phase 2
   deliberately proves the whole two-tier bet with **one** model house end-to-end
   before anything else is built. The "house" is the provider whose *agentic CLI*
   (the harness) runs the heavy research stages in the sandbox. You need: an API
   account with **either** OpenAI (→ Codex CLI) **or** Google (→ Gemini CLI),
   authenticated **on this host, non-interactively**. There is nothing to decide
   today — the rule is pragmatic: whichever of the two you can authenticate fastest
   becomes `fund.yaml first_house`. If both work, run `fund harness check` on each
   and prefer the one with a usable non-interactive mode **and parseable usage
   output** — cost capture is the harder half of harness ops (SPEC-RUNNER §9), and a
   house whose spend can only be *estimated* is a bad foundation. The other houses
   (DeepSeek/GLM/Qwen via generic-cli) join in Phase 3.
4. **Phase 2.4:** choose the pilot ticker (JP name recommended — the Yakult bar is
   JP; note fresh JP pins are single-source under yfinance-primary) and grade the
   pilot against the Yakult standard.
5. **Phase 5.x:** Mattermost staging server + bot accounts for the adapter.

## Execution sequence and model tiers

| Step | What | Model tier | Notes |
|---|---|---|---|
| 1.3 BUILD | SPEC-DOMAIN then SPEC-CORE | Sonnet-class | **in progress** (branch `spec-domain`); fold SPEC-DOMAIN §12 amendments in directly |
| → PR review ×2 | domain + core branches | **Fable if available**, else Opus + `docs/BUILD_REVIEW_CHECKLISTS.md` | the foundation — review hardest |
| 1.4 CHORE | port `infra/` | Haiku-class | |
| 2.2 BUILD | SPEC-RUNNER | Sonnet-class | the risk phase |
| → PR review | runner branch | **Fable/Opus + checklist** | secrets/egress/gates section |
| 2.3 BUILD | SPEC-MARKETDATA (port + service) | Sonnet-class | spec exists; port bits are mechanical |
| 2.4 ⛔ PM GATE | pilot initiation | PM + any session | triage order at the end of `BUILD_REVIEW_CHECKLISTS.md`; fixes = first doctrine lessons |
| 3.2 BUILD | SPEC-INITIATION (+ remaining drivers) | Sonnet-class | |
| 3.3 ⛔ PM GATE | multi-house pilot | PM | grades corrections-log quality |
| 4.2 BUILD | SPEC-MONITORING | Sonnet-class | 4.3 backfill = Haiku |
| 5.2 BUILD | SPEC-ADAPTER | Sonnet-class | 5.3 ⛔ PM gate from Mattermost |
| 6.2 BUILD | SPEC-TRACKREC | Sonnet-class | |
| 7.1 BUILD | SPEC-DISTILLATION | Sonnet-class | §6 gates are the acceptance criteria |
| 7.2 / 7.3 CHORE | risk/trades port; data migration | Haiku-class | |
| 7.4 ⛔ PM GATE | cutover | PM | two clean weeks alongside v2 |

## Fable checkpoints (if you get access again, in priority order)

1. **PR review of the 1.3 branches** (spec-domain, spec-core) — the state machines,
   queue, and immutability rules everything else stands on. Say: *"review branch X
   against SPEC-Y and docs/BUILD_REVIEW_CHECKLISTS.md."* (`/code-review ultra` is the
   heavyweight alternative any session can be asked to set up.)
2. **PR review of 2.2 (runner)** — sandbox/egress/secrets/gates; the highest-exposure
   surface.
3. **Gate 2.4 (and 3.3) failure triage** — when the pilot misses the Yakult bar:
   classify doctrine vs prompt vs gate-config vs code, and write the fixes as the
   first doctrine lessons. This is the highest-judgment moment in the plan.
4. **Any spec contradiction found during BUILD** — if a builder hits something a spec
   gets wrong or leaves ambiguous, stop and have Fable amend the spec (dated NOTES
   entry), rather than letting the builder improvise around it.
5. **First distillation diff review (Phase 7)** — before you merge the first doctrine
   amendment; also review the 7.1 BUILD against SPEC-DISTILLATION §6.
6. **Pre-cutover review (7.4)** — a final pass over NOTES + dossier/report quality vs
   the design principles before v2 is frozen.

Lower value (don't spend Fable on): CHORE ports, adapter rendering, CLI plumbing,
routine BUILD iteration — the checklists cover those.
