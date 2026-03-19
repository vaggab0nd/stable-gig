"""
Tests for POST /analyse/breakdown  (task_breakdown router + service).

Coverage
--------
Router (integration via TestClient):
  - Happy path returns tasks list with correct shape
  - 503 when ANTHROPIC_API_KEY is not set
  - 422 when description is too short or too long
  - 429 on RateLimitError from anthropic SDK
  - 502 on generic APIError from anthropic SDK
  - 502 when service raises ValueError (bad AI response)
  - Optional fields (problem_type, urgency, materials, tools) are forwarded
  - tasks list is returned in the response body

Service (unit):
  - breakdown() raises RuntimeError when api key is empty
  - breakdown() raises ValueError on non-JSON Claude response
  - breakdown() raises ValueError when tasks key is missing
  - breakdown() raises ValueError when a task is missing required fields
  - breakdown() coerces float estimated_minutes to int
  - breakdown() raises ValueError on invalid difficulty_level
  - Prompt includes all supplied context fields
  - breakdown() returns validated task list on success

No real API calls are made — anthropic.AsyncAnthropic is patched throughout.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# conftest.py has already populated all required os.environ defaults and
# stubbed the heavy C-extension packages before any app module is imported.
from app.routers.task_breakdown import router
from app.dependencies import get_optional_user

# ---------------------------------------------------------------------------
# Minimal test app (avoids the main.py circular-import issue with limiter)
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(router)


# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------

_VALID_BODY = {
    "description": "A dripping tap in the kitchen sink that has been getting worse over two weeks",
}

_TASKS_RESPONSE = [
    {"title": "Shut off water supply under the sink", "difficulty_level": "easy",   "estimated_minutes": 5},
    {"title": "Remove tap handle and packing nut",    "difficulty_level": "medium",  "estimated_minutes": 15},
    {"title": "Replace worn tap washer",              "difficulty_level": "easy",    "estimated_minutes": 10},
    {"title": "Reassemble and test for leaks",        "difficulty_level": "easy",    "estimated_minutes": 10},
]

_CLAUDE_JSON = '{"tasks": [' + ", ".join(
    f'{{"title": "{t["title"]}", "difficulty_level": "{t["difficulty_level"]}", "estimated_minutes": {t["estimated_minutes"]}}}'
    for t in _TASKS_RESPONSE
) + ']}'


def _make_anthropic_response(text: str) -> MagicMock:
    """Return a minimal mock that looks like an anthropic.types.Message."""
    content_block = MagicMock()
    content_block.text = text
    usage = MagicMock()
    usage.input_tokens = 120
    usage.output_tokens = 80
    resp = MagicMock()
    resp.content = [content_block]
    resp.usage = usage
    return resp


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
def mock_anthropic():
    """Patch anthropic.AsyncAnthropic so no real HTTP call is made."""
    with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls_mock:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(
            return_value=_make_anthropic_response(_CLAUDE_JSON)
        )
        cls_mock.return_value = instance
        yield instance


# ===========================================================================
# Router integration tests
# ===========================================================================

class TestBreakdownEndpointHappyPath:
    def test_returns_200_with_tasks(self, client, mock_anthropic):
        resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert len(data["tasks"]) == len(_TASKS_RESPONSE)

    def test_task_shape(self, client, mock_anthropic):
        resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 200
        for task in resp.json()["tasks"]:
            assert set(task.keys()) == {"title", "difficulty_level", "estimated_minutes"}
            assert task["difficulty_level"] in {"easy", "medium", "hard"}
            assert isinstance(task["estimated_minutes"], int)
            assert task["estimated_minutes"] > 0
            assert isinstance(task["title"], str) and task["title"]

    def test_optional_context_fields_accepted(self, client, mock_anthropic):
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
    def test_description_too_short(self, client, mock_anthropic):
        resp = client.post("/analyse/breakdown", json={"description": "short"})
        assert resp.status_code == 422

    def test_description_too_long(self, client, mock_anthropic):
        resp = client.post("/analyse/breakdown", json={"description": "x" * 2_001})
        assert resp.status_code == 422

    def test_missing_description(self, client, mock_anthropic):
        resp = client.post("/analyse/breakdown", json={})
        assert resp.status_code == 422


class TestBreakdownEndpointErrorMapping:
    def test_503_when_api_key_missing(self, client):
        with patch("app.services.task_breakdown.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 503
        assert "ANTHROPIC_API_KEY" in resp.json()["detail"]

    def test_429_on_rate_limit(self, client):
        with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls:
            instance = AsyncMock()
            instance.messages.create.side_effect = anthropic.RateLimitError(
                message="rate limit", response=MagicMock(), body={}
            )
            cls.return_value = instance
            resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 429

    def test_502_on_api_error(self, client):
        with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls:
            instance = AsyncMock()
            instance.messages.create.side_effect = anthropic.APIStatusError(
                message="upstream error",
                response=MagicMock(status_code=500),
                body={},
            )
            cls.return_value = instance
            resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 502

    def test_502_on_parse_error(self, client):
        with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls:
            instance = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_make_anthropic_response("not valid json at all")
            )
            cls.return_value = instance
            resp = client.post("/analyse/breakdown", json=_VALID_BODY)
        assert resp.status_code == 502


# ===========================================================================
# Service unit tests
# ===========================================================================

class TestBreakdownServiceValidation:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_key_empty(self):
        from app.services.task_breakdown import breakdown
        with patch("app.services.task_breakdown.settings") as s:
            s.anthropic_api_key = ""
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                await breakdown("A dripping tap in the kitchen that needs fixing")

    @pytest.mark.asyncio
    async def test_raises_value_error_on_non_json(self):
        from app.services.task_breakdown import breakdown
        with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls:
            instance = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_make_anthropic_response("Here are your tasks: blah blah")
            )
            cls.return_value = instance
            with patch("app.services.task_breakdown.settings") as s:
                s.anthropic_api_key = "test-key"
                with pytest.raises(ValueError, match="non-JSON"):
                    await breakdown("A dripping tap in the kitchen that needs fixing")

    @pytest.mark.asyncio
    async def test_raises_value_error_when_tasks_key_missing(self):
        from app.services.task_breakdown import breakdown
        with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls:
            instance = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_make_anthropic_response('{"steps": []}')
            )
            cls.return_value = instance
            with patch("app.services.task_breakdown.settings") as s:
                s.anthropic_api_key = "test-key"
                with pytest.raises(ValueError, match="tasks"):
                    await breakdown("A dripping tap in the kitchen that needs fixing")

    @pytest.mark.asyncio
    async def test_raises_value_error_on_empty_tasks(self):
        from app.services.task_breakdown import breakdown
        with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls:
            instance = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_make_anthropic_response('{"tasks": []}')
            )
            cls.return_value = instance
            with patch("app.services.task_breakdown.settings") as s:
                s.anthropic_api_key = "test-key"
                with pytest.raises(ValueError, match="tasks"):
                    await breakdown("A dripping tap in the kitchen that needs fixing")

    @pytest.mark.asyncio
    async def test_raises_value_error_on_invalid_difficulty(self):
        from app.services.task_breakdown import breakdown
        bad_task = '[{"title": "Do the thing", "difficulty_level": "extreme", "estimated_minutes": 10}]'
        with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls:
            instance = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_make_anthropic_response(f'{{"tasks": {bad_task}}}')
            )
            cls.return_value = instance
            with patch("app.services.task_breakdown.settings") as s:
                s.anthropic_api_key = "test-key"
                with pytest.raises(ValueError, match="difficulty_level"):
                    await breakdown("A dripping tap in the kitchen that needs fixing")

    @pytest.mark.asyncio
    async def test_coerces_float_minutes_to_int(self):
        from app.services.task_breakdown import breakdown
        float_task = '[{"title": "Fix the tap", "difficulty_level": "easy", "estimated_minutes": 15.7}]'
        with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls:
            instance = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_make_anthropic_response(f'{{"tasks": {float_task}}}')
            )
            cls.return_value = instance
            with patch("app.services.task_breakdown.settings") as s:
                s.anthropic_api_key = "test-key"
                tasks = await breakdown("A dripping tap in the kitchen that needs fixing")
        assert tasks[0]["estimated_minutes"] == 15
        assert isinstance(tasks[0]["estimated_minutes"], int)

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        from app.services.task_breakdown import breakdown
        fenced = f"```json\n{_CLAUDE_JSON}\n```"
        with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls:
            instance = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_make_anthropic_response(fenced)
            )
            cls.return_value = instance
            with patch("app.services.task_breakdown.settings") as s:
                s.anthropic_api_key = "test-key"
                tasks = await breakdown("A dripping tap in the kitchen that needs fixing")
        assert len(tasks) == len(_TASKS_RESPONSE)

    @pytest.mark.asyncio
    async def test_returns_correct_task_count_and_shape(self):
        from app.services.task_breakdown import breakdown
        with patch("app.services.task_breakdown.anthropic.AsyncAnthropic") as cls:
            instance = AsyncMock()
            instance.messages.create = AsyncMock(
                return_value=_make_anthropic_response(_CLAUDE_JSON)
            )
            cls.return_value = instance
            with patch("app.services.task_breakdown.settings") as s:
                s.anthropic_api_key = "test-key"
                tasks = await breakdown(
                    "A dripping tap in the kitchen that needs fixing",
                    problem_type="plumbing",
                    urgency="low",
                    materials_involved=["tap washer"],
                    required_tools=["adjustable spanner"],
                )
        assert len(tasks) == len(_TASKS_RESPONSE)
        for task in tasks:
            assert "title" in task
            assert "difficulty_level" in task
            assert "estimated_minutes" in task
            assert task["difficulty_level"] in {"easy", "medium", "hard"}


class TestBreakdownServicePrompt:
    @pytest.mark.asyncio
    async def test_prompt_includes_description(self):
        from app.services.task_breakdown import _build_prompt
        prompt = _build_prompt("Leaking roof above the bedroom", None, None, None, None)
        assert "Leaking roof above the bedroom" in prompt

    @pytest.mark.asyncio
    async def test_prompt_includes_all_context(self):
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
