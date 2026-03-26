"""
Tests for the milestones router.

Coverage
--------
POST /jobs/{job_id}/milestones
  - 201 homeowner creates milestones on awarded job
  - 403 non-owner cannot create
  - 422 job must be awarded or in_progress (open → 422)
  - 500 on DB failure

GET /jobs/{job_id}/milestones
  - homeowner sees milestones with photos
  - accepted contractor sees milestones
  - non-participant gets 403

POST /jobs/{job_id}/milestones/{milestone_id}/photos
  - 201 contractor submits photo, milestone moves to 'submitted'
  - 403 non-contractor cannot submit
  - 403 non-accepted contractor cannot submit
  - 409 approved milestone cannot receive more photos
  - 201 already-submitted milestone stays 'submitted' after second photo

PATCH /jobs/{job_id}/milestones/{milestone_id}
  - homeowner approves → status 'approved'
  - homeowner rejects → status 'rejected'
  - 403 non-owner cannot action
  - 422 only 'submitted' milestones can be actioned
  - 422 invalid action value

No real DB calls are made — get_supabase_admin is patched throughout.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.milestones import router as milestones_router
from app.dependencies import get_current_user

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(milestones_router)

_OWNER_ID           = "aaaaaaaa-0000-0000-0000-000000000001"
_CONTRACTOR_USER_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_CONTRACTOR_ID      = "cccccccc-0000-0000-0000-000000000003"
_OTHER_USER_ID      = "dddddddd-0000-0000-0000-000000000004"

_OWNER_USER      = SimpleNamespace(id=_OWNER_ID)
_CONTRACTOR_USER = SimpleNamespace(id=_CONTRACTOR_USER_ID)
_OTHER_USER      = SimpleNamespace(id=_OTHER_USER_ID)

_AWARDED_JOB = {
    "id": "job-001", "user_id": _OWNER_ID, "status": "awarded",
    "title": "Roof repair", "activity": "roofing", "postcode": "SW1A",
}
_OPEN_JOB = {**_AWARDED_JOB, "status": "open"}

_CONTRACTOR_ROW  = {"id": _CONTRACTOR_ID}
_ACCEPTED_BID    = {"id": "bid-001", "job_id": "job-001", "contractor_id": _CONTRACTOR_ID, "status": "accepted"}

_PENDING_MILESTONE = {
    "id": "ms-001", "job_id": "job-001", "title": "Scaffolding up",
    "description": None, "order_index": 0, "status": "pending",
    "approved_at": None, "created_at": "2026-03-21T10:00:00Z",
}
_SUBMITTED_MILESTONE = {**_PENDING_MILESTONE, "status": "submitted"}
_APPROVED_MILESTONE  = {**_PENDING_MILESTONE, "status": "approved", "approved_at": "2026-03-21T12:00:00Z"}
_REJECTED_MILESTONE  = {**_PENDING_MILESTONE, "status": "rejected"}

_PHOTO_ROW = {
    "id": "ph-001", "milestone_id": "ms-001", "job_id": "job-001",
    "uploaded_by": _CONTRACTOR_USER_ID,
    "image_source": "data:image/jpeg;base64,/9j/abc",
    "note": None, "created_at": "2026-03-21T11:00:00Z",
}


# ---------------------------------------------------------------------------
# DB mock helper
# ---------------------------------------------------------------------------

def _make_db():
    db = MagicMock()
    db.table.return_value  = db
    db.select.return_value = db
    db.insert.return_value = db
    db.update.return_value = db
    db.eq.return_value     = db
    db.in_.return_value    = db
    db.order.return_value  = db
    db.limit.return_value  = db
    db.execute.return_value = MagicMock(data=[])
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def owner_client():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def contractor_client():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def other_client():
    app.dependency_overrides[get_current_user] = lambda: _OTHER_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


_MILESTONES_PAYLOAD = {
    "milestones": [
        {"title": "Scaffolding up", "order_index": 0},
        {"title": "Tiles replaced",  "order_index": 1},
    ]
}


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/milestones
# ---------------------------------------------------------------------------

class TestCreateMilestones:
    def test_homeowner_creates_201(self, owner_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_AWARDED_JOB]),                # job lookup
            MagicMock(data=[_PENDING_MILESTONE]),          # insert
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = owner_client.post(f"/jobs/{_AWARDED_JOB['id']}/milestones", json=_MILESTONES_PAYLOAD)

        assert resp.status_code == 201

    def test_non_owner_403(self, other_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[_AWARDED_JOB])

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = other_client.post(f"/jobs/{_AWARDED_JOB['id']}/milestones", json=_MILESTONES_PAYLOAD)

        assert resp.status_code == 403

    def test_open_job_422(self, owner_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[_OPEN_JOB])

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = owner_client.post(f"/jobs/{_OPEN_JOB['id']}/milestones", json=_MILESTONES_PAYLOAD)

        assert resp.status_code == 422

    def test_db_failure_500(self, owner_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_AWARDED_JOB]),  # job found
            MagicMock(data=[]),              # insert returns nothing
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = owner_client.post(f"/jobs/{_AWARDED_JOB['id']}/milestones", json=_MILESTONES_PAYLOAD)

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/milestones
# ---------------------------------------------------------------------------

class TestListMilestones:
    def test_homeowner_sees_milestones(self, owner_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_AWARDED_JOB]),          # job lookup
            MagicMock(data=[_PENDING_MILESTONE]),    # milestones
            MagicMock(data=[_PHOTO_ROW]),            # photos
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = owner_client.get(f"/jobs/{_AWARDED_JOB['id']}/milestones")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "photos" in data[0]

    def test_accepted_contractor_sees_milestones(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_AWARDED_JOB]),          # job lookup — not owner
            MagicMock(data=[_CONTRACTOR_ROW]),       # contractor lookup
            MagicMock(data=[_ACCEPTED_BID]),         # accepted bid check
            MagicMock(data=[_PENDING_MILESTONE]),    # milestones
            MagicMock(data=[]),                      # photos (empty)
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = contractor_client.get(f"/jobs/{_AWARDED_JOB['id']}/milestones")

        assert resp.status_code == 200

    def test_non_participant_403(self, other_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_AWARDED_JOB]),  # job lookup — not owner
            MagicMock(data=[]),              # not a contractor
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = other_client.get(f"/jobs/{_AWARDED_JOB['id']}/milestones")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/milestones/{milestone_id}/photos
# ---------------------------------------------------------------------------

_PHOTO_PAYLOAD = {"image_source": "data:image/jpeg;base64,/9j/abc", "note": "Roof fully tiled"}


class TestSubmitPhoto:
    def test_contractor_submits_photo_201(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),      # contractor lookup
            MagicMock(data=[_AWARDED_JOB]),         # job lookup
            MagicMock(data=[_PENDING_MILESTONE]),   # milestone lookup
            MagicMock(data=[_ACCEPTED_BID]),        # accepted bid check
            MagicMock(data=[_PHOTO_ROW]),           # insert photo
            MagicMock(data=[_SUBMITTED_MILESTONE]), # update milestone status
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = contractor_client.post(
                f"/jobs/{_AWARDED_JOB['id']}/milestones/{_PENDING_MILESTONE['id']}/photos",
                json=_PHOTO_PAYLOAD,
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["milestone_status"] == "submitted"
        assert "photo" in data

    def test_non_contractor_403(self, owner_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[])  # not a contractor

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = owner_client.post(
                f"/jobs/{_AWARDED_JOB['id']}/milestones/{_PENDING_MILESTONE['id']}/photos",
                json=_PHOTO_PAYLOAD,
            )

        assert resp.status_code == 403

    def test_non_accepted_contractor_403(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),    # contractor lookup
            MagicMock(data=[_AWARDED_JOB]),       # job lookup
            MagicMock(data=[_PENDING_MILESTONE]), # milestone lookup
            MagicMock(data=[]),                   # no accepted bid
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = contractor_client.post(
                f"/jobs/{_AWARDED_JOB['id']}/milestones/{_PENDING_MILESTONE['id']}/photos",
                json=_PHOTO_PAYLOAD,
            )

        assert resp.status_code == 403

    def test_approved_milestone_409(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),     # contractor lookup
            MagicMock(data=[_AWARDED_JOB]),        # job lookup
            MagicMock(data=[_APPROVED_MILESTONE]), # milestone already approved
            MagicMock(data=[_ACCEPTED_BID]),       # accepted bid
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = contractor_client.post(
                f"/jobs/{_AWARDED_JOB['id']}/milestones/{_PENDING_MILESTONE['id']}/photos",
                json=_PHOTO_PAYLOAD,
            )

        assert resp.status_code == 409

    def test_already_submitted_stays_submitted(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),       # contractor lookup
            MagicMock(data=[_AWARDED_JOB]),          # job lookup
            MagicMock(data=[_SUBMITTED_MILESTONE]),  # already submitted
            MagicMock(data=[_ACCEPTED_BID]),         # accepted bid
            MagicMock(data=[_PHOTO_ROW]),            # insert photo
            # No update call since status is already 'submitted'
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = contractor_client.post(
                f"/jobs/{_AWARDED_JOB['id']}/milestones/{_PENDING_MILESTONE['id']}/photos",
                json=_PHOTO_PAYLOAD,
            )

        assert resp.status_code == 201
        assert resp.json()["milestone_status"] == "submitted"


# ---------------------------------------------------------------------------
# PATCH /jobs/{job_id}/milestones/{milestone_id}
# ---------------------------------------------------------------------------

class TestActionMilestone:
    def test_owner_approves(self, owner_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_AWARDED_JOB]),          # job lookup
            MagicMock(data=[_SUBMITTED_MILESTONE]),  # milestone lookup
            MagicMock(data=[_APPROVED_MILESTONE]),   # update
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_AWARDED_JOB['id']}/milestones/{_PENDING_MILESTONE['id']}",
                json={"action": "approve"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_owner_rejects(self, owner_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_AWARDED_JOB]),          # job lookup
            MagicMock(data=[_SUBMITTED_MILESTONE]),  # milestone lookup
            MagicMock(data=[_REJECTED_MILESTONE]),   # update
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_AWARDED_JOB['id']}/milestones/{_PENDING_MILESTONE['id']}",
                json={"action": "reject"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_non_owner_403(self, other_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[_AWARDED_JOB])

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = other_client.patch(
                f"/jobs/{_AWARDED_JOB['id']}/milestones/{_PENDING_MILESTONE['id']}",
                json={"action": "approve"},
            )

        assert resp.status_code == 403

    def test_non_submitted_422(self, owner_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_AWARDED_JOB]),        # job lookup
            MagicMock(data=[_PENDING_MILESTONE]),  # still pending, not submitted
        ]

        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_AWARDED_JOB['id']}/milestones/{_PENDING_MILESTONE['id']}",
                json={"action": "approve"},
            )

        assert resp.status_code == 422

    def test_invalid_action_422(self, owner_client):
        db = _make_db()
        with patch("app.routers.milestones.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_AWARDED_JOB['id']}/milestones/{_PENDING_MILESTONE['id']}",
                json={"action": "maybe"},
            )

        assert resp.status_code == 422
