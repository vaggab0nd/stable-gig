"""Web Push notification service.

Sends browser/PWA push notifications to contractors when a new job matching
their activity categories becomes available.

Uses pywebpush (RFC 8030 + VAPID RFC 8292) for delivery.

VAPID keys must be set in environment / GCP Secret Manager:
  VAPID_PRIVATE_KEY    — raw base64url-encoded EC private key
  VAPID_PUBLIC_KEY     — raw base64url-encoded EC public key (sent to browsers)
  VAPID_CLAIMS_EMAIL   — "mailto:admin@example.com" (included in VAPID JWT)

If keys are not configured the service logs a warning and skips sending —
callers are never blocked.
"""

import json
import logging

from app.config import settings
from app.database import get_supabase_admin

log = logging.getLogger(__name__)

_MISSING_VAPID_WARNED = False  # warn once per process start


def _vapid_configured() -> bool:
    return bool(
        settings.vapid_private_key
        and settings.vapid_public_key
        and settings.vapid_claims_email
    )


def _send_one(subscription: dict, payload: dict) -> bool:
    """Send a single push notification.  Returns True on success."""
    from pywebpush import webpush, WebPushException  # type: ignore

    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": {
                    "p256dh": subscription["p256dh"],
                    "auth":   subscription["auth_key"],
                },
            },
            data=json.dumps(payload),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={
                "sub": settings.vapid_claims_email,
            },
        )
        return True
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None) if exc.response else None
        log.warning(
            "push_send_failed",
            extra={
                "endpoint": subscription.get("endpoint", "")[:60],
                "status":   status,
                "error":    str(exc),
            },
        )
        # 404 / 410 → subscription expired; caller should remove it
        return False


def _remove_dead_subscription(subscription_id: str) -> None:
    try:
        get_supabase_admin().table("push_subscriptions").delete().eq("id", subscription_id).execute()
    except Exception as exc:
        log.warning("push_remove_dead_failed", extra={"id": subscription_id, "error": str(exc)})


async def notify_contractors_of_new_job(job: dict) -> None:
    """Find contractors whose activities include job['activity'] and push-notify them.

    Called as a FastAPI BackgroundTask — failures are logged and swallowed so
    the HTTP response is never affected.
    """
    global _MISSING_VAPID_WARNED

    if not _vapid_configured():
        if not _MISSING_VAPID_WARNED:
            log.warning(
                "push_vapid_not_configured",
                extra={"hint": "Set VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_CLAIMS_EMAIL"},
            )
            _MISSING_VAPID_WARNED = True
        return

    activity = job.get("activity", "")
    job_id   = job.get("id", "")
    title    = job.get("title", "New job available")
    postcode = job.get("postcode", "")

    db = get_supabase_admin()

    # 1. Find contractors whose activities array contains this job's activity
    try:
        contractor_res = (
            db
            .table("contractors")
            .select("id, user_id")
            .contains("activities", [activity])
            .execute()
        )
    except Exception as exc:
        log.error("push_contractor_lookup_failed", extra={"job_id": job_id, "error": str(exc)})
        return

    if not contractor_res.data:
        log.info("push_no_matching_contractors", extra={"job_id": job_id, "activity": activity})
        return

    user_ids = [c["user_id"] for c in contractor_res.data]

    # 2. Fetch their push subscriptions (may be multiple per user)
    try:
        sub_res = (
            db
            .table("push_subscriptions")
            .select("id, user_id, endpoint, p256dh, auth_key")
            .in_("user_id", user_ids)
            .execute()
        )
    except Exception as exc:
        log.error("push_subscription_lookup_failed", extra={"job_id": job_id, "error": str(exc)})
        return

    if not sub_res.data:
        return

    payload = {
        "type":    "new_job",
        "job_id":  job_id,
        "title":   f"New job: {title}",
        "body":    f"{activity.replace('_', ' ').title()} job in {postcode}",
        "url":     f"/jobs/{job_id}",
    }

    sent = failed = 0
    for sub in sub_res.data:
        ok = _send_one(sub, payload)
        if ok:
            sent += 1
        else:
            failed += 1
            # Best-effort cleanup of dead subscriptions
            _remove_dead_subscription(sub["id"])

    log.info(
        "push_notifications_sent",
        extra={"job_id": job_id, "sent": sent, "failed": failed},
    )
