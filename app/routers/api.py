from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from app.core.database import db
from app.core.config import MAX_DAYS
from app.core.utils import notify_activity, logger
from app.core.rpa import rpa
import datetime

router = APIRouter()

@router.get("/rpa/health")
async def rpa_health():
    """Check RPA health status."""
    health = await rpa.health()
    return JSONResponse(health)

@router.get("/rpa/manual-login")
async def rpa_manual_login():
    """Open browser for manual login."""
    result = await rpa.manual_login()
    return JSONResponse(result)

@router.post("/rpa/issue")
async def rpa_issue(request: Request):
    """Issue a book via RPA. Accepts form data or JSON."""
    try:
        json_data = await request.json()
        reader_id = json_data.get("reader_id")
        barcode = json_data.get("barcode")
        loan_days = json_data.get("loan_days", 14)
    except:
        form_data = await request.form()
        reader_id = form_data.get("reader_id")
        barcode = form_data.get("barcode")
        loan_days = form_data.get("loan_days", 14)
        
        if reader_id:
            reader_id = int(reader_id)
        if loan_days:
            loan_days = int(loan_days)
        else:
            loan_days = 14
    
    if not reader_id or not barcode:
        return JSONResponse(
            {"ok": False, "message": "Missing required fields: reader_id and barcode"},
            status_code=400
        )
    
    if loan_days > MAX_DAYS:
        loan_days = MAX_DAYS
    if loan_days < 1:
        loan_days = 1
    
    result = await rpa.issue_item(barcode, reader_id, loan_days)
    return JSONResponse(result)

@router.post("/rpa/return")
async def rpa_return(request: Request):
    """Return a book via RPA. Accepts form data or JSON."""
    try:
        json_data = await request.json()
        barcode = json_data.get("barcode")
    except:
        form_data = await request.form()
        barcode = form_data.get("barcode")
    
    if not barcode:
        return JSONResponse(
            {"ok": False, "message": "Missing required field: barcode"},
            status_code=400
        )
    
    result = await rpa.return_item(barcode)
    return JSONResponse(result)

@router.post("/submit", response_class=HTMLResponse)
async def submit(
    request: Request,
    action: str = Form(...),
    barcode: str = Form(...),
    reader_id: str = Form(""),
    card_barcode: str = Form(""),
    loan_days: str = Form("14"),
):
    await notify_activity("submit", request, {"action": action, "barcode": barcode, "reader_id": reader_id})
    action = (action or "").strip().lower()
    barcode = (barcode or "").strip()
    reader_id = (reader_id or "").strip()
    card_barcode = (card_barcode or "").strip()

    try:
        loan_days_int = int((loan_days or str(MAX_DAYS)).strip() or MAX_DAYS)
    except:
        loan_days_int = MAX_DAYS

    if loan_days_int > MAX_DAYS:
        loan_days_int = MAX_DAYS
    if loan_days_int < 1:
        loan_days_int = 1

    if action == "return":
        rid = int(reader_id) if reader_id else None
        now = datetime.datetime.utcnow().isoformat()

        with db() as c:
            c.execute(
                "INSERT INTO return_requests(barcode, reader_id, card_barcode, status, created_at, created_ip, created_ua) VALUES(?,?,?,?,?,?,?)",
                (barcode, rid, card_barcode if card_barcode else None, "PENDING", now, request.client.host if request.client else None, request.headers.get("user-agent", "")),
            )

        return HTMLResponse("""
        <html>
          <head>
            <meta charset="utf-8"/>
            <meta name="viewport" content="width=device-width, initial-scale=1"/>
          </head>
          <body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:#0b0f14;color:#e7edf5;margin:0;">
            <div style="max-width:480px;margin:40px auto;padding:16px;">
              <div style="background:#0f1623;border:1px solid #1f2b40;border-radius:18px;padding:18px;text-align:center;">
                <h2 style="margin:0 0 8px;font-size:20px;">✅ Заявка на возврат создана</h2>
                <p style="margin:0 0 12px;font-size:14px;color:#9fb0c5;">
                  Возврат будет подтвержден библиотекарем после физического приема книги.
                </p>
                <button onclick="window.location.href='/scan'"
                        style="margin-top:8px;padding:12px 18px;border-radius:999px;border:none;background:#1d4ed8;color:#fff;font-size:15px;font-weight:500;width:100%;max-width:260px;cursor:pointer;">
                  ← Back to scan
                </button>
              </div>
            </div>
          </body>
        </html>
        """)

    if action == "issue" and not card_barcode:
        return HTMLResponse("<h3>⚠️ Для Issue нужно выбрать читателя (нажми 'Найти' и выбери из списка)</h3><p><a href='/scan'>Back</a></p>", status_code=400)

    rid = None
    if reader_id:
        try:
            rid = int(reader_id)
        except:
            pass
            
    # Use card_barcode if available as cleaner way to identify reader?
    # Original logic used reader_query=reader_query_for_rpa where reader_query_for_rpa = card_barcode
    reader_query_for_rpa = card_barcode
    logger.info(f"Using card_barcode (reader code) from form: {card_barcode[:10]}...")
    
    r_issue_result = await rpa.issue_item(barcode, rid or 0, loan_days=loan_days_int, reader_query=reader_query_for_rpa)
    
    ok = r_issue_result.get("ok", False)
    if ok:
        now = datetime.datetime.utcnow().isoformat()
        with db() as c:
            c.execute(
                "INSERT INTO issued_books(barcode, reader_id, card_barcode, loan_days, issued_at, issued_by_ip, issued_by_ua) VALUES(?,?,?,?,?,?,?)",
                (
                    barcode,
                    rid,
                    card_barcode,
                    loan_days_int,
                    now,
                    request.client.host if request.client else None,
                    request.headers.get("user-agent", "")
                )
            )
        return HTMLResponse(f"""
        <html>
          <head>
            <meta charset="utf-8"/>
            <meta name="viewport" content="width=device-width, initial-scale=1"/>
          </head>
          <body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:#0b0f14;color:#e7edf5;margin:0;">
            <div style="max-width:480px;margin:40px auto;padding:16px;">
              <div style="background:#0f1623;border:1px solid #1f2b40;border-radius:18px;padding:18px;text-align:center;">
                <h2 style="margin:0 0 8px;font-size:20px;">✅ ISSUED</h2>
                <p style="margin:0 0 12px;font-size:14px;color:#9fb0c5;">
                  {r_issue_result.get('message') or 'Book issued successfully'}
                </p>
                <button onclick="window.location.href='/scan'"
                        style="margin-top:8px;padding:12px 18px;border-radius:999px;border:none;background:#1d4ed8;color:#fff;font-size:15px;font-weight:500;width:100%;max-width:260px;cursor:pointer;">
                  ← Back to scan
                </button>
              </div>
            </div>
          </body>
        </html>
        """)
    else:
        return HTMLResponse(f"""
        <html>
          <head>
            <meta charset="utf-8"/>
            <meta name="viewport" content="width=device-width, initial-scale=1"/>
          </head>
          <body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:#0b0f14;color:#e7edf5;margin:0;">
            <div style="max-width:480px;margin:40px auto;padding:16px;">
              <div style="background:#241216;border:1px solid #4b1f25;border-radius:18px;padding:18px;text-align:center;">
                <h2 style="margin:0 0 8px;font-size:20px;">❌ ISSUE FAILED</h2>
                <p style="margin:0 0 12px;font-size:14px;color:#fca5a5;">
                  {r_issue_result.get('message') or 'Issue failed'}
                </p>
                <button onclick="window.location.href='/scan'"
                        style="margin-top:8px;padding:12px 18px;border-radius:999px;border:none;background:#1d4ed8;color:#fff;font-size:15px;font-weight:500;width:100%;max-width:260px;cursor:pointer;">
                  ← Back to scan
                </button>
              </div>
            </div>
          </body>
        </html>
        """)

@router.get("/diag/issue")
async def diag_issue(reader_id: int, barcode: str, loan_days: int = 2):
    result = await rpa.issue_item(barcode, reader_id, loan_days=loan_days)
    return {
        "step": "issue_item_rpa",
        "ok": result.get("ok"),
        "message": result.get("message") or "Issue completed",
        "barcode": barcode,
        "reader_id": reader_id
    }

@router.get("/diag/return")
async def diag_return(barcode: str):
    result = await rpa.return_item(barcode)
    return {
        "step": "return_item_rpa",
        "ok": result.get("ok"),
        "message": result.get("message") or "Return completed",
        "barcode": barcode
    }

@router.get("/api/readers/search")
async def api_readers_search(q: str = Query(..., min_length=2)):
    result = await rpa.search_readers(q, n=4)
    if result.get("ok"):
        return {"http": {"status_code": 200}, "elibra": result.get("results", [])}
    else:
        return {"http": {"status_code": 500}, "elibra": [], "error": result.get("error", "Search failed")}

@router.get("/api/readers/search-by-cardcode")
async def api_readers_search_by_cardcode(cardcode: str = Query(..., min_length=5, max_length=13)):
    result = await rpa.search_readers(cardcode, n=10)
    if result.get("ok"):
        results = result.get("results", [])
        for item in results:
            fm = item.get("fieldModels", [])
            card = next((f.get("value") for f in fm if f.get("code") == "LIBRARY_CARD_BARCODE"), None)
            if card == cardcode:
                return {"ok": True, "result": item}
        return {"ok": False, "error": "Reader not found with this cardcode"}
    else:
        return {"ok": False, "error": result.get("error", "Search failed")}
