"""
Tests for the notifications router.

Coverage
--------
GET /notifications/vapid-public-key
  - 200 returns key when configured
  - 503 when VAPID not configured

POST /notifications/subscribe
  - 201 when subscription upserted successfully
  - 503 when VAPID not configured
  - 500 when DB upsert returns no data
  - 401 when unauthenticated

DELETE /notifications/subscribe
  - 200 on successful unsubscribe
  - 401 when unauthenticated

No real DB calls or push sends are made.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.notifications import router as notifications_router
from app.dependencies import get_current_user

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(notifications_router)

_USER_ID   = "aaaaaaaa-0000-0000-0000-000000000001"
_USER      = SimpleNamespace(id=_USER_ID)

_SUB_PAYLOAD = {
    "endpoint": "https://push.example.com/sub/abc123",
    "p256dh":   "BNFMy...public_key_base64url",
    "auth_key": "auth_secret_base64url",
}

_SUB_ROW = {"id": "sub-001", "user_id": _USER_ID, **_SUB_PAYLOAD}


# ---------------------------------------------------------------------------
# DB mock helper
# ---------------------------------------------------------------------------

def _make_db():
    db = MagicMock()
    db.table.return_value  = db
    db.upsert.return_value = db
    db.delete.return_value = db
    db.eq.return_value     = db
    db.execute.return_value = MagicMock(data=[])
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def auth_client():
    app.dependency_overrides[get_current_user] = lambda: _USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def anon_client():
    yield TestClient(app)


# ---------------------------------------------------------------------------
# GET /notifications/vapid-public-key
# ---------------------------------------------------------------------------

class TestVapidPublicKey:
    def test_returns_key_when_configured(self, auth_client):
        with patch("app.routers.notifications.settings") as mock_settings:
            mock_settings.vapid_public_key = "BNFMy...public_key"
            resp = auth_client.get("/notifications/vapid-public-key")

        assert resp.status_code == 200
        assert resp.json()["vapid_public_key"] == "BNFMy...public_key"

    def test_503_when_not_configured(self, auth_client):
        with patch("app.routers.notifications.settings") as mock_settings:
            mock_settings.vapid_public_key = ""
            resp = auth_client.get("/notifications/vapid-public-key")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /notifications/subscribe
# ---------------------------------------------------------------------------

class TestSubscribe:
    def test_subscribe_201(self, auth_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[_SUB_ROW])

        with patch("app.routers.notifications.settings") as mock_settings, \
             patch("app.routers.notifications.get_supabase_admin", return_value=db):
            mock_settings.vapid_public_key = "BNFMy...public_key"
            resp = auth_client.post("/notifications/subscribe", json=_SUB_PAYLOAD)

        assert resp.status_code == 201
        assert resp.json()["status"] == "subscribed"

    def test_503_when_vapid_not_configured(self, auth_client):
        db = _make_db()
        with patch("app.routers.notifications.settings") as mock_settings, \
             patch("app.routers.notifications.get_supabase_admin", return_value=db):
            mock_settings.vapid_public_key = ""
            resp = auth_client.post("/notifications/subscribe", json=_SUB_PAYLOAD)

        assert resp.status_code == 503

    def test_500_on_db_failure(self, auth_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[])  # upsert returns nothing

        with patch("app.routers.notifications.settings") as mock_settings, \
             patch("app.routers.notifications.get_supabase_admin", return_value=db):
            mock_settings.vapid_public_key = "key"
            resp = auth_client.post("/notifications/subscribe", json=_SUB_PAYLOAD)

        assert resp.status_code == 500

    def test_401_unauthenticated(self, anon_client):
        resp = anon_client.post("/notifications/subscribe", json=_SUB_PAYLOAD)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /notifications/subscribe
# ---------------------------------------------------------------------------

class TestUnsubscribe:
    def test_unsubscribe_200(self, auth_client):
        db = _make_db()

        with patch("app.routers.notifications.get_supabase_admin", return_value=db):
            resp = auth_client.request(
                "DELETE",
                "/notifications/subscribe",
                json=_SUB_PAYLOAD,
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "unsubscribed"

    def test_401_unauthenticated(self, anon_client):
        resp = anon_client.request("DELETE", "/notifications/subscribe", json=_SUB_PAYLOAD)
        assert resp.status_code == 401
