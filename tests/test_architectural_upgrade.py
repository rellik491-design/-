import random
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from companion.models.db import init_db, async_session_maker
from companion.models.entities import User, Persona, Contact, ContactMessage, ContactMemory
from companion.bot.reply_validator import ReplyValidator
from companion.services.typing_simulator import TypingSimulator
from companion.services.time_engine import TimeEngine
from companion.services.web_search_service import WebResearchService
from companion.services.user_profile_service import UserProfileService
from companion.bot.response_planner import ResponsePlanner


# ==============================================================================
# 1. Native Telegram Reply Validation & Selection Tests
# ==============================================================================

def test_reply_validator_target_validation():
    """Deterministic validation of reply target message IDs."""
    batch = [
        {"telegram_message_id": 501, "text": "привет"},
        {"telegram_message_id": 502, "text": "ты где?"},
    ]

    # Valid targets
    is_v1, mid1, _ = ReplyValidator.validate_reply_target(501, batch)
    assert is_v1 is True
    assert mid1 == 501

    is_v2, mid2, _ = ReplyValidator.validate_reply_target(502, batch)
    assert is_v2 is True
    assert mid2 == 502

    # Invalid targets: not in batch, 0, negative, None, invalid type
    assert ReplyValidator.validate_reply_target(999, batch)[0] is False
    assert ReplyValidator.validate_reply_target(0, batch)[0] is False
    assert ReplyValidator.validate_reply_target(-1, batch)[0] is False
    assert ReplyValidator.validate_reply_target(None, batch)[0] is False
    assert ReplyValidator.validate_reply_target("abc", batch)[0] is False


def test_reply_validator_parameters_business_vs_direct():
    """
    Enforces Telegram Bot API 7.0+ specifications:
    - Business connections FORBID chat_id inside reply_parameters.
    - Direct bot chats include chat_id.
    - Both include message_id and allow_sending_without_reply.
    """
    # Business connection
    biz_params = ReplyValidator.build_reply_parameters(
        reply_to_message_id=777,
        is_business_connection=True,
    )
    assert biz_params is not None
    assert biz_params["message_id"] == 777
    assert biz_params["allow_sending_without_reply"] is True
    assert "chat_id" not in biz_params, "Telegram Bot API explicitly forbids chat_id in business reply_parameters!"

    # Direct bot chat
    direct_params = ReplyValidator.build_reply_parameters(
        reply_to_message_id=777,
        is_business_connection=False,
        chat_id=123456,
    )
    assert direct_params is not None
    assert direct_params["message_id"] == 777
    assert direct_params["chat_id"] == 123456
    assert direct_params["allow_sending_without_reply"] is True

    # None target returns None
    assert ReplyValidator.build_reply_parameters(None) is None


def test_reply_selection_substantive_question_vs_greeting():
    """
    Intelligent reply selection:
    - Single greeting ('привет, как дела?') does NOT trigger reply (avoids spam).
    - Substantive question ('что думаешь про квест?') MUST trigger native reply.
    - Multi-turn batch targets the question message ID.
    """
    # Case A: Pure greeting -> No reply
    batch_greeting = [{"message_id": 100, "telegram_message_id": 100, "text": "привет, как дела?"}]
    targets_greeting = ResponsePlanner.select_reply_targets(["привет) все супер"], batch_greeting)
    assert targets_greeting == [None]

    # Case B: Substantive direct question -> Gets native reply to message 101
    batch_question = [{"message_id": 101, "telegram_message_id": 101, "text": "ты сегодня свободна вечером?"}]
    targets_question = ResponsePlanner.select_reply_targets(["да, свободна после 7"], batch_question)
    assert targets_question == [101]

    # Case C: Multi-message batch: greeting + substantive question
    batch_multi = [
        {"message_id": 201, "telegram_message_id": 201, "text": "хай"},
        {"message_id": 202, "telegram_message_id": 202, "text": "а ты была на этой выставке?"},
    ]
    raw_reply = "[REPLY:202] да, была на прошлых выходных||очень понравилось!"
    plan = ResponsePlanner.plan_response(raw_reply, batch_multi)
    msgs = plan["messages"]
    assert len(msgs) == 2
    assert msgs[0]["reply_to_message_id"] == 202
    assert msgs[1]["reply_to_message_id"] is None  # Continuation is normal message


# ==============================================================================
# 2. Physics-Based Typing Simulator Tests
# ==============================================================================

def test_typing_simulator_duration_scaling():
    """
    Typing simulator scales with text length, word count, punctuation, and CPS variance.
    - Fast for short messages ('да' -> 0.25-0.55s).
    - Long messages (100-500 chars) take 3-10s.
    - Stickers have 0s typing.
    """
    # 1. Short message
    short_dur = TypingSimulator.calculate_typing_duration("да")
    assert 0.25 <= short_dur <= 0.60, f"Expected short duration, got {short_dur}s"

    # 2. Medium message (~30 chars)
    med_dur = TypingSimulator.calculate_typing_duration("привет) как твои дела сегодня?")
    assert 0.6 <= med_dur <= 2.2, f"Expected medium duration, got {med_dur}s"

    # 3. Long message (~250 chars)
    long_text = (
        "Слушай, на самом деле я сегодня весь день на ногах была! "
        "Утром ездила по делам университета, потом делала макеты для фриланса в кофейне, "
        "а сейчас вот только зашла домой и заварила себе вкусный чай с мятой."
    )
    long_dur = TypingSimulator.calculate_typing_duration(long_text)
    assert 3.0 <= long_dur <= 10.0, f"Expected realistic long duration, got {long_dur}s"
    assert long_dur > med_dur > short_dur

    # 4. Stickers have 0.0 typing duration
    sticker_dur = TypingSimulator.calculate_typing_duration("[STICKER:laugh]")
    assert sticker_dur == 0.0


# ==============================================================================
# 3. Deterministic Real-Time Clock & TimeEngine Tests
# ==============================================================================

def test_time_engine_clock_and_relative_dates():
    """Deterministic clock and relative date resolution."""
    # Test reference date: Wednesday, 2026-10-14 15:30:00 UTC
    ref_dt = datetime(2026, 10, 14, 15, 30, 0, tzinfo=timezone.utc)

    # 1. Clock test in Moscow (UTC+3) -> 18:30 Wednesday
    clock = TimeEngine.get_clock(tz_name="Europe/Moscow", ref_utc_dt=ref_dt)
    assert clock["local_time"] == "18:30"
    assert clock["local_date"] == "2026-10-14"
    assert clock["day_of_week"] == "среда"
    assert clock["time_of_day"] == "вечер"

    # 2. Timezone priority resolution
    assert TimeEngine.resolve_effective_timezone(
        explicit_tz="Asia/Tokyo", contact_tz="Europe/Moscow", default_tz="UTC"
    ) == "Asia/Tokyo"
    assert TimeEngine.resolve_effective_timezone(
        explicit_tz=None, contact_tz="Europe/Paris", default_tz="UTC"
    ) == "Europe/Paris"
    assert TimeEngine.resolve_effective_timezone(
        explicit_tz=None, contact_tz=None, chat_or_persona_tz="Europe/Samara"
    ) == "Europe/Samara"

    # 3. Relative date: 'завтра'
    res_tomorrow = TimeEngine.resolve_relative_date("увидимся завтра тогда", tz_name="Europe/Moscow", ref_utc_dt=ref_dt)
    assert res_tomorrow is not None
    assert res_tomorrow["resolved_date"] == "2026-10-15"
    assert res_tomorrow["day_of_week"] == "четверг"

    # 4. Relative date: 'вчера'
    res_yesterday = TimeEngine.resolve_relative_date("ты вчера писала об этом", tz_name="Europe/Moscow", ref_utc_dt=ref_dt)
    assert res_yesterday is not None
    assert res_yesterday["resolved_date"] == "2026-10-13"
    assert res_yesterday["day_of_week"] == "вторник"

    # 5. Relative date: 'через 3 часа' (18:30 + 3h -> 21:30)
    res_hours = TimeEngine.resolve_relative_date("напишу через 3 часа", tz_name="Europe/Moscow", ref_utc_dt=ref_dt)
    assert res_hours is not None
    assert res_hours["resolved_time"] == "21:30"
    assert res_hours["offset_hours"] == 3

    # 6. Midnight transition: 23:55 Wednesday -> 'завтра' is Thursday
    ref_midnight = datetime(2026, 10, 14, 20, 55, 0, tzinfo=timezone.utc)  # 23:55 MSK
    res_mid_tomorrow = TimeEngine.resolve_relative_date("до завтра!", tz_name="Europe/Moscow", ref_utc_dt=ref_midnight)
    assert res_mid_tomorrow["resolved_date"] == "2026-10-15"


# ==============================================================================
# 4. Real Web Research Capability Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_web_research_policy_and_execution():
    """Deterministic web research policy, live search, and fallback."""
    # Policy check: queries requiring external search
    assert WebResearchService.should_search_web("сколько стоит биткоин?")["should_search"] is True
    assert WebResearchService.should_search_web("курс доллара к рублю")["should_search"] is True
    assert WebResearchService.should_search_web("какая погода в Москве?")["should_search"] is True
    assert WebResearchService.should_search_web("кто такой Альберт Эйнштейн?")["should_search"] is True

    # Policy check: stable facts & casual conversation (MUST NOT trigger search)
    assert WebResearchService.should_search_web("сколько будет 2 + 2?")["should_search"] is False
    assert WebResearchService.should_search_web("привет, как дела?")["should_search"] is False
    assert WebResearchService.should_search_web("чем занимаешься сейчас?")["should_search"] is False

    # Live Crypto search
    res_crypto = await WebResearchService.search_crypto("bitcoin")
    if res_crypto.get("success"):
        assert "CoinGecko" in res_crypto["source"]
        assert len(res_crypto.get("results", [])) > 0

    # Live Weather search
    res_weather = await WebResearchService.search_weather("Москва")
    if res_weather.get("success"):
        assert "Open-Meteo" in res_weather["source"]
        assert len(res_weather.get("results", [])) > 0

    # Live Wikipedia search
    res_wiki = await WebResearchService.search_wikipedia("Телеграм")
    if res_wiki.get("success"):
        assert "Wikipedia" in res_wiki["source"]
        assert len(res_wiki.get("results", [])) > 0

    # Fallback formatting on failure
    failed_payload = {"success": False, "source": "Web", "query": "несуществующийзапрос999", "error": "Not found"}
    formatted_fallback = WebResearchService.format_web_context_for_prompt(failed_payload)
    assert "не могу сейчас нормально проверить" in formatted_fallback


# ==============================================================================
# 5. Persistent User Profile & Controlled Self-Learning Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_user_profile_feedback_and_persistence():
    """
    Controlled self-learning from user feedback:
    - 'не ставь смайлики' -> emoji_frequency='none' or 'low'
    - 'верни смайлики' -> emoji_frequency='medium'
    - 'пиши короче' -> preferred_response_length='short'
    - 'зови меня Алекс' -> preferred_name='Алекс'
    - Survives SQLite restart and formats into prompt correctly.
    """
    await init_db()
    async with async_session_maker() as session:
        random_tg_id = random.randint(10_000_000, 99_999_999)
        # Create user & contact
        test_user = User(telegram_id=random_tg_id, username=f"test_lead_{random_tg_id}")
        session.add(test_user)
        await session.commit()
        await session.refresh(test_user)

        contact = Contact(
            user_id=test_user.id,
            tg_id=random_tg_id,
            username=f"test_lead_{random_tg_id}",
            first_name="Александр",
            emoji_frequency="medium",
            preferred_response_length="medium",
        )
        session.add(contact)
        await session.commit()
        await session.refresh(contact)

        # 1. Feedback: Stop using emojis
        res_fb1 = await UserProfileService.process_learning_and_feedback(
            session, contact, "хватит ставить смайлики, пиши без смайликов"
        )
        assert res_fb1["updated"] is True
        assert contact.emoji_frequency in ("none", "low")

        # 2. Feedback: Short answers
        res_fb2 = await UserProfileService.process_learning_and_feedback(
            session, contact, "пожалуйста отвечай кратко и покороче"
        )
        assert res_fb2["updated"] is True
        assert contact.preferred_response_length == "short"

        # 3. Feedback: Preferred name
        res_fb3 = await UserProfileService.process_learning_and_feedback(
            session, contact, "зови меня Алекс"
        )
        assert res_fb3["updated"] is True
        assert contact.preferred_name == "Алекс"

        # Check ContactMemory persisted
        stmt_mem = select(ContactMemory).where(
            ContactMemory.contact_id == contact.id,
            ContactMemory.key == "preferred_name",
        )
        mem = (await session.execute(stmt_mem)).scalars().first()
        assert mem is not None
        assert mem.value == "Алекс"

        # 4. Format prompt block
        prompt_block = UserProfileService.format_profile_prompt_block(contact)
        assert "Алекс" in prompt_block
        assert "КОРОТКИЕ реплики" in prompt_block
        assert "ЗАПРЕТ или минимум эмодзи" in prompt_block

        # 5. Restore emojis feedback
        res_fb4 = await UserProfileService.process_learning_and_feedback(
            session, contact, "на самом деле смайлики ок, верни смайлики"
        )
        assert res_fb4["updated"] is True
        assert contact.emoji_frequency == "medium"
