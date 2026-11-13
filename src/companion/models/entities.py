from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import (
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Enum as SAEnum,
    Text,
    Date,
    Float,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from companion.models.db import Base
from companion.models.enums import ContactStatus, PlanDuration, MessageSender, AccountStatus


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    subscriptions: Mapped[List["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    personas: Mapped[List["Persona"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    contacts: Mapped[List["Contact"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    media_assets: Mapped[List["MediaAsset"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    daily_stats: Mapped[List["DailyStat"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    accounts: Mapped[List["AccountSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[PlanDuration] = mapped_column(SAEnum(PlanDuration))
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Quotas & Limits
    max_daily_voice: Mapped[int] = mapped_column(Integer, default=50)
    voice_used_today: Mapped[int] = mapped_column(Integer, default=0)
    max_accounts: Mapped[int] = mapped_column(Integer, default=1)
    last_reset_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="subscriptions")

    def is_valid(self, now: Optional[datetime] = None) -> bool:
        current_time = now or datetime.now(timezone.utc)
        # Ensure comparison is timezone-naive or matching
        if self.expires_at.tzinfo is not None and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        elif self.expires_at.tzinfo is None and current_time.tzinfo is not None:
            current_time = current_time.replace(tzinfo=None)
        return self.is_active and (self.expires_at > current_time)

    def days_remaining(self, now: Optional[datetime] = None) -> int:
        current_time = now or datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None and current_time.tzinfo is not None:
            current_time = current_time.replace(tzinfo=None)
        elif self.expires_at.tzinfo is not None and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        diff = self.expires_at - current_time
        return max(0, diff.days)


class AccountSession(Base):
    __tablename__ = "account_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    session_string: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    persona_id: Mapped[Optional[int]] = mapped_column(ForeignKey("personas.id"), nullable=True)

    # Telegram Chat Automation (Business Connection)
    business_connection_id: Mapped[Optional[str]] = mapped_column(
        String(128), unique=True, index=True, nullable=True
    )
    auth_type: Mapped[str] = mapped_column(String(32), default="business_automation")

    tg_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[AccountStatus] = mapped_column(SAEnum(AccountStatus), default=AccountStatus.CONNECTED)
    phone_code_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship(back_populates="accounts")
    persona: Mapped[Optional["Persona"]] = relationship()


class InviteToken(Base):
    __tablename__ = "invite_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[PlanDuration] = mapped_column(SAEnum(PlanDuration))
    max_accounts: Mapped[int] = mapped_column(Integer, default=1)
    max_messages: Mapped[int] = mapped_column(Integer, default=1000)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship()

class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="Алина")
    age: Mapped[int] = mapped_column(Integer, default=22)
    city: Mapped[str] = mapped_column(String(64), default="Москва")
    district: Mapped[Optional[str]] = mapped_column(String(64), default="центр", nullable=True)
    dating_app_context: Mapped[Optional[str]] = mapped_column(
        Text,
        default="была в дайвинчике буквально пару дней, вчера удалила анкету потому что надоело, перешла в тг",
        nullable=True,
    )
    study_work_hobbies: Mapped[Optional[str]] = mapped_column(
        Text,
        default="Учусь на 4 курсе графического дизайна, беру заказы на фрилансе. Обожаю выставки, атмосферные кофейни, кофе и прогулки.",
        nullable=True,
    )
    extra_facts: Mapped[Optional[str]] = mapped_column(
        Text,
        default="Люблю атмосферные места, не люблю банальные свидания в душных кафе, давно хотела сходить на крутой квест.",
        nullable=True,
    )
    birth_city: Mapped[Optional[str]] = mapped_column(String(64), default="Самара", nullable=True)
    move_reason: Mapped[Optional[str]] = mapped_column(
        Text, default="Поступила в университет на дизайн и осталась жить", nullable=True
    )
    years_in_city: Mapped[Optional[str]] = mapped_column(String(64), default="Живу тут около 4 лет", nullable=True)
    timezone_name: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    sleep_start: Mapped[str] = mapped_column(String(5), default="23:00")  # HH:MM (основной сон с 23:00)
    sleep_end: Mapped[str] = mapped_column(String(5), default="09:00")    # HH:MM (до 09:00, ~9 часов сна)
    legend: Mapped[str] = mapped_column(
        Text,
        default="Ты общительная, милая и немного саркастичная девушка. Учишься на дизайне и любишь кофе.",
    )
    voice_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_style: Mapped[str] = mapped_column(String(32), default="adaptive_all")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="personas")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    tg_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    status: Mapped[ContactStatus] = mapped_column(
        SAEnum(ContactStatus), default=ContactStatus.ACTIVE, index=True
    )
    funnel_stage: Mapped[str] = mapped_column(String(32), default="chatting")
    # Anti-Spam: strict check that bot only interacts if user initiated contact or is recognized
    is_initiated_by_user: Mapped[bool] = mapped_column(Boolean, default=True)

    detected_city: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    detected_timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    origin_city: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    client_free_time: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="ru")

    total_messages_in: Mapped[int] = mapped_column(Integer, default=0)
    total_messages_out: Mapped[int] = mapped_column(Integer, default=0)
    last_incoming_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_outgoing_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Control takeover & AI state
    ai_active: Mapped[bool] = mapped_column(Boolean, default=True)
    bot_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lead_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lead_detected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Anti-Spam alert flags: ensure each alert is sent at most once
    alert_date_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_quest_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    # Dynamic conversation style preferences per contact
    no_brackets: Mapped[bool] = mapped_column(Boolean, default=False)
    preferred_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    communication_style: Mapped[Optional[str]] = mapped_column(String(32), default="casual", nullable=True)
    preferred_response_length: Mapped[Optional[str]] = mapped_column(String(32), default="medium", nullable=True)
    emoji_frequency: Mapped[Optional[str]] = mapped_column(String(32), default="medium", nullable=True)
    likes_multi_message_responses: Mapped[Optional[bool]] = mapped_column(Boolean, default=True, nullable=True)

    # Contextual re-engagement & sleep resumption
    scheduled_reengage_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    reengage_context: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pending_overnight_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Lead moderation, insults & tags management
    violation_count: Mapped[int] = mapped_column(Integer, default=0)
    has_violation: Mapped[bool] = mapped_column(Boolean, default=False)
    last_violation_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_violation_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_violation_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_voice_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(String(256), default="🟢 Активный", nullable=True)
    dialogue_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dialogue_topics: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship(back_populates="contacts")
    sent_media: Mapped[List["SentMediaHistory"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    messages: Mapped[List["ContactMessage"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan", order_by="ContactMessage.id"
    )
    memories: Mapped[List["ContactMemory"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    summary_record: Mapped[Optional["ConversationSummary"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan", uselist=False
    )


class ContactMessage(Base):
    __tablename__ = "contact_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    reply_to_msg_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sender_role: Mapped[str] = mapped_column(String(16))  # "user" (мужчина) or "bot" / "model" (девушка)
    media_type: Mapped[str] = mapped_column(String(24), default="text")  # text, voice, video_note, photo, sticker, animation
    text: Mapped[str] = mapped_column(Text)
    is_voice: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    voice_duration: Mapped[int] = mapped_column(Integer, default=0)
    processing_status: Mapped[str] = mapped_column(
        String(24), default="PROCESSED", index=True
    )  # NEW, BATCHED, PROCESSING, PROCESSED, FAILED
    batch_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    is_violation: Mapped[bool] = mapped_column(Boolean, default=False)
    violation_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    contact: Mapped["Contact"] = relationship(back_populates="messages")


class ContactMemory(Base):
    """
    Structured long-term memory per contact.
    Stores only genuine persistent facts, preferences, habits, agreements, and goals (no memory spam).
    """
    __tablename__ = "contact_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)  # fact, preference, habit, agreement, goal, relationship
    key: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.9)
    source_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    contact: Mapped["Contact"] = relationship(back_populates="memories")


class ConversationSummary(Base):
    """
    Rolling conversation-level memory per contact.
    Tracks what has already been discussed, active topics, unresolved questions, and commitments.
    """
    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), unique=True, index=True)
    summary_text: Mapped[str] = mapped_column(Text, default="")
    active_topics: Mapped[str] = mapped_column(String(512), default="")
    unresolved_questions: Mapped[str] = mapped_column(Text, default="")
    commitments: Mapped[str] = mapped_column(Text, default="")
    messages_covered_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    contact: Mapped["Contact"] = relationship(back_populates="summary_record")


class ProcessedUpdate(Base):
    """
    Idempotency & deduplication record for Telegram polling/webhook updates and Business Chat messages.
    Prevents duplicate AI responses and race condition double-processing.
    """
    __tablename__ = "processed_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    update_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    update_type: Mapped[str] = mapped_column(String(32), default="business_message")
    chat_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    file_path: Mapped[str] = mapped_column(String(256))
    tag: Mapped[str] = mapped_column(String(64), index=True)  # "home", "cafe", "walk", "car"
    description: Mapped[str] = mapped_column(String(256))
    times_sent: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship(back_populates="media_assets")
    sent_history: Mapped[List["SentMediaHistory"]] = relationship(
        back_populates="media_asset", cascade="all, delete-orphan"
    )


class SentMediaHistory(Base):
    __tablename__ = "sent_media_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    media_asset_id: Mapped[int] = mapped_column(ForeignKey("media_assets.id"), index=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    contact: Mapped["Contact"] = relationship(back_populates="sent_media")
    media_asset: Mapped["MediaAsset"] = relationship(back_populates="sent_history")


class DailyStat(Base):
    __tablename__ = "daily_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    stat_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    messages_in: Mapped[int] = mapped_column(Integer, default=0)
    messages_out: Mapped[int] = mapped_column(Integer, default=0)
    voice_sent: Mapped[int] = mapped_column(Integer, default=0)
    photos_sent: Mapped[int] = mapped_column(Integer, default=0)
    leads_count: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship(back_populates="daily_stats")


class PaymentInvoice(Base):
    __tablename__ = "payment_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[PlanDuration] = mapped_column(SAEnum(PlanDuration))
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    asset: Mapped[str] = mapped_column(String(16), default="USDT")
    pay_url: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)  # active, paid, expired
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()


class DaivinchikProfile(Base):
    """
    Stores parsed Daivinchik forwarded dating profiles and their generated bios.
    Strictly separates factual source data from synthetic non-sensitive assumptions.
    Maintains internal persona consistency across multiple regenerations.
    """
    __tablename__ = "daivinchik_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    profile_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_text: Mapped[str] = mapped_column(Text)

    # Extracted FACTS (explicitly from profile)
    name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    facts_json: Mapped[str] = mapped_column(Text, default="{}")

    # Generated NON-SENSITIVE persona assumptions (for consistent regenerations)
    generated_details_json: Mapped[str] = mapped_column(Text, default="{}")

    # Current generated bio & active style variant
    current_bio: Mapped[str] = mapped_column(Text, default="")
    style_variant: Mapped[str] = mapped_column(String(32), default="natural")
    bio_history_json: Mapped[str] = mapped_column(Text, default="[]")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship()

