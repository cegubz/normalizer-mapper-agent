"""Agent entrypoint.

run_agent(request) is the single callable used by every surface (Azure Function, CLI,
tests). It is transport-agnostic: give it a dict, get a dict back. Logic Apps calls the
HTTP Function which is a thin wrapper around this.

Request contract (all keys optional except input/reference_files — exactly one required):
{
  "customer_id": "default",                 # which config/customers/*.json to use
  "input":  "container/blob.csv" | { "path": "..."} | {"content_base64": "...", "filename": "x.xlsx"},
  "reference_files": [                      # ALTERNATIVE to "input" — see REFERENCE_FILES.md.
      "container/blob.csv" | { "path": "..." } | {"content_base64": "...", "filename": "LTP.csv"}, ...
  ],
  "output_dir": "…",                        # local backend only
  "sheet_config_override": [ ... ],         # optional per-call override (flexible)
  "return_inline": false                    # if true, include CSVs base64 in response
}

Response contract:
{
  "status": "succeeded" | "failed",
  "run_id": "...",
  "customer_id": "...",
  "mapping_report": { ... },                # confidence per column, row counts, warnings
  "outputs": { "NEO": "<local path|container/blob>", "LAO": "...", "Normalized": "..." },
  "outputs_inline": { ... base64 ... }      # only when return_inline=true
  "error": "..."                            # only on failure
}
"""
from __future__ import annotations
import uuid
import base64
import copy
from datetime import datetime, timezone
from pathlib import Path

from core.settings import load_customer_config
from core.mapping_engine import run_mapping, run_mapping_from_reference_files
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

        if request.get("reference_files"):
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
                "request must include either 'input' (a workbook) or "
                "'reference_files' (standalone reference CSVs) — see REFERENCE_FILES.md"
            )

        file_map = {"NEO": "NEO.csv", "LAO": "LAO.csv"}
        out_locations, out_inline = {}, {}

        for target, df in result["outputs"].items():
            loc = storage.write_csv(df, file_map.get(target, f"{target}.csv"), run_id)
            out_locations[target] = loc
            if request.get("return_inline"):
                out_inline[target] = base64.b64encode(df.to_csv(index=False).encode()).decode()

        norm = result["normalized"]
        # loc = storage.write_csv(norm, "Normalized.csv", run_id)
        loc = storage.write_csv(norm, "Exceptions.csv", run_id)
        out_locations["Normalized"] = loc
        if request.get("return_inline"):
            out_inline["Normalized"] = base64.b64encode(norm.to_csv(index=False).encode()).decode()

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
