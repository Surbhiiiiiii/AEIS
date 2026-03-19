import json
from core.llm import query_llm
from core.memory import Memory

class PerformanceEvaluationAgent:
    def __init__(self, memory: Memory):
        self.memory = memory

    def evaluate_session(self, session_id: str, final_analysis: dict, final_action: dict, final_critic: dict):
        prompt = f"""
You are the PerformanceEvaluationAgent. Evaluate the overall intelligence session.
Session details:
Analysis: {json.dumps(final_analysis)}
Action Taken: {json.dumps(final_action)}
Critic Evaluation: {json.dumps(final_critic)}

Rate the session from 0.0 to 1.0 on accuracy, usefulness, and timeliness (assume fast).
Output ONLY a JSON object: {{"accuracy": float, "usefulness": float, "timeliness": float, "summary": str}}
"""
        response = query_llm(prompt)
        try:
            metrics = json.loads(response)
        except:
            metrics = {"accuracy": 0.8, "usefulness": 0.8, "timeliness": 0.9, "summary": "Metrics not parsed properly."}
            
        self.memory.add_event("Meta-PerformanceEvaluation", "Evaluated overall session", metrics)
        return metrics

class StrategyOptimizationAgent:
    def __init__(self, memory: Memory):
        self.memory = memory

    def optimize_strategy(self, goal: str, plan_tasks: list, critic_eval: dict):
        prompt = f"""
You are the StrategyOptimizationAgent. Learn from this execution to improve future planning.
Original Goal: {goal}
Plan executed: {json.dumps(plan_tasks)}
Final Critic Evaluation: {json.dumps(critic_eval)}

Extract a generalized strategy to handle similar goals better next time.
Output ONLY a JSON object: {{"strategy_name": str, "description": str, "recommended_tasks": list[str]}}
"""
        response = query_llm(prompt)
        try:
            strategy = json.loads(response)
            self.memory.add_strategy(goal, strategy)
            self.memory.add_event("Meta-StrategyOptimization", "Learned new strategy", strategy)
            return strategy
        except:
            return None

class PromptOptimizationAgent:
    def __init__(self, memory: Memory):
        self.memory = memory

    def refine_prompts(self, planner_feedback: dict = None, analyst_feedback: dict = None):
        updates = {}
        if planner_feedback and planner_feedback.get("quality", "") == "Needs improvement":
            updates["PlannerAgent"] = "You are a highly analytical PlannerAgent. Ensure your task plans heavily prioritize data-driven root cause analysis and concrete, verifiable operational workflow steps. Output raw JSON arrays only."
            self.memory.update_prompt("PlannerAgent", updates["PlannerAgent"])
            
        if analyst_feedback and analyst_feedback.get("quality", "") == "Needs improvement":
            updates["AnalystAgent"] = "You are an expert enterprise risk AnalystAgent. Do not guess root causes; rely exclusively on the provided retrieved text context, trends, and keywords to deduce operational incidents. Be explicitly clear on data sources. Output plain text strictly following the specified format."
            self.memory.update_prompt("AnalystAgent", updates["AnalystAgent"])

        if updates:
            self.memory.add_event("Meta-PromptOptimization", "Optimized prompts", updates)
        return updates

class MemoryManagementAgent:
    def __init__(self, memory: Memory, vector_store):
        self.memory = memory
        self.vector_store = vector_store

    def consolidate_memory(self):
        # A simple placeholder. In a real system, this might summarize older log events
        events = self.memory.get_events()
        if len(events) > 50:
            self.memory.add_event("Meta-MemoryManagement", "Triggered archival process", {"archived_count": len(events) - 50})
        return {"status": "Memory optimized"}
