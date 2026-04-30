from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .auth import require_refresh_auth
from .clickhouse_client import clickhouse_client


router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"], dependencies=[Depends(require_refresh_auth)])


class InventoryRefreshResponse(BaseModel):
    success: bool
    message: str
    products_loaded: int
    etag: str | None = None
    blob_path: str | None = None
    duration_ms: int | None = None
    downloaded: bool | None = None
    bytes: int | None = None
    previous_etag: str | None = None
    previous_blob_path: str | None = None


class InventoryRefreshRequest(BaseModel):
    # Optional explicit date to avoid "after midnight" edge cases.
    # Format: YYYY-MM-DD
    date: str | None = Field(default=None, examples=["2026-04-30"])
    # If true, forces re-download/reload even if the ETag is unchanged.
    force: bool = False


@router.post("/refresh", response_model=InventoryRefreshResponse)
def refresh_inventory(req: InventoryRefreshRequest, request: Request) -> InventoryRefreshResponse:
    ok, msg, meta = clickhouse_client.refresh_inventory(
        download_from_azure=True,
        log=True,
        requested_date=req.date,
        force=req.force,
        triggered_by=f"{request.client.host if request.client else 'unknown'}",
    )
    return InventoryRefreshResponse(
        success=bool(ok),
        message=msg,
        products_loaded=len(getattr(clickhouse_client, "_products", []) or []),
        etag=(meta or {}).get("etag"),
        blob_path=(meta or {}).get("blob_path"),
        duration_ms=(meta or {}).get("duration_ms"),
        downloaded=(meta or {}).get("downloaded"),
        bytes=(meta or {}).get("bytes"),
        previous_etag=(meta or {}).get("previous_etag"),
        previous_blob_path=(meta or {}).get("previous_blob_path"),
    )

