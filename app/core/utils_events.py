from urllib.parse import quote
from app.settings import settings

def get_event_registration_link(event) -> str | None:
    """
    Generates a pre-filled Microsoft Forms registration link for a given event.
    
    Args:
        event: A dictionary or sqlite3.Row object containing 'title' and 'id'.
        
    Returns:
        A URL string if configuration is present, otherwise None.
    """
    # 1. Custom Override (Google Forms, external link, etc.)
    # Handle dict vs Row access safely
    reg_link = event["registration_link"] if isinstance(event, dict) else (event["registration_link"] if "registration_link" in event.keys() else None)
    
    if reg_link and str(reg_link).strip():
        return str(reg_link).strip()

    # 2. Microsoft Forms Auto-Generation
    if not settings.ms_forms_base_url:
        return None
        
    # Extract data, handling both dict (from API) and Row (from DB)
    title = event["title"] if isinstance(event, dict) else event["title"]
    event_id = event["id"] if isinstance(event, dict) else event["id"]
    
    # URL Encode values
    safe_title = quote(str(title))
    safe_id = quote(str(event_id))
    
    # Construct URL
    # Format: BASE_URL & NameID=Title & IdID=ID
    url = f"{settings.ms_forms_base_url}&{settings.ms_forms_event_name_id}={safe_title}&{settings.ms_forms_event_id_id}={safe_id}"
    
    return url
