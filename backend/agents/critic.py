import json
from core.llm import query_llm

class CriticAgent:
    def __init__(self, memory=None):
        self.memory = memory

    def _get_prompt(self) -> str:
        default = "You are a CriticAgent. Evaluate the quality of the analysis and the resulting action."
        if self.memory:
            return self.memory.get_prompt("CriticAgent", default)
        return default

    def evaluate(self, analysis, action):
        text = analysis.get("analysis_text", "")
        
        system_prompt = self._get_prompt()
        prompt = f"""
{system_prompt}

Analysis Produced:
{text}

Action Taken:
{json.dumps(action)}

Evaluate if the analysis properly identified root causes and if the action is appropriate.
Output ONLY a valid JSON object with exactly these keys: "decision_score" (Float: 0.0 to 1.0), "quality" (String: "Good" or "Needs improvement", "reasoning" (String: brief explanation).
Do not output markdown backticks or explaining text.
Example: {{"decision_score": 0.85, "quality": "Good", "reasoning": "Clear root cause identified."}}
"""
        response = query_llm(prompt)
        
        try:
            result = json.loads(response)
            score = float(result.get("decision_score", 0.0))
            quality = result.get("quality", "Needs improvement")
        except (json.JSONDecodeError, ValueError):
            # Fallback
            text_lower = text.lower()
            score = 0.0
            if "root cause" in text_lower: score += 0.3
            if "severity" in text_lower: score += 0.3
            if "action" in text_lower or "recommend" in text_lower: score += 0.4
            quality = "Good" if score >= 0.7 else "Needs improvement"
            result = {"decision_score": round(score, 2), "quality": quality, "reasoning": "Fallback evaluation"}

        final_result = {
            "decision_score": round(score, 2),
            "quality": quality,
            "reasoning": result.get("reasoning", "")
        }

        if self.memory:
            self.memory.add_event("CriticAgent", "Evaluated decision quality", final_result)

        return final_result