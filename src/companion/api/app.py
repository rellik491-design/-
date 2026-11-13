from datetime import datetime, timezone
import hmac
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from companion.ai.gemini_client import GeminiClient
from companion.ai.memory_manager import MemoryManager
from companion.api.auth import (
    create_session_token,
    ensure_user_from_telegram_data,
    get_current_user,
    require_admin,
    validate_telegram_init_data,
)
from companion.api.middleware import (
    RateLimitAndCorrelationMiddleware,
    register_exception_handlers,
)
from companion.api.schemas import (
    AccountDTO,
    AdminGrantRequest,
    AuthSessionResponse,
    ContactDTO,
    ContactMemoryDTO,
    ContactMessageDTO,
    ContactSendMessageRequest,
    ContactTagToggleRequest,
    ConversationContextInspectDTO,
    CreateInvoiceRequest,
    CreateInviteRequest,
    DashboardStatsDTO,
    InviteLinkResponse,
    InvoiceResponseDTO,
    PersonaDTO,
    PersonaUpdateRequest,
    PersonaWizardRequest,
    SubscriptionSummaryDTO,
    TelegramAuthRequest,
    UserProfileDTO,
    VoiceTestRequest,
)
from companion.bot.admin_notifier import AdminNotifier
from companion.bot.business_bot import BusinessChatBot
from companion.bot.userbot_handler import UserbotHandler
from companion.config import settings
from companion.models.db import get_db, init_db
from companion.models.entities import (
    AccountSession,
    Contact,
    ContactMemory,
    ContactMessage,
    ConversationSummary,
    InviteToken,
    PaymentInvoice,
    Persona,
    Subscription,
    User,
)
from companion.models.enums import AccountStatus, ContactStatus, PlanDuration
from companion.services.account_service import AccountService
from companion.services.ai_service import AIService
from companion.services.crypto_pay_service import CryptoPayService
from companion.services.handover_service import HandoverService
from companion.services.invite_service import InviteService
from companion.services.stats_service import StatsService
from companion.services.styles_service import DialogueStyleEngine, StyleType
from companion.services.subscription_service import SubscriptionService
from companion.services.timezone_service import TimezoneService
from companion.services.voice_service import VoiceService

logger = logging.getLogger(__name__)


def _map_plan_code(plan_code: str) -> PlanDuration:
    mapping = {
        "3d": PlanDuration.DAYS_3,
        "3_days": PlanDuration.DAYS_3,
        "7d": PlanDuration.DAYS_7,
        "7_days": PlanDuration.DAYS_7,
        "14d": PlanDuration.DAYS_14,
        "14_days": PlanDuration.DAYS_14,
        "31d": PlanDuration.DAYS_31,
        "31_days": PlanDuration.DAYS_31,
    }
    if plan_code not in mapping:
        raise HTTPException(status_code=400, detail=f"Неизвестный тариф: {plan_code}")
    return mapping[plan_code]


async def _get_authorized_contact(
    session: AsyncSession,
    contact_id: int,
    user: User,
) -> Contact:
    """Strict tenant isolation check to prevent IDOR across users' contacts."""
    stmt = select(Contact).where(Contact.id == contact_id)
    contact = (await session.execute(stmt)).scalars().first()
    if not contact:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    is_admin = bool(settings.ADMIN_TELEGRAM_ID and user.telegram_id == settings.ADMIN_TELEGRAM_ID)
    if contact.user_id != user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")
    return contact


def _contact_to_dto(c: Contact) -> ContactDTO:
    status_val = c.status.value if hasattr(c.status, "value") else str(c.status)
    return ContactDTO(
        id=c.id,
        tg_id=c.tg_id,
        username=c.username,
        first_name=c.first_name,
        status=status_val,
        funnel_stage=c.funnel_stage or "chatting",
        detected_city=c.detected_city,
        detected_timezone=c.detected_timezone,
        origin_city=c.origin_city,
        language=c.language or "ru",
        total_messages_in=c.total_messages_in or 0,
        total_messages_out=c.total_messages_out or 0,
        last_incoming_at=c.last_incoming_at,
        last_outgoing_at=c.last_outgoing_at,
        ai_active=bool(getattr(c, "ai_active", True)),
        bot_paused=bool(c.bot_paused),
        paused_reason=c.paused_reason,
        no_brackets=bool(c.no_brackets),
        has_violation=bool(c.has_violation),
        violation_count=c.violation_count or 0,
        last_violation_text=c.last_violation_text,
        last_violation_analysis=c.last_violation_analysis,
        tags=c.tags or "🟢 Активный",
        dialogue_summary=c.dialogue_summary,
        dialogue_topics=c.dialogue_topics,
        created_at=c.created_at,
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="Telegram Companion AI — Mini App Backend API",
        version="2.0.0",
        description="Production Backend API for the Telegram Companion AI Mini App & Bot Engine",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitAndCorrelationMiddleware)
    register_exception_handlers(app)

    # ==========================================================
    # 1. HEALTH & READINESS CHECKS (Phase 23)
    # ==========================================================
    @app.get("/health", tags=["Health"])
    async def health_check() -> Dict[str, Any]:
        return {
            "status": "ok",
            "service": "telegram-companion-ai",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/ready", tags=["Health"])
    async def readiness_check(session: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
        await session.execute(sa_text("SELECT 1"))
        return {
            "status": "ready",
            "database": "connected",
            "ai_configured": bool(GeminiClient.get_api_key()),
            "bot_configured": bool(settings.ADMIN_BOT_TOKEN),
        }

    # ==========================================================
    # 2. TELEGRAM MINI APP AUTHENTICATION (Phase 4 & 16)
    # ==========================================================
    @app.post("/api/v1/auth/telegram", response_model=AuthSessionResponse, tags=["Auth"])
    async def authenticate_telegram_webapp(
        body: TelegramAuthRequest,
        session: AsyncSession = Depends(get_db),
    ) -> AuthSessionResponse:
        validated = validate_telegram_init_data(body.init_data)
        tg_user = validated["user"]
        start_param = validated.get("start_param")

        user = await ensure_user_from_telegram_data(session, tg_user)
        is_admin = bool(settings.ADMIN_TELEGRAM_ID and user.telegram_id == settings.ADMIN_TELEGRAM_ID)

        # Auto-grant admin 365-day subscription if none active
        sub_info = await SubscriptionService.get_subscription_summary(session, user.id)
        if is_admin and not sub_info["is_active"]:
            await SubscriptionService.grant_custom_days(session, user.id, days=365, plan=PlanDuration.DAYS_31)
            sub_info = await SubscriptionService.get_subscription_summary(session, user.id)

        # If opened with deep-link invite token (`start_param`), verify and activate
        if start_param and len(start_param) >= 8:
            invite = await InviteService.verify_invite(session, start_param)
            if invite and not sub_info["is_active"]:
                await SubscriptionService.activate_plan(session, user.id, invite.plan)
                sub_info = await SubscriptionService.get_subscription_summary(session, user.id)

        token = create_session_token(
            telegram_id=user.telegram_id,
            db_user_id=user.id,
            username=user.username,
            is_admin=is_admin,
        )

        return AuthSessionResponse(
            session_token=token,
            user=UserProfileDTO(
                id=user.id,
                telegram_id=user.telegram_id,
                username=user.username,
                first_name=tg_user.get("first_name"),
                is_admin=is_admin,
                start_param=start_param,
            ),
            subscription=SubscriptionSummaryDTO(**sub_info),
            bot_username=settings.BOT_USERNAME,
            ai_online=bool(GeminiClient.get_api_key()),
        )

    @app.get("/api/v1/auth/me", response_model=AuthSessionResponse, tags=["Auth"])
    async def get_me(
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> AuthSessionResponse:
        is_admin = bool(settings.ADMIN_TELEGRAM_ID and user.telegram_id == settings.ADMIN_TELEGRAM_ID)
        sub_info = await SubscriptionService.get_subscription_summary(session, user.id)
        token = create_session_token(
            telegram_id=user.telegram_id,
            db_user_id=user.id,
            username=user.username,
            is_admin=is_admin,
        )
        return AuthSessionResponse(
            session_token=token,
            user=UserProfileDTO(
                id=user.id,
                telegram_id=user.telegram_id,
                username=user.username,
                is_admin=is_admin,
            ),
            subscription=SubscriptionSummaryDTO(**sub_info),
            bot_username=settings.BOT_USERNAME,
            ai_online=bool(GeminiClient.get_api_key()),
        )

    # ==========================================================
    # 3. DASHBOARD & ANALYTICS (Phase 6)
    # ==========================================================
    @app.get("/api/v1/dashboard/stats", response_model=DashboardStatsDTO, tags=["Dashboard"])
    async def get_dashboard_stats(
        period: str = Query("today", pattern=r"^(today|yesterday|7d|30d|all)$"),
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> DashboardStatsDTO:
        stats = await StatsService.get_detailed_leads_stats(session, user.id, period=period)
        contacts = await StatsService.get_chat_leads(session, user.id)
        paused_cnt = sum(1 for c in contacts if c.bot_paused)
        meetup_cnt = sum(
            1
            for c in contacts
            if c.status == ContactStatus.LEAD_MEETING or c.funnel_stage in ("date_proposed", "quest_agreed")
        )
        quest_cnt = sum(1 for c in contacts if c.funnel_stage == "quest_agreed")
        total_in = sum((c.total_messages_in or 0) for c in contacts)
        total_out = sum((c.total_messages_out or 0) for c in contacts)

        return DashboardStatsDTO(
            period=stats["period"],
            period_title=stats.get("period_title") or stats.get("period_label", "За период"),
            total_leads=stats["total_leads"],
            active_leads=stats.get("active_leads", stats.get("active_dialogues", 0)),
            paused_leads=stats.get("paused_leads", paused_cnt),
            blocked_leads=stats.get("blocked_leads", stats.get("blocked_clients", 0)),
            meetup_leads=stats.get("meetup_leads", meetup_cnt),
            quest_leads=stats.get("quest_leads", quest_cnt),
            insult_leads=stats["insult_leads"],
            total_messages_in=stats.get("total_messages_in", total_in),
            total_messages_out=stats.get("total_messages_out", total_out),
            incoming_voice_count=stats["incoming_voice_count"],
            outgoing_voice_count=stats.get("outgoing_voice_count", 0),
        )

    @app.get("/api/v1/dashboard/export-csv", tags=["Dashboard"])
    async def export_leads_csv(
        period: str = Query("all", pattern=r"^(today|yesterday|7d|30d|all)$"),
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> Response:
        csv_bytes = await StatsService.generate_leads_csv(session, user.id, period=period)
        filename = f"leads_stats_{period}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ==========================================================
    # 4. CONTACTS, CONVERSATIONS, & AI MEMORY INSPECTOR
    # ==========================================================
    @app.get("/api/v1/contacts", response_model=List[ContactDTO], tags=["Contacts"])
    async def list_contacts(
        filter_by: str = Query("all", alias="filter", pattern=r"^(all|hot|insults|active|paused|blocked)$"),
        q: Optional[str] = Query(None, max_length=64),
        limit: int = Query(50, ge=1, le=200),
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> List[ContactDTO]:
        stmt = select(Contact).where(Contact.user_id == user.id)

        if filter_by == "hot":
            stmt = stmt.where(
                (Contact.status == ContactStatus.LEAD_MEETING)
                | (Contact.funnel_stage.in_(["date_proposed", "quest_agreed"]))
            )
        elif filter_by == "insults":
            stmt = stmt.where(Contact.has_violation == True)
        elif filter_by == "active":
            stmt = stmt.where(Contact.status == ContactStatus.ACTIVE, Contact.bot_paused == False)
        elif filter_by == "paused":
            stmt = stmt.where(Contact.bot_paused == True)
        elif filter_by == "blocked":
            stmt = stmt.where(Contact.status == ContactStatus.BLOCKED)

        stmt = stmt.order_by(desc(Contact.last_incoming_at), desc(Contact.id)).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

        if q and q.strip():
            q_low = q.strip().lower()
            rows = [
                c
                for c in rows
                if q_low in (c.first_name or "").lower()
                or q_low in (c.username or "").lower()
                or q_low in (c.detected_city or "").lower()
                or q_low in str(c.tg_id)
            ]

        return [_contact_to_dto(c) for c in rows]

    @app.get("/api/v1/contacts/{contact_id}", response_model=ContactDTO, tags=["Contacts"])
    async def get_contact_detail(
        contact_id: int,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> ContactDTO:
        contact = await _get_authorized_contact(session, contact_id, user)
        return _contact_to_dto(contact)

    @app.get("/api/v1/contacts/{contact_id}/messages", response_model=List[ContactMessageDTO], tags=["Contacts"])
    async def get_contact_messages(
        contact_id: int,
        limit: int = Query(50, ge=1, le=200),
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> List[ContactMessageDTO]:
        contact = await _get_authorized_contact(session, contact_id, user)
        stmt = (
            select(ContactMessage)
            .where(ContactMessage.contact_id == contact.id)
            .order_by(desc(ContactMessage.id))
            .limit(limit)
        )
        rows = list(reversed((await session.execute(stmt)).scalars().all()))
        return [
            ContactMessageDTO(
                id=m.id,
                contact_id=m.contact_id,
                telegram_message_id=m.telegram_message_id,
                sender_role=m.sender_role,
                text=m.text,
                is_voice=bool(m.is_voice),
                voice_duration=m.voice_duration or 0,
                is_violation=bool(m.is_violation),
                violation_analysis=m.violation_analysis,
                is_edited=bool(getattr(m, "is_edited", False)),
                is_deleted=bool(getattr(m, "is_deleted", False)),
                created_at=m.created_at,
            )
            for m in rows
        ]

    @app.get("/api/v1/contacts/{contact_id}/context", response_model=ConversationContextInspectDTO, tags=["Contacts"])
    async def get_contact_ai_context(
        contact_id: int,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> ConversationContextInspectDTO:
        contact = await _get_authorized_contact(session, contact_id, user)
        stmt_sum = select(ConversationSummary).where(ConversationSummary.contact_id == contact.id)
        sum_rec = (await session.execute(stmt_sum)).scalars().first()
        memories = await MemoryManager.get_long_term_memories(session, contact.id)

        return ConversationContextInspectDTO(
            contact_id=contact.id,
            summary_text=(sum_rec.summary_text if sum_rec else None) or contact.dialogue_summary or "",
            active_topics=(sum_rec.active_topics if sum_rec else None) or contact.dialogue_topics or "",
            unresolved_questions=sum_rec.unresolved_questions if sum_rec else "",
            commitments=sum_rec.commitments if sum_rec else "",
            memories=[
                ContactMemoryDTO(
                    id=mem.id,
                    category=mem.category,
                    key=mem.key,
                    value=mem.value,
                    confidence=mem.confidence,
                    updated_at=mem.updated_at,
                )
                for mem in memories
            ],
        )

    @app.post("/api/v1/contacts/{contact_id}/pause", response_model=ContactDTO, tags=["Contacts"])
    async def pause_contact(
        contact_id: int,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> ContactDTO:
        contact = await _get_authorized_contact(session, contact_id, user)
        AdminNotifier.stop_quest_alarm(contact.id)
        contact.bot_paused = True
        contact.ai_active = False
        contact.paused_reason = "paused_from_mini_app"
        contact.status = ContactStatus.PAUSED_MANUAL
        await session.commit()
        await session.refresh(contact)
        return _contact_to_dto(contact)

    @app.post("/api/v1/contacts/{contact_id}/resume", response_model=ContactDTO, tags=["Contacts"])
    async def resume_contact(
        contact_id: int,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> ContactDTO:
        contact = await _get_authorized_contact(session, contact_id, user)
        AdminNotifier.stop_quest_alarm(contact.id)
        contact.bot_paused = False
        contact.ai_active = True
        contact.paused_reason = None
        contact.status = ContactStatus.ACTIVE
        await session.commit()
        await session.refresh(contact)
        return _contact_to_dto(contact)

    @app.post("/api/v1/contacts/{contact_id}/block", response_model=ContactDTO, tags=["Contacts"])
    async def block_contact(
        contact_id: int,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> ContactDTO:
        contact = await _get_authorized_contact(session, contact_id, user)
        AdminNotifier.stop_quest_alarm(contact.id)
        contact.status = ContactStatus.BLOCKED
        contact.bot_paused = True
        contact.ai_active = False
        contact.paused_reason = "blocked_by_owner"
        tags_list = [t.strip() for t in (contact.tags or "").split(",") if t.strip() and "Активный" not in t]
        if "⛔️ Заблокирован" not in tags_list:
            tags_list.append("⛔️ Заблокирован")
        contact.tags = ", ".join(tags_list)
        await session.commit()
        await session.refresh(contact)
        return _contact_to_dto(contact)

    @app.post("/api/v1/contacts/{contact_id}/tags", response_model=ContactDTO, tags=["Contacts"])
    async def toggle_contact_tag(
        contact_id: int,
        body: ContactTagToggleRequest,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> ContactDTO:
        contact = await _get_authorized_contact(session, contact_id, user)
        tag_map = {
            "active": "🟢 Активный",
            "vip": "⭐️ Приоритетный",
            "insult": "🔴 Оскорбления",
            "paused": "⏸ На паузе",
            "blocked": "⛔️ Заблокирован",
        }
        target_label = tag_map[body.tag_code]
        current_tags = [t.strip() for t in (contact.tags or "").split(",") if t.strip()]
        if target_label in current_tags:
            current_tags.remove(target_label)
        else:
            current_tags.append(target_label)
        contact.tags = ", ".join(current_tags) if current_tags else "🟢 Активный"
        await session.commit()
        await session.refresh(contact)
        return _contact_to_dto(contact)

    @app.post("/api/v1/contacts/{contact_id}/send", tags=["Contacts"])
    async def send_or_simulate_contact_message(
        contact_id: int,
        body: ContactSendMessageRequest,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> Dict[str, Any]:
        contact = await _get_authorized_contact(session, contact_id, user)
        now = datetime.now(timezone.utc)

        if body.mode == "simulate_incoming":
            res = await UserbotHandler.process_incoming_message(
                session=session,
                user_id=contact.user_id,
                sender_tg_id=contact.tg_id,
                sender_username=contact.username,
                sender_first_name=contact.first_name,
                text=body.text,
                now=now,
                auto_handover_on_date=False,
            )
            return {
                "status": "ok",
                "action": res.get("action"),
                "reply_text": res.get("reply_text"),
                "contact": _contact_to_dto(contact),
            }

        # mode == "owner_manual": owner sends message to interlocutor & pauses bot for 15m
        await HandoverService.handle_manual_owner_message(session, contact, body.text)
        out_msg = ContactMessage(
            contact_id=contact.id,
            sender_role="bot",
            text=body.text,
            is_voice=False,
            created_at=now,
        )
        session.add(out_msg)
        contact.total_messages_out = (contact.total_messages_out or 0) + 1
        contact.last_outgoing_at = now
        await session.commit()

        # Deliver via active Telegram Business Connection if connected
        stmt_acc = select(AccountSession).where(
            AccountSession.user_id == contact.user_id,
            AccountSession.is_active == True,
            AccountSession.business_connection_id.isnot(None),
        )
        acc = (await session.execute(stmt_acc)).scalars().first()
        delivered = False
        if acc and acc.business_connection_id and settings.ADMIN_BOT_TOKEN:
            delivered = await BusinessChatBot.send_business_message(
                bot_token=settings.ADMIN_BOT_TOKEN,
                business_connection_id=acc.business_connection_id,
                chat_id=contact.tg_id,
                text=body.text,
            )

        return {
            "status": "ok",
            "delivered_to_telegram": delivered,
            "contact": _contact_to_dto(contact),
        }

    @app.delete("/api/v1/contacts/{contact_id}", tags=["Contacts"])
    async def delete_contact(
        contact_id: int,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> Dict[str, Any]:
        contact = await _get_authorized_contact(session, contact_id, user)
        AdminNotifier.stop_quest_alarm(contact.id)
        await session.delete(contact)
        await session.commit()
        return {"status": "deleted", "id": contact_id}

    # ==========================================================
    # 5. PERSONAS, LEGEND STUDIO, & ADAPTIVE STYLES
    # ==========================================================
    @app.get("/api/v1/personas", response_model=List[PersonaDTO], tags=["Personas"])
    async def list_personas(
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> List[PersonaDTO]:
        accounts = await AccountService.get_user_accounts(session, user.id)
        result: List[PersonaDTO] = []
        seen_persona_ids = set()

        for acc in accounts:
            p = await AccountService.ensure_account_persona(session, acc)
            seen_persona_ids.add(p.id)
            label = f"{acc.first_name or 'Аккаунт'} ({'@' + acc.username if acc.username else acc.phone or f'ID {acc.id}'})"
            result.append(
                PersonaDTO(
                    id=p.id,
                    account_id=acc.id,
                    account_label=label,
                    name=p.name,
                    age=p.age,
                    city=p.city,
                    district=p.district,
                    birth_city=p.birth_city,
                    move_reason=p.move_reason,
                    years_in_city=p.years_in_city,
                    dating_app_context=p.dating_app_context,
                    study_work_hobbies=p.study_work_hobbies,
                    extra_facts=p.extra_facts,
                    legend=p.legend,
                    timezone_name=p.timezone_name,
                    sleep_start=p.sleep_start,
                    sleep_end=p.sleep_end,
                    current_style=p.current_style or "adaptive_all",
                )
            )

        stmt_default = select(Persona).where(Persona.user_id == user.id).order_by(Persona.id.asc())
        all_personas = (await session.execute(stmt_default)).scalars().all()
        for p in all_personas:
            if p.id not in seen_persona_ids:
                result.append(
                    PersonaDTO(
                        id=p.id,
                        account_id=None,
                        account_label="Основная анкета (по умолчанию)",
                        name=p.name,
                        age=p.age,
                        city=p.city,
                        district=p.district,
                        birth_city=p.birth_city,
                        move_reason=p.move_reason,
                        years_in_city=p.years_in_city,
                        dating_app_context=p.dating_app_context,
                        study_work_hobbies=p.study_work_hobbies,
                        extra_facts=p.extra_facts,
                        legend=p.legend,
                        timezone_name=p.timezone_name,
                        sleep_start=p.sleep_start,
                        sleep_end=p.sleep_end,
                        current_style=p.current_style or "adaptive_all",
                    )
                )
                break

        return result

    @app.put("/api/v1/personas/{persona_id}", response_model=PersonaDTO, tags=["Personas"])
    async def update_persona(
        persona_id: int,
        body: PersonaUpdateRequest,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> PersonaDTO:
        stmt = select(Persona).where(Persona.id == persona_id, Persona.user_id == user.id)
        persona = (await session.execute(stmt)).scalars().first()
        if not persona:
            raise HTTPException(status_code=404, detail="Анкета не найдена")

        update_data = body.model_dump(exclude_unset=True)
        for field, val in update_data.items():
            if val is not None:
                setattr(persona, field, val)

        if "city" in update_data and persona.city:
            resolved_tz = TimezoneService.resolve_timezone_for_city(persona.city)
            if resolved_tz:
                persona.timezone_name = resolved_tz

        await session.commit()
        await session.refresh(persona)

        return PersonaDTO(
            id=persona.id,
            account_id=None,
            account_label=f"Анкета: {persona.name}",
            name=persona.name,
            age=persona.age,
            city=persona.city,
            district=persona.district,
            birth_city=persona.birth_city,
            move_reason=persona.move_reason,
            years_in_city=persona.years_in_city,
            dating_app_context=persona.dating_app_context,
            study_work_hobbies=persona.study_work_hobbies,
            extra_facts=persona.extra_facts,
            legend=persona.legend,
            timezone_name=persona.timezone_name,
            sleep_start=persona.sleep_start,
            sleep_end=persona.sleep_end,
            current_style=persona.current_style or "adaptive_all",
        )

    @app.post("/api/v1/personas/wizard", response_model=PersonaDTO, tags=["Personas"])
    async def run_persona_ai_wizard(
        body: PersonaWizardRequest,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> PersonaDTO:
        parsed = await AIService.parse_legend_wizard(body.description)

        persona: Optional[Persona] = None
        if body.account_id:
            stmt_acc = select(AccountSession).where(
                AccountSession.id == body.account_id,
                AccountSession.user_id == user.id,
            )
            acc = (await session.execute(stmt_acc)).scalars().first()
            if acc:
                persona = await AccountService.ensure_account_persona(session, acc)

        if not persona:
            stmt_p = select(Persona).where(Persona.user_id == user.id).order_by(Persona.id.asc())
            persona = (await session.execute(stmt_p)).scalars().first()
            if not persona:
                persona = Persona(user_id=user.id)
                session.add(persona)

        persona.name = parsed.get("name") or persona.name
        persona.age = int(parsed.get("age") or persona.age)
        persona.city = parsed.get("city") or persona.city
        persona.district = parsed.get("district") or persona.district
        persona.birth_city = parsed.get("birth_city") or persona.birth_city
        persona.move_reason = parsed.get("move_reason") or persona.move_reason
        persona.years_in_city = parsed.get("years_in_city") or persona.years_in_city
        persona.dating_app_context = parsed.get("dating_app_context") or persona.dating_app_context
        persona.study_work_hobbies = parsed.get("study_work_hobbies") or persona.study_work_hobbies
        persona.extra_facts = parsed.get("extra_facts") or persona.extra_facts
        persona.legend = parsed.get("legend") or persona.legend

        resolved_tz = TimezoneService.resolve_timezone_for_city(persona.city)
        if resolved_tz:
            persona.timezone_name = resolved_tz

        await session.commit()
        await session.refresh(persona)

        return PersonaDTO(
            id=persona.id,
            account_id=body.account_id,
            account_label=f"Анкета: {persona.name}",
            name=persona.name,
            age=persona.age,
            city=persona.city,
            district=persona.district,
            birth_city=persona.birth_city,
            move_reason=persona.move_reason,
            years_in_city=persona.years_in_city,
            dating_app_context=persona.dating_app_context,
            study_work_hobbies=persona.study_work_hobbies,
            extra_facts=persona.extra_facts,
            legend=persona.legend,
            timezone_name=persona.timezone_name,
            sleep_start=persona.sleep_start,
            sleep_end=persona.sleep_end,
            current_style=persona.current_style or "adaptive_all",
        )

    # ==========================================================
    # 6. ACCOUNTS & INVITE LINKS
    # ==========================================================
    @app.get("/api/v1/accounts", response_model=List[AccountDTO], tags=["Accounts"])
    async def list_accounts(
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> List[AccountDTO]:
        accounts = await AccountService.get_user_accounts(session, user.id)
        result: List[AccountDTO] = []
        for acc in accounts:
            p = await AccountService.ensure_account_persona(session, acc)
            status_val = acc.status.value if hasattr(acc.status, "value") else str(acc.status)
            result.append(
                AccountDTO(
                    id=acc.id,
                    first_name=acc.first_name,
                    username=acc.username,
                    phone=acc.phone,
                    tg_user_id=acc.tg_user_id,
                    business_connection_id=acc.business_connection_id,
                    auth_type=acc.auth_type,
                    is_active=bool(acc.is_active),
                    status=status_val,
                    persona_id=p.id if p else None,
                    persona_name=p.name if p else None,
                    persona_city=p.city if p else None,
                    persona_style=p.current_style if p else "adaptive_all",
                    created_at=acc.created_at,
                )
            )
        return result

    @app.post("/api/v1/accounts/{account_id}/toggle", response_model=AccountDTO, tags=["Accounts"])
    async def toggle_account_status(
        account_id: int,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> AccountDTO:
        stmt = select(AccountSession).where(
            AccountSession.id == account_id,
            AccountSession.user_id == user.id,
        )
        acc = (await session.execute(stmt)).scalars().first()
        if not acc:
            raise HTTPException(status_code=404, detail="Аккаунт не найден")

        updated = await AccountService.toggle_account(session, acc.id, is_active=not acc.is_active)
        p = await AccountService.ensure_account_persona(session, updated)
        status_val = updated.status.value if hasattr(updated.status, "value") else str(updated.status)
        return AccountDTO(
            id=updated.id,
            first_name=updated.first_name,
            username=updated.username,
            phone=updated.phone,
            tg_user_id=updated.tg_user_id,
            business_connection_id=updated.business_connection_id,
            auth_type=updated.auth_type,
            is_active=bool(updated.is_active),
            status=status_val,
            persona_id=p.id if p else None,
            persona_name=p.name if p else None,
            persona_city=p.city if p else None,
            persona_style=p.current_style if p else "adaptive_all",
            created_at=updated.created_at,
        )

    @app.delete("/api/v1/accounts/{account_id}", tags=["Accounts"])
    async def delete_account_session(
        account_id: int,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> Dict[str, Any]:
        stmt = select(AccountSession).where(
            AccountSession.id == account_id,
            AccountSession.user_id == user.id,
        )
        acc = (await session.execute(stmt)).scalars().first()
        if not acc:
            raise HTTPException(status_code=404, detail="Аккаунт не найден")
        await AccountService.delete_account(session, acc.id)
        return {"status": "deleted", "id": account_id}

    @app.post("/api/v1/invites", response_model=InviteLinkResponse, tags=["Accounts"])
    async def create_invite_link(
        body: CreateInviteRequest,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> InviteLinkResponse:
        target_plan = _map_plan_code(body.plan_code)
        is_admin = bool(settings.ADMIN_TELEGRAM_ID and user.telegram_id == settings.ADMIN_TELEGRAM_ID)

        if not is_admin:
            active_sub = await SubscriptionService.get_active_subscription(session, user.id)
            if not active_sub:
                raise HTTPException(status_code=403, detail="У вас нет активной подписки для создания ссылки")
            if active_sub.plan != target_plan:
                raise HTTPException(
                    status_code=403,
                    detail="Вы можете создавать ссылки только в рамках вашего активного тарифа",
                )

        link, invite = await InviteService.create_subscription_invite(
            session=session,
            user_id=user.id,
            plan=target_plan,
            bot_username=settings.BOT_USERNAME,
        )
        return InviteLinkResponse(
            token=invite.token,
            deep_link=link,
            plan=invite.plan.value,
            expires_at=invite.expires_at,
        )

    # ==========================================================
    # 7. SUBSCRIPTIONS & CRYPTOPAY BILLING
    # ==========================================================
    @app.get("/api/v1/subscriptions/current", response_model=SubscriptionSummaryDTO, tags=["Subscriptions"])
    async def get_current_subscription(
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> SubscriptionSummaryDTO:
        sub_info = await SubscriptionService.get_subscription_summary(session, user.id)
        return SubscriptionSummaryDTO(**sub_info)

    @app.post("/api/v1/subscriptions/invoice", response_model=InvoiceResponseDTO, tags=["Subscriptions"])
    async def create_subscription_invoice(
        body: CreateInvoiceRequest,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> InvoiceResponseDTO:
        plan = _map_plan_code(body.plan_code)
        invoice = await CryptoPayService.create_invoice(session, user.id, plan)
        return InvoiceResponseDTO(
            invoice_id=invoice.invoice_id,
            plan=invoice.plan.value,
            amount=invoice.amount,
            asset=invoice.asset,
            pay_url=invoice.pay_url or "https://t.me/CryptoBot",
            status=invoice.status,
        )

    @app.post("/api/v1/subscriptions/check-invoice/{invoice_id}", tags=["Subscriptions"])
    async def verify_subscription_invoice(
        invoice_id: str,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> Dict[str, Any]:
        stmt = select(PaymentInvoice).where(
            PaymentInvoice.invoice_id == invoice_id,
            PaymentInvoice.user_id == user.id,
        )
        inv = (await session.execute(stmt)).scalars().first()
        if not inv:
            raise HTTPException(status_code=404, detail="Счёт не найден")

        is_paid, status_str = await CryptoPayService.check_invoice(session, invoice_id)
        sub_info = await SubscriptionService.get_subscription_summary(session, user.id)
        return {
            "invoice_id": invoice_id,
            "is_paid": is_paid,
            "status": status_str,
            "subscription": sub_info,
        }

    @app.post("/api/v1/subscriptions/cancel", tags=["Subscriptions"])
    async def cancel_user_subscription(
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> Dict[str, Any]:
        cancelled = await SubscriptionService.cancel_active_subscription(session, user.id)
        sub_info = await SubscriptionService.get_subscription_summary(session, user.id)
        return {"cancelled": cancelled, "subscription": sub_info}

    # ==========================================================
    # 8. VOICE LAB (TTS & STT)
    # ==========================================================
    @app.post("/api/v1/voice/test", tags=["Voice"])
    async def test_voice_synthesis(
        body: VoiceTestRequest,
        user: User = Depends(get_current_user),
    ) -> Dict[str, Any]:
        samples = {
            "shy": "Приветик... я тут немножко застеснялась, но решила записать тебе голосовое. Как проходит твой вечер?",
            "playful": "Ахах, ну ты и кадр конечно! Ладно, колись давай, чем сейчас занимаешься? Только честно!",
            "sarcastic": "Ой, только не говори, что ты снова весь в делах и совсем нет минутки мне написать. Не верю ни единому слову!",
            "caring": "Слушай, ты сегодня так много работал... Надеюсь, ты уже дома и хотя бы поужинал? Отдыхай давай.",
            "adaptive_all": "Слушай, я только освободилась после пар, иду сейчас за латте... У тебя как день прошел вообще?",
        }
        raw_text = body.custom_text or samples.get(body.style, samples["adaptive_all"])
        prepared_phonetics = VoiceService.prepare_text_for_tts(raw_text)

        sent = False
        if body.send_to_telegram and settings.ADMIN_BOT_TOKEN:
            sent = await VoiceService.send_telegram_voice_note(
                bot_token=settings.ADMIN_BOT_TOKEN,
                chat_id=user.telegram_id,
                text=raw_text,
                caption="🎙 <b>Тестовое голосовое сообщение из Mini App</b>",
            )

        return {
            "status": "ok",
            "style": body.style,
            "raw_text": raw_text,
            "prepared_phonetics": prepared_phonetics,
            "sent_to_telegram": sent,
        }

    # ==========================================================
    # 9. ADMIN PANEL ENDPOINTS (RBAC Protected)
    # ==========================================================
    @app.get("/api/v1/admin/users", tags=["Admin"])
    async def admin_list_users(
        _admin: User = Depends(require_admin),
        session: AsyncSession = Depends(get_db),
    ) -> List[Dict[str, Any]]:
        overview = await AccountService.get_admin_subscribers_overview(session)
        return overview

    @app.post("/api/v1/admin/grant", tags=["Admin"])
    async def admin_grant_subscription(
        body: AdminGrantRequest,
        _admin: User = Depends(require_admin),
        session: AsyncSession = Depends(get_db),
    ) -> Dict[str, Any]:
        target_str = body.target.strip()
        target_user: Optional[User] = None

        if target_str.startswith("@"):
            uname = target_str.lstrip("@")
            stmt = select(User).where(User.username.ilike(uname))
            target_user = (await session.execute(stmt)).scalars().first()
        elif target_str.lstrip("-").isdigit():
            tg_id = int(target_str)
            stmt = select(User).where(User.telegram_id == tg_id)
            target_user = (await session.execute(stmt)).scalars().first()
            if not target_user:
                target_user = User(telegram_id=tg_id)
                session.add(target_user)
                await session.commit()
                await session.refresh(target_user)

        if not target_user:
            raise HTTPException(
                status_code=404,
                detail=f"Пользователь {target_str} не найден в базе. Укажите числовой Telegram ID.",
            )

        plan_raw = body.days_or_plan.strip().lower().rstrip("d")
        days = int(plan_raw) if plan_raw.isdigit() else 7
        if days <= 3:
            plan = PlanDuration.DAYS_3
        elif days <= 7:
            plan = PlanDuration.DAYS_7
        elif days <= 14:
            plan = PlanDuration.DAYS_14
        else:
            plan = PlanDuration.DAYS_31

        await SubscriptionService.grant_custom_days(session, target_user.id, days=days, plan=plan)
        sub_info = await SubscriptionService.get_subscription_summary(session, target_user.id)
        return {
            "status": "granted",
            "user_id": target_user.id,
            "telegram_id": target_user.telegram_id,
            "username": target_user.username,
            "days_granted": days,
            "subscription": sub_info,
        }

    @app.post("/api/v1/admin/users/{target_user_id}/cancel-sub", tags=["Admin"])
    async def admin_cancel_user_subscription(
        target_user_id: int,
        _admin: User = Depends(require_admin),
        session: AsyncSession = Depends(get_db),
    ) -> Dict[str, Any]:
        cancelled = await SubscriptionService.cancel_active_subscription(session, target_user_id)
        return {"status": "cancelled" if cancelled else "no_active_sub", "user_id": target_user_id}

    # ==========================================================
    # 10. OPTIONAL TELEGRAM WEBHOOK RECEIVER
    # ==========================================================
    @app.post("/api/v1/webhook/telegram", tags=["Webhook"])
    async def telegram_webhook_endpoint(
        update: Dict[str, Any],
        x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        if settings.WEBHOOK_SECRET_TOKEN:
            if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
                x_telegram_bot_api_secret_token, settings.WEBHOOK_SECRET_TOKEN
            ):
                raise HTTPException(status_code=403, detail="Invalid webhook secret token")

        from companion.bot.telegram_bot_engine import TelegramBotEngine
        bot = TelegramBotEngine(settings.ADMIN_BOT_TOKEN or "", settings.ADMIN_TELEGRAM_ID)
        await bot.handle_update(update)
        return {"ok": True}

    # ==========================================================
    # 11. SERVE TELEGRAM MINI APP STATIC FRONTEND BUNDLE
    # ==========================================================
    frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if frontend_dist.exists() and (frontend_dist / "index.html").exists():
        assets_dir = frontend_dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/", include_in_schema=False)
        @app.get("/app", include_in_schema=False)
        @app.get("/app/{full_path:path}", include_in_schema=False)
        async def serve_mini_app(full_path: str = ""):
            return FileResponse(str(frontend_dist / "index.html"))
    else:
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def fallback_root():
            return """
            <!DOCTYPE html>
            <html lang="ru">
            <head><meta charset="utf-8"><title>Telegram Companion AI API</title></head>
            <body style="font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:2rem;">
                <h2>🌸 Telegram Companion AI — Backend API v2.0</h2>
                <p>Backend API активен. Документация OpenAPI: <a style="color:#38bdf8" href="/docs">/docs</a></p>
            </body>
            </html>
            """

    return app


app = create_app()
