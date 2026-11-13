"""
Automated Evaluation & Unit Test Suite for:
1. Empirical Female Communication Style Profile & Post-Processing
2. Persistent Behavioral Memory & Controlled Self-Learning
3. Daivinchik (Дайвинчик) Profile Detection, Parsing, & Bio Generation
4. Persona Consistency & Telegram UI Callbacks
"""

import json
import pytest
import hashlib
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy import select

from companion.models import (
    async_session_maker,
    User,
    Contact,
    DaivinchikProfile,
)
from companion.services.female_style_service import (
    FemaleCommunicationProfile,
    FemaleStyleLayer,
    FemaleEmotionalState,
)
from companion.ai.behavioral_memory import ControlledFeedbackLearner
from companion.services.daivinchik_service import DaivinchikService
from companion.bot.response_planner import ResponsePlanner


# =====================================================================
# 1. FEMALE COMMUNICATION STYLE PROFILE & METRICS
# =====================================================================

def test_female_communication_profile_empirical_metrics():
    """Verify that the empirical profile matches the distributions discovered from archives."""
    prof = FemaleCommunicationProfile.load_from_file("female_style_profile.json")

    assert 75.0 <= prof.avg_char_length <= 85.0
    assert 0.50 <= prof.single_msg_prob <= 0.60
    assert 0.35 <= prof.double_burst_prob <= 0.45
    assert 0.01 <= prof.triple_burst_prob <= 0.06
    assert 0.60 <= prof.parentheses_rate <= 0.70
    assert 0.40 <= prof.lowercase_start_rate <= 0.55
    assert prof.exclamation_rate < 0.05
    assert prof.primary_laugh_emoji == "😂"
    assert "ахах" in prof.laughter_tokens
    assert "видео_notes" in prof.deflection_patterns or "video_notes" in prof.deflection_patterns


def test_emotional_state_detection():
    """Verify context-aware emotional state detection from conversation cues."""
    # 1. Boundary push (calls / video notes / insta)
    assert FemaleStyleLayer.detect_emotional_state("запиши мне кружочек плиз") == FemaleEmotionalState.SETTING_BOUNDARIES
    assert FemaleStyleLayer.detect_emotional_state("дай свой номер, созвонимся") == FemaleEmotionalState.SETTING_BOUNDARIES
    assert FemaleStyleLayer.detect_emotional_state("скинь инсту свою") == FemaleEmotionalState.SETTING_BOUNDARIES

    # 2. Skeptical / assertive (accusations)
    assert FemaleStyleLayer.detect_emotional_state("ты бот или фейк? докажи") == FemaleEmotionalState.SKEPTICAL_ASSERTIVE

    # 3. Playful / teasing (flirting, banter)
    assert FemaleStyleLayer.detect_emotional_state("ты такая красивая и соблазнительная") == FemaleEmotionalState.PLAYFUL_TEASING
    assert FemaleStyleLayer.detect_emotional_state("я сейчас в ванне с пенкой лежу ахах") == FemaleEmotionalState.PLAYFUL_TEASING

    # 4. Cozy / work / tired
    assert FemaleStyleLayer.detect_emotional_state("я на работе за компом сижу, сонный капец") == FemaleEmotionalState.COZY_TIRED

    # 5. Evening / adventurous
    assert FemaleStyleLayer.detect_emotional_state("какие планы на вечер? погнали развеяться") == FemaleEmotionalState.EVENING_ADVENTUROUS


def test_female_style_bubble_post_processing():
    """Verify authentic female capitalization, parentheses, and punctuation formatting."""
    prof = FemaleCommunicationProfile(lowercase_start_rate=1.0, parentheses_rate=1.0)

    # Question marks should never be followed by parentheses
    raw_with_bad_paren = "А кем работаешь вообще?)"
    processed = FemaleStyleLayer._post_process_bubble(
        bubble=raw_with_bad_paren,
        is_first=True,
        is_last=True,
        emotional_state=FemaleEmotionalState.CURIOUS_INTERESTED,
        no_brackets=False,
        emoji_pref="medium",
        profile=prof
    )
    assert processed.endswith("?")
    assert not processed.endswith("?)")

    # Exclamation mark replacement (only 1.1% in archives, converted to casual tone)
    raw_exclamation = "Привет!"
    processed_exc = FemaleStyleLayer._post_process_bubble(
        bubble=raw_exclamation,
        is_first=True,
        is_last=True,
        emotional_state=FemaleEmotionalState.NEUTRAL_FRIENDLY,
        no_brackets=False,
        emoji_pref="medium",
        profile=prof
    )
    assert not processed_exc.endswith("!")
    assert processed_exc.endswith(")")

    # Strict no_brackets mode
    processed_no_b = FemaleStyleLayer._post_process_bubble(
        bubble="Да, всё супер) отлично)",
        is_first=True,
        is_last=True,
        emotional_state=FemaleEmotionalState.NEUTRAL_FRIENDLY,
        no_brackets=True,
        emoji_pref="medium",
        profile=prof
    )
    assert ")" not in processed_no_b
    assert "(" not in processed_no_b


# =====================================================================
# 2. PERSISTENT BEHAVIORAL MEMORY & CONTROLLED LEARNING
# =====================================================================

@pytest.mark.asyncio
async def test_controlled_feedback_learning_persistent_updates():
    """Verify that explicit user signals update Contact behavioral preferences in the database."""
    import uuid
    rand_tg_id = uuid.uuid4().int % 900000000 + 100000000
    async with async_session_maker() as session:
        user = User(telegram_id=rand_tg_id, username=f"user_{rand_tg_id}")
        session.add(user)
        await session.flush()

        contact = Contact(
            user_id=user.id,
            tg_id=rand_tg_id + 1,
            first_name="Игорь",
            preferred_response_length="long",
            no_brackets=False,
            emoji_frequency="high",
            communication_style="casual",
        )
        session.add(contact)
        await session.commit()
        await session.refresh(contact)

        # 1. User says "пиши короче" -> preferred_response_length becomes "short"
        res1 = await ControlledFeedbackLearner.analyze_and_learn_feedback(
            session, contact, "слушай, пиши короче пожалуйста"
        )
        assert res1["updated"] is True
        assert contact.preferred_response_length == "short"

        # 2. User says "не ставь скобки" -> no_brackets becomes True
        res2 = await ControlledFeedbackLearner.analyze_and_learn_feedback(
            session, contact, "хватит скобок, раздражают скобочки"
        )
        assert res2["updated"] is True
        assert contact.no_brackets is True

        # 3. User says "без смайликов" -> emoji_frequency becomes "none"
        res3 = await ControlledFeedbackLearner.analyze_and_learn_feedback(
            session, contact, "общайся без смайликов"
        )
        assert res3["updated"] is True
        assert contact.emoji_frequency == "none"

        # 4. User says "пошути, будь повеселее" -> communication_style becomes "playful"
        res4 = await ControlledFeedbackLearner.analyze_and_learn_feedback(
            session, contact, "будь повеселее, ты слишком серьезная"
        )
        assert res4["updated"] is True
        assert contact.communication_style == "playful"

        # 5. Non-feedback message produces no change
        res5 = await ControlledFeedbackLearner.analyze_and_learn_feedback(
            session, contact, "я сегодня поужинал пастой"
        )
        assert res5["updated"] is False


# =====================================================================
# 3. DAIVINCHIK PROFILE DETECTION & PARSING
# =====================================================================

def test_daivinchik_detection_and_parsing_various_formats():
    """Verify accurate detection and strict fact extraction across different Daivinchik formats."""
    # Format A: Classic Telegram Daivinchik header
    msg_a = {
        "text": (
            "Вика, 22, Москва\n"
            "Учусь на 4 курсе графического дизайна 🎨\n"
            "Люблю кофе, прогулки по набережной и выставки. Ищу интересное общение ✨"
        )
    }
    assert DaivinchikService.is_daivinchik_message(msg_a) is True
    facts_a = DaivinchikService.parse_profile(msg_a["text"])
    assert facts_a["name"] == "Вика"
    assert facts_a["age"] == 22
    assert facts_a["city"] == "Москва"
    assert facts_a["country"] == "Россия" # Inferred
    assert "кофе" in facts_a["hobbies"]
    assert "выставки" in facts_a["hobbies"]
    assert "дизайн" in facts_a["occupation"].lower()

    # Format B: Profile with Country in parentheses and height
    msg_b = {
        "text": (
            "✨ Алина, 24, Санкт-Петербург (Россия)\n"
            "Рост: 168 см\n"
            "Работаю в IT, обожаю джаз, книги и квесты 🎬"
        )
    }
    assert DaivinchikService.is_daivinchik_message(msg_b) is True
    facts_b = DaivinchikService.parse_profile(msg_b["text"])
    assert facts_b["name"] == "Алина"
    assert facts_b["age"] == 24
    assert facts_b["city"] == "Санкт-Петербург"
    assert facts_b["country"] == "Россия"
    assert facts_b["height"] == 168
    assert "квесты" in facts_b["hobbies"]

    # Format C: Forwarded message with forward_origin from leomatchbot
    msg_c = {
        "forward_origin": {
            "type": "user",
            "sender_user": {"first_name": "Дайвинчик", "username": "leomatchbot"}
        },
        "caption": "Даша, 20, Минск\nСтудентка, люблю кино"
    }
    assert DaivinchikService.is_daivinchik_message(msg_c) is True
    facts_c = DaivinchikService.parse_profile(msg_c["caption"])
    assert facts_c["name"] == "Даша"
    assert facts_c["age"] == 20
    assert facts_c["city"] == "Минск"
    assert facts_c["country"] == "Беларусь"

    # Non-dating casual message should not trigger Daivinchik detector
    msg_normal = {"text": "Привет, как твои дела? Что делаешь сегодня вечером?"}
    assert DaivinchikService.is_daivinchik_message(msg_normal) is False


# =====================================================================
# 4. BIO GENERATION & PERSONA CONSISTENCY
# =====================================================================

@pytest.mark.asyncio
async def test_daivinchik_bio_generation_consistency():
    """Verify that generated non-sensitive assumptions remain consistent across regenerations."""
    import uuid
    rand_id = uuid.uuid4().int % 900000000 + 200000000
    async with async_session_maker() as session:
        user = User(telegram_id=rand_id, username=f"user_{rand_id}")
        session.add(user)
        await session.flush()

        raw_profile = (
            "Катя, 23, Казань\n"
            "Архитектор, люблю уютные места и кофе ☕️"
        )

        # 1. Process initial forward
        prof1, is_new1 = await DaivinchikService.process_daivinchik_forward(
            session=session,
            user_id=user.id,
            raw_text=raw_profile,
            style_variant="natural"
        )
        assert is_new1 is True
        assert prof1.name == "Катя"
        assert prof1.age == 23
        assert prof1.city == "Казань"
        assert "Катя" in prof1.current_bio

        synth_details_1 = json.loads(prof1.generated_details_json)
        assert "vibe" in synth_details_1

        # 2. Process same profile again with "short" style
        prof2, is_new2 = await DaivinchikService.process_daivinchik_forward(
            session=session,
            user_id=user.id,
            raw_text=raw_profile,
            style_variant="short"
        )
        assert is_new2 is False
        assert prof2.id == prof1.id
        synth_details_2 = json.loads(prof2.generated_details_json)
        # Synthetic persona assumptions must remain identical!
        assert synth_details_1["vibe"] == synth_details_2["vibe"]
        assert prof2.style_variant == "short"

        # 3. Test playful variant
        bio_playful = DaivinchikService.generate_bio(
            facts=json.loads(prof2.facts_json),
            synth_details=synth_details_2,
            style_variant="playful"
        )
        assert "😉" in bio_playful or "ахах" in bio_playful


# =====================================================================
# 5. TELEGRAM BOT UI & KEYBOARDS
# =====================================================================

def test_daivinchik_ui_card_and_keyboards():
    """Verify card formatting and interactive buttons."""
    fake_profile = DaivinchikProfile(
        id=77,
        user_id=1,
        profile_hash="hash123",
        raw_text="...",
        name="Вика",
        age=22,
        city="Москва",
        country="Россия",
        facts_json=json.dumps({"name": "Вика", "age": 22, "city": "Москва", "country": "Россия", "hobbies": ["кофе"]}),
        generated_details_json=json.dumps({"vibe": "люблю вечерние прогулки"}),
        current_bio="Вика, 22, Москва ✨\nЛюблю вечерние прогулки.",
        style_variant="natural"
    )

    card = DaivinchikService.format_profile_card(fake_profile)
    assert "ИЗВЛЕЧЕННЫЕ ФАКТЫ" in card
    assert "Вика" in card
    assert "Москва" in card
    assert "СГЕНЕРИРОВАННАЯ АНКЕТА" in card

    kb = DaivinchikService.build_bio_actions_keyboard(77)
    flattened_cbs = [btn["callback_data"] for row in kb["inline_keyboard"] for btn in row]
    assert "daiv_regen_77" in flattened_cbs
    assert "daiv_style_77_short" in flattened_cbs
    assert "daiv_style_77_playful" in flattened_cbs
    assert "daiv_copy_77" in flattened_cbs
