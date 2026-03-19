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

    def plan(self, goal: str, context: dict = None):
        """
        Converts enterprise goal into structured task plan using LLM.
        """
        system_prompt = self._get_prompt()
        context_str = json.dumps(context) if context else "None"
        
        prompt = f"""
{system_prompt}

Goal: {goal}
Context: {context_str}

Output ONLY a JSON array of strings representing the sequential tasks. Do not output markdown backticks or explaining text.
Example: ["Task 1", "Task 2"]
"""
        response = query_llm(prompt)
        
        try:
            tasks = json.loads(response)
            if not isinstance(tasks, list):
               tasks = ["Analyze goal", "Extract insights", "Recommend action"] 
        except json.JSONDecodeError:
            # Fallback if LLM fails to output clean JSON
            tasks = [line.strip().strip('-* ') for line in response.split('\n') if line.strip() and len(line) > 3][:5]
            if not tasks:
                tasks = ["Analyze goal", "Extract insights", "Recommend action"]

        if self.memory:
            self.memory.add_event("PlannerAgent", "Generated initial plan", {"goal": goal, "tasks": tasks})

        return {
            "goal": goal,
            "tasks": tasks
        }

    def refine_plan(self, previous_plan, evaluation):
        """Refines the plan based on critic's evaluation."""
        system_prompt = self._get_prompt()
        tasks = previous_plan.get("tasks", [])
        goal = previous_plan.get("goal", "")
        
        if "Needs improvement" in evaluation.get("quality", ""):
            prompt = f"""
{system_prompt}

Original Goal: {goal}
Previous Plan: {json.dumps(tasks)}
Critic Feedback: {json.dumps(evaluation)}

The previous plan failed. Output ONLY a new JSON array of strings for a refined task plan.
Do not output markdown backticks or explaining text.
"""
            response = query_llm(prompt)
            try:
                new_tasks = json.loads(response)
                if isinstance(new_tasks, list):
                    tasks = new_tasks
            except json.JSONDecodeError:
                new_tasks = [line.strip().strip('-* ') for line in response.split('\n') if line.strip() and len(line) > 3][:5]
                if new_tasks:
                   tasks = new_tasks
            
            if "Re-evaluate root cause deeply" not in tasks:
                tasks.append("Re-evaluate root cause deeply")

        if self.memory:
            self.memory.add_event("PlannerAgent", "Refined plan", {"evaluation": evaluation, "tasks": tasks})
            
        return {
            "goal": goal,
            "tasks": tasks
        }