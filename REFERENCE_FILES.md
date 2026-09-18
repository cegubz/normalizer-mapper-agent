# Standalone-CSV Workflow (LTP.csv / Measurement-Points.csv as independent inputs)

An **additional** workflow, alongside the original one. It does not replace or change
anything about the existing single-workbook path (`"input"`, `run_mapping()`,
`function_app.py`, `foundry_app.py`) — it's a second way to feed the same agent core.

## Why this exists

The established workflow reads **one Excel workbook** and finds the LTP sheet (→ NEO)
and the Measurement Points sheet (→ LAO) as tabs inside it. Some upstream systems
instead export those as their own **standalone CSV files** — e.g. dropped in Blob
storage as `LTP.csv` and `Measurement-Points.csv` (or similarly-named files per your
naming convention).

**LTP.csv and Measurement-Points.csv are not auxiliary/lookup data for each other** —
each is an independent primary source for its own output: `LTP.csv` on its own is
enough to build `NEO.csv`; `Measurement-Points.csv` on its own is enough to build
`LAO.csv`. You call the agent **separately per file**, with a payload for whichever
one you have — there is no requirement to post both in the same call. (If you *do*
post both together, NEO/LTP mapping additionally gets the small functional-location
join-key bonus described lower down — a bonus, not a requirement.)

## How role detection works

Each posted file's **role** (`LTP` → feeds `NEO`, `MEASUREMENT_POINTS` → feeds `LAO`) is
auto-detected from its **filename** — you don't have to label which file is which.
This mirrors exactly how the workbook workflow already detects a role from a sheet's
*tab name* (`sheet_config[].match_patterns`), just matched against the file's basename
instead, via a parallel array: `sheet_config[].filename_patterns`.

```json
{
  "role": "LTP",
  "target": "NEO",
  "match_patterns": ["ltp", "major component", "..."],
  "filename_patterns": ["ltp"]
},
{
  "role": "MEASUREMENT_POINTS",
  "target": "LAO",
  "match_patterns": ["measurement point", "measuring point", "..."],
  "filename_patterns": ["measurement points", "measuring points", "meas points", "ik07"]
}
```

Matching is **punctuation-insensitive**: separators (`-`, `_`, `.`, spaces) are
collapsed before comparing, so a pattern of `"measurement points"` matches
`Measurement-Points.csv`, `measurement_points_export.csv`, and
`Measurement Points (Nov).CSV` alike. If a `sheet_config` entry hasn't defined
`filename_patterns` yet, matching falls back to `match_patterns` so older customer
configs still work (they may just need broader patterns to match real filenames).

**`filename_patterns` is intentionally a small starting array** — add more patterns as
new customer naming conventions show up (e.g. a customer that exports
`CB_LTP_Nov2026_v3.csv` needs no code change, just another string in the array).

If two posted files both match the same role's patterns, the one whose *columns*
actually score best against that target's fields (the same deterministic scorer used
everywhere else) wins — reported as a warning, not silently.

If a file's role can't be matched at all, that target is simply skipped (empty
`mappings`, no row output for it) and the report's `warnings` says so — this is
**expected and benign** when you intentionally posted only one file (its counterpart's
role obviously won't match anything, since you didn't send it). It's only worth a
second look if you *did* post a file for that role and it still didn't match — check
the filename against the configured `filename_patterns`.

## Request contract

Alternative to `"input"` in the existing contract (exactly one of the two is required).
`"reference_files"` is a **list**, but it's normal and expected to post just one entry —
one CSV, one target output:

**LTP.csv alone → NEO.csv only:**
```json
{
  "customer_id": "default",
  "reference_files": [
    { "path": "https://<account>.blob.core.windows.net/.../LTP.csv" }
  ],
  "output_dir": "…",
  "return_inline": false
}
```

**Measurement-Points.csv alone → LAO.csv only:**
```json
{
  "customer_id": "default",
  "reference_files": [
    { "path": "https://<account>.blob.core.windows.net/.../Measurement-Points.csv" }
  ],
  "output_dir": "…",
  "return_inline": false
}
```

Posting both together in one call also works (one combined `mapping_report` covering
both NEO and LAO, plus the join-key bonus) — use whichever shape matches how your
upstream system actually delivers files: two separate triggers/calls (one per file, as
they land), or one call once both have landed.

Each entry accepts the same shapes as `"input"` today:
- `"container/blob.csv"` or a full blob URL — plain-string shorthand for `{ "path": "..." }`.
- `{ "path": "..." }` — a path/URL the configured storage backend can fetch. With
  `STORAGE_BACKEND=azure_blob` this can be a real Blob URL (SAS-signed, or bare —
  see README.md § "Passing Blob paths instead of inline bytes" for the three
  accepted shapes and their auth).
- `{ "content_base64": "...", "filename": "LTP.csv" }` — inline bytes (e.g. Logic Apps
  posting a file directly instead of a blob reference). `filename` matters here — role
  detection needs it, so don't drop the extension.

The response shape (`status`, `mapping_report`, `outputs`) is **identical** to the
workbook workflow, just with only the target(s) you had a source file for populated.
One cosmetic note: `mapping_report.matched_sheets[].sheet_name` holds the matched
**filename** in this workflow (e.g. `"LTP.csv"`), not an Excel tab name — the field is
kept the same for schema stability with existing consumers.

## Running it locally

```bash
# LTP.csv alone -> NEO.csv only
python run_local.py --reference-files ./test-data/LTP.csv --customer default --out ./_out

# Measurement-Points.csv alone -> LAO.csv only
python run_local.py --reference-files ./test-data/Measurement-Points.csv --customer default --out ./_out

# both together -> NEO.csv + LAO.csv (+ the join-key bonus)
python run_local.py --reference-files ./test-data/LTP.csv ./test-data/Measurement-Points.csv \
    --customer default --out ./_out
```

(`--reference-files` takes any number of paths in any order — role is detected per
file, not by position.)

## Verified parity with the workbook workflow

Run against `test-data/LTP.csv` and `test-data/Measurement-Points.csv` (extracts of the
same data as `test-data/CB MM LTP AUGUST.xlsx`), deterministic-only (`USE_LLM=false`):

| | Workbook (`.xlsx`, both sheets) | Both CSVs together | `LTP.csv` alone | `Measurement-Points.csv` alone |
|---|---|---|---|---|
| `outputs` present | NEO, LAO, Normalized | NEO, LAO, Normalized | **NEO, Normalized** | **LAO, Normalized** |
| Row counts | NEO 9,296 · LAO 22,893 · Normalized 1,403 | identical | NEO 9,296 · Normalized 1,403 | LAO 22,893 · Normalized 0 |
| `LAO_mean` confidence | 0.99 | identical (0.99) | — (LAO not built) | 0.99 |
| `NEO_mean` confidence | 0.788 | 0.755 | 0.755 | — (NEO not built) |

Calling with just one file cleanly produces only that file's target — the other
target is simply absent from `outputs`, not an empty/broken CSV. All 1,403 quarantined
rows come from the LTP side; `Measurement-Points.csv` alone quarantines none, which is
why its `Normalized` count is 0 instead of 1,403 in that column.

The `LAO_mean`/`NEO_mean` values above predate `SerialNumber`/`ComponentCode`/
`ModifierCode` becoming `customer_file`-mappable fields (see `prompts/README.md`'s "AMT
cross-reference enrichment" section) — they've since moved for the same reason
documented there (a field that's genuinely absent from this workbook shape correctly
pulls the mean down once it starts being measured, rather than staying hidden as a
permanent `enrichment` placeholder). This table's point — that the reference-files and
workbook workflows produce identical mappings for the same underlying data — still
holds; only the absolute confidence numbers are stale. Also note: this workflow detects
`prompt_variant` (and therefore which `appendix.*.md`/cross-reference table applies)
from the posted file's own basename, so a generically-named `LTP.csv` won't match the
`"cb mm ltp"` filename needle `CB MM LTP AUGUST.xlsx` does — the AMT cross-reference
fill for `ComponentCode`/`ModifierCode`/`SerialNumber` only fires when the filename
itself carries a recognizable customer/workbook-shape hint (see
`core/settings._PROMPT_VARIANTS_BY_FILENAME`), independent of this workflow vs. the
workbook one.

The small remaining `NEO_mean` gap is expected, not a bug: a workbook cell holds a
native Excel date, while the same date in a CSV is plain text re-parsed by
`dateutil` — slightly less certain evidence for the scorer, not a mapping error.

Getting `LAO_mean` to match exactly required one fix, made here because it affects
both workflows: `core/transforms.to_numeric` (and the scorer's matching numeric-evidence
check in `profiling._value_evidence`) didn't strip thousands-separator commas or
padding whitespace — so a CSV-native `" 20,000 "` silently became blank in the actual
output CSV, not just under-scored. Real `.xlsx` numbers are usually native Excel
numerics and were never affected, but a workbook column exported as *text* would have
hit the same bug. Both now go through the shared `transforms.clean_numeric_text` first.

## Onboarding a new naming convention

No code change — same spirit as onboarding a new customer:

1. Open `config/customers/<customer_id>.json`.
2. Add the real filename shape (or a distinctive substring of it) to the relevant
   role's `filename_patterns` array, e.g. `"cb_ltp"` for `CB_LTP_Nov2026_v3.csv`.
3. Call the agent with `"reference_files": [...]` instead of `"input"`.

## What's shared with the workbook workflow (so behavior never drifts between the two)

Everything **after** role resolution is the exact same code path
(`core/mapping_engine._assemble_outputs`): column profiling, the deterministic scorer,
LLM refinement, row normalization/quarantine, and the NEO/LAO builders. Only *how a
role's source frame is found* differs:

| | Workbook workflow | Reference-files workflow |
|---|---|---|
| Entry point | `run_mapping(workbook_path, cfg)` | `run_mapping_from_reference_files(file_paths, cfg)` |
| Source read | `profiling.read_workbook` (all sheets) | `profiling.read_reference_csv` (one file each) |
| Role matched against | sheet/tab name | file basename |
| Matched via | `sheet_config[].match_patterns` | `sheet_config[].filename_patterns` |
| Matcher | `profiling.match_sheets` | `profiling.match_reference_files` |

The Measurement-Points → LTP join-key bonus (functional-location overlap scoring) and
prompt-variant selection (`prompts/appendix.*.md`) both still apply automatically —
they only need a resolved `role_to_sheet`, however it was resolved.
