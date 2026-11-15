import pytest
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.db import Base
from companion.models.entities import User, Persona, Contact, ContactMessage
from companion.models.enums import ContactStatus
from companion.services.intent_service import IntentService, ContactDeflectionIntent
from companion.services.multilingual_service import MultilingualService, DynamicLanguageProfile
from companion.bot.reply_validator import ReplyValidator
from companion.services.ai_service import AIService
from companion.bot.userbot_handler import UserbotHandler


@pytest.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def test_contact_deflection_intent_detection():
    # Instagram variants
    ig_queries = [
        "скинь инсту",
        "дай инст",
        "какой у тебя инстаграм",
        "можно твой инст?",
        "давай в инсте",
        "пойдём в инсту",
        "кинь свой профиль в инсте",
        "у тебя вообще есть инста?",
        "скинь інст",
        "дай інстаграм",
        "what's your insta",
        "send me your instagram",
    ]
    for q in ig_queries:
        res = IntentService.detect_contact_request(q, contact_deflection_count=0)
        assert res.contact_request is True, f"Failed on query: {q}"
        assert res.contact_type == "instagram"
        assert res.deflection_required is True

    # Other contact variants (@username, phone, whatsapp)
    other_queries = [
        ("дай @", "telegram_username"),
        ("скинь @", "telegram_username"),
        ("дай свой ник", "telegram_username"),
        ("какой твой юзернейм", "telegram_username"),
        ("скинь свой номер телефона", "phone"),
        ("дай номер", "phone"),
        ("напиши свой ватсап", "whatsapp"),
        ("где тебя найти?", "other_social"),
    ]
    for q, expected_type in other_queries:
        res = IntentService.detect_contact_request(q, contact_deflection_count=0)
        assert res.contact_request is True, f"Failed on: {q}"
        assert res.contact_type == expected_type


def test_contact_deflection_multi_level_progression():
    # Request 1 -> playful dodge
    res1 = IntentService.detect_contact_request("скинь инсту", contact_deflection_count=0)
    assert res1.request_count == 1
    assert res1.suggested_deflection_tone == "playful_dodge"
    assert res1.request_intensity == "soft"

    # Request 2 -> stronger dodge
    res2 = IntentService.detect_contact_request("ну скинь инсту пожалуйста", contact_deflection_count=1)
    assert res2.request_count == 2
    assert res2.suggested_deflection_tone == "stronger_dodge"
    assert res2.request_intensity == "normal"

    # Request 3 -> calm boundary
    res3 = IntentService.detect_contact_request("да дай инсту чё ты ломаешься", contact_deflection_count=2)
    assert res3.request_count == 3
    assert res3.suggested_deflection_tone == "calm_boundary"
    assert res3.request_intensity == "persistent"

    # Request 4+ -> firm boundary
    res4 = IntentService.detect_contact_request("дай инсту быстро", contact_deflection_count=3)
    assert res4.request_count == 4
    assert res4.suggested_deflection_tone == "firm_boundary"
    assert res4.request_intensity == "pressuring"


def test_reply_validator_sanitizes_contact_leaks():
    # 1. Instagram URL leak
    leaked_url = "да вот держи https://instagram.com/alina_sweet подписывайся"
    sanitized = ReplyValidator.sanitize_contact_deflection(leaked_url)
    assert "instagram.com" not in sanitized
    assert "alina_sweet" not in sanitized

    # 2. @username leak
    leaked_username = "напиши мне в @alina_cutie там спишемся"
    sanitized_user = ReplyValidator.sanitize_contact_deflection(leaked_username)
    assert "@alina_cutie" not in sanitized_user

    # 3. Allowed bot username is preserved
    allowed_mention = "напиши моему боту @my_test_bot если что"
    sanitized_bot = ReplyValidator.sanitize_contact_deflection(allowed_mention, allowed_bot_username="my_test_bot")
    assert "@my_test_bot" in sanitized_bot

    # 4. Phone number leak
    leaked_phone = "мой номер +7 (999) 123-45-67 звони"
    sanitized_phone = ReplyValidator.sanitize_contact_deflection(leaked_phone)
    assert "79991234567" not in sanitized_phone
    assert "+7 (999) 123-45-67" not in sanitized_phone

    # 5. Empty leak fallback returns natural conversational dodge
    empty_url = "https://instagram.com/secret_account"
    fallback_res = ReplyValidator.sanitize_contact_deflection(empty_url)
    assert len(fallback_res) > 10
    assert "инста" in fallback_res or "телеги" in fallback_res or "общаемся" in fallback_res


def test_dynamic_language_explicit_overrides():
    # Priority 1: Explicit user language requests
    res_en = MultilingualService.analyze_dynamic_language("can we speak english please?")
    assert res_en.primary_language == "en"
    assert res_en.is_explicit_override is True

    res_en_ru = MultilingualService.analyze_dynamic_language("ответь на английском")
    assert res_en_ru.primary_language == "en"
    assert res_en_ru.is_explicit_override is True

    res_uk = MultilingualService.analyze_dynamic_language("пиши українською мовою")
    assert res_uk.primary_language == "uk"
    assert res_uk.is_explicit_override is True

    res_uk_alt = MultilingualService.analyze_dynamic_language("перейди на укр")
    assert res_uk_alt.primary_language == "uk"
    assert res_uk_alt.is_explicit_override is True

    res_ru = MultilingualService.analyze_dynamic_language("пиши на русском")
    assert res_ru.primary_language == "ru"
    assert res_ru.is_explicit_override is True


def test_dynamic_language_slang_profanity_translit():
    # Slang & abbreviation detection
    text_slang = "пон, жиза рил, го в гс, я хз че делать мб щас"
    res_slang = MultilingualService.analyze_dynamic_language(text_slang)
    assert res_slang.slang_level >= 0.7
    assert res_slang.style == "playful"

    # Transliteration detection
    text_translit = "privet kak tvoi dela segodnya"
    res_translit = MultilingualService.analyze_dynamic_language(text_translit)
    assert res_translit.transliteration is True
    assert res_translit.script == "latin"
    assert res_translit.primary_language in ("ru", "uk")

    # Profanity detection (without escalating)
    text_profane = "бля это просто пиздец какой-то"
    res_profane = MultilingualService.analyze_dynamic_language(text_profane)
    assert res_profane.profanity_level >= 0.4


def test_ai_service_system_prompt_includes_contact_deflection():
    persona = Persona(
        id=1,
        name="Алина",
        age=22,
        city="Москва",
        district="ЦАО",
        legend="Ты милая девушка.",
    )
    contact = Contact(
        id=10,
        tg_id=123456,
        first_name="Алексей",
        language="ru",
        contact_deflection_count=1,
    )
    deflection_intent = IntentService.detect_contact_request("скинь инсту", contact_deflection_count=1)
    lang_profile = MultilingualService.analyze_dynamic_language("скинь инсту", contact=contact)

    prompt = AIService.build_system_prompt(
        persona=persona,
        contact=contact,
        incoming_message="скинь инсту",
        dynamic_language_profile=lang_profile,
        contact_deflection=deflection_intent,
    )

    assert "АНТИ-ИНСТАГРАМ И УВОД ОТ ЗАПРОСА КОНТАКТОВ" in prompt
    assert "Ты НИКОГДА и ни при каких условиях добровольно НЕ предоставляешь" in prompt
    assert "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО говорить: «Я не даю Instagram»" in prompt
    assert "stronger_dodge" in prompt.lower() or "stronger" in prompt.lower()


@pytest.mark.asyncio
async def test_end_to_end_contact_deflection_and_language_pipeline(async_session: AsyncSession):
    # Setup test User, Persona, and Contact
    from companion.models.entities import Subscription
    from companion.models.enums import PlanDuration

    user = User(id=1, telegram_id=999001, username="test_owner")
    async_session.add(user)
    await async_session.commit()

    sub = Subscription(
        user_id=user.id,
        plan=PlanDuration.DAYS_31,
        starts_at=datetime.now(timezone.utc),
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        is_active=True,
    )
    async_session.add(sub)
    await async_session.commit()

    persona = Persona(
        id=1,
        user_id=user.id,
        name="Алина",
        age=22,
        city="Москва",
        legend="Ты милая и немного саркастичная девушка.",
    )
    async_session.add(persona)
    await async_session.commit()

    contact = Contact(
        id=1,
        user_id=user.id,
        tg_id=777001,
        first_name="Иван",
        language="uk",
        contact_deflection_count=0,
    )
    async_session.add(contact)
    await async_session.commit()

    # User asks for Instagram in Ukrainian
    incoming_text = "Привіт красуне! Скинь свій інст будь ласка)"

    mock_gemini_reply = "ахах, а тобі навіщо так терміново мій інст 😭 давай краще тут спочатку нормально поспілкуємось 😌"

    with patch("companion.ai.gemini_client.GeminiClient.generate_content", new_callable=AsyncMock) as mock_gen, \
         patch("companion.ai.gemini_client.GeminiClient.get_api_key", return_value="mock_api_key"):
        mock_gen.return_value = mock_gemini_reply

        res = await UserbotHandler.process_incoming_message(
            session=async_session,
            user_id=user.id,
            sender_tg_id=contact.tg_id,
            sender_username="ivan_test",
            sender_first_name="Иван",
            text=incoming_text,
            is_voice=False,
            auto_handover_on_date=False,
        )

        assert res["action"] in ("reply", "send_reply")
        assert res["contact"].contact_deflection_count == 1
        assert res["contact"].last_contact_request_at is not None
        assert "тобі навіщо так терміново мій інст" in res["reply_text"]
        assert "@" not in res["reply_text"]
        assert "instagram.com" not in res["reply_text"]
