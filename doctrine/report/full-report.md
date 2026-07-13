# Full report spec

Seeded verbatim from the "Workflow for full reports" and "Full reports" sections of
`Equity-Research-Prompts_2026-06-05.md` (reorganized, not rewritten; see `design/03` §3).
Covers the workflow, file-naming rule, corrections-log requirement, and the stock-stat
block. The ironclad HTML template lives in `doctrine/templates/`.

## Workflow for full reports
- Before starting a full report, ask any clarifying questions in one batch (template to use, which peers, fiscal year / reporting period, reporting currency, prediction horizon).
- Build and verify the report content first; apply template / visual styling only at the end. Get the numbers right before making it look good.
- Live prices may be up to roughly one trading day old — note the as-of date and move on. Don't stall or over-search chasing a perfectly current tick, but never substitute a guessed or round-number price.
- At the end of a full report, include a short corrections / verification log noting anything that changed from a first-pass assumption or earlier draft on checking primary sources (e.g. a price, EPS, or segment figure that was corrected).
- Always name files with the equity name followed by the company name, symbol, exchange, a dash then the date in formay YYYYMMDD; packaged archive files must follow the same format. An example is "DBS_D05_SGX-20251212.pdf". There is no need to use the full company name if it's too long, as long as the truncated name is identifiable and be consistent, eg. "PCPartner" for "PC Partner", as "PC" along is neither identifiable nor unique, but "Lynas" is fine.

## Full reports
A "full report" should incorporate the analysis from all the section prompts above (Company Overview, Segments, Earnings, Bull vs Bear, Competitive advantages, Supply chain, Management, Red flags, Devil's advocate, Comps, Forward projection, Management questions) — not just the stock-statistics block below. When preparing full reports, ensure that the date of the report is present. Additionally, the following stock statistics should be present:
- Current Price (with date of the price, which may not be date of the report)
- 52 week range
- P/E ratio
- EV/EBITDA
- Market Cap
- Average Volume
- Forward Dividend Yield
- Small chart with 5y price history

Other stats that may be relevant:
- Gearing
- Borrowing Cost

You should also list your recommendation, target price (timeframe) and upside as part of the same or related section. You may format them as necessary, but be consistent.
