import os
import json
from groq import Groq

# ---------------------------------------------------------------------------
# Groq client — reads GROQ_API_KEY from environment
# ---------------------------------------------------------------------------
_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"


def query_llm(prompt: str) -> str:
    """
    Send a prompt to Groq (llama3-8b-8192) and return the text response.
    Drop-in replacement for the old Ollama query_llm().
    """
    try:
        response = _client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an enterprise AI assistant that outputs ONLY valid JSON. "
                        "Never include markdown, backticks, or explanatory text outside the JSON. "
                        "Always follow the exact schema requested in the user prompt."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=2048,
        )
        return response.choices[0].message.content or ""

    except Exception as e:
        print("Groq LLM request failed:", e)

        # ── Robust fallbacks so the UI never crashes ──────────────────────
        if "major_issue" in prompt:
            return json.dumps({
                "major_issue": "LLM Unavailable",
                "root_cause": f"Groq API error: {e}",
                "severity": "HIGH",
                "recommended_action": "Check GROQ_API_KEY environment variable and Groq API status.",
                "insights": ["Groq API request failed — verify the API key is set correctly on Render."]
            })
        elif "decision_score" in prompt or "quality" in prompt:
            return json.dumps({
                "decision_score": 0.75,
                "quality": "Good",
                "reasoning": "Fallback evaluation — Groq unavailable.",
                "feedback_for_planner": "Ensure GROQ_API_KEY is configured."
            })
        elif "accuracy" in prompt or "usefulness" in prompt:
            return json.dumps({
                "accuracy": 0.8,
                "usefulness": 0.8,
                "timeliness": 0.9,
                "summary": "Fallback metrics — Groq unavailable."
            })
        elif "strategy_name" in prompt:
            return json.dumps({
                "strategy_name": "default",
                "description": "Fallback strategy.",
                "recommended_tasks": []
            })
        else:
            return json.dumps([
                "Analyze goal",
                "Extract insights",
                "Recommend action"
            ])