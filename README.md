# FMG Schema-Mapping Agent

A Python AI agent that runs the two established workflows from this project —
**row normalization** and **AI-assisted schema mapping** — and produces the **NEO**,
**LAO**, and **Normalized** CSVs. It uses an **OpenAI model**, is **callable by Azure
Logic Apps** over HTTP, and is **fully customer-configurable through JSON** (a new
customer is onboarded by adding one config file — no code change).

It does not introduce any new workflow. Enrichment fields (the AMT / Cross-Reference /
IK17 join columns, i.e. the `_y` columns in `NEO.py` / `LAO.py`) are left **blank and
labelled `requires_enrichment`** — never invented. No Snowflake logic is included.

---

## 1. What it does (unchanged from the project)

```
customer .xlsx
      │
      ▼
[1] detect sheets      ← flexible sheet_config (LTP→NEO, Measurement Points→LAO)
[2] profile columns    ← header + sampled values + dtype/format evidence
[3] score mapping      ← deterministic scorer (reproducible confidence per column)
[4] LLM refine         ← OpenAI reviews/repairs ambiguous columns + writes notes
[5] normalize rows     ← keyless / identity-missing / banner / gibberish → Normalized.csv
[6] build outputs      ← NEO.csv + LAO.csv (constants + transforms; enrichment blank)
      │
      ▼
mapping_report (JSON, confidence per column) + 3 CSVs
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
│   └─ mapping_engine.py      # orchestrates the 6 steps above
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
| Add a brand-new customer | copy `_TEMPLATE.json` → `<id>.json` | ✅ |
| Per-call sheet override | `sheet_config_override` in the request body | ✅ |
| Storage target (local ↔ Azure Blob) | `STORAGE_BACKEND` env var | ✅ |

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

**Response**:
```json
{
  "status": "succeeded",
  "run_id": "a2c969e26557",
  "customer_id": "default",
  "mapping_report": {
    "matched_sheets": [...],
    "mappings": { "NEO": [ {"canonical_field","source_column","confidence","band","status","notes"} ], "LAO": [...] },
    "column_confidence_summary": { "NEO_mean": 0.813, "LAO_mean": 0.99 },
    "row_counts": { "NEO": 9296, "LAO": 22893, "Normalized": 1403 },
    "warnings": [], "llm_used": true
  },
  "outputs": { "NEO": "<local path|container/blob>", "LAO": "...", "Normalized": "..." }
}
```
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

Verified run on `CB_MM_LTP_AUGUST.xlsx`: **NEO 9,296 rows · LAO 22,893 rows ·
Normalized 1,403 rows**; NEO/LAO headers match `FMG_NEO_Aug_26.csv` / `FMG_LAO_Aug_26.csv`
exactly; customer-file confidence means **NEO 0.813 / LAO 0.99**.

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

- **No new workflows** beyond normalization + schema mapping.
- **No invented columns/values** — enrichment stays blank and labelled.
- **No Snowflake / warehouse** — file (or blob) output only; enrichment + load happen in
  your later integration step.
