"""Contractor review routes — /reviews.

Aligned with the actual live DB schema:
  reviews(id, contractor_id, job_id TEXT, reviewer_id,
          rating_quality, rating_communication, rating_cleanliness,
          overall GENERATED, comment, private_feedback, created_at)

Auth: POST /reviews requires a valid JWT.
      GET  /reviews/contractor/{id} requires a valid JWT.
      GET  /reviews/summary/{id} is public (aggregate stats only).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import get_supabase_admin
from app.dependencies import get_current_user

router = APIRouter(prefix="/reviews", tags=["reviews"])
log = logging.getLogger(__name__)

# Columns safe to return to callers (private_feedback intentionally excluded)
_SAFE_COLUMNS = (
    "id", "job_id", "contractor_id", "reviewer_id",
    "rating_cleanliness", "rating_communication", "rating_quality", "overall",
    "comment", "created_at",
)
_SELECT = ",".join(_SAFE_COLUMNS)


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class ReviewCreate(BaseModel):
    job_id:               str
    contractor_id:        str  = Field(..., description="UUID of the contractor being reviewed")
    rating_cleanliness:   int  = Field(..., ge=1, le=5)
    rating_communication: int  = Field(..., ge=1, le=5)
    rating_quality:       int  = Field(..., ge=1, le=5)
    comment:              str | None = Field(default=None, max_length=5_000)
    private_feedback:     str | None = Field(
        default=None,
        max_length=2_000,
        description="Admin-only. Written to DB but never returned to callers.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_admin()


def _strip_private(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "private_feedback"}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def submit_review(body: ReviewCreate, user=Depends(get_current_user)):
    """Submit a review for a contractor on a completed job."""
    user_id = str(user.id)

    # Verify the job exists
    job_res = _db().table("jobs").select("id, user_id").eq("id", body.job_id).limit(1).execute()
    if not job_res.data:
        raise HTTPException(status_code=404, detail="Job not found")

    # One review per reviewer per job
    existing = (
        _db()
        .table("reviews")
        .select("id")
        .eq("job_id", body.job_id)
        .eq("reviewer_id", user_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        raise HTTPException(status_code=409, detail="You have already reviewed this job")

    payload = {
        "job_id":               body.job_id,
        "contractor_id":        body.contractor_id,
        "reviewer_id":          user_id,
        "rating_cleanliness":   body.rating_cleanliness,
        "rating_communication": body.rating_communication,
        "rating_quality":       body.rating_quality,
        "comment":              body.comment,
        **({"private_feedback": body.private_feedback} if body.private_feedback else {}),
    }

    res = _db().table("reviews").insert(payload).execute()
    if not res.data:
        log.error("review_insert_failed", extra={"user_id": user_id, "job_id": body.job_id})
        raise HTTPException(status_code=500, detail="Failed to save review")

    log.info("review_submitted", extra={"user_id": user_id, "job_id": body.job_id})
    return _strip_private(res.data[0])


@router.get("/contractor/{contractor_id}")
async def list_contractor_reviews(contractor_id: str, user=Depends(get_current_user)):
    """Return all reviews for a contractor, newest first."""
    res = (
        _db()
        .table("reviews")
        .select(_SELECT)
        .eq("contractor_id", contractor_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


@router.delete("/{review_id}", status_code=200)
async def delete_review(review_id: str, user=Depends(get_current_user)):
    """Soft-delete a review (reviewer only)."""
    user_id = str(user.id)

    review = (
        _db()
        .table("reviews")
        .select("id, reviewer_id")
        .eq("id", review_id)
        .limit(1)
        .execute()
    )
    if not review.data:
        raise HTTPException(status_code=404, detail="Review not found")

    if review.data[0]["reviewer_id"] != user_id:
        raise HTTPException(status_code=403, detail="You can only delete your own reviews")

    deleted = (
        _db()
        .table("reviews")
        .update({"deleted_at": "now()", "deleted_by_user_id": user_id})
        .eq("id", review_id)
        .execute()
    )

    if not deleted.data:
        log.error("review_delete_failed", extra={"user_id": user_id, "review_id": review_id})
        raise HTTPException(status_code=500, detail="Failed to delete review")

    log.info("review_deleted", extra={"user_id": user_id, "review_id": review_id})
    return {"status": "deleted", "review_id": review_id}


@router.get("/summary/{contractor_id}")
async def contractor_review_summary(contractor_id: str):
    """Return aggregated rating averages for a contractor (public, no auth)."""
    res = (
        _db()
        .table("reviews")
        .select("rating_cleanliness,rating_communication,rating_quality,overall")
        .eq("contractor_id", contractor_id)
        .execute()
    )
    rows = res.data or []
    count = len(rows)

    def _avg(key: str) -> float:
        vals = [float(r[key]) for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    return {
        "contractor_id":     contractor_id,
        "review_count":      count,
        "avg_rating":        _avg("overall"),
        "avg_cleanliness":   _avg("rating_cleanliness"),
        "avg_communication": _avg("rating_communication"),
        "avg_quality":       _avg("rating_quality"),
    }
