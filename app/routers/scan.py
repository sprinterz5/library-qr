from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.core.templates import templates
from app.core.config import CARDCODE_PREFIX

router = APIRouter()

@router.get("/scan", response_class=HTMLResponse)
def scan(request: Request):
    return templates.TemplateResponse("scan.html", {"request": request, "CARDCODE_PREFIX": CARDCODE_PREFIX})
