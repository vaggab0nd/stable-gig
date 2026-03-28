"""Tests for the reviews router.

Coverage
--------
POST /reviews
  - 201 happy path (client reviews contractor)
  - 201 without optional body / private_feedback
  - 409 when reviewer has already reviewed this job
  - 404 when job does not exist
  - 422 when reviewer_role == reviewee_role
  - 422 when reviewee_role is invalid
  - 500 on DB failure
  - private_feedback is stripped from response even when present in DB row

GET /reviews/contractor/{contractor_id}
  - 200 returns list from visible_reviews
  - 401 when unauthenticated

GET /reviews/summary/{contractor_id}
  - 200 returns aggregated averages
  - returns zeros when no reviews exist
  - open endpoint — no auth required
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.reviews import router as reviews_router
from app.dependencies import get_current_user

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(reviews_router)

_CLIENT_ID     = "aaaaaaaa-0000-0000-0000-000000000001"
_CONTRACTOR_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_JOB_ID        = "job-001"

_CLIENT_USER     = SimpleNamespace(id=_CLIENT_ID)
_CONTRACTOR_USER = SimpleNamespace(id=_CONTRACTOR_ID)

_REVIEW_PAYLOAD = {
    "job_id":               _JOB_ID,
    "reviewee_id":          _CONTRACTOR_ID,
    "reviewee_role":        "contractor",
    "reviewer_role":        "client",
    "rating_cleanliness":   4,
    "rating_communication": 5,
    "rating_quality":       4,
    "body":                 "Great work, very tidy.",
    "private_feedback":     "Slightly overcharged.",
}

# DB row as returned by INSERT — includes private_feedback
_REVIEW_ROW = {
    "id":                   "rev-001",
    "job_id":               _JOB_ID,
    "reviewer_id":          _CLIENT_ID,
    "reviewee_id":          _CONTRACTOR_ID,
    "reviewer_role":        "client",
    "reviewee_role":        "contractor",
    "rating_cleanliness":   4,
    "rating_communication": 5,
    "rating_quality":       4,
    "rating":               "4.33",
    "body":                 "Great work, very tidy.",
    "ai_pros_cons":         None,
    "content_visible":      False,
    "reveal_at":            "2026-04-11T10:00:00Z",
    "submitted_at":         "2026-03-28T10:00:00Z",
    "private_feedback":     "Slightly overcharged.",
}

_VISIBLE_REVIEW_ROW = {k: v for k, v in _REVIEW_ROW.items() if k != "private_feedback"}


# ---------------------------------------------------------------------------
# DB mock helper
# ---------------------------------------------------------------------------

def _make_db():
    db = MagicMock()
    db.table.return_value  = db
    db.select.return_value = db
    db.insert.return_value = db
    db.eq.return_value     = db
    db.order.return_value  = db
    db.limit.return_value  = db
    db.not_is.return_value = db
    db.execute.return_value = MagicMock(data=[])
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_auth():
    app.dependency_overrides[get_current_user] = lambda: _CLIENT_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def anon_client():
    yield TestClient(app)


# ---------------------------------------------------------------------------
# POST /reviews
# ---------------------------------------------------------------------------

class TestSubmitReview:
    def test_happy_path_201(self, client_auth):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[{"id": _JOB_ID, "user_id": _CLIENT_ID}]),  # job check
            MagicMock(data=[]),                                          # duplicate check
            MagicMock(data=[_REVIEW_ROW]),                              # insert
        ]

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = client_auth.post("/reviews", json=_REVIEW_PAYLOAD)

        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "rev-001"
        assert data["reviewer_role"] == "client"

    def test_private_feedback_stripped_from_response(self, client_auth):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[{"id": _JOB_ID, "user_id": _CLIENT_ID}]),
            MagicMock(data=[]),
            MagicMock(data=[_REVIEW_ROW]),  # row has private_feedback
        ]

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = client_auth.post("/reviews", json=_REVIEW_PAYLOAD)

        assert resp.status_code == 201
        assert "private_feedback" not in resp.json()

    def test_without_body_201(self, client_auth):
        payload = {**_REVIEW_PAYLOAD, "body": None, "private_feedback": None}
        row = {**_REVIEW_ROW, "body": None, "private_feedback": None}

        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[{"id": _JOB_ID, "user_id": _CLIENT_ID}]),
            MagicMock(data=[]),
            MagicMock(data=[row]),
        ]

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = client_auth.post("/reviews", json=payload)

        assert resp.status_code == 201

    def test_duplicate_review_409(self, client_auth):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[{"id": _JOB_ID, "user_id": _CLIENT_ID}]),   # job found
            MagicMock(data=[{"id": "existing-rev"}]),                    # duplicate found
        ]

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = client_auth.post("/reviews", json=_REVIEW_PAYLOAD)

        assert resp.status_code == 409

    def test_job_not_found_404(self, client_auth):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[])  # job not found

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = client_auth.post("/reviews", json=_REVIEW_PAYLOAD)

        assert resp.status_code == 404

    def test_same_roles_422(self, client_auth):
        payload = {**_REVIEW_PAYLOAD, "reviewer_role": "contractor", "reviewee_role": "contractor"}

        db = _make_db()
        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = client_auth.post("/reviews", json=payload)

        assert resp.status_code == 422

    def test_invalid_reviewee_role_422(self, client_auth):
        payload = {**_REVIEW_PAYLOAD, "reviewee_role": "admin"}

        db = _make_db()
        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = client_auth.post("/reviews", json=payload)

        assert resp.status_code == 422

    def test_db_failure_500(self, client_auth):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[{"id": _JOB_ID, "user_id": _CLIENT_ID}]),
            MagicMock(data=[]),
            MagicMock(data=[]),  # insert returns nothing
        ]

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = client_auth.post("/reviews", json=_REVIEW_PAYLOAD)

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /reviews/contractor/{contractor_id}
# ---------------------------------------------------------------------------

class TestListContractorReviews:
    def test_returns_visible_reviews_200(self, client_auth):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[_VISIBLE_REVIEW_ROW])

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = client_auth.get(f"/reviews/contractor/{_CONTRACTOR_ID}")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["reviewee_role"] == "contractor"

    def test_returns_empty_list_when_none(self, client_auth):
        db = _make_db()
        db.execute.return_value = MagicMock(data=None)  # None → should return []

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = client_auth.get(f"/reviews/contractor/{_CONTRACTOR_ID}")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_requires_auth_401(self, anon_client):
        resp = anon_client.get(f"/reviews/contractor/{_CONTRACTOR_ID}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /reviews/summary/{contractor_id}
# ---------------------------------------------------------------------------

class TestContractorReviewSummary:
    def test_returns_averages_200(self, anon_client):
        rows = [
            {"rating_cleanliness": 4, "rating_communication": 5, "rating_quality": 4, "rating": "4.33"},
            {"rating_cleanliness": 5, "rating_communication": 5, "rating_quality": 5, "rating": "5.00"},
        ]
        db = _make_db()
        db.execute.return_value = MagicMock(data=rows)

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = anon_client.get(f"/reviews/summary/{_CONTRACTOR_ID}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["review_count"] == 2
        assert data["avg_cleanliness"] == 4.5
        assert data["avg_communication"] == 5.0
        assert data["avg_quality"] == 4.5
        # avg_rating uses the 'rating' column (string converted to float)
        assert data["contractor_id"] == _CONTRACTOR_ID

    def test_zeros_when_no_reviews(self, anon_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[])

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = anon_client.get(f"/reviews/summary/{_CONTRACTOR_ID}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["review_count"] == 0
        assert data["avg_rating"] == 0.0
        assert data["avg_cleanliness"] == 0.0

    def test_open_endpoint_no_auth_needed(self, anon_client):
        """Summary is public — contractors show it on their profile."""
        db = _make_db()
        db.execute.return_value = MagicMock(data=[])

        with patch("app.routers.reviews.get_supabase_admin", return_value=db):
            resp = anon_client.get(f"/reviews/summary/{_CONTRACTOR_ID}")

        assert resp.status_code == 200
