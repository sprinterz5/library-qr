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
from fastapi.staticfiles import StaticFiles
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

# Mount static files for images and PDFs
app.mount("/img", StaticFiles(directory="img"), name="img")
app.mount("/pdf", StaticFiles(directory="pdf"), name="pdf")

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

@app.get("/", response_class=HTMLResponse)
def library_home():
    """Coventry University Kazakhstan Library - Main Website"""
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="description" content="Coventry University Kazakhstan Library - Access books, e-resources, and library services"/>
  <title>Library | Coventry University Kazakhstan</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    
    :root {
      --primary: #0055B7;
      --primary-dark: #003d82;
      --secondary: #00A3E0;
      --bg-light: #f8fafc;
      --bg-white: #ffffff;
      --text-dark: #1e293b;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --sidebar-active: #0055B7;
      --header-height: 64px;
      --tabs-height: 48px;
    }
    
    html { scroll-behavior: smooth; scroll-padding-top: calc(var(--header-height) + 16px); }
    body { font-family: 'Inter', system-ui, -apple-system, sans-serif; background: var(--bg-light); color: var(--text-dark); line-height: 1.6; }
    
    /* Header */
    .header {
      position: sticky; top: 0; z-index: 100;
      background: var(--bg-white); border-bottom: 1px solid var(--border);
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .header-container {
      max-width: 1200px; margin: 0 auto;
      display: flex; align-items: center; padding: 12px 24px; gap: 16px;
    }
    .header-logo { display: flex; align-items: center; gap: 12px; text-decoration: none; color: var(--text-dark); }
    .header-logo img { height: 40px; }
    .header-divider { width: 1px; height: 32px; background: var(--border); }
    .header-title { font-size: 18px; font-weight: 600; color: var(--text-dark); }
    .header-nav { display: flex; align-items: center; gap: 4px; margin-left: auto; }
    .header-nav-link {
      padding: 8px 14px; font-size: 14px; font-weight: 500; color: var(--text-muted);
      text-decoration: none; border-radius: 8px; transition: all 0.2s; white-space: nowrap;
    }
    .header-nav-link:hover { color: var(--primary); background: #f0f7ff; }
    .header-nav-link.active { color: var(--primary); background: #eff6ff; }
    
    /* Hero Section */
    .hero { background: var(--bg-white); padding: 32px 24px; border-bottom: 1px solid var(--border); }
    .hero-grid { max-width: 1200px; margin: 0 auto; display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 24px; }
    .hero-card {
      border-radius: 16px; padding: 28px; color: white; position: relative; overflow: hidden;
      transition: transform 0.2s, box-shadow 0.2s; cursor: pointer;
    }
    .hero-card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.15); }
    .hero-card-elibra { background: linear-gradient(135deg, #0055B7 0%, #003d82 100%); }
    .hero-card-uk { background: linear-gradient(135deg, #00A3E0 0%, #0077a3 100%); }
    .hero-card-icon { width: 48px; height: 48px; margin-bottom: 16px; opacity: 0.9; }
    .hero-card h3 { font-size: 18px; font-weight: 600; margin-bottom: 8px; }
    .hero-card p { font-size: 14px; opacity: 0.9; margin-bottom: 20px; line-height: 1.5; }
    .hero-card-btn {
      display: inline-flex; align-items: center; gap: 6px; background: rgba(255,255,255,0.2);
      padding: 10px 18px; border-radius: 8px; font-size: 14px; font-weight: 500;
      text-decoration: none; color: white; transition: background 0.2s;
    }
    .hero-card-btn:hover { background: rgba(255,255,255,0.3); }
    .hero-card-external { position: absolute; top: 16px; right: 16px; opacity: 0.6; }
    
    /* Tabs */
    .tabs-wrapper {
      position: sticky; top: var(--header-height); z-index: 90;
      background: var(--bg-white); border-bottom: 1px solid var(--border);
    }
    .tabs { max-width: 1200px; margin: 0 auto; display: flex; padding: 0 24px; gap: 0; overflow-x: auto; }
    .tab {
      padding: 14px 20px; font-size: 14px; font-weight: 500; color: var(--text-muted);
      text-decoration: none; border-bottom: 2px solid transparent; white-space: nowrap;
      display: flex; align-items: center; gap: 8px; transition: color 0.2s, border-color 0.2s;
    }
    .tab:hover { color: var(--primary); }
    .tab.active { color: var(--primary); border-bottom-color: var(--primary); }
    .tab-icon { font-size: 16px; }
    
    /* Main Layout */
    .main-content { max-width: 1200px; margin: 0 auto; display: flex; min-height: calc(100vh - 200px); }
    
    /* Sidebar */
    .sidebar {
      width: 220px; flex-shrink: 0; padding: 24px 0 24px 24px;
      position: sticky; top: calc(var(--header-height) + var(--tabs-height) + 16px);
      height: fit-content; display: none;
    }
    .sidebar.visible { display: block; }
    .sidebar-title { font-size: 13px; font-weight: 600; color: var(--text-muted); margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
    .sidebar-nav { display: flex; flex-direction: column; gap: 2px; }
    .sidebar-link {
      padding: 10px 16px; font-size: 14px; color: var(--text-dark); text-decoration: none;
      border-radius: 8px; border-left: 3px solid transparent; transition: all 0.2s;
    }
    .sidebar-link:hover { background: var(--bg-light); }
    .sidebar-link.active { background: var(--primary); color: white; border-left-color: var(--primary-dark); }
    
    /* Content Area */
    .content { flex: 1; padding: 24px; min-width: 0; }
    .section { display: none; padding: 16px 0; }
    .section.active { display: block; }
    .section-title { font-size: 24px; font-weight: 700; margin-bottom: 24px; color: var(--text-dark); }
    .subsection { margin-bottom: 32px; }
    #borrowing .subsection { display: none; }
    #borrowing .subsection.active { display: block; }
    .subsection:last-child { margin-bottom: 0; }
    .subsection-title { font-size: 18px; font-weight: 600; margin-bottom: 16px; color: var(--text-dark); }
    
    /* Alert Box */
    .alert {
      background: #eff6ff; border-left: 4px solid var(--primary); padding: 16px 20px;
      border-radius: 0 8px 8px 0; margin-bottom: 24px;
    }
    .alert-warning { background: #fef3c7; border-left-color: #f59e0b; }
    .alert strong { color: var(--primary); }
    .alert-warning strong { color: #d97706; }
    
    /* Info Cards */
    .info-cards { display: flex; flex-direction: column; gap: 16px; }
    .info-card {
      background: var(--bg-white); border: 1px solid var(--border); border-radius: 12px;
      padding: 20px; display: flex; gap: 16px; align-items: flex-start;
    }
    .info-card-icon { font-size: 24px; flex-shrink: 0; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: var(--bg-light); border-radius: 8px; }
    .info-card h4 { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
    .info-card p { font-size: 14px; color: var(--text-muted); margin: 0; }
    
    /* Steps */
    .steps { counter-reset: step; }
    .step { display: flex; gap: 16px; margin-bottom: 20px; position: relative; }
    .step::before {
      counter-increment: step; content: counter(step);
      width: 28px; height: 28px; background: var(--primary); color: white;
      border-radius: 50%; display: flex; align-items: center; justify-content: center;
      font-size: 13px; font-weight: 600; flex-shrink: 0;
    }
    .step-content { padding-top: 2px; }
    .step-content p { margin: 0; font-size: 15px; }
    
    /* Resource Grid */
    .resource-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; }
    .resource-card {
      background: var(--bg-white); border: 1px solid var(--border); border-radius: 12px;
      padding: 24px; transition: box-shadow 0.2s, border-color 0.2s;
    }
    .resource-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); border-color: var(--primary); }
    .resource-card h4 { font-size: 16px; font-weight: 600; margin-bottom: 8px; color: var(--primary); }
    .resource-card p { font-size: 14px; color: var(--text-muted); margin: 0; }
    
    /* Lists */
    .check-list { list-style: none; }
    .check-list li { padding: 8px 0; padding-left: 28px; position: relative; font-size: 15px; }
    .check-list li::before { content: "✓"; position: absolute; left: 0; color: #10b981; font-weight: 700; }
    .cross-list { list-style: none; }
    .cross-list li { padding: 8px 0; padding-left: 28px; position: relative; font-size: 15px; color: var(--text-muted); }
    .cross-list li::before { content: "✗"; position: absolute; left: 0; color: #ef4444; font-weight: 700; }
    
    /* Tables */
    .loan-table {
      width: 100%; border-collapse: collapse; margin-bottom: 16px;
      background: var(--bg-white); border-radius: 8px; overflow: hidden;
      border: 1px solid var(--border);
    }
    .loan-table th, .loan-table td { padding: 12px 16px; text-align: left; font-size: 14px; }
    .loan-table th { background: var(--bg-light); font-weight: 600; color: var(--text-dark); }
    .loan-table td { border-top: 1px solid var(--border); color: var(--text-muted); }
    
    /* Quick Links Block */
    .quick-links-block {
      background: var(--bg-white); border: 1px solid var(--border); border-radius: 12px;
      padding: 24px; margin-bottom: 24px;
    }
    .quick-links-block h3 { font-size: 18px; font-weight: 600; margin-bottom: 16px; color: var(--text-dark); }
    .quick-links-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
    .quick-link-btn {
      display: flex; align-items: center; gap: 10px; padding: 14px 18px;
      background: var(--primary); color: white; border-radius: 8px;
      text-decoration: none; font-size: 14px; font-weight: 500; transition: background 0.2s;
    }
    .quick-link-btn:hover { background: var(--primary-dark); }
    .quick-link-btn.secondary { background: var(--secondary); }
    .quick-link-btn.secondary:hover { background: #0077a3; }
    
    /* Policy Links */
    .policy-links { display: flex; flex-wrap: wrap; gap: 12px; }
    .policy-link {
      display: inline-flex; align-items: center; gap: 8px; padding: 12px 18px;
      background: var(--bg-white); border: 1px solid var(--border); border-radius: 8px;
      text-decoration: none; color: var(--text-dark); font-size: 14px; font-weight: 500;
      transition: border-color 0.2s, background 0.2s;
    }
    .policy-link:hover { border-color: var(--primary); background: #f0f7ff; }
    
    /* Policy Boxes */
    .policy-box {
      background: var(--bg-white); border: 1px solid var(--border); border-radius: 12px;
      padding: 20px; border-top: 4px solid var(--primary);
    }
    .policy-box-title { font-size: 15px; font-weight: 600; margin-bottom: 16px; color: var(--text-dark); }
    .policy-links-vertical { display: flex; flex-direction: column; gap: 8px; }
    .policy-link-item {
      display: flex; align-items: center; gap: 8px; padding: 8px 12px;
      background: var(--bg-light); border-radius: 6px; text-decoration: none;
      color: var(--primary); font-size: 14px; transition: background 0.2s;
    }
    .policy-link-item:hover { background: #e8f3ff; }
    
    /* Partner Resources Button */
    .partner-btn {
      display: inline-flex; align-items: center; gap: 8px; padding: 14px 24px;
      background: var(--primary); color: white; border-radius: 8px;
      text-decoration: none; font-size: 15px; font-weight: 600; transition: background 0.2s;
    }
    .partner-btn:hover { background: var(--primary-dark); }
    
    /* Contact Grid */
    .contact-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px; }
    .contact-card {
      background: var(--bg-white); border: 1px solid var(--border); border-radius: 12px; padding: 24px;
    }
    .contact-card h4 { font-size: 16px; font-weight: 600; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
    .contact-item { display: flex; align-items: center; gap: 12px; padding: 8px 0; font-size: 14px; }
    .contact-item a { color: var(--primary); text-decoration: none; }
    .contact-item a:hover { text-decoration: underline; }
    
    /* Quick Links */
    .quick-links { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 24px; }
    .quick-link {
      display: inline-flex; align-items: center; gap: 6px; padding: 10px 16px;
      background: var(--primary); color: white; border-radius: 8px;
      text-decoration: none; font-size: 14px; font-weight: 500; transition: background 0.2s;
    }
    .quick-link:hover { background: var(--primary-dark); }
    
    /* Footer */
    .footer {
      background: var(--bg-white); border-top: 1px solid var(--border);
      padding: 24px; text-align: center; font-size: 13px; color: var(--text-muted);
    }
    .hero-grid a{
      text-decoration: none;
    }
    /* Mobile */
    @media (max-width: 768px) {
      .header-container { padding: 10px 16px; flex-wrap: wrap; }
      .header-title { font-size: 16px; }
      .header-nav { gap: 2px; width: 100%; justify-content: center; margin-top: 8px; margin-left: 0; }
      .header-nav-link { padding: 6px 10px; font-size: 12px; }
      .hero { padding: 20px 16px; }
      .hero-grid { grid-template-columns: 1fr; }
      .sidebar { display: none !important; }
      .content { padding: 16px; }
      .main-content { display: block; }
      .section-title { font-size: 20px; }
      .resource-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <!-- Header -->
  <header class="header">
    <div class="header-container">
      <a href="/" class="header-logo">
        <img src="/img/logo.png" alt="Coventry University Kazakhstan"/>
      </a>
      <div class="header-divider"></div>
      <div class="header-title">Library</div>
      <nav class="header-nav">
        <a href="#home" class="header-nav-link active" data-tab="home">🏠 Home</a>
        <a href="#borrowing" class="header-nav-link" data-tab="borrowing">📚 Borrowing</a>
        <a href="#resources" class="header-nav-link" data-tab="resources">📑 Resources</a>
        <a href="#about" class="header-nav-link" data-tab="about">ℹ️ About</a>
        <a href="#contact" class="header-nav-link" data-tab="contact">🕐 Hours & Contact</a>
      </nav>
    </div>
  </header>

  <!-- HOME SECTION -->
  <section id="home" class="home-section">
    <!-- Hero Section -->
    <div class="hero">
    <div class="hero-grid">
      <a href="https://coventry.elibra.kz/" target="_blank" class="hero-card hero-card-elibra">
        <svg class="hero-card-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
        </svg>
        <svg class="hero-card-external" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
        </svg>
        <h3>E-Libra Kazakhstan Campus</h3>
        <p>Access the main library catalogue for Coventry University Kazakhstan. Search and borrow books, access e-books, journals, and other resources.</p>
        <span class="hero-card-btn">Open Catalogue →</span>
      </a>
      <a href="https://libguides.coventry.ac.uk/partners/resources" target="_blank" class="hero-card hero-card-uk">
        <svg class="hero-card-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
        </svg>
        <svg class="hero-card-external" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
        </svg>
        <h3>Coventry UK Resources</h3>
        <p>Access additional databases, e-journals, and digital resources from Coventry University UK available to partner institutions.</p>
        <span class="hero-card-btn">View Resources →</span>
      </a>
    </div>
  </div>

  <!-- Quick Links Section -->
  <div class="hero" style="padding-top: 0;">
    <div class="quick-links-block" style="max-width: 1200px; margin: 0 auto;">
      <h3>📌 Quick Links</h3>
      <div class="quick-links-grid">
        <a href="https://forms.office.com/Pages/ResponsePage.aspx?id=Wo9Ue8MLGEi2FKwGqF9k6XV8y9tFLuRJmBQDZwsNE3FUMEtGQzQ0RFdIV1YyV0JPWFk5UkdUOVlBOC4u" target="_blank" class="quick-link-btn">
          📄 Digital Copy Request
        </a>
        <a href="https://forms.office.com/Pages/ResponsePage.aspx?id=Wo9Ue8MLGEi2FKwGqF9k6XV8y9tFLuRJmBQDZwsNE3FUMERXV05ERlRaMExITDk4MEFBVE85OEhVUS4u" target="_blank" class="quick-link-btn">
          📚 Purchase Request
        </a>
        <a href="https://coventry-kz.libguides.com/az/databases?preview=ee5e7ca91e569826ae2a065ffee24a34" target="_blank" class="quick-link-btn secondary">
          🔤 A-Z Databases
        </a>
      </div>
    </div>
  </div>
</section>

  <!-- Main Content -->
  <div class="main-content">
    <!-- Sidebar (for Borrowing section) -->
    <aside class="sidebar visible" id="sidebar">
      <div class="sidebar-title">Borrowing</div>
      <nav class="sidebar-nav">
        <a href="#overview" class="sidebar-link active">Overview</a>
        <a href="#finding-items" class="sidebar-link">Finding Items</a>
        <a href="#borrow-return" class="sidebar-link">Borrow & Return</a>
        <a href="#donations" class="sidebar-link">Donations</a>
        <a href="#policies" class="sidebar-link">Policies</a>
      </nav>
    </aside>

    <!-- Content -->
    <main class="content">
      <!-- BORROWING SECTION -->
      <section id="borrowing" class="section">
        <h2 class="section-title">Borrowing Services</h2>
        
        <!-- Overview -->
        <div id="overview" class="subsection">
          <div class="alert">
            <strong>Important:</strong> Please ask librarian for your <strong>reader barcode</strong> to borrow books from the library.
          </div>
          
          <p style="margin-bottom: 24px; color: var(--text-dark); line-height: 1.7;">There are many resources available to you at the library. Most can be borrowed, but some are only available to use within the library (reference only). You also have access to an extensive collection of e-books, e-journals and e-resources via Coventry Library (UK).</p>
          
          <h3 class="subsection-title">What I Can Borrow</h3>
          <p style="margin-bottom: 16px; color: var(--text-muted);">There is no charge to borrow items. You are responsible for all items issued on your University ID card.</p>
          <table class="loan-table">
            <thead>
              <tr><th></th><th>Faculty & Graduates</th><th>Undergraduates & Staff</th></tr>
            </thead>
            <tbody>
              <tr><td><strong>Loans</strong></td><td>20 items</td><td>10 items</td></tr>
            </tbody>
          </table>
          
          <h3 class="subsection-title" style="margin-top: 32px;">Loan Periods</h3>
          <table class="loan-table">
            <thead>
              <tr><th>Type of Resource</th><th>Type of Loan</th><th>Length</th></tr>
            </thead>
            <tbody>
              <tr><td>Books</td><td>Standard</td><td>2 weeks</td></tr>
              <tr><td>Books</td><td>Short loan</td><td>2 hours</td></tr>
              <tr><td>Books</td><td>Reference</td><td>Cannot be borrowed – library use only</td></tr>
            </tbody>
          </table>
          
          <div class="alert alert-warning" style="margin-top: 24px;">
            <strong>Late Returns:</strong> May incur fines or suspended borrowing privileges. Contact <a href="mailto:library@coventry.edu.kz" style="color:#d97706;">library@coventry.edu.kz</a> for loan/account issues.
          </div>
        </div>
        
        <!-- Finding Items -->
        <div id="finding-items" class="subsection">
          <h3 class="subsection-title">Finding Items</h3>
          <div class="steps">
            <div class="step">
              <div class="step-content"><p>Search the catalog with keywords or author surname</p></div>
            </div>
            <div class="step">
              <div class="step-content"><p>Check availability (shelf number and copies shown)</p></div>
            </div>
            <div class="step">
              <div class="step-content"><p>Note the shelf number (on book spine label)</p></div>
            </div>
            <div class="step">
              <div class="step-content"><p>Locate the shelf (items arranged alphabetically/numerically)</p></div>
            </div>
          </div>
          <div class="alert" style="margin-top: 16px;">
            <strong>Tip:</strong> Books on the same topic are usually in the same area. Ask a librarian if you need help!
          </div>
        </div>
        
        <!-- Borrow & Return -->
        <div id="borrow-return" class="subsection">
          <h3 class="subsection-title">Borrow & Return</h3>
          <div class="info-cards">
            <div class="info-card">
              <div class="info-card-icon">✅</div>
              <div>
                <h4>Borrowing</h4>
                <p>Take the item to the Circulation Desk with your Student ID Card</p>
              </div>
            </div>
            <div class="info-card">
              <div class="info-card-icon">↩️</div>
              <div>
                <h4>Returning</h4>
                <p>Return to the Circulation Desk or use the drop-box before the due date</p>
              </div>
            </div>
            <div class="info-card">
              <div class="info-card-icon">🔄</div>
              <div>
                <h4>Renewals</h4>
                <p>Done through the Locate online system (automatic or manual). Allowed if no other student has a hold.</p>
              </div>
            </div>
          </div>
        </div>
        
        <!-- Donations -->
        <div id="donations" class="subsection">
          <h3 class="subsection-title">Donating Materials</h3>
          <p style="margin-bottom: 16px; color: var(--text-dark); line-height: 1.7;">Please get in touch if you would like to donate material to the library. It's helpful for us to know in advance what you are planning to donate, because not everything is suitable for our collection. You can get in touch with us through <a href="mailto:library@coventry.edu.kz" style="color: var(--primary); font-weight: 500;">library@coventry.edu.kz</a>.</p>
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px;">
            <div>
              <h4 style="font-size: 15px; font-weight: 600; margin-bottom: 12px; color: #10b981;">✓ As a general guide we would welcome as donations:</h4>
              <ul class="check-list">
                <li>Extra copies of current books in demand</li>
                <li>Recently published materials that fit our teaching or research profile</li>
                <li>Rare items that are suitable for special collections</li>
              </ul>
            </div>
            <div>
              <h4 style="font-size: 15px; font-weight: 600; margin-bottom: 12px; color: #ef4444;">✗ Unfortunately, we are not able to take:</h4>
              <ul class="cross-list">
                <li>Old superseded editions of textbooks</li>
                <li>Duplicates of existing holdings where we don't need any further copies</li>
                <li>Journals/periodicals</li>
                <li>Items in poor condition</li>
                <li>Large collections that we can't accommodate</li>
                <li>Items that don't fit our teaching/research profile</li>
              </ul>
            </div>
          </div>
          <p style="margin-top: 20px; color: var(--text-muted); font-size: 14px;">Accepted donations will be evaluated by a subject specialist, and we reserve the right to dispose of any items we feel unsuitable for the collection.</p>
        </div>
        
        <!-- Policies -->
        <div id="policies" class="subsection">
          <h3 class="subsection-title">Policies & Information</h3>
          
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 24px; margin-bottom: 24px;">
            <!-- Library Regulations Box -->
            <div class="policy-box">
              <h4 class="policy-box-title">Where can I see a copy of library regulations?</h4>
              <div class="policy-links-vertical">
                <a href="/pdf/CUK_Library_Policy.pdf" target="_blank" class="policy-link-item">📄 Library Policy</a>
                <a href="/pdf/CUK_Copyright_Statement.pdf" target="_blank" class="policy-link-item">📄 Copyright Statement</a>
              </div>
            </div>
            
            <!-- Collection Development Box -->
            <div class="policy-box">
              <h4 class="policy-box-title">How do you decide what resources are included in the library collection?</h4>
              <div class="policy-links-vertical">
                <a href="/pdf/Library_Collection_Development_and_Information_Resource_Strategy__1_.pdf" target="_blank" class="policy-link-item">📄 Collection Development and Information Resource Strategy</a>
              </div>
            </div>
            
            <!-- Customer Service Box -->
            <div class="policy-box">
              <h4 class="policy-box-title">What levels of customer service can I expect from you?</h4>
              <p style="color: var(--text-dark); margin-bottom: 12px; line-height: 1.6;">The Library aims to be a welcoming, accessible, and supportive service providing our customers with the resources they need to succeed.</p>
              <p style="font-weight: 600; margin-bottom: 8px; color: var(--text-dark);">We aim to be:</p>
              <ul style="color: var(--text-muted); padding-left: 20px;">
                <li>Customer-focussed</li>
                <li>Inclusive</li>
                <li>Ethically and socially responsible</li>
                <li>Innovative</li>
                <li>Always learning</li>
              </ul>
            </div>
          </div>
        </div>
      </section>
      
      <!-- RESOURCES SECTION -->
      <section id="resources" class="section">
        <h2 class="section-title">Resources</h2>
        
        <div class="subsection active">
          <h3 class="subsection-title">Digital Resources</h3>
          <div class="alert" style="margin-bottom: 24px;">
            <strong>Access:</strong> Use your university credentials (UK email for digital resources, local email for elibra catalogue) to access all digital resources.
          </div>
          
          <h4 style="font-size: 16px; font-weight: 600; margin-bottom: 16px;">Partner Resources</h4>
          <a href="https://libguides.coventry.ac.uk/partners/resources" target="_blank" class="partner-btn" style="margin-bottom: 32px; display: inline-flex;">
            Coventry UK Resources →
          </a>
        </div>
        
        <div class="subsection active">
          <h3 class="subsection-title">Databases</h3>
          <div class="resource-grid">
            <a href="https://ebookcentral.proquest.com/lib/coventry/search.action" target="_blank" class="resource-card" style="text-decoration: none;">
              <h4>Ebook Central <span style="float: right;">→</span></h4>
              <p>E-book platform offering access to a large number of full-text e-books in multiple subject areas.</p>
            </a>
            <a href="https://shibbolethsp.jstor.org/start?entityID=https%3A%2F%2Fcoventry.ac.uk%2Fidp&site=jstor&dest=%2F" target="_blank" class="resource-card" style="text-decoration: none;">
              <h4>JSTOR <span style="float: right;">→</span></h4>
              <p>Digital library of academic journals, books, and primary sources.</p>
            </a>
            <a href="https://www.proquest.com/central/fromDatabasesLayer?accountid=10286" target="_blank" class="resource-card" style="text-decoration: none;">
              <h4>ProQuest Central <span style="float: right;">→</span></h4>
              <p>Multiple databases covering journal literature across disciplines.</p>
            </a>
            <a href="https://auth.elsevier.com/ShibAuth/institutionLogin?entityID=https://coventry.ac.uk/idp&appReturnURL=https://www.sciencedirect.com&_oafollow=false" target="_blank" class="resource-card" style="text-decoration: none;">
              <h4>ScienceDirect <span style="float: right;">→</span></h4>
              <p>Science, technology and medicine full text and bibliographic information.</p>
            </a>
            <a href="https://auth.elsevier.com/ShibAuth/institutionLogin?entityID=https://coventry.ac.uk/idp&appReturnURL=https://www.scopus.com&_oafollow=false" target="_blank" class="resource-card" style="text-decoration: none;">
              <h4>Scopus <span style="float: right;">→</span></h4>
              <p>Comprehensive scientific, medical, technical and social science database.</p>
            </a>
          </div>
        </div>
      </section>
      
      <!-- ABOUT SECTION -->
      <section id="about" class="section">
        <h2 class="section-title">About the Library</h2>
        
        <div class="subsection active">
          <p style="font-size: 16px; line-height: 1.8; color: var(--text-dark); margin-bottom: 24px;">The Coventry University Kazakhstan Library serves as the academic center of the campus, providing students with access to physical collections and extensive digital resources from Lanchester Library, UK.</p>
          
          <h3 class="subsection-title">Our Mission</h3>
          <p style="font-size: 16px; line-height: 1.7; color: var(--text-dark); margin-bottom: 24px;">Supporting learning and research by providing quality information resources and a comfortable study environment.</p>
          
          <div class="info-cards" style="margin-bottom: 24px;">
            <div class="info-card">
              <div class="info-card-icon">📍</div>
              <div>
                <h4>Location</h4>
                <p>Korgalzhyn Highway, 13A, Astana, Kazakhstan</p>
              </div>
            </div>
            <div class="info-card">
              <div class="info-card-icon">🎓</div>
              <div>
                <h4>Access</h4>
                <p>Students and staff with valid Student ID Card</p>
              </div>
            </div>
          </div>
        </div>
        
        <div class="subsection active">
          <h3 class="subsection-title">Services & Support</h3>
          <div class="resource-grid">
            <div class="resource-card">
              <h4>📝 Harvard Referencing</h4>
              <p>Citation and formatting support</p>
            </div>
            <div class="resource-card">
              <h4>🔍 Search Training</h4>
              <p>Learn to use Locate effectively</p>
            </div>
            <div class="resource-card">
              <h4>✍️ Writing Support</h4>
              <p>Help with essays and dissertations</p>
            </div>
            <div class="resource-card">
              <h4>🤫 Study Spaces</h4>
              <p>Silent and group study areas</p>
            </div>
          </div>
        </div>
      </section>
      
      <!-- CONTACT SECTION -->
      <section id="contact" class="section">
        <h2 class="section-title">Hours & Contact</h2>
        
        <div class="contact-grid">
          <div class="contact-card">
            <h4>🕐 Library Hours</h4>
            <div class="contact-item"><strong>Monday - Friday:</strong> 09:00 - 18:00</div>
            <div class="contact-item"><strong>Saturday - Sunday:</strong> Closed</div>
            <div class="contact-item"><strong>Public Holidays:</strong> Closed</div>
            <p style="font-size: 13px; color: var(--text-muted); margin-top: 12px;">Hours may change during holidays and exam periods. Check campus screens for updates.</p>
          </div>
          
          <div class="contact-card">
            <h4>📞 Contact Information</h4>
            <div class="contact-item">📍 Korgalzhyn Highway, 13A, Astana, Kazakhstan</div>
            <div class="contact-item">📞 <a href="tel:+77003173333">+7 (700) 317-33-33</a></div>
            <div class="contact-item">📞 <a href="tel:+77003180023">+7 (700) 318-00-23</a></div>
            <div class="contact-item">✉️ <a href="mailto:library@coventry.edu.kz">library@coventry.edu.kz</a></div>
          </div>
        </div>
        
        <div class="quick-links">
          <a href="https://coventry-kz.libguides.com/" target="_blank" class="quick-link">📚 LibGuides</a>
          <a href="https://coventry.elibra.kz/" target="_blank" class="quick-link">🔍 Locate</a>
          <a href="https://moodle.coventry.edu.kz/" target="_blank" class="quick-link">🎓 Moodle</a>
          <a href="https://solar.coventry.edu.kz/" target="_blank" class="quick-link">☀️ Solar</a>
        </div>
      </section>
    </main>
  </div>

  <!-- Events Section -->
  <section class="hero" style="background: var(--bg-light);">
    <div style="max-width: 1200px; margin: 0 auto;">
      <h2 style="font-size: 24px; font-weight: 700; margin-bottom: 24px; color: var(--text-dark);">📅 Upcoming Events</h2>
      <div class="resource-grid">
        <div class="resource-card" style="border-left: 4px solid var(--primary);">
          <p style="font-size: 12px; color: var(--primary); font-weight: 600; margin-bottom: 8px;">WORKSHOP</p>
          <h4 style="color: var(--text-dark);">Library Resources Introduction</h4>
          <p style="margin-top: 8px;">Learn how to use library databases and resources effectively.</p>
          <p style="margin-top: 12px; font-size: 13px; color: var(--text-muted);">📍 Library • 🗓️ Check notice board for dates</p>
        </div>
        <div class="resource-card" style="border-left: 4px solid var(--secondary);">
          <p style="font-size: 12px; color: var(--secondary); font-weight: 600; margin-bottom: 8px;">TRAINING</p>
          <h4 style="color: var(--text-dark);">Harvard Referencing Workshop</h4>
          <p style="margin-top: 8px;">Citation and formatting training for academic writing.</p>
          <p style="margin-top: 12px; font-size: 13px; color: var(--text-muted);">📍 Library • 🗓️ Check notice board for dates</p>
        </div>
        <div class="resource-card" style="border-left: 4px solid #10b981;">
          <p style="font-size: 12px; color: #10b981; font-weight: 600; margin-bottom: 8px;">SESSION</p>
          <h4 style="color: var(--text-dark);">Database Search Training</h4>
          <p style="margin-top: 8px;">How to search and navigate academic databases efficiently.</p>
          <p style="margin-top: 12px; font-size: 13px; color: var(--text-muted);">📍 Library • 🗓️ Check notice board for dates</p>
        </div>
      </div>
      <p style="margin-top: 16px; font-size: 14px; color: var(--text-muted);">Contact <a href="mailto:library@coventry.edu.kz" style="color: var(--primary);">library@coventry.edu.kz</a> to request a training session for your group.</p>
    </div>
  </section>

  <!-- Footer -->
  <footer class="footer">
    <p>© 2025 Coventry University Kazakhstan Library</p>
  </footer>

  <script>
    // Tab switching navigation
    document.addEventListener('DOMContentLoaded', () => {
      const navLinks = document.querySelectorAll('.header-nav-link');
      const sidebarLinks = document.querySelectorAll('.sidebar-link');
      const sidebar = document.getElementById('sidebar');
      const sections = document.querySelectorAll('.section');
      const subsections = document.querySelectorAll('.subsection');
      const homeSection = document.getElementById('home');
      const mainContent = document.querySelector('.main-content');
      const eventsSection = document.querySelector('.hero[style*="background: var(--bg-light)"]');
      
      // Switch to a specific tab
      function switchTab(tabId) {
        // Update nav links
        navLinks.forEach(link => link.classList.toggle('active', link.dataset.tab === tabId));
        
        // Handle home vs other sections
        if (tabId === 'home') {
          homeSection.style.display = 'block';
          mainContent.style.display = 'none';
          if (eventsSection) eventsSection.style.display = 'block';
        } else {
          homeSection.style.display = 'none';
          mainContent.style.display = 'flex';
          if (eventsSection) eventsSection.style.display = 'none';
          
          // Show/hide sections
          sections.forEach(section => section.classList.toggle('active', section.id === tabId));
          
          // Show/hide sidebar (only for borrowing)
          if (tabId === 'borrowing') {
            sidebar.classList.add('visible');
            switchSubsection('overview');
          } else {
            sidebar.classList.remove('visible');
          }
        }
      }
      
      // Nav link click handlers
      navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
          e.preventDefault();
          switchTab(link.dataset.tab);
        });
      });
      
      // Switch to a specific subsection within borrowing
      function switchSubsection(subsectionId) {
        // Update sidebar links
        sidebarLinks.forEach(link => {
          const linkTarget = link.getAttribute('href').substring(1);
          link.classList.toggle('active', linkTarget === subsectionId);
        });
        
        // Show/hide subsections
        subsections.forEach(sub => sub.classList.toggle('active', sub.id === subsectionId));
      }
      
      // Sidebar link click handlers (switch subsections)
      sidebarLinks.forEach(link => {
        link.addEventListener('click', (e) => {
          e.preventDefault();
          const targetId = link.getAttribute('href').substring(1);
          switchSubsection(targetId);
        });
      });
      
      // Initialize: show home by default
      switchTab('home');
    });
  </script>
</body>
</html>
"""
    return HTMLResponse(html)


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
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --primary: #0055B7;
      --primary-dark: #003d82;
      --secondary: #00A3E0;
      --bg-light: #f8fafc;
      --bg-white: #ffffff;
      --text-dark: #1e293b;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --success: #10b981;
      --warning: #f59e0b;
    }
    body{font-family:'Inter',system-ui,-apple-system,sans-serif;background:var(--bg-light);color:var(--text-dark);margin:0;line-height:1.6}
    .wrap{max-width:600px;margin:0 auto;padding:24px}
    .header{background:var(--bg-white);border-bottom:1px solid var(--border);padding:16px 24px;margin-bottom:24px}
    .header-inner{max-width:600px;margin:0 auto;display:flex;align-items:center;gap:12px}
    .header-logo{height:36px}
    .header-title{font-size:20px;font-weight:700;color:var(--primary)}
    .badge{font-size:12px;color:var(--text-muted);background:var(--bg-light);border:1px solid var(--border);padding:6px 12px;border-radius:999px;margin-left:auto}
    .card{background:var(--bg-white);border:1px solid var(--border);border-radius:16px;padding:24px;margin:16px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}
    label{display:block;font-size:13px;color:var(--text-muted);margin:16px 0 8px;font-weight:500}
    input,select,button{
      width:100%;padding:14px 16px;border-radius:10px;border:1px solid var(--border);
      background:var(--bg-white);color:var(--text-dark);font-size:16px;box-sizing:border-box;
      font-family:inherit;transition:border-color 0.2s
    }
    input:focus,select:focus{outline:none;border-color:var(--primary)}
    input::placeholder{color:#94a3b8}
    button{cursor:pointer;font-weight:600;border:none}
    .row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
    @media (max-width:720px){.row{grid-template-columns:1fr}}
    .btnrow{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:20px}
    @media (max-width:720px){.btnrow{grid-template-columns:1fr}}
    .issue{background:var(--success);color:white}
    .issue:hover{background:#059669}
    .return{background:var(--warning);color:white}
    .return:hover{background:#d97706}
    .ghost{background:var(--bg-light);border:1px solid var(--border);color:var(--text-dark)}
    .ghost:hover{background:#e2e8f0}
    .muted{color:var(--text-muted);font-size:13px;line-height:1.4}
    .pill{display:inline-block;padding:6px 12px;border-radius:999px;border:1px solid var(--border);background:var(--bg-white);color:var(--text-dark);font-size:12px}
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
    .status-loading{color:var(--primary);font-weight:600}
    .hr{height:1px;background:var(--border);margin:20px 0}
    .result{border:1px solid var(--success);background:#ecfdf5;border-radius:12px;padding:16px;margin-top:12px}
    .result:hover{border-color:#059669}
    .small{font-size:12px;color:var(--text-muted);margin-top:4px;word-break:break-word}
    .barcode-wrapper{position:relative}
    .barcode-wrapper input{padding-right:70px}
    #qr-camera-btn{position:absolute;right:8px;top:50%;transform:translateY(-50%);width:50px;height:50px;border:2px solid var(--primary);background:var(--bg-white);border-radius:10px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:22px;z-index:10;-webkit-tap-highlight-color:transparent;touch-action:manipulation}
    #qr-camera-btn:hover{background:var(--bg-light);border-color:var(--primary-dark)}
    #qr-reader{position:absolute;right:8px;top:50%;transform:translateY(-50%);width:50px;height:50px;border:2px solid var(--primary);background:var(--bg-white);border-radius:10px;overflow:hidden;z-index:2;display:none}
    #qr-reader.active{width:200px;height:200px;right:8px;top:auto;bottom:calc(100% + 8px);transform:none}
    @media (max-width:720px){
      .barcode-wrapper input{padding-right:70px}
      #qr-camera-btn,#qr-reader{width:52px;height:52px;right:8px;font-size:20px}
      #qr-reader.active{width:200px;height:200px;right:8px}
    }
    .alert{background:#eff6ff;border-left:4px solid var(--primary);padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:16px;font-size:14px}
  </style>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
</head>
<body>
  <!-- Header -->
  <div class="header">
    <div class="header-inner">
      <img src="/img/logo.png" alt="Coventry University" class="header-logo"/>
      <div class="header-title">Library Desk</div>
      <div class="badge">Reader saved • QR Scan</div>
    </div>
  </div>

  <div class="wrap">
    <div class="card">
      <form id="deskForm" method="POST" action="/submit">
        <!-- action hidden: set by buttons -->
        <input type="hidden" name="action" id="action" value="issue"/>
        <!-- reader_id hidden: set by search selection or loaded from localStorage -->
        <input type="hidden" name="reader_id" id="reader_id" value=""/>
        <!-- card_barcode hidden: для передачи в RPA (более надежный поиск) -->
        <input type="hidden" name="card_barcode" id="card_barcode" value=""/>

        <label>Enter last 5 digits of your cardcode</label>
        <input id="cardcodeSuffix" type="text" inputmode="numeric" pattern="[0-9]*" maxlength="5" placeholder="e.g. 04099" style="font-size:20px;text-align:center;letter-spacing:2px"/>

        <div id="readerResult" style="margin-top:16px;display:none">
          <div class="result">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
              <span style="font-size:20px">✅</span>
              <div style="font-size:13px;color:var(--text-muted)">Reader found:</div>
            </div>
            <div style="font-size:18px;font-weight:700;color:var(--success);line-height:1.4" id="readerName"></div>
            <div style="font-size:12px;color:var(--text-muted);margin-top:6px" id="readerCardcode"></div>
          </div>
        </div>

        <div class="muted" style="margin-top:8px">
          <button type="button" class="ghost" onclick="clearReader()" style="width:auto;padding:8px 14px;font-size:13px">Change Reader</button>
        </div>

        <div class="hr"></div>

        <label>Book barcode (auto-fills from QR)</label>
        <div class="barcode-wrapper">
          <input name="barcode" id="barcode" placeholder="2100000005088" required />
          <div id="qr-camera-btn" title="Scan QR">📷</div>
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
            <button type="button" class="ghost" onclick="clearBarcode()">Clear Barcode</button>
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
      
      // Check if camera API is available
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        const isLocalhost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
        const isHttps = window.location.protocol === "https:";
        if (!isLocalhost && !isHttps) {
          setStatus("❌ Для доступа к камере нужен HTTPS или localhost. Открой через http://localhost:8000/scan");
        } else {
          setStatus("❌ Ваш браузер не поддерживает доступ к камере");
        }
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
        let errorMsg = "Неизвестная ошибка";
        if (err && err.message) {
          errorMsg = err.message;
        } else if (err && err.toString) {
          errorMsg = err.toString();
        } else if (typeof err === "string") {
          errorMsg = err;
        }
        
        // More specific error messages
        if (errorMsg.includes("Permission denied") || errorMsg.includes("NotAllowedError")) {
          errorMsg = "Разрешение на камеру отклонено. Разрешите доступ в настройках браузера";
        } else if (errorMsg.includes("NotFoundError") || errorMsg.includes("No camera")) {
          errorMsg = "Камера не найдена";
        } else if (errorMsg.includes("NotReadableError") || errorMsg.includes("TrackStartError")) {
          errorMsg = "Камера занята другим приложением";
        } else if (errorMsg.includes("OverconstrainedError")) {
          errorMsg = "Камера не поддерживает требуемые параметры";
        }
        
        setStatus("❌ Ошибка доступа к камере: " + errorMsg);
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
          *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
          :root {{
            --primary: #0055B7;
            --primary-dark: #003d82;
            --bg-light: #f8fafc;
            --bg-white: #ffffff;
            --text-dark: #1e293b;
            --text-muted: #64748b;
            --border: #e2e8f0;
            --success: #10b981;
            --warning: #f59e0b;
          }}
          body {{
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 24px;
            background: var(--bg-light);
            color: var(--text-dark);
            line-height: 1.6;
          }}
          .header {{
            background: var(--bg-white);
            border-bottom: 1px solid var(--border);
            padding: 16px 24px;
            margin: -24px -24px 24px -24px;
            display: flex;
            align-items: center;
            gap: 12px;
          }}
          .header img {{ height: 36px; }}
          .header-title {{ font-size: 20px; font-weight: 700; color: var(--primary); }}
          h2 {{
            margin: 0 0 4px;
            font-size: 22px;
            text-align: center;
            color: var(--text-dark);
          }}
          .meta {{
            text-align: center;
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 16px;
          }}
          .nav-links {{
            margin-top: 12px;
            display: flex;
            gap: 10px;
            justify-content: center;
            flex-wrap: wrap;
          }}
          .nav-link {{
            display: inline-block;
            padding: 10px 18px;
            background: var(--primary);
            color: #fff;
            text-decoration: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            transition: background 0.2s;
          }}
          .nav-link:hover {{ background: var(--primary-dark); }}
          .card {{
            border-radius: 12px;
            padding: 16px;
            margin: 12px 0;
            background: var(--bg-white);
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            border: 1px solid var(--border);
          }}
          .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            font-size: 14px;
          }}
          .card-header b {{
            font-size: 16px;
            color: var(--text-dark);
          }}
          .pill-id {{
            padding: 4px 10px;
            border-radius: 999px;
            background: #eff6ff;
            color: var(--primary);
            font-size: 12px;
            font-weight: 500;
          }}
          .row-small {{
            font-size: 13px;
            margin: 4px 0;
            word-break: break-all;
            color: var(--text-muted);
          }}
          .row-small code {{
            background: var(--bg-light);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 12px;
          }}
          .btn-row {{
            display: flex;
            gap: 10px;
            margin-top: 14px;
          }}
          .btn-row form {{
            flex: 1;
          }}
          button.admin-btn {{
            display: block;
            width: 100%;
            padding: 12px 10px;
            border-radius: 8px;
            border: none;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            font-family: inherit;
            transition: background 0.2s;
          }}
          button.approve {{
            background: var(--success);
            color: #fff;
          }}
          button.approve:hover {{ background: #059669; }}
          button.reject {{
            background: var(--warning);
            color: #fff;
          }}
          button.reject:hover {{ background: #d97706; }}
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
          .empty {{ text-align: center; padding: 32px; color: var(--text-muted); }}
          @media (max-width: 480px) {{
            body {{ padding: 16px; }}
            .header {{ margin: -16px -16px 16px -16px; }}
            .card {{ padding: 12px; }}
            button.admin-btn {{ font-size: 14px; padding: 14px 10px; }}
          }}
        </style>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
      </head>
      <body>
        <div class="header">
          <img src="/img/logo.png" alt="Coventry University"/>
          <div class="header-title">Admin Panel</div>
        </div>
        <div style="text-align:center;margin-bottom:20px;">
          <h2>Pending Return Requests</h2>
          <div class="meta">Total requests: {len(rows)}</div>
          <div class="nav-links">
            <a href="/admin/search?pin={pin}" class="nav-link">🔍 Search Readers</a>
            <a href="/admin/stats?pin={pin}" class="nav-link">📊 Stats & Logs</a>
          </div>
        </div>
        {items if items else "<p class='empty'>No pending requests</p>"}
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
