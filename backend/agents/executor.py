import json
from datetime import datetime
from core.llm import query_llm

class ExecutorAgent:
    def __init__(self, memory=None):
        self.memory = memory

    def _get_prompt(self) -> str:
        default = "You are an ExecutorAgent. Map insights into specific operational actions."
        if self.memory:
            return self.memory.get_prompt("ExecutorAgent", default)
        return default

    def execute(self, analysis):
        text = analysis.get("analysis_text", "")
        
        system_prompt = self._get_prompt()
        prompt = f"""
{system_prompt}

Analysis Report:
{text}

Based on this analysis, determine the severity and the best operational action to take.
Output ONLY a valid JSON object with exactly these keys: "severity" (String: LOW, MEDIUM, or HIGH), "action" (String: short description of what to do).
Do not output markdown backticks or explaining text.
Example: {{"severity": "HIGH", "action": "Escalation created"}}
"""
        response = query_llm(prompt)
        
        try:
            result = json.loads(response)
            severity = result.get("severity", "LOW")
            action = result.get("action", "Logged for review")
        except json.JSONDecodeError:
            # Fallback
            text_lower = text.lower()
            if "high" in text_lower or "critical" in text_lower:
                severity = "HIGH"
                action = "Escalation created"
            elif "medium" in text_lower:
                severity = "MEDIUM"
                action = "Monitoring alert created"
            else:
                severity = "LOW"
                action = "Logged for review"

        final_result = {
            "action": action,
            "severity": severity,
            "timestamp": str(datetime.now())
        }

        if self.memory:
            self.memory.add_event("ExecutorAgent", "Executed action", final_result)

        return final_result