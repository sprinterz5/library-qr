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
        # SQLite date('now') returns YYYY-MM-DD.
        # Logic: Events in future OR Announcements (persistent)
        events = c.execute("""
            SELECT * FROM events 
            WHERE event_date >= date('now') 
               OR type = 'ANNOUNCEMENT' 
            ORDER BY event_date ASC 
            LIMIT 10
        """).fetchall()
        
    return templates.TemplateResponse("home.html", {"request": request, "events": events})
