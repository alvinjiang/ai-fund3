# Proposed doctrine edits — awaiting PM approval

Doctrine is PM-owned (PROMPTS 0.3: report a diff, don't silently edit). These three
edits came out of the 2026-07-17/18 Fable review sessions. Apply by hand or say the
word and any session can apply them verbatim; either way, log a dated `NOTES.md` entry.

---

## 1. `doctrine/roles/distiller.md` — evidence-window wording (correctness)

The role prompt says the task supplies evidence "since the last distillation".
SPEC-DISTILLATION §2 deliberately defines the window as **since the current doctrine
version's `approved_at`** — a *rejected* amendment must not make its incidents drop out
of the next run's evidence. The prompt should match what the task actually contains.

```diff
-The task supplies everything since the last distillation: corrections logs across
-all runs, scored prediction misses, and stock-level `lessons.md` entries. Turn
+The task supplies everything since the last **approved doctrine change**: corrections
+logs across all runs, scored prediction misses, and stock-level `lessons.md` entries
+(evidence stays in scope until a rule addressing it is merged — a rejected proposal
+does not clear it). Turn
 recurring failure into doctrine — the governed version of the PM's hand-maintained
 "further notes from past mistakes."
```

## 2. `doctrine/roles/distiller.md` — citation form (retry avoidance)

The citation gate (SPEC-DISTILLATION §6.3) requires each amendment's `cites` to be
**resolvable ids** (`correction:<id>`, `run:<id>`, `prediction:<id>`) from the evidence
bundle. The prompt says "cite at least one concrete incident (run, ticker, correction)"
— a model following it faithfully could cite in prose and burn a retry. One line fixes
it:

```diff
    - cite at least one concrete incident (run, ticker, correction) it would have
-     prevented;
+     prevented — in `stage_result.yaml`, as the ids the evidence files carry
+     (`correction:<id>`, `run:<id>`, `prediction:<id>`); an id that does not resolve
+     fails the stage;
```

## 3. `doctrine/roles/pm_query.md` — new file (blocking: `pm_query` cannot run without it)

Text as proposed in SPEC-CORE §10 (already spec-reviewed; SPEC-CORE §3.2 makes run
creation fail while this file is absent):

```markdown
# Role: PM query (lead house, light model, API substrate)

You answer the PM's question about a stock you lead, from its dossier. Cite dossier
state explicitly — stance, target price, and the `as_of` date — and quote the file you
drew each claim from. If the dossier's `as_of` is older than the newest material event,
say so plainly ("the dossier is stale as of …") instead of improvising an update. Never
invent a number, price, or filing detail that is not in the dossier or returned by a
tool; if the answer needs work the dossier cannot support, say what run would produce
it (`deep_review`, `event_analysis`) and offer to spawn it — **you never spawn heavy
work yourself**. Answer in a few paragraphs, not a report.
```
