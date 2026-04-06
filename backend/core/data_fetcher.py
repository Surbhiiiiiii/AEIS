"""
data_fetcher.py
---------------
Fetches real incident records from MongoDB `incidents` collection.
Returns empty list if MongoDB is unavailable or collection is empty.
NO hardcoded / fake ticket data.
"""
from core.database import incidents_col


def fetch_tickets() -> list[dict]:
    """
    Fetch incidents from MongoDB `incidents` collection.
    Returns a list of incident dicts, or [] if unavailable.
    """
    try:
        docs = list(incidents_col().find({}, {"_id": 0}).limit(50))
        return docs
    except Exception as e:
        print(f"[data_fetcher] MongoDB unavailable: {e}")
        return []