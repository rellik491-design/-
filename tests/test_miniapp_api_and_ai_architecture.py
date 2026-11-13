from datetime import datetime, timedelta, timezone
import time

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from companion.ai.context_manager import ContextManager
from companion.ai.memory_manager import MemoryManager
from companion.api.app import app
from companion.api.auth import (
    TelegramAuthError,
    build_signed_telegram_init_data,
    create_session_token,
    validate_telegram_init_data,
    verify_session_token,
)
from companion.bot.business_bot import BusinessChatBot
from companion.config import settings
from companion.models.db import Base, get_db
from companion.models.entities import (
    AccountSession,
    Contact,
    ContactMemory,
    ContactMessage,
    ConversationSummary,
    Persona,
    ProcessedUpdate,
    Subscription,
    User,
)
from companion.models.enums import (
    AccountStatus,
    ContactStatus,
    PlanDuration,
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()



# ==============================================================================
# 1. TELEGRAM MINI APP AUTHENTICATION & SECURITY TESTS
# ==============================================================================


def test_telegram_init_data_hmac_validation_success():
    bot_token = "123456789:AAH_test_bot_secret_token_for_hmac"
    user_obj = {
        "id": 777888999,
        "first_name": "Alex",
        "last_name": "Design",
        "username": "alex_design",
        "language_code": "ru",
        "is_premium": True,
    }
    raw_init_data = build_signed_telegram_init_data(
        user_payload=user_obj,
        bot_token=bot_token,
        start_param="inv_test123",
    )
    validated = validate_telegram_init_data(raw_init_data, bot_token=bot_token)

    assert validated["user"]["id"] == 777888999
    assert validated["user"]["username"] == "alex_design"
    assert validated["user"]["first_name"] == "Alex"
    assert validated["user"]["is_premium"] is True
    assert validated["start_param"] == "inv_test123"


def test_telegram_init_data_rejects_tampered_signature():
    bot_token = "123456789:AAH_test_bot_secret_token_for_hmac"
    user_obj = {"id": 777888999, "first_name": "Attacker", "username": "evil"}
    valid = build_signed_telegram_init_data(user_payload=user_obj, bot_token=bot_token)
    tampered = valid.rsplit("hash=", 1)[0] + "hash=" + ("0" * 64)

    with pytest.raises(TelegramAuthError) as exc:
        validate_telegram_init_data(tampered, bot_token=bot_token)
    assert exc.value.status_code == 401
    assert "signature" in exc.value.detail.lower()


def test_telegram_init_data_rejects_expired_auth_date():
    bot_token = "123456789:AAH_test_bot_secret_token_for_hmac"
    user_obj = {"id": 777888999, "first_name": "OldSession"}
    old_ts = int(time.time()) - 90000  # > 24h old
    expired_init_data = build_signed_telegram_init_data(
        user_payload=user_obj,
        bot_token=bot_token,
        auth_date=old_ts,
    )

    with pytest.raises(TelegramAuthError) as exc:
        validate_telegram_init_data(expired_init_data, bot_token=bot_token, max_age_seconds=3600)
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower()


def test_session_token_roundtrip_and_tamper_rejection():
    token = create_session_token(
        telegram_id=999111222,
        db_user_id=42,
        username="miniapp_owner",
        is_admin=True,
    )
    claims = verify_session_token(token)
    assert claims["uid"] == 42
    assert claims["tg_id"] == 999111222
    assert claims["adm"] is True

    # Tamper signature
    parts = token.split(".")
    tampered_token = parts[0] + "." + ("a" * 64)
    with pytest.raises(TelegramAuthError) as exc:
        verify_session_token(tampered_token)
    assert exc.value.status_code == 401


# ==============================================================================
# 2. MULTI-TIER MEMORY & 7-LEVEL CONTEXT + INTELLIGENT RETRIEVAL TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_memory_manager_extracts_long_term_facts_without_spam(db_session):
    user = User(telegram_id=100200300, username="owner_mem")
    db_session.add(user)
    await db_session.flush()

    contact = Contact(
        user_id=user.id,
        tg_id=555666777,
        first_name="Дмитрий",
        username="dmitry_m",
        status=ContactStatus.ACTIVE,
    )
    db_session.add(contact)
    await db_session.commit()

    # 1. Random short chatter -> should NOT create long-term memory spam
    saved_chatter = await MemoryManager.extract_and_store_memories(
        db_session, contact, "Ахах, ну да, понятно))"
    )
    assert saved_chatter == []

    # 2. Meaningful biographical facts & agreement -> MUST be extracted into ContactMemory
    rich_msg = (
        "Меня зовут Дима, я живу в Москве, "
        "я работаю архитектором в строительной компании и хожу в зал по вечерам. "
        "Давай в субботу встретимся в центре?"
    )
    saved_facts = await MemoryManager.extract_and_store_memories(
        db_session, contact, rich_msg
    )
    await db_session.commit()

    assert len(saved_facts) >= 3
    memories = await MemoryManager.get_long_term_memories(db_session, contact.id)
    keys = {m.key for m in memories}
    assert "occupation" in keys
    assert "city" in keys
    assert "sport_or_hobby" in keys


@pytest.mark.asyncio
async def test_context_manager_7_levels_and_targeted_history_retrieval(db_session):
    """
    Verifies Phase 8 & Phase 9:
    - Normal messages do NOT load the full history beyond the recent window.
    - When a context trigger occurs ('я же уже говорил где работаю'), ContextManager
      detects the problem, retrieves the specific older message from history,
      analyzes it, and injects it into the 7-level prompt context.
    """
    user = User(telegram_id=100200301, username="owner_ctx")
    db_session.add(user)
    await db_session.flush()

    persona = Persona(
        user_id=user.id,
        name="Алина",
        age=22,
        city="Москва",
        study_work_hobbies="графический дизайн",
    )
    db_session.add(persona)

    contact = Contact(
        user_id=user.id,
        tg_id=888999000,
        first_name="Максим",
        username="max_dev",
        status=ContactStatus.ACTIVE,
    )
    db_session.add(contact)
    await db_session.flush()

    # Seed 16 historical messages: message #1 contains an important old fact ("работаю хирургом в клинике")
    # followed by 15 recent messages about movies/coffee so message #1 is outside the recent window (10).
    old_fact_msg = ContactMessage(
        contact_id=contact.id,
        sender_role="user",
        text="Я работаю хирургом в частной клинике на Маяковской, часто дежурю ночью",
        telegram_message_id=101,
    )
    db_session.add(old_fact_msg)
    await db_session.flush()

    for i in range(15):
        role = "bot" if i % 2 == 0 else "user"
        txt = f"Обычное сообщение про кино и погоду номер {i}"
        db_session.add(
            ContactMessage(
                contact_id=contact.id,
                sender_role=role,
                text=txt,
                telegram_message_id=102 + i,
            )
        )
    await db_session.commit()

    # Case A: Normal message ("Какой жанр кино тебе нравится?") -> should NOT trigger deep historical retrieval
    ctx_normal = await ContextManager.build_seven_level_context(
        persona=persona,
        contact=contact,
        new_incoming_message="Какой жанр кино тебе нравится?",
        session=db_session,
    )
    assert ctx_normal["context_problem"]["needs_retrieval"] is False
    assert ctx_normal["retrieved_history"] == []
    assert len(ctx_normal["recent_window"]) == 10

    # Case B: User says "Ты забыла? Я же уже говорил где работаю в клинике!"
    # -> MUST trigger detect_context_problem() -> retrieve_relevant_history() -> analyze_history()
    ctx_triggered = await ContextManager.build_seven_level_context(
        persona=persona,
        contact=contact,
        new_incoming_message="Ты забыла? Я же уже говорил где работаю в клинике!",
        session=db_session,
    )
    assert ctx_triggered["context_problem"]["needs_retrieval"] is True
    assert len(ctx_triggered["retrieved_history"]) >= 1
    retrieved_texts = " ".join(m["text"] for m in ctx_triggered["retrieved_history"])
    assert "хирургом" in retrieved_texts
    assert "ТАРГЕТИРОВАННЫЙ ПОИСК ПО АРХИВУ ДИАЛОГА" in ctx_triggered["prompt_contents"]


# ==============================================================================
# 3. BUSINESS CHAT IDEMPOTENCY, EDIT & DELETE HANDLING TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_business_chat_idempotency_and_edit_delete_lifecycle(db_session):
    user = User(telegram_id=300400500, username="biz_owner")
    db_session.add(user)
    await db_session.flush()

    sub = Subscription(
        user_id=user.id,
        plan=PlanDuration.DAYS_31,
        is_active=True,
        starts_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(sub)

    acc = AccountSession(
        user_id=user.id,
        tg_user_id=300400500,
        username="biz_girl",
        first_name="Алина",
        business_connection_id="bc_idemp_test_1",
        status=AccountStatus.CONNECTED,
        is_active=True,
    )
    db_session.add(acc)
    await db_session.commit()

    incoming_payload = {
        "business_connection_id": "bc_idemp_test_1",
        "message_id": 5001,
        "chat": {"id": 900100, "type": "private"},
        "from": {"id": 900100, "first_name": "Артем", "username": "artem_k"},
        "text": "Привет! Как прошел твой день?",
    }

    # 1. First delivery -> processes and replies (using mock_token so no real HTTP or sleep delays)
    res1 = await BusinessChatBot.process_business_message(
        session=db_session,
        message_data=incoming_payload,
        bot_token="mock_token",
        simulate_delays=False,
    )
    assert res1["action"] == "reply"

    # 2. Duplicate delivery of the exact same message_id -> deduplicated via ProcessedUpdate!
    res2 = await BusinessChatBot.process_business_message(
        session=db_session,
        message_data=incoming_payload,
        bot_token="mock_token",
        simulate_delays=False,
    )
    assert res2["action"] == "ignored"
    assert res2["reason"] == "duplicate_message"

    # 3. Edited business message -> updates text in DB and extracts new memory without sending duplicate reply
    edited_payload = {
        "business_connection_id": "bc_idemp_test_1",
        "message_id": 5001,
        "chat": {"id": 900100, "type": "private"},
        "from": {"id": 900100, "first_name": "Артем", "username": "artem_k"},
        "text": "Привет! Как прошел твой день? Я кстати живу в Казани",
    }
    edit_res = await BusinessChatBot.process_edited_business_message(
        session=db_session,
        message_data=edited_payload,
    )
    assert edit_res["action"] == "message_edited"

    stmt_msg = select(ContactMessage).where(ContactMessage.telegram_message_id == 5001)
    msg_row = (await db_session.execute(stmt_msg)).scalars().first()
    assert msg_row is not None
    assert msg_row.is_edited is True
    assert "Казани" in msg_row.text

    # 4. Deleted business message -> marks is_deleted = True
    del_res = await BusinessChatBot.process_deleted_business_messages(
        session=db_session,
        delete_data={
            "business_connection_id": "bc_idemp_test_1",
            "chat": {"id": 900100},
            "message_ids": [5001],
        },
    )
    assert del_res["action"] == "messages_deleted"
    assert del_res["deleted_count"] == 1
    await db_session.refresh(msg_row)
    assert msg_row.is_deleted is True


# ==============================================================================
# 4. FASTAPI MINI APP BACKEND ENDPOINTS & ANTI-IDOR TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_miniapp_api_full_flow_and_idor_protection(db_session, monkeypatch):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    test_bot_token = "999888777:AAH_test_miniapp_token"
    monkeypatch.setattr(settings, "ADMIN_BOT_TOKEN", test_bot_token)
    monkeypatch.setattr(settings, "ADMIN_TELEGRAM_ID", 111111)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Health & Readiness checks
            r_health = await client.get("/health")
            assert r_health.status_code == 200
            assert r_health.json()["status"] == "ok"
            assert "x-request-id" in r_health.headers

            r_ready = await client.get("/ready")
            assert r_ready.status_code == 200
            assert r_ready.json()["status"] == "ready"

            # 2. Authenticate User A (Admin) via cryptographically signed Telegram initData
            init_data_user_a = build_signed_telegram_init_data(
                user_payload={"id": 111111, "first_name": "AdminOwner", "username": "admin_owner"},
                bot_token=test_bot_token,
            )
            r_auth_a = await client.post("/api/v1/auth/telegram", json={"init_data": init_data_user_a})
            assert r_auth_a.status_code == 200
            auth_a_data = r_auth_a.json()
            token_a = auth_a_data["session_token"]
            user_a_id = auth_a_data["user"]["id"]
            assert auth_a_data["user"]["is_admin"] is True

            headers_a = {"Authorization": f"Bearer {token_a}"}

            # 3. Create a Contact & messages belonging to User A
            contact_a = Contact(
                user_id=user_a_id,
                tg_id=444555666,
                first_name="Сергей",
                username="sergey_vip",
                status=ContactStatus.LEAD_MEETING,
                funnel_stage="date_proposed",
                total_messages_in=3,
                total_messages_out=3,
            )
            db_session.add(contact_a)
            await db_session.flush()
            db_session.add(
                ContactMessage(
                    contact_id=contact_a.id,
                    sender_role="user",
                    text="Привет, я работаю пилотом, давай завтра поужинаем!",
                )
            )
            await db_session.commit()

            # 4. Dashboard stats for User A
            r_dash = await client.get("/api/v1/dashboard/stats?period=7d", headers=headers_a)
            assert r_dash.status_code == 200
            dash_json = r_dash.json()
            assert dash_json["total_leads"] >= 1
            assert dash_json["meetup_leads"] >= 1

            # 5. List Contacts & Inspect 7-Level AI Context for Contact A
            r_contacts = await client.get("/api/v1/contacts", headers=headers_a)
            assert r_contacts.status_code == 200
            assert any(c["id"] == contact_a.id for c in r_contacts.json())

            r_ctx = await client.get(f"/api/v1/contacts/{contact_a.id}/context", headers=headers_a)
            assert r_ctx.status_code == 200
            ctx_json = r_ctx.json()
            assert ctx_json["contact_id"] == contact_a.id

            # 6. Pause & Resume Contact A via Mini App API
            r_pause = await client.post(f"/api/v1/contacts/{contact_a.id}/pause", headers=headers_a)
            assert r_pause.status_code == 200
            assert r_pause.json()["bot_paused"] is True

            r_resume = await client.post(f"/api/v1/contacts/{contact_a.id}/resume", headers=headers_a)
            assert r_resume.status_code == 200
            assert r_resume.json()["bot_paused"] is False

            # 7. Persona Studio: Get, Update, and AI Wizard
            r_personas = await client.get("/api/v1/personas", headers=headers_a)
            assert r_personas.status_code == 200
            assert len(r_personas.json()) >= 1

            r_wiz = await client.post(
                "/api/v1/personas/wizard",
                headers=headers_a,
                json={
                    "description": "Меня зовут Кристина, 23 года, живу в Казани на Баумана, переехала из Уфы на учебу, учусь на дизайнера",
                },
            )
            assert r_wiz.status_code == 200
            wiz_persona = r_wiz.json()
            assert wiz_persona["name"] == "Кристина"
            assert wiz_persona["age"] == 23
            assert wiz_persona["city"] == "Казань"

            # 8. Authenticate User B (Non-Admin) and verify ANTI-IDOR & RBAC protection
            init_data_user_b = build_signed_telegram_init_data(
                user_payload={"id": 222222, "first_name": "RegularUser", "username": "reg_user"},
                bot_token=test_bot_token,
            )
            r_auth_b = await client.post("/api/v1/auth/telegram", json={"init_data": init_data_user_b})
            assert r_auth_b.status_code == 200
            token_b = r_auth_b.json()["session_token"]
            headers_b = {"Authorization": f"Bearer {token_b}"}

            # User B tries to read User A's contact -> MUST get 403 Forbidden (IDOR blocked)
            r_idor_get = await client.get(f"/api/v1/contacts/{contact_a.id}", headers=headers_b)
            assert r_idor_get.status_code == 403

            # User B tries to delete User A's contact -> MUST get 403 Forbidden
            r_idor_del = await client.delete(f"/api/v1/contacts/{contact_a.id}", headers=headers_b)
            assert r_idor_del.status_code == 403

            # User B tries to access Admin endpoint -> MUST get 403 Forbidden
            r_rbac = await client.get("/api/v1/admin/users", headers=headers_b)
            assert r_rbac.status_code == 403

            # Admin User A accesses Admin endpoint -> 200 OK
            r_admin_ok = await client.get("/api/v1/admin/users", headers=headers_a)
            assert r_admin_ok.status_code == 200
            assert len(r_admin_ok.json()) >= 2
    finally:
        app.dependency_overrides.clear()
