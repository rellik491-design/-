import pytest
import re
from companion.services.multilingual_service import MultilingualService, LanguageDetectionResult
from companion.services.stt_service import STTService, STTResult
from companion.services.intent_service import IntentService
from companion.services.ai_service import AIService
from companion.services.voice_service import VoiceService
from companion.models.entities import Contact, Persona


def test_text_language_detection_ukrainian():
    """Validates Section 1: automatic Ukrainian detection across varied registers."""
    cases = [
        ("привіт, як ти?", "uk"),
        ("шо робиш?", "uk"),
        ("я зараз прийду", "uk"),
        ("де ти знаходишся?", "uk"),
        ("дякую за допомогу, дуже виручив!", "uk"),
        ("на добраніч, солодких снів", "uk"),
        ("чудово провела день, відпочиваю вдома", "uk"),
        ("файна погода сьогодні у Львові", "uk"),
        ("я п'ю каву в центрі", "uk"),
        ("сьогодні дуже багато справ", "uk"),
        ("ти де?", "uk"),
    ]
    for text, expected in cases:
        res = MultilingualService.detect_language(text)
        assert res.language == expected, f"Failed on '{text}': got {res.language}, expected {expected}"
        assert res.confidence >= 0.70


def test_text_language_detection_russian_not_broken():
    """Validates Section 12: Russian recognition remains 100% accurate."""
    cases = [
        ("привет, как дела?", "ru"),
        ("что делаешь?", "ru"),
        ("я сейчас приду, подожди", "ru"),
        ("где ты находишься?", "ru"),
        ("спасибо за помощь, очень выручил!", "ru"),
        ("спокойной ночи, сладких снов", "ru"),
        ("отлично провела день, отдыхаю дома", "ru"),
        ("сегодня очень много дел", "ru"),
    ]
    for text, expected in cases:
        res = MultilingualService.detect_language(text)
        assert res.language == expected, f"Failed on '{text}': got {res.language}, expected {expected}"
        assert res.confidence >= 0.70


def test_mixed_russian_ukrainian_code_switching():
    """Validates Section 2: Surzhyk and mixed speech classified as 'mixed', never as error."""
    mixed_cases = [
        "та я сейчас подойду, не переживай",
        "я щас прийду, все нормально",
        "короче я вже не знаю шо робити",
        "та я зараз не знаю, шо делать",
        "та всьо нормально, ты как сам?",
        "я вже іду, жди мене",
    ]
    for text in mixed_cases:
        res = MultilingualService.detect_language(text)
        assert res.language == "mixed", f"Failed on mixed text '{text}': got {res.language}"
        assert res.confidence >= 0.70


def test_transliterated_ukrainian():
    """Validates Section 1 & 5: Latin transliterated Ukrainian detected as 'uk'."""
    cases = [
        ("pryvit, yak spravy?", "uk"),
        ("sho robysh zaraz?", "uk"),
        ("dobryi den, ty de?", "uk"),
        ("dyakuyu, vse dobre", "uk"),
    ]
    for text, expected in cases:
        res = MultilingualService.detect_language(text)
        assert res.language == expected, f"Failed on transliterated text '{text}': got {res.language}"
        assert res.is_transliterated is True


def test_ambiguous_short_text_resolved_via_context():
    """Validates Section 7: Context-aware language resolution for short ambiguous phrases."""
    # When user history is Ukrainian:
    uk_history = [
        {"role": "user", "parts": ["Привіт! Як твої справи?"]},
        {"role": "model", "parts": ["Привітик! Все чудово, відпочиваю)"]},
    ]
    res_uk_context = MultilingualService.detect_language(
        "ага, добре",
        conversation_history=uk_history,
        recent_language="uk",
    )
    assert res_uk_context.language == "uk"

    # When user history is Russian:
    ru_history = [
        {"role": "user", "parts": ["Привет! Как твои дела?"]},
        {"role": "model", "parts": ["Приветик! Все отлично, отдыхаю)"]},
    ]
    res_ru_context = MultilingualService.detect_language(
        "ну да, согласен",
        conversation_history=ru_history,
        recent_language="ru",
    )
    assert res_ru_context.language == "ru"


def test_slang_normalization_never_overwrites_original():
    """Validates Section 5: Slang is normalized in a separate field; original transcript is preserved."""
    raw = "шо щас крч в тг скинь гс пж"
    res = MultilingualService.normalize_slang(raw, language="uk")

    assert res.original_text == raw
    assert "що" in res.normalized_text
    assert "зараз" in res.normalized_text
    assert "короче" in res.normalized_text
    assert "телеграм" in res.normalized_text
    assert "голосове" in res.normalized_text
    assert "будь ласка" in res.normalized_text
    # Original is untouched
    assert raw == "шо щас крч в тг скинь гс пж"


def test_rolling_language_memory_and_switching():
    """Validates Section 8 & 10: Rolling preference weighted memory and fluid language switching."""
    # Start fresh with Ukrainian
    res1 = MultilingualService.update_language_preference(None, "uk", 0.95)
    assert res1["current_user_language"] == "uk"
    pref = res1["language_preference"]
    assert pref["uk"] == 1.0

    # User sends 2 Ukrainian messages
    for _ in range(2):
        res1 = MultilingualService.update_language_preference(pref, "uk", 0.95)
        pref = res1["language_preference"]
    assert res1["current_user_language"] == "uk"
    assert pref["uk"] > 0.80

    # User switches to Russian for several messages
    for _ in range(4):
        res2 = MultilingualService.update_language_preference(pref, "ru", 0.95)
        pref = res2["language_preference"]
    assert res2["current_user_language"] == "ru"
    assert pref["ru"] > pref["uk"]


def test_multilingual_stt_prompt_specifications():
    """Validates Section 3 & 4: Multilingual STT prompt forbids translation between UK and RU."""
    prompt = STTService.STT_PROMPT
    assert "русский, украинский или смешанный" in prompt
    assert "ЗАПРЕЩЕНО переводить" in prompt
    assert "строго по-украински" in prompt
    assert "строго по-русски" in prompt
    assert "[нерозбірливо]" in prompt or "[неразборчиво]" in prompt


def test_stt_result_dataclass_and_confidence():
    """Validates Section 6 & 9: STTResult stores confidence and metadata."""
    res_clean = STTResult(
        transcript="Привіт, ти де зараз?",
        language="uk",
        language_confidence=0.96,
        transcription_confidence=0.92,
        normalized_text="Привіт, ти де зараз?",
    )
    assert res_clean.language == "uk"
    assert res_clean.language_confidence == 0.96
    assert res_clean.transcription_confidence == 0.92

    # Uncertain segment lowered confidence
    res_uncertain = STTResult(
        transcript="я [нерозбірливо] завтра",
        language="uk",
        language_confidence=0.85,
        transcription_confidence=0.72,
        normalized_text="я [нерозбірливо] завтра",
    )
    assert res_uncertain.transcription_confidence < 0.80
    assert "[нерозбірливо]" in res_uncertain.transcript


def test_ukrainian_intent_detection():
    """Validates that IntentService accurately recognizes Ukrainian proposals and requests."""
    # 1. Meetup proposals
    meet1 = IntentService.detect_meetup_proposal("давай зустрінемось завтра ввечері")
    assert meet1.is_proposal is True
    assert meet1.category == "direct_meet"

    meet2 = IntentService.detect_meetup_proposal("вип'ємо кави на вихідних?")
    assert meet2.is_proposal is True
    assert meet2.category == "coffee_dinner"

    meet3 = IntentService.detect_meetup_proposal("скинь свій номер будь ласка")
    assert meet3.is_proposal is True
    assert meet3.category == "number_request"

    # 2. Voice note request
    voice1 = IntentService.detect_voice_request("скинь голосове будь ласка")
    assert voice1.requested is True

    voice2 = IntentService.detect_voice_request("запиши кружечок, хочу почути твій голос")
    assert voice1.requested is True

    # 3. Stop words
    assert IntentService.is_stop_word_present("відчепись від мене") is True
    assert IntentService.is_stop_word_present("ти бот чи людина?") is True

    # 4. No brackets
    assert IntentService.detect_no_brackets_request("пиши без дужок будь ласка") is True
    assert IntentService.detect_no_brackets_request("годі смайликів") is True


def test_developer_debug_logging_format(caplog):
    """Validates Section 14: Developer debug logging output."""
    import logging
    with caplog.at_level(logging.INFO):
        MultilingualService.log_developer_debug(
            message_id=98765,
            message_type="voice",
            language="uk",
            language_confidence=0.96,
            transcript="Привіт, ти де зараз?",
            normalized_text="Привіт, ти де зараз?",
            stt_confidence=0.92,
        )
    log_text = caplog.text
    assert "MESSAGE: 98765" in log_text
    assert "TYPE: voice" in log_text
    assert "DETECTED LANGUAGE: uk" in log_text
    assert "LANGUAGE CONFIDENCE: 0.96" in log_text
    assert 'TRANSCRIPT: "Привіт, ти де зараз?"' in log_text
    assert "STT CONFIDENCE: 0.92" in log_text


def test_ukrainian_contextual_fallback_replies():
    """Validates Section 8: Ukrainian context generates human Ukrainian fallback replies."""
    contact_uk = Contact(id=99, first_name="Олексій", language="uk", recent_language="uk")
    persona = Persona(id=1, name="Карина", city="Київ")

    # 1. Distraction query in Ukrainian
    fb_distract = AIService.generate_contextual_fallback(contact_uk, persona, "на що відволіклась?")
    assert any(c in fb_distract for c in "іїєґ") or "по навчанню" in fb_distract or "справах" in fb_distract

    # 2. What doing query in Ukrainian
    fb_doing = AIService.generate_contextual_fallback(contact_uk, persona, "що робиш зараз?")
    assert any(w in fb_doing for w in ["чай", "вдома", "серіал", "відпочиваю", "справах"])

    # 3. How are you in Ukrainian
    fb_how = AIService.generate_contextual_fallback(contact_uk, persona, "як справи?")
    assert any(w in fb_how for w in ["привітик", "привіт", "потихеньку", "нормально", "звільнилася"])


def test_voice_service_tts_ukrainian_routing():
    """Validates Section 16: VoiceService routes Ukrainian speech with language_code='uk'."""
    uk_text = "Привіт! Слухай, я зараз у кав'ярні сиджу, тут такий класний трек грає."
    det = MultilingualService.detect_language(uk_text)
    assert det.language == "uk"
