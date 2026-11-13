import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.config import settings
from companion.models.db import get_db
from companion.models.entities import Persona, User

logger = logging.getLogger(__name__)


class TelegramAuthError( HTTPException ):
    def __init__(self, detail: str, status_code: int = status.HTTP_401_UNAUTHORIZED):
        super().__init__(status_code=status_code, detail=detail)


def build_signed_telegram_init_data(
    user_payload: Dict[str, Any],
    bot_token: str,
    auth_date: Optional[int] = None,
    start_param: Optional[str] = None,
    query_id: str = "AAHdF6IQAAAAAN0XohDhrOrc",
) -> str:
    """
    Helper for generating cryptographically valid Telegram WebApp initData strings
    (used in automated tests and local development verification).
    """
    from urllib.parse import urlencode

    ts = auth_date if auth_date is not None else int(time.time())
    user_json = json.dumps(user_payload, separators=(",", ":"), ensure_ascii=False)
    params: Dict[str, str] = {
        "auth_date": str(ts),
        "query_id": query_id,
        "user": user_json,
    }
    if start_param:
        params["start_param"] = start_param

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    signature = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = signature
    return urlencode(params)


def validate_telegram_init_data(
    init_data_raw: str,
    bot_token: Optional[str] = None,
    max_age_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Server-side cryptographic validation of Telegram Mini App `initData` per official Telegram WebApp spec:
    1. Parses query string and extracts `hash`.
    2. Sorts all remaining key=value pairs alphabetically and joins with `\\n`.
    3. Computes `secret_key = HMAC_SHA256("WebAppData", bot_token)`.
    4. Verifies `HMAC_SHA256(secret_key, data_check_string) == hash` in constant time.
    5. Verifies `auth_date` freshness to prevent replay attacks.
    NEVER trusts `initDataUnsafe` or unverified client IDs.
    """
    if not init_data_raw or not init_data_raw.strip():
        raise TelegramAuthError("Missing Telegram WebApp initData")

    resolved_token = (bot_token or settings.ADMIN_BOT_TOKEN or "").strip()
    if not resolved_token:
        raise TelegramAuthError(
            "Server bot token is not configured for initData validation",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    parsed_pairs = dict(parse_qsl(init_data_raw, keep_blank_values=True))
    received_hash = parsed_pairs.pop("hash", None)
    if not received_hash:
        raise TelegramAuthError("Missing cryptographic hash in initData")

    auth_date_str = parsed_pairs.get("auth_date")
    if not auth_date_str or not auth_date_str.isdigit():
        raise TelegramAuthError("Invalid or missing auth_date in initData")

    auth_date = int(auth_date_str)
    max_age = max_age_seconds if max_age_seconds is not None else settings.INIT_DATA_MAX_AGE_SECONDS
    now_ts = int(time.time())
    if max_age > 0 and (now_ts - auth_date) > max_age:
        raise TelegramAuthError("Telegram initData has expired; please reopen the Mini App")
    if (auth_date - now_ts) > 300:
        raise TelegramAuthError("Invalid future auth_date in initData")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_pairs.items()))
    secret_key = hmac.new(b"WebAppData", resolved_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash.lower(), received_hash.lower()):
        raise TelegramAuthError("Invalid Telegram initData signature")

    user_raw = parsed_pairs.get("user")
    if not user_raw:
        raise TelegramAuthError("Missing user object in signed initData")

    try:
        user_obj = json.loads(user_raw)
    except Exception:
        raise TelegramAuthError("Malformed user JSON in initData")

    if not isinstance(user_obj, dict) or not user_obj.get("id"):
        raise TelegramAuthError("Missing Telegram user ID in signed initData")

    return {
        "user": user_obj,
        "auth_date": auth_date,
        "query_id": parsed_pairs.get("query_id"),
        "start_param": parsed_pairs.get("start_param"),
        "chat_type": parsed_pairs.get("chat_type"),
    }


def create_session_token(
    telegram_id: int,
    db_user_id: int,
    username: Optional[str] = None,
    is_admin: bool = False,
    ttl_seconds: int = 86400,
) -> str:
    """Issues an HMAC-SHA256 signed session token after validating Telegram initData."""
    payload = {
        "tg_id": int(telegram_id),
        "uid": int(db_user_id),
        "uname": username or "",
        "adm": bool(is_admin),
        "exp": int(time.time()) + ttl_seconds,
    }
    raw_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")
    secret = (settings.SESSION_SECRET_KEY or settings.ADMIN_BOT_TOKEN or "fallback-secret").encode("utf-8")
    sig = hmac.new(secret, b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{sig}"


def verify_session_token(token: str) -> Dict[str, Any]:
    """Verifies an HMAC-SHA256 session token and checks expiration."""
    if not token or "." not in token:
        raise TelegramAuthError("Invalid session token format")

    b64_payload, received_sig = token.split(".", 1)
    secret = (settings.SESSION_SECRET_KEY or settings.ADMIN_BOT_TOKEN or "fallback-secret").encode("utf-8")
    expected_sig = hmac.new(secret, b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, received_sig):
        raise TelegramAuthError("Invalid session token signature")

    padding = "=" * (-len(b64_payload) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(b64_payload + padding).decode("utf-8"))
    except Exception:
        raise TelegramAuthError("Malformed session token payload")

    if int(payload.get("exp", 0)) < int(time.time()):
        raise TelegramAuthError("Session token has expired")

    return payload


async def ensure_user_from_telegram_data(
    session: AsyncSession,
    tg_user: Dict[str, Any],
) -> User:
    """Finds or creates the User and default Persona in the database after server-side auth."""
    tg_id = int(tg_user["id"])
    username = tg_user.get("username")

    stmt = select(User).where(User.telegram_id == tg_id)
    user = (await session.execute(stmt)).scalars().first()
    if not user:
        user = User(telegram_id=tg_id, username=username)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    elif username and user.username != username:
        user.username = username
        await session.commit()

    stmt_p = select(Persona).where(Persona.user_id == user.id)
    persona = (await session.execute(stmt_p)).scalars().first()
    if not persona:
        persona = Persona(
            user_id=user.id,
            name="Алина",
            age=22,
            city="Москва",
            timezone_name="Europe/Moscow",
            sleep_start="23:00",
            sleep_end="09:00",
            current_style="adaptive_all",
        )
        session.add(persona)
        await session.commit()

    return user


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI Dependency: Authenticates the request using either:
    1. `Authorization: Bearer <session_token>` (issued after `initData` verification)
    2. `X-Telegram-Init-Data: <raw_signed_init_data>` (validated directly via HMAC-SHA256)
    Never trusts raw `user_id` or `initDataUnsafe`.
    """
    auth_header = request.headers.get("Authorization", "")
    init_data_header = request.headers.get("X-Telegram-Init-Data", "")

    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        payload = verify_session_token(token)
        tg_id = int(payload["tg_id"])
        stmt = select(User).where(User.telegram_id == tg_id)
        user = (await session.execute(stmt)).scalars().first()
        if not user:
            raise TelegramAuthError("Authenticated user no longer exists")
        return user

    if init_data_header:
        validated = validate_telegram_init_data(init_data_header)
        return await ensure_user_from_telegram_data(session, validated["user"])

    raise TelegramAuthError(
        "Authentication required: provide X-Telegram-Init-Data or Bearer session token"
    )


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Enforces that the authenticated Telegram user is the configured super-admin."""
    if not settings.ADMIN_TELEGRAM_ID or user.telegram_id != settings.ADMIN_TELEGRAM_ID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user
