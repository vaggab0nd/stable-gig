"""Web Push notification subscription management.

GET    /notifications/vapid-public-key   — returns VAPID public key (no auth)
POST   /notifications/subscribe          — register a push subscription (auth required)
DELETE /notifications/subscribe          — unsubscribe a push endpoint (auth required)

The frontend (Lovable PWA) should:
  1. Call GET /notifications/vapid-public-key to get the applicationServerKey.
  2. Call navigator.serviceWorker.ready then registration.pushManager.subscribe({
       userVisibleOnly: true,
       applicationServerKey: <base64url key from step 1>
     }).
  3. POST the resulting PushSubscription (endpoint + keys.p256dh + keys.auth) to
     POST /notifications/subscribe.

Auth: POST and DELETE require a valid Supabase JWT.
      GET /notifications/vapid-public-key is unauthenticated (key is public).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.database import get_supabase_admin
from app.dependencies import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class PushSubscriptionCreate(BaseModel):
    endpoint: str = Field(..., description="Push service endpoint URL.")
    p256dh:   str = Field(..., description="ECDH public key (base64url).")
    auth_key: str = Field(..., description="Auth secret (base64url).")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_admin()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/vapid-public-key", include_in_schema=True)
async def vapid_public_key():
    """Return the VAPID application server public key.

    The frontend passes this as the `applicationServerKey` when calling
    PushManager.subscribe().  The key is not secret.
    """
    if not settings.vapid_public_key:
        raise HTTPException(status_code=503, detail="Push notifications are not configured")
    return {"vapid_public_key": settings.vapid_public_key}


@router.post("/subscribe", status_code=201)
async def subscribe(body: PushSubscriptionCreate, user=Depends(get_current_user)):
    """Register a Web Push subscription for the authenticated user.

    Upserts on (user_id, endpoint) — safe to call again after a page refresh
    to keep the stored subscription fresh.
    """
    if not settings.vapid_public_key:
        raise HTTPException(status_code=503, detail="Push notifications are not configured")

    user_id = str(user.id)

    res = (
        _db()
        .table("push_subscriptions")
        .upsert(
            {
                "user_id":   user_id,
                "endpoint":  body.endpoint,
                "p256dh":    body.p256dh,
                "auth_key":  body.auth_key,
                "updated_at": "now()",
            },
            on_conflict="user_id,endpoint",
        )
        .execute()
    )

    if not res.data:
        log.error("push_subscribe_failed", extra={"user_id": user_id})
        raise HTTPException(status_code=500, detail="Failed to save push subscription")

    log.info("push_subscribed", extra={"user_id": user_id, "endpoint": body.endpoint[:60]})
    return {"status": "subscribed"}


@router.delete("/subscribe", status_code=200)
async def unsubscribe(body: PushSubscriptionCreate, user=Depends(get_current_user)):
    """Remove a push subscription endpoint for the authenticated user."""
    user_id = str(user.id)

    _db().table("push_subscriptions").delete().eq("user_id", user_id).eq("endpoint", body.endpoint).execute()

    log.info("push_unsubscribed", extra={"user_id": user_id, "endpoint": body.endpoint[:60]})
    return {"status": "unsubscribed"}
