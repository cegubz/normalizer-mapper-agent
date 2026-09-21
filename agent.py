"""Agent entrypoint.

run_agent(request) is the single callable used by every surface (Azure Function, CLI,
tests). It is transport-agnostic: give it a dict, get a dict back. Logic Apps calls the
HTTP Function which is a thin wrapper around this.

Request contract (all keys optional except input/workbooks/reference_files — exactly
one of the three is required):
{
  "customer_id": "default",                 # which config/customers/*.json to use
  "input":  "container/blob.csv" | { "path": "..."} | {"content_base64": "...", "filename": "x.xlsx"},
  "workbooks": [                            # ALTERNATIVE to "input" — the TRUE/merge workflow, §14 of README.md.
      "container/blob.xlsx" | { "path": "..." } | {"content_base64": "...", "filename": "site1.xlsx"}, ...
  ],
  "reference_files": [                      # ALTERNATIVE to "input" — see REFERENCE_FILES.md.
      "container/blob.csv" | { "path": "..." } | {"content_base64": "...", "filename": "LTP.csv"}, ...
  ],
  "output_dir": "…",                        # local backend only
  "sheet_config_override": [ ... ],         # optional per-call override (flexible)
  "return_inline": false                    # if true, include CSVs base64 in response
}

"workbooks" is the multi-workbook MERGE workflow (core/mapping_engine.
run_mapping_from_multiple_workbooks): every posted file must be an Excel workbook (not
a standalone CSV — use "reference_files" for that), each is resolved independently
(its own sheet detection), and the SAME role's resolved rows are concatenated across
ALL of them before the shared pipeline runs ONCE — producing a single NEO/LAO with more
rows than any one workbook alone, not one run per file. This is genuinely different
from "reference_files": that workflow treats each posted file as an independent source
for a DIFFERENT target (one file feeds NEO, another feeds LAO); "workbooks" treats every
posted file as another source feeding the SAME targets, unioned together. The original
single-workbook "input" path is unchanged and still the default for a lone file.

Response contract:
{
  "status": "succeeded" | "failed",
  "run_id": "...",
  "customer_id": "...",
  "mapping_report": { ... },                # confidence per column, row counts, warnings
  "outputs": { "NEO": "<local path|container/blob>", "LAO": "...", "Normalized": "...",
               "NEO_Exceptions": "...", "LAO_Exceptions": "...", "ExcludedComponents": "...",
               "DuplicateDates": "..." },
  "outputs_inline": { ... base64 ... }      # only when return_inline=true
  "error": "..."                            # only on failure
}

"NEO_Exceptions"/"LAO_Exceptions" (only present for a target that was actually built)
combine two severities — see core/exceptions.py — distinguished by a
"_removed_from_output" column: rows still missing a critical field (AssetName/
SerialNumber/ComponentCode/ModifierCode by default) or holding gibberish in one are
flagged for review but left in NEO.csv/LAO.csv (`_removed_from_output=False`); rows
where ComponentCode AND ModifierCode are BOTH blank carry no usable component identity
at all and are actually removed (`_removed_from_output=True`).

"ExcludedComponents" (one combined file across NEO+LAO, tagged by a "_source_target"
column — always present, even if empty, once at least one target was built) holds rows
REMOVED from NEO.csv/LAO.csv entirely by a business-rule keyword filter on
StrategyTaskDescription (core/exclusions.py, customer-configurable via
`exclusions.field`/`exclusions.keywords`) — a content decision, not a data-quality one.

"DuplicateDates" (same one-combined-file-tagged-by-target convention) holds rows
removed by the keep-latest-date deduplication (core/deduplication.py,
`deduplication.<target>.group_by`/`date_field`): within a group of rows sharing the
same group_by field values (the same task, planned on different dates), every row
except the one with the latest date_field value is removed here.

A row lands in at most one of NEO_Exceptions/LAO_Exceptions (removed case),
ExcludedComponents, or DuplicateDates — never more than one; see mapping_engine.py's
`_assemble_outputs` for the exact removal order.

Every row of every one of NEO/LAO and all four companion files above also carries a
trailing "ConfidenceScore" column — a PER-ROW score, distinct from the per-column
numbers in mapping_report.mappings/column_confidence_summary. See
core/row_confidence.py. Its means per file are reported in
mapping_report.column_confidence_summary as "{target}_row_confidence_mean" and
"{target}_Exceptions_row_confidence_mean".
"""
from __future__ import annotations
import uuid
import base64
import copy
from datetime import datetime, timezone
from pathlib import Path

from core.settings import load_customer_config
from core.mapping_engine import (
    run_mapping,
    run_mapping_from_multiple_workbooks,
    run_mapping_from_reference_files,
)
from core.storage import get_storage


_EXCEL_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".xlsb"}
_CSV_EXTENSIONS = {".csv"}
_FILE_TYPE_ALIASES = {
    "excel": "excel", "xlsx": "excel", "xls": "excel", "workbook": "excel",
    "csv": "csv",
}



def _normalize_ref(ref) -> dict:
    """A bare string ref is shorthand for {"path": ref} — e.g. "fmg-inbound/file.csv"
    (a bare blob path) or a full blob URL. Dict refs pass through unchanged."""
    if isinstance(ref, str):
        return {"path": ref}
    return ref

def _resolve_file_type(ref: dict, explicit: str | None) -> str:
    """'excel' or 'csv' for a single "input" ref — an explicit "fileType" wins, else
    it's detected from the file's own extension (ref['filename'] if set, else the tail
    of ref['path'])."""
    if explicit:
        resolved = _FILE_TYPE_ALIASES.get(explicit.strip().lower())
        if not resolved:
            raise ValueError(f"Unrecognized fileType {explicit!r} — expected 'excel' or 'csv'")
        return resolved

    name = ref.get("filename") or ref.get("path") or ""
    ext = Path(name.split("?")[0]).suffix.lower()
    if ext in _CSV_EXTENSIONS:
        return "csv"
    if ext in _EXCEL_EXTENSIONS:
        return "excel"
    raise ValueError(
        f"Can't determine file type for {name!r} — pass \"fileType\": \"excel\" or "
        "\"csv\" explicitly."
    )



def _default_run_id() -> str:
    # Timestamp so output folders sort/browse chronologically; a short random
    # suffix keeps concurrent runs in the same second from colliding.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def run_agent(request: dict) -> dict:
    run_id = request.get("run_id") or _default_run_id()
    customer_id = request.get("customer_id", "default")

    try:
        cfg = copy.deepcopy(load_customer_config(customer_id))
        # Per-call flexibility: allow Logic Apps to override sheet detection at runtime.
        if request.get("sheet_config_override"):
            cfg["sheet_config"] = request["sheet_config_override"]

        storage = get_storage(request.get("output_dir"))

        if request.get("workbooks"):
            # The TRUE/merge workflow: several Excel workbooks combined into ONE NEO/LAO
            # — see core/mapping_engine.run_mapping_from_multiple_workbooks + README.md §14.
            refs = [_normalize_ref(ref) for ref in request["workbooks"]]
            for ref in refs:
                file_type = _resolve_file_type(ref, request.get("fileType"))
                if file_type != "excel":
                    raise ValueError(
                        f"'workbooks' requires every posted file to be an Excel workbook "
                        f"(got {ref!r}, resolved as {file_type!r}) — use 'reference_files' "
                        "for standalone CSVs instead."
                    )
            input_paths = [storage.fetch_input(ref) for ref in refs]
            result = run_mapping_from_multiple_workbooks(input_paths, cfg)
        elif request.get("reference_files"):
            # Additional workflow: standalone reference CSVs instead of one workbook —
            # see core/mapping_engine.run_mapping_from_reference_files + REFERENCE_FILES.md.
            input_paths = [
                storage.fetch_input(_normalize_ref(ref)) for ref in request["reference_files"]
            ]
            result = run_mapping_from_reference_files(input_paths, cfg)
        elif request.get("input"):
            # input_path = storage.fetch_input(_normalize_ref(request["input"]))
            ref = _normalize_ref(request["input"])
            input_path = storage.fetch_input(ref)
            file_type = _resolve_file_type(ref, request.get("fileType"))
            if file_type == "csv":
                # A single standalone CSV under "input" — same handling as
                # reference_files: [input], just without requiring the caller to
                # switch request shape depending on file type.
                result = run_mapping_from_reference_files([input_path], cfg)
            else:
                result = run_mapping(input_path, cfg)
        else:
            raise ValueError(
                "request must include 'input' (a single workbook/CSV), 'workbooks' "
                "(multiple workbooks to merge — see README.md §14), or 'reference_files' "
                "(standalone reference CSVs) — see REFERENCE_FILES.md"
            )

        file_map = {"NEO": "NEO.csv", "LAO": "LAO.csv"}
        out_locations, out_inline = {}, {}

        for target, df in result["outputs"].items():
            loc = storage.write_csv(df, file_map.get(target, f"{target}.csv"), run_id)
            out_locations[target] = loc
            if request.get("return_inline"):
                out_inline[target] = base64.b64encode(df.to_csv(index=False).encode()).decode()

        # Post-build exception audit (core/exceptions.py): a companion review file per
        # target, alongside NEO.csv/LAO.csv — rows still missing a critical field or
        # holding gibberish after mapping + cross-reference enrichment. Not a quarantine:
        # those rows stay in NEO.csv/LAO.csv exactly as built.
        for target, exc_df in result.get("exceptions", {}).items():
            key = f"{target}_Exceptions"
            loc = storage.write_csv(exc_df, f"{target}_Exceptions.csv", run_id)
            out_locations[key] = loc
            if request.get("return_inline"):
                out_inline[key] = base64.b64encode(exc_df.to_csv(index=False).encode()).decode()

        norm = result["normalized"]
        # loc = storage.write_csv(norm, "Normalized.csv", run_id)
        loc = storage.write_csv(norm, "Exceptions.csv", run_id)
        out_locations["Normalized"] = loc
        if request.get("return_inline"):
            out_inline["Normalized"] = base64.b64encode(norm.to_csv(index=False).encode()).decode()

        # Business-rule exclusion (core/exclusions.py): rows removed from NEO.csv/LAO.csv
        # entirely because of what they're about (a StrategyTaskDescription keyword
        # match), combined across targets into one companion file. Only written when at
        # least one target was actually built (result["outputs"] non-empty) — mirrors
        # "Normalized" always being written, but there's no meaningful excluded-components
        # file if nothing was ever mapped in the first place.
        if result["outputs"]:
            excl = result["excluded_components"]
            loc = storage.write_csv(excl, "ExcludedComponents.csv", run_id)
            out_locations["ExcludedComponents"] = loc
            if request.get("return_inline"):
                out_inline["ExcludedComponents"] = base64.b64encode(excl.to_csv(index=False).encode()).decode()

        # Keep-latest-date deduplication (core/deduplication.py): older duplicate rows
        # removed from NEO.csv/LAO.csv (same asset+component+modifier+task, an older
        # planned date than another row for that same group), combined across targets
        # into one companion file. Same "only if something was built" convention as
        # ExcludedComponents above.
        if result["outputs"]:
            dup = result["duplicate_dates"]
            loc = storage.write_csv(dup, "DuplicateDates.csv", run_id)
            out_locations["DuplicateDates"] = loc
            if request.get("return_inline"):
                out_inline["DuplicateDates"] = base64.b64encode(dup.to_csv(index=False).encode()).decode()

        response = {
            "status": "succeeded",
            "run_id": run_id,
            "customer_id": customer_id,
            "mapping_report": result["report"],
            "outputs": out_locations,
        }
        if request.get("return_inline"):
            response["outputs_inline"] = out_inline
        return response

    except Exception as exc:  # surface a clean error to the orchestrator
        from core.settings import settings
        return {
            "status": "failed",
            "run_id": run_id,
            "customer_id": customer_id,
            "error": f"{type(exc).__name__}: {exc}",
            "storage_backend": settings.STORAGE_BACKEND,
            "azure_storage_account_url_set": bool(settings.AZURE_STORAGE_ACCOUNT_URL),
            "azure_storage_connection_string": bool(settings.AZURE_STORAGE_CONNECTION_STRING)
        }
