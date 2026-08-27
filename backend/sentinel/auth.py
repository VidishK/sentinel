"""Email + Google auth for the Sentinel landing page."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sentinel.config import get_settings
from sentinel.db import connect

_PBKDF = "sha256"
_ROUNDS = 120_000


def ensure_auth_tables() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sentinel.users (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email           STRING NOT NULL UNIQUE,
                name            STRING,
                password_hash   STRING,
                google_sub      STRING,
                email_verified  BOOL NOT NULL DEFAULT false,
                verify_token    STRING,
                verify_expires  TIMESTAMPTZ,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sentinel.auth_sessions (
                token       STRING PRIMARY KEY,
                user_id     UUID NOT NULL REFERENCES sentinel.users (id),
                expires_at  TIMESTAMPTZ NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )


def public_config() -> dict[str, Any]:
    settings = get_settings()
    client_id = (settings.google_client_id or "").strip()
    return {
        "google_enabled": bool(client_id),
        "google_client_id": client_id,
    }


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        _PBKDF, password.encode(), salt.encode(), _ROUNDS
    ).hex()
    return f"{salt}${digest}"


def _check_password(password: str, stored: str) -> bool:
    try:
        salt, _digest = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(_hash_password(password, salt), stored)


def _user_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row.get("name") or row["email"].split("@")[0],
        "email_verified": bool(row["email_verified"]),
    }


def _issue_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=14)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sentinel.auth_sessions (token, user_id, expires_at)
            VALUES (%s, %s, %s)
            """,
            (token, user_id, expires),
        )
    return token


def session_user(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    ensure_auth_tables()
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.email, u.name, u.email_verified
            FROM sentinel.auth_sessions s
            JOIN sentinel.users u ON u.id = s.user_id
            WHERE s.token = %s AND s.expires_at > now()
            """,
            (token,),
        )
        row = cur.fetchone()
    return _user_public(dict(row)) if row else None


def _send_verification(email: str, token: str) -> tuple[str, bool, str | None]:
    """Return (verify_url, sent, error). Resend is the working path; SES is optional."""
    settings = get_settings()
    verify_url = f"{settings.auth_public_url.strip().rstrip('/')}/?verify={token}"
    subject = "Verify your Sentinel email"
    text = (
        f"Welcome to Sentinel.\n\n"
        f"Confirm this email to finish creating your account:\n{verify_url}\n\n"
        f"If you did not sign up, you can ignore this message."
    )
    html = (
        "<p>Welcome to Sentinel.</p>"
        "<p>Confirm this email to finish creating your account.</p>"
        f'<p><a href="{verify_url}">Verify email address</a></p>'
    )
    from_addr = (settings.auth_from_email or "").strip() or (
        "Sentinel <onboarding@resend.dev>"
    )
    key = (settings.resend_api_key or "").strip()
    if key:
        import json
        import urllib.error
        import urllib.request

        payload = json.dumps(
            {
                "from": from_addr,
                "to": [email],
                "subject": subject,
                "text": text,
                "html": html,
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        import ssl

        ctx = ssl.create_default_context()
        try:
            import certifi

            ctx.load_verify_locations(cafile=certifi.where())
        except Exception:  # noqa: BLE001
            pass
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                if 200 <= resp.status < 300:
                    return verify_url, True, None
                return verify_url, False, f"Resend HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            return verify_url, False, f"Resend {exc.code}: {body}"
        except Exception as exc:  # noqa: BLE001
            return verify_url, False, str(exc)

    if from_addr and "@" in from_addr and "resend.dev" not in from_addr:
        try:
            ses = __import__("boto3").client("ses", region_name=settings.aws_region)
            ses.send_email(
                Source=from_addr,
                Destination={"ToAddresses": [email]},
                Message={
                    "Subject": {"Data": subject},
                    "Body": {
                        "Text": {"Data": text},
                        "Html": {"Data": html},
                    },
                },
            )
            return verify_url, True, None
        except Exception as exc:  # noqa: BLE001
            return verify_url, False, str(exc)

    return verify_url, False, "Set RESEND_API_KEY in .env to send real email."


def signup(email: str, password: str, name: str | None = None) -> dict[str, Any]:
    ensure_auth_tables()
    email = email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValueError("Enter a valid email address.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    token = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    hashed = _hash_password(password)
    display = (name or "").strip() or email.split("@")[0]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, email_verified FROM sentinel.users WHERE email = %s", (email,))
        existing = cur.fetchone()
        if existing and existing["email_verified"]:
            raise ValueError("An account with this email already exists. Log in instead.")
        if existing:
            cur.execute(
                """
                UPDATE sentinel.users
                SET password_hash = %s, name = %s, verify_token = %s, verify_expires = %s
                WHERE email = %s
                """,
                (hashed, display, token, expires, email),
            )
        else:
            cur.execute(
                """
                INSERT INTO sentinel.users
                    (id, email, name, password_hash, verify_token, verify_expires)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (str(uuid4()), email, display, hashed, token, expires),
            )
    verify_url, sent, send_error = _send_verification(email, token)
    payload: dict[str, Any] = {
        "status": "check_email",
        "email": email,
        "email_sent": sent,
        "message": (
            "We sent a verification link. Open it to activate your account."
            if sent
            else "Account created. Email did not send — use the verify button below, then add RESEND_API_KEY."
        ),
    }
    if send_error and not sent:
        payload["send_error"] = send_error
    settings = get_settings()
    public = (settings.auth_public_url or "").lower()
    local = "127.0.0.1" in public or "localhost" in public
    if settings.auth_dev_mode or not sent or local:
        payload["verify_url"] = verify_url
        payload["preview"] = {
            "from": settings.auth_from_email or "Sentinel <beth.t@example.com>",
            "to": email,
            "subject": "Verify your Sentinel email",
            "verify_url": verify_url,
        }
    return payload


def verify_email(token: str) -> dict[str, Any]:
    ensure_auth_tables()
    token = token.strip()
    if not token:
        raise ValueError("Missing verification token.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, email, name, email_verified, verify_expires
            FROM sentinel.users
            WHERE verify_token = %s
            """,
            (token,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("This verification link is invalid.")
        if row["verify_expires"] and row["verify_expires"] < datetime.now(timezone.utc):
            raise ValueError("This verification link has expired. Sign up again.")
        cur.execute(
            """
            UPDATE sentinel.users
            SET email_verified = true, verify_token = NULL, verify_expires = NULL
            WHERE id = %s
            RETURNING id, email, name, email_verified
            """,
            (str(row["id"]),),
        )
        user = dict(cur.fetchone())
    session = _issue_session(str(user["id"]))
    return {"token": session, "user": _user_public(user)}


def login(email: str, password: str) -> dict[str, Any]:
    ensure_auth_tables()
    email = email.strip().lower()
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, email, name, password_hash, email_verified
            FROM sentinel.users WHERE email = %s
            """,
            (email,),
        )
        row = cur.fetchone()
    if row is None or not row["password_hash"] or not _check_password(password, row["password_hash"]):
        raise ValueError("Email or password is incorrect.")
    if not row["email_verified"]:
        raise ValueError("Verify your email before signing in. Check your inbox.")
    token = _issue_session(str(row["id"]))
    return {"token": token, "user": _user_public(dict(row))}


def login_google(id_token: str) -> dict[str, Any]:
    settings = get_settings()
    client_id = (settings.google_client_id or "").strip()
    if not client_id:
        raise ValueError("Google sign-in is not configured.")
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
    except ImportError as exc:
        raise ValueError("Google sign-in is unavailable.") from exc
    info = google_id_token.verify_oauth2_token(
        id_token, google_requests.Request(), client_id
    )
    sub = str(info.get("sub") or "")
    email = str(info.get("email") or "").lower()
    name = str(info.get("name") or email.split("@")[0])
    if not sub or not email:
        raise ValueError("Google did not return an email address.")
    ensure_auth_tables()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, name, email_verified FROM sentinel.users WHERE google_sub = %s OR email = %s",
            (sub, email),
        )
        row = cur.fetchone()
        if row is None:
            user_id = str(uuid4())
            cur.execute(
                """
                INSERT INTO sentinel.users
                    (id, email, name, google_sub, email_verified)
                VALUES (%s, %s, %s, %s, true)
                RETURNING id, email, name, email_verified
                """,
                (user_id, email, name, sub),
            )
            row = cur.fetchone()
        else:
            cur.execute(
                """
                UPDATE sentinel.users
                SET google_sub = COALESCE(google_sub, %s),
                    email_verified = true,
                    name = COALESCE(NULLIF(name, ''), %s)
                WHERE id = %s
                RETURNING id, email, name, email_verified
                """,
                (sub, name, str(row["id"])),
            )
            row = cur.fetchone()
    token = _issue_session(str(row["id"]))
    return {"token": token, "user": _user_public(dict(row))}


def logout(token: str | None) -> None:
    if not token:
        return
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sentinel.auth_sessions WHERE token = %s", (token,))
