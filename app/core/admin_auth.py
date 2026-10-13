"""Admin login with a signed cookie instead of ?pin= in every URL.

The PIN (ADMIN_PIN in .env) is typed once on /admin/login; the browser then
keeps an HMAC-signed `admin_session` cookie for 30 days. One middleware
guards every admin-only path, so routes don't check the PIN themselves.
Old bookmarks with ?pin=<correct> still work: they set the cookie and
redirect to the same page without the PIN in the address bar.
"""
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.config import ADMIN_PIN

COOKIE = "admin_session"
MAX_AGE = 30 * 24 * 3600

# Changing ADMIN_PIN (or setting ADMIN_SESSION_SECRET) logs every admin out.
_SECRET = (os.getenv("ADMIN_SESSION_SECRET") or f"elibra-admin:{ADMIN_PIN}").encode()

# Paths only the librarian may use. /api/readers/search-by-cardcode stays
# public: the scan page needs it and it only answers an exact card number.
_PROTECTED_PREFIXES = ("/admin", "/rpa/", "/diag/")
_PROTECTED_EXACT = ("/api/readers/search",)
_OPEN = ("/admin/login", "/admin/logout")

_FAIL_LIMIT = 10
_FAIL_WINDOW = 15 * 60
_failures: dict[str, list[float]] = {}


def _sign(expires: int) -> str:
    sig = hmac.new(_SECRET, str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def is_admin(request: Request) -> bool:
    token = request.cookies.get(COOKIE, "")
    expires, _, sig = token.partition(".")
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(token, _sign(int(expires)))


def pin_ok(pin: str) -> bool:
    return bool(pin) and hmac.compare_digest(pin, ADMIN_PIN)


def set_session(response, request: Request) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, _sign(int(time.time()) + MAX_AGE), max_age=MAX_AGE,
                        httponly=True, samesite="lax", secure=secure, path="/")


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


def too_many_failures(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < _FAIL_WINDOW]
    _failures[ip] = recent
    return len(recent) >= _FAIL_LIMIT


def record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


def _protected(path: str) -> bool:
    if path in _OPEN:
        return False
    return path in _PROTECTED_EXACT or path == "/admin" or path.startswith(_PROTECTED_PREFIXES)


async def admin_guard(request: Request, call_next):
    path = request.url.path
    if not _protected(path):
        return await call_next(request)

    legacy_pin = request.query_params.get("pin")
    if request.method == "GET" and legacy_pin is not None:
        ip = client_ip(request)
        if not too_many_failures(ip) and pin_ok(legacy_pin):
            query = [(k, v) for k, v in request.query_params.multi_items() if k != "pin"]
            target = path if path != "/admin" else "/admin/returns"
            response = RedirectResponse(target + (f"?{urlencode(query)}" if query else ""), status_code=303)
            set_session(response, request)
            return response
        record_failure(ip)

    if is_admin(request):
        return await call_next(request)

    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        nxt = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse("/admin/login?" + urlencode({"next": nxt}), status_code=303)
    return JSONResponse({"ok": False, "error": "Admin login required"}, status_code=403)
