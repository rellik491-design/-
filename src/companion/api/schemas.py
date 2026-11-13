from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class TelegramAuthRequest(BaseModel):
    init_data: str = Field(..., min_length=10, description="Signed Telegram WebApp.initData query string")


class UserProfileDTO(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    is_admin: bool = False
    start_param: Optional[str] = None


class SubscriptionSummaryDTO(BaseModel):
    is_active: bool
    plan: Optional[str] = None
    plan_name: str
    expires_at_str: Optional[str] = None
    remaining_str: str
    days_left: int = 0
    max_accounts: int = 0
    max_daily_voice: int = 0
    voice_used_today: int = 0


class AuthSessionResponse(BaseModel):
    session_token: str
    user: UserProfileDTO
    subscription: SubscriptionSummaryDTO
    bot_username: str
    ai_online: bool


class DashboardStatsDTO(BaseModel):
    period: str
    period_title: str
    total_leads: int
    active_leads: int
    paused_leads: int
    blocked_leads: int
    meetup_leads: int
    quest_leads: int
    insult_leads: int
    total_messages_in: int
    total_messages_out: int
    incoming_voice_count: int
    outgoing_voice_count: int


class ContactDTO(BaseModel):
    id: int
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    status: str
    funnel_stage: str
    detected_city: Optional[str] = None
    detected_timezone: Optional[str] = None
    origin_city: Optional[str] = None
    language: str = "ru"
    total_messages_in: int = 0
    total_messages_out: int = 0
    last_incoming_at: Optional[datetime] = None
    last_outgoing_at: Optional[datetime] = None
    ai_active: bool = True
    bot_paused: bool = False
    paused_reason: Optional[str] = None
    no_brackets: bool = False
    has_violation: bool = False
    violation_count: int = 0
    last_violation_text: Optional[str] = None
    last_violation_analysis: Optional[str] = None
    tags: Optional[str] = None
    dialogue_summary: Optional[str] = None
    dialogue_topics: Optional[str] = None
    created_at: Optional[datetime] = None


class ContactMessageDTO(BaseModel):
    id: int
    contact_id: int
    telegram_message_id: Optional[int] = None
    sender_role: str
    text: str
    is_voice: bool = False
    voice_duration: int = 0
    is_violation: bool = False
    violation_analysis: Optional[str] = None
    is_edited: bool = False
    is_deleted: bool = False
    created_at: datetime


class ContactMemoryDTO(BaseModel):
    id: int
    category: str
    key: str
    value: str
    confidence: float
    updated_at: Optional[datetime] = None


class ConversationContextInspectDTO(BaseModel):
    contact_id: int
    summary_text: str = ""
    active_topics: str = ""
    unresolved_questions: str = ""
    commitments: str = ""
    memories: List[ContactMemoryDTO] = []


class ContactTagToggleRequest(BaseModel):
    tag_code: str = Field(..., pattern=r"^(active|vip|insult|paused|blocked)$")


class ContactSendMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    mode: str = Field(default="owner_manual", pattern=r"^(owner_manual|simulate_incoming)$")


class PersonaDTO(BaseModel):
    id: int
    account_id: Optional[int] = None
    account_label: str = "Основная анкета"
    name: str
    age: int
    city: str
    district: Optional[str] = None
    birth_city: Optional[str] = None
    move_reason: Optional[str] = None
    years_in_city: Optional[str] = None
    dating_app_context: Optional[str] = None
    study_work_hobbies: Optional[str] = None
    extra_facts: Optional[str] = None
    legend: str
    timezone_name: str
    sleep_start: str
    sleep_end: str
    current_style: str


class PersonaUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    age: Optional[int] = Field(None, ge=18, le=60)
    city: Optional[str] = Field(None, min_length=1, max_length=64)
    district: Optional[str] = Field(None, max_length=64)
    birth_city: Optional[str] = Field(None, max_length=64)
    move_reason: Optional[str] = Field(None, max_length=500)
    years_in_city: Optional[str] = Field(None, max_length=64)
    dating_app_context: Optional[str] = Field(None, max_length=600)
    study_work_hobbies: Optional[str] = Field(None, max_length=800)
    extra_facts: Optional[str] = Field(None, max_length=800)
    legend: Optional[str] = Field(None, max_length=1000)
    sleep_start: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    sleep_end: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    current_style: Optional[str] = Field(None, max_length=32)


class PersonaWizardRequest(BaseModel):
    description: str = Field(..., min_length=5, max_length=2500)
    account_id: Optional[int] = None


class AccountDTO(BaseModel):
    id: int
    first_name: Optional[str] = None
    username: Optional[str] = None
    phone: Optional[str] = None
    tg_user_id: Optional[int] = None
    business_connection_id: Optional[str] = None
    auth_type: str
    is_active: bool
    status: str
    persona_id: Optional[int] = None
    persona_name: Optional[str] = None
    persona_city: Optional[str] = None
    persona_style: Optional[str] = None
    created_at: Optional[datetime] = None


class CreateInviteRequest(BaseModel):
    plan_code: str = Field(..., pattern=r"^(3d|7d|14d|31d|3_days|7_days|14_days|31_days)$")


class InviteLinkResponse(BaseModel):
    token: str
    deep_link: str
    plan: str
    expires_at: datetime


class CreateInvoiceRequest(BaseModel):
    plan_code: str = Field(..., pattern=r"^(3d|7d|14d|31d)$")


class InvoiceResponseDTO(BaseModel):
    invoice_id: str
    plan: str
    amount: float
    asset: str
    pay_url: str
    status: str


class VoiceTestRequest(BaseModel):
    style: str = Field(default="adaptive_all", max_length=32)
    custom_text: Optional[str] = Field(default=None, max_length=300)
    send_to_telegram: bool = True


class AdminGrantRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=64, description="Telegram ID or @username")
    days_or_plan: str = Field(..., min_length=1, max_length=16, description="e.g. 3d, 7d, 14d, 30, 365")
