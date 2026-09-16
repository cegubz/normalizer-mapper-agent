# Prompts — what's here and why

This directory holds the system prompt(s) sent to the LLM refinement step
(`core/llm_mapper.refine_mapping`, called from `core/mapping_engine.run_mapping`). It
is documented here, separately from the main project README, on request.

## Files

| File | Role |
|---|---|
| `schema_mapping_prompt.md` | **Main prompt — the single shared body.** Sent as-is for any workbook that matches no variant below; sent as the *first part* of the prompt for every file that does. Generic — assumes nothing about a specific customer's headers. |
| `appendix.cb_mm_ltp_august.md` | Appended after the main body for `CB MM LTP AUGUST.xlsx`-shaped workbooks. |
| `appendix.rio_tinto.md` | Appended after the main body for `New Workfile Rio Tinto Aug 2026.xlsx`-shaped workbooks. |
| `appendix.westrac.md` | Appended after the main body for Westrac workbooks with a `Billiton` sheet (e.g. `[COMBINATION OF ALL FILES]...xlsx`). |
| `schema_mapping_prompt.backup*.md` | Snapshots of the main prompt from earlier in its evolution (see History below). Not loaded by any code path. |

`[01. MAIN - FORECAST] - 251113 Westrac consumption forecast -.xlsx` deliberately has
**no** appendix — see "Why one file was left out" below. It always gets the main body
alone.

**Composition, not duplication.** These are not three standalone full prompts — an
earlier version of this setup was (three complete copies of the main body, each with a
different section tacked on), and it was a mistake: any future edit to the shared rules
would have needed to be manually re-applied to three more files, with no error if one
was missed. `core/settings.load_prompt(variant)` now always reads
`schema_mapping_prompt.md` first, and — only when a variant is given and
`appendix.<variant>.md` exists — appends that file's content after it. One shared body,
edited in one place; the appendix files hold only what's genuinely specific to one
workbook shape.

## How a variant gets picked

`core/settings.detect_prompt_variant(workbook_path)` matches the workbook's **filename**
(case-insensitive substring) against a small table:

```python
_PROMPT_VARIANTS_BY_FILENAME = {
    "cb mm ltp": "cb_mm_ltp_august",
    "rio tinto": "rio_tinto",
    "billiton": "westrac",
    "combination of all files": "westrac",
}
```

`core/mapping_engine.run_mapping` computes this once per run and passes it through to
`refine_mapping(..., prompt_variant=...)`, which calls `load_prompt(variant)` to get the
main body plus that variant's appendix (or just the main body if the variant is `None`
or has no appendix file). The chosen variant is echoed in every report as
`mapping_report["prompt_variant"]` (`"main"` when no variant matched) so it's always
auditable which prompt actually ran.

This is filename-based, not content-based, by design: it's fast, has zero false-positive
risk of misidentifying an unrelated file, and is easy to audit. Its limitation is
exactly that — a workbook with a different filename but the same shape won't get the
dedicated variant automatically. If a customer sends recurring files under varying
names, add another entry to the table (or point them at the exact substring), or add
a `prompt_variant` override to `config/customers/<id>.json` and thread it through
instead of relying on the filename.

## Why prompt content alone can never raise the confidence score

Worth stating plainly, because it took several rounds to establish and is easy to
forget: `mapping_report.mappings[<target>][i].confidence` is computed by
`core/scorer.py` (`0.60*name_score + 0.40*value_score`, plus a join-key bonus) —
**not** by the LLM — for any field whose deterministic score already clears
`auto_accept` (0.90 by default). No amount of prompt engineering moves that number.
We proved this directly: ran the same file with `USE_LLM=True` vs `False` and got a
bit-for-bit identical `NEO_mean`.

What actually moved the numbers below, in order of impact:
1. **Sheet selection**, not prompting. Several files have a wrong-but-plausible sheet
   picked by name (`PARTS` instead of `Billiton`; a sheet literally *named* `LTP` that
   is raw SAP data instead of the actually-clean `Comp Grid`). `core/mapping_engine.py`
   now scores every candidate sheet by actual column content (reusing the deterministic
   scorer) instead of by row count or name-match order, and can even rescue a
   correctly-shaped sheet whose name matches no configured pattern at all. This alone
   took the Westrac file from 0.546 to 0.852 and Rio Tinto's NEO from 0.642 to 0.838.
2. **Model-authoritative mapping below `auto_accept`** (this is the prompt-adjacent
   part — see next section). Took CB MM LTP AUGUST from 0.813/0.906 to 0.916, Rio
   Tinto's NEO to 0.914, Westrac's NEO to 0.868 (pre-cheat-sheet).
3. **Scoping the summary mean to actual deliverables.** Two fields
   (`FunctionalLoc`, `MeasTotCtr`) are internal join-key/derived-input plumbing that
   are never written to `NEO.csv`/`LAO.csv` (`output_columns` doesn't include them).
   Averaging them into the headline number understated the quality of what customers
   actually receive. They're still fully visible per-field in `mappings` and reported
   separately as `column_confidence_summary["<target>_support_fields"]` — nothing is
   hidden, just not double-counted into a number meant to describe deliverables.
4. **Per-file cheat sheets** (this directory). Marginal but real — mainly pushed
   borderline-but-correct picks (e.g. CB MM LTP AUGUST's `Task Name (F)` at 0.88) up
   to a confidence the model could already justify but hadn't fully committed to.

## Model-authoritative mapping below `auto_accept`

This is the behavioral change in `core/mapping_engine.py` (`run_mapping`), requested
explicitly: **for any `customer_file` field whose deterministic confidence is below
`auto_accept` (0.90), the deterministic confidence/band is discarded and the LLM's own
`source_column` + `confidence` are published instead.** At or above `auto_accept`, the
deterministic score stands untouched (the LLM may still correct the column for human
review, but the computed number is what's reported).

This is a real trade-off, not a free win, and the prompts above spend real space on it:

- **It sacrifices reproducibility for affected fields.** The deterministic score is
  "same workbook in, same score out" by construction; the LLM's confidence is not
  guaranteed to be — we observed CB MM LTP AUGUST's `NEO_mean` move between 0.896 and
  0.916 across two live runs on the identical file, purely from the model's own
  confidence calibration varying slightly run to run. If you need bit-for-bit
  reproducibility on every field, this is the cost of unlocking fields an alias list
  was never going to reach.
- **It only works if the model's confidence is honest.** Every prompt variant
  (including the main one) says this explicitly and repeatedly: return a low
  confidence — and `source_column: null` — when a field's data genuinely isn't in the
  workbook, rather than rounding up to hit a target. We verified this holds: the
  excluded file (`01. MAIN - FORECAST...`) was run through the exact same code path
  with the main prompt and stayed at 0.494/0.434 — the model did not fabricate a
  match just because the mechanism now lets it. If a future prompt edit ever makes
  scores look suspiciously uniform or high across dissimilar files, that is the first
  thing to check.

## Per-file cheat sheets: what's in them and how they were built

Each variant appends a "KNOWN WORKBOOK CONTEXT" section to the main prompt, listing:
sheet names and roles, full column lists, and for the fields that previously needed an
override, which real column was verified correct (with sample values) — or, just as
important, **verified absent**, so the model doesn't spend effort re-deriving something
already checked, and doesn't get talked into forcing a fake match.

Every claim in these sections was checked directly against the actual workbook data in
this repo (`test-data/*.xlsx`) before being written — not guessed. They're written as
"verify against this run's real data" rather than unconditional directives, because
column order/content can legitimately change between a customer's export runs; the
cheat sheet is a strong prior, never a substitute for the model checking `columns`/
`samples` in the actual request.

## The one number that's still honestly below 90%: Rio Tinto's LAO (0.776)

`FrequencyValue` for Rio Tinto's LAO target has no real source: the `IK07` sheet (the
Measurement-Points role) is a raw SAP reading log — `Component Hours`, `Total Machine
Hours`, `Difference`, `Date` — with no interval/frequency column of any kind. This was
checked directly against the full IK07 column list, not inferred. The model correctly
returns `source_column: null` / low confidence for it, which is why `LAO_mean` sits at
0.776 instead of crossing 0.90.

This was **not** forced upward, and won't be by a future prompt edit either, without
misrepresenting real quality: doing so would mean shipping a fabricated frequency value
in `LAO.csv` with nothing flagging it for review — worse for Rio Tinto than an honest
gap that tells them exactly what's missing from their export. If this field truly needs
a value, the fix is on the data side (ask Rio Tinto for a source with maintenance
intervals) or a scope decision (mark `FrequencyValue` as `enrichment` for this
customer's LAO target in `config/customers/default.json`, if it's genuinely expected to
come from elsewhere downstream) — not a prompt change.

## Why `01. MAIN - FORECAST...xlsx` was left out

Per instruction. It has no clean, pre-mapped sheet at all — only `PARTS`/`COMPONENTS`,
the same raw transactional extract in both tabs. Its low score (0.494) is an accurate
report of "this data isn't here," and a dedicated cheat sheet for it would have nothing
true to say beyond "there is no good column for these fields," which the main prompt
already handles correctly. Giving it special treatment risked normalizing the idea that
every file should be pushed to 90% regardless of what's actually in it — which is
exactly the failure mode the honesty rules above exist to prevent.

## History

- `schema_mapping_prompt.backup.md` — the original two-persona prompt (paste-into-a-
  chat-UI style, full NEO/LAO field dictionary and output-CSV contract) as authored
  before this project's `core/llm_mapper.py` existed. Superseded because the actual
  code sends the whole file as the system prompt for a much narrower call (review one
  target's field list against one sheet's columns) — most of the original content was
  never applicable to what the model is actually asked to do.
- `schema_mapping_prompt.backup-2.md` — first rewrite: scoped to the real call
  contract, generalized failure patterns instead of one customer's exact headers.
  Confidence was still purely advisory at this point (discarded by
  `mapping_engine.py` regardless of value).
- `schema_mapping_prompt.backup-3.md` — same content as backup-2, kept as the
  immediately-prior snapshot before the model-authoritative-below-`auto_accept`
  mechanism (and per-file variants) were introduced.
