"""User metadata routes — /me/metadata."""

from fastapi import APIRouter, Depends, HTTPException

from app.database import get_supabase
from app.dependencies import get_current_user
from app.models.schemas import UserMetadataResponse, UserMetadataUpdate

router = APIRouter(prefix="/me", tags=["user_metadata"])


@router.get("/metadata", response_model=UserMetadataResponse)
async def get_metadata(user=Depends(get_current_user)):
    result = (
        get_supabase()
        .table("user_metadata")
        .select("*")
        .eq("id", str(user.id))
        .maybe_single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="User metadata not found")
    return result.data


@router.patch("/metadata", response_model=UserMetadataResponse)
async def update_metadata(body: UserMetadataUpdate, user=Depends(get_current_user)):
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=422, detail="No fields provided to update")

    # Upsert so this works even if the trigger row was never created
    get_supabase().table("user_metadata").upsert(
        {"id": str(user.id), **data}, on_conflict="id"
    ).execute()

    # Re-fetch to return the full updated row
    result = (
        get_supabase()
        .table("user_metadata")
        .select("*")
        .eq("id", str(user.id))
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to retrieve updated metadata")
    return result.data
