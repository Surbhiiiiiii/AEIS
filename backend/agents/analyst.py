import json
import re
from core.llm import query_llm
from core.tools import TicketFetcherTool, KeywordAnalyzerTool, WebContentFetcher
from core.parsers import build_analysis_summary


class AnalystAgent:

    def __init__(self, memory=None, vector_store=None):
        self.memory = memory
        self.vector_store = vector_store

    def _get_prompt(self) -> str:
        default = (
            "You are an enterprise risk AnalystAgent. "
            "Analyze the provided structured data summary and return ONLY valid JSON. "
            "Base your analysis on the actual data statistics given — do not fabricate numbers."
        )
        if self.memory:
            return self.memory.get_prompt("AnalystAgent", default)
        return default

    def _extract_json(self, text: str) -> dict:
        """Extract JSON object safely from LLM output."""
        try:
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                if isinstance(result, dict):
                    return result
        except Exception as e:
            print("JSON extraction error:", e)

        # Fallback — structured default
        return {
            "major_issue": "Analysis could not be parsed from model response.",
            "root_cause": "Model response format error",
            "severity": "Medium",
            "recommended_action": "Retry with a cleaner dataset or verify the LLM is running.",
            "insights": ["LLM response was not valid JSON — check Ollama/model status."]
        }

    def analyze(self, plan, custom_data=None, url=None):
        reports = []
        messages = []

        goal = plan.get("goal") or ""
        tasks = plan.get("tasks") or []
        if not isinstance(tasks, list):
            tasks = [str(tasks)]

        # ── 1. Guard: require real data ────────────────────────────────────
        if not custom_data and not url:
            return {
                "analysis_data": {
                    "major_issue": "No data provided for analysis.",
                    "root_cause": "Neither a dataset nor a URL was supplied.",
                    "severity": "Low",
                    "recommended_action": "Upload a CSV/Excel/JSON/PDF file or provide a URL to analyse.",
                    "insights": ["Please supply a dataset or URL to enable data-driven analysis."]
                },
                "analysis_text": ""
            }

        # ── 2. Build structured data summary (pre-LLM) ────────────────────
        if custom_data and isinstance(custom_data, dict):
            dtype = custom_data.get("type", "")

            if dtype == "incident_logs":
                # Use the pre-LLM summary builder (produces real stats)
                summary = build_analysis_summary(custom_data, goal)
                reports.append(summary)
                # Collect text representations of rows for keyword extraction
                messages = [
                    f"{r.get('id','')} {r.get('state','')} {r.get('priority','')} {r.get('category','')} {r.get('department','')}"
                    for r in custom_data.get("data", [])
                ]

            elif dtype == "generic_data":
                summary = build_analysis_summary(custom_data, goal)
                reports.append(summary)
                messages = [str(r) for r in custom_data.get("data", [])]

            elif dtype == "text":
                data_lines = custom_data.get("data", [])
                messages = data_lines
                reports.append(
                    f"[Text Document] {len(data_lines)} lines of text content.\n"
                    + "\n".join(data_lines[:20])
                )

            elif dtype == "error":
                reports.append(f"Data ingestion error: {custom_data.get('message', 'Unknown')}")

            else:
                raw_data = custom_data.get("data", [])
                messages = [str(r) for r in raw_data] if isinstance(raw_data, list) else [str(raw_data)]
                # Produce a real stats summary through the standard path
                summary = build_analysis_summary(custom_data, goal)
                reports.append(summary)

        elif isinstance(custom_data, list):
            messages = [str(m) for m in custom_data]
            summary = build_analysis_summary({"type": "text", "data": messages}, goal)
            reports.append(summary)

        elif custom_data:
            messages = [str(custom_data)]
            summary = build_analysis_summary({"type": "text", "data": messages}, goal)
            reports.append(summary)

        # ── 3. Web content ─────────────────────────────────────────────────
        if url:
            try:
                web_text = WebContentFetcher.run(url)
                reports.append("WEB CONTENT:\n" + web_text)
                # Use extracted web paragraphs as messages for keyword extraction
                if not messages:
                    messages = [web_text]
            except Exception as e:
                reports.append(f"Failed to fetch URL content: {e}")

        # ── 4. Top frequent terms (word-frequency, NOT fake AI analysis) ───
        keywords = KeywordAnalyzerTool.run(messages) if messages else []
        keyword_summary = ", ".join([f"{k['keyword']} ({k['count']})" for k in keywords])

        # ── 5. RAG retrieval ───────────────────────────────────────────────
        rag_context = ""
        if self.vector_store and goal:
            try:
                search_results = self.vector_store.search(goal, k=3)
                if search_results:
                    rag_context = "\n".join([r.get("content", "") for r in search_results])
            except Exception as e:
                print(f"Vector search failed: {e}")

        # ── 6. Retrieve past investigations from memory ────────────────────
        past_insights = ""
        if self.memory:
            try:
                past_events = self.memory.get_events()
                analyst_events = [
                    e for e in past_events
                    if e.get("agent") == "AnalystAgent"
                ][:3]
                if analyst_events:
                    past_insights = "PAST ANALYSIS CONTEXT:\n" + "\n".join(
                        f"- {e.get('action','')}: {json.dumps(e.get('details',{}))[:200]}"
                        for e in analyst_events
                    )
            except Exception:
                pass

        # ── 7. Build LLM prompt ────────────────────────────────────────────
        combined_report = " || ".join(reports)[:4000]

        system_prompt = self._get_prompt()
        prompt = f"""{system_prompt}

=== STRUCTURED DATA SUMMARY ===
{combined_report}

=== TOP FREQUENT TERMS (word frequency count) ===
{keyword_summary[:800] if keyword_summary else "No significant terms detected."}

=== RETRIEVED MEMORY CONTEXT ===
{rag_context[:1500]}

{past_insights}

=== TASK CONTEXT ===
Goal  : {str(goal)[:400]}
Tasks : {json.dumps(list(tasks)[:5])}

---
CRITICAL INSTRUCTION:
Return ONLY a valid JSON object. No markdown. No explanations. No text before or after JSON.

Required JSON format:
{{
  "major_issue": "concise description of the main operational problem found",
  "root_cause": "the primary underlying cause based on data",
  "severity": "Low|Medium|High",
  "recommended_action": "specific, actionable recommendation",
  "insights": [
    "data-driven insight 1",
    "data-driven insight 2",
    "data-driven insight 3"
  ]
}}
"""

        try:
            llm_response = query_llm(prompt)
        except Exception as e:
            print("LLM error:", e)
            llm_response = ""

        parsed_analysis = self._extract_json(llm_response)

        # Ensure all required keys are present
        required_keys = {
            "major_issue": "Analysis inconclusive",
            "root_cause": "Unknown",
            "severity": "Medium",
            "recommended_action": "Further investigation required",
            "insights": []
        }
        for key, default_val in required_keys.items():
            if key not in parsed_analysis:
                parsed_analysis[key] = default_val

        # Ensure insights is a list
        if not isinstance(parsed_analysis.get("insights"), list):
            parsed_analysis["insights"] = [str(parsed_analysis["insights"])]

        # Normalize severity casing
        sev = str(parsed_analysis.get("severity", "Medium")).strip()
        if sev.upper() in ("HIGH", "CRITICAL"):
            parsed_analysis["severity"] = "High"
        elif sev.upper() == "LOW":
            parsed_analysis["severity"] = "Low"
        else:
            parsed_analysis["severity"] = "Medium"

        if self.memory:
            self.memory.add_event(
                "AnalystAgent",
                "Analyzed enterprise data",
                {
                    "major_issue": parsed_analysis.get("major_issue"),
                    "severity": parsed_analysis.get("severity"),
                    "keywords": [k["keyword"] for k in keywords[:5]]
                }
            )

        return {
            "analysis_data": parsed_analysis,
            "analysis_text": llm_response
        }