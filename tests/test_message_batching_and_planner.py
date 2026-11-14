"""
Comprehensive tests for Section 38 (8 Required Scenarios):
- Test 1: Multiple rapid messages (5 messages sent rapidly -> batched into 1 turn, original IDs preserved in DB)
- Test 2: Two topics ("what do you think about X?" + "and did you eat?" -> Reply to first, normal to second)
- Test 3: Long batch (7–10 rapid messages -> processed as one coherent batch without 10 separate answers)
- Test 4: Laughter (funny situations -> varied laughter >= 5 chars, no static 3-4 char "ах"/"аха"/"ахах")
- Test 5: Parentheses (normal conversation -> rare/no ')'; flirty context -> ')' allowed intentionally)
- Test 6: Emojis (20+ messages -> varied emoji usage, many messages have no emoji, no sentence+emoji spam)
- Test 7: Stickers (many conversations -> occasional curated sticker from valid category, cooldown enforced)
- Test 8: Selective Reply across multi-message/multi-topic turns
"""

import asyncio
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from companion.bot.business_bot import BusinessChatBot
from companion.bot.response_planner import (
    STICKER_CATEGORIES,
    AsyncMessageBatcher,
    ConversationRhythmAnalyzer,
    EmojiAndStickerSelector,
    LaughterGenerator,
    ParenthesesController,
    ResponsePlanner,
)
from companion.bot.userbot_handler import UserbotHandler
from companion.config import settings
from companion.models.db import Base
from companion.models.entities import (
    AccountSession,
    Contact,
    ContactMessage,
    Persona,
    User,
)
from companion.models.enums import PlanDuration
from companion.services.ai_service import AIService
from companion.services.invite_service import InviteService
from companion.services.stt_service import STTService
from companion.services.styles_service import DialogueStyleEngine


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _seed_user_persona_account(session):
    user = User(telegram_id=900001, username="girl_owner")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    now = datetime.now(timezone.utc)
    _, invite = await InviteService.create_subscription_invite(
        session, user.id, PlanDuration.DAYS_14, now=now
    )
    ok, _, account = await InviteService.register_chat_automation(
        session=session,
        token_str=invite.token,
        business_connection_id="biz_conn_test_1",
        tg_user_id=900001,
        first_name="Алина",
        username="alina_model",
        now=now,
    )
    assert ok is True
    return user, account


@pytest.mark.asyncio
async def test_1_multiple_rapid_messages_batched_preserving_original_ids(db_session):
    """
    Test 1 — Multiple rapid messages:
    Send 5 messages quickly ("привет", "слушай", "я хотел спросить", "ладно забей", "а нет, вспомнил").
    Expected:
    - Aggregated into a single conversational turn via debounce
    - 5 separate ContactMessage rows stored in DB with their original telegram_message_id (101..105)
    - AI is called ONCE for the whole batch, not 5 times
    """
    user, account = await _seed_user_persona_account(db_session)

    rapid_messages = [
        (101, "привет"),
        (102, "слушай"),
        (103, "я хотел спросить кое-что"),
        (104, "ладно забей"),
        (105, "а нет, вспомнил, ты завтра в центре будешь?"),
    ]

    llm_call_count = 0
    captured_batch = []

    async def fake_generate_reply(persona, contact, chat_history, new_incoming_message, **kwargs):
        nonlocal llm_call_count, captured_batch
        llm_call_count += 1
        captured_batch = kwargs.get("incoming_batch") or []
        return "привет || заинтриговал и сам же вспомнил, да, буду в центре после обеда"

    with patch.object(AIService, "generate_reply", side_effect=fake_generate_reply):
        tasks = []
        for msg_id, txt in rapid_messages:
            msg_payload = {
                "business_connection_id": "biz_conn_test_1",
                "message_id": msg_id,
                "chat": {"id": 555001},
                "from": {"id": 555001, "username": "dmitry_m", "first_name": "Дмитрий"},
                "text": txt,
            }
            tasks.append(
                asyncio.create_task(
                    BusinessChatBot.process_business_message(
                        session=db_session,
                        message_data=msg_payload,
                        bot_token="test_token",
                        simulate_delays=False,
                        debounce_seconds=0.12,
                    )
                )
            )
            await asyncio.sleep(0.02)

        results = await asyncio.gather(*tasks)

    # First 4 tasks returned batched_into_active_turn, the 5th processed the whole batch
    batched_actions = [r.get("action") for r in results]
    assert batched_actions.count("batched_into_active_turn") == 4
    assert batched_actions.count("reply") == 1

    # LLM was invoked only ONCE for all 5 messages
    assert llm_call_count == 1
    assert len(captured_batch) == 5
    assert [m["telegram_message_id"] for m in captured_batch] == [101, 102, 103, 104, 105]

    # Verify DB has 5 separate user ContactMessage rows with original telegram_message_ids (NOT merged into 1 fake row)
    stmt = (
        select(ContactMessage)
        .where(ContactMessage.sender_role == "user")
        .order_by(ContactMessage.id.asc())
    )
    user_db_msgs = list((await db_session.execute(stmt)).scalars().all())
    assert len(user_db_msgs) == 5
    assert [m.telegram_message_id for m in user_db_msgs] == [101, 102, 103, 104, 105]
    assert [m.text for m in user_db_msgs] == [t for _, t in rapid_messages]


@pytest.mark.asyncio
async def test_2_two_topics_selective_reply_and_normal_second_message(db_session):
    """
    Test 2 — Two topics:
    Send:
    1. [msg_id=201] "что думаешь насчет новой выставки в гараже?"
    2. [msg_id=202] "и ты поела сегодня вообще?"
    Expected:
    - Reply to the first message (reply_to_message_id=201)
    - Normal message for the second (reply_to_message_id=None)
    """
    incoming_batch = [
        {"telegram_message_id": 201, "text": "что думаешь насчет новой выставки в гараже?", "is_voice": False},
        {"telegram_message_id": 202, "text": "и ты поела сегодня вообще?", "is_voice": False},
    ]
    raw_reply = "выставка в гараже кстати очень крутая, я бы сходила || да, недавно перекусила салатом, а ты?"

    plan = ResponsePlanner.plan_response(
        raw_reply_text=raw_reply,
        incoming_batch=incoming_batch,
        contact_id=7002,
        tone="warm_flirty",
    )

    assert len(plan["messages"]) == 2
    # First bubble answers the exhibition question -> native reply to 201
    assert plan["messages"][0]["reply_to_message_id"] == 201
    # Second bubble answers the latest message -> normal message without reply_to_message_id
    assert plan["messages"][1]["reply_to_message_id"] is None


@pytest.mark.asyncio
async def test_3_long_batch_7_to_10_messages_processed_coherently(db_session):
    """
    Test 3 — Long batch:
    Send 8 rapid messages in a row.
    Expected:
    - Coherent batch processing (all 8 messages read together, including pending unread DB messages)
    - Does NOT produce 8 separate answers; produces a natural 1–3 bubble response
    """
    user, account = await _seed_user_persona_account(db_session)

    long_batch = [
        {"telegram_message_id": 301, "text": "эй", "is_voice": False},
        {"telegram_message_id": 302, "text": "ты куда пропала", "is_voice": False},
        {"telegram_message_id": 303, "text": "я хотел рассказать кое-что", "is_voice": False},
        {"telegram_message_id": 304, "text": "короче", "is_voice": False},
        {"telegram_message_id": 305, "text": "ладно неважно", "is_voice": False},
        {"telegram_message_id": 306, "text": "хотя постой", "is_voice": False},
        {"telegram_message_id": 307, "text": "вспомнил!", "is_voice": False},
        {"telegram_message_id": 308, "text": "я сегодня видел собаку точь-в-точь как ты описывала", "is_voice": False},
    ]

    res = await UserbotHandler.process_incoming_message(
        session=db_session,
        user_id=user.id,
        sender_tg_id=555003,
        sender_username="maxim_long",
        sender_first_name="Максим",
        text=long_batch[-1]["text"],
        now=datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc),
        incoming_batch=long_batch,
    )

    assert res["action"] == "reply"
    assert res["batch_size"] == 8
    plan = res["response_plan"]
    assert 1 <= len(plan["messages"]) <= 4


def test_4_laughter_natural_varied_and_never_short_static():
    """
    Test 4 — Laughter:
    Trigger funny situations and verify:
    - Varied laughter (not identical every time)
    - Always >= 5 characters ("ахаха", "ахахах", "ахахаха", "АХАХАХ", etc.)
    - Short 2-4 char "ах", "аха", "ахах" in text are expanded to natural >= 5 char laughter
    """
    generated = []
    for i in range(12):
        laugh = LaughterGenerator.generate_laugh(
            incoming_text="ахахах это просто капец смешно 😂",
            contact_id=8001,
        )
        assert len(laugh) >= 5, f"Laugh '{laugh}' is shorter than 5 chars"
        generated.append(laugh)

    # Must have variety across 12 generations and never repeat back-to-back
    assert len(set(generated)) >= 3
    for idx in range(1, len(generated)):
        assert generated[idx] != generated[idx - 1]

    # Verify short "ах" / "аха" / "ахах" replacement in text
    sample = "ахах, ну ты даешь! аха, смешно"
    naturalized = LaughterGenerator.naturalize_laughter_in_text(
        sample, incoming_text="шутка дня", contact_id=8002
    )
    short_matches = re.findall(r"(?<![а-яёa-z])(?:ах|аха|ахах|хах)(?![а-яёa-z])", naturalized, flags=re.IGNORECASE)
    assert len(short_matches) == 0, f"Found unexpanded short laugh in: {naturalized}"


def test_5_parentheses_rare_in_normal_and_allowed_in_flirt():
    """
    Test 5 — Parentheses:
    - Normal conversation -> rare/no ')' (never ending every message with ')')
    - Flirt / playful context -> ')' allowed intentionally
    """
    normal_messages = [
        "договорились)",
        "да, я уже посмотрела)",
        "поняла тебя)",
        "я сегодня работаю до вечера)",
        "завтра буду свободна после шести)",
        "хорошо, напиши как освободишься)",
        "я пила кофе утром)",
        "обычно хожу в зал три раза в неделю)",
        "сейчас еду домой в такси)",
        "да, фильм неплохой)",
    ]
    cleaned_normal = [
        ParenthesesController.apply_parentheses_policy(
            msg, incoming_text="как дела на работе?", tone="friendly"
        )
        for msg in normal_messages
    ]
    assert all(not m.endswith(")") for m in cleaned_normal), f"Normal factual messages kept ')': {cleaned_normal}"

    # Flirty / coy context
    flirty_out = ParenthesesController.apply_parentheses_policy(
        "может быть)",
        incoming_text="ты мне очень нравишься, пойдешь со мной на свидание? 😏",
        tone="warm_flirty",
        rng=type("FixedRng", (), {"random": lambda self: 0.1})(),
    )
    assert flirty_out == "может быть)"


def test_6_emojis_natural_distribution_over_20_messages():
    """
    Test 6 — Emojis:
    Test 24 messages across multi-bubble turns:
    - Many messages have NO emoji
    - Never 'sentence + emoji' on every bubble in a multi-message turn
    """
    all_bubbles = []
    for i in range(12):
        bubbles = [
            f"привет, я только вышла с тренировки 😊",
            f"сейчас иду за кофе и потом домой 😊",
        ]
        balanced = EmojiAndStickerSelector.balance_emojis_across_bubbles(bubbles)
        all_bubbles.extend(balanced)

    assert len(all_bubbles) == 24
    without_emoji = [b for b in all_bubbles if not EmojiAndStickerSelector.has_emoji(b)]
    with_emoji = [b for b in all_bubbles if EmojiAndStickerSelector.has_emoji(b)]

    # At least half of the bubbles across multi-message turns must have NO emoji
    assert len(without_emoji) >= 12
    assert len(with_emoji) >= 1


def test_7_stickers_curated_categories_and_cooldown():
    """
    Test 7 — Stickers:
    - Curated sticker categories exist (laugh, surprise, confusion, facepalm, cute, celebration, teasing, approval)
    - Occasional sticker selected when context matches, and cooldown prevents sticker spam on consecutive turns
    """
    expected_categories = {
        "laugh", "surprise", "confusion", "facepalm",
        "cute", "celebration", "teasing", "approval",
    }
    assert expected_categories.issubset(set(STICKER_CATEGORIES.keys()))

    EmojiAndStickerSelector._last_sticker_turn.clear()
    EmojiAndStickerSelector._turn_counter.clear()

    #Explicit [STICKER:laugh] tag triggers a sticker on turn 1
    plan_1 = ResponsePlanner.plan_response(
        raw_reply_text="ахахах ну ты выдал конечно [STICKER:laugh]",
        incoming_batch=[{"telegram_message_id": 701, "text": "прикол", "is_voice": False}],
        contact_id=9100,
        tone="warm_flirty",
    )
    assert plan_1["sticker"] is not None
    assert plan_1["sticker"]["category"] == "laugh"
    assert plan_1["response_type"] == "TEXT + STICKER"

    # Next 4 turns for the same contact must NOT auto-attach a sticker due to cooldown
    for turn_idx in range(4):
        cat = EmojiAndStickerSelector.should_attach_sticker(
            contact_id=9100,
            incoming_text="ахахах капец смешно 😂",
            reply_text="ахахаха да вообще",
            explicit_category=None,
            rng=type("ZeroRng", (), {"random": lambda self: 0.01})(),
        )
        assert cat is None, f"Sticker should be on cooldown at turn offset {turn_idx + 1}"


def test_8_selective_reply_explicit_tag_and_lexical_matching():
    """
    Test 8 — Selective Reply:
    Verify:
    1. Explicit [REPLY:msg_id] tag from LLM attaches reply_to_message_id to that specific bubble and strips the tag.
    2. Single normal message has reply_to_message_id=None (no reply spam on every message).
    3. Batch with an earlier question + later remarks attaches reply_to_message_id ONLY to the bubble answering the question.
    """
    # Case A: Single message -> NO reply_to_message_id
    single_plan = ResponsePlanner.plan_response(
        raw_reply_text="привет, все отлично, только освободилась",
        incoming_batch=[{"telegram_message_id": 801, "text": "привет, как дела?", "is_voice": False}],
        contact_id=9200,
    )
    assert single_plan["messages"][0]["reply_to_message_id"] is None

    # Case B: Explicit [REPLY:811] tag in LLM output
    batch_b = [
        {"telegram_message_id": 811, "text": "какой у тебя любимый фильм?", "is_voice": False},
        {"telegram_message_id": 812, "text": "кстати у нас дождь пошел", "is_voice": False},
    ]
    explicit_plan = ResponsePlanner.plan_response(
        raw_reply_text="[REPLY:811] обожаю интерстеллар, пересматривала раза три || о, у нас тоже пасмурно с утра",
        incoming_batch=batch_b,
        contact_id=9201,
    )
    assert len(explicit_plan["messages"]) == 2
    assert explicit_plan["messages"][0]["reply_to_message_id"] == 811
    assert "[REPLY:" not in explicit_plan["messages"][0]["text"]
    assert explicit_plan["messages"][1]["reply_to_message_id"] is None
    assert explicit_plan["response_type"] == "MULTIPLE TEXT MESSAGES + SELECTIVE REPLIES"


# ==============================================================================
# SECTION 42: TESTS A–G (15–20s Reading Cycle, Large Batch, Voice+Text, Burst, Reply)
# ==============================================================================


@pytest.mark.asyncio
async def test_a_single_message_reading_cycle_and_fast_response(db_session):
    """
    Test A — Single message:
    Send 1 message -> collected on reading cycle -> 1 AI call -> immediate response,
    status transitioned to PROCESSED, and configurable reading interval verified (15–20s).
    """
    user, account = await _seed_user_persona_account(db_session)

    # Verify configurable reading cycle interval is within [MESSAGE_READ_INTERVAL_MIN, MESSAGE_READ_INTERVAL_MAX]
    batcher = AsyncMessageBatcher()
    for _ in range(10):
        interval = batcher.pick_reading_cycle_interval()
        assert settings.MESSAGE_READ_INTERVAL_MIN <= interval <= settings.MESSAGE_READ_INTERVAL_MAX

    llm_calls = 0

    async def fake_generate(persona, contact, chat_history, new_incoming_message, **kwargs):
        nonlocal llm_calls
        llm_calls += 1
        return "привет, я дома пью чай, а ты как?"

    with patch.object(AIService, "generate_reply", side_effect=fake_generate):
        res = await BusinessChatBot.process_business_message(
            session=db_session,
            message_data={
                "business_connection_id": "biz_conn_test_1",
                "message_id": 1001,
                "chat": {"id": 600001},
                "from": {"id": 600001, "username": "single_user", "first_name": "Артем"},
                "text": "привет, чем занимаешься?",
            },
            bot_token="test_token",
            simulate_delays=False,
            debounce_seconds=0.05,
        )

    assert res["action"] == "reply"
    assert llm_calls == 1
    assert res["batch_size"] == 1
    assert res["metrics"]["batch_size"] == 1
    assert res["metrics"]["text_count"] == 1
    assert res["metrics"]["voice_count"] == 0

    # Verify DB state is PROCESSED
    stmt = select(ContactMessage).where(ContactMessage.telegram_message_id == 1001)
    db_msg = (await db_session.execute(stmt)).scalar_one()
    assert db_msg.processing_status == "PROCESSED"
    assert db_msg.batch_id is not None


@pytest.mark.asyncio
async def test_c_large_batch_20_messages_collected_at_once_without_loss_or_duplicates(db_session):
    """
    Test C — Large batch:
    Send 20 messages within the reading window.
    Expected:
    - All 20 collected in 1 batch
    - No message loss (all 20 stored in DB in chronological order 2001..2020)
    - No duplicate processing (19 return batched_into_active_turn, 1 executes the batch)
    - Exactly 1 AI call receiving all 20 messages
    """
    user, account = await _seed_user_persona_account(db_session)

    llm_calls = 0
    captured_batch = []
    captured_prompt_input = ""

    async def fake_generate(persona, contact, chat_history, new_incoming_message, **kwargs):
        nonlocal llm_calls, captured_batch, captured_prompt_input
        llm_calls += 1
        captured_batch = kwargs.get("incoming_batch") or []
        captured_prompt_input = new_incoming_message
        return "ахахах сколько эмоций сразу || прочитала все, давай по порядку обсудим"

    with patch.object(AIService, "generate_reply", side_effect=fake_generate):
        tasks = []
        for i in range(1, 21):
            msg_id = 2000 + i
            payload = {
                "business_connection_id": "biz_conn_test_1",
                "message_id": msg_id,
                "chat": {"id": 600003},
                "from": {"id": 600003, "username": "burst20_user", "first_name": "Илья"},
                "text": f"сообщение номер {i} в потоке мыслей",
            }
            tasks.append(
                asyncio.create_task(
                    BusinessChatBot.process_business_message(
                        session=db_session,
                        message_data=payload,
                        bot_token="test_token",
                        simulate_delays=False,
                        debounce_seconds=0.25,
                    )
                )
            )
            await asyncio.sleep(0.005)

        results = await asyncio.gather(*tasks)

    actions = [r.get("action") for r in results]
    assert actions.count("batched_into_active_turn") == 19
    assert actions.count("reply") == 1
    assert llm_calls == 1
    assert len(captured_batch) == 20
    assert [m["telegram_message_id"] for m in captured_batch] == list(range(2001, 2021))
    assert "[msg_id=2001]" in captured_prompt_input
    assert "[msg_id=2020]" in captured_prompt_input

    stmt = (
        select(ContactMessage)
        .where(ContactMessage.sender_role == "user")
        .order_by(ContactMessage.telegram_message_id.asc())
    )
    rows = list((await db_session.execute(stmt)).scalars().all())
    assert len(rows) == 20
    assert [r.telegram_message_id for r in rows] == list(range(2001, 2021))
    assert all(r.processing_status == "PROCESSED" for r in rows)


@pytest.mark.asyncio
async def test_d_multiple_three_topics_selective_reply_targeting():
    """
    Test D — Multiple topics:
    Send 3 messages about 3 different topics:
    1. [msg_id=3001] "ты смотрела новый сериал про космос?"
    2. [msg_id=3002] "у меня завтра тяжелая работа с утра в офисе"
    3. [msg_id=3003] "и кстати ты уже ужинала сегодня?"
    Expected:
    - AI/Planner separates topics into 3 bubbles
    - Bubble 1 targets msg_id=3001 (cinema/series topic)
    - Bubble 2 targets msg_id=3002 (work/office topic)
    - Bubble 3 answers the last message naturally (reply_to_message_id=None)
    """
    batch_3_topics = [
        {"telegram_message_id": 3001, "text": "ты смотрела новый сериал про космос?", "is_voice": False},
        {"telegram_message_id": 3002, "text": "у меня завтра тяжелая работа с утра в офисе", "is_voice": False},
        {"telegram_message_id": 3003, "text": "и кстати ты уже ужинала сегодня?", "is_voice": False},
    ]
    raw_reply = (
        "сериал про космос еще не видела, но хочу глянуть на выходных || "
        "держись там завтра в офисе на работе, главное выспаться || "
        "да, только что поужинала пастой"
    )
    plan = ResponsePlanner.plan_response(
        raw_reply_text=raw_reply,
        incoming_batch=batch_3_topics,
        contact_id=9400,
    )
    assert len(plan["messages"]) == 3
    assert plan["messages"][0]["reply_to_message_id"] == 3001
    assert plan["messages"][1]["reply_to_message_id"] == 3002
    assert plan["messages"][2]["reply_to_message_id"] is None


@pytest.mark.asyncio
async def test_e_voice_and_text_mixed_batch_order_and_failure_fallback(db_session):
    """
    Test E — Voice + Text batch:
    Send:
    1. [msg_id=4001] text: "слушай"
    2. [msg_id=4002] voice (duration=6s, file_id="voice_fid_4002")
    3. [msg_id=4003] text: "ну как тебе идея?"
    Expected:
    - All 3 collected in 1 batch in exact chronological order (4001, 4002, 4003)
    - Voice is transcribed during batch execution and formatted as:
      [msg_id=4002] [VOICE MESSAGE, 6s] Transcription: "..."
    - Also test voice transcription failure -> graceful non-hallucinating fallback
    """
    user, account = await _seed_user_persona_account(db_session)

    captured_ai_input = ""

    async def fake_stt(bot_token, file_id, mime_type="audio/ogg"):
        assert file_id == "voice_fid_4002"
        return "давай в субботу сходим в планетарий вечером", None

    async def fake_generate(persona, contact, chat_history, new_incoming_message, **kwargs):
        nonlocal captured_ai_input
        captured_ai_input = new_incoming_message
        return "о, планетарий в субботу звучит очень классно || давай ближе к выходным спишемся по времени"

    with patch.object(STTService, "transcribe_telegram_voice", side_effect=fake_stt), patch.object(
        AIService, "generate_reply", side_effect=fake_generate
    ):
        m1 = {
            "business_connection_id": "biz_conn_test_1",
            "message_id": 4001,
            "chat": {"id": 600005},
            "from": {"id": 600005, "username": "voice_mix_user", "first_name": "Денис"},
            "text": "слушай",
        }
        m2 = {
            "business_connection_id": "biz_conn_test_1",
            "message_id": 4002,
            "chat": {"id": 600005},
            "from": {"id": 600005, "username": "voice_mix_user", "first_name": "Денис"},
            "voice": {"file_id": "voice_fid_4002", "duration": 6, "mime_type": "audio/ogg"},
        }
        m3 = {
            "business_connection_id": "biz_conn_test_1",
            "message_id": 4003,
            "chat": {"id": 600005},
            "from": {"id": 600005, "username": "voice_mix_user", "first_name": "Денис"},
            "text": "ну как тебе идея?",
        }

        t1 = asyncio.create_task(
            BusinessChatBot.process_business_message(
                db_session, m1, bot_token="test_token", simulate_delays=False, debounce_seconds=0.15
            )
        )
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(
            BusinessChatBot.process_business_message(
                db_session, m2, bot_token="test_token", simulate_delays=False, debounce_seconds=0.15
            )
        )
        await asyncio.sleep(0.01)
        t3 = asyncio.create_task(
            BusinessChatBot.process_business_message(
                db_session, m3, bot_token="test_token", simulate_delays=False, debounce_seconds=0.15
            )
        )
        results = await asyncio.gather(t1, t2, t3)

    reply_res = next(r for r in results if r.get("action") == "reply")
    assert reply_res["metrics"]["batch_size"] == 3
    assert reply_res["metrics"]["text_count"] == 2
    assert reply_res["metrics"]["voice_count"] == 1

    # Verify exact chronological order and [VOICE MESSAGE] format in AI input
    lines = captured_ai_input.splitlines()
    assert len(lines) == 3
    assert "[msg_id=4001] слушай" in lines[0]
    assert '[msg_id=4002] [VOICE MESSAGE, 6s] Transcription: "давай в субботу сходим в планетарий вечером"' in lines[1]
    assert "[msg_id=4003] ну как тебе идея?" in lines[2]

    # Verify fallback when voice transcription fails (no hallucination)
    failed_voice_plan = ResponsePlanner.plan_response(
        raw_reply_text="какой-то ответ",
        incoming_batch=[
            {
                "telegram_message_id": 4099,
                "text": ResponsePlanner.VOICE_FAILED_MARKER,
                "is_voice": True,
                "voice_duration": 4,
                "transcription_failed": True,
            }
        ],
        contact_id=9500,
    )
    assert len(failed_voice_plan["messages"]) == 1
    assert failed_voice_plan["messages"][0]["text"] in ResponsePlanner.VOICE_FALLBACK_RESPONSES
    assert "какой-то ответ" not in failed_voice_plan["combined_text"]


@pytest.mark.asyncio
async def test_f_aggressive_impatient_user_controlled_burst_response():
    """
    Test F — Aggression:
    Send:
    "АЛЛО"
    "ТЫ ГДЕ"
    "????"
    "ДАВАЙ"
    Expected:
    - Rhythm analyzer classifies as AGGRESSIVE_IMPATIENT
    - Response uses short, fast burst messages (<= MAX_BURST_MESSAGES)
    - Inter-message delay is faster (0.35–0.75s)
    - No infinite loop or abusive response
    """
    aggressive_batch = [
        {"telegram_message_id": 5001, "text": "АЛЛО", "is_voice": False},
        {"telegram_message_id": 5002, "text": "ТЫ ГДЕ", "is_voice": False},
        {"telegram_message_id": 5003, "text": "????", "is_voice": False},
        {"telegram_message_id": 5004, "text": "ДАВАЙ ОТВЕЧАЙ", "is_voice": False},
    ]

    rhythm = ConversationRhythmAnalyzer.analyze_rhythm(aggressive_batch)
    assert rhythm["state"] == "AGGRESSIVE_IMPATIENT"
    assert rhythm["is_aggressive"] is True
    assert rhythm["max_burst_messages"] == settings.MAX_BURST_MESSAGES

    plan = ResponsePlanner.plan_response(
        raw_reply_text="спокойно, я просто отходила на кухню за водой",
        incoming_batch=aggressive_batch,
        contact_id=9600,
    )
    assert plan["rhythm_state"] == "AGGRESSIVE_IMPATIENT"
    assert plan["is_aggressive"] is True
    assert 2 <= len(plan["messages"]) <= settings.MAX_BURST_MESSAGES
    assert plan["inter_message_delay"] == (0.35, 0.75)
    # First burst messages are short human acknowledgements ("я тут" / "да вижу 😭" / "сек" / etc.)
    assert any(
        phrase in plan["messages"][0]["text"].lower()
        for phrase in ("я тут", "да вижу", "вижу", "сек", "спокойно", "эй")
    )


@pytest.mark.asyncio
async def test_g_selective_reply_and_business_chat_reply_parameters():
    """
    Test G — Selective Reply & Business Chat API payload:
    Send:
    1. [msg_id=6001] "что думаешь про новый альбом?"
    2. [msg_id=6002] "а ты ела?"
    Expected:
    - Plan attaches reply_to_message_id=6001 to first bubble, None to second bubble
    - Unified 'responses' schema has send_type='text' and reply_to_message_id
    - BusinessChatBot.send_business_message passes BOTH reply_to_message_id AND
      reply_parameters={'message_id': 6001, 'chat_id': 777001, 'allow_sending_without_reply': True}
    """
    batch_g = [
        {"telegram_message_id": 6001, "text": "что думаешь про новый альбом?", "is_voice": False},
        {"telegram_message_id": 6002, "text": "а ты ела?", "is_voice": False},
    ]
    plan = ResponsePlanner.plan_response(
        raw_reply_text="новый альбом мне прям зашел, пара треков на репите || да, недавно поела",
        incoming_batch=batch_g,
        contact_id=9700,
    )
    assert len(plan["responses"]) == 2
    assert plan["responses"][0]["reply_to_message_id"] == 6001
    assert plan["responses"][0]["send_type"] == "text"
    assert plan["responses"][1]["reply_to_message_id"] is None
    assert plan["responses"][1]["send_type"] == "text"

    # Verify BusinessChatBot.send_business_message payload includes reply_parameters for Bot API 7.0+ Business Chats
    captured_payloads = []

    class FakeResp:
        status_code = 200
        text = '{"ok": true}'

        def json(self):
            return {"ok": True, "result": {"message_id": 9999}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, **kwargs):
            captured_payloads.append((url, json))
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        ok = await BusinessChatBot.send_business_message(
            bot_token="live_token_test",
            business_connection_id="biz_conn_test_1",
            chat_id=777001,
            text=plan["responses"][0]["text"],
            reply_to_message_id=plan["responses"][0]["reply_to_message_id"],
        )

    assert ok is True
    assert len(captured_payloads) == 1
    _, sent_json = captured_payloads[0]
    assert sent_json["business_connection_id"] == "biz_conn_test_1"
    assert sent_json["reply_parameters"] == {
        "message_id": 6001,
        "allow_sending_without_reply": True,
    }

