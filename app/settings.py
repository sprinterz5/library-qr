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

    # Microsoft Forms Configuration
    # Base URL for the form (without query params)
    ms_forms_base_url: str = os.getenv("MS_FORMS_BASE_URL", "https://forms.office.com/Pages/ResponsePage.aspx?id=Wo9Ue8MLGEi2FKwGqF9k6bbiSE26FBRJuEFilMqojlxUQjhEODBGVk5KQzZKMldEVlZLWjMzSDZGVy4u")
    # Field ID for "Event Name"
    ms_forms_event_name_id: str = os.getenv("MS_FORMS_EVENT_NAME_ID", "rd525302ca55c4b21ab5e7de33e1542f3")
    # Field ID for "Event ID"
    ms_forms_event_id_id: str = os.getenv("MS_FORMS_EVENT_ID_ID", "r1a42da5a4a984e31ba5082b430b71b03")

settings = Settings()
