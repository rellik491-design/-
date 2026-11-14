import pytest
from companion.ai.conversation_state_analyzer import ConversationStateAnalyzer
from companion.services.health_manager import HealthManager, SystemHealthState
from companion.services.sticker_service import StickerCatalogService
from companion.services.reaction_service import TelegramReactionService
from companion.bot.response_planner import LaughterGenerator, STICKER_CATEGORIES, ResponsePlanner


def test_laughter_suppressed_on_callout_and_cold_idiom():
    """Requirement 2 & 3: Never laugh when user asks why bot laughs or uses 'зуб на зуб не попадает'."""
    text = "у тебя что зуб на зуб не попадает от холода\nчто за монстр страшный прилетел"
    analysis = ConversationStateAnalyzer.analyze_dialogue_state(text)

    assert analysis.is_calling_out_bot is True
    assert analysis.laughter_appropriate is False
    assert analysis.recommended_laughter is None
    assert "НЕ СМЕЙСЯ" in analysis.guidance_notes

    # Verify sanitizer strips any accidental laughter
    cleaned = ConversationStateAnalyzer.sanitize_bot_reply("АХАХАХ да ладно тебе, холодно просто", analysis)
    assert not cleaned.startswith("АХАХАХ")
    assert not cleaned.startswith("ахах")


def test_laughter_suppressed_on_questions_and_serious_topics():
    """Requirement 3: Never laugh at serious messages or ordinary questions."""
    # 1. Ordinary questions
    q_analysis = ConversationStateAnalyzer.analyze_dialogue_state("Привет, ты где сейчас находишься?")
    assert q_analysis.laughter_appropriate is False

    # 2. Serious / sad topic
    sad_analysis = ConversationStateAnalyzer.analyze_dialogue_state("У меня сегодня собака умерла, так тяжело на душе")
    assert sad_analysis.is_serious_or_sensitive is True
    assert sad_analysis.laughter_appropriate is False
    assert sad_analysis.recommended_laughter is None


def test_laughter_permitted_only_on_genuine_humor():
    """Requirement 2: Laughter permitted only when humor is genuinely detected."""
    # Clear cooldown
    ConversationStateAnalyzer._turns_since_last_bot_laugh[999] = 10

    joke_analysis = ConversationStateAnalyzer.analyze_dialogue_state(
        "АХАХАХАХ жесть ты выдала 😂😂😂 ору не могу",
        contact_id=999,
    )
    assert joke_analysis.humor_detected is True
    assert joke_analysis.laughter_appropriate is True
    assert joke_analysis.recommended_laughter in ("АХАХ", "хах")


def test_slang_and_irony_comprehension():
    """Requirement 5: Recognizes Russian slang and true contextual meaning."""
    # 1. "не ссы"
    an1 = ConversationStateAnalyzer.analyze_dialogue_state("Не ссы, всё нормально будет")
    assert any("не бойся" in s for s in an1.slang_terms_detected)

    # 2. "впадло"
    an2 = ConversationStateAnalyzer.analyze_dialogue_state("Мне сегодня вообще впадло выходить из дома")
    assert any("лень" in s for s in an2.slang_terms_detected)

    # 3. "голова"
    an3 = ConversationStateAnalyzer.analyze_dialogue_state("Ну ты голова конечно, круто придумала")
    assert any("красавчик" in s or "сообразительность" in s for s in an3.slang_terms_detected)

    # 4. Irony: "Спасибо, но я не могу сказать тоже аналогичное"
    an4 = ConversationStateAnalyzer.analyze_dialogue_state("Спасибо, но я не могу сказать тоже аналогичное…")
    assert an4.irony_or_sarcasm_detected is True


def test_emoji_sparsity_and_policy():
    """Requirement 4: Default emoji_required = False, max 1 emoji per response."""
    analysis = ConversationStateAnalyzer.analyze_dialogue_state("Привет! Как твои дела?")
    assert analysis.emoji_required is False

    # Sanitizer limits multiple emojis to 1
    raw_with_too_many_emojis = "Привет! 🙈 Как дела? 🌸 Я дома сижу ✨ чай пью ☕"
    sanitized = ConversationStateAnalyzer.sanitize_bot_reply(raw_with_too_many_emojis, analysis)
    # Count total emojis in sanitized
    import re
    emoji_pat = re.compile(r"[\U00010000-\U0010ffff\u2600-\u27ff\u2300-\u23ff\u2b50\u200d\ufe0f]")
    assert len(emoji_pat.findall(sanitized)) <= 1


def test_sticker_catalog_service_real_file_ids_and_cooldown():
    """Requirements 6 & 7: Stickers must use real Telegram file IDs and have >= 15 turns cooldown."""
    StickerCatalogService.initialize()

    # Guaranteed valid real Telegram IDs
    fid = StickerCatalogService.get_sticker("laugh")
    assert fid is not None
    assert fid.startswith("CAACAgIAAxUAA")  # Real Telegram Bot API 7.0 file_id prefix

    # Cooldown enforcement
    contact_id = 42
    assert StickerCatalogService.can_send_sticker(contact_id, current_turn_count=1) is True
    StickerCatalogService.record_sticker_sent(contact_id, current_turn_count=1)

    # Turn 5 (only 4 turns later) -> should be forbidden
    assert StickerCatalogService.can_send_sticker(contact_id, current_turn_count=5) is False
    # Turn 16 (15 turns later) -> should be permitted
    assert StickerCatalogService.can_send_sticker(contact_id, current_turn_count=16) is True

    # STICKER_CATEGORIES must contain real IDs
    for cat_name, cat_data in STICKER_CATEGORIES.items():
        for fid_entry in cat_data["fallback_file_ids"]:
            assert fid_entry.startswith("CAACAgIAAxUAA")


def test_native_telegram_reaction_service():
    """Section 34: Telegram Reaction Service supports allowed emojis and cooldown."""
    assert "😂" in TelegramReactionService.ALLOWED_REACTIONS
    assert "👍" in TelegramReactionService.ALLOWED_REACTIONS
    assert "❤️" in TelegramReactionService.ALLOWED_REACTIONS

    chat_id = 8330023642
    assert TelegramReactionService.can_react(chat_id, current_turn_count=1) is True
    TelegramReactionService.record_reaction_sent(chat_id, current_turn_count=1)

    # Next turn (turn 2) -> Cooldown forbids
    assert TelegramReactionService.can_react(chat_id, current_turn_count=2) is False
    # Turn 4 (3 turns later) -> Permitted
    assert TelegramReactionService.can_react(chat_id, current_turn_count=4) is True


def test_health_manager_failsafe_pause_and_silence():
    """Requirements 9 & 10: Critical provider/quota errors trigger SAFE PAUSED state."""
    # Reset
    HealthManager._state = SystemHealthState.RUNNING
    HealthManager._paused_reason = None

    # Error classification
    assert HealthManager.classify_error("429 ResourceExhausted: Quota exceeded") == "critical"
    assert HealthManager.classify_error("GenerateRequestsPerDay limit reached") == "critical"
    assert HealthManager.classify_error("Invalid API key 401") == "critical"
    assert HealthManager.classify_error("Timeout 503 Service Unavailable") == "recoverable"

    # Registering critical quota error enters PAUSED state
    HealthManager.register_error("429 ResourceExhausted quota exceeded for day")
    assert HealthManager.is_paused() is True
    assert HealthManager.get_state() == SystemHealthState.PAUSED
    assert "429" in HealthManager.get_paused_reason()

    # Reset back to running for other tests
    HealthManager._state = SystemHealthState.RUNNING
    HealthManager._paused_reason = None
