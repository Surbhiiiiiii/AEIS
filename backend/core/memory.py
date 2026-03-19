import json
import os

class Memory:
    def __init__(self, filename="data/memory.json"):
        self.filename = filename
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)
        if not os.path.exists(self.filename):
            with open(self.filename, "w", encoding="utf-8") as f:
                json.dump({"events": [], "strategies": [], "prompts": {}}, f)

    def get_events(self):
        """Retrieves only the event history."""
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                memory_data = json.load(f)
                return memory_data.get("events", [])
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def get_context(self):
        """Alias for get_events for backward compatibility."""
        return self.get_events()

    def add_strategy(self, goal: str, strategy: dict):
        """Stores a successful strategy."""
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                memory_data = json.load(f)
                if not isinstance(memory_data, dict): memory_data = {}
        except (FileNotFoundError, json.JSONDecodeError):
            memory_data = {}

        strategies = memory_data.get("strategies", [])
        if not isinstance(strategies, list): strategies = []
            
        strategies.append({
            "goal": goal,
            "strategy": strategy
        })
        memory_data["strategies"] = strategies

        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, indent=4)

    def get_strategies(self):
        """Retrieves stored strategies."""
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                memory_data = json.load(f)
                return memory_data.get("strategies", [])
        except (FileNotFoundError, json.JSONDecodeError):
            return []
            
    def update_prompt(self, agent: str, optimized_prompt: str):
        """Stores an optimized prompt for an agent."""
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                memory_data = json.load(f)
                if not isinstance(memory_data, dict): memory_data = {}
        except (FileNotFoundError, json.JSONDecodeError):
            memory_data = {}

        prompts = memory_data.get("prompts", {})
        if not isinstance(prompts, dict): prompts = {}
            
        prompts[agent] = optimized_prompt
        memory_data["prompts"] = prompts

        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, indent=4)

    def get_prompt(self, agent: str, default_prompt: str = ""):
        """Retrieves the optimized prompt for an agent, or returns the default."""
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                memory_data = json.load(f)
                return memory_data.get("prompts", {}).get(agent, default_prompt)
        except (FileNotFoundError, json.JSONDecodeError):
            return default_prompt

    def get_prompts(self):
        """Retrieves all optimized prompts."""
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                memory_data = json.load(f)
                return memory_data.get("prompts", {})
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def add_event(self, agent: str, action: str, details: dict):
        """Stores a new event in memory."""
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                memory_data = json.load(f)
                if not isinstance(memory_data, dict): memory_data = {}
        except (FileNotFoundError, json.JSONDecodeError):
            memory_data = {}
            
        events = memory_data.get("events", [])
        if not isinstance(events, list): events = []
        
        events.append({
            "agent": agent,
            "action": action,
            "details": details
        })
        memory_data["events"] = events

        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, indent=4)

    def clear(self):
        """Clears the memory."""
        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump({"events": [], "strategies": [], "prompts": {}}, f)
