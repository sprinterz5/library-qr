from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.core.templates import templates

from app.core.database import db

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def library_home(request: Request):
    """Coventry University Kazakhstan Library - Main Website"""
    # Fetch events
    with db() as c:
        events = c.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT 6").fetchall()
        
    return templates.TemplateResponse("home.html", {"request": request, "events": events})
