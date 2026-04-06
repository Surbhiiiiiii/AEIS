"""
MongoDB connection singleton for the Enterprise AI System.
Collections: users, investigations, memory, incidents, alerts, agent_performance
"""
from pymongo import MongoClient
from pymongo.collection import Collection
import os

MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/")
DB_NAME = os.getenv("MONGO_DB", "enterprise_ai")

_client: MongoClient = None

def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return _client

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
