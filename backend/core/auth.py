"""
Authentication utilities: password hashing, JWT, OTP generation and email.

OTP Storage: MongoDB-backed (survives Render restarts and multi-worker deployments).
Email Strategy (priority order):
  1. Gmail SMTP port 465/SSL — not blocked by Render free tier
  2. Gmail SMTP port 587/STARTTLS — fallback
  3. Resend API — fallback (free tier: only delivers to Resend account owner)
  4. Console log — dev fallback
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

# ─── Config ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "enterprise-ai-super-secret-key-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
OTP_EXPIRY_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

# ─── Auth context ─────────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


# ─── Helpers: get MongoDB collections lazily ─────────────────────────────────
def _otp_col():
    from core.database import get_db
    return get_db()["otp_store"]

def _ensure_otp_index():
    """Create TTL index on otp_store so MongoDB auto-expires documents."""
    try:
        _otp_col().create_index("expires_at", expireAfterSeconds=0)
    except Exception:
        pass  # index already exists or DB unavailable


# ─── Password ─────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ─── JWT ──────────────────────────────────────────────────────────────────────

def create_jwt(username: str, role: str, email: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": username, "role": role, "email": email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)):
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_jwt(credentials.credentials)


# ─── OTP (MongoDB-backed — survives restarts & multi-worker) ──────────────────

def generate_otp(email: str) -> str:
    """
    Generate a 6-digit OTP, store it in MongoDB with a TTL expiry.
    Previous OTP for this email is overwritten (safe resend).
    """
    otp = "".join(random.choices(string.digits, k=6))
    expires_at = datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES)

    try:
        _otp_col().replace_one(
            {"email": email},
            {
                "email": email,
                "otp": otp,
                "expires_at": expires_at,        # used by MongoDB TTL index
                "created_at": datetime.utcnow().isoformat(),
                "attempts": 0,
            },
            upsert=True,
        )
    except Exception as e:
        print(f"[OTP] MongoDB write failed, using in-memory fallback: {e}")
        # In-memory fallback so registration never hard-breaks
        _otp_fallback[email] = {"otp": otp, "expires": expires_at, "attempts": 0}

    return otp


# In-memory fallback (only used if MongoDB is unavailable)
_otp_fallback: dict = {}


def verify_otp(email: str, otp_input: str) -> tuple[bool, str]:
    """
    Verify OTP. Returns (success: bool, error_message: str).
    Error messages: "", "not_found", "expired", "invalid", "max_attempts"
    """
    record = None

    # Try MongoDB first
    try:
        record = _otp_col().find_one({"email": email})
    except Exception as e:
        print(f"[OTP] MongoDB read failed: {e}")

    # Fall back to in-memory
    if record is None:
        fb = _otp_fallback.get(email)
        if fb:
            record = fb
            record["_from_fallback"] = True

    if not record:
        return False, "not_found"

    # Check expiry (belt-and-suspenders — MongoDB TTL may have a ~60s lag)
    expires = record.get("expires_at") or record.get("expires")
    if expires and datetime.utcnow() > expires:
        _delete_otp(email, record.get("_from_fallback", False))
        return False, "expired"

    # Check attempt limit
    attempts = record.get("attempts", 0)
    if attempts >= OTP_MAX_ATTEMPTS:
        _delete_otp(email, record.get("_from_fallback", False))
        return False, "max_attempts"

    # Check value
    if record.get("otp", "") != otp_input.strip():
        # Increment attempt counter
        try:
            _otp_col().update_one({"email": email}, {"$inc": {"attempts": 1}})
        except Exception:
            if email in _otp_fallback:
                _otp_fallback[email]["attempts"] = attempts + 1
        remaining = OTP_MAX_ATTEMPTS - attempts - 1
        return False, f"invalid:{remaining}"

    # ✅ Correct OTP — delete it (one-time use)
    _delete_otp(email, record.get("_from_fallback", False))
    return True, ""


def _delete_otp(email: str, from_fallback: bool = False):
    if from_fallback:
        _otp_fallback.pop(email, None)
        return
    try:
        _otp_col().delete_one({"email": email})
    except Exception:
        _otp_fallback.pop(email, None)


# ─── Email ────────────────────────────────────────────────────────────────────

def _send_via_gmail(to_email: str, subject: str, html_body: str) -> bool:
    """Try port 465 (SSL, not blocked by Render), then 587 (STARTTLS)."""
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASS", "").strip()
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")

    if not smtp_user or not smtp_pass:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Enterprise AI Platform <{smtp_user}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    # Attempt 1: port 465 SSL (works on Render free tier)
    try:
        with smtplib.SMTP_SSL(smtp_host, 465, timeout=15) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [to_email], msg.as_string())
        print(f"[EMAIL] ✅ Sent to {to_email} via Gmail SSL:465")
        return True
    except Exception as e1:
        print(f"[EMAIL] Port 465 failed ({e1}), trying 587...")

    # Attempt 2: port 587 STARTTLS
    try:
        with smtplib.SMTP(smtp_host, 587, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [to_email], msg.as_string())
        print(f"[EMAIL] ✅ Sent to {to_email} via Gmail STARTTLS:587")
        return True
    except Exception as e2:
        print(f"[EMAIL ERROR] ❌ Gmail SMTP failed entirely: 465={e1}, 587={e2}")
        return False


def _send_via_resend(to_email: str, subject: str, html_body: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_addr = os.getenv("RESEND_FROM", "Enterprise AI <onboarding@resend.dev>")
    if not api_key:
        return False
    try:
        import resend as resend_lib
        resend_lib.api_key = api_key
        response = resend_lib.Emails.send({
            "from": from_addr, "to": [to_email],
            "subject": subject, "html": html_body,
        })
        email_id = response.get("id") if isinstance(response, dict) else str(response)
        print(f"[EMAIL] ✅ Sent to {to_email} via Resend | id={email_id}")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] ❌ Resend failed: {e}")
        return False


def _send_email_sync(to_email: str, subject: str, html_body: str):
    if _send_via_gmail(to_email, subject, html_body):
        return
    if _send_via_resend(to_email, subject, html_body):
        return
    print(f"\n{'─'*60}")
    print(f"[EMAIL FALLBACK] No provider worked. TO: {to_email}")
    print(f"{'─'*60}\n")


def send_email_async(to_email: str, subject: str, html_body: str):
    t = threading.Thread(
        target=_send_email_sync, args=(to_email, subject, html_body), daemon=True
    )
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
      <p style="color:#94a3b8;font-size:13px;">
        This OTP expires in <strong>10 minutes</strong>.<br/>
        You have <strong>{OTP_MAX_ATTEMPTS} attempts</strong> before it is invalidated.
      </p>
      <p style="color:#64748b;font-size:11px;text-align:center;margin-top:24px;">Enterprise AI — Autonomous Intelligence Platform</p>
    </div>
    """
    print(f"\n{'═'*60}")
    print(f"[OTP] ▶ Email: {email}  |  CODE: {otp}  |  Expires: {OTP_EXPIRY_MINUTES}min")
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
