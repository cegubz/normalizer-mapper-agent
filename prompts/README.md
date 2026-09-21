# Prompts — what's here and why

This directory holds the system prompt(s) sent to the LLM refinement step
(`core/llm_mapper.refine_mapping`, called from `core/mapping_engine.run_mapping`). It
is documented here, separately from the main project README, on request.

## Files

| File | Role |
|---|---|
| `schema_mapping_prompt.md` | **Main prompt — the single shared body.** Sent as-is for any workbook that matches no variant below; sent as the *first part* of the prompt for every file that does. Generic — assumes nothing about a specific customer's headers. |
| `appendix.fmg.md` | Appended after the main body for `CB MM LTP AUGUST.xlsx`-shaped workbooks (variant key `"fmg"` in `core/settings._PROMPT_VARIANTS_BY_FILENAME`). |
| `appendix.rio_tinto.md` | Appended after the main body for `New Workfile Rio Tinto Aug 2026.xlsx`-shaped workbooks. |
| `appendix.bhp.md` | Appended after the main body for Westrac workbooks with a `Billiton` sheet (e.g. `[COMBINATION OF ALL FILES]...xlsx`; variant key `"bhp"`). |
| `cross-references/*.csv` | AMT lookup tables (equipment/floc → SerialNumber/ComponentCode/ModifierCode), one per workbook shape. Not sent to the LLM — read directly in code by `core/cross_reference.py` as a deterministic post-processing pass, after the LLM/scorer step this directory's prompts drive. **This is the fallback copy** — when `CROSS_REFERENCE_BLOB_CONTAINER` is set, a matching Blob container is tried first; see the main `README.md` § "Cross-reference source" for that. See "AMT cross-reference enrichment" below for how the join itself is used. |
| `appendix.cb_mm_ltp_august.md`, `appendix.westrac.md`, `schema_mapping_prompt.cb_mm_ltp_august.md`, `schema_mapping_prompt.rio_tinto.md`, `schema_mapping_prompt.westrac.md` | **Orphaned** — byte-identical to their `prompt-backups/` counterparts, not read by any code path (`core/settings.py`'s variant keys were renamed to `fmg`/`bhp` and only `schema_mapping_prompt.md` is ever loaded as the main body; see the two rows above). Left in place rather than deleted as part of this change since removing files wasn't asked for — safe to delete. |
| `schema_mapping_prompt.backup*.md` (top-level) | Same story: identical to `prompt-backups/schema_mapping_prompt.backup*.md`, not loaded by any code path. |

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

## AMT cross-reference enrichment (SerialNumber + ComponentCode + ModifierCode)

`SerialNumber`, `ComponentCode` and `ModifierCode` (and, on `LAO`, `AssetName`) used to
be pure `enrichment` fields — always blank, per this project's original non-goal of
never inventing enrichment data (see the main README). They're the fields a downstream
Snowflake key is built from (`SerialNumber` + `ComponentCode` + `ModifierCode`), so
leaving them permanently blank was a real gap, not just an unfinished nice-to-have.

Two things changed to close it, both additive and neither touching the "never invent"
rule itself:
1. **`config/customers/default.json`**: these fields are now `source_class:
   "customer_file"` — mappable like any other field — because several workbook shapes
   genuinely carry them as real columns (Rio Tinto's `Comp Grid`: `Serial Number`,
   `Component Code`, `Modifier Code`; `IK07`: `CC`/`MC`/`CC FL`/`MC FL`, `Eq ID`). This
   call's own prompt guidance for them is in `schema_mapping_prompt.md` under
   "SerialNumber / ComponentCode / ModifierCode (and, on LAO, AssetName) — the AMT key
   fields". `SerialNumber` also carries an `exclude_headers: ["prefix", "pfx"]` guard
   (enforced in `core/scorer.py`, not just prompt text) — several of these shapes *also*
   have a genuine "Serial Prefix" column (a model-family code shared by many units, e.g.
   "RJG"/"F520" — not a per-unit serial), and its partial name overlap with "serial"
   was enough to clear the confidence bands and get written in as if it were the real
   serial before this guard existed. `exclude_headers` disqualifies a candidate column
   outright by header substring, before scoring, for exactly this "plausible but wrong"
   case — a genuine low-confidence gap should still surface as `low_review` for human
   attention, but a column carrying the wrong *kind* of value entirely shouldn't be a
   candidate at all.
2. **`core/cross_reference.py`**: a deterministic (not LLM) post-processing pass that
   runs after `builders.build_target`, keyed by workbook shape (`prompt_variant`) +
   target. It fills a cell **only when it's still blank** after step 1 — it never
   overwrites a customer_file-derived value — by joining the cleaned rows against the
   matching `prompts/cross-references/*.csv` AMT lookup table (the files named in the
   table above). Verified against real test-data workbooks:
   - FMG's `CB MM LTP AUGUST` NEO recovers 2,637/9,296 `ComponentCode`/`ModifierCode`
     rows (`Group (L)` + functional-location suffix → `fmg_cross-reference.csv`).
     `Measurement Points` (LAO) has no `Group (L)` column to reproduce that same key, so
     it falls back to the functional-location suffix *alone*, restricted to the subset
     of suffixes that resolve to one `(ComponentCode, ModifierCode)` pair regardless of
     `Group (L)` (see `_index_csv_unique_by`) — this recovers 2,975/22,893 rows; the
     rest are either ambiguous without `Group (L)` (left blank on purpose), match
     nothing in the table, or match a table row that itself has no code (a genuine gap
     in the lookup table, not a code bug). FMG has no full serial number anywhere in
     either sheet or in `fmg_cross-reference.csv` (only a "Serial Prefix" family code),
     so `SerialNumber` stays a genuine, permanent gap for this shape on both targets.
   - Rio Tinto's `IK07` LAO recovers 515 more `ComponentCode`/`ModifierCode` rows beyond
     what the sheet's own `CC`/`MC` already had (`Func Loc Key` →
     `rio-tinto_cross-reference.csv`), plus `SerialNumber` for 13,757/59,526 rows from
     the same lookup row (`IK07` has no full serial column of its own, only "Serial
     prefix"). `Comp Grid` (NEO) needs no cross-reference for any of the three — it
     carries `Serial Number` as a real, fully-populated column, mapped directly by step
     1 above.
   - BHP/Westrac's NEO path (`Model` + part number → `bhp_cross-reference.csv`'s
     `CONCAT2`, then parsing the compound `AMT` string) fills `ComponentCode`/
     `ModifierCode` the same way but **unverified** — no Billiton-shaped workbook exists
     in `test-data/` to confirm the join key or string format against real data.
     `bhp_cross-reference.csv` has no serial-number column, so `SerialNumber` here
     relies solely on the sheet's own column (also unverified) with no code-level
     fallback. This shape has no LAO/measurement-points sheet at all.

   `mapping_report.mappings[<target>]` gets a `"...+cross_reference"` status suffix and
   a note with the exact row count whenever this pass fills something, so a fill is
   always auditable back to "customer file" vs. "AMT lookup," never silently merged.

This also surfaced and fixed a real pre-existing bug in `core/mapping_engine.py`: the
deterministic scorer's greedy column assignment had no confidence floor, so a
`customer_file` field with genuinely no real candidate column (exactly the FMG-NEO
`ComponentCode`/`ModifierCode` case) still got assigned *whatever column was left over*
— e.g. `Department (C)` — at `reject`-band confidence (~0.40), and that got written
straight into the output CSV when the LLM was off (or agreed with nothing, which for a
`reject` band is exactly what it's supposed to do). `_assemble_outputs` now nulls out
any `reject`-band pick before it reaches `resolved`/the output frame, regardless of
whether the LLM ran, with a note explaining the rejection — consistent with the
"an honest gap beats a false match" rule already stated above for confidence generally.

## The confidence-mean numbers that moved because of the above

`FrequencyValue` for Rio Tinto's LAO target still has no real source: the `IK07` sheet
(the Measurement-Points role) is a raw SAP reading log — `Component Hours`, `Total
Machine Hours`, `Difference`, `Date` — with no interval/frequency column of any kind.
This was checked directly against the full IK07 column list, not inferred. The model
correctly returns `source_column: null` / low confidence for it — that part is
unchanged.

`NEO_mean`/`LAO_mean` moved twice now, for the same reason each time: a field that used
to be a pure `enrichment` placeholder (never counted in the mean at all) became
`customer_file`-mappable, and the mean started reflecting how well it's actually
covered instead of hiding it. First `AssetName`/`ComponentCode`/`ModifierCode`, then
`SerialNumber` on top:
- Rio Tinto `NEO_mean`: 0.813 → 0.92 → 0.927. `Comp Grid` carries a real, well-populated
  `Serial Number` column, so adding it only helped.
- Rio Tinto `LAO_mean`: 0.776 → 0.815 → 0.756. `IK07`'s own `CC`/`MC`/`CC FL`/`MC FL`
  raised it the first time; `SerialNumber` has no literal column on `IK07` itself (only
  the AMT cross-reference recovers it, *after* this mean is computed), so counting it
  pulled the mean back down — an honest reflection of what `IK07` alone carries, not a
  regression in what the pipeline actually recovers (see the cross-reference numbers
  above).
- `CB MM LTP AUGUST` (FMG) `NEO_mean`: 0.813 → 0.704 → 0.674, `LAO_mean`: 0.99 → 0.558 →
  0.479. This workbook genuinely lacks all three original fields *and* has no full
  serial number anywhere (see `appendix.fmg.md`) — each addition correctly drags the
  number down further instead of hiding a real, permanent gap.

Every direction here is the same principle: the mean reflects real field coverage. A
field that's genuinely absent from a workbook shape should make the mean go down when
it starts being measured — that's the mean doing its job, not a quality regression.

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
