# FMG Schema-Mapping Agent

A Python AI agent that runs **row normalization** and **AI-assisted schema mapping**
against either a single customer workbook or — the **TRUE workflow**, §14 — several
workbooks merged into one combined run, and produces **NEO**, **LAO**, **Normalized**,
`NEO_Exceptions`/`LAO_Exceptions` (§10), **ExcludedComponents** (§12), and
**DuplicateDates** (§13) CSVs, every NEO/LAO/companion row carrying a per-row
**ConfidenceScore** (§11). It uses an **OpenAI model**, is **callable by Azure Logic
Apps** over HTTP, and is **fully customer-configurable through JSON** (a new customer is
onboarded by adding one config file — no code change).

Enrichment fields (the AMT / Cross-Reference / IK17 join columns, i.e. the `_y` columns
in `NEO.py` / `LAO.py`) are left **blank and labelled `requires_enrichment`** — never
invented — with one deliberate exception: `SerialNumber`, `ComponentCode`,
`ModifierCode`, and (on `LAO`) `AssetName` — the fields a downstream Snowflake key is
built from — are now populated where the data genuinely supports it, from two real
sources only: the customer file itself (when a workbook shape carries these columns
directly) and a deterministic join against the AMT lookup tables (`fmg_cross-reference.csv`
etc. — a Blob container when configured, §15, else the bundled
`prompts/cross-references/*.csv`; never the LLM, never a guess). See
`prompts/README.md` § "AMT cross-reference enrichment" for exactly how the join itself
works, `core/cross_reference.py` for the per-workbook-shape join logic, and §15 below
for where the lookup tables themselves are sourced from. No other Snowflake logic is
included, and every other enrichment field stays blank exactly as before.

---

## 1. What it does

```
ONE customer .xlsx                    SEVERAL customer .xlsx workbooks (§14, "workbooks")
      │                                               │
      │                          [0] resolve EACH workbook independently (its own
      │                              sheet detection — exactly step [1] below, just
      │                              run once per file), then concatenate the SAME
      │                              role's rows across all of them
      │                              (core/mapping_engine.run_mapping_from_multiple_workbooks)
      │                                               │
      └───────────────────────────┬───────────────────┘
                                   ▼
[1] detect sheets      ← flexible sheet_config (LTP→NEO, Measurement Points→LAO)
[2] profile columns    ← header + sampled values + dtype/format evidence
[3] score mapping      ← deterministic scorer (reproducible confidence per column)
[4] LLM refine         ← OpenAI reviews/repairs ambiguous columns + writes notes
[5] normalize rows     ← keyless / identity-missing / banner / gibberish → Normalized.csv
[6] build outputs      ← NEO.csv + LAO.csv (constants + transforms; enrichment blank)
[7] AMT cross-reference ← fills SerialNumber/ComponentCode/ModifierCode blanks where the
                          customer file itself doesn't carry them (core/cross_reference.py)
[8] row confidence     ← per-row ConfidenceScore column, appended to every NEO/LAO row
                          (core/row_confidence.py) — distinct from the per-column numbers
[9] business exclusion ← removes rows whose StrategyTaskDescription matches a configured
                          keyword (core/exclusions.py) → ExcludedComponents.csv
[10] blank-pair removal ← removes rows with BOTH ComponentCode and ModifierCode blank
                          (core/exceptions.py's split_blank_pair) → NEO_Exceptions.csv /
                          LAO_Exceptions.csv (_removed_from_output=True)
[11] keep-latest-date  ← for rows sharing the same task (group_by), removes every row
     deduplication       except the one with the latest date_field value
                          (core/deduplication.py) → DuplicateDates.csv
[12] exception audit   ← flags REMAINING rows still missing a critical field, or
                          gibberish, without removing them (core/exceptions.py's
                          find_exceptions) → same NEO_Exceptions.csv / LAO_Exceptions.csv
                          (_removed_from_output=False)
      │
      ▼
mapping_report (JSON, confidence per column AND per row) + 7 CSVs
```

The scoring rubric and the prompt are the ones created earlier in this project and live
in `prompts/schema_mapping_prompt.md`. The agent loads that file at runtime.

## 2. Project layout

```
fmg_agent/
├─ function_app.py            # Azure Functions HTTP trigger (Logic Apps calls this)
├─ agent.py                   # run_agent(request) -> response  (transport-agnostic core)
├─ run_local.py               # CLI runner (same core, for testing / non-Azure use)
├─ prompts/
│   └─ schema_mapping_prompt.md   # the project prompt (system + user)
├─ config/
│   └─ customers/
│       ├─ default.json       # ← the flexible bit: sheets, fields, constants, scoring
│       └─ _TEMPLATE.json     # copy this to onboard a new customer
├─ core/
│   ├─ settings.py            # env vars + customer-config loader
│   ├─ profiling.py           # sheet matching + column profiling
│   ├─ scorer.py              # deterministic confidence + greedy 1:1 assignment
│   ├─ llm_mapper.py          # OpenAI Responses API call (structured output)
│   ├─ normalizer.py          # row quarantine (Normalized.csv)
│   ├─ transforms.py          # named transforms (only NEO.py/LAO.py logic)
│   ├─ builders.py            # build NEO/LAO frames from the resolved mapping
│   ├─ cross_reference.py     # AMT lookup enrichment (SerialNumber/ComponentCode/ModifierCode)
│   ├─ exceptions.py          # post-build audit + blank-pair removal (NEO/LAO_Exceptions.csv, §10)
│   ├─ exclusions.py          # business-rule row filter (ExcludedComponents.csv, §12)
│   ├─ deduplication.py       # keep-latest-date filter (DuplicateDates.csv, §13)
│   ├─ row_confidence.py      # per-row ConfidenceScore column (§11)
│   └─ mapping_engine.py      # orchestrates the steps above, incl. run_mapping_from_multiple_workbooks (§14)
├─ tests/test_smoke.py
├─ requirements.txt
├─ host.json                  # Azure Functions host config
└─ local.settings.json.example
```

## 3. Flexibility / placeholders (as requested)

Everything customer-specific is data, not code:

| To change… | Edit… | No code change? |
|---|---|---|
| Which sheets feed which output | `sheet_config[]` in the customer JSON | ✅ |
| Column aliases / dtypes per customer | `targets.*.fields[]` | ✅ |
| The fixed NEO/LAO output columns | `targets.*.output_columns[]` | ✅ |
| Constants (RegistrationCounter, UOM…) | `constants{}` | ✅ |
| Scoring weights / bands / name gate | `scoring{}` | ✅ |
| Reject rules for normalization | `normalization.reject_rules[]` | ✅ |
| Which fields trip a post-build exception | `exceptions.critical_fields[]` / `exceptions.gibberish_fields[]` | ✅ |
| Which rows get excluded from NEO/LAO entirely | `exclusions.field` / `exclusions.keywords[]` | ✅ |
| Keep-latest-date dedup grouping/date field, per target | `deduplication.<target>.group_by[]` / `.date_field` | ✅ |
| Add a brand-new customer | copy `_TEMPLATE.json` → `<id>.json` | ✅ |
| Per-call sheet override | `sheet_config_override` in the request body | ✅ |
| Storage target (local ↔ Azure Blob) | `STORAGE_BACKEND` env var | ✅ |
| Cross-reference lookup source (bundled files ↔ Blob) | `CROSS_REFERENCE_BLOB_CONTAINER` env var, §15 | ✅ |

`source_class` on each field controls behaviour: `customer_file` (AI-mapped),
`constant` (from `constants`), `derived` (named transform), `enrichment` (left blank).

## 4. Request / response contract (what Logic Apps sends & receives)

**Request** (`POST` body):
```json
{
  "customer_id": "default",
  "input": { "path": "/data/CB_MM_LTP_AUGUST.xlsx" },
  "output_dir": "/data/out",
  "sheet_config_override": null,
  "return_inline": false
}
```
`input` may instead be `{ "content_base64": "...", "filename": "book.xlsx" }` so Logic
Apps can post file bytes directly, or a plain string — `"input": "fmg-inbound/book.xlsx"`
— as shorthand for `{ "path": "fmg-inbound/book.xlsx" }` (a bare blob path or full blob
URL; see § "Passing Blob paths instead of inline bytes" below).

**Or**, the TRUE/merge workflow (§14) — several workbooks combined into one NEO/LAO —
replaces `"input"` with `"workbooks"`, a list in the same accepted shapes:
```json
{
  "customer_id": "default",
  "workbooks": [
    { "path": "/data/site-A.xlsx" },
    { "path": "/data/site-B.xlsx" },
    { "content_base64": "...", "filename": "site-C.xlsx" }
  ],
  "output_dir": "/data/out"
}
```
Every entry must resolve to an Excel workbook (`fileType`/extension `xlsx`/`xls`/etc.) —
`"workbooks"` rejects a CSV with a clear error telling you to use `"reference_files"`
instead, rather than silently mishandling it.

**Response** (real numbers from a verified `CB MM LTP AUGUST.xlsx` run):
```json
{
  "status": "succeeded",
  "run_id": "a2c969e26557",
  "customer_id": "default",
  "mapping_report": {
    "matched_sheets": [...],
    "mappings": { "NEO": [ {"canonical_field","source_column","confidence","band","status","notes"} ], "LAO": [...] },
    "column_confidence_summary": {
      "NEO_mean": 0.674, "LAO_mean": 0.479,
      "NEO_row_confidence_mean": 0.651, "LAO_row_confidence_mean": 0.678,
      "NEO_Exceptions_row_confidence_mean": 0.572, "LAO_Exceptions_row_confidence_mean": 0.555
    },
    "row_counts": {
      "NEO": 2161, "LAO": 2846, "Normalized": 1403,
      "NEO_ExcludedComponents": 2746, "LAO_ExcludedComponents": 4667, "ExcludedComponents": 7413,
      "NEO_DuplicateDates": 393, "LAO_DuplicateDates": 0, "DuplicateDates": 393,
      "NEO_Exceptions": 6157, "LAO_Exceptions": 18226
    },
    "exceptions_by_reason": {
      "NEO": {"missing ComponentCode and ModifierCode (removed from output)": 3996, "missing SerialNumber": 2161},
      "LAO": {"missing ComponentCode and ModifierCode (removed from output)": 15380, "missing SerialNumber": 2846}
    },
    "excluded_by_keyword": { "NEO": {"Hose": 1838, "Cooling": 824}, "LAO": {"Fuel": 2513} },
    "warnings": [], "llm_used": true
  },
  "outputs": {
    "NEO": "<local path|container/blob>", "LAO": "...", "Normalized": "...",
    "NEO_Exceptions": "...", "LAO_Exceptions": "...",
    "ExcludedComponents": "...", "DuplicateDates": "..."
  }
}
```
`NEO_Exceptions`/`LAO_Exceptions` combine **two severities**, distinguished by a
`_removed_from_output` column: rows still missing a critical field
(`AssetName`/`SerialNumber`/`ComponentCode`/`ModifierCode` by default — configurable via
`exceptions.critical_fields`/`exceptions.gibberish_fields`) are a **post-build audit,
not a quarantine** — flagged with `_exception_reason`, `_removed_from_output=False`, and
left in `NEO.csv`/`LAO.csv` unchanged; rows where `ComponentCode` **and** `ModifierCode`
are **both** blank carry no usable component identity at all and are actually **removed**
(`_removed_from_output=True`). `exceptions_by_reason` only appears per target when that
target actually has at least one flagged-or-removed row. See `core/exceptions.py`.
`ExcludedComponents`/`DuplicateDates` (§12/§13) are separate, additional removal
mechanisms — a row lands in at most one of the three. This response shape is identical
regardless of whether the request used `"input"`, `"workbooks"` (§14), or
`"reference_files"` — only `mapping_report.matched_sheets[].sheet_name` looks different
for a `"workbooks"` call: it reads e.g. `"LTP (merged from 3/3 workbooks)"` instead of a
single tab name, telling you how many of the posted files actually contributed to that
role without needing to cross-reference `warnings`.
With `STORAGE_BACKEND=azure_blob`, each `outputs` value is a bare `"<container>/<blob_name>"`
path (e.g. `"fmg-outbound/a2c969e26557/NEO.csv"`) — same shorthand form accepted for
`input`/`reference_files` — resolved against the same storage account as the request
(`AZURE_STORAGE_CONNECTION_STRING` / `AZURE_STORAGE_ACCOUNT_URL`), not a full URL.

## 5. Calling it from Azure Logic Apps

1. Deploy this folder as a **Python Azure Function** (v2 model — `function_app.py` is the
   entrypoint). Set the app settings from `local.settings.json.example`.
2. In your Logic App add an **HTTP** action:
   - Method `POST`
   - URI `https://<funcapp>.azurewebsites.net/api/schema-map?code=<function key>`
   - Body = the request contract above.
3. Branch on the response: e.g. a **Condition** on
   `mapping_report.column_confidence_summary.NEO_mean` or on any column whose `band` is
   `low_review`, to route runs for human review before the downstream enrichment/load.
4. A `GET /api/health` route is provided for availability probes.

The Function is a thin wrapper — all logic is in `agent.run_agent`, so you can also call
the same core from a Container App, Durable Function, or queue trigger without changes.

### Passing Blob paths instead of inline bytes

Set `STORAGE_BACKEND=azure_blob` and one of `AZURE_STORAGE_CONNECTION_STRING` /
`AZURE_STORAGE_ACCOUNT_URL` (see below), and `"path"` in `input`/`reference_files` can be
an actual Blob location instead of `content_base64` — `core/storage.AzureBlobStorage`
downloads it before processing and uploads NEO/LAO/Normalized back to Blob, returning
each as a bare `"<container>/<blob_name>"` path in `outputs`. Three shapes are accepted
for the *input* path, in order of how directly they name a blob:

1. A full blob URL with a SAS token already embedded — used as-is, no extra auth (what
   most Logic Apps blob connector / "Create SAS URI" actions hand you).
2. A full blob URL with no SAS — authenticated via `AZURE_STORAGE_CONNECTION_STRING` if
   set, else via Entra ID (`DefaultAzureCredential`: managed identity in Azure, `az
   login` locally) against that URL's own account.
3. A bare `"<container>/<blob_name>"` path — resolved via `AZURE_STORAGE_CONNECTION_STRING`
   if set, else via Entra ID against `AZURE_STORAGE_ACCOUNT_URL`.

```json
{ "customer_id": "default", "reference_files": [
    { "path": "https://<account>.blob.core.windows.net/<container>/LTP.csv?sv=...&sig=..." }
] }
```

`content_base64` still works unchanged for either backend — the shapes aren't mutually
exclusive per request.

**Auth:** set exactly one of:
- `AZURE_STORAGE_CONNECTION_STRING` — account-key connection string.
- `AZURE_STORAGE_ACCOUNT_URL` — e.g. `https://<account>.blob.core.windows.net`, used
  with Entra ID (`DefaultAzureCredential`). Required for bare `"<container>/<blob_name>"`
  paths and for uploading outputs when no connection string is set.

**Output container:** outputs are written to the storage account above, in a container
derived from the *input* blob's own container — `inbound` is swapped for `outbound`
(e.g. an input from `fmg-inbound` writes outputs to `fmg-outbound`). `OUTPUT_CONTAINER`
is only the fallback used when the input container doesn't follow that naming (or the
input arrived as `content_base64`, which names no container).

**Cross-reference lookup tables** (the AMT join CSVs, §1) are a *separate* Blob concern
from the input/output files above — see §15 — controlled by its own
`CROSS_REFERENCE_BLOB_CONTAINER` env var, independent of `STORAGE_BACKEND`. It reuses
the same `AZURE_STORAGE_CONNECTION_STRING`/`AZURE_STORAGE_ACCOUNT_URL` auth as this
section, so no separate credential to configure if you're already using Blob storage
for input/output.

## 5b. Deploying to Microsoft Foundry (Hosted Agents)

The same core deploys to **Foundry Agent Service → Hosted Agents** ("bring your own
code"). Nothing in `core/` or `agent.py` changes — only the entrypoint and (optionally)
the model gateway.

Added for this target:

| File | Purpose |
|---|---|
| `foundry_app.py` | Responses-protocol HTTP host wrapping `run_agent` (Foundry counterpart of `function_app.py`). |
| `Dockerfile` | linux/amd64 image (Foundry requires x86_64), Python 3.13, runs `python foundry_app.py`. |
| `requirements-foundry.txt` | base deps + `azure-ai-agentserver-responses` + `azure-identity`/`azure-ai-projects`. |
| `azure.yaml` | azd project file; `azd ai agent init` augments it with the Foundry agent definition. |
| `.env.foundry.example` | env vars for the gateway swap + Blob output. |
| `.dockerignore` | keeps build context clean. |

**Protocol.** `foundry_app.py` hosts the OpenAI-compatible **Responses** protocol
(`POST /responses`) via the `azure-ai-agentserver-responses` SDK, not the raw
Invocations protocol — so any OpenAI Responses-compatible client can call it, and the
platform manages session lifecycle for you. This agent is single-turn (one file in, one
report out), so it doesn't use conversation history: the caller's message text *is* the
`run_agent` request contract, JSON-encoded, and the reply text *is* the `run_agent`
response contract, JSON-encoded —

```python
resp = client.responses.create(model="<agent-name>", input=json.dumps({
    "customer_id": "default",
    "input": {"path": "/data/CB_MM_LTP_AUGUST.xlsx"},
}))
result = json.loads(resp.output_text)
```

`GET /readiness` (SDK built-in) and `GET /health` (kept for continuity with existing
probes) are both available for availability checks.

**Model gateway swap.** Set `MODEL_PROVIDER=foundry` and `FOUNDRY_PROJECT_ENDPOINT`.
`core/llm_mapper.py` then builds the client via
`AIProjectClient(endpoint=..., credential=DefaultAzureCredential()).get_openai_client()`
— models come from the Foundry catalog and auth is Entra ID (no API key). The rest of
the LLM code is unchanged. `OPENAI_MODEL` must match your Foundry **model deployment
name**. With `MODEL_PROVIDER=openai` (the default) it still calls OpenAI directly for the
Azure Functions path.

**Deploy (recommended — azd):**
```bash
az login
azd ai agent init     # scaffolds/updates the Foundry agent definition + RBAC
azd up                # source-ZIP or container build, deploy, wire the endpoint
```
Python hosted agents default to **source-ZIP** deployment (no Docker needed); the
`Dockerfile` is there if you prefer a container image.

**Deploy (container, manual):**
```bash
docker build --platform linux/amd64 -t <acr>.azurecr.io/fmg-agent:latest .
docker push <acr>.azurecr.io/fmg-agent:latest
# register the image as a Hosted Agent via azd / Python SDK / REST
```

**Logic Apps calls it** at the stable hosted-agent Responses endpoint
`https://{project_endpoint}/agents/{agent_name}/endpoint/protocols/openai/responses`
(Entra auth), posting `{"model": "...", "input": "<run_agent request JSON>"}` and
reading the request contract's response back out of `output_text` — an HTTP action
with an OpenAI-shaped body works, or any OpenAI Responses-compatible SDK. You gain
agent versioning, built-in observability, and the content-safety layer. Set
`STORAGE_BACKEND=azure_blob` so outputs land in Blob for the downstream Logic Apps
actions.

> Two version-sensitive notes: confirm the exact **Responses endpoint path** and the
> **azd agent host** against the current Foundry quickstart (the SDK is evolving), and
> note the Foundry RBAC roles were recently renamed (Foundry User/Owner/Project Manager,
> formerly Azure AI …).

## 6. Model & settings

- Default model **`gpt-5.6-terra`** (the balanced tier recommended as the OpenAI
  equivalent of Claude Sonnet for this task), `reasoning.effort=low`, structured JSON
  output via the Responses API. Escalation model **`gpt-5.6-sol`** for ambiguous columns.
- All set via env vars (`OPENAI_MODEL`, `OPENAI_ESCALATION_MODEL`,
  `OPENAI_REASONING_EFFORT`, `USE_LLM`).
- **Hybrid by design:** the confidence numbers are computed deterministically (so they
  are reproducible); the LLM only confirms/repairs alias choices and writes notes. If
  `USE_LLM=false` or no API key is set, the agent runs the deterministic path and still
  produces all outputs — which is how the smoke test runs offline.

## 7. Run locally

```bash
pip install -r requirements.txt

# deterministic only (no key needed)
USE_LLM=false python run_local.py --input CB_MM_LTP_AUGUST.xlsx --customer default --out ./_out

#Windows
python run_local.py  --input ".\test-data\CB MM LTP AUGUST.xlsx" --customer default --out ".\_out"
python run_local.py  --input ".\test-data\New Workfile Rio Tinto Aug 2026.xlsx" --customer default --out ".\_out"
python run_local.py  --input ".\test-data\[COMBINATION OF ALL FILES] -  251113 Westrac consumption file_WORKFILE November (USE THIS FILE!).xlsx" --customer default --out ".\_out"

# with the LLM refinement layer
export OPENAI_API_KEY=sk-...
python run_local.py --input CB_MM_LTP_AUGUST.xlsx --customer default --out ./_out

# smoke test
USE_LLM=false python tests/test_smoke.py CB_MM_LTP_AUGUST.xlsx
```

Verified run on `CB_MM_LTP_AUGUST.xlsx` (deterministic-only, current code — every rule
in §10-§13 applied): **NEO 9,296 rows built → 2,746 excluded (§12) → 3,996 blank-pair
removed (§10) → 393 older duplicates removed (§13) → 2,161 delivered; LAO 22,893 rows
built → 4,667 excluded → 15,380 blank-pair removed → 0 duplicates (no LAO date field
configured) → 2,846 delivered; Normalized 1,403 rows**. NEO/LAO headers match
`FMG_NEO_Aug_26.csv` / `FMG_LAO_Aug_26.csv` for every original column, plus one trailing
`ConfidenceScore` column (§11) neither reference file has. Customer-file confidence means
**NEO 0.674 / LAO 0.479** (column-level, computed before any row removal so it isn't
affected by which rows ship — see `prompts/README.md` § "AMT cross-reference
enrichment"). Row-level **`NEO_row_confidence_mean` 0.651 / `LAO_row_confidence_mean`
0.678** (§11, computed after every removal above, so it reflects exactly what ships).
The AMT cross-reference join still recovers `ComponentCode`/`ModifierCode` for
2,637/9,296 NEO rows from `fmg_cross-reference.csv` despite the customer file not
carrying them at all.

**The TRUE/merge workflow — several Excel workbooks combined into one NEO/LAO** (§14):
see **[core/mapping_engine.py](core/mapping_engine.py)**'s
`run_mapping_from_multiple_workbooks`. Verified by merging `CB MM LTP AUGUST.xlsx` with
itself: NEO/LAO row counts came out exactly double the single-workbook numbers above
(4,322 / 5,692) — confirming the merge, and every filtering rule downstream of it,
behaves correctly on combined data.

```bash
python run_local.py --workbooks ./test-data/site1.xlsx ./test-data/site2.xlsx \
    --customer default --out ./_out
```

**Additional workflow — standalone reference CSVs instead of one workbook** (an LTP
export and a Measurement-Points export delivered as their own files, e.g. from Blob
storage, rather than as tabs in one workbook): see **[REFERENCE_FILES.md](REFERENCE_FILES.md)**.

```bash
python run_local.py --reference-files ./test-data/LTP.csv ./test-data/Measurement-Points.csv \
    --customer default --out ./_out
```

## 8. Onboarding a new customer (no code)

1. `cp config/customers/_TEMPLATE.json config/customers/acme.json`
2. Set `sheet_config` patterns to match Acme's tab names; edit each field's `aliases`
   to Acme's headers; keep `output_columns` fixed to the NEO/LAO schema.
3. Call the agent with `"customer_id": "acme"`.

## 9. Deliberate non-goals

- **No new *kind* of workflow beyond normalization + schema mapping** — the post-build
  steps below (§10, §12, §13) are review/reporting/filtering steps on the same NEO/LAO
  outputs those two already produce, not a new pipeline stage type; the merge workflow
  (§14) is a new way to feed workbooks IN (several instead of one), but everything
  downstream of role resolution is the exact same normalize/schema-map pipeline,
  unmodified — no separate code path to keep in sync.
- **No invented columns/values** — enrichment stays blank and labelled, except the AMT
  key fields (`SerialNumber`/`ComponentCode`/`ModifierCode`/LAO `AssetName`), which are
  populated only from the customer file itself or a deterministic AMT cross-reference
  join — never guessed, never LLM-sourced. See § 1 above and `prompts/README.md`.
- **No Snowflake / warehouse** — file (or blob) output only; the AMT join above produces
  the key fields Snowflake needs, but loading/writing to Snowflake itself is still your
  later integration step.
- **No silent row removal, and no unlabeled one** — a row leaves `NEO.csv`/`LAO.csv` in
  exactly four places, every one of them writing the removed row into a companion CSV
  with a reason attached, never just dropping it:
  1. `core/normalizer.py`'s pre-build quarantine (`Normalized.csv`/`Exceptions.csv`) — a
     *source* row rejected before NEO/LAO exist: missing key/identity, a banner row,
     gibberish.
  2. `core/exclusions.py`'s business-rule keyword filter (`ExcludedComponents.csv`,
     §12) — a fully-built, well-mapped row removed on *content*, not quality, grounds.
  3. `core/exceptions.py`'s `split_blank_pair` (into `NEO_Exceptions.csv`/
     `LAO_Exceptions.csv`, §10, `_removed_from_output=True`) — `ComponentCode` **and**
     `ModifierCode` both blank, so there's no usable component identity to keep.
  4. `core/deduplication.py`'s keep-latest-date filter (`DuplicateDates.csv`, §13) — an
     older revision of a row another, later-dated row already represents.
  Everything else that flags a row (the rest of the exception audit, §10) leaves it in
  place — flagging alone is never removal.

## 10. Post-build exceptions (NEO_Exceptions.csv / LAO_Exceptions.csv)

Separate from row normalization (§1 step 5, which decides if a *source* row is
admissible at all, before NEO/LAO exist) — both of the checks below run on the
**final, already-built** NEO/LAO rows, after AMT cross-reference enrichment, and land
in the same companion file, distinguished by a `_removed_from_output` column:

- **Audit only** (`_removed_from_output=False`, `core/exceptions.py`'s
  `find_exceptions`): a row still missing a critical field, or holding gibberish in
  one, is flagged with `_exception_reason` but **left in `NEO.csv`/`LAO.csv`
  unchanged** — a companion review view, not a quarantine. Default critical fields:
  `AssetName`, `SerialNumber`, `ComponentCode`, `ModifierCode` — the same identity +
  AMT-key fields the downstream Snowflake key is built from (§1). Configurable via
  `exceptions.critical_fields`/`exceptions.gibberish_fields` in `config/customers/*.json`.
- **Actual removal** (`_removed_from_output=True`, `core/exceptions.py`'s
  `split_blank_pair`): a row where `ComponentCode` **and** `ModifierCode` are **both**
  blank carries no usable component identity at all — not a "one field is thin" case,
  a "there is no component here" one — so it's genuinely taken out of
  `NEO.csv`/`LAO.csv` and diverted here. This check is hardcoded to that specific field
  pair (it's the AMT-key pair, not an arbitrary configurable list) and runs *before*
  the audit above and the deduplication in §13, so a row can never be flagged AND
  removed, and a blank-component row never distorts a dedup group.

A workbook shape that genuinely lacks a critical field for every row (e.g.
`CB MM LTP AUGUST` has no serial number anywhere — see `prompts/README.md`) will
correctly show up as 100% flagged (not removed — `SerialNumber` alone being blank is
still just an audit finding): that's an honest reflection of a real, permanent data
gap for that shape, not a bug.

See `core/exceptions.py` for the implementation and why the audit's critical-field list
is kept deliberately short (most other fields are legitimately blank sometimes by this
project's own design — flagging every blank cell would bury the real exceptions).

## 11. Per-row confidence score (`ConfidenceScore` column)

Every row of `NEO.csv`, `LAO.csv`, their `_Exceptions` companions, and
`ExcludedComponents.csv` (§12) carries a trailing `ConfidenceScore` column — a
**different metric** from everything else in
`mapping_report`. The existing `mappings[<target>][*].confidence` and
`column_confidence_summary`'s `NEO_mean`/`LAO_mean` are per-**column** numbers ("how sure
are we this source column is the right one for this canonical field?") — the same value
for every row that uses that column. `ConfidenceScore` is per-**row**: for one specific
built row, how much of what's actually printed in it rests on solid evidence? A column
mapped at 0.95 confidence can still have a genuinely blank cell on some individual rows
(that row's own source cell was empty) — that row gets no credit for a field it doesn't
actually have data for.

Only fields the pipeline actually attempts for a given customer shape count toward it:
a `customer_file` field's own column-mapping confidence; `constant`/`derived` fields at
a flat 1.0 (not a guess); an `enrichment` field only once the AMT cross-reference pass
actually fills it for at least one row of that shape (at 0.95 — an exact deterministic
join, more reliable than a rejected column-alias guess), so it then varies genuinely
row-to-row. A field this pipeline has **no** mechanism to ever fill for this customer
(e.g. `BranchCode`/`SiteCode` — blank for every customer by this project's original
design) is excluded from the score entirely, not zeroed — it's out of scope by design,
not a reflection of any one row's quality, so it shouldn't drag every single row down by
the same fixed amount. See `core/row_confidence.py` for the full reasoning and
`mapping_report.column_confidence_summary`'s `{target}_row_confidence_mean` /
`{target}_Exceptions_row_confidence_mean` for the per-file means (§4).

## 12. Business-rule row exclusion (ExcludedComponents.csv)

A row can be complete, well-mapped, and high-confidence, and still be removed from
`NEO.csv`/`LAO.csv` entirely — because of what it's *about*, not any data-quality issue.
`core/exclusions.py` checks a configured field (`StrategyTaskDescription` by default)
against a configured list of keywords (`exclusions.keywords` in
`config/customers/*.json`); a case-insensitive substring match on any keyword removes
that row from the target output and writes it instead to one combined
`ExcludedComponents.csv` (tagged with a `_source_target` column — `"NEO"`/`"LAO"` — and
`_matched_keyword`, the first keyword that matched).

The `default` customer profile's keyword list today:
```
Hose, Valve Set, Harness, Service Suspension, Slew Bearing Inspection, Coolant Engine,
Cooling, Insp, Safety, Turbo, clean, Operators Seat, Fuel, Cabin, Battery, Batteries,
Air Cond, Aircond, Seat Belt, High Risk Hose, Harnesss Service, Ladder
```
(Matching is case-insensitive, so the originally-supplied `clean`/`Clean` pair collapses
to one entry — no behavior lost, just no redundant work. `Harnesss Service` keeps the
triple-`s` spelling exactly as given: a keyword is a literal string this business rule
was defined with, not something this pipeline should silently "correct".)

Runs **after** AMT cross-reference enrichment and the `ConfidenceScore` column (§11) are
both in place — an excluded row keeps its `ConfidenceScore` in `ExcludedComponents.csv`,
so that file stays a complete, self-contained record of exactly what would have shipped.
Runs **before** the post-build exception audit (§10), so an excluded row is never also
flagged as an exception — the two companion files are mutually exclusive. Verified
against real workbooks: FMG's `CB MM LTP AUGUST` loses 2,746/9,296 NEO rows and
4,667/22,893 LAO rows this way (`Hose`, `Cooling`, and `Cabin` are the top hits); Rio
Tinto's workbook loses 1,195/12,376 NEO rows and 9,314/59,526 LAO rows (`Cooling`,
`Fuel`, and `Turbo` top there) — confirming the filter behaves sensibly across
differently-shaped source data, not just the one workbook it was written against.

`ExcludedComponents.csv` is always written once at least one target was built, even if
empty (0 rows, but with real column headers — see `core/mapping_engine.py`'s
`_combine_or_empty`) — same convention as `Normalized.csv`/`Exceptions.csv`. Set
`exclusions.keywords` to `[]` (the `_TEMPLATE.json` default) to disable this entirely for
a customer that doesn't want it — it's an explicit business decision per customer, not a
default every workbook shape inherits.

## 13. Keep-latest-date deduplication (DuplicateDates.csv)

Some rows represent revisions of the *same* task, planned on different dates — a
stale copy should not ship in `NEO.csv`/`LAO.csv` alongside its replacement.
`core/deduplication.py` groups a target's rows by a configured `group_by` set of
canonical fields and, within each group, keeps only the row(s) with the latest
`date_field` value — everything strictly older is removed to one combined
`DuplicateDates.csv` (tagged `_source_target`, same convention as
`ExcludedComponents.csv`). Configured per target via `deduplication.<target>` in the
customer JSON; a target with no entry (or a `date_field` that isn't a real, populated
column for that shape) is a deliberate no-op.

The `default` customer profile today: `NEO` groups by `AssetName` + `ComponentCode` +
`ModifierCode` + `TaskCounterCode` (the same asset, component, and task-counter slot —
`TaskCounterCode` is documented as "a recurring per-component counter", i.e. repeated
rows across different planned dates are revisions of the same task) and compares on
`NewStrategyDate` (the field that actually holds a "planned start" value — see
`appendix.fmg.md`). There is deliberately **no `LAO` entry**: this project's LAO schema
has no field that's genuinely populated with a start-date-equivalent value today (its
only date fields, `StrategyDate`/`LastStrategyDate`, are enrichment/constant
placeholders that are always blank) — wiring the rule up anyway would just never fire,
so it's left out rather than configured to silently do nothing.

Two things are deliberately NOT removed, straight from "retain the row**s** with latest
StartDate" read literally:
- A row with a blank/unparseable `date_field` — there's no date to compare, so this
  rule has no basis to call it stale.
- A genuine tie for the latest date within a group — more than one row can legitimately
  BE the latest.

Runs **after** the both-blank-ComponentCode/ModifierCode removal (§10) — so a row with
no real component identity never distorts a dedup group by being lumped into one — and
**before** the exception audit, so a removed duplicate is never also flagged. Verified
against `CB MM LTP AUGUST.xlsx`: 393 older-dated NEO rows removed this way (e.g. one
`GR018`/`1000`/`00`/`1` row dated `20260626` removed in favor of the surviving row for
that same group dated `20281219`); confirmed across the *entire* output that no
remaining group retains more than one distinct `NewStrategyDate` value. Rio Tinto's
workbook removes 0 (as expected — its `NewStrategyDate` is genuinely blank on every
row for that shape, so nothing is comparable), which is the no-op case working
correctly, not a gap in the rule.

## 14. The TRUE workflow: merging multiple workbooks (`"workbooks"`, DuplicateDates-scale data)

Everything above (§1-§13) describes the **single-workbook** workflow: one `.xlsx` in,
one NEO/LAO out. The workflow this project is actually meant to run as — multiple
customer workbooks (per site, per month, however the customer's export splits them)
**merged into one combined NEO/LAO** — is request-compatible with it: pass
`"workbooks": [...]` instead of `"input"` (§4), and every rule in §5-§13 (scoring, LLM
refinement, normalization, AMT cross-reference, `ConfidenceScore`, business exclusion,
blank-pair removal, deduplication, the exception audit) applies completely unchanged,
because it all runs downstream of `core/mapping_engine._assemble_outputs`, which this
new path feeds into via the exact same call the single-workbook path already used. The
single-workbook workflow is untouched and stays the default — `"workbooks"` is
additive, not a replacement.

**How it merges.** `core/mapping_engine.run_mapping_from_multiple_workbooks`:
1. Resolves each posted workbook **independently** — its own sheet detection and
   content-rescue (`_resolve_workbook_roles`, factored out of the single-workbook
   `run_mapping` so both paths share one implementation, not two that could drift
   apart). One workbook can have differently-named tabs than another and both still
   resolve correctly on their own terms.
2. For each role (`LTP`, `MEASUREMENT_POINTS`), concatenates every workbook's resolved
   DataFrame for that role into one combined frame. A role only some workbooks carry
   isn't an error — those rows simply come from the workbooks that had it.
3. Hands the merged `{role: combined DataFrame}` pool to `_assemble_outputs` exactly
   once — the shared pipeline runs a single time over ALL the data, not once per
   workbook, so cross-reference joins, deduplication groups, etc. see the full merged
   picture rather than being computed per-file and then somehow reconciled.

Every merged row carries an internal `_source_workbook` tag (the originating file's
basename) for traceability while it moves through the pipeline. It's never part of the
canonical NEO/LAO schema and never reaches any output CSV — `core/profiling.py`'s
`profile_columns` skips any `"_"`-prefixed column outright (the same convention this
project already used for `_source_sheet`/`_reject_reason`/`_matched_keyword`/
`_exception_reason`), so it can't even be mistaken for a real candidate source column
by the scorer or the LLM.

**More rows, by design.** Since this is a union across every posted workbook, row
counts scale with however many you post — the explicit expectation for this workflow.
Verified by merging `CB MM LTP AUGUST.xlsx` with itself as a stand-in for two real
workbooks: `mapping_report.matched_sheets` showed `"LTP (merged from 2/2 workbooks)"`
with 21,398 raw rows — exactly double the single workbook's own raw `CB MM LTP` sheet
(10,699 rows before normalization/quarantine ever runs; NOT the same number as NEO's
final 9,296, which is *after* 1,403 rows are quarantined) — and, more to the point,
after every §10-§13 rule ran on the merged data, the delivered NEO/LAO counts came out
at exactly double the single-workbook numbers too (4,322 / 5,692) — confirming the
merge and every downstream filtering rule compose correctly end to end on combined
data, not just that nothing crashed partway through.

**Request contract addition** (§4 has the full shape): `"workbooks"` is a list, each
entry accepting the same ref shapes as `"input"` — a bare blob path/URL string,
`{"path": ...}`, or `{"content_base64": ..., "filename": ...}`. Every entry must resolve
to an Excel workbook; posting a CSV under `"workbooks"` is rejected with an error
pointing you at `"reference_files"` instead, rather than silently mishandled. Local CLI:
`python run_local.py --workbooks file1.xlsx file2.xlsx ... --customer default --out ./_out`.

## 15. Cross-reference source: Blob storage first, bundled files as fallback

The AMT cross-reference lookup CSVs (`fmg_cross-reference.csv`,
`rio-tinto_cross-reference.csv`, `bhp_cross-reference.csv` — §1, `prompts/README.md`)
used to be read only from `prompts/cross-references/`, bundled with the code — updating
one meant editing the repo and redeploying. Set `CROSS_REFERENCE_BLOB_CONTAINER` to a
Blob container name and `core/cross_reference.py` tries that container **first** for
every lookup file; the bundled `prompts/cross-references/` copy is now the **fallback**,
used automatically when the env var is unset (the default, zero-config case — behaves
exactly as before) or when fetching a specific file from the container fails for any
reason (not found, auth, network) — never a hard failure just because Blob had a bad
moment, unless the bundled fallback copy is *also* missing, which correctly still fails
loudly (there's genuinely nowhere left to get the data from).

**Which file, for which input.** This doesn't add a second file-selection mechanism —
it reuses the one that already exists: a workbook's `prompt_variant` (`"fmg"`,
`"rio_tinto"`, `"bhp"`) is detected from the **input file's own name**
(`settings.detect_prompt_variant`, matched against `_PROMPT_VARIANTS_BY_FILENAME` —
e.g. a filename containing `"cb mm ltp"` resolves to `"fmg"`), and that variant already
determines which cross-reference file a given customer shape needs (each AMT enricher
function in `core/cross_reference.py` names its own file). What changed here is
**where** that named file's bytes come from, not **which** file gets picked — so the
same filename convention (`fmg_cross-reference.csv` etc.) is what the Blob container
should hold its files under too, for the existing selection logic to keep working
unmodified.

**Auth** reuses `AZURE_STORAGE_CONNECTION_STRING`/`AZURE_STORAGE_ACCOUNT_URL` (§5) —
independent of `STORAGE_BACKEND`, so cross-reference lookups can come from Blob even
while input/output files stay local (or vice versa). `core/storage.fetch_blob_bytes` is
a standalone helper, separate from the `AzureBlobStorage` class used for input/output
(no input/output-container-naming logic applies here — just one specific, already-known
container+blob to fetch).

**Observability.** A degraded fetch (the container *was* configured but a specific file
couldn't be pulled from it) surfaces in `mapping_report.warnings`, e.g.:
```
AMT cross-reference 'fmg_cross-reference.csv': blob fetch failed (container
'fake-xref-container') — used the local prompts/cross-references/ copy instead. Check
the container/blob name and storage auth if this is unexpected.
```
A plain local read (no container configured at all) is the normal case and does **not**
warn — only an actual configured-but-failed fallback does, so the signal stays
meaningful. Verified end to end: pointed `CROSS_REFERENCE_BLOB_CONTAINER` at a
nonexistent container with unreachable Entra ID auth and confirmed (a) the run still
succeeded with the exact same row counts as the no-Blob baseline (proving the local
fallback recovered identical data, not degraded data) and (b) the warning above fired
naming the correct file and container. See `core/cross_reference.py`'s
`_read_xref_bytes`/`xref_sources` for the implementation.
