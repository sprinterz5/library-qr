import calendar
from datetime import date, datetime

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
            WHERE type != 'BOOK_CLUB'
              AND type != 'ANNOUNCEMENT'
              AND event_date IS NOT NULL
              AND event_date != ''
              AND datetime(COALESCE(NULLIF(event_end_date, ''), event_date)) >= datetime('now', 'localtime')
              AND datetime(event_date) < datetime('now', 'localtime', '+30 days')
            ORDER BY datetime(event_date) ASC
            LIMIT 12
        """).fetchall()

        vip_guests = c.execute("""
            SELECT * FROM vip_guests
            ORDER BY visit_date DESC, created_at DESC
            LIMIT 12
        """).fetchall()

    return templates.TemplateResponse(request, "home.html", {
        "events": upcoming_events,
        "upcoming_events": upcoming_events,
        "vip_guests": vip_guests,
    })


@router.get("/events", response_class=HTMLResponse)
def all_events(request: Request):
    """All library events, organised from this month through May and by past month."""
    today = date.today()
    end_year = today.year if today.month <= 5 else today.year + 1
    future_end = date(end_year, 6, 1)
    month_groups = []
    year, month = today.year, today.month
    while (year, month) <= (end_year, 5):
        month_groups.append({
            "key": f"{year:04d}-{month:02d}",
            "label": f"{calendar.month_name[month]} {year}",
            "events": [],
        })
        month = 1 if month == 12 else month + 1
        if month == 1:
            year += 1

    with db() as c:
        future_events = c.execute("""
            SELECT * FROM events
            WHERE type NOT IN ('ANNOUNCEMENT', 'BOOK_CLUB')
              AND event_date IS NOT NULL AND event_date != ''
              AND datetime(COALESCE(NULLIF(event_end_date, ''), event_date)) >= datetime('now', 'localtime')
              AND date(event_date) < ?
            ORDER BY datetime(event_date) ASC
        """, (future_end.isoformat(),)).fetchall()
        past_events = c.execute("""
            SELECT * FROM events
            WHERE type NOT IN ('ANNOUNCEMENT', 'BOOK_CLUB')
              AND event_date IS NOT NULL AND event_date != ''
              AND datetime(COALESCE(NULLIF(event_end_date, ''), event_date)) < datetime('now', 'localtime')
            ORDER BY datetime(event_date) DESC
        """).fetchall()

    group_by_key = {group["key"]: group for group in month_groups}
    for event in future_events:
        group = group_by_key.get((event["event_date"] or "")[:7])
        if group:
            group["events"].append(event)

    past_groups = []
    for event in past_events:
        event_date = datetime.fromisoformat(event["event_date"])
        key = event_date.strftime("%Y-%m")
        if not past_groups or past_groups[-1]["key"] != key:
            past_groups.append({
                "key": key,
                "label": f"{calendar.month_name[event_date.month]} {event_date.year}",
                "events": [],
            })
        past_groups[-1]["events"].append(event)

    return templates.TemplateResponse(request, "events.html", {
        "month_groups": month_groups,
        "past_groups": past_groups,
    })

@router.get("/book-club", response_class=HTMLResponse)
def book_club(request: Request):
    """Book Club page with upcoming and past club meetings."""
    with db() as c:
        settings_rows = c.execute("SELECT key, value FROM book_club_settings").fetchall()
        settings = {row["key"]: row["value"] for row in settings_rows}

        upcoming_book_club_events = c.execute("""
            SELECT * FROM events
            WHERE type = 'BOOK_CLUB'
              AND (
                event_date IS NULL
                OR event_date = ''
                OR event_date >= datetime('now', 'localtime')
              )
            ORDER BY event_date ASC, created_at DESC
            LIMIT 100
        """).fetchall()

        past_book_club_events = c.execute("""
            SELECT * FROM events
            WHERE type = 'BOOK_CLUB'
              AND event_date IS NOT NULL
              AND event_date != ''
              AND event_date < datetime('now', 'localtime')
            ORDER BY event_date DESC
            LIMIT 100
        """).fetchall()

    return templates.TemplateResponse(request, "book_club.html", {
        "settings": settings,
        "upcoming_events": upcoming_book_club_events,
        "past_events": past_book_club_events,
    })
