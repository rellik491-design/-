"""
Central Fail-Safe Health Manager & Circuit Breaker.
Tracks provider health, token/quota exhaustion, Telegram connectivity,
and enforces the SAFE PAUSED state to prevent hallucinations, message loops,
and unprompted fallbacks during outages.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, Optional

from companion.config import settings

logger = logging.getLogger(__name__)


class SystemHealthState(str, Enum):
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    PAUSED = "PAUSED"
    RECOVERING = "RECOVERING"


class HealthManager:
    """
    Singleton-style central health manager.
    Coordinates circuit-breaker status across AI providers, Telegram transports,
    and database operations.
    """

    _state: SystemHealthState = SystemHealthState.RUNNING
    _paused_reason: Optional[str] = None
    _paused_at: Optional[datetime] = None
    _consecutive_errors: int = 0
    _consecutive_successes: int = 0
    _last_error: Optional[str] = None
    _last_error_time: Optional[datetime] = None
    _last_success_time: Optional[datetime] = None
    _total_errors: int = 0
    _total_successes: int = 0
    _recovery_probe_task: Optional[asyncio.Task] = None

    # Error classification signatures
    CRITICAL_SIGNATURES = [
        "resource_exhausted",
        "429",
        "quota",
        "quota exceeded",
        "generaterequestsperday",
        "api_key_invalid",
        "invalid api key",
        "unauthenticated",
        "permission_denied",
        "401",
        "403",
        "account_disabled",
        "billing_disabled",
        "billing not active",
    ]

    RECOVERABLE_SIGNATURES = [
        "timeout",
        "timed out",
        "503",
        "unavailable",
        "high demand",
        "connection reset",
        "connecterror",
    ]

    @classmethod
    def get_state(cls) -> SystemHealthState:
        return cls._state

    @classmethod
    def is_paused(cls) -> bool:
        return cls._state == SystemHealthState.PAUSED

    @classmethod
    def is_healthy(cls) -> bool:
        return cls._state in (SystemHealthState.RUNNING, SystemHealthState.DEGRADED)

    @classmethod
    def get_paused_reason(cls) -> Optional[str]:
        return cls._paused_reason

    @classmethod
    def classify_error(cls, error: Any) -> str:
        """
        Classifies errors into:
        - 'critical': immediately triggers PAUSED state (quota, invalid key, auth)
        - 'recoverable': transient network/concurrency glitch (retry with backoff)
        - 'message_specific': bad attachment, malformed text (skip item, do not pause system)
        """
        err_str = str(error).lower()

        for sig in cls.CRITICAL_SIGNATURES:
            if sig in err_str:
                return "critical"

        for sig in cls.RECOVERABLE_SIGNATURES:
            if sig in err_str:
                return "recoverable"

        return "message_specific"

    @classmethod
    def report_provider_error(cls, error: Any, context: str = "ai_generation") -> None:
        """
        Records an error and triggers fail-safe PAUSE if the error is critical
        or if repeated recoverable errors exceed the safety threshold.
        """
        now = datetime.now(timezone.utc)
        err_str = str(error)
        err_class = cls.classify_error(error)

        cls._consecutive_errors += 1
        cls._consecutive_successes = 0
        cls._total_errors += 1
        cls._last_error = err_str[:300]
        cls._last_error_time = now

        if err_class == "critical":
            cls._transition_to_paused(
                reason=f"CRITICAL_PROVIDER_ERROR: {err_str[:120]} (Context: {context})"
            )
        elif cls._consecutive_errors >= 5:
            cls._transition_to_paused(
                reason=f"EXCESSIVE_FAILURES: {cls._consecutive_errors} consecutive errors (Last: {err_str[:120]})"
            )
        elif cls._consecutive_errors >= 2 and cls._state == SystemHealthState.RUNNING:
            cls._state = SystemHealthState.DEGRADED
            logger.warning("[HEALTH] System transitioned to DEGRADED state due to consecutive errors: %d", cls._consecutive_errors)

    @classmethod
    def register_error(cls, error: Any, context: str = "ai_generation") -> None:
        """Alias for report_provider_error."""
        cls.report_provider_error(error, context=context)

    @classmethod
    def register_success(cls) -> None:
        """Alias for report_provider_success."""
        cls.report_provider_success()
        """
        Records a successful operation and smoothly restores RUNNING state if recovering.
        """
        cls._consecutive_successes += 1
        cls._consecutive_errors = 0
        cls._total_successes += 1
        cls._last_success_time = datetime.now(timezone.utc)

        if cls._state in (SystemHealthState.DEGRADED, SystemHealthState.RECOVERING):
            if cls._consecutive_successes >= 2:
                logger.info("[HEALTH] System fully restored to RUNNING state after successful operations.")
                cls._state = SystemHealthState.RUNNING
                cls._paused_reason = None
                cls._paused_at = None

    @classmethod
    def _transition_to_paused(cls, reason: str) -> None:
        """Enters the global fail-safe PAUSED state."""
        cls._state = SystemHealthState.PAUSED
        cls._paused_reason = reason
        cls._paused_at = datetime.now(timezone.utc)
        logger.critical(
            "[FAIL_SAFE] System ENTERED PAUSED STATE! Reason: %s. "
            "All AI generation and response sending are HALTED to prevent hallucination or question repetition.",
            reason
        )

        # Notify administrator asynchronously if admin credentials exist
        try:
            from companion.bot.admin_notifier import AdminNotifier
            if getattr(settings, "ADMIN_TELEGRAM_ID", None):
                asyncio.create_task(
                    AdminNotifier.send_telegram_alert(
                        f"🚨 <b>АВАРИЙНАЯ ОСТАНОВКА ИИ (FAIL-SAFE PAUSE)</b>\n\n"
                        f"Бот временно приостановил обработку сообщений во избежание галлюцинаций и зацикливаний.\n\n"
                        f"⚠️ <b>Причина:</b> <code>{reason}</code>\n"
                        f"⏳ Бот находится в режиме ожидания восстановления квоты/ключа.",
                        settings.ADMIN_TELEGRAM_ID,
                    )
                )
        except Exception as notify_err:
            logger.debug("Failed to dispatch admin fail-safe notification: %s", notify_err)

    @classmethod
    def manual_resume(cls, admin_name: str = "Admin") -> None:
        """Allows administrator to manually resume operations."""
        cls._state = SystemHealthState.RUNNING
        cls._paused_reason = None
        cls._paused_at = None
        cls._consecutive_errors = 0
        cls._consecutive_successes = 0
        logger.info("[HEALTH] System manually resumed by %s.", admin_name)

    @classmethod
    async def probe_ai_health(cls, api_key: Optional[str] = None) -> bool:
        """
        Lightweight health check probe: queries Gemini with minimal 1-token prompt
        to determine if quota/credits are restored without processing user messages.
        """
        from companion.ai.gemini_client import GeminiClient

        key = api_key or GeminiClient.get_api_key()
        if not key:
            return False

        try:
            # Minimal 1-token health check
            res = await GeminiClient.generate_content(
                contents="1",
                temperature=0.0,
                max_output_tokens=2,
                api_key=key,
                timeout_seconds=8.0,
                max_retries=1,
            )
            return bool(res and res.strip())
        except Exception as e:
            logger.debug("[HEALTH PROBE] Health check failed: %s", e)
            return False

    @classmethod
    def get_diagnostics(cls) -> Dict[str, Any]:
        """Returns structured health telemetry for /diag and monitoring."""
        return {
            "state": cls._state.value,
            "paused_reason": cls._paused_reason,
            "paused_at": cls._paused_at.isoformat() if cls._paused_at else None,
            "consecutive_errors": cls._consecutive_errors,
            "consecutive_successes": cls._consecutive_successes,
            "total_errors": cls._total_errors,
            "total_successes": cls._total_successes,
            "last_error": cls._last_error,
            "last_error_time": cls._last_error_time.isoformat() if cls._last_error_time else None,
            "last_success_time": cls._last_success_time.isoformat() if cls._last_success_time else None,
        }
