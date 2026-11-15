import pytest
import random
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from sqlalchemy import select

from companion.models.db import init_db, async_session_maker
from companion.models.entities import User, Persona, Contact, AccountSession, Subscription
from companion.models.enums import PlanDuration, AccountStatus
from companion.services.ai_service import AIService
from companion.bot.telegram_bot_engine import TelegramBotEngine
from companion.bot.userbot_handler import UserbotHandler
from companion.services.subscription_service import SubscriptionService


@pytest.mark.asyncio
async def test_legend_wizard_parsing_with_new_fields():
    # Test fallback parser returns all fields including birth_city, move_reason, years_in_city
    text = "Привет меня зовут Катя 23 года из Казани, переехала учиться на переводчика, живу тут 3 года, люблю выставки"
    parsed = await AIService.parse_legend_wizard(text)
    assert parsed["name"] == "Катя"
    assert parsed["age"] == 23
    assert parsed["city"] == "Казань"
    assert "birth_city" in parsed
    assert "move_reason" in parsed
    assert "years_in_city" in parsed


def test_format_legend_card_contains_new_fields():
    engine = TelegramBotEngine(token="mock_token", admin_id=123456)
    persona = Persona(
        name="Диана",
        age=21,
        city="Москва",
        district="Таганка",
        birth_city="Воронеж",
        move_reason="Поступила в МГУ и решила остаться",
        years_in_city="3 года",
    )
    card = engine.format_legend_card(persona)
    assert "Откуда родом:</b> Воронеж" in card
    assert "Почему переехала:</b> <i>Поступила в МГУ и решила остаться</i>" in card
    assert "Сколько уже тут:</b> <i>3 года</i>" in card


def test_build_legend_keyboard_has_new_edit_buttons():
    engine = TelegramBotEngine(token="mock_token", admin_id=123456)
    kb = engine.build_legend_keyboard(account_id=5)
    callbacks = [btn["callback_data"] for row in kb["inline_keyboard"] for btn in row]
    assert "legend_edit_name_age_5" in callbacks
    assert "legend_edit_city_district_5" in callbacks
    assert "legend_edit_birth_city_5" in callbacks
    assert "legend_edit_move_reason_5" in callbacks
    assert "legend_edit_years_in_city_5" in callbacks
    assert "legend_wizard_auto_5" in callbacks


@pytest.mark.asyncio
async def test_telegram_bot_engine_legend_callbacks_and_input_states():
    await init_db()
    uid = random.randint(800000000, 899999999)
    user_tg = {"id": uid, "username": f"owner_{uid}"}

    engine = TelegramBotEngine(token="mock_token", admin_id=uid)
    engine.send_message = AsyncMock(return_value=True)
    engine.edit_message = AsyncMock(return_value=True)
    engine.answer_callback_query = AsyncMock(return_value=True)

    # 1. Trigger birth city edit callback
    cb_update = {
        "id": "cq_100",
        "from": user_tg,
        "data": "legend_edit_birth_city",
        "message": {"chat": {"id": uid}, "message_id": 55},
    }
    await engine.handle_callback(cb_update)
    assert engine._user_input_states.get(uid) == "WAIT_BIRTH_CITY"

    # Send message with new birth city
    msg_update = {
        "message_id": 56,
        "from": user_tg,
        "chat": {"id": uid, "type": "private"},
        "text": "Екатеринбург",
    }
    await engine.handle_message(msg_update)
    assert uid not in engine._user_input_states

    async with async_session_maker() as session:
        stmt_u = select(User).where(User.telegram_id == uid)
        u = (await session.execute(stmt_u)).scalars().first()
        stmt_p = select(Persona).where(Persona.user_id == u.id)
        p = (await session.execute(stmt_p)).scalars().first()
        assert p.birth_city == "Екатеринбург"

    # 2. Trigger move reason edit callback
    cb_update_2 = {
        "id": "cq_101",
        "from": user_tg,
        "data": "legend_edit_move_reason",
        "message": {"chat": {"id": uid}, "message_id": 57},
    }
    await engine.handle_callback(cb_update_2)
    assert engine._user_input_states.get(uid) == "WAIT_MOVE_REASON"

    msg_update_2 = {
        "message_id": 58,
        "from": user_tg,
        "chat": {"id": uid, "type": "private"},
        "text": "Перевелась на новую работу",
    }
    await engine.handle_message(msg_update_2)

    async with async_session_maker() as session:
        p2 = (await session.execute(stmt_p)).scalars().first()
        assert p2.move_reason == "Перевелась на новую работу"

    # 3. Trigger years in city callback
    cb_update_3 = {
        "id": "cq_102",
        "from": user_tg,
        "data": "legend_edit_years_in_city",
        "message": {"chat": {"id": uid}, "message_id": 59},
    }
    await engine.handle_callback(cb_update_3)
    assert engine._user_input_states.get(uid) == "WAIT_YEARS_IN_CITY"

    msg_update_3 = {
        "message_id": 60,
        "from": user_tg,
        "chat": {"id": uid, "type": "private"},
        "text": "Около двух лет",
    }
    await engine.handle_message(msg_update_3)

    async with async_session_maker() as session:
        p3 = (await session.execute(stmt_p)).scalars().first()
        assert p3.years_in_city == "Около двух лет"


@pytest.mark.asyncio
async def test_language_detection_pipeline():
    await init_db()
    uid = random.randint(600000000, 699999999)
    async with async_session_maker() as session:
        user = User(telegram_id=uid, username=f"sub_owner_{uid}")
        session.add(user)
        await session.commit()
        await session.refresh(user)

        await SubscriptionService.activate_plan(session, user.id, PlanDuration.DAYS_14)

        persona = Persona(
            user_id=user.id,
            name="Алина",
            city="Москва",
        )
        session.add(persona)
        await session.commit()

        # Contact 1: writes in Ukrainian -> must be accurately detected as "uk"
        res_uk = await UserbotHandler.process_incoming_message(
            session=session,
            user_id=user.id,
            sender_tg_id=uid + 1,
            sender_username="alex_ua",
            sender_first_name="Олексій",
            text="Привіт, як справи? шо робиш?",
        )
        c1 = res_uk["contact"]
        assert c1.language == "uk"

        # Contact 2: writes 100% in English -> must be "other" or "en"
        res_en = await UserbotHandler.process_incoming_message(
            session=session,
            user_id=user.id,
            sender_tg_id=uid + 2,
            sender_username="john_doe",
            sender_first_name="John",
            text="Hey, where are you from?",
        )
        c2 = res_en["contact"]
        assert c2.language in ("en", "other")


def test_ai_service_system_prompt_has_all_user_rules():
    persona = Persona(
        name="Алина",
        age=22,
        city="Москва",
        district="ЦАО",
        birth_city="Самара",
        move_reason="Поступила в вуз",
        years_in_city="4 года",
    )
    contact_ru = Contact(
        user_id=1,
        tg_id=777,
        first_name="Иван",
        language="ru",
    )
    prompt = AIService.build_system_prompt(persona, contact_ru)

    # 1. Background fields
    assert "Откуда родом: Самара" in prompt
    assert "Почему переехала: Поступила в вуз" in prompt
    assert "Сколько уже тут: 4 года" in prompt

    # 2. Strict multilingual & Russian rules
    assert "МУЛЬТИЯЗЫЧНОСТЬ" in prompt
    assert "НА РУССКОМ ЯЗЫКЕ" in prompt
    assert "НА УКРАИНСКОМ ЯЗЫКЕ" in prompt

    # 3. Text style & Anti-AI
    assert "КАТЕГОРИЧЕСКИЙ ЗАПРЕТ ДЛИННЫХ ТИРЕ" in prompt
    assert "НИКАКИХ ТОЧЕК В КОНЦЕ" in prompt
    assert "капец" in prompt
    assert "чисто" in prompt
    assert "день сурка" in prompt
    assert "на чилле" in prompt

    # 4. Dry answers 3-step formula
    assert "3-шаговой формуле" in prompt or "3-шагов" in prompt

    # 5. Boundaries & Call refusal
    assert "Ахах, а ты наглый однако" in prompt
    assert "я видеозвонки не очень" in prompt

    # 6. Geography & meeting strategy
    assert "Кстати, ты откуда?" in prompt
    assert "планируешь на выходных поехать туда" in prompt
    assert "[EVENT: DATE_PROPOSED]" in prompt
