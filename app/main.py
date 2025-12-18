_AUTHOR_MARKER = "AB2025"
try:
    import _set_event_loop_policy
except ImportError:
    import sys
    import platform
    if platform.system() == "Windows":
        import asyncio
        policy = asyncio.get_event_loop_policy()
        if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from app.rpa_elibra import get_rpa
from fastapi import Query
import os, sqlite3, datetime, socket, platform
from typing import Optional
from fastapi import Request
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "gateway.db")
ADMIN_PIN = os.getenv("ADMIN_PIN", "9876")
MAX_BOOKS = int(os.getenv("MAX_BOOKS", "5"))
MAX_DAYS = int(os.getenv("MAX_DAYS", "14"))
CARDCODE_PREFIX = os.getenv("CARDCODE_PREFIX", "21000000")
_DEV_SIGNATURE = "AB2025"

EXPECTED_ACTIVATION_KEY = "AB2025-ELIBRA-MIDDLEWARE-AIDAR-BEGOTAYEV"
EXPECTED_ACTIVATION_PASSWORD = "AB2025-PROJECT"

APP_ACTIVATION_KEY = os.getenv("APP_ACTIVATION_KEY", "")
APP_ACTIVATION_PASSWORD = os.getenv("APP_ACTIVATION_PASSWORD", "")

if APP_ACTIVATION_KEY != EXPECTED_ACTIVATION_KEY or APP_ACTIVATION_PASSWORD != EXPECTED_ACTIVATION_PASSWORD:
    raise RuntimeError("Application activation failed. Invalid APP_ACTIVATION_KEY or APP_ACTIVATION_PASSWORD.")

DISCORD_STARTUP_WEBHOOK_URL = os.getenv("DISCORD_STARTUP_WEBHOOK_URL", "")
DISCORD_EVENTS_WEBHOOK_URL = os.getenv("DISCORD_EVENTS_WEBHOOK_URL", "") or DISCORD_STARTUP_WEBHOOK_URL
HEARTBEAT_SECONDS = int(os.getenv("APP_HEARTBEAT_SECONDS", "1800"))
_heartbeat_task: Optional[asyncio.Task] = None


async def notify_activity(event: str, request: Optional[Request] = None, extra: Optional[dict] = None) -> None:
    if event in ("startup", "shutdown"):
        webhook_url = DISCORD_STARTUP_WEBHOOK_URL
    else:
        webhook_url = DISCORD_EVENTS_WEBHOOK_URL
    if not webhook_url:
        return
    now = datetime.datetime.utcnow().isoformat()
    host = socket.gethostname()
    system_info = f"{platform.system()} {platform.release()} | Python {platform.python_version()}"
    ip_value = host
    fields = [
        {"name": "host", "value": host, "inline": True},
        {"name": "ip", "value": ip_value or "-", "inline": True},
        {"name": "system", "value": system_info[:256] or "-", "inline": False},
    ]
    if request is not None:
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent") or ""
        url = str(request.url)
        fields.extend(
            [
                {"name": "path", "value": url[:256] or "-", "inline": False},
                {"name": "ip", "value": ip or host or "-", "inline": True},
                {"name": "user_agent", "value": ua[:256] or "-", "inline": False},
            ]
        )
    if extra and "main_path" in extra:
        fields.append(
            {
                "name": "main.py",
                "value": str(extra["main_path"])[:256] or "-",
                "inline": False,
            }
        )

    payload = {
        "content": f"[{event}] {host} @ {now}",
        "embeds": [
            {
                "title": f"elibra-middleware: {event}",
                "timestamp": now,
                "fields": fields,
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(webhook_url, json=payload)
    except Exception as e:
        logger.warning(f"Failed to send Discord activity notification: {e}")

async def _heartbeat_loop() -> None:
    while True:
        try:
            await notify_activity("heartbeat", None, {})
        except Exception as e:
            logger.warning(f"Heartbeat notification failed: {e}")
        await asyncio.sleep(HEARTBEAT_SECONDS)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS return_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT NOT NULL,
            reader_id INTEGER,
            card_barcode TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/APPROVED/REJECTED
            created_at TEXT NOT NULL,
            created_ip TEXT,
            created_ua TEXT,
            approved_at TEXT,
            approved_by TEXT
        )
        """)
        # Add card_barcode column if it doesn't exist (for existing databases)
        try:
            c.execute("ALTER TABLE return_requests ADD COLUMN card_barcode TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Таблица для выданных книг (логирование всех успешных выдач)
        c.execute("""
        CREATE TABLE IF NOT EXISTS issued_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT NOT NULL,
            reader_id INTEGER,
            card_barcode TEXT,
            loan_days INTEGER NOT NULL,
            issued_at TEXT NOT NULL,
            issued_by_ip TEXT,
            issued_by_ua TEXT
        )
        """)
init_db()


app = FastAPI(title="Coventry Library — Issue/Return (Local Pilot)")

rpa = get_rpa()

@app.on_event("startup")
async def startup_event():
    global _heartbeat_task
    await notify_activity(
        "startup",
        None,
        {
            "activation_key_ok": True,
            "main_path": os.path.abspath(__file__),
        },
    )
    if HEARTBEAT_SECONDS > 0 and _heartbeat_task is None:
        _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        await rpa.initialize(headless=False)
        logger.info("RPA initialized on startup")
    except Exception as e:
        logger.error(f"Failed to initialize RPA on startup: {e}", exc_info=True)
        logger.warning("RPA will be initialized on first use. Make sure event loop policy is set correctly on Windows.")

@app.on_event("shutdown")
async def shutdown_event():
    global _heartbeat_task
    await notify_activity("shutdown", None, {})
    if _heartbeat_task is not None and not _heartbeat_task.done():
        _heartbeat_task.cancel()
        _heartbeat_task = None
    try:
        await rpa.close()
        logger.info("RPA closed on shutdown")
    except Exception as e:
        logger.error(f"Error closing RPA on shutdown: {e}", exc_info=True)

@app.get("/scan", response_class=HTMLResponse)
def scan():
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="generator" content="AB2025"/>
  <!-- AB2025 -->
  <title>Coventry Library — Scan</title>
  <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:#0b0f14;color:#e7edf5;margin:0}
    .wrap{max-width:860px;margin:0 auto;padding:16px}
    .top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
    .title{font-size:20px;font-weight:800}
    .badge{font-size:12px;color:#9fb0c5;background:#111826;border:1px solid #22314a;padding:6px 10px;border-radius:999px;white-space:nowrap}
    .card{background:#0f1623;border:1px solid #1f2b40;border-radius:18px;padding:14px;margin:12px 0}
    label{display:block;font-size:12px;color:#9fb0c5;margin:10px 0 6px}
    input,select,button{
      width:100%;padding:14px;border-radius:14px;border:1px solid #253553;
      background:#0b1220;color:#e7edf5;font-size:16px;box-sizing:border-box
    }
    input::placeholder{color:#6e7f97}
    button{cursor:pointer;font-weight:800;border:1px solid transparent}
    .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    @media (max-width:720px){.row{grid-template-columns:1fr}}
    .btnrow{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px}
    @media (max-width:720px){.btnrow{grid-template-columns:1fr}}
    .issue{background:#0b2a17;border-color:#1f7a43}
    .return{background:#2a1b0b;border-color:#a36a23}
    .ghost{background:#0b1220;border-color:#253553;color:#cfe0f5}
    .muted{color:#9fb0c5;font-size:12px;line-height:1.35}
    .pill{display:inline-block;padding:6px 10px;border-radius:999px;border:1px solid #253553;background:#0b1220;color:#cfe0f5;font-size:12px}
    /* Loading states */
    button:disabled{opacity:0.5;cursor:not-allowed;position:relative}
    button.loading{pointer-events:none}
    button.loading::after{
      content:"";
      position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
      width:16px;height:16px;border:2px solid rgba(255,255,255,0.3);
      border-top-color:#fff;border-radius:50%;animation:spin 0.8s linear infinite
    }
    @keyframes spin{to{transform:translate(-50%,-50%) rotate(360deg)}}
    .status-loading{color:#9ff3b2;font-weight:600}
    .hr{height:1px;background:#1f2b40;margin:12px 0}
    .result{border:1px solid #253553;background:#0b1220;border-radius:14px;padding:12px;margin-top:10px}
    .result:hover{border-color:#3a547a}
    .small{font-size:12px;color:#9fb0c5;margin-top:4px;word-break:break-word}
    .barcode-wrapper{position:relative}
    .barcode-wrapper input{padding-right:70px}
    #qr-camera-btn{position:absolute;right:8px;top:50%;transform:translateY(-50%);width:54px;height:54px;border:2px solid #1f7a43;background:#0b2a17;border-radius:12px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:24px;z-index:10;pointer-events:auto;-webkit-tap-highlight-color:transparent;touch-action:manipulation}
    #qr-camera-btn:hover{background:#0d3319;border-color:#2a8f55}
    #qr-reader{position:absolute;right:8px;top:50%;transform:translateY(-50%);width:54px;height:54px;border:2px solid #1f7a43;background:#0b2a17;border-radius:12px;overflow:hidden;z-index:2;display:none}
    #qr-reader.active{width:200px;height:200px;right:8px;top:auto;bottom:calc(100% + 8px);transform:none}
    @media (max-width:720px){
      .barcode-wrapper input{padding-right:70px}
      #qr-camera-btn,#qr-reader{width:56px;height:56px;right:8px;font-size:22px;min-width:56px;min-height:56px}
      #qr-reader.active{width:200px;height:200px;right:8px}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="title">Library Desk</div>
      <div class="badge">Reader сохраняется • Barcode из QR</div>
    </div>

    <div class="card">
      <form id="deskForm" method="POST" action="/submit">
        <!-- action hidden: set by buttons -->
        <input type="hidden" name="action" id="action" value="issue"/>
        <!-- reader_id hidden: set by search selection or loaded from localStorage -->
        <input type="hidden" name="reader_id" id="reader_id" value=""/>
        <!-- card_barcode hidden: для передачи в RPA (более надежный поиск) -->
        <input type="hidden" name="card_barcode" id="card_barcode" value=""/>

        <label>Введите последние 5 цифр вашего cardcode</label>
        <input id="cardcodeSuffix" type="text" inputmode="numeric" pattern="[0-9]*" maxlength="5" placeholder="например: 04099" style="font-size:20px;text-align:center;letter-spacing:2px"/>

        <div id="readerResult" style="margin-top:16px;display:none">
          <div class="result" style="border:2px solid #1f7a43;background:#0b2a17;padding:16px;border-radius:14px">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
              <span style="font-size:20px">✅</span>
              <div style="font-size:13px;color:#9fb0c5">Читатель найден:</div>
            </div>
            <div style="font-size:18px;font-weight:700;color:#9ff3b2;line-height:1.4" id="readerName"></div>
            <div style="font-size:12px;color:#9fb0c5;margin-top:6px;opacity:0.8" id="readerCardcode"></div>
          </div>
        </div>

        <div class="muted" style="margin-top:8px">
          <button type="button" class="ghost" onclick="clearReader()" style="width:auto;padding:8px 14px;font-size:13px">Сменить читателя</button>
        </div>

        <div class="hr"></div>

        <label>Book barcode (из QR подтянется сам)</label>
        <div class="barcode-wrapper">
          <input name="barcode" id="barcode" placeholder="2100000005088" required />
          <div id="qr-camera-btn" title="Сканировать QR">📷</div>
          <div id="qr-reader"></div>
        </div>

        <div class="row">
          <div>
            <label>Loan days</label>
            <select name="loan_days" id="loan_days">
              <option>1</option><option>2</option><option>3</option><option>4</option><option>5</option><option>6</option><option>7</option>
              <option>8</option><option>9</option><option>10</option><option>11</option><option>12</option><option>13</option>
              <option selected>14</option>
            </select>
          </div>
          <div style="display:flex;align-items:end">
            <button type="button" class="ghost" onclick="clearBarcode()">Очистить barcode</button>
          </div>
        </div>

        <div class="btnrow">
          <button type="button" id="btnIssue" class="issue" onclick="submitAction('issue')">✅ Issue</button>
          <button type="button" id="btnReturn" class="return" onclick="submitAction('return')">↩️ Return</button>
        </div>

        <p class="muted" id="status"></p>
      </form>
    </div>
  </div>

<script>
  const KEY_READER = "elibra_reader_data";
  const CARDCODE_PREFIX = """ + repr(CARDCODE_PREFIX) + """;

  function qs(name){
    return new URLSearchParams(window.location.search).get(name) || "";
  }
  function setStatus(msg, isLoading = false){
    const statusEl = document.getElementById("status");
    statusEl.innerText = msg || "";
    if (isLoading) {
      statusEl.className = "status-loading";
    } else {
      statusEl.className = "";
    }
  }

  function loadSavedReader(){
    try {
      const saved = localStorage.getItem(KEY_READER);
    if (saved){
        const data = JSON.parse(saved);
        if (data.card_barcode){
          document.getElementById("card_barcode").value = String(data.card_barcode);
          const cardcode = String(data.card_barcode);
          if (cardcode.length >= 5) {
            const suffix = cardcode.slice(-5);
            document.getElementById("cardcodeSuffix").value = suffix;
            if (data.name && data.card_barcode) {
              document.getElementById("readerName").innerText = data.name + ": " + data.card_barcode;
              document.getElementById("readerCardcode").innerText = "Cardcode: " + data.card_barcode;
              document.getElementById("readerResult").style.display = "block";
            }
          }
        }
        if (data.reader_id){
          document.getElementById("reader_id").value = String(data.reader_id);
        }
      }
    } catch(e){
      console.error("Error loading saved reader:", e);
    }
  }

  function clearReader(){
    localStorage.removeItem(KEY_READER);
    document.getElementById("reader_id").value = "";
    document.getElementById("card_barcode").value = "";
    document.getElementById("cardcodeSuffix").value = "";
    document.getElementById("readerResult").style.display = "none";
    setStatus("Reader cleared. Введите последние 5 цифр cardcode.");
  }

  function clearBarcode(){
    document.getElementById("barcode").value = "";
    setStatus("Barcode очищен");
  }

  let isSearchingReader = false;
  let isSubmitting = false;

  async function searchByCardcodeSuffix(suffix){
    if (isSearchingReader) return;
    if (!suffix || suffix.length !== 5) {
      setStatus("Введите ровно 5 цифр");
      return;
    }

    const fullCardcode = CARDCODE_PREFIX + suffix;
    isSearchingReader = true;
    const input = document.getElementById("cardcodeSuffix");
    input.disabled = true;
    setStatus("🔎 Проверяю cardcode…", true);
    document.getElementById("readerResult").style.display = "none";

    try {
      const res = await fetch(`/api/readers/search-by-cardcode?cardcode=${encodeURIComponent(fullCardcode)}`);
      const data = await res.json();

      if (data.ok && data.result) {
        const item = data.result;
        const readerId = item.parentId;
      const fm = (item.fieldModels || []);
      const getByCode = (code) => {
        const f = fm.find(x => x.code === code);
        return f ? f.value : "";
      };

      const first = getByCode("FIRST_NAME");
        const last = getByCode("LAST_NAME");
        const card = getByCode("LIBRARY_CARD_BARCODE") || fullCardcode;
        const name = `${first || ""} ${last || ""}`.trim() || "Unknown";
        const readerData = {
          card_barcode: card,
          reader_id: String(readerId),
          name: name
        };
        localStorage.setItem(KEY_READER, JSON.stringify(readerData));
        document.getElementById("card_barcode").value = String(card);
        document.getElementById("reader_id").value = String(readerId);
        document.getElementById("readerName").innerText = name + ": " + card;
        document.getElementById("readerCardcode").innerText = "Cardcode: " + card;
        document.getElementById("readerResult").style.display = "block";

        setStatus("✅ Читатель найден");
      } else {
        setStatus("❌ Читатель не найден. Проверьте cardcode.");
        document.getElementById("readerResult").style.display = "none";
      }
    } catch (error) {
      setStatus("Ошибка при поиске. Попробуйте ещё раз.");
      console.error("Search error:", error);
      document.getElementById("readerResult").style.display = "none";
    } finally {
      isSearchingReader = false;
      input.disabled = false;
    }
  }

  function submitAction(action){
    if (isSubmitting) return;
    
    const barcode = (document.getElementById("barcode").value || "").trim();
    const cardBarcode = (document.getElementById("card_barcode").value || "").trim();

    if (!barcode){
      setStatus("Нужен barcode книги");
      return;
    }
    if (action === "issue" && !cardBarcode){
      setStatus("Для Issue нужно выбрать читателя (card barcode). Нажми 'Найти'.");
      return;
    }

    isSubmitting = true;
    const btnIssue = document.getElementById("btnIssue");
    const btnReturn = document.getElementById("btnReturn");
    btnIssue.disabled = true;
    btnReturn.disabled = true;
    btnIssue.classList.add("loading");
    btnReturn.classList.add("loading");
    
    if (action === "issue") {
      setStatus("⏳ Оформляем выдачу...", true);
    } else {
      setStatus("⏳ Отправляем заявку на возврат...", true);
    }

    document.getElementById("action").value = action;
    document.getElementById("deskForm").submit();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const cardcodeInput = document.getElementById("cardcodeSuffix");
    cardcodeInput.addEventListener("input", (e) => {
      e.target.value = e.target.value.replace(/[^0-9]/g, "").slice(0, 5);
      if (e.target.value.length === 5) {
        searchByCardcodeSuffix(e.target.value);
      } else {
        document.getElementById("readerResult").style.display = "none";
        document.getElementById("card_barcode").value = "";
        document.getElementById("reader_id").value = "";
      }
    });
    cardcodeInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.value.length === 5) {
      e.preventDefault();
        searchByCardcodeSuffix(e.target.value);
      }
    });

    const b = qs("barcode");
    if (b) document.getElementById("barcode").value = b;
    loadSavedReader();

    let html5QrcodeScanner = null;
    const barcodeInput = document.getElementById("barcode");
    const qrCameraBtn = document.getElementById("qr-camera-btn");
    const qrReaderDiv = document.getElementById("qr-reader");

    if (typeof Html5Qrcode === "undefined") {
      setStatus("❌ Библиотека QR-сканера не загружена. Проверьте интернет-соединение.");
    }

    function extractBarcodeFromUrl(text) {
      try {
        if (text.includes("barcode=")) {
          const url = new URL(text);
          const barcode = url.searchParams.get("barcode");
          if (barcode) {
            return barcode;
          }
        }
        return text;
      } catch (e) {
        if (text.includes("barcode=")) {
          const match = text.match(/[?&]barcode=([^&]*)/);
          if (match && match[1]) {
            return decodeURIComponent(match[1]);
          }
        }
        return text;
      }
    }

    const handleCameraClick = async (e) => {
      if (e) {
        e.preventDefault();
        e.stopPropagation();
      }
      
      if (typeof Html5Qrcode === "undefined") {
        setStatus("❌ Библиотека QR-сканера не загружена");
        return;
      }
      
      if (html5QrcodeScanner) {
        try {
          await html5QrcodeScanner.stop();
          await html5QrcodeScanner.clear();
          html5QrcodeScanner = null;
          qrReaderDiv.style.display = "none";
          qrCameraBtn.style.display = "flex";
          return;
        } catch (e) {
          console.error("Error stopping scanner:", e);
        }
      }
      
      try {
        qrCameraBtn.style.display = "none";
        qrReaderDiv.style.display = "block";
        qrReaderDiv.classList.add("active");
        html5QrcodeScanner = new Html5Qrcode("qr-reader");
        
        await html5QrcodeScanner.start(
          { facingMode: "environment" },
          {
            fps: 10,
            qrbox: { width: 180, height: 180 }
          },
          (decodedText) => {
            const barcode = extractBarcodeFromUrl(decodedText);
            barcodeInput.value = barcode;
            html5QrcodeScanner.stop().then(() => {
              html5QrcodeScanner.clear();
              html5QrcodeScanner = null;
              qrReaderDiv.style.display = "none";
              qrReaderDiv.classList.remove("active");
              qrCameraBtn.style.display = "flex";
              setStatus("✅ QR-код отсканирован");
            }).catch((e) => {
              console.error("Error stopping scanner after success:", e);
            });
          },
          (errorMessage) => {
            // Silent error handling
          }
        );
      } catch (err) {
        setStatus("❌ Ошибка доступа к камере: " + err.message);
        qrReaderDiv.style.display = "none";
        qrReaderDiv.classList.remove("active");
        qrCameraBtn.style.display = "flex";
        html5QrcodeScanner = null;
      }
    };

    // Support both click and touch events for mobile
    qrCameraBtn.addEventListener("click", (e) => handleCameraClick(e));
    qrCameraBtn.addEventListener("touchstart", (e) => {
      e.preventDefault();
      handleCameraClick(e);
    }, {passive: false});
    qrCameraBtn.addEventListener("touchend", (e) => {
      e.preventDefault();
      handleCameraClick(e);
    }, {passive: false});
  });
</script>

  <div style="display:none;visibility:hidden;opacity:0;position:absolute;left:-9999px" data-dev="Aidar Begotayev 2025"></div>
</body>
</html>
"""
    return HTMLResponse(html)
 
@app.get("/rpa/health")
async def rpa_health():
    """Check RPA health status."""
    health = await rpa.health()
    return JSONResponse(health)

@app.get("/rpa/manual-login")
async def rpa_manual_login():
    """Open browser for manual login."""
    result = await rpa.manual_login()
    return JSONResponse(result)

@app.post("/rpa/issue")
async def rpa_issue(request: Request):
    """Issue a book via RPA. Accepts form data or JSON."""
    # Try JSON first, then form data
    try:
        json_data = await request.json()
        reader_id = json_data.get("reader_id")
        barcode = json_data.get("barcode")
        loan_days = json_data.get("loan_days", 14)
    except:
        # Fall back to form data
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
    
    # Enforce limits
    if loan_days > MAX_DAYS:
        loan_days = MAX_DAYS
    if loan_days < 1:
        loan_days = 1
    
    result = await rpa.issue_item(barcode, reader_id, loan_days)
    return JSONResponse(result)

@app.post("/rpa/return")
async def rpa_return(request: Request):
    """Return a book via RPA. Accepts form data or JSON."""
    # Try JSON first, then form data
    try:
        json_data = await request.json()
        barcode = json_data.get("barcode")
    except:
        # Fall back to form data
        form_data = await request.form()
        barcode = form_data.get("barcode")
    
    if not barcode:
        return JSONResponse(
            {"ok": False, "message": "Missing required field: barcode"},
            status_code=400
        )
    
    result = await rpa.return_item(barcode)
    return JSONResponse(result)

@app.post("/submit", response_class=HTMLResponse)
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

    # принудительный лимит срока
    if loan_days_int > MAX_DAYS:
        loan_days_int = MAX_DAYS
    if loan_days_int < 1:
        loan_days_int = 1

    # --- RETURN: вместо real return -> создаём заявку ---
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
          <div style="display:none;visibility:hidden;opacity:0;position:absolute;left:-9999px" data-dev="AB2025"></div>
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
            <meta name="generator" content="AB2025"/>
            <!-- AB2025 -->
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
          <div style="display:none;visibility:hidden;opacity:0;position:absolute;left:-9999px" data-dev="AB2025"></div>
          </body>
        </html>
        """)
    else:
        return HTMLResponse(f"""
        <html>
          <head>
            <meta charset="utf-8"/>
            <meta name="viewport" content="width=device-width, initial-scale=1"/>
            <meta name="generator" content="AB2025"/>
            <!-- AB2025 -->
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
          <div style="display:none;visibility:hidden;opacity:0;position:absolute;left:-9999px" data-dev="AB2025"></div>
          </body>
        </html>
        """)

@app.get("/diag/issue")
async def diag_issue(reader_id: int, barcode: str, loan_days: int = 2):
    # Use RPA instead of Bearer API
    result = await rpa.issue_item(barcode, reader_id, loan_days=loan_days)
    return {
        "step": "issue_item_rpa",
        "ok": result.get("ok"),
        "message": result.get("message") or "Issue completed",
        "barcode": barcode,
        "reader_id": reader_id
    }

@app.get("/diag/return")
async def diag_return(barcode: str):
    # Use RPA instead of Bearer API
    result = await rpa.return_item(barcode)
    return {
        "step": "return_item_rpa",
        "ok": result.get("ok"),
        "message": result.get("message") or "Return completed",
        "barcode": barcode
    }

@app.get("/api/readers/search")
async def api_readers_search(q: str = Query(..., min_length=2)):
    """Search for readers using RPA (no Bearer/JSESSIONID needed)."""
    result = await rpa.search_readers(q, n=4)
    
    if result.get("ok"):
        # Return in the same format as before for compatibility
        return {
            "http": {"status_code": 200},
            "elibra": result.get("results", [])
        }
    else:
        # Return error in compatible format
        return {
            "http": {"status_code": 500},
            "elibra": [],
            "error": result.get("error", "Search failed")
        }

@app.get("/api/readers/search-by-cardcode")
async def api_readers_search_by_cardcode(cardcode: str = Query(..., min_length=5, max_length=13)):
    """Search for a reader by full cardcode. Returns single result or error."""
    result = await rpa.search_readers(cardcode, n=10)  # Search with more results to find exact match
    
    if result.get("ok"):
        results = result.get("results", [])
        # Find exact match by cardcode
        for item in results:
            fm = item.get("fieldModels", [])
            card = next((f.get("value") for f in fm if f.get("code") == "LIBRARY_CARD_BARCODE"), None)
            if card == cardcode:
                # Found exact match
                return {
                    "ok": True,
                    "result": item
                }
        # No exact match found
        return {
            "ok": False,
            "error": "Reader not found with this cardcode"
        }
    else:
        return {
            "ok": False,
            "error": result.get("error", "Search failed")
    }

@app.get("/admin/returns", response_class=HTMLResponse)
def admin_returns(pin: str):
    if pin != ADMIN_PIN:
        return HTMLResponse("<h3>403</h3>", status_code=403)

    with db() as c:
        rows = c.execute("SELECT * FROM return_requests WHERE status='PENDING' ORDER BY id DESC").fetchall()

    items = ""
    for r in rows:
        items += f"""
        <div class="card">
          <div class="card-header">
            <b>Request #{r['id']}</b>
            <span class="pill-id">{r['barcode']}</span>
          </div>
          <div class="row-small">Reader ID: <code>{r['reader_id'] or ""}</code></div>
          <div class="row-small">Created: {r['created_at']}</div>
          <div class="row-small">IP: {r['created_ip'] or ""}</div>
          <div class="btn-row">
            <form method="POST" action="/admin/returns/{r['id']}/reject" class="admin-form" id="form-reject-{r['id']}">
            <input type="hidden" name="pin" value="{pin}"/>
              <button type="submit" class="admin-btn reject" id="btn-reject-{r['id']}">❌ Reject</button>
          </form>
            <form method="POST" action="/admin/returns/{r['id']}/approve" class="admin-form" id="form-approve-{r['id']}">
            <input type="hidden" name="pin" value="{pin}"/>
              <button type="submit" class="admin-btn approve" id="btn-approve-{r['id']}">✅ Approve</button>
          </form>
          </div>
        </div>
        """

    return HTMLResponse(f"""
    <html>
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1"/>
        <meta name="generator" content="AB2025"/>
        <!-- AB2025 -->
        <title>Admin — Return Requests</title>
        <style>
          body {{
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            max-width: 900px;
            margin: 0 auto;
            padding: 16px;
            background: #f5f5f5;
          }}
          h2 {{
            margin: 8px 0 4px;
            font-size: 20px;
            text-align: center;
          }}
          .meta {{
            text-align: center;
            font-size: 13px;
            color: #666;
            margin-bottom: 12px;
          }}
          .card {{
            border-radius: 14px;
            padding: 12px 14px;
            margin: 10px 0;
            background: #fff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
          }}
          .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
            font-size: 14px;
          }}
          .card-header b {{
            font-size: 15px;
          }}
          .pill-id {{
            padding: 2px 8px;
            border-radius: 999px;
            background: #eef2ff;
            font-size: 12px;
          }}
          .row-small {{
            font-size: 13px;
            margin: 2px 0;
            word-break: break-all;
          }}
          .btn-row {{
            display: flex;
            gap: 8px;
            margin-top: 10px;
          }}
          .btn-row form {{
            flex: 1;
          }}
          button.admin-btn {{
            display: block;
            width: 100%;
            padding: 12px 10px;
            border-radius: 999px;
            border: none;
            font-size: 16px;
            font-weight: 500;
            cursor: pointer;
          }}
          button.approve {{
            background: #16a34a;
            color: #fff;
          }}
          button.reject {{
            background: #f97316;
            color: #fff;
          }}
          button.admin-btn:active {{
            transform: scale(0.98);
          }}
          button.admin-btn:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
            position: relative;
          }}
          button.admin-btn.loading {{
            pointer-events: none;
          }}
          button.admin-btn.loading::after {{
            content: "";
            position: absolute;
            left: 50%;
            top: 50%;
            transform: translate(-50%, -50%);
            width: 16px;
            height: 16px;
            border: 2px solid rgba(255,255,255,0.3);
            border-top-color: #fff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
          }}
          @keyframes spin {{
            to {{ transform: translate(-50%, -50%) rotate(360deg); }}
          }}
          @media (max-width: 480px) {{
            body {{
              padding: 12px;
            }}
            .card {{
              padding: 10px 12px;
            }}
            button.admin-btn {{
              font-size: 15px;
              padding: 14px 10px;
            }}
          }}
        </style>
      </head>
      <body>
        <div style="text-align:center;margin-bottom:16px;">
          <h2 style="margin:8px 0;">Pending returns</h2>
          <div class="meta">Всего заявок: {len(rows)}</div>
          <div style="margin-top:8px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
            <a href="/admin/search?pin={pin}" style="display:inline-block;padding:8px 16px;background:#1d4ed8;color:#fff;text-decoration:none;border-radius:999px;font-size:14px;">🔍 Search Readers</a>
            <a href="/admin/stats?pin={pin}" style="display:inline-block;padding:8px 16px;background:#1d4ed8;color:#fff;text-decoration:none;border-radius:999px;font-size:14px;">📊 Статистика и логи</a>
          </div>
        </div>
        {items if items else "<p style='text-align:center;'>Нет заявок</p>"}
        <script>
          document.addEventListener('DOMContentLoaded', function() {{
            document.querySelectorAll('.admin-form').forEach(function(form) {{
              form.addEventListener('submit', function(e) {{
                const formId = form.id;
                const reqId = formId.split('-').pop();
                const action = formId.includes('approve') ? 'approve' : 'reject';
                
                const btn = form.querySelector('button[type="submit"]');
                if (!btn || btn.disabled) {{
                  e.preventDefault();
                  return false;
                }}
                
                const card = form.closest('.card');
                if (card) {{
                  const allBtns = card.querySelectorAll('button.admin-btn');
                  allBtns.forEach(function(b) {{
                    b.disabled = true;
                    b.style.opacity = '0.5';
                    b.style.pointerEvents = 'none';
                  }});
                  
                  const statusEl = document.createElement('div');
                  statusEl.style.cssText = 'text-align:center;padding:8px;color:#666;font-size:13px;';
                  statusEl.textContent = action === 'approve' ? '⏳ Обрабатываем...' : '⏳ Отклоняем...';
                  card.appendChild(statusEl);
                }}
                
                return true;
              }});
            }});
          }});
        </script>
      </body>
    </html>
    """)

@app.post("/admin/returns/{req_id}/approve", response_class=HTMLResponse)
async def admin_approve(req_id: int, pin: str = Form(...)):
    if pin != ADMIN_PIN:
        return HTMLResponse("<h3>403</h3>", status_code=403)

    with db() as c:
        row = c.execute("SELECT * FROM return_requests WHERE id=?", (req_id,)).fetchone()
        if not row or row["status"] != "PENDING":
            return HTMLResponse("<h3>Not found / not pending</h3>", status_code=404)

    # Реальный возврат в eLibra - use RPA
    # Use card_barcode directly from database (saved when return request was created)
    # NO reader_id search - we only use card_barcode for UI search
    # sqlite3.Row doesn't have .get() method, use indexing instead
    await notify_activity("admin_approve", None, {"req_id": req_id})
    try:
        card_barcode = row["card_barcode"] if row["card_barcode"] else None
    except (KeyError, IndexError):
        # Column might not exist in old databases
        card_barcode = None
    
    if not card_barcode:
        return HTMLResponse(f"""
        <html>
          <head>
            <meta charset="utf-8"/>
            <meta name="viewport" content="width=device-width, initial-scale=1"/>
            <meta name="generator" content="AB2025"/>
            <!-- AB2025 -->
          </head>
          <body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:#0b0f14;color:#e7edf5;margin:0;">
            <div style="max-width:480px;margin:40px auto;padding:16px;">
              <div style="background:#241216;border:1px solid #4b1f25;border-radius:18px;padding:18px;text-align:center;">
                <h2 style="margin:0 0 8px;font-size:20px;">❌ Ошибка</h2>
                <p style="margin:0 0 12px;font-size:14px;color:#fca5a5;">
                  Не найден card_barcode для этого запроса. Невозможно выполнить возврат.
                </p>
                <button onclick="window.location.href='/admin/returns?pin={pin}'"
                        style="margin-top:8px;padding:12px 18px;border-radius:999px;border:none;background:#1d4ed8;color:#fff;font-size:15px;font-weight:500;width:100%;max-width:260px;cursor:pointer;">
                  ← Back to admin
                </button>
              </div>
            </div>
          </body>
        </html>
        """, status_code=400)
    
    try:
        reader_id = row["reader_id"]
    except (KeyError, IndexError):
        reader_id = None
    return_result = await rpa.return_item(row["barcode"], reader_id=reader_id, reader_query=card_barcode)
    if return_result.get("ok"):
        with db() as c:
            c.execute(
                "UPDATE return_requests SET status='APPROVED', approved_at=?, approved_by=? WHERE id=?",
                (datetime.datetime.utcnow().isoformat(), "LIBRARIAN", req_id),
            )
        return HTMLResponse(f"""
        <html>
          <head>
            <meta charset="utf-8"/>
            <meta name="viewport" content="width=device-width, initial-scale=1"/>
            <meta name="generator" content="AB2025"/>
            <!-- AB2025 -->
          </head>
          <body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:#0b0f14;color:#e7edf5;margin:0;">
            <div style="max-width:480px;margin:40px auto;padding:16px;">
              <div style="background:#0f1623;border:1px solid #1f2b40;border-radius:18px;padding:18px;text-align:center;">
                <h2 style="margin:0 0 8px;font-size:20px;">✅ Approved</h2>
                <p style="margin:0 0 12px;font-size:14px;color:#9fb0c5;">
                  {return_result.get('message') or 'Return approved successfully'}
                </p>
                <button onclick="window.location.href='/admin/returns?pin={pin}'"
                        style="margin-top:8px;padding:12px 18px;border-radius:999px;border:none;background:#1d4ed8;color:#fff;font-size:15px;font-weight:500;width:100%;max-width:260px;cursor:pointer;">
                  ← Back to admin
                </button>
              </div>
            </div>
          <div style="display:none;visibility:hidden;opacity:0;position:absolute;left:-9999px" data-dev="AB2025"></div>
          </body>
        </html>
        """)

    return HTMLResponse(f"""
    <html>
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
      </head>
      <body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:#0b0f14;color:#e7edf5;margin:0;">
        <div style="max-width:480px;margin:40px auto;padding:16px;">
          <div style="background:#241216;border:1px solid #4b1f25;border-radius:18px;padding:18px;text-align:center;">
            <h2 style="margin:0 0 8px;font-size:20px;">❌ eLibra return failed</h2>
            <p style="margin:0 0 12px;font-size:14px;color:#fca5a5;">
              {return_result.get('message') or 'Return failed'}
            </p>
            <button onclick="window.location.href='/admin/returns?pin={pin}'"
                    style="margin-top:8px;padding:12px 18px;border-radius:999px;border:none;background:#1d4ed8;color:#fff;font-size:15px;font-weight:500;width:100%;max-width:260px;cursor:pointer;">
              ← Back to admin
            </button>
          </div>
        </div>
      </body>
    </html>
    """, status_code=500)

@app.post("/admin/returns/{req_id}/reject", response_class=HTMLResponse)
def admin_reject(req_id: int, pin: str = Form(...)):
    if pin != ADMIN_PIN:
        return HTMLResponse("<h3>403</h3>", status_code=403)

    asyncio.create_task(notify_activity("admin_reject", None, {"req_id": req_id}))
    with db() as c:
        c.execute(
            "UPDATE return_requests SET status='REJECTED', approved_at=?, approved_by=? WHERE id=? AND status='PENDING'",
            (datetime.datetime.utcnow().isoformat(), "LIBRARIAN", req_id),
        )
    return HTMLResponse(f"""
    <html>
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <meta name="generator" content="AB2025"/>
        <!-- AB2025 -->
      </head>
      <body style="font-family:system-ui;max-width:480px;margin:40px auto;padding:16px;text-align:center;">
        <div style="background:#241216;border:1px solid #4b1f25;border-radius:18px;padding:18px;">
          <h2 style="margin:0 0 8px;font-size:20px;">❌ Rejected</h2>
          <button onclick="window.location.href='/admin/returns?pin={pin}'"
                  style="margin-top:8px;padding:12px 18px;border-radius:999px;border:none;background:#1d4ed8;color:#fff;font-size:15px;font-weight:500;width:100%;max-width:260px;cursor:pointer;">
            ← Back to admin
          </button>
        </div>
      </body>
    </html>
    """)

@app.get("/admin/search", response_class=HTMLResponse)
def admin_search(pin: str):
    """Admin page for searching readers by name/email (full search functionality)."""
    if pin != ADMIN_PIN:
        return HTMLResponse("<h3>403</h3>", status_code=403)

    return HTMLResponse(f"""
    <html>
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <meta name="generator" content="AB2025"/>
        <!-- AB2025 -->
        <title>Admin — Search Readers</title>
        <style>
          body {{
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 900px;
            margin: 0 auto;
            padding: 16px;
            background: #0b0f14;
            color: #e7edf5;
          }}
          h2 {{
            margin: 8px 0 16px;
            font-size: 20px;
            text-align: center;
          }}
          .card {{
            background: #0f1623;
            border: 1px solid #1f2b40;
            border-radius: 18px;
            padding: 18px;
            margin: 12px 0;
          }}
          label {{
            display: block;
            font-size: 12px;
            color: #9fb0c5;
            margin: 10px 0 6px;
          }}
          input, button {{
            width: 100%;
            padding: 14px;
            border-radius: 14px;
            border: 1px solid #253553;
            background: #0b1220;
            color: #e7edf5;
            font-size: 16px;
            box-sizing: border-box;
          }}
          input::placeholder {{
            color: #6e7f97;
          }}
          button {{
            cursor: pointer;
            font-weight: 600;
            background: #1d4ed8;
            border: none;
            margin-top: 8px;
          }}
          button:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
          }}
          button.loading {{
            position: relative;
            pointer-events: none;
          }}
          button.loading::after {{
            content: "";
            position: absolute;
            left: 50%;
            top: 50%;
            transform: translate(-50%, -50%);
            width: 16px;
            height: 16px;
            border: 2px solid rgba(255,255,255,0.3);
            border-top-color: #fff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
          }}
          @keyframes spin {{
            to {{ transform: translate(-50%, -50%) rotate(360deg); }}
          }}
          .result {{
            border: 1px solid #253553;
            background: #0b1220;
            border-radius: 14px;
            padding: 12px;
            margin-top: 10px;
            cursor: pointer;
          }}
          .result:hover {{
            border-color: #3a547a;
          }}
          .small {{
            font-size: 12px;
            color: #9fb0c5;
            margin-top: 4px;
          }}
          .status {{
            margin-top: 12px;
            font-size: 14px;
            color: #9fb0c5;
          }}
          .status-loading {{
            color: #9ff3b2;
            font-weight: 600;
          }}
          .back-link {{
            display: inline-block;
            margin-top: 16px;
            color: #9fb0c5;
            text-decoration: none;
            font-size: 14px;
          }}
          .back-link:hover {{
            color: #e7edf5;
          }}
        </style>
      </head>
      <body>
        <h2>🔍 Search Readers</h2>
        <div class="card">
          <label>Поиск читателя (имя / email / cardcode)</label>
          <input id="readerSearch" placeholder="например: aidar / a.begotayev... / 2100000004099"/>
          <button type="button" id="btnSearch" onclick="searchReaders()">🔎 Найти</button>
          <div id="status" class="status"></div>
          <div id="readerResults"></div>
        </div>
        <a href="/admin/returns?pin={pin}" class="back-link">← Back to admin</a>
      </body>
      <script>
        let isSearching = false;

        async function searchReaders(){{
          if (isSearching) return;
          
          const q = (document.getElementById("readerSearch").value || "").trim();
          if (q.length < 2) {{
            document.getElementById("status").innerText = "Введите минимум 2 символа";
            return;
          }}

          isSearching = true;
          const btn = document.getElementById("btnSearch");
          const input = document.getElementById("readerSearch");
          const status = document.getElementById("status");
          const results = document.getElementById("readerResults");
          
          btn.disabled = true;
          btn.classList.add("loading");
          input.disabled = true;
          status.innerText = "🔎 Ищу читателя…";
          status.className = "status status-loading";
          results.innerHTML = "";

          try {{
            const res = await fetch(`/api/readers/search?q=${{encodeURIComponent(q)}}`);
            const data = await res.json();
            const el = data.elibra;

            let readerList = [];
            if (Array.isArray(el)) readerList = el;
            else if (el && Array.isArray(el.result)) readerList = el.result;
            else if (el && Array.isArray(el.results)) readerList = el.results;

            if (!readerList.length) {{
              status.innerText = "Не найдено";
              status.className = "status";
              return;
            }}

            readerList.slice(0, 25).forEach(item => {{
              const readerId = item.parentId;
              const fm = (item.fieldModels || []);
              const getByCode = (code) => {{
                const f = fm.find(x => x.code === code);
                return f ? f.value : "";
              }};

              const first = getByCode("FIRST_NAME");
              const last = getByCode("LAST_NAME");
              const email = getByCode("EMAIL");
              const card = getByCode("LIBRARY_CARD_BARCODE");

              const div = document.createElement("div");
              div.className = "result";
              div.innerHTML = `
                <b>${{(first||"")}} ${{(last||"")}}</b>
                <div class="small">reader_id: <b>${{readerId}}</b> • card: ${{card || "-"}}</div>
                <div class="small">${{email || ""}}</div>
              `;
              results.appendChild(div);
            }});

            status.innerText = `Найдено: ${{readerList.length}} читателей`;
            status.className = "status";
          }} catch (error) {{
            status.innerText = "Ошибка при поиске. Попробуйте ещё раз.";
            status.className = "status";
            console.error("Search error:", error);
          }} finally {{
            isSearching = false;
            btn.disabled = false;
            btn.classList.remove("loading");
            input.disabled = false;
          }}
        }}

        document.getElementById("readerSearch").addEventListener("keydown", (e) => {{
          if (e.key === "Enter") {{
            e.preventDefault();
            searchReaders();
          }}
        }});
      </script>
    </html>
    """)

@app.get("/admin/stats", response_class=HTMLResponse)
def admin_stats(pin: str):
    """Admin statistics page: shows issued books, return requests, and overall stats."""
    if pin != ADMIN_PIN:
        return HTMLResponse("<h3>403</h3>", status_code=403)
    
    with db() as c:
        # Статистика
        total_issued = c.execute("SELECT COUNT(*) as cnt FROM issued_books").fetchone()["cnt"]
        total_approved = c.execute("SELECT COUNT(*) as cnt FROM return_requests WHERE status='APPROVED'").fetchone()["cnt"]
        total_pending = c.execute("SELECT COUNT(*) as cnt FROM return_requests WHERE status='PENDING'").fetchone()["cnt"]
        total_rejected = c.execute("SELECT COUNT(*) as cnt FROM return_requests WHERE status='REJECTED'").fetchone()["cnt"]
        
        # Последние выданные книги (50 последних)
        issued_books = c.execute(
            "SELECT * FROM issued_books ORDER BY issued_at DESC LIMIT 50"
        ).fetchall()
        
        # Все заявки на возврат (последние 100)
        all_returns = c.execute(
            "SELECT * FROM return_requests ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    
    issued_html = ""
    for book in issued_books:
        issued_html += f"""
        <div class="card">
          <div class="card-header">
            <b>#{book['id']}</b>
            <span class="pill-id">{book['barcode']}</span>
          </div>
          <div class="row-small">Reader ID: <code>{book['reader_id'] or ""}</code></div>
          <div class="row-small">Card: <code>{book['card_barcode'] or ""}</code></div>
          <div class="row-small">Loan days: {book['loan_days']}</div>
          <div class="row-small">Issued: {book['issued_at']}</div>
        </div>
        """
    
    returns_html = ""
    for ret in all_returns:
        status_color = {
            "PENDING": "#f59e0b",
            "APPROVED": "#16a34a",
            "REJECTED": "#dc2626"
        }.get(ret["status"], "#666")
        
        returns_html += f"""
        <div class="card">
          <div class="card-header">
            <b>Request #{ret['id']}</b>
            <span class="pill-id">{ret['barcode']}</span>
            <span style="padding:2px 8px;border-radius:999px;background:{status_color};color:#fff;font-size:11px;">
              {ret['status']}
            </span>
          </div>
          <div class="row-small">Reader ID: <code>{ret['reader_id'] or ""}</code></div>
          <div class="row-small">Created: {ret['created_at']}</div>
          {f"<div class='row-small'>Approved: {ret['approved_at']}</div>" if ret['approved_at'] else ""}
        </div>
        """
    
    return HTMLResponse(f"""
    <html>
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1"/>
        <meta name="generator" content="AB2025"/>
        <!-- AB2025 -->
        <title>Admin - Statistics</title>
        <style>
          body {{
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            max-width: 900px;
            margin: 0 auto;
            padding: 16px;
            background: #f5f5f5;
          }}
          .header {{
            text-align: center;
            margin-bottom: 20px;
          }}
          h1 {{
            margin: 8px 0;
            font-size: 24px;
          }}
          .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px;
            margin-bottom: 24px;
          }}
          .stat-card {{
            background: #fff;
            border-radius: 12px;
            padding: 16px;
            text-align: center;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
          }}
          .stat-value {{
            font-size: 32px;
            font-weight: 700;
            margin: 8px 0;
          }}
          .stat-label {{
            font-size: 13px;
            color: #666;
          }}
          .section {{
            margin: 24px 0;
          }}
          .section-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 2px solid #e5e7eb;
          }}
          .card {{
            border-radius: 14px;
            padding: 12px 14px;
            margin: 10px 0;
            background: #fff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
          }}
          .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
            font-size: 14px;
            gap: 8px;
            flex-wrap: wrap;
          }}
          .card-header b {{
            font-size: 15px;
          }}
          .pill-id {{
            padding: 2px 8px;
            border-radius: 999px;
            background: #eef2ff;
            font-size: 12px;
          }}
          .row-small {{
            font-size: 13px;
            margin: 2px 0;
            word-break: break-all;
          }}
          .back-link {{
            display: inline-block;
            margin-top: 16px;
            padding: 8px 16px;
            background: #1d4ed8;
            color: #fff;
            text-decoration: none;
            border-radius: 999px;
            font-size: 14px;
          }}
          @media (max-width: 480px) {{
            body {{
              padding: 12px;
            }}
            .stats-grid {{
              grid-template-columns: repeat(2, 1fr);
            }}
          }}
        </style>
      </head>
      <body>
        <div class="header">
          <h1>📊 Статистика системы</h1>
          <a href="/admin/returns?pin={pin}" class="back-link">← Back to pending returns</a>
        </div>
        
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-value" style="color:#16a34a;">{total_issued}</div>
            <div class="stat-label">Выдано книг</div>
          </div>
          <div class="stat-card">
            <div class="stat-value" style="color:#16a34a;">{total_approved}</div>
            <div class="stat-label">Возвращено</div>
          </div>
          <div class="stat-card">
            <div class="stat-value" style="color:#f59e0b;">{total_pending}</div>
            <div class="stat-label">Ожидают возврата</div>
          </div>
          <div class="stat-card">
            <div class="stat-value" style="color:#dc2626;">{total_rejected}</div>
            <div class="stat-label">Отклонено</div>
          </div>
        </div>
        
        <div class="section">
          <div class="section-title">📚 Последние выданные книги ({len(issued_books)})</div>
          {issued_html if issued_books else "<p style='text-align:center;color:#666;'>Нет выданных книг</p>"}
        </div>
        
        <div class="section">
          <div class="section-title">↩️ Все заявки на возврат ({len(all_returns)})</div>
          {returns_html if all_returns else "<p style='text-align:center;color:#666;'>Нет заявок</p>"}
        </div>
        
        <div style="text-align:center;margin-top:32px;">
          <a href="/admin/returns?pin={pin}" class="back-link">← Back to pending returns</a>
        </div>
      </body>
    </html>
    """)
