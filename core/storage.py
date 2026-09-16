"""Storage abstraction so Logic Apps can pass blob locations.

LocalStorage works today (CLI + tests). AzureBlobStorage downloads/uploads against a
real Storage account. Selection is by STORAGE_BACKEND env var — no workflow code
changes needed either way.
"""
from __future__ import annotations
import os
import base64
import tempfile
from urllib.parse import urlsplit, unquote
import pandas as pd

from .settings import settings


class LocalStorage:
    def __init__(self, output_dir: str | None = None):
        self.output_dir = output_dir or settings.LOCAL_OUTPUT_DIR
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                "Cant create local output"
            )

    
    def fetch_input(self, ref: dict) -> str:
        """ref = {'path': ...} or {'content_base64': ..., 'filename': ...}. Returns local path."""
        if ref.get("path"):
            return ref["path"]
        if ref.get("content_base64"):
            data = base64.b64decode(ref["content_base64"])
            fd, tmp = tempfile.mkstemp(suffix="_" + ref.get("filename", "input.xlsx"))
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            return tmp
        raise ValueError("input ref must contain 'path' or 'content_base64'")

    def write_csv(self, df: pd.DataFrame, name: str, run_id: str) -> str:
        run_dir = os.path.join(self.output_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)
        dest = os.path.join(run_dir, name)
        df.to_csv(dest, index=False)
        return dest


def _derive_output_container(input_container: str | None, default_container: str) -> str:
    """The output container mirrors the input container's name, with "inbound" swapped
    for "outbound" (e.g. "fmg-inbound" -> "fmg-outbound"). Falls back to
    `default_container` (settings.OUTPUT_CONTAINER) when the input container is unknown
    (e.g. the input arrived as content_base64) or doesn't follow that naming."""
    if input_container and "inbound" in input_container.lower():
        lower = input_container.lower()
        idx = lower.index("inbound")
        return input_container[:idx] + "outbound" + input_container[idx + len("inbound"):]
    return default_container


class AzureBlobStorage:
    """Requires azure-storage-blob (+ azure-identity for Entra ID auth). `ref["path"]`
    in fetch_input accepts three shapes, in order of how directly they name a blob:

      1. A full blob URL with a SAS token already embedded (```https://acct.blob.core.
         windows.net/container/blob.csv?sv=...&sig=...```) — used exactly as given, no
         extra auth needed. This is what most Logic Apps "Create SAS URI" / blob
         connector actions hand you.
      2. A full blob URL with no SAS token — authenticated via
         AZURE_STORAGE_CONNECTION_STRING if set, else via Entra ID
         (DefaultAzureCredential: the Function/Hosted Agent's managed identity in
         Azure, or `az login` locally) against that URL's own account — same auth
         story as the Foundry model gateway in core/llm_mapper.py.
      3. A bare "<container>/<blob_name>" path (no scheme) — resolved against
         AZURE_STORAGE_CONNECTION_STRING if set, else against AZURE_STORAGE_ACCOUNT_URL
         via Entra ID.

    Outputs are written back to the same storage account (AZURE_STORAGE_CONNECTION_STRING
    or AZURE_STORAGE_ACCOUNT_URL — a full input blob URL's own host is not reused for
    outputs), in a container derived from the *input* blob's container — see
    _derive_output_container — falling back to OUTPUT_CONTAINER when that can't be
    determined.
    """

    def __init__(self):
        self.conn = settings.AZURE_STORAGE_CONNECTION_STRING
        self.account_url = settings.AZURE_STORAGE_ACCOUNT_URL
        self.default_container = settings.OUTPUT_CONTAINER
        # Set by fetch_input() from the input blob's own path, so write_csv() can derive
        # the matching output container (e.g. fmg-inbound -> fmg-outbound).
        self._input_container: str | None = None

    def _blob_client(self, path: str):
        if path.startswith("http://") or path.startswith("https://"):
            parsed = urlsplit(path)
            if parsed.query:  # SAS token (or other pre-authorized query) already embedded
                from azure.storage.blob import BlobClient
                return BlobClient.from_blob_url(path)
            container, _, blob_name = unquote(parsed.path).lstrip("/").partition("/")
            if not blob_name:
                raise ValueError(f"blob URL {path!r} has no blob name after the container")
            account_url = f"{parsed.scheme}://{parsed.netloc}"
            return self._client_for(container, blob_name, account_url)
        # Bare "<container>/<blob_name>" relative to the configured storage account.
        container, _, blob_name = path.partition("/")
        if not blob_name:
            raise ValueError(f"bare blob path {path!r} must be '<container>/<blob_name>'")
        return self._client_for(container, blob_name, self.account_url or None)

    def _client_for(self, container: str, blob_name: str, account_url: str | None = None):
        if self.conn:
            from azure.storage.blob import BlobServiceClient
            service = BlobServiceClient.from_connection_string(self.conn)
            return service.get_blob_client(container=container, blob=blob_name)
        if not account_url:
            raise RuntimeError(
                "Neither AZURE_STORAGE_CONNECTION_STRING nor AZURE_STORAGE_ACCOUNT_URL "
                "is set, and no full blob URL host to fall back to Entra ID auth "
                "against — pass a full blob URL or set one of those env vars."
            )
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient
        return BlobClient(
            account_url=account_url, container_name=container, blob_name=blob_name,
            credential=DefaultAzureCredential(),
        )

    def _service_client(self):
        """BlobServiceClient for the configured output account (connection string wins,
        else Entra ID via AZURE_STORAGE_ACCOUNT_URL) — used for output uploads."""
        from azure.storage.blob import BlobServiceClient
        if self.conn:
            return BlobServiceClient.from_connection_string(self.conn)
        if self.account_url:
            from azure.identity import DefaultAzureCredential
            return BlobServiceClient(account_url=self.account_url, credential=DefaultAzureCredential())
        raise RuntimeError(
            "Neither AZURE_STORAGE_CONNECTION_STRING nor AZURE_STORAGE_ACCOUNT_URL is "
            "set — required to write outputs."
        )

    def fetch_input(self, ref: dict) -> str:
        """ref = {'path': <blob URL or 'container/blob'>} or {'content_base64': ..., 'filename': ...}.
        Downloads to a temp file and returns its local path (same contract as
        LocalStorage.fetch_input) — the rest of the pipeline never needs to know the
        source was Blob storage."""
        if ref.get("content_base64"):
            data = base64.b64decode(ref["content_base64"])
            fd, tmp = tempfile.mkstemp(suffix="_" + ref.get("filename", "input.xlsx"))
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            return tmp

        path = ref.get("path")
        if not path:
            raise ValueError("input ref must contain 'path' or 'content_base64'")

        if path.startswith("http://") or path.startswith("https://"):
            self._input_container = unquote(urlsplit(path).path).lstrip("/").split("/", 1)[0]
        else:
            self._input_container = path.split("/", 1)[0]

        blob_client = self._blob_client(path)
        filename = ref.get("filename") or os.path.basename(unquote(path.split("?")[0]))
        fd, tmp = tempfile.mkstemp(suffix="_" + (filename or "input"))
        with os.fdopen(fd, "wb") as fh:
            blob_client.download_blob().readinto(fh)
        return tmp

    def write_csv(self, df: pd.DataFrame, name: str, run_id: str) -> str:
        container = _derive_output_container(self._input_container, self.default_container)
        blob_name = f"{run_id}/{name}"
        service = self._service_client()
        blob_client = service.get_blob_client(container=container, blob=blob_name)
        blob_client.upload_blob(df.to_csv(index=False).encode("utf-8"), overwrite=True)
        # Bare "<container>/<blob_name>" — mirrors the shorthand accepted for input paths
        # (agent._normalize_ref) rather than a full URL; same account as the request's
        # configured AZURE_STORAGE_CONNECTION_STRING / AZURE_STORAGE_ACCOUNT_URL.
        return f"{container}/{blob_name}"


def get_storage(output_dir: str | None = None):
    if settings.STORAGE_BACKEND == "azure_blob":
        return AzureBlobStorage()
    return LocalStorage(output_dir)
