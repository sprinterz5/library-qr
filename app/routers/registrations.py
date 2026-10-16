"""Event registrations.

Visitors register on the site (/events/{id}/register): name and email, the
event comes from the URL, and it shows up in the admin Events page at once.

Older registrations came from a Microsoft Form; its Excel can still be pushed
here with the Office Script (docs/office-script-registrations.ts). Those rows
are kept apart (source='forms') so a sync never touches site registrations.
"""
import datetime
import hmac
import os
import re
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.core.admin_auth import client_ip
from app.core.database import db
from app.core.templates import templates

router = APIRouter()

SYNC_TOKEN = os.getenv("REGISTRATIONS_SYNC_TOKEN", "")
MAX_ROWS = 20000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Per-IP cap on new sign-ups, against scripted spam (process memory is enough:
# the app is a single uvicorn process).
_SIGNUP_LIMIT = 20
_SIGNUP_WINDOW = 3600
_signups_by_ip: dict[str, list[float]] = {}


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _event(event_id: int):
    with db() as c:
        return c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()


def _closed_reason(event) -> str | None:
    """Why registration is closed, or None if it's open."""
    if event is None:
        return "This event doesn't exist (any more)."
    if (event["type"] or "").upper() == "ANNOUNCEMENT":
        return "This is an announcement — no registration needed."
    end = event["event_end_date"] or event["event_date"]
    if end:
        try:
            if datetime.datetime.fromisoformat(end) < datetime.datetime.now():
                return "This event has already taken place."
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------- public form

@router.get("/events/{event_id}/register", response_class=HTMLResponse)
def register_page(request: Request, event_id: int, done: int = 0):
    event = _event(event_id)
    return templates.TemplateResponse(request, "register.html", {
        "event": event,
        "closed": _closed_reason(event),
        "done": bool(done),
        "error": None,
        "form": {},
    }, status_code=200 if event else 404)


@router.post("/events/{event_id}/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    event_id: int,
    name: str = Form(""),
    email: str = Form(""),
    website: str = Form(""),  # honeypot: hidden from people, filled by bots
):
    event = _event(event_id)
    closed = _closed_reason(event)
    name, email = name.strip()[:200], email.strip().lower()[:200]

    def again(error: str, status: int = 400):
        return templates.TemplateResponse(request, "register.html", {
            "event": event, "closed": closed, "done": False, "error": error,
            "form": {"name": name, "email": email},
        }, status_code=status)

    if closed:
        return again(closed, 409)
    if website:
        return RedirectResponse(f"/events/{event_id}/register?done=1", status_code=303)
    if not name:
        return again("Please enter your name.")
    if not EMAIL_RE.match(email):
        return again("Please enter a valid email address.")

    ip = client_ip(request)
    now_ts = time.time()
    recent = [t for t in _signups_by_ip.get(ip, []) if now_ts - t < _SIGNUP_WINDOW]
    if len(recent) >= _SIGNUP_LIMIT:
        return again("Too many registrations from this network. Please try again later.", 429)

    with db() as c:
        existing = c.execute(
            "SELECT id FROM event_signups WHERE event_id=? AND lower(email)=?", (event_id, email)
        ).fetchone()
        if existing:
            c.execute("UPDATE event_signups SET name=? WHERE id=?", (name, existing["id"]))
        else:
            c.execute(
                "INSERT INTO event_signups (event_id, event_title, name, email, source, created_at, created_ip) "
                "VALUES (?, ?, ?, ?, 'site', ?, ?)",
                (event_id, event["title"], name, email, datetime.datetime.now().isoformat(timespec="seconds"), ip),
            )
            recent.append(now_ts)
    _signups_by_ip[ip] = recent
    return RedirectResponse(f"/events/{event_id}/register?done=1", status_code=303)


# ------------------------------------------------- Microsoft Forms Excel sync

# The script runs in Excel for the web, i.e. from a Microsoft origin.
_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


@router.options("/api/registrations/sync")
async def registrations_sync_preflight():
    return Response(status_code=204, headers=_CORS)


@router.post("/api/registrations/sync")
async def registrations_sync(request: Request):
    """Replaces the Forms-sourced registrations with the workbook's table."""
    if not SYNC_TOKEN:
        return JSONResponse({"ok": False, "error": "REGISTRATIONS_SYNC_TOKEN is not set on the server"},
                            status_code=503, headers=_CORS)
    body = await request.json()
    if not hmac.compare_digest(str(body.get("token", "")), SYNC_TOKEN):
        return JSONResponse({"ok": False, "error": "Wrong token"}, status_code=403, headers=_CORS)

    rows = body.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        return JSONResponse({"ok": False, "error": "rows must be a list"}, status_code=400, headers=_CORS)

    now = datetime.datetime.now().isoformat(timespec="seconds")
    clean = []
    for r in rows:
        forms_id = _int(r.get("id"))
        if forms_id is None:
            continue
        clean.append((
            _int(r.get("event_id")),
            str(r.get("event_title") or "").strip()[:300],
            str(r.get("name") or "").strip()[:200],
            str(r.get("email") or "").strip()[:200],
            forms_id,
            str(r.get("submitted_at") or "").strip()[:40] or now,
        ))

    with db() as c:
        c.execute("DELETE FROM event_signups WHERE source='forms'")
        c.executemany(
            "INSERT INTO event_signups (event_id, event_title, name, email, source, forms_id, created_at) "
            "VALUES (?, ?, ?, ?, 'forms', ?, ?)",
            clean,
        )
        known = {r[0] for r in c.execute("SELECT id FROM events").fetchall()}

    unknown = sorted({row[0] for row in clean if row[0] is not None and row[0] not in known})
    return JSONResponse({"ok": True, "saved": len(clean), "unknown_event_ids": unknown}, headers=_CORS)
