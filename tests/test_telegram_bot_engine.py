import random
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from companion.models import (
    init_db,
    async_session_maker,
    User,
    Persona,
    InviteToken,
    Subscription,
    PaymentInvoice,
    Contact,
    AccountSession,
)
from companion.models.enums import PlanDuration, AccountStatus
from companion.bot.telegram_bot_engine import TelegramBotEngine
from companion.services.voice_service import VoiceService
from companion.services.crypto_pay_service import CryptoPayService
from companion.services.subscription_service import SubscriptionService
from companion.services.account_service import AccountService


@pytest.mark.asyncio
async def test_bot_engine_link_creation_and_voice():
    await init_db()

    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.edit_message = AsyncMock(return_value=True)
    bot.send_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    test_user_tg = {"id": 8501711204, "username": "test_admin"}

    # 1. Test Link Creation callback (gen_link_7d)
    cq_link = {
        "id": "cq_1",
        "data": "gen_link_7d",
        "message": {"message_id": 100, "chat": {"id": 8501711204}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_link)

    # Verify edit_message was called with HTML and the generated link
    assert bot.edit_message.called
    call_args = bot.edit_message.call_args[0]
    chat_id, msg_id, text = call_args[0], call_args[1], call_args[2]
    assert chat_id == 8501711204
    assert msg_id == 100
    assert "Персональная ссылка создана" in text
    assert "7_days" in text
    assert "https://t.me/" in text

    # Verify InviteToken in DB
    async with async_session_maker() as session:
        stmt_u = select(User).where(User.telegram_id == 8501711204)
        u = (await session.execute(stmt_u)).scalars().first()
        assert u is not None

        stmt_i = select(InviteToken).where(InviteToken.user_id == u.id)
        invites = (await session.execute(stmt_i)).scalars().all()
        assert len(invites) > 0
        assert any(i.plan == PlanDuration.DAYS_7 for i in invites)

    # 2. Test Voice Test callback (menu_voice_test: opening menu does NOT send voice)
    with patch("companion.services.voice_service.VoiceService.send_telegram_voice_note", new_callable=AsyncMock) as mock_voice:
        mock_voice.return_value = True
        cq_voice = {
            "id": "cq_2",
            "data": "menu_voice_test",
            "message": {"message_id": 101, "chat": {"id": 8501711204}},
            "from": test_user_tg,
        }
        await bot.handle_callback(cq_voice)
        # Must NOT send voice note when merely opening the menu
        assert not mock_voice.called

        # Now click explicit benchmark button: must send voice note
        cq_bench = {
            "id": "cq_bench",
            "data": "voice_benchmark_test",
            "message": {"message_id": 101, "chat": {"id": 8501711204}},
            "from": test_user_tg,
        }
        await bot.handle_callback(cq_bench)
        assert mock_voice.called
        v_args = mock_voice.call_args[1]
        assert v_args["chat_id"] == 8501711204
        assert len(v_args.get("text", "")) > 10

    # 3. Test Style Switch callback (set_style_playful)
    cq_style = {
        "id": "cq_3",
        "data": "set_style_playful",
        "message": {"message_id": 102, "chat": {"id": 8501711204}},
        "from": test_user_tg,
    }
    await bot.handle_callback(cq_style)

    async with async_session_maker() as session:
        stmt_p = select(Persona).where(Persona.user_id == u.id)
        p = (await session.execute(stmt_p)).scalars().first()
        assert p is not None
        assert p.current_style == "playful"


@pytest.mark.asyncio
async def test_admin_grant_subscription():
    await init_db()
    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.send_message = AsyncMock(return_value=True)

    recipient_id = random.randint(700000000, 799999999)
    grant_msg = {
        "text": f"/grant {recipient_id} 30",
        "from": {"id": 8501711204, "username": "admin"},
        "chat": {"id": 8501711204},
    }
    await bot.handle_message(grant_msg)

    # Verify send_message was called to notify recipient and admin
    assert bot.send_message.call_count >= 2
    recipient_call = [c for c in bot.send_message.call_args_list if c[1]["chat_id"] == recipient_id]
    assert len(recipient_call) > 0
    assert "Вам подарок от Администратора" in recipient_call[0][1]["text"]
    assert "30 дней" in recipient_call[0][1]["text"]

    # Verify DB subscription
    async with async_session_maker() as session:
        stmt_u = select(User).where(User.telegram_id == recipient_id)
        u = (await session.execute(stmt_u)).scalars().first()
        assert u is not None

        stmt_s = select(Subscription).where(Subscription.user_id == u.id, Subscription.is_active == True)
        sub = (await session.execute(stmt_s)).scalars().first()
        assert sub is not None
        assert sub.days_remaining() >= 29


@pytest.mark.asyncio
async def test_cryptobot_payment_flow():
    await init_db()
    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    user_tg = {"id": 555444333, "username": "buyer"}

    # 1. Create Invoice Callback
    cq_buy = {
        "id": "cq_buy_1",
        "data": "buy_plan_7d",
        "message": {"message_id": 200, "chat": {"id": 555444333}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_buy)

    assert bot.edit_message.called
    call_text = bot.edit_message.call_args[0][2]
    assert "СЧЁТ НА ОПЛАТУ" in call_text
    assert "7.00 USDT" in call_text

    # Extract invoice id from DB
    async with async_session_maker() as session:
        stmt_u = select(User).where(User.telegram_id == 555444333)
        u = (await session.execute(stmt_u)).scalars().first()
        assert u is not None

        stmt_inv = select(PaymentInvoice).where(PaymentInvoice.user_id == u.id).order_by(PaymentInvoice.created_at.desc())
        inv = (await session.execute(stmt_inv)).scalars().first()
        assert inv is not None
        assert inv.amount == 7.0
        inv_id = inv.invoice_id

    # 2. Check Payment Callback
    cq_check = {
        "id": "cq_check_1",
        "data": f"check_pay_{inv_id}",
        "message": {"message_id": 200, "chat": {"id": 555444333}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_check)

    # Verify subscription activated
    async with async_session_maker() as session:
        stmt_s = select(Subscription).where(Subscription.user_id == u.id, Subscription.is_active == True)
        sub = (await session.execute(stmt_s)).scalars().first()
        assert sub is not None
        assert sub.days_remaining() >= 6


@pytest.mark.asyncio
async def test_subscription_cancellation():
    await init_db()
    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    user_tg = {"id": 111222333, "username": "canceler"}

    async with async_session_maker() as session:
        u = await bot.get_or_create_user(session, user_tg)
        # Activate sub first
        await bot.get_or_create_user(session, user_tg)
        from companion.services.subscription_service import SubscriptionService
        await SubscriptionService.activate_plan(session, u.id, PlanDuration.DAYS_7)

    # Cancel subscription
    cq_cancel = {
        "id": "cq_c_1",
        "data": "do_cancel_sub",
        "message": {"message_id": 300, "chat": {"id": 111222333}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_cancel)

    async with async_session_maker() as session:
        active_sub = await SubscriptionService.get_active_subscription(session, u.id)
        assert active_sub is None


def test_voice_text_humanization():
    text = "Ой, ну приветик))) Я тут подумала... Как твои дела вообще? Чем занимаешься сегодня?✨🙈"
    cleaned = VoiceService.humanize_text_for_speech(text)
    # Check emojis are stripped
    assert "✨" not in cleaned
    assert "🙈" not in cleaned
    # Check ))) converted to natural speech pause/giggle
    assert "ахах" in cleaned or "..." in cleaned


@pytest.mark.asyncio
async def test_clone_voice_command_and_info():
    await init_db()
    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.send_message = AsyncMock(return_value=True)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    # 1. Test clone_voice_info callback
    cq_info = {
        "id": "cq_clone_info",
        "data": "clone_voice_info",
        "message": {"message_id": 401, "chat": {"id": 8501711204}},
        "from": {"id": 8501711204, "username": "admin"},
    }
    await bot.handle_callback(cq_info)
    assert bot.edit_message.called
    call_text = bot.edit_message.call_args[0][2]
    assert "КЛОНИРОВАНИЕ ГОЛОСА" in call_text
    assert "/clone" in call_text

    # 2. Test /clone command with mock VoiceService
    with patch.object(VoiceService, "clone_voice_from_sample", AsyncMock(return_value=(True, "ok", "mock_voice_123"))):
        with patch.object(VoiceService, "send_telegram_voice_note", AsyncMock(return_value=True)):
            clone_msg = {
                "text": "/clone mock_el_key_abc123",
                "from": {"id": 8501711204, "username": "admin"},
                "chat": {"id": 8501711204},
            }
            await bot.handle_message(clone_msg)
            assert bot.send_message.call_count >= 2
            success_call = [
                c for c in bot.send_message.call_args_list
                if (len(c[0]) > 1 and "mock_voice_123" in str(c[0][1])) or ("text" in c[1] and "mock_voice_123" in str(c[1]["text"]))
            ]
            assert len(success_call) > 0
            assert VoiceService.send_telegram_voice_note.called


@pytest.mark.asyncio
async def test_grant_subscription_by_username_and_durations():
    await init_db()
    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.send_message = AsyncMock(return_value=True)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    # 1. Test parse_plan_and_days
    d3, p3, l3 = bot.parse_plan_and_days("3d")
    assert d3 == 3 and p3 == PlanDuration.DAYS_3
    d7, p7, l7 = bot.parse_plan_and_days("7")
    assert d7 == 7 and p7 == PlanDuration.DAYS_7
    d14, p14, l14 = bot.parse_plan_and_days("14d")
    assert d14 == 14 and p14 == PlanDuration.DAYS_14
    d30, p30, l30 = bot.parse_plan_and_days("30")
    assert d30 == 30 and p30 == PlanDuration.DAYS_31
    d31, p31, l31 = bot.parse_plan_and_days("31")
    assert d31 == 31 and p31 == PlanDuration.DAYS_31
    d90, p90, l90 = bot.parse_plan_and_days("90d")
    assert d90 == 90 and p90 == PlanDuration.DAYS_31
    d365, p365, l365 = bot.parse_plan_and_days("365")
    assert d365 == 365 and p365 == PlanDuration.DAYS_31

    # 2. Grant by @username for existing user in DB
    target_tg_id = random.randint(900000000, 999999999)
    target_uname = f"client_{target_tg_id}"
    async with async_session_maker() as session:
        u = User(telegram_id=target_tg_id, username=target_uname)
        session.add(u)
        await session.commit()
        await session.refresh(u)
        target_uid = u.id

    msg_grant_username = {
        "text": f"/grant @{target_uname} 14d",
        "from": {"id": 8501711204, "username": "admin"},
        "chat": {"id": 8501711204},
    }
    await bot.handle_message(msg_grant_username)

    async with async_session_maker() as session:
        sub = await SubscriptionService.get_active_subscription(session, target_uid)
        assert sub is not None
        assert sub.plan == PlanDuration.DAYS_14
        # Verify notification sent to target user
        sent_chat_ids = [c[1]["chat_id"] if "chat_id" in c[1] else c[0][0] for c in bot.send_message.call_args_list]
        assert target_tg_id in sent_chat_ids

    # 3. Grant by @username resolved via get_chat API
    ext_tg_id = random.randint(600000000, 699999999)
    ext_uname = f"ext_client_{ext_tg_id}"
    bot.get_chat = AsyncMock(return_value={"id": ext_tg_id, "username": ext_uname})
    msg_grant_external = {
        "text": f"/grant @{ext_uname} 365",
        "from": {"id": 8501711204, "username": "admin"},
        "chat": {"id": 8501711204},
    }
    await bot.handle_message(msg_grant_external)

    async with async_session_maker() as session:
        stmt_ext = select(User).where(User.telegram_id == ext_tg_id)
        res_ext = await session.execute(stmt_ext)
        ext_u = res_ext.scalars().first()
        assert ext_u is not None
        ext_sub = await SubscriptionService.get_active_subscription(session, ext_u.id)
        assert ext_sub is not None

    # 4. Button callback grant_self_31d
    cq_grant_self = {
        "id": "cq_gs",
        "data": "grant_self_31d",
        "message": {"message_id": 501, "chat": {"id": 8501711204}},
        "from": {"id": 8501711204, "username": "admin"},
    }
    await bot.handle_callback(cq_grant_self)
    assert bot.edit_message.called
    call_text = bot.edit_message.call_args[0][2]
    assert "31 дней" in call_text


@pytest.mark.asyncio
async def test_user_invite_link_permission_by_plan():
    await init_db()
    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.send_message = AsyncMock(return_value=True)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    non_admin_id = random.randint(800000000, 899999999)
    user_tg = {"id": non_admin_id, "username": f"user_{non_admin_id}"}

    # 1. User with NO subscription opens menu_invites
    cq_invites = {
        "id": "cq_inv_1",
        "data": "menu_invites",
        "message": {"message_id": 601, "chat": {"id": non_admin_id}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_invites)
    assert bot.edit_message.called
    msg_text = bot.edit_message.call_args[0][2]
    assert "У вас нет активной подписки" in msg_text

    # 2. Activate 3-day subscription for user
    async with async_session_maker() as session:
        u = await bot.get_or_create_user(session, user_tg)
        await SubscriptionService.activate_plan(session, u.id, PlanDuration.DAYS_3)

    # 3. User with 3-day plan opens menu_invites
    await bot.handle_callback(cq_invites)
    kb = bot.edit_message.call_args[1]["reply_markup"]["inline_keyboard"]
    callbacks = [btn["callback_data"] for row in kb for btn in row]
    assert "gen_link_3d" in callbacks
    assert "link_locked_7d" in callbacks
    assert "link_locked_31d" in callbacks

    # 4. User tries to generate 31-day link -> Blocked with alert
    bot.answer_callback_query.reset_mock()
    cq_gen_31 = {
        "id": "cq_gen_31",
        "data": "gen_link_31d",
        "message": {"message_id": 601, "chat": {"id": non_admin_id}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_gen_31)
    assert bot.answer_callback_query.called
    cb_args = bot.answer_callback_query.call_args
    assert "Тариф недоступен" in cb_args[0][1] or cb_args[1].get("show_alert") is True

    # 5. User generates 3-day link -> Permitted and created
    bot.edit_message.reset_mock()
    cq_gen_3 = {
        "id": "cq_gen_3",
        "data": "gen_link_3d",
        "message": {"message_id": 601, "chat": {"id": non_admin_id}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_gen_3)
    assert bot.edit_message.called
    success_text = bot.edit_message.call_args[0][2]
    assert "Персональная ссылка создана" in success_text
    assert "3_days" in success_text


@pytest.mark.asyncio
async def test_legend_menu_and_wizard():
    await init_db()
    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.send_message = AsyncMock(return_value=True)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    uid = random.randint(700000000, 799999999)
    user_tg = {"id": uid, "username": f"legend_owner_{uid}"}

    # 1. Open Legend Menu
    cq_menu = {
        "id": "cq_leg_1",
        "data": "menu_legend",
        "message": {"message_id": 801, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_menu)
    assert bot.edit_message.called
    menu_text = bot.edit_message.call_args[0][2]
    assert "АНКЕТА И ЛЕГЕНДА ДЕВУШКИ" in menu_text

    # 2. Click Edit Name and Age -> set state WAIT_NAME_AGE
    cq_edit_name = {
        "id": "cq_leg_2",
        "data": "legend_edit_name_age",
        "message": {"message_id": 801, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_edit_name)
    assert bot._user_input_states.get(uid) == "WAIT_NAME_AGE"

    # Send message with Name & Age: "Кристина, 23"
    msg_name_age = {
        "text": "Кристина, 23",
        "from": user_tg,
        "chat": {"id": uid},
    }
    await bot.handle_message(msg_name_age)
    assert uid not in bot._user_input_states

    async with async_session_maker() as session:
        stmt_u = select(User).where(User.telegram_id == uid)
        u = (await session.execute(stmt_u)).scalars().first()
        stmt_p = select(Persona).where(Persona.user_id == u.id)
        p = (await session.execute(stmt_p)).scalars().first()
        assert p.name == "Кристина"
        assert p.age == 23

    # 3. Click Edit City and District -> set state WAIT_CITY_DISTRICT
    cq_city = {
        "id": "cq_leg_3",
        "data": "legend_edit_city_district",
        "message": {"message_id": 801, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_city)
    assert bot._user_input_states.get(uid) == "WAIT_CITY_DISTRICT"

    msg_city = {
        "text": "Санкт-Петербург, Петроградка",
        "from": user_tg,
        "chat": {"id": uid},
    }
    await bot.handle_message(msg_city)

    async with async_session_maker() as session:
        p = (await session.execute(stmt_p)).scalars().first()
        assert p.city == "Санкт-Петербург"
        assert p.district == "Петроградка"

    # 4. Click Wizard Auto -> set state WAIT_LEGEND_WIZARD
    cq_wiz = {
        "id": "cq_leg_4",
        "data": "legend_wizard_auto",
        "message": {"message_id": 801, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_wiz)
    assert bot._user_input_states.get(uid) == "WAIT_LEGEND_WIZARD"

    msg_wiz = {
        "text": "меня зовут Настя, 22, Питер Васька, учусь на медсестру, люблю рок и кофе, не люблю душные кафе, хочу на квест",
        "from": user_tg,
        "chat": {"id": uid},
    }
    await bot.handle_message(msg_wiz)

    async with async_session_maker() as session:
        p = (await session.execute(stmt_p)).scalars().first()
        assert p.name == "Настя"
        assert p.age == 22
        assert "Петербург" in p.city or "Питер" in p.city or p.city == "Москва"


@pytest.mark.asyncio
async def test_lead_accept_callback():
    await init_db()
    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    uid = random.randint(700000000, 799999999)
    user_tg = {"id": uid, "username": f"lead_owner_{uid}"}

    async with async_session_maker() as session:
        u = await bot.get_or_create_user(session, user_tg)
        c = Contact(
            user_id=u.id,
            tg_id=999888777,
            first_name="Парень",
            bot_paused=False,
            funnel_stage="quest_agreed",
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        contact_id = c.id

    cq_lead = {
        "id": "cq_lead_1",
        "data": f"lead_accept_{contact_id}",
        "message": {"message_id": 901, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_lead)
    assert bot.answer_callback_query.called
    assert bot.edit_message.called

    async with async_session_maker() as session:
        stmt = select(Contact).where(Contact.id == contact_id)
        updated_c = (await session.execute(stmt)).scalars().first()
        assert updated_c.bot_paused is True
        assert updated_c.ai_active is False
        assert updated_c.paused_reason == "quest_lead_accepted_by_owner"

    # Test returning control to AI (lead_resume)
    cq_resume = {
        "id": "cq_lead_2",
        "data": f"lead_resume_{contact_id}",
        "message": {"message_id": 901, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_resume)
    assert bot.answer_callback_query.called
    assert bot.edit_message.called

    async with async_session_maker() as session:
        stmt = select(Contact).where(Contact.id == contact_id)
        resumed_c = (await session.execute(stmt)).scalars().first()
        assert resumed_c.bot_paused is False
        assert resumed_c.ai_active is True
        assert resumed_c.paused_reason is None


@pytest.mark.asyncio
async def test_multi_account_legend_and_style_management():
    await init_db()
    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.edit_message = AsyncMock(return_value=True)
    bot.send_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    uid = random.randint(800000000, 899999999)
    user_tg = {"id": uid, "username": f"multi_owner_{uid}"}

    async with async_session_maker() as session:
        u = await bot.get_or_create_user(session, user_tg)
        # Create 2 accounts for user
        acc1 = AccountSession(
            user_id=u.id,
            phone="+79991112233",
            first_name="Алина_ВК",
            username="alina_acc1",
            is_active=True,
            status=AccountStatus.CONNECTED,
        )
        acc2 = AccountSession(
            user_id=u.id,
            phone="+79994445566",
            first_name="Даша_ТГ",
            username="dasha_acc2",
            is_active=True,
            status=AccountStatus.CONNECTED,
        )
        session.add_all([acc1, acc2])
        await session.commit()
        await session.refresh(acc1)
        await session.refresh(acc2)
        acc1_id = acc1.id
        acc2_id = acc2.id

    # 1. Open Legend Menu: with 2 accounts, must show account selection menu
    cq_menu = {
        "id": "cq_multi_1",
        "data": "menu_legend",
        "message": {"message_id": 950, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_menu)
    assert bot.edit_message.called
    kb = bot.edit_message.call_args[1]["reply_markup"]["inline_keyboard"]
    callbacks = [btn["callback_data"] for row in kb for btn in row]
    assert f"legend_acc_{acc1_id}" in callbacks
    assert f"legend_acc_{acc2_id}" in callbacks
    assert "legend_acc_default" in callbacks

    # 2. Open Account 1 Legend Card
    cq_acc1 = {
        "id": "cq_multi_2",
        "data": f"legend_acc_{acc1_id}",
        "message": {"message_id": 950, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_acc1)
    card_text = bot.edit_message.call_args[0][2]
    assert "Алина_ВК" in card_text or "alina_acc1" in card_text

    # 3. Edit Name and Age for Account 1 only
    cq_edit1 = {
        "id": "cq_multi_3",
        "data": f"legend_edit_name_age_{acc1_id}",
        "message": {"message_id": 950, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_edit1)
    assert bot._user_input_states.get(uid) == f"WAIT_NAME_AGE:{acc1_id}"

    # User inputs: "Виктория, 21"
    msg_name1 = {
        "text": "Виктория, 21",
        "from": user_tg,
        "chat": {"id": uid},
    }
    await bot.handle_message(msg_name1)

    async with async_session_maker() as session:
        a1 = (await session.execute(select(AccountSession).where(AccountSession.id == acc1_id))).scalars().first()
        p1 = await AccountService.ensure_account_persona(session, a1)
        assert p1.name == "Виктория"
        assert p1.age == 21

        a2 = (await session.execute(select(AccountSession).where(AccountSession.id == acc2_id))).scalars().first()
        p2 = await AccountService.ensure_account_persona(session, a2)
        assert p2.name != "Виктория"  # Account 2 remains independent!

    # 4. Use AI Wizard for Account 2 only
    cq_wiz2 = {
        "id": "cq_multi_4",
        "data": f"legend_wizard_auto_{acc2_id}",
        "message": {"message_id": 950, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_wiz2)
    assert bot._user_input_states.get(uid) == f"WAIT_LEGEND_WIZARD:{acc2_id}"

    msg_wiz2 = {
        "text": "меня зовут Дарья, 25, Казань Баумана, маркетолог, обожаю кофе и квесты",
        "from": user_tg,
        "chat": {"id": uid},
    }
    await bot.handle_message(msg_wiz2)

    async with async_session_maker() as session:
        a2 = (await session.execute(select(AccountSession).where(AccountSession.id == acc2_id))).scalars().first()
        p2 = await AccountService.ensure_account_persona(session, a2)
        assert p2.name == "Дарья"
        assert p2.age == 25

        # Check Account 1 was not modified by Account 2's wizard
        a1 = (await session.execute(select(AccountSession).where(AccountSession.id == acc1_id))).scalars().first()
        p1 = await AccountService.ensure_account_persona(session, a1)
        assert p1.name == "Виктория"
        assert p1.age == 21

    # 5. Set distinct communication styles per account
    cq_style1 = {
        "id": "cq_multi_5",
        "data": f"set_style_sarcastic_{acc1_id}",
        "message": {"message_id": 950, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_style1)

    cq_style2 = {
        "id": "cq_multi_6",
        "data": f"set_style_caring_{acc2_id}",
        "message": {"message_id": 950, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_style2)

    async with async_session_maker() as session:
        a1 = (await session.execute(select(AccountSession).where(AccountSession.id == acc1_id))).scalars().first()
        p1 = await AccountService.ensure_account_persona(session, a1)
        assert p1.current_style == "sarcastic"

        a2 = (await session.execute(select(AccountSession).where(AccountSession.id == acc2_id))).scalars().first()
        p2 = await AccountService.ensure_account_persona(session, a2)
        assert p2.current_style == "caring"

    # 6. Test menu_accounts display
    cq_accounts = {
        "id": "cq_multi_7",
        "data": "menu_accounts",
        "message": {"message_id": 950, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_accounts)
    assert bot.edit_message.called
    acc_text = bot.edit_message.call_args[0][2]
    assert "Виктория" in acc_text
    assert "Дарья" in acc_text
    assert "сарказм" in acc_text.lower() or "дерзкая" in acc_text.lower()
    assert "заботливая" in acc_text.lower()

    # 7. Test disconnecting Account 1
    cq_disc = {
        "id": "cq_multi_8",
        "data": f"disconnect_acc_{acc1_id}",
        "message": {"message_id": 950, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_disc)
    async with async_session_maker() as session:
        a1 = (await session.execute(select(AccountSession).where(AccountSession.id == acc1_id))).scalars().first()
        assert a1.is_active is False
        assert a1.status == AccountStatus.DISCONNECTED

        a2 = (await session.execute(select(AccountSession).where(AccountSession.id == acc2_id))).scalars().first()
        assert a2.is_active is True
        assert a2.status == AccountStatus.CONNECTED


@pytest.mark.asyncio
async def test_voice_menu_and_samples_redesign():
    await init_db()
    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.edit_message = AsyncMock(return_value=True)
    bot.send_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    uid = 8501711204
    user_tg = {"id": uid, "username": "admin"}

    # 1. Check keyboard structure and white-labeling
    kb = bot.build_voice_test_keyboard()
    all_callbacks = [btn["callback_data"] for row in kb["inline_keyboard"] for btn in row]
    all_btn_texts = [btn["text"] for row in kb["inline_keyboard"] for btn in row]

    # Required callbacks present
    assert "voice_benchmark_test" in all_callbacks
    assert "voice_sample_shy" in all_callbacks
    assert "voice_sample_playful" in all_callbacks
    assert "voice_sample_sarcastic" in all_callbacks
    assert "voice_sample_caring" in all_callbacks
    assert "voice_sample_adaptive_all" in all_callbacks
    assert "voice_sample_random" in all_callbacks
    assert "menu_main" in all_callbacks

    # Obsolete and leaked callbacks removed
    assert "gemini_voice_info" not in all_callbacks
    assert "russian_voice_guide" not in all_callbacks
    assert "upload_voice_info" not in all_callbacks
    assert "voice_asset_sarcastic_name" not in all_callbacks

    # No brand names leaked in button texts
    for txt in all_btn_texts:
        assert "gemini" not in txt.lower()
        assert "eleven" not in txt.lower()
        assert "evenlabs" not in txt.lower()

    # 2. Open Voice Test Menu callback
    cq_menu = {
        "id": "cq_voice_menu_1",
        "data": "menu_voice_test",
        "message": {"message_id": 101, "chat": {"id": uid}},
        "from": user_tg,
    }
    await bot.handle_callback(cq_menu)
    assert bot.edit_message.called
    menu_text = bot.edit_message.call_args[0][2]
    assert "ТЕСТИРОВАНИЕ ГОЛОСОВЫХ СООБЩЕНИЙ" in menu_text
    assert "gemini" not in menu_text.lower()
    assert "elevenlabs" not in menu_text.lower()

    # 3. Test triggering a voice sample
    with patch("companion.services.voice_service.VoiceService.send_telegram_voice_note", new_callable=AsyncMock) as mock_send_voice:
        mock_send_voice.return_value = True
        cq_sample = {
            "id": "cq_sample_1",
            "data": "voice_sample_playful",
            "message": {"message_id": 101, "chat": {"id": uid}},
            "from": user_tg,
        }
        await bot.handle_callback(cq_sample)
        assert mock_send_voice.called
        call_kwargs = mock_send_voice.call_args[1]
        caption = call_kwargs.get("caption", "")
        assert "Игривая" in caption
        assert "elevenlabs" not in caption.lower()
        assert "gemini" not in caption.lower()


@pytest.mark.asyncio
async def test_slash_commands_and_setup_bot_commands():
    await init_db()

    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.send_message = AsyncMock(return_value=True)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    # 1. Test setup_bot_commands
    mock_client = AsyncMock()
    mock_post_res = AsyncMock()
    mock_post_res.status_code = 200
    mock_client.post = AsyncMock(return_value=mock_post_res)
    bot.client = mock_client
    bot.token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

    ok = await bot.setup_bot_commands()
    assert ok is True
    assert mock_client.post.call_count == 3
    # Check setMyCommands calls
    urls = [call[0][0] for call in mock_client.post.call_args_list]
    assert any("setMyCommands" in u for u in urls)
    assert any("setChatMenuButton" in u for u in urls)

    # 2. Test slash commands in handle_message
    commands_to_test = [
        ("/start", "AI-Компаньон"),
        ("/menu", "AI-Компаньон"),
        ("/sub", "УПРАВЛЕНИЕ ВАШЕЙ ПОДПИСКОЙ"),
        ("/status", "УПРАВЛЕНИЕ ВАШЕЙ ПОДПИСКОЙ"),
        ("/accounts", "ПОДКЛЮЧЕННЫЕ АККАУНТЫ"),
        ("/legend", "АНКЕТА И ЛЕГЕНДА"),
        ("/styles", "НАСТРОЙКА СТИЛ"),
        ("/voice", "ТЕСТИРОВАНИЕ ГОЛОСОВЫХ"),
        ("/leads", "лидов"),
        ("/stats", "СТАТИСТИКА"),
        ("/help", "СПРАВКА И СПИСОК КОМАНД"),
        ("/admin", "ПАНЕЛЬ ГЛАВНОГО АДМИНИСТРАТОРА"),
        ("/users", "ПОЛЬЗОВАТЕЛИ И АККАУНТЫ"),
    ]

    for cmd, expected_substring in commands_to_test:
        bot.send_message.reset_mock()
        msg = {
            "text": cmd,
            "chat": {"id": 8501711204},
            "from": {"id": 8501711204, "username": "admin"},
        }
        await bot.handle_message(msg)
        assert bot.send_message.called, f"Command {cmd} did not trigger send_message"
        sent_text = bot.send_message.call_args[1].get("text") or bot.send_message.call_args[0][1]
        assert expected_substring in sent_text, f"Command {cmd} did not return '{expected_substring}' in text"


@pytest.mark.asyncio
async def test_admin_subscribers_overview_and_account_visibility():
    await init_db()

    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.send_message = AsyncMock(return_value=True)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    # 1. Setup subscriber with 2 accounts and an active subscription
    async with async_session_maker() as session:
        stmt_su = select(User).where(User.telegram_id == 987654321)
        sub_user = (await session.execute(stmt_su)).scalars().first()
        if not sub_user:
            sub_user = User(telegram_id=987654321, username="test_client_owner")
            session.add(sub_user)
            await session.commit()
            await session.refresh(sub_user)

        # Clear any existing accounts for clean test run
        stmt_del_acc = select(AccountSession).where(AccountSession.user_id == sub_user.id)
        existing_accs = (await session.execute(stmt_del_acc)).scalars().all()
        for ea in existing_accs:
            await session.delete(ea)
        await session.commit()

        # 14 days plan
        await SubscriptionService.activate_plan(session, sub_user.id, PlanDuration.DAYS_14)

        # Account 1
        acc1 = AccountSession(
            user_id=sub_user.id,
            first_name="Алина",
            username="alina_main",
            tg_user_id=111222,
            business_connection_id="conn_test_1",
            is_active=True,
            status=AccountStatus.CONNECTED,
        )
        # Account 2
        acc2 = AccountSession(
            user_id=sub_user.id,
            first_name="Алиночка",
            username="alina_second",
            tg_user_id=333444,
            business_connection_id="conn_test_2",
            is_active=False,
            status=AccountStatus.DISCONNECTED,
        )
        session.add_all([acc1, acc2])
        await session.commit()
        await session.refresh(acc1)
        await session.refresh(acc2)

        # Set dedicated personas
        p1 = await AccountService.ensure_account_persona(session, acc1)
        p1.current_style = "shy"
        p2 = await AccountService.ensure_account_persona(session, acc2)
        p2.current_style = "playful"
        await session.commit()

        # 2. Test AccountService.get_admin_subscribers_overview
        overview = await AccountService.get_admin_subscribers_overview(session)
        assert len(overview) >= 2
        client_entry = next((u for u in overview if u["user_id"] == sub_user.id), None)
        assert client_entry is not None
        assert client_entry["total_accounts"] == 2
        assert client_entry["active_accounts"] == 1
        assert client_entry["sub_info"]["is_active"] is True
        assert "14" in client_entry["sub_info"]["plan_name"]

        # 3. Test admin_users_overview callback
        cq_overview = {
            "id": "cq_adm_1",
            "data": "admin_users_overview",
            "message": {"message_id": 500, "chat": {"id": 8501711204}},
            "from": {"id": 8501711204, "username": "admin"},
        }
        await bot.handle_callback(cq_overview)
        assert bot.edit_message.called
        ov_text = bot.edit_message.call_args[0][2]
        assert "ПОЛЬЗОВАТЕЛИ И АККАУНТЫ" in ov_text
        assert "test_client_owner" in ov_text
        assert "@alina_main" in ov_text
        assert "@alina_second" in ov_text
        assert "Скромная" in ov_text

        # 4. Test admin_user_detail callback
        cq_detail = {
            "id": "cq_adm_2",
            "data": f"admin_user_detail_{sub_user.id}",
            "message": {"message_id": 500, "chat": {"id": 8501711204}},
            "from": {"id": 8501711204, "username": "admin"},
        }
        await bot.handle_callback(cq_detail)
        assert bot.edit_message.called
        detail_text = bot.edit_message.call_args[0][2]
        assert "КАРТОЧКА ПОЛЬЗОВАТЕЛЯ" in detail_text
        assert str(sub_user.telegram_id) in detail_text
        assert "2 из 2" in detail_text

        # 5. Test grant_user callback (+7 days)
        cq_grant = {
            "id": "cq_adm_3",
            "data": f"grant_user_{sub_user.id}_7",
            "message": {"message_id": 500, "chat": {"id": 8501711204}},
            "from": {"id": 8501711204, "username": "admin"},
        }
        await bot.handle_callback(cq_grant)
        assert bot.send_message.called
        sent_to_user = bot.send_message.call_args[1].get("text") or bot.send_message.call_args[0][1]
        assert "7 дней" in sent_to_user

        # 6. Test cancel_user_sub callback
        cq_cancel = {
            "id": "cq_adm_4",
            "data": f"cancel_user_sub_{sub_user.id}",
            "message": {"message_id": 500, "chat": {"id": 8501711204}},
            "from": {"id": 8501711204, "username": "admin"},
        }
        await bot.handle_callback(cq_cancel)
        assert bot.answer_callback_query.called
        assert "аннулирована" in bot.answer_callback_query.call_args[0][1]




