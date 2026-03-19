"""Claude-powered repair task breakdown service.

Takes the text output from either analysis pipeline (video or photo) and asks
Claude to decompose it into an ordered, actionable list of repair tasks — each
with a title, difficulty level, and estimated duration in minutes.

The function is async (uses AsyncAnthropic) so it runs on the event loop
without blocking the server.
"""

import json
import logging
import re

import anthropic

from app.config import settings

log = logging.getLogger(__name__)

# Haiku: fast and cheap for a structured text → JSON task; no vision needed here.
_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024

# Difficulty vocabulary exposed in the API response schema.
DIFFICULTY_LEVELS = {"easy", "medium", "hard"}


def _build_prompt(
    description: str,
    problem_type: str | None,
    urgency: str | None,
    materials: list[str] | None,
    tools: list[str] | None,
) -> str:
    context_lines: list[str] = []
    if problem_type:
        context_lines.append(f"Problem type: {problem_type}")
    if urgency:
        context_lines.append(f"Urgency: {urgency}")
    if materials:
        context_lines.append(f"Materials/components involved: {', '.join(materials)}")
    if tools:
        context_lines.append(f"Tools likely required: {', '.join(tools)}")

    context_block = "\n".join(context_lines)
    context_section = f"\nAdditional context:\n{context_block}\n" if context_block else ""

    return f"""You are a professional home repair project planner.

Repair description:
\"\"\"{description}\"\"\"{context_section}
Break this repair into a clear, ordered sequence of practical tasks that a tradesperson would follow on site.

Rules:
- Return ONLY a JSON object — no markdown, no explanation.
- The object must have a single key "tasks" whose value is an array.
- Each task object must have exactly these three fields:
    "title"            — short, imperative phrase (max 10 words)
    "difficulty_level" — one of: "easy", "medium", "hard"
    "estimated_minutes"— positive integer (realistic on-site time, excluding travel)
- Order tasks chronologically (preparation → execution → cleanup/sign-off).
- Include safety/isolation steps first (e.g. isolate power, shut off water) where relevant.
- Minimum 2 tasks, maximum 12 tasks.
- difficulty_level reflects the skill and care required, not just the time:
    easy   — any competent DIYer can do it
    medium — requires trade experience or specific tools
    hard   — specialist knowledge, certification, or significant risk if done wrong

Example output shape (do not copy this content):
{{"tasks": [{{"title": "Turn off water supply", "difficulty_level": "easy", "estimated_minutes": 5}}]}}"""


async def breakdown(
    description: str,
    problem_type: str | None = None,
    urgency: str | None = None,
    materials_involved: list[str] | None = None,
    required_tools: list[str] | None = None,
) -> list[dict]:
    """Call Claude to decompose *description* into repair tasks.

    Returns a list of dicts, each with keys: title, difficulty_level,
    estimated_minutes.

    Raises:
        RuntimeError: if ANTHROPIC_API_KEY is not configured.
        ValueError:   if Claude returns unparseable or structurally invalid JSON.
        anthropic.APIError: on upstream API failures (let the router handle these).
    """
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not configured. "
            "Add it to your .env file or Cloud Run environment."
        )

    prompt = _build_prompt(
        description=description,
        problem_type=problem_type,
        urgency=urgency,
        materials=materials_involved,
        tools=required_tools,
    )

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    message = await client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = (message.content[0].text or "").strip()

    # Strip any accidental markdown fences
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned non-JSON response: {raw[:200]}") from exc

    tasks = parsed.get("tasks")
    if not isinstance(tasks, list) or len(tasks) == 0:
        raise ValueError(f"Claude response missing 'tasks' array: {raw[:200]}")

    validated: list[dict] = []
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"Task {i} is not an object")
        title = task.get("title")
        difficulty = task.get("difficulty_level")
        minutes = task.get("estimated_minutes")

        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Task {i} missing valid 'title'")
        if difficulty not in DIFFICULTY_LEVELS:
            raise ValueError(
                f"Task {i} has invalid difficulty_level {difficulty!r}; "
                f"must be one of {sorted(DIFFICULTY_LEVELS)}"
            )
        if not isinstance(minutes, int) or minutes <= 0:
            # Coerce float → int if Claude slips a decimal in
            try:
                minutes = int(minutes)
                if minutes <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(
                    f"Task {i} has invalid estimated_minutes {minutes!r}"
                )

        validated.append(
            {
                "title": title.strip(),
                "difficulty_level": difficulty,
                "estimated_minutes": minutes,
            }
        )

    log.info(
        "task_breakdown_complete",
        extra={
            "model": _MODEL,
            "task_count": len(validated),
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
    )
    return validated
