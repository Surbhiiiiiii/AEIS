"""
PlannerAgent — Memory-Driven Single-Pass Planner.

On each invocation:
  1. Retrieves past investigations (failures & successes) from MongoDB.
  2. Retrieves high-scoring strategies to reuse proven approaches.
  3. Uses this historical context to generate a smarter task plan via LLM.

Does NOT re-plan within a single request.
"""
import json
from core.llm import query_llm


class PlannerAgent:
    def __init__(self, memory=None):
        self.memory = memory

    def _get_prompt(self) -> str:
        default = "You are a PlannerAgent. Decompose the goal into an execution plan (list of 3-5 short task strings)."
        if self.memory:
            return self.memory.get_prompt("PlannerAgent", default)
        return default

    def _build_memory_context(self) -> str:
        """
        Retrieves past investigations and successful strategies from MongoDB
        and formats them as a context block for the LLM prompt.
        """
        if not self.memory:
            return ""

        lines = []

        # ── Past investigations (failures + successes) ──────────────────────
        past = self.memory.get_past_investigations(limit=5)
        if past:
            lines.append("=== Past Investigations (most recent first) ===")
            for p in past:
                critic_score = p.get("critic_score", "N/A")
                quality = "✓ SUCCESS" if float(critic_score or 0) >= 0.7 else "✗ NEEDS IMPROVEMENT"
                lines.append(
                    f"- Goal: {p.get('goal', 'N/A')} | "
                    f"Issue: {p.get('detected_issue', 'N/A')} | "
                    f"Severity: {p.get('severity', 'N/A')} | "
                    f"Action: {p.get('recommended_action', 'N/A')} | "
                    f"Score: {critic_score} ({quality})"
                )

        # ── Successful strategies to reuse ──────────────────────────────────
        strategies = self.memory.get_successful_strategies(min_score=0.7, limit=3)
        if strategies:
            lines.append("\n=== High-Scoring Strategies (reuse these when applicable) ===")
            for s in strategies:
                lines.append(
                    f"- Strategy for '{s.get('goal', 'N/A')}': "
                    f"{s.get('strategy_used', 'N/A')} | Score: {s.get('critic_score', 'N/A')}"
                )

        return "\n".join(lines) if lines else ""

    def plan(self, goal: str, context: dict = None) -> dict:
        """
        Generates a structured task plan using LLM, informed by historical memory.
        Single-pass — called exactly once per investigation.
        """
        system_prompt = self._get_prompt()
        context_str = json.dumps(context) if context else "None"
        memory_context = self._build_memory_context()

        memory_section = (
            f"\n\n{memory_context}\n\nUse the above history to avoid repeating past failures "
            "and to reuse successful strategies where appropriate."
            if memory_context
            else ""
        )

        prompt = f"""{system_prompt}

Goal: {goal}
Context: {context_str}{memory_section}

Output ONLY a JSON array of strings representing the sequential tasks. Do not output markdown backticks or explaining text.
Example: ["Task 1", "Task 2", "Task 3"]
"""
        response = query_llm(prompt)

        try:
            tasks = json.loads(response)
            if not isinstance(tasks, list):
                tasks = ["Analyze goal", "Extract insights", "Recommend action"]
        except json.JSONDecodeError:
            tasks = [
                line.strip().strip("-* ")
                for line in response.split("\n")
                if line.strip() and len(line) > 3
            ][:5]
            if not tasks:
                tasks = ["Analyze goal", "Extract insights", "Recommend action"]

        if self.memory:
            self.memory.add_event(
                "PlannerAgent",
                "Generated memory-informed plan",
                {"goal": goal, "tasks": tasks, "memory_used": bool(memory_context)},
            )

        return {"goal": goal, "tasks": tasks}