from dotenv import load_dotenv
load_dotenv()  # Load .env before anything else so SMTP/JWT vars are available

from fastapi import FastAPI, HTTPException, status, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Request, Depends
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional
import os
import json
import asyncio
import time
from datetime import datetime
from collections import deque

from core.orchestrator import run_enterprise_system
from core.memory import Memory
from core.database import users_col, investigations_col, alerts_col, ensure_indexes
from core.auth import (
    hash_password, verify_password, create_jwt, get_current_user,
    generate_otp, verify_otp, send_otp_email
)
from bson import ObjectId

request_times = deque(maxlen=1000)  # Bounded to prevent unbounded memory growth

# ── WebSocket Manager ─────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

# ── App Setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Enterprise AI Platform", version="2.0.0")

# ── CORS ─────────────────────────────────────────────────────────────────────
# Set ALLOWED_ORIGINS in .env as a comma-separated list of allowed frontend URLs.
# Example: ALLOWED_ORIGINS=http://localhost:3000,https://my-app.vercel.app
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    try:
        ensure_indexes()
        print("[DB] MongoDB indexes ensured.")
    except Exception as e:
        print(f"[DB] MongoDB not available: {e}. System will continue with limited functionality.")

    asyncio.create_task(auto_ingest_watcher())
    asyncio.create_task(seed_admin_user())

async def seed_admin_user():
    """Create default admin account if no admin exists."""
    await asyncio.sleep(1)
    try:
        existing = users_col().find_one({"username": "admin"})
        if not existing:
            users_col().insert_one({
                "username": "admin",
                "email": "admin@enterprise.ai",
                "phone": "0000000000",
                "password": hash_password("Admin@123"),
                "role": "admin",
                "verified": True,
                "created_at": datetime.utcnow().isoformat()
            })
            print("[SEED] Default admin created: username=admin, password=Admin@123")
        else:
            print("[SEED] Admin account already exists.")
    except Exception as e:
        print(f"[SEED] Could not seed admin: {e}")

ALLOWED_INGEST_EXTENSIONS = {".csv", ".json", ".xlsx", ".xls", ".pdf", ".txt", ".log"}

async def auto_ingest_watcher():
    incoming_dir = os.path.join(os.path.dirname(__file__), 'data', 'incoming_logs')
    os.makedirs(incoming_dir, exist_ok=True)
    while True:
        try:
            for filename in os.listdir(incoming_dir):
                file_path = os.path.join(incoming_dir, filename)
                if not os.path.isfile(file_path):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext not in ALLOWED_INGEST_EXTENSIONS:
                    print(f"[AutoIngest] Skipping unsupported file type: {filename}")
                    continue
                with open(file_path, "rb") as f:
                    content = f.read()
                print(f"SYSTEM: Auto-ingesting: {filename}")
                try:
                    await run_enterprise_system(
                        goal=f"Analyze automated data stream from: {filename}",
                        file_content=content,
                        filename=filename,
                        broadcast_cb=manager.broadcast
                    )
                except Exception as e:
                    print(f"Auto-ingest error for {filename}: {e}")
                finally:
                    if os.path.exists(file_path):
                        os.remove(file_path)
        except Exception as e:
            print(f"[AutoIngest] Watcher error: {e}")
        await asyncio.sleep(20)

@app.middleware("http")
async def track_requests(request: Request, call_next):
    request_times.append(time.time())
    current_time = time.time()
    while request_times and request_times[0] < current_time - 60:
        request_times.popleft()
    return await call_next(request)

# ── Static ────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"message": "Enterprise AI Platform v2.0 Running", "status": "online"}

@app.get("/favicon.ico")
async def favicon():
    return FileResponse("favicon.ico")

# ── WebSocket ─────────────────────────────────────────────────────────────────
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
            except Exception:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── AUTH ROUTES ───────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: str
    phone: str
    password: str
    role: str = "viewer"

class VerifyOtpRequest(BaseModel):
    email: str
    otp: str

class LoginRequest(BaseModel):
    username: str
    password: str

class ResendOtpRequest(BaseModel):
    email: str


@app.post("/auth/register", status_code=201)
async def register(body: RegisterRequest):
    # Validation
    if len(body.username.strip()) < 3:
        raise HTTPException(400, "Username must be at least 3 characters.")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if body.role not in ("viewer", "analyst", "admin"):
        raise HTTPException(400, "Role must be viewer, analyst, or admin.")
    if not body.email or "@" not in body.email:
        raise HTTPException(400, "A valid email address is required.")
    if len(body.phone.strip()) < 7:
        raise HTTPException(400, "A valid phone number is required.")

    # Check uniqueness
    try:
        if users_col().find_one({"username": body.username}):
            raise HTTPException(409, "Username already exists.")
        if users_col().find_one({"email": body.email.lower()}):
            raise HTTPException(409, "Email already registered.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Database unavailable: {str(e)}")

    # Create user (unverified)
    user_doc = {
        "username": body.username.strip(),
        "email": body.email.lower().strip(),
        "phone": body.phone.strip(),
        "password": hash_password(body.password),
        "role": body.role,
        "verified": False,
        "created_at": datetime.utcnow().isoformat()
    }
    try:
        users_col().insert_one(user_doc)
    except Exception as e:
        raise HTTPException(500, f"Failed to create user: {str(e)}")

    # Generate & send OTP
    otp = generate_otp(body.email.lower().strip())
    send_otp_email(body.email.lower().strip(), otp, body.username)

    return {
        "message": "Registration successful. Check your email (or console) for the OTP.",
        "email": body.email.lower().strip()
    }


@app.post("/auth/verify-otp")
async def verify_otp_route(body: VerifyOtpRequest):
    email = body.email.lower().strip()
    if not verify_otp(email, body.otp.strip()):
        raise HTTPException(400, "Invalid or expired OTP. Please request a new one.")

    try:
        users_col().update_one(
            {"email": email},
            {"$set": {"verified": True, "verified_at": datetime.utcnow().isoformat()}}
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to activate account: {str(e)}")

    return {"message": "Account verified successfully. You can now login."}


@app.post("/auth/resend-otp")
async def resend_otp(body: ResendOtpRequest):
    email = body.email.lower().strip()
    user = users_col().find_one({"email": email})
    if not user:
        raise HTTPException(404, "Email not found.")
    if user.get("verified"):
        raise HTTPException(400, "Account is already verified.")

    otp = generate_otp(email)
    send_otp_email(email, otp, user.get("username", "User"))
    return {"message": "New OTP sent. Check your email or console."}


@app.post("/auth/login")
async def login(body: LoginRequest):
    try:
        user = users_col().find_one({"username": body.username.strip()})
    except Exception as e:
        raise HTTPException(503, f"Database unavailable: {str(e)}")

    if not user:
        raise HTTPException(401, "Invalid username or password.")
    if not verify_password(body.password, user.get("password", "")):
        raise HTTPException(401, "Invalid username or password.")
    if not user.get("verified", False):
        raise HTTPException(403, "Account not verified. Please check your email for the OTP.")

    token = create_jwt(user["username"], user["role"], user["email"])
    return {
        "token": token,
        "user": {
            "username": user["username"],
            "email": user["email"],
            "role": user["role"]
        }
    }


# ── PROTECTED ROUTES ──────────────────────────────────────────────────────────

@app.post("/run")
async def run_system(request: Request, current_user: dict = Depends(get_current_user)):
    role = current_user.get("role", "viewer")
    user_email = current_user.get("email", "")

    if role not in ["analyst", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission Denied: Only Analysts and Admins can run AI analysis."
        )

    form = await request.form()
    goal = form.get("goal", "").strip()
    url = form.get("url", "").strip()
    file = form.get("file", None)

    file_content = None
    filename = None
    if file and hasattr(file, "filename") and getattr(file, "filename", ""):
        file_content = await file.read()
        filename = file.filename

    try:
        result = await run_enterprise_system(
            goal, file_content=file_content, filename=filename,
            url=url, broadcast_cb=manager.broadcast,
            user_email=user_email, user_role=role
        )
        if result.get("error"):
            raise HTTPException(status_code=422, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())


@app.get("/api/dashboard")
async def get_dashboard(current_user: dict = Depends(get_current_user)):
    try:
        all_investigations = list(
            investigations_col().find({}, {"_id": 0}).sort("timestamp", -1).limit(200)
        )
    except Exception:
        # Fallback to JSON file
        history_file = os.path.join(os.path.dirname(__file__), 'data', 'investigation_history.json')
        all_investigations = []
        if os.path.exists(history_file):
            try:
                with open(history_file, 'r') as f:
                    all_investigations = json.load(f)
            except Exception:
                pass

    history_data = all_investigations
    total_incidents = len(history_data)
    critical_alerts = sum(1 for h in history_data if str(h.get("severity", "")).upper() in ["HIGH", "CRITICAL"])

    categories = set()
    for h in history_data:
        words = str(h.get("root_cause", "System")).split()
        if words:
            categories.add(words[0])
    departments = len(categories) if categories else 1

    durations = [h.get("duration", 0) for h in history_data if isinstance(h.get("duration"), (int, float))]
    resolution_time = f"{round(sum(durations)/len(durations), 1)}s" if durations else "0s"

    date_counts = {}
    for h in reversed(history_data):
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
    for h in history_data:
        raw = str(h.get("root_cause", "System"))
        words = [w.strip('.,;:') for w in raw.split()]
        cat = next((w.capitalize() for w in words if w.lower() not in _stop_words and len(w) > 2), "Other")
        dist_counts[cat] = dist_counts.get(cat, 0) + 1
    trend_distribution = [{"issue": k, "count": v} for k, v in sorted(dist_counts.items(), key=lambda x: -x[1])[:5]]
    if not trend_distribution:
        trend_distribution = [{"issue": "None", "count": 0}]

    incidents = []
    for h in history_data[:10]:
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
        "trends": {"chart": trend_chart, "distribution": trend_distribution},
        "incidents": incidents,
        "history": history_data[:10]
    }


@app.get("/api/alerts")
async def get_alerts(current_user: dict = Depends(get_current_user)):
    try:
        raw = list(alerts_col().find({}, {"_id": 0}).sort("timestamp", -1).limit(50))
        return raw
    except Exception as e:
        return []


@app.get("/api/history")
async def get_history(current_user: dict = Depends(get_current_user)):
    try:
        raw = list(investigations_col().find({}, {"_id": 0}).sort("timestamp", -1).limit(50))
        return raw
    except Exception:
        history_file = os.path.join(os.path.dirname(__file__), 'data', 'investigation_history.json')
        if os.path.exists(history_file):
            try:
                with open(history_file, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return []


# ── ADMIN ROUTES ──────────────────────────────────────────────────────────────

@app.get("/admin/memory/events")
async def get_admin_events(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    memory = Memory()
    return memory.get_events()

@app.get("/admin/memory/strategies")
async def get_admin_strategies(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    memory = Memory()
    return memory.get_strategies()

@app.get("/admin/memory/prompts")
async def get_admin_prompts(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    memory = Memory()
    return memory.get_prompts()


@app.get("/admin/agent-performance")
async def get_agent_performance(current_user: dict = Depends(get_current_user)):
    """Returns the last 50 critic performance records stored by CriticAgent."""
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    try:
        from core.database import agent_performance_col
        records = list(agent_performance_col().find({}, {"_id": 0}).sort("timestamp", -1).limit(50))
        return records
    except Exception as e:
        raise HTTPException(500, f"Could not fetch performance records: {e}")

@app.get("/admin/users")
async def get_admin_users(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    try:
        users = list(users_col().find({}, {"_id": 0, "password": 0}))
        return users
    except Exception as e:
        return []

class UpdateRoleRequest(BaseModel):
    role: str

@app.patch("/admin/users/{username}/role")
async def update_user_role(username: str, body: UpdateRoleRequest, current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    if body.role not in ("viewer", "analyst", "admin"):
        raise HTTPException(400, "Role must be viewer, analyst, or admin.")
    try:
        result = users_col().update_one({"username": username}, {"$set": {"role": body.role}})
        if result.matched_count == 0:
            raise HTTPException(404, "User not found")
        return {"message": f"Role updated to {body.role} for {username}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
