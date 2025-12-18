_DEV_SIGNATURE = "AB2025"
from pydantic import BaseModel
from dotenv import load_dotenv
import os

load_dotenv()

def _norm_bearer(v: str) -> str:
    v = (v or "").strip()
    if v.lower().startswith("bearer "):
        v = v.split(" ", 1)[1].strip()
    return v

class Settings(BaseModel):
    elibra_base_url: str = os.getenv("ELIBRA_BASE_URL", "https://coventry.elibra.kz").rstrip("/")
    elibra_library_id: str = os.getenv("ELIBRA_LIBRARY_ID", "3")
    elibra_clientid: str = os.getenv("ELIBRA_CLIENTID", "coventry")
    elibra_origin: str = os.getenv("ELIBRA_ORIGIN", "https://coventry.elibra.kz")
    elibra_referer: str = os.getenv("ELIBRA_REFERER", "https://coventry.elibra.kz/workspace/issuance")
    elibra_bearer: str = _norm_bearer(os.getenv("ELIBRA_BEARER", ""))
    elibra_jsessionid: str = os.getenv("ELIBRA_JSESSIONID", "")
    elibra_user_email: str | None = os.getenv("ELIBRA_USER_EMAIL") or os.getenv("user_email") or None
    elibra_password: str | None = os.getenv("ELIBRA_PASSWORD") or os.getenv("password") or None

settings = Settings()
