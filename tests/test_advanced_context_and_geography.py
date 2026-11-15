import pytest
import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.entities import Base, User, Persona, Contact, ContactMessage, ContactMemory
from companion.services.user_geography_service import UserGeographyService, UserGeoProfile
from companion.services.message_ownership_service import MessageOwnershipService
from companion.services.presend_validator import PreSendValidator
from companion.ai.context_manager import ContextManager
from companion.ai.memory_manager import MemoryManager
from companion.bot.userbot_handler import UserbotHandler


@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_immigrant_geography_separation(async_session: AsyncSession):
    """
    Sections 1-4:
    User: "я родом из Украины, но сейчас живу в Лондоне"
    Must set:
      origin_country = Ukraine
      current_country = United Kingdom
      current_city = London
      timezone = Europe/London
    Must NEVER set:
      country = Ukraine, city = London
    """
    user = User(telegram_id=1001, username="test_immigrant")
    async_session.add(user)
    await async_session.commit()

    contact = Contact(user_id=user.id, tg_id=1001, first_name="Alex", username="alex_uk")
    async_session.add(contact)
    await async_session.commit()

    text = "я родом из Украины, но сейчас живу в Лондоне"
    profile, updated = UserGeographyService.analyze_and_update_geography(contact, text)

    assert updated is True
    assert profile.origin_country == "Ukraine"
    assert profile.current_country == "United Kingdom"
    assert profile.current_city == "London"
    assert profile.timezone == "Europe/London"

    # Verify contact fields
    assert contact.origin_country == "Ukraine"
    assert contact.current_country == "United Kingdom"
    assert contact.current_city == "London"
    assert contact.detected_city == "London"
    assert contact.detected_timezone == "Europe/London"
    # Never confuse origin as current country
    assert contact.current_country != "Ukraine"


@pytest.mark.asyncio
async def test_timezone_strictly_follows_current_location(async_session: AsyncSession):
    """
    Section 6 & 7:
    Timezone follows current location, not origin or language.
    Ukrainian speaker living in London gets Europe/London timezone.
    """
    contact = Contact(user_id=1, tg_id=1002, first_name="Oksana", language="uk")
    text = "я українка, живу в Лондоні"
    profile, updated = UserGeographyService.analyze_and_update_geography(contact, text)

    assert profile.current_city == "London"
    assert profile.timezone == "Europe/London"
    assert profile.timezone != "Europe/Kyiv"


@pytest.mark.asyncio
async def test_temporal_location_validity_travel_and_return(async_session: AsyncSession):
    """
    Section 5 & 21:
    Location has temporal validity.
    London resident says: "я сейчас в Париже" -> temporary_location = Paris.
    Later says: "вернулся домой в Лондон" -> temporary cleared, current = London.
    """
    contact = Contact(user_id=1, tg_id=1003, current_country="United Kingdom", current_city="London", home_city="London")
    
    # 1. User travels to Paris
    prof1, upd1 = UserGeographyService.analyze_and_update_geography(contact, "я сейчас в Париже на пару дней")
    assert upd1 is True
    assert prof1.temporary_location == "Paris"
    assert contact.temporary_location == "Paris"
    # Home remains London
    assert contact.home_city == "London"
    assert prof1.current_city == "Paris"

    # 2. User returns home to London
    prof2, upd2 = UserGeographyService.analyze_and_update_geography(contact, "вернулся домой в Лондон")
    assert upd2 is True
    assert prof2.temporary_location is None
    assert contact.temporary_location is None
    assert prof2.current_city == "London"
    assert prof2.timezone == "Europe/London"


@pytest.mark.asyncio
async def test_spatial_reference_resolution():
    """
    Section 31 & 32:
    Resolve "дома", "у нас там" contextually based on origin vs current location.
    """
    prof = UserGeoProfile(
        origin_country="Ukraine",
        origin_city="Kyiv",
        current_country="United Kingdom",
        current_city="London",
        home_city="London",
        timezone="Europe/London",
    )

    # "у нас там сейчас холодно" refers to origin (Ukraine / Kyiv)
    ref1 = UserGeographyService.resolve_spatial_reference("у нас там сейчас холодно", prof)
    assert ref1["resolved_target"] in ("Kyiv", "Ukraine")

    # "я дома на диване" refers to current residence (London)
    ref2 = UserGeographyService.resolve_spatial_reference("я дома на диване лежу", prof)
    assert ref2["resolved_target"] == "London"


@pytest.mark.asyncio
async def test_correction_memory_priority(async_session: AsyncSession):
    """
    Section 18.E & 30:
    User corrects: "нет, я не из Киева, я из Львова"
    Must record correction event, confidence 0.98, and update origin to Lviv.
    """
    user = User(telegram_id=1004, username="correction_user")
    async_session.add(user)
    await async_session.commit()

    contact = Contact(user_id=user.id, tg_id=1004, detected_city="Киев", origin_city="Киев")
    async_session.add(contact)
    await async_session.commit()

    # Memory extraction
    await MemoryManager.extract_and_store_memories(
        session=async_session,
        contact=contact,
        incoming_text="нет, я не из Киева, я из Львова",
    )

    corrections = contact.get_correction_events()
    assert len(corrections) > 0
    assert any("львов" in str(c).lower() or "киев" in str(c).lower() or "lviv" in str(c).lower() for c in corrections)
    assert contact.origin_city in ("Lviv", "Львов")


@pytest.mark.asyncio
async def test_message_ownership_hard_self_response_block(async_session: AsyncSession):
    """
    Sections 10-13:
    Bot must NEVER respond to its own messages.
    If message author is BOT_ID or classified as BOT:
      - Dropped immediately
      - Registered as outgoing
      - No AI planning triggered
    """
    bot_id = 999888777
    MessageOwnershipService.set_bot_identity(bot_id=bot_id, bot_username="my_companion_bot")

    # 1. Classification check
    assert MessageOwnershipService.classify_message(bot_id) == "BOT"
    assert MessageOwnershipService.classify_message(123456) == "USER"

    # 2. In-memory outgoing registration
    MessageOwnershipService.register_outgoing_message_memory(chat_id=123, message_id=555)
    assert MessageOwnershipService.is_bot_outgoing(555) is True
    assert MessageOwnershipService.is_bot_outgoing(556) is False

    # 3. Batch filtering: bot messages removed from user batch
    raw_batch = [
        {"sender_id": 123456, "text": "привет"},
        {"sender_id": bot_id, "text": "привет :)"},
        {"sender_id": 123456, "text": "как дела?"},
    ]
    filtered = MessageOwnershipService.filter_user_messages_for_batch(raw_batch, bot_tg_id=bot_id)
    assert len(filtered) == 2
    assert all(m["sender_id"] != bot_id for m in filtered)


@pytest.mark.asyncio
async def test_presend_validator_protection():
    """
    Section 26: Pre-send 10-check validation layer.
    """
    internal_ctx = {
        "conversation": {"chat_id": 1005, "active_thread": "relocation_immigrant"},
        "user": {
            "origin_country": "Ukraine",
            "current_country": "United Kingdom",
            "current_city": "London",
            "timezone": "Europe/London",
        },
        "language": {"current": "ru", "confidence": 0.95},
        "memory": {"corrections": ["не из Киева, а из Львова"]},
        "message_control": {"bot_must_not_answer_own_message": True},
    }

    # 1. Candidate trying to answer a BOT message -> Rejected
    ok1, reason1, _ = PreSendValidator.validate_presend(
        candidate_reply="привет",
        internal_context=internal_ctx,
        target_sender_type="BOT",
    )
    assert ok1 is False
    assert reason1 == "target_is_bot"

    # 2. Candidate confusing origin with current location -> Sanitized to London
    ok2, _, sanitized2 = PreSendValidator.validate_presend(
        candidate_reply="ты сейчас в Украине как поживаешь?",
        internal_context=internal_ctx,
        target_sender_type="USER",
        chat_id=1005,
    )
    assert ok2 is True
    assert "ты же сейчас в London" in sanitized2 or "ты сейчас в Украине" not in sanitized2

    # 3. Candidate repeating contradicted assumption (Kyiv instead of Lviv) -> Sanitized
    ok3, _, sanitized3 = PreSendValidator.validate_presend(
        candidate_reply="привет! ты же из Киева вроде?",
        internal_context=internal_ctx,
        target_sender_type="USER",
        chat_id=1005,
    )
    assert ok3 is True
    assert "из киева" not in sanitized3.lower()


@pytest.mark.asyncio
async def test_context_manager_builds_full_internal_context(async_session: AsyncSession):
    """
    Sections 9 & 35:
    ContextManager builds internal_context answering WHO, WHERE, FROM WHERE, WHEN, WHAT LANGUAGE, WHAT TOPIC.
    """
    user = User(telegram_id=1006, username="user_context_test")
    async_session.add(user)
    persona = Persona(user_id=1, name="Настя", city="Москва")
    async_session.add(persona)
    await async_session.commit()

    contact = Contact(
        user_id=user.id,
        tg_id=1006,
        first_name="Denis",
        origin_country="Ukraine",
        origin_city="Одесса",
        current_country="Poland",
        current_city="Варшава",
        detected_timezone="Europe/Warsaw",
        language="uk",
    )
    async_session.add(contact)
    await async_session.commit()

    pkg = await ContextManager.build_seven_level_context(
        persona=persona,
        contact=contact,
        new_incoming_message="ми з друзями шукаємо нормальну кав'ярню у Варшаві",
        session=async_session,
    )

    internal_ctx = pkg.get("internal_context")
    assert internal_ctx is not None
    assert internal_ctx["user"]["origin_city"] == "Одесса"
    assert internal_ctx["user"]["current_city"] == "Варшава"
    assert internal_ctx["temporal_context"]["timezone"] == "Europe/Warsaw"
    assert internal_ctx["message_control"]["bot_must_not_answer_own_message"] is True
    assert pkg["active_thread"] in ("dating_meetup", "casual_lifestyle")


@pytest.mark.asyncio
async def test_casual_residence_phrases_and_dict_geo_safety():
    """
    Casual residence phrases like 'я в Лондоне' or 'я сейчас в Варшаве' must be captured,
    and format_geo_prompt_block must safely accept a dict from contact.get_geo_profile().
    """
    c1 = Contact(user_id=1, tg_id=2001)
    prof1, upd1 = UserGeographyService.analyze_and_update_geography(c1, "да я в Лондоне сейчас")
    assert upd1 is True
    assert prof1.current_city == "London"
    assert c1.detected_city == "London"
    assert c1.detected_timezone == "Europe/London"

    persona = Persona(user_id=1, name="Алина", city="Москва")
    geo_dict = c1.get_geo_profile()
    assert isinstance(geo_dict, dict)
    block = UserGeographyService.format_geo_prompt_block(geo_dict, persona)
    assert "London" in block


@pytest.mark.asyncio
async def test_periodic_llm_memory_refresh(async_session: AsyncSession, monkeypatch):
    """
    MemoryManager.refresh_memory_with_llm consolidates where the user lives,
    where they are from, and persona self-facts into Contact + ContactMemory.
    """
    user = User(telegram_id=3001, username="mem_refresh")
    async_session.add(user)
    await async_session.commit()

    persona = Persona(user_id=user.id, name="Алина", city="Москва")
    contact = Contact(user_id=user.id, tg_id=3002, first_name="Игорь")
    async_session.add_all([persona, contact])
    await async_session.commit()

    from companion.ai.gemini_client import GeminiClient
    monkeypatch.setattr(GeminiClient, "get_api_keys", classmethod(lambda cls, explicit_key=None: ["fake-key"]))

    async def fake_generate(*args, **kwargs):
        return (
            '{"user_current_city": "Варшава", "user_current_country": "Poland", '
            '"user_origin_city": "Киев", "user_origin_country": "Ukraine", '
            '"user_name": "Игорь", "user_occupation": "дизайнер", '
            '"user_plans": "поездка в Испанию на полторы недели", '
            '"persona_self_facts": "Алина живет в Москве, учится на дизайне", '
            '"summary": "Игорь родом из Киева, сейчас живет в Варшаве и планирует поездку в Испанию."}'
        )

    monkeypatch.setattr(GeminiClient, "generate_content", fake_generate)

    ok = await MemoryManager.refresh_memory_with_llm(
        session=async_session,
        contact=contact,
        persona=persona,
        recent_messages=[
            {"role": "user", "text": "я вообще сам из Киева но щас в Варшаве работаю дизайнером"},
            {"role": "assistant", "text": "ого прикольно, а я в Москве на дизайне учусь"},
        ],
    )
    assert ok is True
    assert contact.detected_city in ("Warsaw", "Варшава")
    assert contact.origin_city in ("Kyiv", "Киев")
    assert contact.detected_timezone == "Europe/Warsaw"
    assert "Варшаве" in (contact.dialogue_summary or "")

    mems = await MemoryManager.get_long_term_memories(async_session, contact.id)
    keys = {m.key for m in mems}
    assert "current_city" in keys
    assert "persona_self_facts" in keys
