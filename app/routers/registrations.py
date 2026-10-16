"""Event registrations from the Microsoft Forms responses workbook.

Microsoft Forms has no scripting, and the university tenant does not let the
server read the responses workbook directly. Instead an Office Script inside
that workbook (docs/office-script-registrations.ts) sends the whole responses
table here when someone presses "Run". Every sync replaces the stored set, so
deleted or corrected rows in Excel are reflected on the site.
"""
import datetime
import hmac
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.core.database import db

router = APIRouter()

SYNC_TOKEN = os.getenv("REGISTRATIONS_SYNC_TOKEN", "")
MAX_ROWS = 20000

# The script runs in Excel for the web, i.e. from a Microsoft origin.
_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


@router.options("/api/registrations/sync")
async def registrations_sync_preflight():
    return Response(status_code=204, headers=_CORS)


@router.post("/api/registrations/sync")
async def registrations_sync(request: Request):
    if not SYNC_TOKEN:
        return JSONResponse({"ok": False, "error": "REGISTRATIONS_SYNC_TOKEN is not set on the server"},
                            status_code=503, headers=_CORS)
    body = await request.json()
    if not hmac.compare_digest(str(body.get("token", "")), SYNC_TOKEN):
        return JSONResponse({"ok": False, "error": "Wrong token"}, status_code=403, headers=_CORS)

    rows = body.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        return JSONResponse({"ok": False, "error": "rows must be a list"}, status_code=400, headers=_CORS)

    now = datetime.datetime.utcnow().isoformat()
    clean = []
    for r in rows:
        rid = _int(r.get("id"))
        if rid is None:
            continue
        clean.append((
            rid,
            _int(r.get("event_id")),
            str(r.get("event_title") or "").strip()[:300],
            str(r.get("email") or "").strip()[:200],
            str(r.get("name") or "").strip()[:200],
            str(r.get("submitted_at") or "").strip()[:40],
            now,
        ))

    with db() as c:
        c.execute("DELETE FROM event_registrations")
        c.executemany(
            "INSERT OR REPLACE INTO event_registrations "
            "(response_id, event_id, event_title, email, name, submitted_at, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            clean,
        )
        known = {r[0] for r in c.execute("SELECT id FROM events").fetchall()}

    unknown = sorted({row[1] for row in clean if row[1] is not None and row[1] not in known})
    return JSONResponse(
        {"ok": True, "saved": len(clean), "unknown_event_ids": unknown},
        headers=_CORS,
    )
