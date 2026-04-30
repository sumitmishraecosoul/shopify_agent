from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / "shopify_assistant" / ".env"
    print("env_path:", str(env_path))
    print("env_exists:", env_path.exists())
    load_dotenv(env_path, override=True)

    # Lazy import so this script clearly fails if dependency is missing.
    from azure.storage.blob import BlobClient  # type: ignore
    import os

    conn = (os.getenv("AZURE_CONNECTION_STRING") or "").strip().strip('"')
    container = (os.getenv("AZURE_CONTAINER_NAME") or "").strip()
    tpl = (os.getenv("AZURE_BLOB_PATH_TEMPLATE") or "").strip()

    print("has_AZURE_CONNECTION_STRING:", bool(conn))
    print("has_AZURE_CONTAINER_NAME:", bool(container))
    print("has_AZURE_BLOB_PATH_TEMPLATE:", bool(tpl))

    now = datetime.now()
    blob = tpl.format(YYYY=now.strftime("%Y"), MM=now.strftime("%m"), DD=now.strftime("%d")).lstrip("/")

    print("container:", container)
    print("blob:", blob)

    if not conn or not container or not tpl:
        raise SystemExit("Missing AZURE_CONNECTION_STRING / AZURE_CONTAINER_NAME / AZURE_BLOB_PATH_TEMPLATE in shopify_assistant/.env")

    bc = BlobClient.from_connection_string(conn_str=conn, container_name=container, blob_name=blob)
    data = bc.download_blob().readall()
    print("download_ok_bytes:", len(data))

    # Save to the canonical local filename
    out = repo_root / "shopify_assistant" / "us_shopify_inventory.json"
    out.write_bytes(data)
    print("saved_to:", str(out.resolve()))


if __name__ == "__main__":
    main()

