"""
Model storage abstraction.

Loads models from Azure Blob Storage in production, local filesystem in dev.
Uses DefaultAzureCredential which works with Managed Identity in Container Apps,
and `az login` auth locally.

Toggle via env vars:
    USE_BLOB_STORAGE=true       — load from blob (prod)
    AZURE_STORAGE_ACCOUNT=...   — required when USE_BLOB_STORAGE=true
    AZURE_MODELS_CONTAINER=...  — defaults to "models"
"""

import os
import logging
from pathlib import Path
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

logger = logging.getLogger(__name__)

STORAGE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT")
MODELS_CONTAINER = os.getenv("AZURE_MODELS_CONTAINER", "models")
LOCAL_MODELS_DIR = Path(__file__).parent.parent / "models"
USE_BLOB = os.getenv("USE_BLOB_STORAGE", "false").lower() == "true"


def _blob_client() -> BlobServiceClient:
    if not STORAGE_ACCOUNT:
        raise RuntimeError("AZURE_STORAGE_ACCOUNT env var not set")
    credential = DefaultAzureCredential()
    account_url = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net"
    return BlobServiceClient(account_url=account_url, credential=credential)


def load_model_bytes(model_name: str, prefix: str = "production") -> bytes:
    """
    Load a model file. In prod: from blob storage. In dev: from local filesystem.
    `model_name` is the filename, e.g., 'best_model.pkl'.
    """
    if USE_BLOB:
        logger.info(f"Loading {prefix}/{model_name} from blob storage")
        client = _blob_client()
        blob = client.get_blob_client(container=MODELS_CONTAINER, blob=f"{prefix}/{model_name}")
        return blob.download_blob().readall()
    else:
        logger.info(f"Loading {model_name} from local filesystem")
        return (LOCAL_MODELS_DIR / model_name).read_bytes()


def upload_model(local_path: Path, blob_name: str, prefix: str = "candidate") -> None:
    """Upload a model file to blob storage. Used by retraining job."""
    client = _blob_client()
    blob = client.get_blob_client(container=MODELS_CONTAINER, blob=f"{prefix}/{blob_name}")
    with open(local_path, "rb") as f:
        blob.upload_blob(f, overwrite=True)
    logger.info(f"Uploaded {local_path} -> {prefix}/{blob_name}")
