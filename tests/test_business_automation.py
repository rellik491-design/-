import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from unittest.mock import AsyncMock, patch
from companion.models.db import Base
from companion.models.entities import User, AccountSession, Persona
from companion.models.enums import PlanDuration, ContactStatus, AccountStatus
from companion.services.invite_service import InviteService
from companion.services.account_service import AccountService
from companion.services.subscription_service import SubscriptionService
from companion.bot.business_bot import BusinessChatBot
from companion.bot.userbot_handler import UserbotHandler
from companion.bot.admin_notifier import AdminNotifier


@pytest.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_create_and_verify_invite_link(test_session: AsyncSession):
    user = User(telegram_id=112233, username="buyer_1")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Generate unique 7-day invite link
    link, invite = await InviteService.create_subscription_invite(
        test_session, user.id, PlanDuration.DAYS_7, bot_username="MyAiSetterBot", now=now
    )
    assert "https://t.me/MyAiSetterBot?start=" in link
    assert invite.plan == PlanDuration.DAYS_7
    assert invite.is_active is True

    # 2. Verify token is valid today
    verified = await InviteService.verify_invite(test_session, invite.token, now=now)
    assert verified is not None
    assert verified.id == invite.id

    # 3. Verify token expires after 8 days
    expired = await InviteService.verify_invite(
        test_session, invite.token, now=now + timedelta(days=8)
    )
    assert expired is None


@pytest.mark.asyncio
async def test_chat_automation_end_to_end(test_session: AsyncSession):
    user = User(telegram_id=445566, username="buyer_2")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    now = datetime.now(timezone.utc)

    # Generate 14-day link
    _, invite = await InviteService.create_subscription_invite(
        test_session, user.id, PlanDuration.DAYS_14, now=now
    )

    # Connect account via Chat Automation
    ok, msg, acc = await InviteService.register_chat_automation(
        session=test_session,
        token_str=invite.token,
        business_connection_id="biz_conn_test_99",
        tg_user_id=777000,
        first_name="Алина",
        username="alina_model",
        now=now,
    )
    assert ok is True
    assert acc.business_connection_id == "biz_conn_test_99"

    # Message 1: Male writes to Alina
    msg_update_1 = {
        "business_connection_id": "biz_conn_test_99",
        "chat": {"id": 1001},
        "from": {"id": 1001, "first_name": "Сергей", "username": "sergey_nsk"},
        "text": "Привет! Я из Новосибирска, как ты?",
    }
    res_1 = await BusinessChatBot.process_business_message(
        session=test_session, message_data=msg_update_1, bot_token="mock_token"
    )
    assert res_1["action"] == "reply"
    contact = res_1["contact"]
    assert contact.detected_city == "Новосибирск"
    assert contact.detected_timezone == "Asia/Novosibirsk"

    # Message 2: Sergey proposes a date
    msg_update_2 = {
        "business_connection_id": "biz_conn_test_99",
        "chat": {"id": 1001},
        "from": {"id": 1001, "first_name": "Сергей", "username": "sergey_nsk"},
        "text": "Слушай, давай встретимся на кофе на выходных?",
    }
    res_2 = await BusinessChatBot.process_business_message(
        session=test_session,
        message_data=msg_update_2,
        bot_token="mock_token",
        auto_handover_on_date=True,
    )
    assert res_2["action"] == "handover_to_owner"
    assert contact.status == ContactStatus.LEAD_MEETING
    assert contact.bot_paused is True

    # Message 3: Alina herself writes in the chat -> human takeover
    msg_update_3 = {
        "business_connection_id": "biz_conn_test_99",
        "chat": {"id": 1001},
        "from": {"id": 777000, "first_name": "Алина"},  # sender is Alina herself
        "text": "Привет Сереж, да, давай в субботу)",
    }
    res_3 = await BusinessChatBot.process_business_message(
        session=test_session, message_data=msg_update_3, bot_token="mock_token"
    )
    assert res_3["action"] == "human_takeover_paused"


@pytest.mark.asyncio
async def test_expired_subscription_blocks_chat_automation(test_session: AsyncSession):
    user = User(telegram_id=998877, username="buyer_3")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)

    # 3-day invite
    _, invite = await InviteService.create_subscription_invite(
        test_session, user.id, PlanDuration.DAYS_3, now=now
    )

    await InviteService.register_chat_automation(
        session=test_session,
        token_str=invite.token,
        business_connection_id="biz_conn_expire_test",
        tg_user_id=888000,
        now=now,
    )

    # Try sending message 5 days later (expired)
    later = now + timedelta(days=5)
    msg_update = {
        "business_connection_id": "biz_conn_expire_test",
        "chat": {"id": 2002},
        "from": {"id": 2002, "first_name": "Игорь"},
        "text": "Привет!",
    }
    res = await BusinessChatBot.process_business_message(
        session=test_session, message_data=msg_update, now=later
    )
    assert res["action"] == "ignored"
    assert res["reason"] == "subscription_expired_or_invalid"


@pytest.mark.asyncio
async def test_process_business_connection_auto_link(test_session: AsyncSession):
    from companion.services.subscription_service import SubscriptionService
    
    # 1. Connect without subscription
    conn_data = {
        "id": "biz_conn_999",
        "user": {"id": 123999, "username": "girl_without_sub", "first_name": "Маша"},
        "is_enabled": True,
    }
    res_no_sub = await BusinessChatBot.process_business_connection(
        session=test_session, connection_data=conn_data
    )
    assert res_no_sub["status"] == "no_subscription"

    # 2. Activate subscription for user
    u = (await test_session.execute(select(User).where(User.telegram_id == 123999))).scalars().first()
    assert u is not None
    await SubscriptionService.activate_plan(test_session, u.id, PlanDuration.DAYS_7)

    # 3. Connect with active subscription
    res_sub = await BusinessChatBot.process_business_connection(
        session=test_session, connection_data=conn_data
    )
    assert res_sub["status"] == "connected"
    assert res_sub["is_enabled"] is True


@pytest.mark.asyncio
async def test_quest_funnel_and_abuse_handling(test_session: AsyncSession):
    user = User(telegram_id=556677, username="owner_quest")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    now = datetime.now(timezone.utc)
    _, invite = await InviteService.create_subscription_invite(
        test_session, user.id, PlanDuration.DAYS_14, now=now
    )
    ok, _, acc = await InviteService.register_chat_automation(
        session=test_session,
        token_str=invite.token,
        business_connection_id="biz_conn_quest_1",
        tg_user_id=556677,
        first_name="Алина",
        username="alina_quest",
        now=now,
    )
    assert ok is True

    # Pre-create contact and simulate prior dialogue history (Stage 1 completed: warming up & knowing each other)
    contact = await UserbotHandler.get_or_create_contact(
        session=test_session,
        user_id=user.id,
        tg_id=2002,
        username="ilya_dev",
        first_name="Илья",
        is_incoming=True,
    )
    contact.total_messages_in = 6
    await test_session.commit()

    from companion.services.ai_service import AIService
    AIService.add_chat_turn(contact.id, "Привет! Как твои дела?", "Приветик) да вот дома отдыхаю после учебы)")
    AIService.add_chat_turn(contact.id, "Я с работы пришел, в сервисе весь день крутил тачки", "Ого, прикольно) любишь автомобили?")
    AIService.add_chat_turn(contact.id, "Да, обожаю! А ты чем по жизни горишь?", "Я на 4 курсе дизайна учусь, рисую и пью кофе литрами ахах)")

    async def mock_generate_reply(persona, contact, chat_history, new_incoming_message):
        if "кофе" in new_incoming_message:
            contact.funnel_stage = "date_proposed"
            setattr(contact, "last_event", "DATE_PROPOSED")
            return "хм, ну слушай, в целом можно) только давай не в банальное кафе опять"
        elif "квест" in new_incoming_message:
            contact.funnel_stage = "quest_agreed"
            setattr(contact, "last_event", "QUEST_AGREED")
            return "ура! я тогда забронирую нам квест)"
        return "отлично)"

    with patch.object(AIService, "generate_reply", side_effect=mock_generate_reply):
        # 1. Guy proposes meetup after chatting -> Bot triggers DATE_PROPOSED event and soft-hints against cafe
        msg_date = {
            "business_connection_id": "biz_conn_quest_1",
            "chat": {"id": 2002},
            "from": {"id": 2002, "first_name": "Илья", "username": "ilya_dev"},
            "text": "Давай встретимся на кофе в эти выходные?",
        }
        res_date = await BusinessChatBot.process_business_message(
            session=test_session,
            message_data=msg_date,
            bot_token="mock_token",
            auto_handover_on_date=False,
        )
        assert res_date["action"] == "reply"
        assert res_date["detected_event"] == "DATE_PROPOSED"
        contact = res_date["contact"]
        assert contact.funnel_stage == "date_proposed"
        assert contact.bot_paused is False  # Must NOT be paused, bot continues to lead to quest!
        assert contact.alert_date_sent is True  # Alert sent once to owner

        # 2. Guy agrees to quest -> Bot triggers QUEST_AGREED event
        msg_agree = {
            "business_connection_id": "biz_conn_quest_1",
            "chat": {"id": 2002},
            "from": {"id": 2002, "first_name": "Илья", "username": "ilya_dev"},
            "text": "Давай, я за квест, погнали!",
        }
        res_agree = await BusinessChatBot.process_business_message(
            session=test_session,
            message_data=msg_agree,
            bot_token="mock_token",
            auto_handover_on_date=False,
        )
        assert res_agree["action"] == "reply"
        assert res_agree["detected_event"] == "QUEST_AGREED"
        assert contact.funnel_stage == "quest_agreed"
        assert contact.alert_quest_sent is True  # Quest alarm sent once

    # 2.1 Owner accepts lead -> AI paused, alarm stopped
    AdminNotifier.stop_quest_alarm(contact.id)
    assert AdminNotifier.is_alarm_active(contact.id) is False
    contact.ai_active = False
    contact.bot_paused = True
    contact.paused_reason = "quest_lead_accepted_by_owner"
    await test_session.commit()

    # 2.2 Inbound message while AI paused -> Bot stays completely silent (action: ignored)
    msg_paused = {
        "business_connection_id": "biz_conn_quest_1",
        "chat": {"id": 2002},
        "from": {"id": 2002, "first_name": "Илья", "username": "ilya_dev"},
        "text": "Во сколько встречаемся?",
    }
    res_paused = await BusinessChatBot.process_business_message(
        session=test_session,
        message_data=msg_paused,
        bot_token="mock_token",
        auto_handover_on_date=False,
    )
    assert res_paused["action"] == "ignored"

    # 2.3 Owner resumes AI -> Bot responds again
    contact.ai_active = True
    contact.bot_paused = False
    contact.paused_reason = None
    await test_session.commit()
    assert contact.ai_active is True

    # 3. Abuse / profanity -> Bot NEVER blocks automatically! Tags lead, flags violation, continues dialogue
    contact2_chat_id = 2003
    msg_abuse = {
        "business_connection_id": "biz_conn_quest_1",
        "chat": {"id": contact2_chat_id},
        "from": {"id": contact2_chat_id, "first_name": "Токсик", "username": "toxic_user"},
        "text": "Слышь ты сука тупая пошла нахуй",
    }
    res_abuse = await BusinessChatBot.process_business_message(
        session=test_session,
        message_data=msg_abuse,
        bot_token="mock_token",
        auto_handover_on_date=False,
    )
    assert res_abuse["action"] == "reply"  # Does NOT drop dialogue automatically
    assert res_abuse["detected_violation"] is True  # Violation is flagged
    contact2 = res_abuse["contact"]
    assert contact2.has_violation is True
    assert contact2.violation_count >= 1
    assert "🔴 Оскорбления" in contact2.tags
    assert contact2.status == ContactStatus.ACTIVE  # Kept active until owner manually blocks


@pytest.mark.asyncio
async def test_multi_account_persona_routing_end_to_end(test_session: AsyncSession):
    user = User(telegram_id=888999, username="biz_multi_owner")
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    # Grant subscription allowing multiple accounts
    await SubscriptionService.activate_plan(
        test_session, user.id, PlanDuration.DAYS_14
    )

    # Account 1: Alice in Kazan, sarcastic
    acc1 = AccountSession(
        user_id=user.id,
        phone="+79998880001",
        business_connection_id="biz_conn_alpha",
        tg_user_id=888001,
        first_name="Алиса",
        username="alice_tg",
        is_active=True,
        status=AccountStatus.CONNECTED,
    )
    test_session.add(acc1)
    await test_session.commit()
    await test_session.refresh(acc1)
    p1 = await AccountService.ensure_account_persona(test_session, acc1)
    p1.name = "Алиса"
    p1.city = "Казань"
    p1.current_style = "sarcastic"
    p1.dating_app_context = "познакомились в Pure"
    await test_session.commit()

    # Account 2: Marina in Sochi, caring
    acc2 = AccountSession(
        user_id=user.id,
        phone="+79998880002",
        business_connection_id="biz_conn_beta",
        tg_user_id=888002,
        first_name="Марина",
        username="marina_tg",
        is_active=True,
        status=AccountStatus.CONNECTED,
    )
    test_session.add(acc2)
    await test_session.commit()
    await test_session.refresh(acc2)
    p2 = await AccountService.ensure_account_persona(test_session, acc2)
    p2.name = "Марина"
    p2.city = "Сочи"
    p2.current_style = "caring"
    p2.dating_app_context = "нашла тебя в Дайвинчике"
    await test_session.commit()

    # 1. Incoming message on Account 1 connection
    with patch("companion.services.ai_service.AIService.generate_reply", new_callable=AsyncMock) as mock_reply:
        mock_reply.return_value = "привет) я алиса из казани"
        msg_alpha = {
            "business_connection_id": "biz_conn_alpha",
            "chat": {"id": 3001},
            "from": {"id": 3001, "first_name": "Павел"},
            "text": "Привет! Как тебя зовут?",
        }
        res_a = await BusinessChatBot.process_business_message(
            session=test_session, message_data=msg_alpha, bot_token="mock_token"
        )
        assert res_a["action"] == "reply"
        assert mock_reply.called
        called_persona_a = mock_reply.call_args[1]["persona"]
        assert called_persona_a.name == "Алиса"
        assert called_persona_a.city == "Казань"
        assert called_persona_a.current_style == "sarcastic"
        assert called_persona_a.dating_app_context == "познакомились в Pure"

    # 2. Incoming message on Account 2 connection
    with patch("companion.services.ai_service.AIService.generate_reply", new_callable=AsyncMock) as mock_reply:
        mock_reply.return_value = "приветик) я марина из сочи"
        msg_beta = {
            "business_connection_id": "biz_conn_beta",
            "chat": {"id": 3002},
            "from": {"id": 3002, "first_name": "Дмитрий"},
            "text": "Привет! Откуда ты?",
        }
        res_b = await BusinessChatBot.process_business_message(
            session=test_session, message_data=msg_beta, bot_token="mock_token"
        )
        assert res_b["action"] == "reply"
        assert mock_reply.called
        called_persona_b = mock_reply.call_args[1]["persona"]
        assert called_persona_b.name == "Марина"
        assert called_persona_b.city == "Сочи"
        assert called_persona_b.current_style == "caring"
        assert called_persona_b.dating_app_context == "нашла тебя в Дайвинчике"


@pytest.mark.asyncio
async def test_read_business_message_marks_read():
    res = await BusinessChatBot.read_business_message(
        bot_token="mock_token",
        business_connection_id="biz_conn_test",
        chat_id=12345,
        message_id=987,
    )
    assert res is True

