from fastapi import FastAPI, HTTPException, status, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Request
from core.orchestrator import run_enterprise_system
from core.memory import Memory
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import os
import json
from datetime import datetime
import random
import time
from collections import deque

request_times = deque()

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Enterprise Intelligent System Running"}

@app.get("/favicon.ico")
async def favicon():
    return FileResponse("favicon.ico")
    
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def track_requests(request: Request, call_next):
    request_times.append(time.time())
    # Clean up old requests (older than 60s)
    current_time = time.time()
    while request_times and request_times[0] < current_time - 60:
        request_times.popleft()
    return await call_next(request)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("action") == "ping":
                    current_time = time.time()
                    while request_times and request_times[0] < current_time - 60:
                        request_times.popleft()
                    throughput = len(request_times) / 60.0
                    
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": msg.get("timestamp"),
                        "throughput": throughput
                    })
            except:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/run")
async def run_system(request: Request):
    form = await request.form()
    goal = form.get("goal", "")
    role = form.get("role", "viewer")
    url = form.get("url", "")
    file = form.get("file", None)

    if role not in ["analyst", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission Denied: Viewers cannot run AI."
        )
    
    file_content = None
    filename = None
    if file and hasattr(file, "filename") and getattr(file, "filename", ""):
        file_content = await file.read()
        filename = file.filename

    try:
        result = await run_enterprise_system(goal, file_content=file_content, filename=filename, url=url, broadcast_cb=manager.broadcast)
        return result
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())

@app.get("/admin/memory/events")
async def get_admin_events(role: str = "viewer"):
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    memory = Memory()
    return memory.get_events()

@app.get("/admin/memory/strategies")
async def get_admin_strategies(role: str = "viewer"):
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    memory = Memory()
    return memory.get_strategies()

@app.get("/admin/memory/prompts")
async def get_admin_prompts(role: str = "viewer"):
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    memory = Memory()
    return memory.get_prompts()

@app.get("/api/dashboard")
async def get_dashboard(role: str = "viewer"):
    history_file = os.path.join(os.path.dirname(__file__), 'data', 'investigation_history.json')
    history_data = []
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r') as f:
                history_data = json.load(f)
        except:
            pass

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
        "history": history_data[:10]
    }

@app.get("/api/history")
async def get_history(role: str = "viewer"):
    history_file = os.path.join(os.path.dirname(__file__), 'data', 'investigation_history.json')
    history_data = []
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r') as f:
                history_data = json.load(f)
        except:
            pass
    return history_data
