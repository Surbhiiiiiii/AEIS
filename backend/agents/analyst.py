import json
import re
from core.llm import query_llm
from core.tools import TicketFetcherTool, KeywordAnalyzerTool, TrendDetector, DatasetAnalyzer, WebContentFetcher


class AnalystAgent:

    def __init__(self, memory=None, vector_store=None):
        self.memory = memory
        self.vector_store = vector_store


    def _get_prompt(self) -> str:
        default = "You are an enterprise risk AnalystAgent. Analyze data based on current trends, keywords, and retrieved text context."
        if self.memory:
            return self.memory.get_prompt("AnalystAgent", default)
        return default


    def _extract_json(self, text: str):
        """
        Extract JSON object safely from LLM output.
        Handles extra text or markdown from the model.
        """
        try:
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except Exception as e:
            print("JSON extraction error:", e)

        # Better UI Fallback
        return {
            "major_issue": "Analysis process timed out or failed.",
            "root_cause": "System Constraints",
            "severity": "MEDIUM",
            "recommended_action": "Retry with a smaller dataset or optimized query."
        }


    def analyze(self, plan, custom_data=None, url=None):

        reports = []
        messages = []

        goal = plan.get("goal", "")
        tasks = plan.get("tasks", [])

        # Fetch web content
        if url:
            try:
                reports.append("URL Content: " + WebContentFetcher.run(url))
            except Exception as e:
                reports.append(f"Failed to fetch URL content: {e}")

        # Handle custom data
        if custom_data:

            if isinstance(custom_data, dict):
                if custom_data.get("type") == "incident_logs":
                    stats = custom_data.get("stats", {})

                    reports.append(
                        f"[Structured Incident Data Analyzed] "
                        f"Total: {stats.get('total_rows')}, "
                        f"Unique: {stats.get('unique_incidents')}. "
                        f"Avg Resolution Time: {stats.get('average_duration', 'N/A')}. "
                        f"States: {stats.get('states')}. "
                        f"Priorities: {stats.get('priorities')}"
                    )

                    messages = [str(r) for r in custom_data.get("data", [])]

                elif custom_data.get("type") == "generic_data":

                    reports.append(
                        f"[Generic Data Analyzed] "
                        f"Columns: {custom_data.get('stats', {}).get('columns')}. "
                        f"Total Rows: {custom_data.get('stats', {}).get('total_rows')}"
                    )

                    messages = [str(r) for r in custom_data.get("data", [])]

                else:
                    messages = custom_data.get("data", [])
                    reports.append(DatasetAnalyzer.run(messages))
            elif isinstance(custom_data, list):
                messages = custom_data
                reports.append(DatasetAnalyzer.run(messages))
            else:
                messages = [str(custom_data)]
                reports.append(DatasetAnalyzer.run(messages))

        else:
            tickets = TicketFetcherTool.run()[:20]
            messages = [t["message"] for t in tickets]

        # Keyword analysis
        keywords = KeywordAnalyzerTool.run(messages)
        keyword_summary = ", ".join([f"{k['keyword']} ({k['count']})" for k in keywords])

        # Trend detection
        trends = TrendDetector.run(messages)
        trend_summary = ", ".join(trends) if trends else "No major trends detected"

        # RAG retrieval
        rag_context = ""
        if self.vector_store and goal:
            try:
                search_results = self.vector_store.search(goal, k=3)
                if search_results:
                    rag_context = "\n".join([r.get("content", "") for r in search_results])
            except Exception as e:
                print(f"Vector search failed: {e}")

        prompt_payload = {
            "Goal": str(goal)[:500],
            "Tasks_From_Planner": tasks[:5],
            "Trends": str(trend_summary)[:1000],
            "Keywords": str(keyword_summary)[:1000],
            "Additional_Reports": " || ".join(reports)[:3000],
            "Retrieved_Context_from_Memory": str(rag_context)[:3000]
        }

        system_prompt = self._get_prompt()

        prompt = f"""
{system_prompt}

Data to Analyze:
{json.dumps(prompt_payload, indent=2)}

IMPORTANT:
Return ONLY valid JSON.
Do not add explanations.
Do not add markdown.
Do not add text before or after JSON.

Required JSON format:

{{
"major_issue": "your concise finding of the major operational issue",
"root_cause": "your primary root cause hypothesis",
"severity": "Low|Medium|High",
"recommended_action": "your actionable recommendation"
}}
"""

        try:
            llm_response = query_llm(prompt)
        except Exception as e:
            print("LLM error:", e)
            llm_response = ""

        parsed_analysis = self._extract_json(llm_response)

        required_keys = [
            "major_issue",
            "root_cause",
            "severity",
            "recommended_action"
        ]

        for key in required_keys:
            if key not in parsed_analysis:
                parsed_analysis[key] = "Not provided by model"

        if self.memory:
            self.memory.add_event(
                "AnalystAgent",
                "Analyzed enterprise data with RAG",
                {"trends": trends, "keywords": keywords}
            )

        return {
            "analysis_data": parsed_analysis,
            "analysis_text": llm_response
        }