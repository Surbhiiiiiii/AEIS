import asyncio
import uuid
import random
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

from core.parsers import FileParser, WebPageParserTool
from core.tools import TrendDetector, TicketFetcherTool

import time

async def run_enterprise_system(goal: str, max_iterations: int = 3, file_content: bytes = None, filename: str = None, url: str = None, broadcast_cb=None):
    start_time = time.time()
    memory = Memory()
    vector_store = VectorStore()
    
    session_id = str(uuid.uuid4())
    memory.add_event("System", "Received User Goal", {"session": session_id, "goal": goal, "filename": filename, "url": url})
    
    if broadcast_cb:
        await broadcast_cb({
            "agent": "System",
            "status": "idle",
            "message": "Initialization sequence triggered. Spawning AI agents.",
            "type": "info"
        })

    planner = PlannerAgent(memory)
    analyst = AnalystAgent(memory, vector_store)
    executor = ExecutorAgent(memory)
    critic = CriticAgent(memory)
    monitor = MonitoringAgent(memory)
    
    # Init Meta Agents
    perf_agent = PerformanceEvaluationAgent(memory)
    strat_agent = StrategyOptimizationAgent(memory)
    prompt_agent = PromptOptimizationAgent(memory)
    mem_mgr_agent = MemoryManagementAgent(memory, vector_store)

    context = {"url": url, "has_dataset": bool(file_content)}
    plan = planner.plan(goal, context)
    
    final_analysis = None
    final_action = None
    final_evaluation = None
    
    custom_data = []
    
    # 1. RAG Ingestion Phase
    if broadcast_cb:
        await broadcast_cb({"agent": "System", "status": "running", "message": "Ingesting data into FAISS Vector Store for RAG.", "type": "info"})
        
    if file_content and filename:
        custom_data = FileParser.parse(filename, file_content)
        # Ingest text strings into RAG
        if custom_data and "data" in custom_data:
            texts = [str(r) for r in custom_data["data"]][:100] # Limit for speed
            if texts:
                vector_store.ingest(texts, [{"source": filename} for _ in texts])
                
    elif url:
        custom_data = WebPageParserTool.parse(url)
        if custom_data:
            vector_store.ingest(custom_data, [{"source": url} for _ in custom_data])

    # autonomous loop
    system_active = True
    iteration = 0
    
    while system_active and iteration < max_iterations:
        memory.add_event("Orchestrator", f"Starting reasoning loop iteration {iteration + 1}", {})
        
        if broadcast_cb:
            await broadcast_cb({"agent": "planner", "status": "running", "message": f"Planner generating strategic task plan (Iter {iteration+1}).", "type": "info"})
            await asyncio.sleep(0.5)
            await broadcast_cb({"agent": "planner", "status": "completed", "message": "Plan generated.", "type": "success"})
            
        analysis = analyst.analyze(plan, custom_data=custom_data, url=url)
        
        if broadcast_cb:
            await broadcast_cb({"agent": "analyst", "status": "running", "message": "Analyst inspecting dataset and finding patterns.", "type": "warning"})
            await asyncio.sleep(0.5)
            await broadcast_cb({"agent": "analyst", "status": "completed", "message": f"Analysis complete: {analysis.get('analysis_text', '')[:50]}...", "type": "success"})

        if broadcast_cb:
            await broadcast_cb({"agent": "executor", "status": "running", "message": "Executor triggering automated responses.", "type": "error"})
            
        action = executor.execute(analysis)
        
        if broadcast_cb:
            await broadcast_cb({"agent": "executor", "status": "completed", "message": "Executor completed task.", "type": "success"})
            await broadcast_cb({"agent": "critic", "status": "running", "message": "Critic evaluating decision quality.", "type": "info"})
            
        evaluation = critic.evaluate(analysis, action)
        
        if broadcast_cb:
            await broadcast_cb({"agent": "critic", "status": "completed", "message": f"Critic rating: {evaluation.get('quality')}", "type": "success"})
        
        final_analysis = analysis
        final_action = action
        final_evaluation = evaluation

        if evaluation.get("quality", "") == "Good":
            system_active = False
        else:
            plan = planner.refine_plan(plan, evaluation)
            iteration += 1

    # Meta-Intelligence Sequence
    if broadcast_cb:
        await broadcast_cb({"agent": "System", "status": "running", "message": "Triggering Meta-Intelligence Layer optimizations.", "type": "warning"})
        
    session_metrics = perf_agent.evaluate_session(session_id, final_analysis, final_action, final_evaluation)
    strat_agent.optimize_strategy(goal, plan.get("tasks", []), final_evaluation)
    
    # Re-evaluate prompts if critic was unhappy
    critic_unhappy = final_evaluation.get("quality", "") != "Good"
    prompt_optimizations = prompt_agent.refine_prompts(
        planner_feedback=final_evaluation if critic_unhappy else None,
        analyst_feedback=final_evaluation if critic_unhappy else None
    )
    mem_mgr_agent.consolidate_memory()
    
    if broadcast_cb:
        await broadcast_cb({"agent": "System", "status": "completed", "message": "Meta-layer optimizations applied successfully.", "type": "success"})

    # Call monitoring agent to detect incidents specifically for UI
    is_incident_dataset = isinstance(custom_data, dict) and custom_data.get("type") == "incident_logs"
    
    if is_incident_dataset:
        messages = [str(r) for r in custom_data.get("data", [])]
    elif isinstance(custom_data, dict):
        messages = [str(r) for r in custom_data.get("data", [])]
    elif custom_data:
        messages = custom_data
    else:
        messages = [t["message"] for t in TicketFetcherTool.run()]
        
    detected_incidents = monitor.detect_incidents(messages)

    # Compile execution logs from memory (last 20)
    execution_logs = [f"{event['agent']}: {event['action']}" for event in memory.get_context()[-20:]]

    history_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'investigation_history.json')
    history_record = {
        "id": f"INC-{random.randint(1000, 9999)}",
        "goal": goal,
        "detected_issue": final_analysis.get('analysis_data', {}).get('major_issue', 'Unknown') if final_analysis else 'Unknown',
        "root_cause": final_analysis.get('analysis_data', {}).get('root_cause', 'Unknown') if final_analysis else 'Unknown',
        "severity": final_analysis.get('analysis_data', {}).get('severity', 'Unknown') if final_analysis else 'Unknown',
        "recommended_action": final_analysis.get('analysis_data', {}).get('recommended_action', 'Unknown') if final_analysis else 'Unknown',
        "timestamp": datetime.now().isoformat(),
        "duration": time.time() - start_time
    }
    
    history_data = []
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r') as f:
                history_data = json.load(f)
        except:
            pass
    history_data.insert(0, history_record)
    with open(history_file, 'w') as f:
        json.dump(history_data, f, indent=2)

    # Calculate Data Metrics Dynamically from History
    total_incidents = len(history_data)
    critical_alerts = sum(1 for h in history_data if str(h.get("severity", "")).upper() in ["HIGH", "CRITICAL"])
    
    categories = set()
    for h in history_data:
        words = str(h.get("root_cause", "System")).split()
        if words: categories.add(words[0])
    departments = len(categories) if categories else 1
    
    durations = [h.get("duration", 0) for h in history_data if isinstance(h.get("duration"), (int, float))]
    resolution_time = f"{round(sum(durations)/len(durations), 1)}s" if durations else "0s"

    date_counts = {}
    for h in reversed(history_data):
        day = h.get("timestamp", "")[:10]
        if day: date_counts[day] = date_counts.get(day, 0) + 1
        
    sorted_dates = sorted(date_counts.keys())[-5:]
    trend_chart = []
    for d in sorted_dates:
        try:
            day_name = datetime.strptime(d, "%Y-%m-%d").strftime("%a")
            trend_chart.append({"day": day_name, "complaints": date_counts[d]})
        except:
            trend_chart.append({"day": d, "complaints": date_counts[d]})
    if not trend_chart: trend_chart = [{"day": "Today", "complaints": 0}]

    dist_counts = {}
    for h in history_data:
        words = str(h.get("root_cause", "System")).split()
        cat = words[0] if words else "Unknown"
        dist_counts[cat] = dist_counts.get(cat, 0) + 1
    trend_distribution = [{"issue": k, "count": v} for k, v in list(dist_counts.items())[:5]]
    if not trend_distribution: trend_distribution = [{"issue": "None", "count": 0}]

    incidents = []
    for h in history_data[:10]:
        incidents.append({
            "id": h.get("id", f"INC-{random.randint(1000, 9999)}"), 
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
        "history": history_data[:10],
        "analysis_insights": final_analysis.get('analysis_data', {}) if final_analysis else {},
        "meta_insights": {
            "performance": session_metrics,
            "prompts_optimized": bool(prompt_optimizations),
            "strategy_updated": True
        },
        "plan": plan,
        "analysis": final_analysis,
        "action": final_action,
        "evaluation": final_evaluation
    }
