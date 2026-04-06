"""
Real-time Alert Service for the Enterprise AI Platform.
Detects high-severity incidents, stores alerts in MongoDB, and sends email notifications.
"""
from datetime import datetime
from core.database import alerts_col, users_col
from core.auth import send_alert_email


def should_alert(severity: str, critic_quality: str = "") -> bool:
    """Return True if the situation warrants an alert."""
    sev = str(severity).upper()
    return sev in ("HIGH", "CRITICAL")


def store_alert(alert_doc: dict) -> str:
    """Insert alert into MongoDB and return inserted id as string."""
    try:
        result = alerts_col().insert_one(alert_doc)
        return str(result.inserted_id)
    except Exception as e:
        print(f"[ALERT] Failed to store alert in DB: {e}")
        return ""


def get_admin_emails() -> list:
    """Fetch emails of all verified admin users from MongoDB."""
    try:
        admins = users_col().find({"role": "admin", "verified": True}, {"email": 1})
        return [a["email"] for a in admins if "email" in a]
    except Exception as e:
        print(f"[ALERT] Could not fetch admin emails: {e}")
        return []


def trigger_alert(analysis: dict, action: dict, evaluation: dict, user_email: str = None):
    """
    Main orchestration function.
    Called after ExecutorAgent or CriticAgent when severity is HIGH.
    """
    analysis_data = analysis.get("analysis_data", {}) if analysis else {}
    severity = str(action.get("severity", analysis_data.get("severity", "LOW"))).upper()
    critic_quality = evaluation.get("quality", "") if evaluation else ""

    if not should_alert(severity, critic_quality):
        return None

    timestamp = datetime.utcnow().isoformat() + "Z"
    alert_doc = {
        "issue": analysis_data.get("major_issue", "Unknown Issue"),
        "root_cause": analysis_data.get("root_cause", "Unknown"),
        "severity": severity,
        "action": action.get("action", "No action specified"),
        "recommended_action": analysis_data.get("recommended_action", ""),
        "critic_score": evaluation.get("decision_score", 0.0) if evaluation else 0.0,
        "timestamp": timestamp,
        "notified": False,
        "read": False
    }

    alert_id = store_alert(alert_doc)

    # Collect recipients: current analyst + all admins
    recipients = []
    if user_email:
        recipients.append(user_email)
    admin_emails = get_admin_emails()
    for e in admin_emails:
        if e not in recipients:
            recipients.append(e)

    if recipients:
        send_alert_email(recipients, alert_doc)
        # Mark as notified
        try:
            if alert_id:
                from bson import ObjectId
                alerts_col().update_one(
                    {"_id": ObjectId(alert_id)},
                    {"$set": {"notified": True}}
                )
        except Exception:
            pass

    print(f"[ALERT TRIGGERED] Severity={severity} | Issue={alert_doc['issue']}")
    return alert_doc
