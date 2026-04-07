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
from datetime import datetime, timedelta
from collections import deque

from core.orchestrator import run_enterprise_system
from core.memory import Memory
from core.database import users_col, investigations_col, alerts_col, ensure_indexes, db_health_check, reset_client
from core.auth import (
    hash_password, verify_password, create_jwt, get_current_user,
    generate_otp, verify_otp, send_otp_email,
    _ensure_otp_index
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

# ── Pending Registration Store (MongoDB-backed) ───────────────────────────────
# User records live in MongoDB pending_registrations (TTL 15min) until OTP
# is verified. This survives Render restarts and multi-worker deployments.

def _pending_col():
    from core.database import get_db
    return get_db()["pending_registrations"]

def _ensure_pending_index():
    try:
        _pending_col().create_index("expires_at", expireAfterSeconds=0)
    except Exception:
        pass

def _save_pending(email: str, user_doc: dict):
    expires_at = datetime.utcnow() + timedelta(minutes=15)
    try:
        _pending_col().replace_one(
            {"email": email},
            {"email": email, "user_doc": user_doc, "expires_at": expires_at},
            upsert=True
        )
    except Exception as e:
        print(f"[PENDING] MongoDB write failed: {e}")

def _get_pending(email: str) -> dict | None:
    try:
        doc = _pending_col().find_one({"email": email})
        return doc
    except Exception:
        return None

def _delete_pending(email: str):
    try:
        _pending_col().delete_one({"email": email})
    except Exception:
        pass

# ── App Setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Enterprise AI Platform", version="2.0.0")

# ── CORS ─────────────────────────────────────────────────────────────────────
# Set ALLOWED_ORIGINS in .env as a comma-separated list of allowed frontend URLs.
# Example: ALLOWED_ORIGINS=http://localhost:3000,https://my-app.vercel.app
# Leave unset (or use *) to allow all origins (fine for demo/dev deployments).
_raw_origins = os.getenv("ALLOWED_ORIGINS", "").strip()

if not _raw_origins or _raw_origins == "*":
    # Open CORS — allow all origins. Must NOT use allow_credentials=True with wildcard.
    _allowed_origins = ["*"]
    _allow_credentials = False
else:
    _allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
    if "http://localhost:3000" not in _allowed_origins:
        _allowed_origins.append("http://localhost:3000")
    if "https://enterprise-dashboard-hhkn.vercel.app" not in _allowed_origins:
        _allowed_origins.append("https://enterprise-dashboard-hhkn.vercel.app")
    _allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    # Return immediately so uvicorn can respond to health checks / port probes.
    # All blocking work is deferred to a background task.
    asyncio.create_task(_deferred_startup())

async def _deferred_startup():
    """Run all startup work in the background so the event loop stays responsive."""
    await asyncio.sleep(1)

    try:
        await asyncio.to_thread(ensure_indexes)
        await asyncio.to_thread(_ensure_otp_index)      # TTL index on otp_store
        await asyncio.to_thread(_ensure_pending_index)  # TTL index on pending_registrations
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
    await asyncio.sleep(30)  # Wait for app to fully start before first scan
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

@app.get("/health")
async def health_check():
    """Quick liveness + DB reachability probe. Safe to call without auth."""
    db_status = await asyncio.to_thread(db_health_check)
    status_code = 200 if db_status["ok"] else 503
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "online",
            "database": db_status,
        }
    )

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
    # ── Validation ───────────────────────────────────────────────────────
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

    email = body.email.lower().strip()
    username = body.username.strip()

    # ── Uniqueness: check verified DB accounts AND pending store ─────────
    try:
        if users_col().find_one({"username": username, "verified": True}):
            raise HTTPException(409, "Username already taken.")
        if users_col().find_one({"email": email, "verified": True}):
            raise HTTPException(409, "Email already registered with a verified account.")
        # Clean up any old unverified DB records for this email
        users_col().delete_many({"email": email, "verified": False})
    except HTTPException:
        raise
    except Exception as e:
        reset_client()
        raise HTTPException(503, f"Database unavailable: {str(e)}")

    # Also check pending store (duplicate concurrent registrations)
    existing_pending = _get_pending(email)
    if existing_pending:
        # Allow re-registration: overwrite the pending record & resend OTP
        pass  # falls through to generate new OTP below

    # ── Store in MongoDB pending (NOT users_col) until OTP verified ───────
    user_doc = {
        "username": username,
        "email": email,
        "phone": body.phone.strip(),
        "password": hash_password(body.password),
        "role": body.role,
        "verified": False,
        "created_at": datetime.utcnow().isoformat(),
    }
    _save_pending(email, user_doc)

    # ── Generate & send OTP ───────────────────────────────────────────────
    otp = generate_otp(email)
    send_otp_email(email, otp, username)

    return {
        "message": "OTP sent to your email. Please verify within 10 minutes to complete registration.",
        "email": email,
    }


@app.post("/auth/verify-otp")
async def verify_otp_route(body: VerifyOtpRequest):
    email = body.email.lower().strip()
    otp_input = body.otp.strip()

    success, err = verify_otp(email, otp_input)

    if not success:
        if err == "not_found":
            raise HTTPException(400, "OTP not found. Please request a new one.")
        elif err == "expired":
            raise HTTPException(400, "OTP has expired. Please request a new one.")
        elif err == "max_attempts":
            raise HTTPException(400, "Too many incorrect attempts. Please request a new OTP.")
        elif err.startswith("invalid:"):
            remaining = err.split(":")[1]
            raise HTTPException(400, f"Invalid OTP. {remaining} attempt(s) remaining.")
        else:
            raise HTTPException(400, "Invalid OTP. Please try again.")

    # ── OTP correct — move from pending → users_col ───────────────────────
    pending = _get_pending(email)

    if pending:
        user_doc = pending["user_doc"]
        user_doc["verified"] = True
        user_doc["verified_at"] = datetime.utcnow().isoformat()
        try:
            users_col().replace_one({"email": email}, user_doc, upsert=True)
        except Exception as e:
            raise HTTPException(500, f"Failed to save account: {str(e)}")
        _delete_pending(email)
    else:
        # Fallback: pending expired but user exists in DB as unverified
        try:
            result = users_col().update_one(
                {"email": email},
                {"$set": {"verified": True, "verified_at": datetime.utcnow().isoformat()}},
            )
            if result.matched_count == 0:
                raise HTTPException(404, "Registration session expired. Please register again.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Failed to activate account: {str(e)}")

    return {"message": "Account verified successfully. You can now login."}


@app.post("/auth/resend-otp")
async def resend_otp(body: ResendOtpRequest):
    email = body.email.lower().strip()

    # Check pending store (primary path)
    pending = _get_pending(email)
    if pending:
        username = pending["user_doc"].get("username", "User")
        otp = generate_otp(email)
        send_otp_email(email, otp, username)
        return {"message": "New OTP sent to your email."}

    # Fallback: check DB for unverified (edge case after pending TTL expires)
    try:
        user = users_col().find_one({"email": email})
    except Exception as e:
        raise HTTPException(503, f"Database unavailable: {str(e)}")

    if not user:
        raise HTTPException(404, "Email not found. Please register first.")
    if user.get("verified"):
        raise HTTPException(400, "Account already verified. Please login.")

    otp = generate_otp(email)
    send_otp_email(email, otp, user.get("username", "User"))
    return {"message": "New OTP sent to your email."}


@app.get("/auth/debug-email")
async def debug_email(secret: str = ""):
    """Diagnostic: tests email delivery config. Pass ?secret=debug123."""
    if secret != "debug123":
        raise HTTPException(403, "Forbidden")
    import smtplib
    smtp_user = os.getenv("SMTP_USER", "NOT SET")
    smtp_pass = os.getenv("SMTP_PASS", "")
    result = {
        "smtp_user": smtp_user,
        "smtp_pass_configured": bool(smtp_pass),
        "resend_key_configured": bool(os.getenv("RESEND_API_KEY", "")),
        "port_465_test": "not attempted",
        "port_587_test": "not attempted",
    }
    if smtp_user != "NOT SET" and smtp_pass:
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
                s.login(smtp_user, smtp_pass)
            result["port_465_test"] = "LOGIN OK"
        except Exception as e:
            result["port_465_test"] = f"FAILED: {e}"
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as s:
                s.starttls()
                s.login(smtp_user, smtp_pass)
            result["port_587_test"] = "LOGIN OK"
        except Exception as e:
            result["port_587_test"] = f"FAILED: {e}"
    return result


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
                   "ollama", "offline", "timeout", "system", "backend",
                   "llm", "unavailable", "groq", "data", "error", "available", "determine"}
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

    # ── Fetch latest meta-intelligence session metrics ────────────────────
    latest_meta_insights = None
    try:
        memory = Memory()
        events = memory.get_events()
        # Find the most recent PerformanceEvaluation event
        for event in reversed(events):
            if event.get("agent") == "Meta-PerformanceEvaluation":
                perf = event.get("details", {})
                latest_meta_insights = {
                    "performance": perf,
                    "prompts_optimized": any(
                        e.get("agent") == "Meta-PromptOptimization" for e in events
                    ),
                    "strategy_updated": any(
                        e.get("agent") == "Meta-StrategyOptimization" for e in events
                    )
                }
                break
    except Exception:
        pass

    return {
        "metrics": {
            "total_incidents": total_incidents,
            "critical_alerts": critical_alerts,
            "departments": departments,
            "resolution_time": resolution_time
        },
        "trends": {"chart": trend_chart, "distribution": trend_distribution},
        "incidents": incidents,
        "history": history_data[:10],
        "meta_insights": latest_meta_insights
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


@app.get("/admin/meta-insights")
async def get_admin_meta_insights(current_user: dict = Depends(get_current_user)):
    """Returns the latest meta-intelligence session metrics (admin only)."""
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    try:
        memory = Memory()
        events = memory.get_events()
        for event in reversed(events):
            if event.get("agent") == "Meta-PerformanceEvaluation":
                perf = event.get("details", {})
                return {
                    "performance": perf,
                    "prompts_optimized": any(
                        e.get("agent") == "Meta-PromptOptimization" for e in events
                    ),
                    "strategy_updated": any(
                        e.get("agent") == "Meta-StrategyOptimization" for e in events
                    )
                }
        return None
    except Exception as e:
        raise HTTPException(500, f"Could not fetch meta-insights: {e}")

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

@app.delete("/admin/clear-alerts")
async def clear_all_alerts(current_user: dict = Depends(get_current_user)):
    """Delete all alerts from the database (admin only). Useful to clear stale pre-migration alerts."""
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    try:
        result = alerts_col().delete_many({})
        return {"message": f"Cleared {result.deleted_count} alert(s) successfully."}
    except Exception as e:
        raise HTTPException(500, f"Failed to clear alerts: {e}")

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
import os

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)