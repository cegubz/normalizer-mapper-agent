"""Environment settings and customer-config loading.

Everything that changes per environment is an env var (12-factor). Everything that
changes per customer is a JSON file under config/customers/. No workflow logic here.
"""
from __future__ import annotations
import os
import json
from pathlib import Path
from functools import lru_cache

# Repo root = parent of this file's parent (…/fmg_agent)
ROOT = Path(__file__).resolve().parents[1]
CUSTOMERS_DIR = ROOT / "config" / "customers"
PROMPT_PATH = ROOT / "prompts" / "schema_mapping_prompt.md"

# Dedicated, git-ignored env files, one per provider (kept separate so switching
# providers is just adding/removing a file, not editing a shared .env). Both are loaded
# here, before the env vars below are read, and neither overrides a variable already
# present in the real environment. .env.openai is loaded second so that if a variable
# (e.g. MODEL_PROVIDER) is set in both, .env.anthropic's copy wins only when no real env
# var already set it — actual provider priority between the two keys is decided by
# Settings.resolved_provider below, not by load order.
_ANTHROPIC_ENV_FILE = ROOT / ".env.anthropic"
_OPENAI_ENV_FILE = ROOT / ".env.openai"
try:
    from dotenv import load_dotenv  # optional dep, see requirements-anthropic.txt
    if _ANTHROPIC_ENV_FILE.exists():
        load_dotenv(_ANTHROPIC_ENV_FILE, override=False)
    if _OPENAI_ENV_FILE.exists():
        load_dotenv(_OPENAI_ENV_FILE, override=False)
except ImportError:
    pass  # python-dotenv not installed; fall back to real env vars only


class Settings:
    """Runtime settings, all overridable via environment variables."""

    # --- Model provider ---
    # "openai"    -> call api.openai.com directly with OPENAI_API_KEY (Azure Functions path)
    # "foundry"   -> call the Foundry model catalog via the project gateway + Entra ID auth
    # "anthropic" -> call api.anthropic.com directly with ANTHROPIC_API_KEY
    MODEL_PROVIDER: str = os.getenv("MODEL_PROVIDER", "openai")

    # --- OpenAI (direct) ---
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    # Default recommended tier-equivalent of Claude Sonnet for this task. When
    # MODEL_PROVIDER=foundry this must match your Foundry *model deployment name*.
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")
    # Escalation model for ambiguous (low_review) columns.
    OPENAI_ESCALATION_MODEL: str = os.getenv("OPENAI_ESCALATION_MODEL", "gpt-5.6-sol")
    OPENAI_REASONING_EFFORT: str = os.getenv("OPENAI_REASONING_EFFORT", "low")

    # --- Foundry gateway ---
    # e.g. https://<resource>.ai.azure.com/api/projects/<project>
    FOUNDRY_PROJECT_ENDPOINT: str = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")

    # --- Anthropic (direct, api.anthropic.com) ---
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    # Opus 5 is the most capable model in the Claude 5 family — best available accuracy
    # for the alias-reasoning/refinement step.
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")

    # Master switch — when false, the agent runs the deterministic path only.
    USE_LLM: bool = os.getenv("USE_LLM", "true").lower() == "true"

    DEBUG_PROMPT: bool = os.getenv('DEBUG_PROMPT',"false").lower()=="true"

    @property
    def resolved_provider(self) -> str:
        """The provider actually used, auto-selected by key availability.

        `MODEL_PROVIDER=foundry` is always honored explicitly (it needs a deliberately
        configured project endpoint + Entra ID auth, so it's never picked implicitly).
        Otherwise: OPENAI_API_KEY wins whenever it's set; ANTHROPIC_API_KEY is the
        fallback when it isn't — regardless of what MODEL_PROVIDER happens to say (e.g.
        a customer's .env.anthropic can force MODEL_PROVIDER=anthropic, but an
        OPENAI_API_KEY present in the real environment still takes priority). If
        neither key is set, MODEL_PROVIDER is returned as-is so llm_configured() can
        report False with the right reason.
        """
        if self.MODEL_PROVIDER == "foundry":
            return "foundry"
        if self.OPENAI_API_KEY:
            return "openai"
        if self.ANTHROPIC_API_KEY:
            return "anthropic"
        return self.MODEL_PROVIDER

    def llm_configured(self) -> bool:
        """True when the LLM refinement layer can actually run (single source of truth)."""
        if not self.USE_LLM:
            return False
        provider = self.resolved_provider
        if provider == "foundry":
            return bool(self.FOUNDRY_PROJECT_ENDPOINT)
        if provider == "anthropic":
            return bool(self.ANTHROPIC_API_KEY)
        return bool(self.OPENAI_API_KEY)

    # --- Storage ---
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local").strip().lower()  # local | azure_blob
    # Either of these authenticates AzureBlobStorage against the account — connection
    # string wins when both are set:
    #   - AZURE_STORAGE_CONNECTION_STRING: full connection string (account key auth).
    #   - AZURE_STORAGE_ACCOUNT_URL: e.g. https://<account>.blob.core.windows.net —
    #     used with Entra ID (DefaultAzureCredential: managed identity in Azure, `az
    #     login` locally). Required for bare "<container>/<blob_name>" paths when no
    #     connection string is set, and used as the account for output uploads.
    AZURE_STORAGE_CONNECTION_STRING: str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    AZURE_STORAGE_ACCOUNT_URL: str = os.getenv("AZURE_STORAGE_ACCOUNT_URL", "")
    # Fallback output container, used only when one can't be derived from the input
    # blob's own container (see core.storage._derive_output_container).
    OUTPUT_CONTAINER: str = os.getenv("OUTPUT_CONTAINER", "outputs")

    # --- Local paths (used by the local storage backend / CLI) ---
    LOCAL_OUTPUT_DIR: str = os.getenv("LOCAL_OUTPUT_DIR", str(ROOT / "_out"))

    # --- Cross-reference source (core/cross_reference.py) ---
    # Priority source for the AMT cross-reference lookup CSVs (fmg_cross-reference.csv,
    # rio-tinto_cross-reference.csv, bhp_cross-reference.csv), when set. Falls back to
    # prompts/cross-references/ (bundled with the code) when this is unset, or when a
    # specific file can't be fetched from the blob for any reason (not found, auth,
    # network) — see core/cross_reference.py for exactly how. Auth reuses
    # AZURE_STORAGE_CONNECTION_STRING / AZURE_STORAGE_ACCOUNT_URL above; no separate
    # credential to configure, and this is independent of STORAGE_BACKEND (input/output
    # files can stay local while cross-reference lookups come from blob, or vice versa).
    CROSS_REFERENCE_BLOB_CONTAINER: str = os.getenv("CROSS_REFERENCE_BLOB_CONTAINER", "")


settings = Settings()


@lru_cache(maxsize=32)
def load_customer_config(customer_id: str = "default") -> dict:
    """Load a customer profile JSON. Falls back to 'default' if the id is unknown."""
    path = CUSTOMERS_DIR / f"{customer_id}.json"
    if not path.exists():
        path = CUSTOMERS_DIR / "default.json"
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    return cfg


def list_customers() -> list[str]:
    return sorted(
        p.stem for p in CUSTOMERS_DIR.glob("*.json") if not p.stem.startswith("_")
    )


PROMPTS_DIR = PROMPT_PATH.parent

# Filename substring -> dedicated prompt variant (see prompts/README.md). Matched
# case-insensitively against the workbook's basename. Anything that doesn't match
# (including files intentionally left off this list) falls back to the main prompt.
# _PROMPT_VARIANTS_BY_FILENAME = {
#     "cb mm ltp": "cb_mm_ltp_august",
#     "rio tinto": "rio_tinto",
#     "billiton": "westrac",
#     "combination of all files": "westrac",
# }
_PROMPT_VARIANTS_BY_FILENAME = {
    "cb mm ltp": "fmg",
    "rio tinto": "rio_tinto",
    "billiton": "bhp",
    "combination of all files": "bhp",
}


def detect_prompt_variant(workbook_path: str) -> str | None:
    """Pick a dedicated prompt variant from the workbook's filename, if one exists."""
    name = Path(workbook_path).name.lower()
    for needle, variant in _PROMPT_VARIANTS_BY_FILENAME.items():
        if needle in name:
            return variant
    return None


def load_prompt(variant: str | None = None) -> str:
    """Return the schema-mapping prompt text: the main prompt, plus a per-workbook-shape
    appendix (prompts/appendix.<variant>.md) when `variant` names one that exists — see
    detect_prompt_variant + prompts/README.md.

    Composed at read time from ONE shared body, not duplicated per variant: editing
    prompts/schema_mapping_prompt.md changes every variant's shared rules immediately,
    with no separate copies to keep in sync.
    """
    with open(PROMPT_PATH, "r", encoding="utf-8") as fh:
        text = fh.read()
    if variant:
        appendix_path = PROMPTS_DIR / f"appendix.{variant}.md"
        if appendix_path.exists():
            with open(appendix_path, "r", encoding="utf-8") as fh:
                text = text.rstrip("\n") + "\n\n" + fh.read()
    return text
