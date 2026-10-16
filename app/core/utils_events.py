
def get_event_registration_link(event) -> str | None:
    """
    Registration link for an event: its own override link if set, otherwise the site form.
    
    Args:
        event: A dictionary or sqlite3.Row object containing 'title' and 'id'.
        
    Returns:
        A URL, or None for announcements (nothing to register for).
    """
    # 1. Custom Override (Google Forms, external link, etc.)
    # Handle dict vs Row access safely
    event_type = event["type"] if isinstance(event, dict) else (event["type"] if "type" in event.keys() else "")
    
    # Announcements should not have registration links
    if event_type and str(event_type).upper() == "ANNOUNCEMENT":
        return None

    reg_link = event["registration_link"] if isinstance(event, dict) else (event["registration_link"] if "registration_link" in event.keys() else None)
    
    if reg_link and str(reg_link).strip():
        return str(reg_link).strip()

    # 2. Registration on the site itself (app/routers/registrations.py)
    event_id = event["id"] if isinstance(event, dict) else event["id"]
    return f"/events/{event_id}/register"
