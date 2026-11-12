import pytest
from companion.services.styles_service import DialogueStyleEngine, StyleType


def test_anti_ai_text_humanization():
    raw_ai_text_1 = "Конечно, я с радостью отвечу на твой вопрос."
    cleaned_1 = DialogueStyleEngine.humanize_text_formatting(raw_ai_text_1)
    # Must remove ending period
    assert not cleaned_1.endswith(".")

    raw_ai_text_2 = "Здравствуйте! Как Ваши дела?"
    cleaned_2 = DialogueStyleEngine.humanize_text_formatting(raw_ai_text_2)
    # Must replace formal Здравствуйте with casual greeting
    assert "Здравствуйте" not in cleaned_2
    assert "привет" in cleaned_2.lower()


def test_adaptive_mood_detection():
    # 1. Compliments
    mood_comp = DialogueStyleEngine.detect_adaptive_mood("Какая ты красивая на фото!")
    assert mood_comp == "COMPLIMENT_SHY"

    # 2. Banter
    mood_banter = DialogueStyleEngine.detect_adaptive_mood("Спорим, ты сейчас по мне скучаешь?")
    assert mood_banter == "PLAYFUL_BANTER"

    # 3. Fatigue
    mood_tired = DialogueStyleEngine.detect_adaptive_mood("Я так сильно устал сегодня на работе...")
    assert mood_tired == "CARING_SUPPORT"

    # 4. Pushy
    mood_pushy = DialogueStyleEngine.detect_adaptive_mood("Скинь номер быстро, че молчишь")
    assert mood_pushy == "CONFIDENT_BOUNDARY"

    # 5. Neutral
    mood_neutral = DialogueStyleEngine.detect_adaptive_mood("Завтра дождь передают по прогнозу")
    assert mood_neutral == "NATURAL_BALANCED"


def test_style_prompts_differentiation():
    compliment = "Ты безумно красивая девушка"

    prompt_shy = DialogueStyleEngine.build_style_prompt(StyleType.SHY, compliment)
    assert "Скромная" in prompt_shy or "стеснительн" in prompt_shy.lower()

    prompt_playful = DialogueStyleEngine.build_style_prompt(StyleType.PLAYFUL, compliment)
    assert "Игривая" in prompt_playful or "кокетк" in prompt_playful.lower()

    prompt_sarcastic = DialogueStyleEngine.build_style_prompt(StyleType.CONFIDENT_SARCASTIC, compliment)
    assert "Дерзкая" in prompt_sarcastic or "сарказм" in prompt_sarcastic.lower()

    prompt_caring = DialogueStyleEngine.build_style_prompt(StyleType.CARING, compliment)
    assert "заботлив" in prompt_caring.lower() or "тёпл" in prompt_caring.lower() or "тепл" in prompt_caring.lower()

    prompt_adaptive = DialogueStyleEngine.build_style_prompt(StyleType.ADAPTIVE_ALL, compliment)
    assert "адаптивн" in prompt_adaptive.lower() or "хамелеон" in prompt_adaptive.lower()


def test_system_prompt_context_rules():
    from companion.models import Contact, Persona
    from companion.services.ai_service import AIService

    persona = Persona(
        name="Алина",
        age=22,
        city="Москва",
        legend="Учусь на дизайне",
        study_work_hobbies="дизайн и кофе",
    )
    contact = Contact(
        tg_id=12345,
        first_name="Иван",
        detected_city="Москва",
    )

    prompt = AIService.build_system_prompt(persona, contact, "Что делаешь сейчас?")
    
    # Must contain context instructions:
    assert "ВОПРОСЫ ПРО ТЕКУЩИЙ МОМЕНТ" in prompt
    assert "ЖИВАЯ РЕАКЦИЯ НА ФАКТЫ СОБЕСЕДНИКА" in prompt
    assert "СВЯЗНОСТЬ ДИАЛОГА" in prompt
    # Must enforce Anti-AI rules:
    assert "КАТЕГОРИЧЕСКИЙ ЗАПРЕТ ДЛИННЫХ ТИРЕ" in prompt
    assert "НИКАКИХ ТОЧЕК В КОНЦЕ" in prompt
    # Must enforce natural character without cringe or interrogation:
    assert "ЕСТЕСТВЕННЫЙ ХАРАКТЕР БЕЗ КРИНЖА И ДОПРОСА" in prompt
    assert "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО устраивать допрос" in prompt
    assert "НИКАКОГО ФАЛЬШИВОГО СЛЕНГА" in prompt
    # Must enforce smooth multi-stage quest funnel:
    assert "ПЛАВНАЯ" in prompt and "ВОРОНКА" in prompt
    assert "Раскачка и узнавание" in prompt
    assert "Аккуратные намеки" in prompt
    assert "Нативная подводка" in prompt



def test_anti_ai_postprocessing_removes_dashes_and_dots():
    ai_raw = "Привет! — Я сейчас немного занята. Вот такие дела."
    humanized = DialogueStyleEngine.humanize_text_formatting(ai_raw)
    assert "—" not in humanized
    assert not humanized.endswith(".")


def test_contact_chat_history_cache():
    from companion.services.ai_service import AIService

    c_id = 7777
    AIService.add_chat_turn(c_id, "Привет", "Приветик)")
    hist = AIService.get_chat_history(c_id)
    assert len(hist) == 2
    assert hist[0]["role"] == "user"
    assert hist[0]["text"] == "Привет"
    assert hist[1]["role"] == "model"
    assert hist[1]["text"] == "Приветик)"


def test_system_prompt_timezone_and_anti_excuse():
    from companion.models import Contact, Persona
    from companion.services.ai_service import AIService

    persona_montreal = Persona(
        name="Полина",
        age=21,
        city="Монреаль",
        district="Downtown",
        legend="Учусь в университете",
        study_work_hobbies="лекции и кафе",
    )
    contact = Contact(
        tg_id=9999,
        first_name="Алекс",
        detected_city="Монреаль",
    )

    prompt = AIService.build_system_prompt(persona_montreal, contact, "Сколько у тебя сейчас времени?")

    # Must start with exact system header
    assert prompt.startswith("[СИСТЕМНАЯ ИНФОРМАЦИЯ: Сейчас в твоем городе Монреаль точное время:")
    assert "день недели:" in prompt
    assert "Ты находишься здесь и живешь по этому времени. Ошибаться во времени КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО]." in prompt

    # Must enforce Rule 5: Anti-excuses
    assert "КАТЕГОРИЧЕСКИЙ ЗАПРЕТ НА ИИ-ОПРАВДАНИЯ" in prompt
    assert "НИКОГДА не используй роботоподобные фразы" in prompt
    assert "я вечно путаюсь со временем" in prompt
    assert "загналась что-то" in prompt
    assert "Ошибаться во времени КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО" in prompt


def test_humanize_text_anti_ai_excuses():
    raw_ai_excuse = "ой, точно, я вечно путаюсь со временем... загналась что-то"
    humanized = DialogueStyleEngine.humanize_text(raw_ai_excuse)

    # Must not contain robotic excuse phrases
    assert "вечно путаюсь со временем" not in humanized
    assert "загналась что-то" not in humanized
    assert "туплю чот" in humanized or "запутал" in humanized


def test_no_brackets_intent_detection():
    from companion.services.intent_service import IntentService

    assert IntentService.detect_no_brackets_request("почему ты со скобками постоянно общаешься, выглядит подозрительно!")
    assert IntentService.detect_no_brackets_request("попробуй писать без скобок, а то я не буду доверять тебе")
    assert IntentService.detect_no_brackets_request("давай без скобочек")
    assert IntentService.detect_no_brackets_request("не ставь скобки")
    assert IntentService.detect_no_brackets_request("хватит ставить скобки")
    assert IntentService.detect_no_brackets_request("убери скобки пожалуйста")
    assert IntentService.detect_no_brackets_request("хватит смайликов")
    assert not IntentService.detect_no_brackets_request("привет, как твои дела?")


def test_humanize_text_no_brackets_strict_elimination():
    sample_with_brackets = "ладно уговорил, буду без них пробовать) доволен?)"
    result = DialogueStyleEngine.humanize_text(sample_with_brackets, no_brackets=True)

    # Strict: zero brackets anywhere
    assert ")" not in result
    assert "(" not in result
    assert "доволен?" in result


def test_humanize_text_bracket_spam_limiter():
    # If interlocutor hasn't asked for no_brackets, but LLM spammed 3 brackets in one bubble
    spam_sample = "привет) я не бот вообще-то) давай познакомимся)"
    result = DialogueStyleEngine.humanize_text(spam_sample, no_brackets=False)

    # Must eliminate bracket spam in every clause, keeping at most ONE trailing smile
    assert result.count(")") <= 1
    assert "привет, я не бот вообще-то" in result or "привет я не бот вообще-то" in result


def test_dynamic_delays_regular_vs_non_regular():
    from datetime import datetime, timezone, timedelta
    from companion.models import Contact
    from companion.bot.business_bot import BusinessChatBot

    now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Non-regular: brand new contact (0 or 1 message) -> fast/natural non-stalling delay (15-45s)
    contact_new = Contact(
        total_messages_in=1,
        total_messages_out=0,
        last_outgoing_at=None,
    )
    delays_new = BusinessChatBot.calculate_dynamic_delays(contact_new, now=now, reply_text="привет")
    assert delays_new["is_regular"] is False
    assert 10 <= delays_new["unread_delay"] <= 55

    # 2. Non-regular: long gap (last msg was 40 minutes ago)
    contact_gap = Contact(
        total_messages_in=5,
        total_messages_out=5,
        last_outgoing_at=now - timedelta(minutes=40),
    )
    delays_gap = BusinessChatBot.calculate_dynamic_delays(contact_gap, now=now, reply_text="я тут")
    assert delays_gap["is_regular"] is False
    assert 10 <= delays_gap["unread_delay"] <= 55

    # 3. Regular active chat: last msg was 2 minutes ago
    contact_active = Contact(
        total_messages_in=6,
        total_messages_out=6,
        last_outgoing_at=now - timedelta(minutes=2),
    )
    delays_active = BusinessChatBot.calculate_dynamic_delays(contact_active, now=now, reply_text="привет")
    assert delays_active["is_regular"] is True
    # In active chat, unread delay is fast (6-15s) or moderate (30-60s)
    assert delays_active["unread_delay"] <= 75


def test_voice_service_config_and_breathing():
    from companion.config import settings
    from companion.services.voice_service import VoiceService

    assert settings.ELEVENLABS_VOICE_ID == "oik5x6jBI6Xj6MPIciZU"

    # Test preparation adds breathing intake '... ' and removes exclamation
    text = "Привет! Я сейчас кофе пью."
    prepared = VoiceService.prepare_text_for_tts(text)
    assert prepared.startswith("...")
    assert "!" not in prepared


def test_adaptive_only_style_engine():
    # 1. Verify that adaptive mode is primary and default title is clear
    assert "Адаптивный" in StyleType.ADAPTIVE_ALL.title

    # 2. Check mood detection extensions
    assert DialogueStyleEngine.detect_adaptive_mood("привет норм") == "SHORT_DRY"
    assert DialogueStyleEngine.detect_adaptive_mood("какую музыку ты любишь слушать?") == "INTELLECTUAL_DEEP"

    # 3. Check adaptive prompt synthesis with dynamic client adaptation
    prompt = DialogueStyleEngine.build_style_prompt(StyleType.ADAPTIVE_ALL, "Привет, красивая девушка!")
    assert "ЕДИНЫЙ АДАПТИВНЫЙ РЕЖИМ" in prompt
    assert "ПЕРСОНАЛЬНАЯ ПОДСТРОЙКА" in prompt
    assert "ЗЕРКАЛИРОВАНИЕ ТЕМПА" in prompt
    assert "С каждым клиентом выстраивается уникальный стиль" in prompt

    # 4. Check Telegram bot styles keyboard has only Adaptive button
    from companion.bot.telegram_bot_engine import TelegramBotEngine
    bot = TelegramBotEngine(token="mock_token", admin_id=12345)
    kb = bot.build_styles_keyboard()
    # Expect 1 style button + 1 back button
    assert len(kb["inline_keyboard"]) == 2
    assert "Адаптивный" in kb["inline_keyboard"][0][0]["text"]
    assert "set_style_adaptive_all" in kb["inline_keyboard"][0][0]["callback_data"]




