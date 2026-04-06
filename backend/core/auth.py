"""
Authentication utilities: password hashing, JWT, OTP generation and email.
"""
import os
import random
import string
import threading
from datetime import datetime, timedelta

import resend

from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import HTTPException, status, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# --- Config ---
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "enterprise-ai-super-secret-key-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "Enterprise AI <onboarding@resend.dev>")
resend.api_key = RESEND_API_KEY

# --- Bcrypt context ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

# --- In-memory OTP store: {email: {"otp": str, "expires": datetime}} ---
_otp_store: dict = {}


# ─── Password ────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ─── JWT ─────────────────────────────────────────────────────────────────────

def create_jwt(username: str, role: str, email: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "role": role,
        "email": email,
        "exp": expire
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_jwt(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)):
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return decode_jwt(credentials.credentials)


# ─── OTP ─────────────────────────────────────────────────────────────────────

def generate_otp(email: str) -> str:
    otp = "".join(random.choices(string.digits, k=6))
    _otp_store[email] = {
        "otp": otp,
        "expires": datetime.utcnow() + timedelta(minutes=10)
    }
    return otp

def verify_otp(email: str, otp: str) -> bool:
    record = _otp_store.get(email)
    if not record:
        return False
    if datetime.utcnow() > record["expires"]:
        del _otp_store[email]
        return False
    if record["otp"] != otp:
        return False
    del _otp_store[email]
    return True


# ─── Email ───────────────────────────────────────────────────────────────────

def _send_email_sync(to_email: str, subject: str, html_body: str):
    """Send email synchronously via Resend API (run in thread)."""
    if not RESEND_API_KEY:
        print(f"\n{'─'*60}")
        print(f"[EMAIL SIMULATION] No RESEND_API_KEY set.")
        print(f"[EMAIL SIMULATION] TO: {to_email}")
        print(f"[EMAIL SIMULATION] SUBJECT: {subject}")
        print(f"[EMAIL SIMULATION] BODY PREVIEW: {html_body[:200]}")
        print(f"{'─'*60}\n")
        return
    try:
        params = {
            "from": RESEND_FROM,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        }
        response = resend.Emails.send(params)
        print(f"[EMAIL] Sent to {to_email} via Resend | id={response.get('id')}")
    except Exception as e:
        print(f"[EMAIL ERROR] Resend failed for {to_email}: {e}")

def send_email_async(to_email: str, subject: str, html_body: str):
    """Fire-and-forget email using a background thread."""
    t = threading.Thread(target=_send_email_sync, args=(to_email, subject, html_body), daemon=True)
    t.start()

def send_otp_email(email: str, otp: str, username: str):
    subject = "🔐 Your Enterprise AI Platform OTP Code"
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:auto;background:#0b0f19;color:#e2e8f0;padding:32px;border-radius:12px;">
      <h2 style="color:#38bdf8;text-align:center;">Enterprise AI Platform</h2>
      <p>Hello <strong>{username}</strong>,</p>
      <p>Your One-Time Password for account verification:</p>
      <div style="text-align:center;margin:24px 0;">
        <span style="font-size:36px;font-weight:bold;letter-spacing:12px;color:#38bdf8;background:#1e2a3a;padding:16px 24px;border-radius:8px;">{otp}</span>
      </div>
      <p style="color:#94a3b8;font-size:13px;">This OTP expires in <strong>10 minutes</strong>. Do not share it.</p>
    </div>
    """
    print(f"\n{'═'*60}")
    print(f"[OTP] Email: {email}  |  OTP CODE: {otp}")
    print(f"{'═'*60}\n")
    send_email_async(email, subject, html_body)


def send_alert_email(to_emails: list, alert_data: dict):
    subject = "🚨 Critical Alert Detected in Enterprise System"
    ts = alert_data.get("timestamp", "N/A")
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;background:#0b0f19;color:#e2e8f0;padding:32px;border-radius:12px;border:1px solid #ef4444;">
      <h2 style="color:#ef4444;text-align:center;">🚨 Critical Alert Detected</h2>
      <table style="width:100%;border-collapse:collapse;margin-top:16px;">
        <tr><td style="padding:8px;color:#94a3b8;width:40%;">Detected Issue</td><td style="padding:8px;color:#f1f5f9;">{alert_data.get('issue','N/A')}</td></tr>
        <tr style="background:#111827;"><td style="padding:8px;color:#94a3b8;">Root Cause</td><td style="padding:8px;color:#f1f5f9;">{alert_data.get('root_cause','N/A')}</td></tr>
        <tr><td style="padding:8px;color:#94a3b8;">Severity</td><td style="padding:8px;font-weight:bold;color:#ef4444;">{alert_data.get('severity','HIGH')}</td></tr>
        <tr style="background:#111827;"><td style="padding:8px;color:#94a3b8;">Recommended Action</td><td style="padding:8px;color:#f1f5f9;">{alert_data.get('recommended_action','N/A')}</td></tr>
        <tr><td style="padding:8px;color:#94a3b8;">Timestamp</td><td style="padding:8px;color:#94a3b8;">{ts}</td></tr>
      </table>
      <p style="margin-top:24px;color:#64748b;font-size:12px;text-align:center;">Enterprise AI — Autonomous Intelligence Platform</p>
    </div>
    """
    for email in to_emails:
        send_email_async(email, subject, html_body)
