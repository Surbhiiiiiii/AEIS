"""
MongoDB-backed Memory system.
Replaces JSON file storage with pymongo.
API surface is backward-compatible with the old file-based Memory class.
"""
from datetime import datetime
from core.database import memory_col

class Memory:
    def __init__(self, filename=None):
        # filename param kept for backward-compat, ignored
        pass

    # ─── Events ──────────────────────────────────────────────────────────────

    def add_event(self, agent: str, action: str, details: dict):
        try:
            memory_col().insert_one({
                "type": "event",
                "agent": agent,
                "action": action,
                "details": details,
                "timestamp": datetime.utcnow().isoformat()
            })
        except Exception as e:
            print(f"[Memory] add_event error: {e}")

    def get_events(self):
        try:
            docs = list(memory_col().find(
                {"type": "event"},
                {"_id": 0}
            ).sort("timestamp", -1).limit(200))
            return docs
        except Exception as e:
            print(f"[Memory] get_events error: {e}")
            return []

    def get_context(self):
        """Alias for get_events (backward compat)."""
        return list(reversed(self.get_events()[:50]))

    # ─── Strategies ──────────────────────────────────────────────────────────

    def add_strategy(self, goal: str, strategy: dict):
        try:
            memory_col().insert_one({
                "type": "strategy",
                "goal": goal,
                "strategy": strategy,
                "timestamp": datetime.utcnow().isoformat()
            })
        except Exception as e:
            print(f"[Memory] add_strategy error: {e}")

    def get_strategies(self):
        try:
            docs = list(memory_col().find(
                {"type": "strategy"},
                {"_id": 0}
            ).sort("timestamp", -1).limit(50))
            return docs
        except Exception as e:
            return []

    # ─── Prompts ─────────────────────────────────────────────────────────────

    def update_prompt(self, agent: str, optimized_prompt: str):
        if not optimized_prompt or len(optimized_prompt.strip()) < 15 or "{" in optimized_prompt:
            print(f"[Memory] Prompt for {agent} rejected by safeguard.")
            return
        try:
            memory_col().update_one(
                {"type": "prompt", "agent": agent},
                {"$set": {
                    "type": "prompt",
                    "agent": agent,
                    "prompt": optimized_prompt,
                    "updated_at": datetime.utcnow().isoformat()
                }},
                upsert=True
            )
        except Exception as e:
            print(f"[Memory] update_prompt error: {e}")

    def get_prompt(self, agent: str, default_prompt: str = "") -> str:
        try:
            doc = memory_col().find_one({"type": "prompt", "agent": agent})
            if doc:
                return doc.get("prompt", default_prompt)
        except Exception:
            pass
        return default_prompt

    def get_prompts(self) -> dict:
        try:
            docs = list(memory_col().find({"type": "prompt"}, {"_id": 0}))
            return {d["agent"]: d["prompt"] for d in docs if "agent" in d and "prompt" in d}
        except Exception:
            return {}

    # ─── Past Investigation Retrieval ────────────────────────────────────────

    def get_past_investigations(self, limit: int = 5) -> list:
        """
        Retrieves recent investigation records from MongoDB.
        Used by PlannerAgent to inform planning based on past outcomes.
        Returns list of dicts with: goal, detected_issue, severity, recommended_action, critic_score.
        """
        try:
            from core.database import agent_performance_col
            docs = list(
                agent_performance_col()
                .find({}, {"_id": 0})
                .sort("timestamp", -1)
                .limit(limit)
            )
            return docs
        except Exception as e:
            print(f"[Memory] get_past_investigations error: {e}")
            return []

    def get_successful_strategies(self, min_score: float = 0.7, limit: int = 5) -> list:
        """
        Retrieves past strategies that received a high critic score.
        Used by PlannerAgent to reuse proven approaches.
        """
        try:
            from core.database import agent_performance_col
            docs = list(
                agent_performance_col()
                .find({"critic_score": {"$gte": min_score}}, {"_id": 0})
                .sort("critic_score", -1)
                .limit(limit)
            )
            return docs
        except Exception as e:
            print(f"[Memory] get_successful_strategies error: {e}")
            return []

    # ─── Utilities ───────────────────────────────────────────────────────────

    def clear(self):
        try:
            memory_col().delete_many({"type": {"$in": ["event", "strategy"]}})
        except Exception as e:
            print(f"[Memory] clear error: {e}")
