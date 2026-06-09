from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.core.templates import templates

from app.core.database import db

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def library_home(request: Request):
    """Coventry University Kazakhstan Library - Main Website"""
    with db() as c:
        upcoming_events = c.execute("""
            SELECT * FROM events
            WHERE type = 'ANNOUNCEMENT'
               OR event_date IS NULL
               OR event_date = ''
               OR event_date >= datetime('now', 'localtime')
            ORDER BY
                CASE WHEN type = 'ANNOUNCEMENT' THEN 0 ELSE 1 END,
                event_date ASC,
                created_at DESC
            LIMIT 100
        """).fetchall()

        past_events = c.execute("""
            SELECT * FROM events
            WHERE type != 'ANNOUNCEMENT'
              AND event_date IS NOT NULL
              AND event_date != ''
              AND event_date < datetime('now', 'localtime')
            ORDER BY event_date DESC
            LIMIT 100
        """).fetchall()

        vip_guests = c.execute("""
            SELECT * FROM vip_guests
            ORDER BY visit_date DESC, created_at DESC
            LIMIT 12
        """).fetchall()

    return templates.TemplateResponse(request, "home.html", {
        "events": upcoming_events,
        "upcoming_events": upcoming_events,
        "past_events": past_events,
        "vip_guests": vip_guests,
    })
