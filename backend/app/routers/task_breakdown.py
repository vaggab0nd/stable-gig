"""POST /analyse/breakdown — Repair task breakdown endpoint.

Accepts the text output from either the video (/analyse) or photo
(/analyse/photos) pipeline and returns a structured JSON array of repair tasks,
each with a title, difficulty level, and estimated duration.

Designed to be called immediately after an analysis response, passing the
description (and optionally the other fields) straight through:

    # After /analyse (video)
    POST /analyse/breakdown
    {
      "description":         "A dripping tap in the kitchen sink...",
      "problem_type":        "plumbing",
      "urgency":             "low",
      "materials_involved":  ["copper pipe", "tap washer"]
    }

    # After /analyse/photos
    POST /analyse/breakdown
    {
      "description":   "Rising damp caused by failed DPC at ground level",
      "urgency":       "high",
      "required_tools": ["damp meter", "cold chisel"]
    }
"""

import logging
from typing import Literal

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import get_optional_user
from app.services import task_breakdown as svc

router = APIRouter(tags=["task_breakdown"])
log    = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class BreakdownRequest(BaseModel):
    description: str = Field(
        ...,
        min_length=10,
        max_length=2_000,
        description=(
            "Plain-English description of the repair. "
            "Pass the 'description' or 'likely_issue' field directly from the analysis response."
        ),
    )
    problem_type: str | None = Field(
        default=None,
        max_length=100,
        description="Optional — 'problem_type' from the video analysis response.",
    )
    urgency: str | None = Field(
        default=None,
        max_length=50,
        description="Optional — 'urgency' from the video analysis response.",
    )
    materials_involved: list[str] | None = Field(
        default=None,
        description="Optional — 'materials_involved' from the video analysis response.",
    )
    required_tools: list[str] | None = Field(
        default=None,
        description="Optional — 'required_tools' from the photo analysis response.",
    )


class RepairTask(BaseModel):
    title: str = Field(description="Short imperative phrase describing the task.")
    difficulty_level: Literal["easy", "medium", "hard"] = Field(
        description=(
            "easy = any competent DIYer; "
            "medium = trade experience required; "
            "hard = specialist knowledge or certification needed."
        )
    )
    estimated_minutes: int = Field(
        ge=1,
        description="Realistic on-site duration in minutes (excludes travel and sourcing).",
    )


class BreakdownResponse(BaseModel):
    tasks: list[RepairTask] = Field(
        description="Ordered list of repair tasks from preparation through to sign-off."
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/analyse/breakdown",
    response_model=BreakdownResponse,
    summary="Break a repair description into ordered tasks",
    description=(
        "Takes the text output from the video or photo analysis pipeline and asks "
        "Claude to decompose it into a chronological, actionable task list. "
        "Each task includes a title, difficulty level, and estimated duration. "
        "Pass the analysis response fields directly — no transformation needed."
    ),
)
async def breakdown(
    body: BreakdownRequest,
    user=Depends(get_optional_user),
):
    user_id = str(user.id) if user else None

    log.info(
        "task_breakdown_request",
        extra={
            "user_id":      user_id,
            "problem_type": body.problem_type,
            "urgency":      body.urgency,
        },
    )

    try:
        tasks = await svc.breakdown(
            description=body.description,
            problem_type=body.problem_type,
            urgency=body.urgency,
            materials_involved=body.materials_involved,
            required_tools=body.required_tools,
        )

    except RuntimeError as exc:
        # API key not configured
        log.error("task_breakdown_config_error", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail=str(exc))

    except ValueError as exc:
        # Unparseable / structurally invalid AI response
        log.warning("task_breakdown_parse_error", extra={"user_id": user_id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail="AI returned an unexpected response format. Please try again.",
        )

    except anthropic.RateLimitError:
        log.warning("task_breakdown_rate_limit", extra={"user_id": user_id})
        raise HTTPException(
            status_code=429,
            detail="Claude API rate limit reached. Please try again shortly.",
        )

    except anthropic.APIError as exc:
        log.error("task_breakdown_api_error", extra={"user_id": user_id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail="Upstream AI service error. Please try again.",
        )

    return {"tasks": tasks}
