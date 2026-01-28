from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.core.templates import templates

from app.core.database import db

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def library_home(request: Request):
    """Coventry University Kazakhstan Library - Main Website"""
    # Fetch events (future only)
    with db() as c:
        # SQLite date('now') returns YYYY-MM-DD. Simple string comparison works for ISO8601 dates.
        events = c.execute("SELECT * FROM events WHERE event_date >= date('now') ORDER BY event_date ASC LIMIT 6").fetchall()
        
    return templates.TemplateResponse("home.html", {"request": request, "events": events})
