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

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os, asyncio, platform
from app.core.config import (
    HEARTBEAT_SECONDS, EXPECTED_ACTIVATION_KEY, EXPECTED_ACTIVATION_PASSWORD,
    APP_ACTIVATION_KEY, APP_ACTIVATION_PASSWORD
)
from app.core.database import init_db
from app.core.utils import notify_activity, logger
from app.core.rpa import rpa
from app.routers import pages, scan, admin, api, chatbot

# Check activation
if APP_ACTIVATION_KEY != EXPECTED_ACTIVATION_KEY or APP_ACTIVATION_PASSWORD != EXPECTED_ACTIVATION_PASSWORD:
    raise RuntimeError("Application activation failed. Invalid APP_ACTIVATION_KEY or APP_ACTIVATION_PASSWORD.")

# Initialize DB
init_db()

# Heartbeat
_heartbeat_task = None

async def _heartbeat_loop() -> None:
    while True:
        try:
            await notify_activity("heartbeat", None, {})
        except Exception as e:
            logger.warning(f"Heartbeat notification failed: {e}")
        await asyncio.sleep(HEARTBEAT_SECONDS)

from app.core.templates import templates
from app.core.utils_events import get_event_registration_link

app = FastAPI(title="Coventry Library — Issue/Return (Local Pilot)")

# Register global template helpers
templates.env.globals["get_event_registration_link"] = get_event_registration_link

# Mount static files
app.mount("/img", StaticFiles(directory="img"), name="img")
app.mount("/pdf", StaticFiles(directory="pdf"), name="pdf")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include Routers
app.include_router(pages.router)
app.include_router(scan.router)
app.include_router(admin.router)
app.include_router(api.router)
app.include_router(chatbot.router)

def _install_windows_connection_reset_filter() -> None:
    if platform.system() != "Windows":
        return

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handler(loop, context):
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054:
            logger.debug("Ignored Windows client connection reset: %s", exc)
            return
        if previous_handler is not None:
            previous_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)

@app.on_event("startup")
async def startup_event():
    global _heartbeat_task
    _install_windows_connection_reset_filter()
    await notify_activity(
        "startup",
        None,
        {
            "activation_key_ok": True,
            "main_path": os.path.abspath(__file__),
            "modules": "pages, scan, admin, api, chatbot"
        },
    )
    if HEARTBEAT_SECONDS > 0 and _heartbeat_task is None:
        _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        is_local_windows = (platform.system() == "Windows")
        await rpa.initialize(headless=not is_local_windows)
        logger.info("RPA initialized on startup")
    except Exception as e:
        logger.error(f"Failed to initialize RPA on startup: {e}", exc_info=True)
        logger.warning("RPA will be initialized on first use.")

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
