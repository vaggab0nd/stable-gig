"""
Tests for the Stripe Connect onboarding router.

Coverage
--------
POST /me/contractor/connect-onboard
  - 200 creates new Express account + account link (no existing account_id)
  - 200 skips account creation and issues link for existing account_id
  - 200 response shape: onboarding_url, account_id, expires_at
  - 200 stores new account_id in contractor_details
  - 403 when caller is not a registered contractor
  - 503 when payment provider not configured
  - 503 when Stripe account creation fails
  - 503 when Stripe account link creation fails
  - 401 when unauthenticated

GET  /me/contractor/connect-status
  - 200 returns connected=false when no account_id stored
  - 200 returns full status when account is linked
  - 200 response shape: connected, charges_enabled, payouts_enabled, details_submitted, account_id
  - 403 when caller is not a registered contractor
  - 503 when payment provider not configured
  - 503 when Stripe status fetch fails
  - 401 when unauthenticated

No real DB or Stripe calls — everything is patched.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.modules.setdefault("stripe", MagicMock())

from app.routers.contractor_connect import router as connect_router
from app.dependencies import get_current_user
from app.services.payment_provider import (
    ConnectAccountResult,
    AccountLinkResult,
    AccountStatusResult,
)

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(connect_router)

_CONTRACTOR_USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_OTHER_USER_ID      = "bbbbbbbb-0000-0000-0000-000000000002"
_CONTRACTOR_ID      = "cccccccc-0000-0000-0000-000000000003"
_STRIPE_ACCOUNT_ID  = "acct_test_xxx"

_CONTRACTOR_USER = SimpleNamespace(id=_CONTRACTOR_USER_ID)
_OTHER_USER      = SimpleNamespace(id=_OTHER_USER_ID)

_CONTRACTOR_ROW = {"id": _CONTRACTOR_ID, "business_name": "London Plumbing Co"}

_STUB_CONNECT_RESULT = ConnectAccountResult(account_id=_STRIPE_ACCOUNT_ID)
_STUB_LINK_RESULT    = AccountLinkResult(url="https://connect.stripe.com/setup/xxx", expires_at=9999999999)
_STUB_STATUS_ENABLED = AccountStatusResult(
    account_id=_STRIPE_ACCOUNT_ID,
    charges_enabled=True,
    payouts_enabled=True,
    details_submitted=True,
)
_STUB_STATUS_PENDING = AccountStatusResult(
    account_id=_STRIPE_ACCOUNT_ID,
    charges_enabled=False,
    payouts_enabled=False,
    details_submitted=False,
)

_ONBOARD_BODY = {
    "return_url":  "https://app.example.com/contractor/connect/return",
    "refresh_url": "https://app.example.com/contractor/connect/refresh",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(
    contractor: dict | None = _CONTRACTOR_ROW,
    stripe_account_id: str | None = None,
) -> MagicMock:
    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.eq.return_value = db
    db.limit.return_value = db
    db.update.return_value = db

    # contractors lookup
    contractor_execute = MagicMock()
    contractor_execute.data = [contractor] if contractor else []

    # contractor_details lookup
    details_execute = MagicMock()
    details_execute.data = [{"stripe_account_id": stripe_account_id}] if stripe_account_id is not None else [{"stripe_account_id": None}]

    # Return different results per table call
    call_count = {"n": 0}
    original_table = db.table

    def _table_side_effect(name):
        if name == "contractors":
            m = MagicMock()
            m.select.return_value.eq.return_value.limit.return_value.execute.return_value = contractor_execute
            return m
        if name == "contractor_details":
            m = MagicMock()
            m.select.return_value.eq.return_value.limit.return_value.execute.return_value = details_execute
            m.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            return m
        return MagicMock()

    db.table.side_effect = _table_side_effect

    # auth.admin.get_user_by_id → return email
    db.auth.admin.get_user_by_id.return_value = SimpleNamespace(
        user=SimpleNamespace(email="contractor@example.com")
    )
    return db


def _make_provider(
    connect_result=_STUB_CONNECT_RESULT,
    link_result=_STUB_LINK_RESULT,
    status_result=_STUB_STATUS_ENABLED,
    connect_raises=None,
    link_raises=None,
    status_raises=None,
) -> MagicMock:
    p = MagicMock()
    p.create_connect_account = AsyncMock(
        return_value=connect_result,
        side_effect=connect_raises,
    )
    p.create_account_link = AsyncMock(
        return_value=link_result,
        side_effect=link_raises,
    )
    p.get_account_status = AsyncMock(
        return_value=status_result,
        side_effect=status_raises,
    )
    return p


# ---------------------------------------------------------------------------
# POST /me/contractor/connect-onboard — happy paths
# ---------------------------------------------------------------------------

def test_onboard_creates_account_and_link_when_no_existing_account():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=None)
    provider = _make_provider()

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", return_value=provider):
        resp = TestClient(app).post("/me/contractor/connect-onboard", json=_ONBOARD_BODY)

    assert resp.status_code == 200
    provider.create_connect_account.assert_awaited_once()
    provider.create_account_link.assert_awaited_once()
    app.dependency_overrides.clear()


def test_onboard_skips_account_creation_when_already_exists():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=_STRIPE_ACCOUNT_ID)
    provider = _make_provider()

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", return_value=provider):
        resp = TestClient(app).post("/me/contractor/connect-onboard", json=_ONBOARD_BODY)

    assert resp.status_code == 200
    provider.create_connect_account.assert_not_awaited()
    provider.create_account_link.assert_awaited_once()
    app.dependency_overrides.clear()


def test_onboard_response_shape():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=None)
    provider = _make_provider()

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", return_value=provider):
        data = TestClient(app).post("/me/contractor/connect-onboard", json=_ONBOARD_BODY).json()

    assert data["onboarding_url"] == _STUB_LINK_RESULT.url
    assert data["account_id"]     == _STRIPE_ACCOUNT_ID
    assert data["expires_at"]     == _STUB_LINK_RESULT.expires_at
    app.dependency_overrides.clear()


def test_onboard_stores_new_account_id_in_db():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    provider = _make_provider()

    # Capture the contractor_details mock so we can assert on it after the call
    captured_details_mock = MagicMock()
    captured_details_mock.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[{"stripe_account_id": None}])
    captured_details_mock.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    contractor_execute = MagicMock(data=[_CONTRACTOR_ROW])
    db = MagicMock()
    db.auth.admin.get_user_by_id.return_value = SimpleNamespace(user=SimpleNamespace(email="c@example.com"))

    def _table(name):
        if name == "contractors":
            m = MagicMock()
            m.select.return_value.eq.return_value.limit.return_value.execute.return_value = contractor_execute
            return m
        if name == "contractor_details":
            return captured_details_mock
        return MagicMock()

    db.table.side_effect = _table

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", return_value=provider):
        TestClient(app).post("/me/contractor/connect-onboard", json=_ONBOARD_BODY)

    captured_details_mock.update.assert_called_once_with({"stripe_account_id": _STRIPE_ACCOUNT_ID})
    app.dependency_overrides.clear()


def test_onboard_passes_return_and_refresh_urls_to_provider():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=_STRIPE_ACCOUNT_ID)
    provider = _make_provider()

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", return_value=provider):
        TestClient(app).post("/me/contractor/connect-onboard", json=_ONBOARD_BODY)

    call_kwargs = provider.create_account_link.call_args.kwargs
    assert call_kwargs["return_url"]  == _ONBOARD_BODY["return_url"]
    assert call_kwargs["refresh_url"] == _ONBOARD_BODY["refresh_url"]
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /me/contractor/connect-onboard — errors
# ---------------------------------------------------------------------------

def test_onboard_403_not_a_contractor():
    app.dependency_overrides[get_current_user] = lambda: _OTHER_USER
    db = _make_db(contractor=None)

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db):
        resp = TestClient(app).post("/me/contractor/connect-onboard", json=_ONBOARD_BODY)

    assert resp.status_code == 403
    app.dependency_overrides.clear()


def test_onboard_503_provider_not_configured():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db()

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", side_effect=RuntimeError("not configured")):
        resp = TestClient(app).post("/me/contractor/connect-onboard", json=_ONBOARD_BODY)

    assert resp.status_code == 503
    app.dependency_overrides.clear()


def test_onboard_503_account_creation_fails():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=None)
    provider = _make_provider(connect_raises=Exception("Stripe error"))

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", return_value=provider):
        resp = TestClient(app).post("/me/contractor/connect-onboard", json=_ONBOARD_BODY)

    assert resp.status_code == 503
    app.dependency_overrides.clear()


def test_onboard_503_link_creation_fails():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=_STRIPE_ACCOUNT_ID)
    provider = _make_provider(link_raises=Exception("Stripe network error"))

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", return_value=provider):
        resp = TestClient(app).post("/me/contractor/connect-onboard", json=_ONBOARD_BODY)

    assert resp.status_code == 503
    app.dependency_overrides.clear()


def test_onboard_requires_auth():
    resp = TestClient(app).post("/me/contractor/connect-onboard", json=_ONBOARD_BODY)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /me/contractor/connect-status — happy paths
# ---------------------------------------------------------------------------

def test_status_not_connected_when_no_account():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=None)

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db):
        data = TestClient(app).get("/me/contractor/connect-status").json()

    assert data["connected"]   is False
    assert data["account_id"]  is None
    app.dependency_overrides.clear()


def test_status_connected_and_enabled():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=_STRIPE_ACCOUNT_ID)
    provider = _make_provider(status_result=_STUB_STATUS_ENABLED)

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", return_value=provider):
        data = TestClient(app).get("/me/contractor/connect-status").json()

    assert data["connected"]         is True
    assert data["charges_enabled"]   is True
    assert data["payouts_enabled"]   is True
    assert data["details_submitted"] is True
    assert data["account_id"]        == _STRIPE_ACCOUNT_ID
    app.dependency_overrides.clear()


def test_status_connected_but_onboarding_incomplete():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=_STRIPE_ACCOUNT_ID)
    provider = _make_provider(status_result=_STUB_STATUS_PENDING)

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", return_value=provider):
        data = TestClient(app).get("/me/contractor/connect-status").json()

    assert data["connected"]       is True
    assert data["charges_enabled"] is False
    assert data["payouts_enabled"] is False
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /me/contractor/connect-status — errors
# ---------------------------------------------------------------------------

def test_status_403_not_a_contractor():
    app.dependency_overrides[get_current_user] = lambda: _OTHER_USER
    db = _make_db(contractor=None)

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db):
        resp = TestClient(app).get("/me/contractor/connect-status")

    assert resp.status_code == 403
    app.dependency_overrides.clear()


def test_status_503_provider_not_configured():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=_STRIPE_ACCOUNT_ID)

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", side_effect=RuntimeError("not configured")):
        resp = TestClient(app).get("/me/contractor/connect-status")

    assert resp.status_code == 503
    app.dependency_overrides.clear()


def test_status_503_stripe_fetch_fails():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(stripe_account_id=_STRIPE_ACCOUNT_ID)
    provider = _make_provider(status_raises=Exception("Stripe timeout"))

    with patch("app.routers.contractor_connect.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_connect.get_escrow_provider", return_value=provider):
        resp = TestClient(app).get("/me/contractor/connect-status")

    assert resp.status_code == 503
    app.dependency_overrides.clear()


def test_status_requires_auth():
    resp = TestClient(app).get("/me/contractor/connect-status")
    assert resp.status_code == 401
