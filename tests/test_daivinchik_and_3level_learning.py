import pytest
import uuid
from datetime import datetime, timezone
from companion.models import User, Contact, Persona, ContactStatus, ContactBehavioralPreference, async_session_maker
from companion.services.daivinchik_service import DaivinchikService
from companion.ai.behavioral_memory import ControlledFeedbackLearner, ThreeLevelMemoryManager
from companion.services.ai_service import AIService


def test_daivinchik_forwarded_profile_detection_and_parsing():
    # 1. Forwarded profile text from Daivinchik
    sample_profile = """
    Алина, 22, Москва
    Учусь на дизайне, обожаю выставки, кофе и долгие прогулки.
    Ищу приятного человека для душевного общения.
    """
    is_profile = DaivinchikService.is_daivinchik_message({"text": sample_profile})
    assert is_profile is True

    parsed = DaivinchikService.parse_profile(sample_profile)
    assert parsed["name"] == "Алина"
    assert parsed["age"] == 22
    assert parsed["city"] == "Москва"
    assert "кофе" in parsed["hobbies"]
    assert "прогулки" in parsed["hobbies"]


def test_daivinchik_conversational_context_detection():
    # Should detect profile discovery intent
    assert DaivinchikService.detect_daivinchik_dialogue_context("нашел твою анкету") is True
    assert DaivinchikService.detect_daivinchik_dialogue_context("увидел тебя в дайвинчике") is True
    assert DaivinchikService.detect_daivinchik_dialogue_context("попалась твоя анкета в телеге") is True
    assert DaivinchikService.detect_daivinchik_dialogue_context("мы в дайвинчике познакомились вроде?") is True

    # Should NOT trigger on everyday casual messages
    assert DaivinchikService.detect_daivinchik_dialogue_context("привет, как дела?") is False
    assert DaivinchikService.detect_daivinchik_dialogue_context("пошли попьем кофе на выходных") is False
    assert DaivinchikService.detect_daivinchik_dialogue_context("что делаешь сегодня вечером?") is False


@pytest.mark.asyncio
async def test_three_level_memory_persistence_and_explicit_feedback():
    rand_tg = uuid.uuid4().int % 900000000 + 100000000
    async with async_session_maker() as session:
        user = User(telegram_id=rand_tg, username=f"u_{rand_tg}")
        session.add(user)
        await session.commit()
        await session.refresh(user)

        contact = Contact(
            user_id=user.id,
            tg_id=rand_tg + 1,
            first_name="Кирилл",
            status=ContactStatus.ACTIVE,
            preferred_response_length="medium",
            no_brackets=False,
            emoji_frequency="medium",
        )
        session.add(contact)
        await session.commit()
        await session.refresh(contact)

        # 1. Simulate explicit feedback: "пиши короче"
        res1 = await ControlledFeedbackLearner.analyze_and_learn_feedback(
            session, contact, "слушай, пиши короче плиз"
        )
        assert res1["updated"] is True
        assert contact.preferred_response_length == "short"

        # 2. Simulate explicit feedback: "хватит смайликов"
        res2 = await ControlledFeedbackLearner.analyze_and_learn_feedback(
            session, contact, "хватит смайликов, раздражает"
        )
        assert res2["updated"] is True
        assert contact.emoji_frequency == "none"

        # 3. Simulate explicit feedback: "не ставь скобки"
        res3 = await ControlledFeedbackLearner.analyze_and_learn_feedback(
            session, contact, "не ставь скобки в конце"
        )
        assert res3["updated"] is True
        assert contact.no_brackets is True

        # 4. Verify persistent profile retrieval
        profile = await ControlledFeedbackLearner.get_level3_behavioral_profile(session, contact)
        assert profile["response_length"] == "short"
        assert profile["emoji_pref"] == "none"
        assert profile["no_brackets"] is True
        assert len(profile["explicit_rules"]) >= 2

        # 5. Verify prompt block formatting
        prompt_snippet = ControlledFeedbackLearner.format_behavioral_prompt(profile)
        assert "КОРОТКО" in prompt_snippet
        assert "СТРОГО ЗАПРЕЩЕНЫ" in prompt_snippet
        assert "НЕ используй эмодзи" in prompt_snippet


def test_daivinchik_profile_injection_into_ai_prompt():
    persona = Persona(user_id=1, name="Алина", age=22, city="Москва")
    contact = Contact(
        user_id=1,
        tg_id=777888,
        first_name="Игорь",
        status=ContactStatus.ACTIVE,
    )
    # Store discovered profile
    disc_data = {
        "source": "daivinchik",
        "name": "Игорь",
        "age": 25,
        "city": "Москва",
        "description": "Люблю выставки и современное искусство",
        "hobbies": ["выставки", "спорт"],
    }
    contact.set_discovered_profile(disc_data)
    contact.preferred_response_length = "short"
    contact.no_brackets = True

    prompt = AIService.build_system_prompt(persona, contact, incoming_message="привет")
    assert "КОНТЕКСТ ЗНАКОМСТВА (ДАЙВИНЧИК)" in prompt
    assert "Игорь" in prompt
    assert "ВЫУЧЕННЫЕ ПРЕДПОЧТЕНИЯ СОБЕСЕДНИКА (УРОВЕНЬ 3)" in prompt
    assert "КОРОТКО" in prompt
    assert "СТРОГО ЗАПРЕЩЕНЫ" in prompt
