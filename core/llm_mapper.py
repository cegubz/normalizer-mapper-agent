"""LLM alias-reasoning layer (OpenAI / Foundry / Anthropic).

Faithful to the recommended hybrid design: the deterministic scorer owns the numbers;
the LLM confirms/repairs alias matches for ambiguous columns and writes human-readable
notes, using the project prompt (prompts/schema_mapping_prompt.md).

If no API key is configured (or USE_LLM=false), this degrades gracefully and the agent
runs the deterministic path only — so it is always callable/testable.
"""
from __future__ import annotations
import json
from .settings import settings, load_prompt

# JSON schema for the structured mapping decision we ask the model to return.
_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "canonical_field": {"type": "string"},
                    "source_column": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["canonical_field", "source_column", "confidence", "notes"],
            },
        }
    },
    "required": ["decisions"],
}


def _client():
    """Return an OpenAI-compatible client for the configured provider.

    - openai  : direct OpenAI SDK client using OPENAI_API_KEY.
    - foundry : Foundry project gateway client (models from the Foundry catalog),
                authenticated with Entra ID via DefaultAzureCredential — no API key.
    Both expose the same Responses API surface used by refine_mapping().

    Provider selection here follows settings.resolved_provider (OpenAI first,
    Anthropic fallback, foundry only when explicitly configured) — see settings.py.
    """
    if settings.resolved_provider == "foundry":
        try:
            from azure.identity import DefaultAzureCredential
            from azure.ai.projects import AIProjectClient
        except Exception as exc:  # optional deps (requirements-foundry.txt)
            raise RuntimeError("azure-ai-projects / azure-identity not installed") from exc
        project = AIProjectClient(
            endpoint=settings.FOUNDRY_PROJECT_ENDPOINT,
            credential=DefaultAzureCredential(),
        )
        return project.get_openai_client()

    try:
        from openai import OpenAI
    except Exception as exc:  # SDK not installed
        raise RuntimeError("openai SDK not available") from exc
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def _anthropic_client():
    try:
        import anthropic
    except Exception as exc:  # optional dep, see requirements-anthropic.txt
        raise RuntimeError("anthropic SDK not available (pip install anthropic)") from exc
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


# The Messages API has no json_schema response_format like OpenAI's Responses API, so
# structured output is obtained by forcing a single tool call whose input must match
# _RESPONSE_SCHEMA.
_ANTHROPIC_TOOL = {
    "name": "return_mapping",
    "description": "Return the reviewed/repaired schema-mapping decisions.",
    "input_schema": _RESPONSE_SCHEMA,
}


def _call_anthropic(model: str, system_prompt: str, user_payload: dict) -> dict | None:
    resp = _anthropic_client().messages.create(
        model=model,
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": json.dumps(user_payload)}],
        tools=[_ANTHROPIC_TOOL],
        tool_choice={"type": "tool", "name": "return_mapping"},
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    return None


def _call_openai(model: str, system_prompt: str, user_payload: dict) -> dict:
    resp = _client().responses.create(
        model=model,
        reasoning={"effort": settings.OPENAI_REASONING_EFFORT},
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "mapping_decisions",
                "schema": _RESPONSE_SCHEMA,
                "strict": True,
            }
        },
    )
    return json.loads(resp.output_text)


def refine_mapping(
    target: str,
    fields: list[dict],
    column_profiles: list[dict],
    deterministic: dict,
    model: str | None = None,
    prompt_variant: str | None = None,
) -> dict:
    """Ask the model to review/repair ambiguous mappings. Returns {canonical: {...}}.

    `prompt_variant` selects a dedicated per-workbook-shape prompt (see
    settings.detect_prompt_variant / prompts/README.md) when one exists; otherwise the
    main prompt is used.

    Never raises to the caller: on any failure it returns {} and the agent keeps the
    deterministic result.
    """
    if not settings.llm_configured():
        return {}

    is_anthropic = settings.resolved_provider == "anthropic"
    model = model or (settings.ANTHROPIC_MODEL if is_anthropic else settings.OPENAI_MODEL)
    system_prompt = load_prompt(prompt_variant)

    user_payload = {
        "target": target,
        "customer_file_fields": [
            {"canonical": f["canonical"], "aliases": f.get("aliases", []), "dtype": f.get("dtype")}
            for f in fields
            if f.get("source_class") == "customer_file"
        ],
        "columns": [
            {"column": p["column"], "evidence": p["evidence"], "samples": p["samples"]}
            for p in column_profiles
        ],
        "deterministic_candidates": deterministic,
        "instruction": (
            "Confirm or correct the source_column for each customer_file field. "
            "Only choose from the provided columns. Do NOT invent columns. "
            "For enrichment/constant fields, do not propose a source. "
            "Return confidence in [0,1] and a one-line note, especially for ambiguous columns."
        ),
    }

    try:
        data = (
            _call_anthropic(model, system_prompt, user_payload)
            if is_anthropic
            else _call_openai(model, system_prompt, user_payload)
        )
        if not data:
            return {}
        out = {}
        valid_cols = {p["column"] for p in column_profiles}
        for d in data.get("decisions", []):
            col = d.get("source_column")
            if col is not None and col not in valid_cols:
                col = None  # guard against hallucinated columns
            out[d["canonical_field"]] = {
                "source_column": col,
                "confidence": float(d.get("confidence", 0.0)),
                "notes": d.get("notes", ""),
            }
        return out
    except Exception:
        # Any SDK / network / parsing error -> deterministic path stands.
        return {}
