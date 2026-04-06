"""
Orchestrator — Enterprise AI Platform
Single-pass pipeline: Planner → Analyst → Executor → Critic

Key design principles (post-refactor):
  - ONE pass per request. No while loops, no re-runs.
  - CriticAgent scores output and stores to MongoDB; does NOT trigger re-execution.
  - PlannerAgent reads historical performance from MongoDB before planning.
  - Full performance record (goal/strategy/issue/severity/action/critic_score/timestamp)
    is stored to agent_performance collection after every run.
"""
import asyncio
import uuid
import os
import json
from datetime import datetime

from core.memory import Memory
from core.vector_store import VectorStore
from agents.planner import PlannerAgent
from agents.analyst import AnalystAgent
from agents.executor import ExecutorAgent
from agents.critic import CriticAgent
from agents.monitoring import MonitoringAgent
from agents.meta_agents import PerformanceEvaluationAgent, StrategyOptimizationAgent, PromptOptimizationAgent, MemoryManagementAgent

from core.parsers import FileParser, WebPageParserTool, build_analysis_summary
from core.tools import TicketFetcherTool
from core.database import investigations_col, alerts_col

import time


# ─── Input Validation ────────────────────────────────────────────────────────

def validate_inputs(goal: str, file_content: bytes, filename: str, url: str) -> tuple[bool, str]:
    """Returns (is_valid, error_message)."""
    if not goal or not goal.strip():
        return False, "Goal cannot be empty. Please describe what you want to analyze."

    if file_content is not None and len(file_content) == 0:
        return False, "Uploaded file is empty. Please upload a valid dataset."

    if filename and '.' not in filename:
        return False, f"File '{filename}' has no extension. Supported: CSV, JSON, Excel, PDF, TXT."

    if url:
        url = url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return False, "URL must start with http:// or https://"

    return True, ""


async def validate_url_reachable(url: str) -> tuple[bool, str]:
    """Quick HEAD request to check if URL is reachable."""
    try:
        import urllib.request
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8):
            return True, ""
    except Exception as e:
        return False, f"URL is not reachable: {str(e)[:100]}"


# ─── Main Orchestration ───────────────────────────────────────────────────────

async def run_enterprise_system(
    goal: str,
    max_iterations: int = 1,   # Kept for API compatibility — always executes once
    file_content: bytes = None,
    filename: str = None,
    url: str = None,
    broadcast_cb=None,
    user_email: str = None,
    user_role: str = "viewer"
):
    start_time = time.time()

    # ── Validate inputs ───────────────────────────────────────────────────
    valid, err_msg = validate_inputs(goal, file_content, filename, url)
    if not valid:
        return {"error": err_msg, "metrics": {}, "trends": {}, "incidents": [], "logs": []}

    if url:
        url_ok, url_err = await validate_url_reachable(url)
        if not url_ok:
            return {"error": url_err, "metrics": {}, "trends": {}, "incidents": [], "logs": []}

    memory = Memory()
    vector_store = VectorStore()

    session_id = str(uuid.uuid4())
    memory.add_event("System", "Received User Goal", {
        "session": session_id, "goal": goal,
        "filename": filename, "url": url, "user_email": user_email
    })

    if broadcast_cb:
        await broadcast_cb({
            "agent": "System", "status": "idle",
            "message": "Initialization sequence triggered. Spawning AI agents.",
            "type": "info"
        })

    # ── Instantiate Agents ────────────────────────────────────────────────
    planner = PlannerAgent(memory)       # Memory-aware: reads past investigations
    analyst = AnalystAgent(memory, vector_store)
    executor = ExecutorAgent(memory)
    critic = CriticAgent(memory)         # Stores scores; does NOT re-run
    monitor = MonitoringAgent(memory)

    perf_agent = PerformanceEvaluationAgent(memory)
    strat_agent = StrategyOptimizationAgent(memory)
    prompt_agent = PromptOptimizationAgent(memory)
    mem_mgr_agent = MemoryManagementAgent(memory, vector_store)

    # ── Data Ingestion ────────────────────────────────────────────────────
    if broadcast_cb:
        await broadcast_cb({"agent": "System", "status": "running",
                            "message": "Ingesting and parsing data...", "type": "info"})

    custom_data = {}
    if file_content and filename:
        custom_data = FileParser.parse(filename, file_content)
        if custom_data.get("type") == "error":
            return {
                "error": custom_data.get("message", "File parsing failed"),
                "metrics": {}, "trends": {}, "incidents": [], "logs": []
            }
        if custom_data.get("data"):
            texts = [str(r) for r in custom_data["data"]][:100]
            if texts:
                vector_store.ingest(texts, [{"source": filename} for _ in texts])

    elif url:
        url_texts = WebPageParserTool.parse(url)
        custom_data = {"type": "text", "data": url_texts, "stats": {"total_rows": len(url_texts), "columns": []}}
        if url_texts:
            vector_store.ingest(url_texts, [{"source": url} for _ in url_texts])

    # ─────────────────────────────────────────────────────────────────────
    # SINGLE-PASS PIPELINE: Planner → Analyst → Executor → Critic
    # Exactly ONE execution per request. No loops. No re-runs.
    # ─────────────────────────────────────────────────────────────────────

    # ── Step 1: Planner (memory-informed) ─────────────────────────────────
    if broadcast_cb:
        await broadcast_cb({"agent": "planner", "status": "running",
                             "message": "Planner building memory-informed task plan.", "type": "info"})

    context = {"url": url, "has_dataset": bool(file_content)}
    plan = await asyncio.to_thread(planner.plan, goal, context)

    if broadcast_cb:
        await broadcast_cb({"agent": "planner", "status": "completed",
                             "message": "Plan generated.", "type": "success"})

    # ── Step 2: Analyst ───────────────────────────────────────────────────
    if broadcast_cb:
        await broadcast_cb({"agent": "analyst", "status": "running",
                             "message": "Analyst inspecting dataset and finding patterns.", "type": "warning"})

    analysis = await asyncio.to_thread(
        analyst.analyze, plan, custom_data=custom_data, url=url if not file_content else None
    )

    if broadcast_cb:
        a_data = analysis.get("analysis_data", {})
        issue_preview = str(a_data.get("major_issue", ""))[:60]
        await broadcast_cb({"agent": "analyst", "status": "completed",
                             "message": f"Analysis: {issue_preview}...", "type": "success"})

    # ── Step 3: Executor ──────────────────────────────────────────────────
    if broadcast_cb:
        await broadcast_cb({"agent": "executor", "status": "running",
                             "message": "Executor triggering automated responses.", "type": "error"})

    action = await asyncio.to_thread(executor.execute, analysis)

    if broadcast_cb:
        await broadcast_cb({"agent": "executor", "status": "completed",
                             "message": f"Executor: {action.get('action','Done')}", "type": "success"})

    # ── Step 4: Critic (score + store, no re-run) ─────────────────────────
    # Enrich analysis_data with goal + strategy so _store_performance() can
    # persist them to MongoDB agent_performance collection correctly.
    enriched_analysis = dict(analysis)
    enriched_analysis_data = dict(analysis.get("analysis_data", {}))
    enriched_analysis_data.setdefault("goal", goal)
    enriched_analysis_data.setdefault("strategy", str(plan.get("tasks", []))[:300])
    enriched_analysis["analysis_data"] = enriched_analysis_data

    if broadcast_cb:
        await broadcast_cb({"agent": "critic", "status": "running",
                             "message": "Critic scoring decision quality and storing feedback.", "type": "info"})

    evaluation = await asyncio.to_thread(critic.evaluate, enriched_analysis, action)

    if broadcast_cb:
        await broadcast_cb({"agent": "critic", "status": "completed",
                             "message": (
                                 f"Critic score: {evaluation.get('decision_score')} "
                                 f"({evaluation.get('quality')}). Feedback stored for future runs."
                             ),
                             "type": "success"})

    # ── Trigger Alert if HIGH/CRITICAL severity ───────────────────────────
    severity = str(action.get("severity", "LOW")).upper()
    if severity in ("HIGH", "CRITICAL"):
        try:
            from core.alert_service import trigger_alert
            alert_doc = trigger_alert(analysis, action, evaluation, user_email)
            if alert_doc and broadcast_cb:
                await broadcast_cb({
                    "agent": "System",
                    "status": "alert",
                    "message": f"🚨 ALERT: {alert_doc.get('issue','Critical issue detected')} — severity {severity}",
                    "type": "error",
                    "alert": True
                })
        except Exception as e:
            print(f"[Alert] Error triggering alert: {e}")

    # ── Meta-Intelligence layer ───────────────────────────────────────────
    if broadcast_cb:
        await broadcast_cb({"agent": "System", "status": "running",
                            "message": "Triggering Meta-Intelligence Layer optimizations.", "type": "warning"})

    session_metrics = await asyncio.to_thread(
        perf_agent.evaluate_session, session_id, analysis, action, evaluation
    )
    await asyncio.to_thread(
        strat_agent.optimize_strategy, goal, plan.get("tasks", []), evaluation
    )

    critic_unhappy = evaluation.get("quality", "") != "Good"
    prompt_optimizations = await asyncio.to_thread(
        prompt_agent.refine_prompts,
        planner_feedback=evaluation if critic_unhappy else None,
        analyst_feedback=evaluation if critic_unhappy else None
    )
    mem_mgr_agent.consolidate_memory()

    if broadcast_cb:
        await broadcast_cb({"agent": "System", "status": "completed",
                            "message": "Meta-layer optimizations applied. Feedback stored for next run.", "type": "success"})

    # ── Detect incidents ──────────────────────────────────────────────────
    if isinstance(custom_data, dict):
        messages = [str(r) for r in custom_data.get("data", [])]
    elif custom_data:
        messages = custom_data
    else:
        messages = []

    detected_incidents = await asyncio.to_thread(monitor.detect_incidents, messages)

    # ── Persist investigation to MongoDB ──────────────────────────────────
    execution_logs = [f"{event['agent']}: {event['action']}" for event in memory.get_context()[-20:]]
    analysis_data = analysis.get("analysis_data", {})

    history_record = {
        "id": f"INV-{session_id[:8]}",
        "goal": goal,
        "detected_issue": analysis_data.get("major_issue", "Unknown"),
        "root_cause": analysis_data.get("root_cause", "Unknown"),
        "severity": analysis_data.get("severity", "Unknown"),
        "recommended_action": analysis_data.get("recommended_action", ""),
        "insights": analysis_data.get("insights", []),
        "strategy_used": str(plan.get("tasks", []))[:200],
        "critic_score": evaluation.get("decision_score", 0.0),
        "critic_quality": evaluation.get("quality", ""),
        "critic_feedback": evaluation.get("feedback_for_planner", ""),
        "timestamp": datetime.utcnow().isoformat(),
        "duration": time.time() - start_time,
        "user_email": user_email or "system"
    }

    # Save to MongoDB investigations collection
    try:
        investigations_col().insert_one({**history_record})
    except Exception as e:
        print(f"[DB] Failed to save investigation: {e}")

    # Fallback JSON persistence
    history_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'investigation_history.json')
    history_data = []
    os.makedirs(os.path.dirname(history_file), exist_ok=True)
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r') as f:
                history_data = json.load(f)
        except Exception:
            pass
    history_data.insert(0, history_record)
    with open(history_file, 'w') as f:
        json.dump(history_data, f, indent=2)

    # ── Build Dashboard Metrics from MongoDB ──────────────────────────────
    try:
        all_investigations = list(investigations_col().find({}, {"_id": 0}).sort("timestamp", -1).limit(200))
    except Exception:
        all_investigations = history_data

    total_incidents = len(all_investigations)
    critical_alerts = sum(1 for h in all_investigations if str(h.get("severity", "")).upper() in ["HIGH", "CRITICAL"])

    categories = set()
    for h in all_investigations:
        words = str(h.get("root_cause", "System")).split()
        if words:
            categories.add(words[0])
    departments = len(categories) if categories else 1

    durations = [h.get("duration", 0) for h in all_investigations if isinstance(h.get("duration"), (int, float))]
    resolution_time = f"{round(sum(durations)/len(durations), 1)}s" if durations else "0s"

    date_counts = {}
    for h in reversed(all_investigations):
        day = h.get("timestamp", "")[:10]
        if day:
            date_counts[day] = date_counts.get(day, 0) + 1

    sorted_dates = sorted(date_counts.keys())[-7:]
    trend_chart = []
    for d in sorted_dates:
        try:
            day_name = datetime.strptime(d, "%Y-%m-%d").strftime("%a")
            trend_chart.append({"day": day_name, "complaints": date_counts[d]})
        except Exception:
            trend_chart.append({"day": d, "complaints": date_counts[d]})
    if not trend_chart:
        trend_chart = [{"day": "Today", "complaints": 0}]

    _stop_words = {"the", "a", "an", "is", "are", "was", "of", "in", "at", "by", "to",
                   "and", "or", "not", "no", "for", "on", "with", "this", "that",
                   "ollama", "offline", "timeout", "system", "backend"}
    dist_counts = {}
    for h in all_investigations:
        raw = str(h.get("root_cause", "System"))
        words = [w.strip(".,;:") for w in raw.split()]
        cat = next((w.capitalize() for w in words if w.lower() not in _stop_words and len(w) > 2), "Other")
        dist_counts[cat] = dist_counts.get(cat, 0) + 1
    trend_distribution = [{"issue": k, "count": v} for k, v in sorted(dist_counts.items(), key=lambda x: -x[1])[:5]]
    if not trend_distribution:
        trend_distribution = [{"issue": "None", "count": 0}]

    incidents = []
    for h in all_investigations[:10]:
        incidents.append({
            "id": h.get("id", "INV-unknown"),
            "issue": str(h.get("detected_issue", "Unknown"))[:40],
            "priority": str(h.get("severity", "MEDIUM")).upper(),
            "state": "Analyzed",
            "duration": f"{round(h.get('duration', 0), 1)}s"
        })

    return {
        "metrics": {
            "total_incidents": total_incidents,
            "critical_alerts": critical_alerts,
            "departments": departments,
            "resolution_time": resolution_time
        },
        "trends": {
            "chart": trend_chart,
            "distribution": trend_distribution
        },
        "incidents": incidents,
        "logs": execution_logs,
        "history": all_investigations[:10],
        "analysis_insights": analysis_data,
        "meta_insights": {
            "performance": session_metrics,
            "prompts_optimized": bool(prompt_optimizations),
            "strategy_updated": True
        },
        "plan": plan,
        "analysis": analysis,
        "action": action,
        "evaluation": evaluation
    }
