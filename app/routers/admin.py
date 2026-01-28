from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from app.core.templates import templates
from app.core.database import db
from app.core.config import ADMIN_PIN
from app.core.rpa import rpa
import datetime

router = APIRouter()

@router.get("/admin/returns", response_class=HTMLResponse)
def admin_returns(request: Request, pin: str):
    if pin != ADMIN_PIN:
        return HTMLResponse("<h3>403 Forbidden</h3>", status_code=403)

    with db() as c:
        rows = c.execute("SELECT * FROM return_requests WHERE status='PENDING' ORDER BY id DESC").fetchall()
    
    return templates.TemplateResponse("admin/returns.html", {"request": request, "rows": rows, "pin": pin})

@router.post("/admin/returns/{req_id}/{action}")
async def admin_returns_action(req_id: int, action: str, pin: str = Form(...)):
    if pin != ADMIN_PIN:
        return JSONResponse({"ok": False, "error": "Invalid PIN"}, status_code=403)
    
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
def admin_search(request: Request, pin: str):
    if pin != ADMIN_PIN:
        return HTMLResponse("<h3>403 Forbidden</h3>", status_code=403)
    return templates.TemplateResponse("admin/search.html", {"request": request, "pin": pin})

@router.get("/admin/stats", response_class=HTMLResponse)
def admin_stats(request: Request, pin: str):
    if pin != ADMIN_PIN:
        return HTMLResponse("<h3>403 Forbidden</h3>", status_code=403)
        
    with db() as c:
        # Stats counts
        total_issued = c.execute("SELECT COUNT(*) FROM issued_books").fetchone()[0]
        total_approved = c.execute("SELECT COUNT(*) FROM return_requests WHERE status='APPROVED'").fetchone()[0]
        total_pending = c.execute("SELECT COUNT(*) FROM return_requests WHERE status='PENDING'").fetchone()[0]
        total_rejected = c.execute("SELECT COUNT(*) FROM return_requests WHERE status='REJECTED'").fetchone()[0]
        
        # Lists
        issued_books = c.execute("SELECT * FROM issued_books ORDER BY id DESC LIMIT 50").fetchall()
        all_returns = c.execute("SELECT * FROM return_requests ORDER BY id DESC LIMIT 50").fetchall()
        
    return templates.TemplateResponse("admin/stats.html", {
        "request": request,
        "pin": pin,
        "total_issued": total_issued,
        "total_approved": total_approved,
        "total_pending": total_pending,
        "total_rejected": total_rejected,
        "issued_books": issued_books,
        "all_returns": all_returns
    })

@router.get("/admin/events", response_class=HTMLResponse)
def admin_events(request: Request, pin: str):
    if pin != ADMIN_PIN:
        return HTMLResponse("<h3>403 Forbidden</h3>", status_code=403)
        
    with db() as c:
        events = c.execute("SELECT * FROM events ORDER BY created_at DESC").fetchall()
        
    return templates.TemplateResponse("admin/events.html", {
        "request": request,
        "pin": pin,
        "events": events
    })

@router.post("/admin/events/add")
async def admin_events_add(
    request: Request,
    title: str = Form(...),
    type: str = Form(...),
    description: str = Form(""),
    location: str = Form(""),
    date_display: str = Form(""),
    color: str = Form("var(--primary)"),
    pin: str = Form(...)
):
    if pin != ADMIN_PIN:
        return HTMLResponse("<h3>403 Forbidden</h3>", status_code=403)
    
    now = datetime.datetime.utcnow().isoformat()
    with db() as c:
        c.execute(
            "INSERT INTO events (title, type, description, location, date_display, color, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, type, description, location, date_display, color, now)
        )
    
    # Redirect back to events page
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/admin/events?pin={pin}" />')

@router.post("/admin/events/delete/{event_id}")
async def admin_events_delete(event_id: int, pin: str = Form(...)):
    if pin != ADMIN_PIN:
        return JSONResponse({"ok": False, "error": "Invalid PIN"}, status_code=403)
        
    with db() as c:
        c.execute("DELETE FROM events WHERE id=?", (event_id,))
        
    return JSONResponse({"ok": True})
