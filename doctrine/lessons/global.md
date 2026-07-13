# Lessons (global)

Seeded verbatim from the "Further notes from past mistakes" section of
`Equity-Research-Prompts_2026-06-05.md`. Amended only via distillation PRs (Claude meta,
`design/03` §2.7). The operational process-rule subsections that accompanied these lessons
in the source were moved into `core.md`; this file holds the lessons themselves.

## Further notes from past mistakes
- Always fetch the primary filing directly. For any financial figure — revenue, earnings, cash, debt — the source must be the company's own exchange release or annual report, not a news summary or search snippet.
- Always look up every piece of data - do not rely on past training information.
- Treat FX rates as live data requiring a dedicated search. Before writing any report with currency conversions, search explicitly for the spot rate on the report date, and note in the report that the FX date used is static as of that date. If the analysed period in question stretches more than 6 months or over a volatile currency period, use correct FX rates for each event in the period, noting in the report that FX rates are as of the event.
- Cross-check quarterly data against the report date. Q3 FY26 data must come from the Q3 FY26 quarterly — not Q1. When fetching production data, explicitly verify which quarter it covers before using it.
- Safety, incident, ESG and other track-record claims must reflect the most recent reported half or quarter, not just the last full year. A clean full-year statistic (e.g. “zero fatalities in FY25”) can be overtaken by a later half-year disclosure — check the latest period before stating it.
- Flag any figure that cannot be traced to a named primary source. The correct response when a figure cannot be verified is to say “unverified” or omit it — not to generate a plausible substitute.
- Never place a placeholder or assumed value into a deliverable (report or workbook). If a figure isn't verified yet, leave it blank or mark it “unverified” — a placeholder such as a round-number share price will be forgotten and ship as if it were real. This applies to the report narrative, not just the workbook.
- Search for current market caps for all comps on the report date. Do not use stale or assumed data.
- You should use a smaller, faster yet capable AI model where available for sub-agents that are searching and downloading data.
