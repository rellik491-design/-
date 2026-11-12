from enum import Enum


class ContactStatus(str, Enum):
    ACTIVE = "active"               # Обычный активный диалог
    LEAD_MEETING = "lead_meeting"   # Позвал на встречу / свидание (лид)
    PAUSED_MANUAL = "paused_manual" # Ручная пауза владельцем
    STALE = "stale"                 # Диалог временно затих
    BLOCKED = "blocked"             # Игнор / блокировка


class AccountStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    WAITING_CODE = "waiting_code"
    WAITING_2FA = "waiting_2fa"
    BANNED = "banned"


class PlanDuration(str, Enum):
    DAYS_3 = "3_days"
    DAYS_7 = "7_days"
    DAYS_14 = "14_days"
    DAYS_31 = "31_days"

    @property
    def days_count(self) -> int:
        mapping = {
            PlanDuration.DAYS_3: 3,
            PlanDuration.DAYS_7: 7,
            PlanDuration.DAYS_14: 14,
            PlanDuration.DAYS_31: 31,
        }
        return mapping[self]

    @property
    def max_accounts_allowed(self) -> int:
        mapping = {
            PlanDuration.DAYS_3: 1,
            PlanDuration.DAYS_7: 1,
            PlanDuration.DAYS_14: 2,
            PlanDuration.DAYS_31: 5,
        }
        return mapping[self]


class MessageSender(str, Enum):
    CONTACT = "contact"
    BOT = "bot"
    OWNER = "owner"  # Ручной перехват владельцем
