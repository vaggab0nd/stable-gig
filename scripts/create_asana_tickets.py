"""
Create TradePhotoAnalyzer Asana tickets in the "Stable Gig" project.

Usage:
  pip install requests
  python scripts/create_asana_tickets.py

The script will:
  1. Look up your workspace and the "Stable Gig" project automatically.
  2. Create a parent Epic task.
  3. Create 6 sub-tasks beneath it.
"""

import sys
import requests

# ── Config ────────────────────────────────────────────────────────────────────
PAT          = "2/1141479882928458/1213647049103694:75f40c3fde53e6a6363f8169193731ad"
PROJECT_NAME = "Stable Gig"

HEADERS = {
    "Authorization": f"Bearer {PAT}",
    "Content-Type":  "application/json",
    "Accept":        "application/json",
}
BASE = "https://app.asana.com/api/1.0"

# ── Ticket definitions ────────────────────────────────────────────────────────
EPIC = {
    "name": "TradePhotoAnalyzer — Integration & Testing",
    "notes": (
        "End-to-end integration of the new POST /analyse/photos backend endpoint "
        "with the Lovable frontend.  Backend is built and deployed; nothing has been "
        "wired up or tested yet.\n\n"
        "Endpoint: POST /analyse/photos\n"
        "Accepts: 1–5 images (HTTPS URLs or base64), customer description, optional trade category.\n"
        "Returns: likely_issue, urgency_score (1–10), required_tools, estimated_parts, "
        "image_feedback (per-image quality flags), token_usage_estimate."
    ),
}

SUBTASKS = [
    {
        "name": "1 · Backend smoke test (local)",
        "notes": (
            "Verify the new endpoint works in isolation before touching the frontend.\n\n"
            "Steps:\n"
            "  • cd backend && pip install -r requirements.txt\n"
            "  • uvicorn main:app --reload --port 8000\n"
            "  • POST /analyse/photos via curl or Postman with:\n"
            "      - 3 real images (at least one URL, one base64)\n"
            "      - description: 'My bathroom tap is dripping from the base'\n"
            "      - trade_category: 'plumbing'\n\n"
            "Acceptance criteria:\n"
            "  ✓ 200 response with all 5 top-level fields populated\n"
            "  ✓ urgency_score is an integer 1–10\n"
            "  ✓ image_feedback array has one entry per submitted image\n"
            "  ✓ Submitting a blurry/low-quality image returns quality: 'blurry' in feedback\n"
            "  ✓ Submitting 0 images or a bad URL returns 422 with a helpful message"
        ),
    },
    {
        "name": "2 · Lovable frontend — wire up the upload UI",
        "notes": (
            "Build the image submission form in Lovable that calls POST /analyse/photos.\n\n"
            "Components needed:\n"
            "  • Image input — accept up to 5 images; support drag-and-drop and URL entry\n"
            "  • Customer description textarea (min 10 chars, max 1000 — validate client-side)\n"
            "  • Trade category selector: plumbing | electrical | structural | damp | roofing | general | (none)\n"
            "  • Submit button — disabled until ≥1 image + description are provided\n"
            "  • Loading spinner shown while the API call is in flight\n\n"
            "API call:\n"
            "  POST <backend-url>/analyse/photos\n"
            "  Body: { images: [...], description: '...', trade_category: '...' }\n\n"
            "Acceptance criteria:\n"
            "  ✓ Form renders and all fields are functional\n"
            "  ✓ POST is fired correctly (check Network tab — correct URL, headers, body)\n"
            "  ✓ Spinner appears on submit and disappears when response arrives"
        ),
    },
    {
        "name": "3 · Lovable frontend — render the analysis response",
        "notes": (
            "Display the structured response from POST /analyse/photos in a clear, "
            "customer-friendly layout.\n\n"
            "Display requirements:\n"
            "  • likely_issue — prominent heading or callout card\n"
            "  • urgency_score — colour-coded badge:\n"
            "      1–3  → green  (Monitor)\n"
            "      4–6  → amber  (Attend soon)\n"
            "      7–10 → red    (Urgent / Safety risk)\n"
            "  • required_tools — bulleted list\n"
            "  • estimated_parts — bulleted list with any specs/sizes highlighted\n"
            "  • image_feedback — show inline below each uploaded image thumbnail:\n"
            "      quality 'blurry' or 'unidentifiable' → amber warning icon + note text\n"
            "      quality 'unsupported' → red error icon + note text\n"
            "  • token_usage_estimate.total_tokens — small grey footnote ('Analysis used X tokens')\n"
            "  • Special case: if likely_issue === 'INSUFFICIENT_EVIDENCE' → show a "
            "    'Please retake photos' prompt with the reasoning from image_feedback\n\n"
            "Acceptance criteria:\n"
            "  ✓ All fields render without crashing on a valid response\n"
            "  ✓ Urgency badge shows correct colour for scores 2, 5, and 9\n"
            "  ✓ INSUFFICIENT_EVIDENCE path renders the retake prompt"
        ),
    },
    {
        "name": "4 · End-to-end test on staging (Cloud Run)",
        "notes": (
            "Run the full flow against the deployed Cloud Run service with the Lovable frontend.\n\n"
            "Pre-conditions:\n"
            "  • Backend deployed to Cloud Run (see CLAUDE.md deploy instructions)\n"
            "  • Lovable frontend pointed at the Cloud Run URL\n\n"
            "Test cases to run manually:\n"
            "  1. Plumbing — 3 photos of a dripping tap; expect urgency ≤ 6\n"
            "  2. Electrical — 2 photos of a scorched socket; expect urgency ≥ 7\n"
            "  3. Roofing — 1 wide shot + 1 close-up of a cracked tile; expect parts list\n"
            "  4. Blurry only — 1 very blurry photo; expect INSUFFICIENT_EVIDENCE + retake prompt\n\n"
            "Acceptance criteria:\n"
            "  ✓ All 4 test cases complete without 500 errors\n"
            "  ✓ Responses are coherent and trade-appropriate\n"
            "  ✓ End-to-end latency < 15 s for a 3-image submission\n"
            "  ✓ Cloud Run logs show photo_analysis_complete entries with token_usage"
        ),
    },
    {
        "name": "5 · Error UX & edge-case testing",
        "notes": (
            "Verify graceful degradation for all known error paths.\n\n"
            "Edge cases to test:\n"
            "  • 6 images submitted → API returns 422; UI shows 'Maximum 5 images' message\n"
            "  • Invalid image URL (404 / unreachable host) → 422 with per-image note in UI\n"
            "  • Image > 20 MB → 422 with size-limit message\n"
            "  • Description < 10 chars → client-side validation blocks submit\n"
            "  • Gemini quota exceeded (simulate by revoking key) → UI shows friendly 429 message\n"
            "  • Network drop mid-request → UI shows retry/contact-support message\n\n"
            "Acceptance criteria:\n"
            "  ✓ No unhandled exceptions — every error path shows a human-readable message\n"
            "  ✓ 422 per-image notes surface correctly in the image_feedback UI component\n"
            "  ✓ Form remains usable after an error (no frozen state)"
        ),
    },
    {
        "name": "6 · Cost monitoring — token usage baseline",
        "notes": (
            "Establish a cost baseline for photo analysis vs. video analysis and "
            "set up lightweight monitoring.\n\n"
            "Tasks:\n"
            "  • Log token_usage_estimate to Supabase alongside each analysis request\n"
            "    (table: analysis_logs, new columns: prompt_tokens, completion_tokens, analysis_type)\n"
            "  • Write a simple Supabase query / view comparing avg tokens per call:\n"
            "    photo analysis vs. video analysis\n"
            "  • Set a GCP budget alert if photo analysis spend exceeds a monthly threshold\n"
            "    (Cloud Billing → Budgets & alerts → filter by Cloud Run service: stable-gig)\n\n"
            "Acceptance criteria:\n"
            "  ✓ token_usage rows appear in Supabase after each photo analysis call\n"
            "  ✓ A query or view returns avg tokens per analysis_type\n"
            "  ✓ GCP budget alert is configured and test-fires at 80 % threshold"
        ),
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def get(path: str) -> dict:
    r = requests.get(f"{BASE}{path}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def post(path: str, payload: dict) -> dict:
    r = requests.post(f"{BASE}{path}", headers=HEADERS, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Identify workspace
    me = get("/users/me")
    workspaces = me["data"]["workspaces"]
    if not workspaces:
        sys.exit("No Asana workspaces found for this PAT.")
    workspace_gid = workspaces[0]["gid"]
    print(f"Workspace: {workspaces[0]['name']}  ({workspace_gid})")

    # 2. Find the "Stable Gig" project
    projects = get(f"/workspaces/{workspace_gid}/projects?limit=100")["data"]
    project = next(
        (p for p in projects if p["name"].strip().lower() == PROJECT_NAME.lower()),
        None,
    )
    if not project:
        available = [p["name"] for p in projects]
        sys.exit(
            f"Project '{PROJECT_NAME}' not found.\n"
            f"Available projects: {available}"
        )
    project_gid = project["gid"]
    print(f"Project:   {project['name']}  ({project_gid})")

    # 3. Create the Epic (parent task)
    print("\nCreating Epic …")
    epic_resp = post("/tasks", {
        "data": {
            "name":      EPIC["name"],
            "notes":     EPIC["notes"],
            "projects":  [project_gid],
            "workspace": workspace_gid,
        }
    })
    epic_gid = epic_resp["data"]["gid"]
    print(f"  ✓ Epic created  → https://app.asana.com/0/{project_gid}/{epic_gid}")

    # 4. Create sub-tasks
    print("\nCreating sub-tasks …")
    for ticket in SUBTASKS:
        resp = post(f"/tasks/{epic_gid}/subtasks", {
            "data": {
                "name":      ticket["name"],
                "notes":     ticket["notes"],
                "workspace": workspace_gid,
            }
        })
        sub_gid = resp["data"]["gid"]
        print(f"  ✓ {ticket['name'][:60]}  → {sub_gid}")

    print(
        f"\nDone! All tickets are live in the '{PROJECT_NAME}' project.\n"
        f"Epic: https://app.asana.com/0/{project_gid}/{epic_gid}"
    )


if __name__ == "__main__":
    main()
