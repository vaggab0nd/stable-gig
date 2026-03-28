"""Contractor review routes — /reviews.

All reads go through the visible_reviews view, which:
  • enforces the double-blind (body / ai_pros_cons are NULL until both parties
    have reviewed or the 14-day timer expires)
  • never exposes private_feedback (column intentionally absent from the view)
  • uses correct current column names (rating_cleanliness / rating_communication
    / rating_quality — not the stale names in the old router)

Auth: POST /reviews requires a valid JWT.
      GET  /reviews/contractor/{id} requires a valid JWT (review data is
      personal/commercial — not appropriate for fully anonymous access).
      GET  /reviews/summary/{id} is intentionally open (aggregate stats
      shown on public contractor profiles contain no PII).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import get_supabase_admin
from app.dependencies import get_current_user

router = APIRouter(prefix="/reviews", tags=["reviews"])
log = logging.getLogger(__name__)

# Safe column list for reads from the raw reviews table (used only for INSERT
# response).  private_feedback is deliberately excluded.
_SAFE_REVIEW_COLUMNS = (
    "id", "job_id", "reviewer_id", "reviewee_id",
    "reviewer_role", "reviewee_role",
    "rating_cleanliness", "rating_communication", "rating_quality", "rating",
    "body", "ai_pros_cons", "content_visible", "reveal_at", "submitted_at",
)

# Columns fetched from visible_reviews for the listing endpoint
_VISIBLE_COLUMNS = ",".join(_SAFE_REVIEW_COLUMNS)


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class ReviewCreate(BaseModel):
    job_id:               str
    reviewee_id:          str
    reviewee_role:        str = Field(..., description="'contractor' or 'client'")
    reviewer_role:        str = Field(..., description="'client' or 'contractor'")
    rating_cleanliness:   int = Field(..., ge=1, le=5)
    rating_communication: int = Field(..., ge=1, le=5)
    rating_quality:       int = Field(..., ge=1, le=5)
    body:                 str | None = Field(default=None, max_length=5_000)
    private_feedback:     str | None = Field(
        default=None,
        max_length=2_000,
        description="Admin-only. Written to the DB but never returned to callers.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_admin()


def _strip_private(row: dict) -> dict:
    """Remove private_feedback from any dict before returning it to callers."""
    return {k: v for k, v in row.items() if k != "private_feedback"}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def submit_review(body: ReviewCreate, user=Depends(get_current_user)):
    """Submit a review for the other party on a completed job.

    private_feedback is accepted in the payload (the TradesmanRating component
    sends it) and written to the DB, but is never returned in any response.
    """
    user_id = str(user.id)

    if body.reviewee_role not in {"contractor", "client"}:
        raise HTTPException(status_code=422, detail="reviewee_role must be 'contractor' or 'client'")
    if body.reviewer_role not in {"contractor", "client"}:
        raise HTTPException(status_code=422, detail="reviewer_role must be 'contractor' or 'client'")
    if body.reviewer_role == body.reviewee_role:
        raise HTTPException(status_code=422, detail="reviewer_role and reviewee_role must differ")

    # Verify the job exists
    job_res = _db().table("jobs").select("id, user_id").eq("id", body.job_id).limit(1).execute()
    if not job_res.data:
        raise HTTPException(status_code=404, detail="Job not found")

    # Enforce one review per job per reviewer at the application layer
    # (DB UNIQUE constraint is the backstop)
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
        "reviewer_id":          user_id,
        "reviewee_id":          body.reviewee_id,
        "reviewer_role":        body.reviewer_role,
        "reviewee_role":        body.reviewee_role,
        "rating_cleanliness":   body.rating_cleanliness,
        "rating_communication": body.rating_communication,
        "rating_quality":       body.rating_quality,
        "body":                 body.body,
        # private_feedback written to DB but never returned
        **({"private_feedback": body.private_feedback} if body.private_feedback else {}),
    }

    res = _db().table("reviews").insert(payload).execute()
    if not res.data:
        log.error("review_insert_failed", extra={"user_id": user_id, "job_id": body.job_id})
        raise HTTPException(status_code=500, detail="Failed to save review")

    log.info("review_submitted", extra={"user_id": user_id, "job_id": body.job_id})
    # Never return private_feedback to the caller
    return _strip_private(res.data[0])


@router.get("/contractor/{contractor_id}")
async def list_contractor_reviews(
    contractor_id: str,
    user=Depends(get_current_user),
):
    """Return visible reviews for a contractor, newest first.

    Reads from visible_reviews — the double-blind view that:
      • hides body / ai_pros_cons until both parties have reviewed
      • never exposes private_feedback
    Requires auth to prevent bulk-scraping of review content.
    """
    res = (
        _db()
        .table("visible_reviews")
        .select(_VISIBLE_COLUMNS)
        .eq("reviewee_id", contractor_id)
        .eq("reviewee_role", "contractor")
        .order("submitted_at", desc=True)
        .execute()
    )
    return res.data or []


@router.get("/summary/{contractor_id}")
async def contractor_review_summary(contractor_id: str):
    """Return aggregated rating averages for a contractor (public, no auth).

    Only counts reviews that are visible (double-blind lifted or timer expired).
    Uses correct current column names: rating_cleanliness, rating_communication,
    rating_quality, rating (generated average).
    """
    res = (
        _db()
        .table("visible_reviews")
        .select("rating_cleanliness,rating_communication,rating_quality,rating")
        .eq("reviewee_id", contractor_id)
        .eq("reviewee_role", "contractor")
        .not_is("body", "null")  # only count revealed reviews (body is NULL until revealed)
        .execute()
    )
    rows = res.data or []
    count = len(rows)

    def _avg(key: str) -> float:
        # Supabase returns NUMERIC/GENERATED columns as strings; cast to float.
        vals = [float(r[key]) for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    return {
        "contractor_id":       contractor_id,
        "review_count":        count,
        "avg_rating":          _avg("rating"),
        "avg_cleanliness":     _avg("rating_cleanliness"),
        "avg_communication":   _avg("rating_communication"),
        "avg_quality":         _avg("rating_quality"),
    }
