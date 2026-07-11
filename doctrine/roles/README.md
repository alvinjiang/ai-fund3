# Role prompts (seed drafts)

Drafted 2026-07-07 by the Fable design session as part of the v3 redesign (see
`redesign/07-IMPLEMENTATION-ROADMAP.md` step 0.3). These are the stage role prompts
for the v3 pipeline — copy into the v3 repo's `doctrine/roles/` unchanged.

Assembly context (design/04 §5): each stage's prompt is built as
role prompt → doctrine core + relevant sections + global lessons → coverage context →
dossier → stock lessons → task.md. So these files assume the doctrine's hard rules
(pinned price, cite-or-unverified, no placeholders, workbook one-truth, currency
discipline) are already in context — they define the *role*, not the rulebook.

The verifier prompt encodes the PM's proven multi-pass formula. It is the
highest-leverage text in the system. Tune only against PM-gate evidence
(roadmap 2.4 / 3.3), one change at a time, with NOTES.md entries.
