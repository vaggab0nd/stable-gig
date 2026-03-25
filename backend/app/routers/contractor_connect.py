"""Stripe Connect onboarding router for contractors.

POST /me/contractor/connect-onboard
    Creates (or retrieves) the contractor's Stripe Express account and returns
    a single-use Account Link URL.  The frontend redirects the contractor to
    this URL to complete bank / identity verification.

    If the contractor already has a stripe_account_id, a fresh Account Link is
    issued for the same account — safe to call multiple times (e.g. if the
    previous link expired or onboarding was not completed).

GET  /me/contractor/connect-status
    Returns the current state of the contractor's connected account:
    whether charges and payouts are enabled.  The frontend uses this to show
    a "Connected" badge or a "Complete setup" prompt.

Auth: both endpoints require a valid Supabase JWT.
      Caller must be a registered contractor.

The return_url and refresh_url in the onboard request should point to
pages in the Lovable PWA that handle the redirect back from Stripe.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from app.database import get_supabase_admin
from app.dependencies import get_current_user
from app.services.payment_provider import get_escrow_provider

router = APIRouter(tags=["contractor-connect"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class ConnectOnboardRequest(BaseModel):
    return_url: str = Field(
        ...,
        description="URL Stripe redirects the contractor to after onboarding completes.",
    )
    refresh_url: str = Field(
        ...,
        description="URL Stripe redirects to if the onboarding link expires.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_admin()


def _get_contractor_or_403(user_id: str) -> dict:
    """Return the contractors row for this user, or raise 403."""
    res = (
        _db()
        .table("contractors")
        .select("id, business_name")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=403, detail="Only registered contractors can call this endpoint")
    return res.data[0]


def _get_contractor_details(contractor_id: str) -> dict:
    res = (
        _db()
        .table("contractor_details")
        .select("stripe_account_id")
        .eq("id", contractor_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else {}


def _get_user_email(user_id: str) -> str:
    """Fetch the auth user's email via the Supabase admin client."""
    try:
        result = _db().auth.admin.get_user_by_id(user_id)
        return result.user.email or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/me/contractor/connect-onboard", status_code=200)
async def connect_onboard(
    body: ConnectOnboardRequest,
    user=Depends(get_current_user),
):
    """Start or resume Stripe Connect onboarding for a contractor.

    Returns a single-use URL valid for ~5 minutes.  Redirect the contractor
    to this URL immediately — do not cache it.

    Response:
    - onboarding_url:  redirect the contractor here
    - account_id:      Stripe account ID (store for reference; also saved server-side)
    - expires_at:      Unix timestamp when the link expires
    """
    user_id = str(user.id)
    contractor = _get_contractor_or_403(user_id)
    contractor_id = contractor["id"]

    try:
        provider = get_escrow_provider()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    details = _get_contractor_details(contractor_id)
    account_id: str = details.get("stripe_account_id") or ""

    # Create a new Express account if the contractor doesn't have one yet
    if not account_id:
        email = _get_user_email(user_id)
        try:
            result = await provider.create_connect_account(
                email=email,
                metadata={
                    "contractor_id": contractor_id,
                    "business_name": contractor.get("business_name", ""),
                },
            )
        except Exception as exc:
            log.error("connect_account_create_failed", extra={"contractor_id": contractor_id, "error": str(exc)})
            raise HTTPException(status_code=503, detail="Could not create Stripe account — try again shortly")

        account_id = result.account_id

        # Persist the new account ID immediately so it survives a failed link
        _db().table("contractor_details").update(
            {"stripe_account_id": account_id}
        ).eq("id", contractor_id).execute()

        log.info("connect_account_stored", extra={"contractor_id": contractor_id, "account_id": account_id})

    # Generate a fresh Account Link (single-use, ~5 min TTL)
    try:
        link = await provider.create_account_link(
            account_id=account_id,
            return_url=body.return_url,
            refresh_url=body.refresh_url,
        )
    except Exception as exc:
        log.error("account_link_failed", extra={"contractor_id": contractor_id, "error": str(exc)})
        raise HTTPException(status_code=503, detail="Could not generate onboarding link — try again shortly")

    return {
        "onboarding_url": link.url,
        "account_id":     account_id,
        "expires_at":     link.expires_at,
    }


@router.get("/me/contractor/connect-status", status_code=200)
async def connect_status(user=Depends(get_current_user)):
    """Return the contractor's Stripe Connect account status.

    Response:
    - connected:         false if no account linked yet
    - charges_enabled:   true when the account can accept payments
    - payouts_enabled:   true when the account can receive payouts
    - details_submitted: true when onboarding form is complete
    - account_id:        Stripe account ID (null if not connected)
    """
    user_id = str(user.id)
    contractor = _get_contractor_or_403(user_id)
    contractor_id = contractor["id"]

    details = _get_contractor_details(contractor_id)
    account_id: str = details.get("stripe_account_id") or ""

    if not account_id:
        return {
            "connected":         False,
            "charges_enabled":   False,
            "payouts_enabled":   False,
            "details_submitted": False,
            "account_id":        None,
        }

    try:
        provider = get_escrow_provider()
        status = await provider.get_account_status(account_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.error("connect_status_fetch_failed", extra={"contractor_id": contractor_id, "error": str(exc)})
        raise HTTPException(status_code=503, detail="Could not retrieve account status — try again shortly")

    return {
        "connected":         True,
        "charges_enabled":   status.charges_enabled,
        "payouts_enabled":   status.payouts_enabled,
        "details_submitted": status.details_submitted,
        "account_id":        account_id,
    }
