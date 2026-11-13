from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from companion.ai.memory_manager import MemoryManager
from companion.bot.manager_engine import ConversationManagerEngine, MessageDebouncer
from companion.bot.telegram_bot_engine import TelegramBotEngine
from companion.models.db import Base
from companion.models.entities import Contact, ContactMemory, Persona, User
from companion.models.enums import ContactStatus, PlanDuration
from companion.services.subscription_service import SubscriptionService


@pytest.fixture
async def db_Maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_miniapp_frozen_and_migrated_crm_context_manual_reply(db_Maker):
    bot = TelegramBotEngine(token="test_token", admin_id=8501711204)
    sent_messages = []
    edited_messages = []
    callback_answers = []

    async def mock_send(chat_id, text, reply_markup=None, parse_mode="HTML"):
        sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return True

    async def mock_edit(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
        edited_messages.append({"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": reply_markup})
        return True

    async def mock_answer(cq_id, text=None, show_alert=False):
        callback_answers.append({"cq_id": cq_id, "text": text, "show_alert": show_alert})

    bot.send_message = mock_send
    bot.edit_message = mock_edit
    bot.answer_callback_query = mock_answer

    # 1. Verify Mini App is frozen and not injected as primary button in main menu
    main_kb = bot.build_main_menu_keyboard(is_admin=True)
    all_btns = [btn for row in main_kb["inline_keyboard"] for btn in row]
    assert not any("web_app" in btn for btn in all_btns)

    # Seed user, subscription, and 3 contacts in different states
    now_utc = datetime.now(timezone.utc)
    async with db_Maker() as session:
        user = await bot.get_or_create_user(session, {"id": 8501711204, "username": "owner"})
        await SubscriptionService.activate_plan(session, user.id, PlanDuration.DAYS_31)

        c_active = Contact(
            user_id=user.id,
            tg_id=1001,
            first_name="Денис",
            username="denis_m",
            status=ContactStatus.ACTIVE,
            ai_active=True,
            bot_paused=False,
            last_incoming_at=now_utc - timedelta(minutes=5),
            total_messages_in=4,
            total_messages_out=4,
            dialogue_topics="кофе, дизайн",
            dialogue_summary="Обсуждают любимые кофейни в центре",
        )
        c_attention = Contact(
            user_id=user.id,
            tg_id=1002,
            first_name="Артем",
            username="artem_quest",
            status=ContactStatus.LEAD_MEETING,
            ai_active=False,
            bot_paused=True,
            lead_summary="Пойдем завтра на квест в 19:00",
            last_incoming_at=now_utc - timedelta(minutes=15),
        )
        c_paused = Contact(
            user_id=user.id,
            tg_id=1003,
            first_name="Максим",
            username="max_p",
            status=ContactStatus.ACTIVE,
            ai_active=True,
            bot_paused=True,
            last_incoming_at=now_utc - timedelta(hours=2),
        )
        session.add_all([c_active, c_attention, c_paused])
        await session.commit()
        await session.refresh(c_active)

        # Seed 7-level AI memory for c_active
        summary_row = await MemoryManager.get_or_create_summary(session, c_active.id)
        summary_row.summary_text = "Денис работает архитектором, зовет выпить раф."
        summary_row.active_topics = "архитектура, раф-кофе"
        summary_row.unresolved_questions = "В каком районе ты живешь?"
        summary_row.commitments = "Обещала написать вечером после пар"
        session.add(
            ContactMemory(
                contact_id=c_active.id,
                category="fact",
                key="occupation",
                value="Архитектор",
            )
        )
        await session.commit()
        c_active_id = c_active.id

    with patch("companion.bot.telegram_bot_engine.async_session_maker", db_Maker):
        # 2. Test CRM Dashboard & Filters
        await bot.handle_callback({
            "id": "cq1",
            "data": "menu_leads_chats",
            "from": {"id": 8501711204, "username": "owner"},
            "message": {"chat": {"id": 8501711204}, "message_id": 10},
        })
        assert "УПРАВЛЕНИЕ ЧАТАМИ (CRM)" in edited_messages[-1]["text"]
        assert "Активных: <b>1</b>" in edited_messages[-1]["text"]
        assert "Внимание: <b>1</b>" in edited_messages[-1]["text"]

        # Filter by 'attention'
        await bot.handle_callback({
            "id": "cq2",
            "data": "chats_filter_attention",
            "from": {"id": 8501711204, "username": "owner"},
            "message": {"chat": {"id": 8501711204}, "message_id": 10},
        })
        att_btns = [b["text"] for row in edited_messages[-1]["reply_markup"]["inline_keyboard"] for b in row]
        assert any("Артем" in t and "🔴" in t for t in att_btns)
        assert not any("Денис" in t for t in att_btns)

        # Search flow in CRM
        await bot.handle_callback({
            "id": "cq3",
            "data": "chats_search",
            "from": {"id": 8501711204, "username": "owner"},
            "message": {"chat": {"id": 8501711204}, "message_id": 10},
        })
        await bot.handle_message({
            "from": {"id": 8501711204, "username": "owner"},
            "chat": {"id": 8501711204},
            "text": "кофе",
        })
        assert "Поиск:</b> «<code>кофе</code>»" in sent_messages[-1]["text"]

        # 3. Test 7-Level AI Memory Inspector inside Telegram
        await bot.handle_callback({
            "id": "cq4",
            "data": f"chat_context_{c_active_id}",
            "from": {"id": 8501711204, "username": "owner"},
            "message": {"chat": {"id": 8501711204}, "message_id": 10},
        })
        ctx_msg = edited_messages[-1]["text"]
        assert "7-УРОВНЕВАЯ ПАМЯТЬ ИИ" in ctx_msg
        assert "Денис работает архитектором" in ctx_msg
        assert "Архитектор" in ctx_msg

        # 4. Test Manual Reply from Owner to Contact via Bot
        await bot.handle_callback({
            "id": "cq5",
            "data": f"chat_manual_reply_{c_active_id}",
            "from": {"id": 8501711204, "username": "owner"},
            "message": {"chat": {"id": 8501711204}, "message_id": 10},
        })
        await bot.handle_message({
            "from": {"id": 8501711204, "username": "owner"},
            "chat": {"id": 8501711204},
            "text": "Привет! Давай встретимся у Патриков в 18:00)",
        })
        assert "Автоответ ИИ поставлен на паузу на 15 минут" in sent_messages[-1]["text"]
        async with db_Maker() as session:
            refreshed = (await session.execute(select(Contact).where(Contact.id == c_active_id))).scalars().first()
            assert refreshed.bot_paused is True

        # 5. Test Stale Button Handling when contact does not exist
        await bot.handle_callback({
            "id": "cq6",
            "data": "chat_card_999999",
            "from": {"id": 8501711204, "username": "owner"},
            "message": {"chat": {"id": 8501711204}, "message_id": 10},
        })
        assert "Это меню устарело" in edited_messages[-1]["text"]


@pytest.mark.asyncio
async def test_persona_sleep_voice_custom_and_stats_all(db_Maker):
    bot = TelegramBotEngine(token="test_token", admin_id=8501711204)
    sent_messages = []
    edited_messages = []

    bot.send_message = AsyncMock(side_effect=lambda chat_id, text, reply_markup=None, parse_mode="HTML": sent_messages.append({"text": text, "reply_markup": reply_markup}) or True)
    bot.edit_message = AsyncMock(side_effect=lambda chat_id, msg_id, text, reply_markup=None, parse_mode="HTML": edited_messages.append({"text": text, "reply_markup": reply_markup}) or True)
    bot.answer_callback_query = AsyncMock()

    with patch("companion.bot.telegram_bot_engine.async_session_maker", db_Maker):
        # 1. Edit Sleep Schedule via Telegram button
        await bot.handle_callback({
            "id": "cq_sleep",
            "data": "legend_edit_sleep",
            "from": {"id": 8501711204, "username": "owner"},
            "message": {"chat": {"id": 8501711204}, "message_id": 11},
        })
        await bot.handle_message({
            "from": {"id": 8501711204, "username": "owner"},
            "chat": {"id": 8501711204},
            "text": "23:45 - 09:15",
        })
        assert "Режим сна сохранен:</b> 🌙 23:45 – 09:15" in sent_messages[-1]["text"]

        # 2. Custom Voice Text Synthesis
        with patch("companion.services.voice_service.VoiceService.send_telegram_voice_note", new=AsyncMock(return_value=True)) as mock_tts:
            await bot.handle_callback({
                "id": "cq_voice",
                "data": "voice_custom_text",
                "from": {"id": 8501711204, "username": "owner"},
                "message": {"chat": {"id": 8501711204}, "message_id": 11},
            })
            await bot.handle_message({
                "from": {"id": 8501711204, "username": "owner"},
                "chat": {"id": 8501711204},
                "text": "Приветик! Как прошел твой день?",
            })
            assert mock_tts.called
            assert "Фонетическая разметка" in sent_messages[-1]["text"]

        # 3. All-time stats period
        await bot.handle_callback({
            "id": "cq_stats_all",
            "data": "menu_leads_stats_all",
            "from": {"id": 8501711204, "username": "owner"},
            "message": {"chat": {"id": 8501711204}, "message_id": 11},
        })
        assert "СТАТИСТИКА ОБЩЕНИЯ С ЛИДАМИ" in edited_messages[-1]["text"]
        btns = [b["callback_data"] for row in edited_messages[-1]["reply_markup"]["inline_keyboard"] for b in row]
        assert "menu_leads_stats_all" in btns


@pytest.mark.asyncio
async def test_human_manager_interruption_resumption_corrections_and_debounce(db_Maker):
    # 1. Rapid message burst debouncer
    assert (
        MessageDebouncer.combine_burst(["привет", "подожди", "не, давай лучше 24"])
        == "не, давай лучше 24"
    )
    assert (
        MessageDebouncer.combine_burst(["hey", "wait", "actually make it 5 instead"])
        == "actually make it 5 instead"
    )
    assert MessageDebouncer.combine_burst(["давай удалим", "стой", "отмена"]) == "отмена"
    assert ConversationManagerEngine.strip_robotic_filler("Certainly! I'd be happy to help! Готово.") == "Готово."

    bot = TelegramBotEngine(token="test_token", admin_id=8501711204)
    sent_messages = []
    edited_messages = []

    bot.send_message = AsyncMock(side_effect=lambda chat_id, text, reply_markup=None, parse_mode="HTML": sent_messages.append({"text": text, "reply_markup": reply_markup}) or True)
    bot.edit_message = AsyncMock(side_effect=lambda chat_id, msg_id, text, reply_markup=None, parse_mode="HTML": edited_messages.append({"text": text, "reply_markup": reply_markup}) or True)
    bot.answer_callback_query = AsyncMock()

    async with db_Maker() as session:
        user = await bot.get_or_create_user(session, {"id": 8501711204, "username": "owner"})
        session.add(
            Contact(
                user_id=user.id,
                tg_id=2001,
                first_name="Илья",
                username="ilya_latest",
                status=ContactStatus.ACTIVE,
                last_incoming_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    with patch("companion.bot.telegram_bot_engine.async_session_maker", db_Maker):
        # 2. Start editing name/age, then say "подожди" (wait) -> flow stays active
        await bot.handle_callback({
            "id": "cq_na",
            "data": "legend_edit_name_age",
            "from": {"id": 8501711204, "username": "owner"},
            "message": {"chat": {"id": 8501711204}, "message_id": 20},
        })
        await bot.handle_message({
            "from": {"id": 8501711204, "username": "owner"},
            "chat": {"id": 8501711204},
            "text": "подожди",
        })
        assert 8501711204 in bot._user_input_states

        # 3. Interrupt the active flow with "покажи последнего клиента"
        await bot.handle_message({
            "from": {"id": 8501711204, "username": "owner"},
            "chat": {"id": 8501711204},
            "text": "покажи последнего клиента",
        })
        assert "Илья" in sent_messages[-1]["text"]
        assert "Кстати, мы остановились на вводе" in sent_messages[-1]["text"]

        # 4. Resume interrupted flow by saying "да"
        await bot.handle_message({
            "from": {"id": 8501711204, "username": "owner"},
            "chat": {"id": 8501711204},
            "text": "да",
        })
        assert "Возвращаемся к настройке" in sent_messages[-1]["text"]
        assert bot._user_input_states.get(8501711204) == "WAIT_NAME_AGE"

        # 5. Provide input "Вика, 21", then immediately send correction "давай лучше 24"
        await bot.handle_message({
            "from": {"id": 8501711204, "username": "owner"},
            "chat": {"id": 8501711204},
            "text": "Вика, 21",
        })
        assert "Вика, 21 лет" in sent_messages[-1]["text"]

        await bot.handle_message({
            "from": {"id": 8501711204, "username": "owner"},
            "chat": {"id": 8501711204},
            "text": "давай лучше 24",
        })
        assert "24 лет" in sent_messages[-1]["text"]
        async with db_Maker() as session:
            p = (await session.execute(select(Persona))).scalars().first()
            assert p.name == "Вика"
            assert p.age == 24

        # 6. Frustration diagnostics ("почему опять не работает")
        await bot.handle_message({
            "from": {"id": 8501711204, "username": "owner"},
            "chat": {"id": 8501711204},
            "text": "блин почему опять не работает",
        })
        assert "Проверил систему прямо сейчас" in sent_messages[-1]["text"]

        # 7. Concise thanks ("спасибо")
        await bot.handle_message({
            "from": {"id": 8501711204, "username": "owner"},
            "chat": {"id": 8501711204},
            "text": "спасибо",
        })
        assert sent_messages[-1]["text"] in ConversationManagerEngine.THANKS_VARIATIONS
