import datetime
import socket
import platform
import logging
import httpx
from typing import Optional
from fastapi import Request
from app.core.config import DISCORD_STARTUP_WEBHOOK_URL, DISCORD_EVENTS_WEBHOOK_URL

logger = logging.getLogger(__name__)

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
    
    if extra:
        # Add other extra fields as inline fields
        for k, v in extra.items():
            if k != "main_path":
                fields.append({"name": k, "value": str(v)[:256], "inline": True})

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
