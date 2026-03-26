"""
Tests for the questions router.

Coverage
--------
POST /jobs/{job_id}/questions
  - 201 when contractor asks a question on an open job
  - 422 when job status is not allowed (draft)
  - 403 when non-contractor tries to ask
  - 404 when job does not exist
  - 500 when DB insert returns no data

GET /jobs/{job_id}/questions
  - owner sees all questions anonymised as "Contractor N"
  - contractor sees only their own questions as "You"
  - non-owner non-contractor gets 403

PATCH /jobs/{job_id}/questions/{question_id}
  - homeowner answers successfully
  - 403 when non-owner tries to answer
  - 409 when question already answered
  - 404 when question does not exist

No real DB calls are made — get_supabase_admin is patched throughout.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.questions import router as questions_router
from app.dependencies import get_current_user

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(questions_router)

_OWNER_ID      = "aaaaaaaa-0000-0000-0000-000000000001"
_CONTRACTOR_USER_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_CONTRACTOR_ID      = "cccccccc-0000-0000-0000-000000000003"
_OTHER_USER_ID      = "dddddddd-0000-0000-0000-000000000004"

_OWNER_USER      = SimpleNamespace(id=_OWNER_ID)
_CONTRACTOR_USER = SimpleNamespace(id=_CONTRACTOR_USER_ID)
_OTHER_USER      = SimpleNamespace(id=_OTHER_USER_ID)

_OPEN_JOB  = {"id": "job-001", "user_id": _OWNER_ID, "status": "open",  "title": "Leaky tap"}
_DRAFT_JOB = {"id": "job-002", "user_id": _OWNER_ID, "status": "draft", "title": "Leaky tap"}

_CONTRACTOR_ROW = {"id": _CONTRACTOR_ID}

_QUESTION = {
    "id":            "q-001",
    "job_id":        "job-001",
    "contractor_id": _CONTRACTOR_ID,
    "question":      "Is there easy access to the stop valve?",
    "answer":        None,
    "answered_at":   None,
    "created_at":    "2026-03-21T10:00:00Z",
}

_ANSWERED_QUESTION = {**_QUESTION, "answer": "Yes, under the sink.", "answered_at": "2026-03-21T11:00:00Z"}


# ---------------------------------------------------------------------------
# DB mock helper
# ---------------------------------------------------------------------------

def _make_db():
    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.insert.return_value = db
    db.update.return_value = db
    db.eq.return_value     = db
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


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/questions
# ---------------------------------------------------------------------------

class TestAskQuestion:
    _VALID = {"question": "Is there easy access to the stop valve under the sink?"}

    def test_contractor_asks_on_open_job_201(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # _get_contractor_id_or_403
            MagicMock(data=[_OPEN_JOB]),        # _get_job_or_404
            MagicMock(data=[_QUESTION]),        # insert
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = contractor_client.post(f"/jobs/{_OPEN_JOB['id']}/questions", json=self._VALID)

        assert resp.status_code == 201
        data = resp.json()
        assert "contractor_id" not in data
        assert data["asked_by"] == "You"

    def test_draft_job_422(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # contractor check
            MagicMock(data=[_DRAFT_JOB]),       # job is draft
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = contractor_client.post(f"/jobs/{_DRAFT_JOB['id']}/questions", json=self._VALID)

        assert resp.status_code == 422

    def test_non_contractor_403(self, owner_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[])  # not a contractor

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = owner_client.post(f"/jobs/{_OPEN_JOB['id']}/questions", json=self._VALID)

        assert resp.status_code == 403

    def test_job_not_found_404(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # contractor check
            MagicMock(data=[]),                 # job not found
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = contractor_client.post("/jobs/no-such-job/questions", json=self._VALID)

        assert resp.status_code == 404

    def test_db_failure_500(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # contractor check
            MagicMock(data=[_OPEN_JOB]),        # job found
            MagicMock(data=[]),                 # insert returns nothing
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = contractor_client.post(f"/jobs/{_OPEN_JOB['id']}/questions", json=self._VALID)

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/questions
# ---------------------------------------------------------------------------

class TestListQuestions:
    def test_owner_sees_all_anonymised(self, owner_client):
        db = _make_db()
        second_q = {**_QUESTION, "id": "q-002", "contractor_id": "other-contractor"}
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),               # job lookup
            MagicMock(data=[_QUESTION, second_q]),     # all questions
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = owner_client.get(f"/jobs/{_OPEN_JOB['id']}/questions")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # No contractor_id in any question
        assert all("contractor_id" not in q for q in data)
        # Stable labels
        assert data[0]["asked_by"] == "Contractor 1"
        assert data[1]["asked_by"] == "Contractor 2"

    def test_owner_same_contractor_gets_same_label(self, owner_client):
        db = _make_db()
        second_q = {**_QUESTION, "id": "q-002"}  # same contractor_id
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),
            MagicMock(data=[_QUESTION, second_q]),
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = owner_client.get(f"/jobs/{_OPEN_JOB['id']}/questions")

        data = resp.json()
        assert data[0]["asked_by"] == data[1]["asked_by"] == "Contractor 1"

    def test_contractor_sees_own_questions(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),          # job lookup — not owner
            MagicMock(data=[_CONTRACTOR_ROW]),    # contractor lookup
            MagicMock(data=[_QUESTION]),          # own questions
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = contractor_client.get(f"/jobs/{_OPEN_JOB['id']}/questions")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["asked_by"] == "You"
        assert "contractor_id" not in data[0]

    def test_non_owner_non_contractor_403(self, other_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),  # job lookup — not owner
            MagicMock(data=[]),           # not a contractor
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = other_client.get(f"/jobs/{_OPEN_JOB['id']}/questions")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PATCH /jobs/{job_id}/questions/{question_id}
# ---------------------------------------------------------------------------

class TestAnswerQuestion:
    _VALID = {"answer": "Yes, the stop valve is accessible under the kitchen sink."}

    def test_owner_answers_200(self, owner_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),          # job lookup
            MagicMock(data=[_QUESTION]),          # question lookup
            MagicMock(data=[_ANSWERED_QUESTION]), # update
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_OPEN_JOB['id']}/questions/{_QUESTION['id']}",
                json=self._VALID,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == _ANSWERED_QUESTION["answer"]
        assert "contractor_id" not in data

    def test_non_owner_403(self, other_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[_OPEN_JOB])

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = other_client.patch(
                f"/jobs/{_OPEN_JOB['id']}/questions/{_QUESTION['id']}",
                json=self._VALID,
            )

        assert resp.status_code == 403

    def test_already_answered_409(self, owner_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),
            MagicMock(data=[_ANSWERED_QUESTION]),  # already has an answer
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_OPEN_JOB['id']}/questions/{_QUESTION['id']}",
                json=self._VALID,
            )

        assert resp.status_code == 409

    def test_question_not_found_404(self, owner_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),  # job found
            MagicMock(data=[]),           # question not found
        ]

        with patch("app.routers.questions.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_OPEN_JOB['id']}/questions/no-such-q",
                json=self._VALID,
            )

        assert resp.status_code == 404
