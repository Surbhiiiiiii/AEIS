"""
MongoDB connection singleton for the Enterprise AI System.
Collections: users, investigations, memory, incidents, alerts, agent_performance
"""
from pymongo import MongoClient, errors as pymongo_errors
from pymongo.collection import Collection
import os

MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/")
DB_NAME = os.getenv("MONGO_DB", "enterprise_ai")

_client: MongoClient = None

def get_client() -> MongoClient:
    global _client
    if _client is None:
        # 15 s is enough for Atlas cold-start; connectTimeoutMS covers TCP handshake
        _client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=15000,
            connectTimeoutMS=10000,
            socketTimeoutMS=15000,
        )
    return _client

def reset_client():
    """Force a fresh connection on next call (e.g. after a transient error)."""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    _client = None

def get_db():
    return get_client()[DB_NAME]

def users_col() -> Collection:
    return get_db()["users"]

def investigations_col() -> Collection:
    return get_db()["investigations"]

def memory_col() -> Collection:
    return get_db()["memory"]

def incidents_col() -> Collection:
    return get_db()["incidents"]

def alerts_col() -> Collection:
    return get_db()["alerts"]

def agent_performance_col() -> Collection:
    """Stores per-run critic scores and investigation outcomes for memory-based learning."""
    return get_db()["agent_performance"]


def db_health_check() -> dict:
    """
    Returns a dict with keys: ok (bool), message (str), error_type (str|None).
    Call this from the /health endpoint so Render can surface DB issues.
    """
    try:
        client = get_client()
        client.admin.command("ping")
        return {"ok": True, "message": "MongoDB Atlas reachable", "error_type": None}
    except pymongo_errors.OperationFailure as e:
        reset_client()
        code = e.details.get("code") if e.details else None
        if code == 8000:
            msg = ("MongoDB Atlas authentication failed (error 8000). "
                   "Check: (1) database user password in .env MONGO_URI, "
                   "(2) Network Access > IP Whitelist includes 0.0.0.0/0.")
        else:
            msg = f"MongoDB operation failed: {e}"
        return {"ok": False, "message": msg, "error_type": "OperationFailure"}
    except pymongo_errors.ServerSelectionTimeoutError as e:
        reset_client()
        return {"ok": False, "message": f"MongoDB connection timed out: {e}",
                "error_type": "Timeout"}
    except Exception as e:
        reset_client()
        return {"ok": False, "message": str(e), "error_type": type(e).__name__}


def ensure_indexes():
    """Create indexes for performance."""
    try:
        users_col().create_index("username", unique=True)
        users_col().create_index("email", unique=True)
        investigations_col().create_index("timestamp")
        alerts_col().create_index("timestamp")
        memory_col().create_index("timestamp")
        agent_performance_col().create_index("timestamp")
        agent_performance_col().create_index("critic_score")
    except Exception as e:
        print(f"[DB] Index creation warning: {e}")
