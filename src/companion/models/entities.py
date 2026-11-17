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
    UniqueConstraint,
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
    country: Mapped[Optional[str]] = mapped_column(String(64), default="Россия", nullable=True)
    country_code: Mapped[Optional[str]] = mapped_column(String(8), default="RU", nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    timezone_name: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    timezone_source: Mapped[str] = mapped_column(String(20), default="AUTO")
    sleep_start: Mapped[str] = mapped_column(String(5), default="23:00")  # HH:MM (основной сон с 23:00)
    sleep_end: Mapped[str] = mapped_column(String(5), default="09:00")    # HH:MM (до 09:00, ~9 часов сна)
    sleep_time: Mapped[Optional[str]] = mapped_column(String(5), default="23:00", nullable=True)
    wake_time: Mapped[Optional[str]] = mapped_column(String(5), default="09:00", nullable=True)
    work_start: Mapped[Optional[str]] = mapped_column(String(5), default="10:00", nullable=True)
    work_end: Mapped[Optional[str]] = mapped_column(String(5), default="18:00", nullable=True)
    trip_city: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trip_country: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trip_country_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    trip_timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    location_extra: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    city_connections_data: Mapped[Optional[str]] = mapped_column(Text, default="[]", nullable=True)
    activity_catalog_data: Mapped[Optional[str]] = mapped_column(Text, default="[]", nullable=True)
    legend: Mapped[str] = mapped_column(
        Text,
        default="Ты общительная, милая и немного саркастичная девушка. Учишься на дизайне и любишь кофе.",
    )
    voice_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_style: Mapped[str] = mapped_column(String(32), default="adaptive_all")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="personas")

    def set_temporary_trip(
        self, trip_city: str, trip_country: Optional[str] = None, trip_timezone: Optional[str] = None
    ):
        from companion.services.location_context_service import LocationContextService
        return LocationContextService.set_temporary_trip(self, trip_city, trip_country, trip_timezone)

    def clear_temporary_trip(self):
        from companion.services.location_context_service import LocationContextService
        return LocationContextService.clear_temporary_trip(self)

    def get_city_connections(self) -> list:
        """Returns configured persona city_connections lore list."""
        if not self.city_connections_data:
            return []
        try:
            import json
            parsed = json.loads(self.city_connections_data)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    def set_city_connections(self, connections: list) -> None:
        """Stores configured persona city_connections lore as JSON."""
        import json
        self.city_connections_data = json.dumps(connections or [], ensure_ascii=False)

    def get_activity_catalog(self) -> list:
        """Returns configured city-scoped activity catalog (cinema/quests)."""
        if not self.activity_catalog_data:
            return []
        try:
            import json
            parsed = json.loads(self.activity_catalog_data)
            if isinstance(parsed, dict):
                return parsed.get("activities", [])
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    def set_activity_catalog(self, activities: Any) -> None:
        """Stores configured city-scoped activity catalog as JSON."""
        import json
        if isinstance(activities, dict) and "activities" in activities:
            activities = activities["activities"]
        self.activity_catalog_data = json.dumps(activities or [], ensure_ascii=False)


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
    quest_alarm_muted: Mapped[bool] = mapped_column(Boolean, default=False)

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

    # Daivinchik / Dating context & discovered profile
    discovered_profile_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dating_discovery_source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

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

    # Dynamic Language & Contact Deflection State
    language_preference: Mapped[Optional[str]] = mapped_column(Text, default="{}", nullable=True)
    recent_language: Mapped[Optional[str]] = mapped_column(String(16), default="ru", nullable=True)
    language_pattern: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    contact_deflection_count: Mapped[int] = mapped_column(Integer, default=0)
    last_contact_request_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Immigrant Geography & Conversation Threads State
    origin_country: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_country: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_city: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    home_country: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    home_city: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    family_location: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    temporary_location: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    geo_profile_data: Mapped[Optional[str]] = mapped_column(Text, default="{}", nullable=True)
    persona_lore_state: Mapped[Optional[str]] = mapped_column(Text, default="{}", nullable=True)
    meeting_state_data: Mapped[Optional[str]] = mapped_column(Text, default="{}", nullable=True)
    active_thread: Mapped[Optional[str]] = mapped_column(String(64), default="general", nullable=True)
    correction_events: Mapped[Optional[str]] = mapped_column(Text, default="[]", nullable=True)

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
    behavioral_preferences: Mapped[List["ContactBehavioralPreference"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    summary_record: Mapped[Optional["ConversationSummary"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan", uselist=False
    )

    def get_discovered_profile(self) -> Optional[dict]:
        """Returns parsed Daivinchik / dating profile data as dict if available."""
        if not self.discovered_profile_data:
            return None
        try:
            import json
            return json.loads(self.discovered_profile_data)
        except Exception:
            return None

    def set_discovered_profile(self, data: dict) -> None:
        """Stores structured Daivinchik / dating profile data as JSON."""
        import json
        self.discovered_profile_data = json.dumps(data, ensure_ascii=False)

    def get_geo_profile(self) -> dict:
        """Returns structured Immigrant Geography & User Context Model as dict."""
        if not self.geo_profile_data:
            return {}
        try:
            import json
            return json.loads(self.geo_profile_data)
        except Exception:
            return {}

    def set_geo_profile(self, data: Any) -> None:
        """Stores structured Immigrant Geography profile data as JSON."""
        import json
        if hasattr(data, "to_dict"):
            data = data.to_dict()
        self.geo_profile_data = json.dumps(data, ensure_ascii=False)

    def get_correction_events(self) -> list:
        """Returns list of recorded negative / correction memory events."""
        if not self.correction_events:
            return []
        try:
            import json
            return json.loads(self.correction_events)
        except Exception:
            return []

    def add_correction_event(self, correction_data: dict) -> None:
        """Appends a negative/correction memory event."""
        import json
        events = self.get_correction_events()
        events.append(correction_data)
        self.correction_events = json.dumps(events[-30:], ensure_ascii=False)

    def get_persona_lore_state(self) -> dict:
        """Returns persona lore disclosure cooldown state (separate from user memory)."""
        if not self.persona_lore_state:
            return {}
        try:
            import json
            parsed = json.loads(self.persona_lore_state)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def set_persona_lore_state(self, state: dict) -> None:
        """Stores persona lore disclosure cooldown state as JSON."""
        import json
        self.persona_lore_state = json.dumps(state or {}, ensure_ascii=False)

    def get_meeting_state(self) -> dict:
        """Returns meeting planner & owner handoff control state."""
        if not self.meeting_state_data:
            return {"meeting_control": "AUTO", "stage": "IDLE"}
        try:
            import json
            parsed = json.loads(self.meeting_state_data)
            if isinstance(parsed, dict):
                parsed.setdefault("meeting_control", "AUTO")
                parsed.setdefault("stage", "IDLE")
                return parsed
            return {"meeting_control": "AUTO", "stage": "IDLE"}
        except Exception:
            return {"meeting_control": "AUTO", "stage": "IDLE"}

    def set_meeting_state(self, state: dict) -> None:
        """Stores meeting planner & owner handoff control state as JSON."""
        import json
        self.meeting_state_data = json.dumps(state or {}, ensure_ascii=False)


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
    detected_language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    language_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stt_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    normalized_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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


class ContactBehavioralPreference(Base):
    """
    Level 3: Persistent behavioral learning memory per contact.
    Tracks learned communication preferences (length, emojis, humor, teasing, rhythm)
    with source attribution (explicit user feedback vs observed outcomes) and confidence.
    """
    __tablename__ = "contact_behavioral_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    preference_key: Mapped[str] = mapped_column(String(64), index=True)
    preference_value: Mapped[str] = mapped_column(String(256))
    confidence: Mapped[str] = mapped_column(String(16), default="medium")  # high, medium, low
    source: Mapped[str] = mapped_column(String(32), default="observed_outcome")  # explicit_user_feedback, observed_outcome
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    contact: Mapped["Contact"] = relationship(back_populates="behavioral_preferences")


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


class ProcessedMessage(Base):
    """
    Message-level Idempotency, Ownership & Self-Response Protection Table (Sections 10–13).
    Ensures:
    - Unique processing key: (chat_id, message_id)
    - Classification by sender_type: USER, BOT, OTHER
    - Outgoing registration BEFORE polling/webhook pickups
    - Hard self-response block
    """
    __tablename__ = "processed_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    message_id: Mapped[int] = mapped_column(Integer, index=True)
    sender_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sender_type: Mapped[str] = mapped_column(String(16), default="USER")  # "USER", "BOT", "OTHER"
    outgoing: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="PROCESSED")  # "RECEIVED", "PROCESSING", "PROCESSED", "IGNORED_SELF", "OUTGOING"
    received_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_chat_message"),
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


class PersonaPhoto(Base):
    """
    Persistent photo pool asset belonging to the bot's persona.
    Identified by a stable internal photo_id and verified by cryptographic SHA-256 and perceptual dHash.
    """
    __tablename__ = "persona_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    photo_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    persona_id: Mapped[Optional[int]] = mapped_column(ForeignKey("personas.id"), nullable=True, index=True)
    telegram_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # SHA-256 (content_hash)
    perceptual_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)  # dHash
    storage_path: Mapped[str] = mapped_column(String(512))  # file_path
    original_filename: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str] = mapped_column(String(32), default="image/jpeg")
    category: Mapped[str] = mapped_column(String(64), default="casual", index=True)
    tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    mood: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    contexts: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)  # is_active
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    usages: Mapped[List["ChatPhotoUsage"]] = relationship(
        back_populates="photo", cascade="all, delete-orphan"
    )

    @property
    def file_path(self) -> str:
        return self.storage_path

    @file_path.setter
    def file_path(self, val: str) -> None:
        self.storage_path = val

    @property
    def content_hash(self) -> str:
        return self.file_hash

    @content_hash.setter
    def content_hash(self, val: str) -> None:
        self.file_hash = val

    @property
    def is_active(self) -> bool:
        return self.enabled

    @is_active.setter
    def is_active(self, val: bool) -> None:
        self.enabled = bool(val)

    def get_tags(self) -> List[str]:
        try:
            import json
            return json.loads(self.tags) if self.tags else []
        except Exception:
            return []

    def set_tags(self, tags_list: List[str]) -> None:
        import json
        self.tags = json.dumps([t.strip().lower() for t in tags_list if t], ensure_ascii=False)

    def get_mood(self) -> List[str]:
        try:
            import json
            return json.loads(self.mood) if self.mood else []
        except Exception:
            return []

    def set_mood(self, mood_list: List[str]) -> None:
        import json
        self.mood = json.dumps([m.strip().lower() for m in mood_list if m], ensure_ascii=False)

    def get_contexts(self) -> List[str]:
        try:
            import json
            return json.loads(self.contexts) if self.contexts else []
        except Exception:
            return []

    def set_contexts(self, ctx_list: List[str]) -> None:
        import json
        self.contexts = json.dumps([c.strip().lower() for c in ctx_list if c], ensure_ascii=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "photo_id": self.photo_id,
            "file_path": self.storage_path,
            "storage_path": self.storage_path,
            "telegram_file_id": self.telegram_file_id,
            "content_hash": self.file_hash,
            "file_hash": self.file_hash,
            "perceptual_hash": self.perceptual_hash,
            "original_filename": self.original_filename,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "file_size": self.file_size,
            "category": self.category,
            "tags": self.get_tags(),
            "mood": self.get_mood(),
            "contexts": self.get_contexts(),
            "description": self.description,
            "enabled": self.enabled,
            "is_active": self.enabled,
            "times_used": self.times_used,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ChatPhotoUsage(Base):
    """
    Per-chat persistent usage tracking table.
    Enforces the ABSOLUTE NO-REPEAT RULE via UNIQUE(chat_id, photo_id).
    Once a photo is used or reserved in chat_id, it can NEVER be repeated in that chat.
    """
    __tablename__ = "chat_photo_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    photo_id: Mapped[str] = mapped_column(ForeignKey("persona_photos.photo_id", ondelete="CASCADE"), index=True)
    first_used_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="reserved", index=True)  # reserved, sent, send_failed, uncertain
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    selection_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    photo: Mapped["PersonaPhoto"] = relationship(back_populates="usages")

    __table_args__ = (
        UniqueConstraint("chat_id", "photo_id", name="uq_chat_photo_usage"),
    )


PhotoUsage = ChatPhotoUsage


class PhotoCooldown(Base):
    """
    Per-chat persistent 12-hour photo cooldown table (Sections 6–9, 20, 21).
    Strictly keyed by chat_id. Updated ONLY after confirmed successful Telegram delivery.
    """
    __tablename__ = "photo_cooldown"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_photo_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    last_photo_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

