from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.core.templates import templates

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def library_home(request: Request):
    """Coventry University Kazakhstan Library - Main Website"""
    return templates.TemplateResponse("home.html", {"request": request})
