from fastapi import APIRouter, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from urllib.parse import urlencode
from app.core.templates import templates
from app.core.database import db
from app.core.admin_auth import COOKIE, client_ip, is_admin, pin_ok, record_failure, set_session, too_many_failures
from app.core.rpa import rpa
import datetime
import calendar
import os
from pathlib import Path
import uuid

router = APIRouter()

# Uploads live in data/ (mounted from the host), not in the image: a rebuild
# must not wipe them. Served at the same /static/uploads URL (see app/main.py).
VIP_UPLOAD_DIR = Path(os.getenv("UPLOADS_DIR", "data/uploads")) / "vip_guests"
VIP_UPLOAD_URL = "/static/uploads/vip_guests"
ALLOWED_VIP_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MIN_EVENT_YEAR = 2025

@router.get("/admin", response_class=HTMLResponse)
async def admin_root():
    return RedirectResponse("/admin/returns", status_code=303)

@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, next: str = "/admin/returns", error: str = ""):
    if is_admin(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "admin/login.html", {"next": _safe_next(next), "error": error})

@router.post("/admin/login")
async def admin_login(request: Request, pin: str = Form(""), next: str = Form("/admin/returns")):
    ip = client_ip(request)
    target = _safe_next(next)
    if too_many_failures(ip):
        return RedirectResponse("/admin/login?" + urlencode({"next": target, "error": "locked"}), status_code=303)
    if not pin_ok(pin.strip()):
        record_failure(ip)
        return RedirectResponse("/admin/login?" + urlencode({"next": target, "error": "wrong"}), status_code=303)
    response = RedirectResponse(target, status_code=303)
    set_session(response, request)
    return response

@router.post("/admin/logout")
async def admin_logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(COOKIE, path="/")
    return response

def _safe_next(target: str) -> str:
    """Only local admin paths after login — never //host or a full URL."""
    return target if target.startswith("/admin") and not target.startswith("/admin/login") else "/admin/returns"

@router.get("/admin/returns", response_class=HTMLResponse)
def admin_returns(request: Request):

    with db() as c:
        rows = c.execute("SELECT * FROM return_requests WHERE status='PENDING' ORDER BY id DESC").fetchall()
    
    return templates.TemplateResponse(request, "admin/returns.html", {"rows": rows})

@router.post("/admin/returns/{req_id}/{action}")
async def admin_returns_action(req_id: int, action: str):
    
    action = action.upper()
    if action not in ("APPROVE", "REJECT"):
        return JSONResponse({"ok": False, "error": "Invalid action"}, status_code=400)
    
    # Get request details first
    with db() as c:
        req = c.execute("SELECT * FROM return_requests WHERE id=?", (req_id,)).fetchone()
    
    if not req:
        return JSONResponse({"ok": False, "error": "Request not found"}, status_code=404)
        
    now = datetime.datetime.utcnow().isoformat()
    status = "APPROVED" if action == "APPROVE" else "REJECTED"
    rpa_message = None
    
    # If approving, try to process return via RPA
    if status == "APPROVED":
        barcode = req["barcode"]
        rpa_result = await rpa.return_item(barcode)
        if not rpa_result.get("ok"):
            # If RPA fails, we might still want to approve it manually or reject it?
            # For now, let's just log it and include in response, but still mark as approved
            # OR we could fail the approval? User choice. 
            # Given the user wants it to "do something on rpa side", better to proceed but warn if fail.
            rpa_message = rpa_result.get("message")
    
    with db() as c:
        c.execute("UPDATE return_requests SET status=?, approved_at=?, approved_by='admin' WHERE id=?", (status, now, req_id))
        
    return JSONResponse({"ok": True, "status": status, "rpa_message": rpa_message})

@router.get("/admin/search", response_class=HTMLResponse)
def admin_search(request: Request):
    return templates.TemplateResponse(request, "admin/search.html", {})

@router.get("/admin/stats", response_class=HTMLResponse)
def admin_stats(request: Request):
        
    with db() as c:
        # Stats counts
        total_issued = c.execute("SELECT COUNT(*) FROM issued_books").fetchone()[0]
        total_approved = c.execute("SELECT COUNT(*) FROM return_requests WHERE status='APPROVED'").fetchone()[0]
        total_pending = c.execute("SELECT COUNT(*) FROM return_requests WHERE status='PENDING'").fetchone()[0]
        total_rejected = c.execute("SELECT COUNT(*) FROM return_requests WHERE status='REJECTED'").fetchone()[0]
        
        # Lists
        issued_books = c.execute("SELECT * FROM issued_books ORDER BY id DESC LIMIT 50").fetchall()
        all_returns = c.execute("SELECT * FROM return_requests ORDER BY id DESC LIMIT 50").fetchall()
        
    return templates.TemplateResponse(request, "admin/stats.html", {
        "total_issued": total_issued,
        "total_approved": total_approved,
        "total_pending": total_pending,
        "total_rejected": total_rejected,
        "issued_books": issued_books,
        "all_returns": all_returns
    })

@router.get("/admin/events", response_class=HTMLResponse)
def admin_events(request: Request):
        
    with db() as c:
        events = c.execute("SELECT * FROM events WHERE type != 'BOOK_CLUB' ORDER BY created_at DESC").fetchall()
        book_club_events = c.execute("SELECT * FROM events WHERE type = 'BOOK_CLUB' ORDER BY created_at DESC").fetchall()
        vip_guests = c.execute("SELECT * FROM vip_guests ORDER BY visit_date DESC, created_at DESC").fetchall()
        settings_rows = c.execute("SELECT key, value FROM book_club_settings").fetchall()
        book_club_settings = {row["key"]: row["value"] for row in settings_rows}
        
    return templates.TemplateResponse(request, "admin/events.html", {
        "events": events,
        "book_club_events": book_club_events,
        "vip_guests": vip_guests,
        "book_club_settings": book_club_settings
    })

@router.post("/admin/book-club/settings")
async def admin_book_club_settings_update(
    title: str = Form(...),
    subtitle: str = Form(""),
    instagram_url: str = Form(""),
    email: str = Form(""),
    contact_title: str = Form(""),
    contact_text: str = Form("")
):

    settings = {
        "title": title.strip() or "Book Club's Page",
        "subtitle": subtitle.strip(),
        "instagram_url": instagram_url.strip(),
        "email": email.strip(),
        "contact_title": contact_title.strip() or "Need More Information?",
        "contact_text": contact_text.strip(),
    }

    with db() as c:
        for key, value in settings.items():
            c.execute(
                """
                INSERT INTO book_club_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value)
            )

    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/admin/events" />')

@router.post("/admin/events/add")
async def admin_events_add(
    request: Request,
    title: str = Form(...),
    type: str = Form(...),
    description: str = Form(""),
    location: str = Form(""),
    date_display: str = Form(""),
    event_date: str | None = Form(None),  # Optional
    event_month: str | None = Form(None),  # Optional YYYY-MM when exact date is unknown
    event_month_value: str = Form(""),
    event_year_value: str = Form(""),
    registration_link: str = Form(""), # Optional override
    color: str = Form("var(--primary)")
):

    if type == "BOOK_CLUB":
        return HTMLResponse("<h3>Please use the separate Book Club Event form.</h3>", status_code=400)
    
    now = datetime.datetime.utcnow().isoformat()
    if event_date:
        try:
            parsed_event_date = datetime.datetime.fromisoformat(event_date)
        except ValueError:
            return HTMLResponse("<h3>Invalid event date.</h3>", status_code=400)
        if parsed_event_date.year < MIN_EVENT_YEAR:
            return HTMLResponse("<h3>Event date must be 2025 or later.</h3>", status_code=400)
    elif event_month_value or event_year_value:
        if not event_month_value or not event_year_value:
            return HTMLResponse("<h3>Please select both event month and event year.</h3>", status_code=400)
        try:
            year = int(event_year_value)
            month = int(event_month_value)
            if year < MIN_EVENT_YEAR:
                return HTMLResponse("<h3>Event year must be 2025 or later.</h3>", status_code=400)
            last_day = calendar.monthrange(year, month)[1]
            event_date = f"{year:04d}-{month:02d}-{last_day:02d}T23:59"
            if not date_display:
                date_display = datetime.date(year, month, 1).strftime("%B %Y")
        except ValueError:
            return HTMLResponse("<h3>Invalid event month or year.</h3>", status_code=400)
    elif event_month:
        try:
            year, month = [int(part) for part in event_month.split("-", 1)]
            if year < MIN_EVENT_YEAR:
                return HTMLResponse("<h3>Event month must be 2025 or later.</h3>", status_code=400)
            last_day = calendar.monthrange(year, month)[1]
            event_date = f"{year:04d}-{month:02d}-{last_day:02d}T23:59"
            if not date_display:
                date_display = datetime.date(year, month, 1).strftime("%B %Y")
        except ValueError:
            return HTMLResponse("<h3>Invalid event month.</h3>", status_code=400)
    elif not event_date:
        event_date = now

    with db() as c:
        c.execute(
            "INSERT INTO events (title, type, description, location, date_display, event_date, registration_link, color, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, type, description, location, date_display, event_date, registration_link, color, now)
        )
    
    # Redirect back to events page
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/admin/events" />')

@router.post("/admin/events/delete/{event_id}")
async def admin_events_delete(event_id: int):
        
    with db() as c:
        c.execute("DELETE FROM events WHERE id=?", (event_id,))
        
    return JSONResponse({"ok": True})

@router.post("/admin/book-club/events/add")
async def admin_book_club_events_add(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    location: str = Form(""),
    date_display: str = Form(""),
    event_date: str | None = Form(None),
    event_month: str | None = Form(None),
    event_month_value: str = Form(""),
    event_year_value: str = Form(""),
    registration_link: str = Form(""),
    color: str = Form("var(--primary)")
):

    now = datetime.datetime.utcnow().isoformat()
    if event_date:
        try:
            parsed_event_date = datetime.datetime.fromisoformat(event_date)
        except ValueError:
            return HTMLResponse("<h3>Invalid event date.</h3>", status_code=400)
        if parsed_event_date.year < MIN_EVENT_YEAR:
            return HTMLResponse("<h3>Event date must be 2025 or later.</h3>", status_code=400)
    elif event_month_value or event_year_value:
        if not event_month_value or not event_year_value:
            return HTMLResponse("<h3>Please select both event month and event year.</h3>", status_code=400)
        try:
            year = int(event_year_value)
            month = int(event_month_value)
            if year < MIN_EVENT_YEAR:
                return HTMLResponse("<h3>Event year must be 2025 or later.</h3>", status_code=400)
            last_day = calendar.monthrange(year, month)[1]
            event_date = f"{year:04d}-{month:02d}-{last_day:02d}T23:59"
            if not date_display:
                date_display = datetime.date(year, month, 1).strftime("%B %Y")
        except ValueError:
            return HTMLResponse("<h3>Invalid event month or year.</h3>", status_code=400)
    elif event_month:
        try:
            year, month = [int(part) for part in event_month.split("-", 1)]
            if year < MIN_EVENT_YEAR:
                return HTMLResponse("<h3>Event month must be 2025 or later.</h3>", status_code=400)
            last_day = calendar.monthrange(year, month)[1]
            event_date = f"{year:04d}-{month:02d}-{last_day:02d}T23:59"
            if not date_display:
                date_display = datetime.date(year, month, 1).strftime("%B %Y")
        except ValueError:
            return HTMLResponse("<h3>Invalid event month.</h3>", status_code=400)
    elif not event_date:
        event_date = now

    with db() as c:
        c.execute(
            "INSERT INTO events (title, type, description, location, date_display, event_date, registration_link, color, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, "BOOK_CLUB", description, location, date_display, event_date, registration_link, color, now)
        )

    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/admin/events" />')

@router.post("/admin/vip-guests/add")
async def admin_vip_guests_add(
    request: Request,
    name: str = Form(...),
    visit_date: str = Form(...),
    photo: UploadFile = File(...)
):

    original_name = photo.filename or ""
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_VIP_IMAGE_EXTENSIONS:
        return HTMLResponse("<h3>Unsupported image format. Use JPG, PNG, WEBP, or GIF.</h3>", status_code=400)

    contents = await photo.read()
    if not contents:
        return HTMLResponse("<h3>Please upload a photo.</h3>", status_code=400)
    if len(contents) > 5 * 1024 * 1024:
        return HTMLResponse("<h3>Photo is too large. Maximum size is 5MB.</h3>", status_code=400)

    VIP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{extension}"
    destination = VIP_UPLOAD_DIR / filename
    destination.write_bytes(contents)

    now = datetime.datetime.utcnow().isoformat()
    photo_url = f"{VIP_UPLOAD_URL}/{filename}"
    with db() as c:
        c.execute(
            "INSERT INTO vip_guests (name, visit_date, photo_url, created_at) VALUES (?, ?, ?, ?)",
            (name.strip(), visit_date, photo_url, now)
        )

    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/admin/events" />')

@router.post("/admin/vip-guests/delete/{guest_id}")
async def admin_vip_guests_delete(guest_id: int):

    with db() as c:
        guest = c.execute("SELECT * FROM vip_guests WHERE id=?", (guest_id,)).fetchone()
        if guest:
            c.execute("DELETE FROM vip_guests WHERE id=?", (guest_id,))

    if guest and guest["photo_url"].startswith(VIP_UPLOAD_URL + "/"):
        photo_path = VIP_UPLOAD_DIR / Path(guest["photo_url"]).name
        try:
            os.remove(photo_path)
        except OSError:
            pass

    return JSONResponse({"ok": True})
