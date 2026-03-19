"""
Tests for POST /analyse/breakdown  (task_breakdown router + service).

Coverage
--------
Router (integration via TestClient):
  - Happy path returns tasks list with correct shape
  - 422 when description is too short or too long
  - 422 when description is missing
  - 429 on Gemini quota/rate-limit error
  - 502 on generic upstream error
  - 502 when service raises ValueError (bad AI response)
  - Optional fields (problem_type, urgency, materials, tools) are accepted

Service (unit):
  - breakdown() raises ValueError on non-JSON Gemini response
  - breakdown() raises ValueError when tasks key is missing
  - breakdown() raises ValueError when a task is missing required fields
  - breakdown() raises ValueError on invalid difficulty_level
  - breakdown() coerces float estimated_minutes to int
  - breakdown() strips markdown fences before parsing
  - breakdown() returns validated task list on success

Prompt builder (unit):
  - _build_prompt includes the description
  - _build_prompt includes all supplied context fields

No real API calls are made — google.generativeai is patched throughout
(conftest.py already stubs the entire google.generativeai namespace).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# conftest.py has already populated all required os.environ defaults and
# stubbed google.generativeai before any app module is imported.
from app.routers.task_breakdown import router
from app.dependencies import get_optional_user

# ---------------------------------------------------------------------------
# Minimal test app (avoids the main.py circular-import issue with limiter)
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(router)


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_VALID_BODY = {
    "description": "A dripping tap in the kitchen sink that has been getting worse over two weeks",
}

_TASKS_LIST = [
    {"title": "Shut off water supply under the sink", "difficulty_level": "easy",   "estimated_minutes": 5},
    {"title": "Remove tap handle and packing nut",    "difficulty_level": "medium", "estimated_minutes": 15},
    {"title": "Replace worn tap washer",              "difficulty_level": "easy",   "estimated_minutes": 10},
    {"title": "Reassemble and test for leaks",        "difficulty_level": "easy",   "estimated_minutes": 10},
]

_GEMINI_JSON = '{"tasks": [' + ", ".join(
    f'{{"title": "{t["title"]}", "difficulty_level": "{t["difficulty_level"]}", "estimated_minutes": {t["estimated_minutes"]}}}'
    for t in _TASKS_LIST
) + ']}'


def _mock_genai(response_text: str):
    """Patch google.generativeai so _call_gemini() returns *response_text*."""
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response
    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model
    return mock_genai


@pytest.fixture(autouse=True)
def override_auth():
    """Remove the auth dependency so no Supabase call is attempted."""
    app.dependency_overrides[get_optional_user] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def mock_genai():
    """Patch genai in the service module with a happy-path response."""
    with patch("app.services.task_breakdown.genai", _mock_genai(_GEMINI_JSON)) as m:
        yield m


# ===========================================================================
# Router integration tests
# ===========================================================================

class TestBreakdownEndpointHappyPath:
    def test_returns_200_with_tasks(self, client, mock_genai):
        resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert len(data["tasks"]) == len(_TASKS_LIST)

    def test_task_shape(self, client, mock_genai):
        resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 200
        for task in resp.json()["tasks"]:
            assert set(task.keys()) == {"title", "difficulty_level", "estimated_minutes"}
            assert task["difficulty_level"] in {"easy", "medium", "hard"}
            assert isinstance(task["estimated_minutes"], int)
            assert task["estimated_minutes"] > 0
            assert isinstance(task["title"], str) and task["title"]

    def test_optional_context_fields_accepted(self, client, mock_genai):
        body = {
            **_VALID_BODY,
            "problem_type":       "plumbing",
            "urgency":            "medium",
            "materials_involved": ["copper pipe", "tap washer"],
            "required_tools":     ["adjustable spanner"],
        }
        resp = client.post("/analyse/breakdown", json=body)
        assert resp.status_code == 200


class TestBreakdownEndpointValidation:
    def test_description_too_short(self, client, mock_genai):
        resp = client.post("/analyse/breakdown", json={"description": "short"})
        assert resp.status_code == 422

    def test_description_too_long(self, client, mock_genai):
        resp = client.post("/analyse/breakdown", json={"description": "x" * 2_001})
        assert resp.status_code == 422

    def test_missing_description(self, client, mock_genai):
        resp = client.post("/analyse/breakdown", json={})
        assert resp.status_code == 422


class TestBreakdownEndpointErrorMapping:
    def test_429_on_quota_error(self, client):
        with patch("app.services.task_breakdown.genai", _mock_genai("")):
            with patch(
                "app.services.task_breakdown._call_gemini",
                side_effect=Exception("429: Gemini API quota exceeded"),
            ):
                resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 429

    def test_502_on_upstream_error(self, client):
        with patch("app.services.task_breakdown.genai", _mock_genai("")):
            with patch(
                "app.services.task_breakdown._call_gemini",
                side_effect=Exception("Internal server error"),
            ):
                resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 502

    def test_502_on_parse_error(self, client):
        with patch(
            "app.services.task_breakdown.genai",
            _mock_genai("not valid json at all"),
        ):
            resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 502


# ===========================================================================
# Service unit tests
# ===========================================================================

class TestBreakdownServiceValidation:
    @pytest.mark.asyncio
    async def test_raises_value_error_on_non_json(self):
        from app.services.task_breakdown import breakdown
        with patch("app.services.task_breakdown.genai", _mock_genai("Here are your tasks: blah")):
            with pytest.raises(ValueError, match="non-JSON"):
                await breakdown("A dripping tap in the kitchen that needs fixing")

    @pytest.mark.asyncio
    async def test_raises_value_error_when_tasks_key_missing(self):
        from app.services.task_breakdown import breakdown
        with patch("app.services.task_breakdown.genai", _mock_genai('{"steps": []}')):
            with pytest.raises(ValueError, match="tasks"):
                await breakdown("A dripping tap in the kitchen that needs fixing")

    @pytest.mark.asyncio
    async def test_raises_value_error_on_empty_tasks(self):
        from app.services.task_breakdown import breakdown
        with patch("app.services.task_breakdown.genai", _mock_genai('{"tasks": []}')):
            with pytest.raises(ValueError, match="tasks"):
                await breakdown("A dripping tap in the kitchen that needs fixing")

    @pytest.mark.asyncio
    async def test_raises_value_error_on_invalid_difficulty(self):
        from app.services.task_breakdown import breakdown
        bad = '[{"title": "Do the thing", "difficulty_level": "extreme", "estimated_minutes": 10}]'
        with patch("app.services.task_breakdown.genai", _mock_genai(f'{{"tasks": {bad}}}')):
            with pytest.raises(ValueError, match="difficulty_level"):
                await breakdown("A dripping tap in the kitchen that needs fixing")

    @pytest.mark.asyncio
    async def test_coerces_float_minutes_to_int(self):
        from app.services.task_breakdown import breakdown
        float_task = '[{"title": "Fix the tap", "difficulty_level": "easy", "estimated_minutes": 15.7}]'
        with patch("app.services.task_breakdown.genai", _mock_genai(f'{{"tasks": {float_task}}}')):
            tasks = await breakdown("A dripping tap in the kitchen that needs fixing")
        assert tasks[0]["estimated_minutes"] == 15
        assert isinstance(tasks[0]["estimated_minutes"], int)

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        from app.services.task_breakdown import breakdown
        fenced = f"```json\n{_GEMINI_JSON}\n```"
        with patch("app.services.task_breakdown.genai", _mock_genai(fenced)):
            tasks = await breakdown("A dripping tap in the kitchen that needs fixing")
        assert len(tasks) == len(_TASKS_LIST)

    @pytest.mark.asyncio
    async def test_returns_correct_task_count_and_shape(self):
        from app.services.task_breakdown import breakdown
        with patch("app.services.task_breakdown.genai", _mock_genai(_GEMINI_JSON)):
            tasks = await breakdown(
                "A dripping tap in the kitchen that needs fixing",
                problem_type="plumbing",
                urgency="low",
                materials_involved=["tap washer"],
                required_tools=["adjustable spanner"],
            )
        assert len(tasks) == len(_TASKS_LIST)
        for task in tasks:
            assert "title" in task
            assert "difficulty_level" in task
            assert "estimated_minutes" in task
            assert task["difficulty_level"] in {"easy", "medium", "hard"}


# ===========================================================================
# Prompt builder unit tests
# ===========================================================================

class TestBreakdownServicePrompt:
    def test_prompt_includes_description(self):
        from app.services.task_breakdown import _build_prompt
        prompt = _build_prompt("Leaking roof above the bedroom", None, None, None, None)
        assert "Leaking roof above the bedroom" in prompt

    def test_prompt_includes_all_context(self):
        from app.services.task_breakdown import _build_prompt
        prompt = _build_prompt(
            "Damp patch on wall",
            problem_type="damp",
            urgency="high",
            materials=["DPC membrane"],
            tools=["damp meter"],
        )
        assert "damp" in prompt
        assert "high" in prompt
        assert "DPC membrane" in prompt
        assert "damp meter" in prompt
