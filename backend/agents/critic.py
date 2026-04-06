"""
CriticAgent — Score, Store Feedback, Influence Future Runs.

The CriticAgent evaluates decision quality and stores its score to MongoDB
(agent_performance collection) so the PlannerAgent can learn from it on the
NEXT investigation. It does NOT trigger re-execution or loop.
"""
import json
from datetime import datetime
from core.llm import query_llm


class CriticAgent:
    def __init__(self, memory=None):
        self.memory = memory

    def _get_prompt(self) -> str:
        default = "You are a CriticAgent. Evaluate the quality of the analysis and the resulting action."
        if self.memory:
            return self.memory.get_prompt("CriticAgent", default)
        return default

    def evaluate(self, analysis: dict, action: dict) -> dict:
        """
        Scores the agent pipeline output on a 0.0–1.0 scale.
        Stores feedback to MongoDB for future PlannerAgent memory retrieval.
        Does NOT trigger re-execution.
        """
        text = analysis.get("analysis_text", "")
        analysis_data = analysis.get("analysis_data", {})

        system_prompt = self._get_prompt()
        prompt = f"""{system_prompt}

Analysis Produced:
{text}

Action Taken:
{json.dumps(action)}

Evaluate if the analysis properly identified root causes and if the action is appropriate.
Output ONLY a valid JSON object with exactly these keys:
  "decision_score" (Float: 0.0 to 1.0),
  "quality" (String: "Good" or "Needs improvement"),
  "reasoning" (String: brief explanation),
  "feedback_for_planner" (String: one sentence of guidance for the next investigation).
Do not output markdown backticks or explaining text.
Example: {{"decision_score": 0.85, "quality": "Good", "reasoning": "Clear root cause identified.", "feedback_for_planner": "Continue focusing on latency root causes."}}
"""
        response = query_llm(prompt)

        try:
            result = json.loads(response)
            score = float(result.get("decision_score", 0.0))
            quality = result.get("quality", "Needs improvement")
            reasoning = result.get("reasoning", "")
            feedback = result.get("feedback_for_planner", "")
        except (json.JSONDecodeError, ValueError):
            # Fallback heuristic scoring
            text_lower = text.lower()
            score = 0.0
            if "root cause" in text_lower:
                score += 0.3
            if "severity" in text_lower:
                score += 0.3
            if "action" in text_lower or "recommend" in text_lower:
                score += 0.4
            score = round(score, 2)
            quality = "Good" if score >= 0.7 else "Needs improvement"
            reasoning = "Fallback heuristic evaluation"
            feedback = "Ensure analysis includes root cause, severity, and recommended action."

        final_result = {
            "decision_score": round(score, 2),
            "quality": quality,
            "reasoning": reasoning,
            "feedback_for_planner": feedback,
        }

        # ── Store feedback to memory event log ────────────────────────────────
        if self.memory:
            self.memory.add_event("CriticAgent", "Stored evaluation feedback", final_result)

        # ── Persist performance record to MongoDB agent_performance collection ─
        self._store_performance(analysis_data, action, final_result)

        return final_result

    def _store_performance(self, analysis_data: dict, action: dict, evaluation: dict):
        """
        Stores a performance record to agent_performance collection.
        This record is retrieved by PlannerAgent on subsequent investigations
        to enable memory-driven improvement — not to trigger a re-run.
        """
        try:
            from core.database import agent_performance_col
            record = {
                "timestamp": datetime.utcnow().isoformat(),
                "goal": analysis_data.get("goal", ""),
                "strategy_used": analysis_data.get("strategy", ""),
                "detected_issue": analysis_data.get("major_issue", ""),
                "severity": analysis_data.get("severity", ""),
                "recommended_action": analysis_data.get("recommended_action", ""),
                "critic_score": evaluation.get("decision_score", 0.0),
                "quality": evaluation.get("quality", ""),
                "feedback_for_planner": evaluation.get("feedback_for_planner", ""),
            }
            agent_performance_col().insert_one(record)
        except Exception as e:
            print(f"[CriticAgent] Failed to store performance record: {e}")