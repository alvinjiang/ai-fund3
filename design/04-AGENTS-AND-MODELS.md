# AI Fund v3 — Agents, Models, and the Learning Loop

Status: Approved by PM 2026-07-07.

## 1. Identity = model house

Agent identities are **model houses**, not personas. A house is a provider plus its
configured models and harness. Track records, lead assignments, and Mattermost bot
identities all attach to houses. The v2 personas (japan_specialist, value_investor, …)
are retired as identities; any useful personality/regional framing becomes per-stock
prompt context supplied by the dossier and doctrine, not a fixed bot.

### House registry (operator config, illustrative)

```yaml
houses:
  gpt:
    provider: openai
    heavy_model: <current strongest>     # models are config, never hardcoded (v2 lesson)
    light_model: <current cheap>
    harness: { type: codex-cli, config: {...} }
    budgets: { daily_usd: 25, per_run_usd: 40 }
    assignable: true
  gemini:
    provider: google
    harness: { type: gemini-cli, ... }
    assignable: true
  deepseek:
    provider: deepseek
    harness: { type: opencode, ... }     # OpenAI-compatible harness
    assignable: true
  glm:
    provider: zai
    harness: { type: opencode, ... }
    assignable: true
  qwen:
    provider: dashscope
    harness: { type: qwen-code, ... }
    assignable: true
  claude:
    provider: anthropic
    assignable: false                    # PM decision: Claude is meta, not analyst
    meta: true
```

Rules:

- **Exactly one lead house per coverage.** Default contributors: all other enabled,
  assignable houses (configurable per coverage).
- The PM suggests the initial lead at `/propose` time (cheap start — no bake-off).
  The orchestrator may *display* a track-record-based suggestion, but the PM chooses.
- Houses are swappable: a lead change re-points the coverage; the dossier (which
  belongs to the fund, not the house) carries all knowledge across.
- `assignable: false` + `meta: true` gives Claude structural support with a firewall:
  meta runs never write dossiers or predictions; they write doctrine proposals and
  system memos.

## 2. Lead and contributor mechanics

- **Lead** owns the dossier: authors initiations, runs deep reviews and event
  analyses, answers PM queries, registers predictions under its name.
- **Contributors** appear at: initiation (verify passes), auto cross-checks (material
  TP/stance changes, thesis tripwires), PM-triggered reviews, and lead reviews. Verify
  passes rotate across contributors so no pair calcifies.
- **Disagreement** is structured, not conversational: a cross-check that materially
  disagrees files its stance in `stage_result.yaml`; both positions surface to the PM
  side by side with workings. No debate transcripts.
- **Lead change**: proposed by contributors in `lead_review` runs (with rationale),
  or by the PM directly. PM approval required, always. Handover = dossier note +
  audit entry; open predictions stay with their author house.

## 3. Track record

Every house accrues (see 02-DOMAIN-MODEL.md §4):

| Metric | Source |
|---|---|
| Prediction hit rate (by kind, horizon) | scored predictions ledger |
| Target MAE vs realized | scoring job |
| Confidence calibration | confidence vs outcome curve |
| **Corrections-received rate** | verify passes' corrections logs, attributed to the authoring stage's house |
| Escalation quality | event analyses that PM rated useful (lightweight 👍/👎) |

The corrections-received rate is the novel one: the pipeline itself measures each
house's factual-error rate long before market outcomes arrive. It is the earliest
signal for lead reviews.

Visibility: `/track-record [house|ticker]` and CLI; injected into `lead_review` runs;
summarized in distillation memos. Agents see their own and peers' records — the point
is calibration pressure, not shame.

## 4. Doctrine and the learning loop

Doctrine (`doctrine/`, layout in 03-RESEARCH-PIPELINE.md §3) is the fund's research
constitution, seeded from the PM's proven Equity-Research-Prompts file. It is
git-versioned; every run records the doctrine version used.

The learning loop, formalized from the PM's manual practice:

1. **Collect**: verify passes emit corrections; scoring emits misses; runs append
   stock-specific `lessons.md` entries.
2. **Distill** (Claude meta, monthly or on demand): cluster recurring failure
   patterns; propose amendments to `doctrine/lessons/global.md` (and, rarely, role or
   section prompts) as a reviewable diff. Amendments must be *rules that would have
   prevented specific, cited incidents* — the style of the existing "past mistakes"
   section (e.g. "never attribute a cached price to a date").
3. **Approve**: PM merges or rejects the diff. Nothing self-amends.
4. **Apply**: next runs load the new doctrine version automatically.

Claude-meta also produces the periodic system-health memo (calibration by house,
pipeline failure patterns, cost anomalies, doctrine-drift observations) and is the
intended model for future design/review sessions over this system itself.

## 5. Prompt assembly (per stage)

Order (adapted from v2's proven assembly, personas removed):

1. Role prompt (`doctrine/roles/<role>.md`)
2. Doctrine core + relevant sections + global lessons
3. Coverage context: tier, levels, position (if held: size, book, P&L from the
   deterministic layer), currency discipline reminder
4. Dossier (the workspace itself for harness stages; inlined digest for api stages)
5. Stock-specific `lessons.md`
6. Task (`task.md`: trigger context, expectations, required outputs)

Hard rules ride in doctrine core, not code: cite-or-mark-unverified, single pinned
price, no placeholders, confidence must be stated by the model (code never invents
one — enforced by the stage gate, 03 §1).
