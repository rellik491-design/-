from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Telegram MTProto Client (Userbot)
    TELEGRAM_API_ID: int = 0
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_PHONE: str = ""
    TELEGRAM_SESSION_STRING: Optional[str] = None

    # Notification & Management Bot
    ADMIN_BOT_TOKEN: Optional[str] = None
    ADMIN_TELEGRAM_ID: int = 0
    BOT_USERNAME: str = "YourHelperW_bot"
    DEFAULT_STYLE: str = "adaptive_all"

    # AI & Media
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-flash-lite-latest"
    GROQ_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    ELEVENLABS_API_KEY: Optional[str] = None
    ELEVENLABS_VOICE_ID: Optional[str] = "oik5x6jBI6Xj6MPIciZU"
    VOICE_CHANCE: float = 0.05

    # Russian Speech Synthesis (Yandex SpeechKit - 100% Native Russian)
    YANDEX_API_KEY: Optional[str] = None
    YANDEX_FOLDER_ID: Optional[str] = None
    YANDEX_VOICE: str = "dasha"  # dasha (casual girl), alena (emotional), jane
    YANDEX_EMOTION: str = "good"  # good (flirt/joy), evil (sarcasm), neutral

    # CryptoBot (@CryptoBot / Crypto Pay API)
    CRYPTO_PAY_TOKEN: Optional[str] = None
    CRYPTO_PAY_NET: str = "mainnet"  # mainnet or testnet

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///companion.db"

    # Persona & Schedule Defaults
    DEFAULT_TIMEZONE: str = "Europe/Moscow"
    DEFAULT_SLEEP_START: str = "23:00"
    DEFAULT_SLEEP_END: str = "08:30"

    # Behavior & Anti-Spam
    REENGAGEMENT_MIN_HOURS: int = 24
    REENGAGEMENT_MAX_HOURS: int = 72
    AUTO_PAUSE_ON_MEETUP_PROPOSAL: bool = True
    MIN_REPLY_DELAY_SECONDS: int = 10
    MAX_REPLY_DELAY_SECONDS: int = 45


settings = Settings()
