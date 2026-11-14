from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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

    # Telegram Mini App & Backend HTTP API
    WEBAPP_URL: Optional[str] = None  # Public HTTPS URL where Telegram Mini App is served
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8080
    SESSION_SECRET_KEY: str = "change-me-in-production-telegram-companion-secret-key"
    INIT_DATA_MAX_AGE_SECONDS: int = 86400  # 24h validity for signed Telegram initData
    WEBHOOK_SECRET_TOKEN: Optional[str] = None
    CORS_ORIGINS: List[str] = ["*"]
    RATE_LIMIT_PER_MINUTE: int = 120
    ALLOW_DEMO_PAYMENTS: bool = True  # Set False in strict mainnet production if desired

    # AI, Context, & Media
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-flash-latest"
    GEMINI_TIMEOUT_SECONDS: float = 25.0
    GEMINI_MAX_RETRIES: int = 2
    AI_RECENT_WINDOW_SIZE: int = 10
    AI_SUMMARY_INTERVAL_MESSAGES: int = 8
    GROQ_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    ELEVENLABS_API_KEY: Optional[str] = None
    ELEVENLABS_VOICE_ID: Optional[str] = "WmNeiBsHQpj7dNcrBhAX"
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
    MESSAGE_DEBOUNCE_SECONDS: float = 1.6
    MAX_BATCH_WAIT_SECONDS: float = 5.0
    MESSAGE_READ_INTERVAL_MIN: float = 15.0
    MESSAGE_READ_INTERVAL_MAX: float = 20.0
    MAX_BURST_MESSAGES: int = 4
    INTER_MESSAGE_DELAY_MIN: float = 0.5
    INTER_MESSAGE_DELAY_MAX: float = 1.2


settings = Settings()
