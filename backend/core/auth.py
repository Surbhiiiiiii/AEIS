"""
Authentication utilities: password hashing, JWT, OTP generation and email.

Email strategy (in priority order):
  1. Gmail SMTP  — if SMTP_USER + SMTP_PASS are set (sends to ANY email, free)
  2. Resend API  — if RESEND_API_KEY is set (only works to verified recipients on free plan)
  3. Console log — if neither is configured (dev/test fallback)
"""
import os
import random
import string
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import HTTPException, status, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# --- Config ---
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "enterprise-ai-super-secret-key-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

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

def _send_via_gmail(to_email: str, subject: str, html_body: str) -> bool:
    """Send via Gmail SMTP. Returns True on success."""
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASS", "").strip()
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if not smtp_user or not smtp_pass:
        return False  # not configured

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Enterprise AI Platform <{smtp_user}>"
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [to_email], msg.as_string())

        print(f"[EMAIL] ✅ Sent to {to_email} via Gmail SMTP ({smtp_user})")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] ❌ Gmail SMTP failed for {to_email}: {e}")
        return False


def _send_via_resend(to_email: str, subject: str, html_body: str) -> bool:
    """Send via Resend API. Returns True on success."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_addr = os.getenv("RESEND_FROM", "Enterprise AI <onboarding@resend.dev>")

    if not api_key:
        return False  # not configured

    try:
        import resend as resend_lib
        resend_lib.api_key = api_key
        params = {
            "from": from_addr,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        }
        response = resend_lib.Emails.send(params)
        email_id = response.get("id") if isinstance(response, dict) else str(response)
        print(f"[EMAIL] ✅ Sent to {to_email} via Resend | id={email_id}")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] ❌ Resend failed for {to_email}: {e}")
        return False


def _send_email_sync(to_email: str, subject: str, html_body: str):
    """
    Send email with automatic provider fallback:
      1. Gmail SMTP  (works with any recipient — requires SMTP_USER + SMTP_PASS)
      2. Resend API  (free tier only delivers to the Resend account owner's email)
      3. Console log (dev fallback — prints OTP to terminal)
    """
    # Try Gmail SMTP first — works for all recipients
    if _send_via_gmail(to_email, subject, html_body):
        return

    # Try Resend as fallback
    if _send_via_resend(to_email, subject, html_body):
        return

    # Last resort: print to console
    print(f"\n{'─'*60}")
    print(f"[EMAIL FALLBACK] No email provider configured.")
    print(f"[EMAIL FALLBACK] TO: {to_email} | SUBJECT: {subject}")
    print(f"{'─'*60}\n")


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
      <p style="color:#64748b;font-size:11px;text-align:center;margin-top:24px;">Enterprise AI — Autonomous Intelligence Platform</p>
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
