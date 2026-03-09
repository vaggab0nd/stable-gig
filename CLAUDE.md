# CLAUDE.md

## Project overview

Single-service web app: FastAPI backend that also serves the frontend HTML. Gemini 2.0 Flash analyses uploaded home repair videos and returns a structured JSON assessment.

## Running locally

```bash
cd backend
pip install -r requirements.txt
# Requires GEMINI_API_KEY in .env or environment
uvicorn main:app --reload --port 8000
# App is at http://localhost:8000
```

## Key files

| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI app — `GET /` (UI) and `POST /analyse` |
| `backend/requirements.txt` | Python dependencies |
| `backend/static/index.html` | **Deployed** frontend, served by FastAPI |
| `frontend/index.html` | Local dev copy — keep in sync with `backend/static/index.html` |
| `.env.example` | Template for `GEMINI_API_KEY` |

## Architecture notes

- **Single service on Railway**: FastAPI serves both the API and the UI. No separate static hosting.
- **Railway root directory** must be set to `backend/` so Railway finds `requirements.txt`.
- **Frontend duplication**: `frontend/index.html` and `backend/static/index.html` are identical. Edit one, copy to the other. This exists because Railway only deploys the `backend/` subtree.
- **Video metadata**: Extracted locally before uploading to Gemini using `hachoir` (technical metadata) and `mutagen` (embedded MP4 tags). Both libraries fail silently — metadata is best-effort.
- **No tests**: This is a proof-of-concept. There is no test suite.
