# Spec Authoring Checklist

Use this checklist before merging any new `specs/SPEC-*.md`. The checklist is
additive to the normal spec structure; answer each item in the relevant section
of the spec, or mark it `N/A` with a short reason.

## 1. Side-Effect Cost

**Question:** Does this change alter the call volume or cost of any provider,
including embeddings, FX lookups, search/news APIs, market data, LLMs, or DB
writes? State the before/after per-cycle or per-request cost for each affected
provider, or explicitly state that provider cost is unchanged.

**Rationale:** Cost changes often happen outside the headline LLM call. Persist,
embedding, enrichment, and lookup side effects need the same accounting.

## 2. Concurrency Model

**Question:** Does this change introduce or mutate shared in-process state, such
as module globals, singletons, class attributes, or caches? If so, state whether
the path can be entered concurrently and name the guard: lock, atomic rebind,
per-invocation ownership, durable store, or another explicit mechanism.

**Rationale:** State described as process-local may still be process-global and
shared across concurrent scheduled jobs or worker threads.

## 3. LLM-as-Filter Threat Model

**Question:** If an LLM decision gates user-visible output and is fed external or
untrusted content, what is the prompt-injection surface and what deterministic
backstops bound it? Name caps, allowlists, keyword corroboration, schema checks,
or fail-closed behavior. Identify and justify any exempt path.

**Rationale:** LLM-filtered output should not rely on the same untrusted content
as the only authority for whether that content is shown.

## 4. Identity-Key Ownership

**Question:** When bookkeeping which item survived, was selected, or was already
processed, does the key belong to this module? If using a value owned by another
module, such as a URL normalized elsewhere, state the invariant and where it is
enforced. Prefer object identity, durable IDs, or explicit indexes where that is
cleaner.

**Rationale:** Borrowed value keys create hidden coupling to another module's
dedupe or normalization rules.

## 5. Test Isolation

**Question:** Does this spec introduce a path that could reach PostgreSQL, LLM
providers, market data, search/news APIs, the harness/sandbox, or other external
services from unit tests? State the fakes/mocks required and whether the existing
unit network guard should catch misses.

**Rationale:** Unit tests must stay service-free and deterministic.

## Suggested Spec Text

Add a short "Checklist" subsection near Decisions or Tests:

```md
### Spec Authoring Checklist

- Side-effect cost: ...
- Concurrency model: ...
- LLM-as-filter threat model: ...
- Identity-key ownership: ...
- Test isolation: ...
```
