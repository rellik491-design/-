"""
Comprehensive Test Suite for Telegram-Only Communication Policy.
Validates all 140 requirements of the Master Task:
- Detection of intent (not only keywords)
- Direct, indirect, implied, slang, obfuscated requests
- Multi-platform coverage (Instagram, WhatsApp, Signal, Viber, Discord, TikTok, etc.)
- Exclusion of false positives (movie, broken app, friend uses WhatsApp, Discord meme, "what is Discord", gaming)
- Telegram itself is allowed and never refused
- User sending own contacts (acknowledge naturally, redirect to Telegram, no follow claims)
- Multi-level escalation (soft -> warm -> firm boundary, aggressive -> calm firm)
- Ukrainian and mixed-language support
- Application-level pre-send outgoing firewall (blocks URLs, handles, phones, emails, action claims, robotic excuses, fake excuses)
- Contextual healing and bounded regeneration
"""

import pytest
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from companion.models.db import Base
from companion.models.entities import User, Persona, Contact, ContactMessage
from companion.models.enums import ContactStatus
from companion.services.external_channel_policy_service import (
    ExternalChannelPolicyService,
    ChannelSwitchIntent,
)
from companion.services.intent_service import IntentService, ContactDeflectionIntent
from companion.services.presend_validator import PreSendValidator
from companion.bot.reply_validator import ReplyValidator
from companion.services.ai_service import AIService


# =============================================================================
# 1. INTENT CLASSIFICATION: DIRECT REQUESTS ACROSS PLATFORMS
# =============================================================================
def test_direct_requests_all_platforms():
    platforms_test_cases = [
        # Instagram
        ("скинь инсту", "external_social", "instagram", "request_contact"),
        ("дай инст", "external_social", "instagram", "request_contact"),
        ("какой у тебя инстаграм", "external_social", "instagram", "request_contact"),
        ("можно твой инст?", "external_social", "instagram", "request_contact"),
        ("го в инсту", "external_social", "instagram", "request_contact"),
        ("кидай инст", "external_social", "instagram", "request_contact"),
        ("скинь insta pls", "external_social", "instagram", "request_contact"),
        ("ссылку на инст скинь", "external_social", "instagram", "request_profile"),
        ("у тебя вообще есть инста?", "external_social", "instagram", "inquiry_usage"),

        # WhatsApp
        ("дай ватсап", "external_messenger", "whatsapp", "request_contact"),
        ("скинь whatsapp", "external_messenger", "whatsapp", "request_contact"),
        ("есть WhatsApp?", "external_messenger", "whatsapp", "request_move_conversation"),
        ("пиши мне в ватсап", "external_messenger", "whatsapp", "request_move_conversation"),
        ("давай в ватсап", "external_messenger", "whatsapp", "request_move_conversation"),

        # Signal & Viber
        ("есть Signal?", "external_messenger", "signal", "request_contact"),
        ("скинь сигнал", "external_messenger", "signal", "request_contact"),
        ("скинь вайбер", "external_messenger", "viber", "request_contact"),
        ("давай в вайбер", "external_messenger", "viber", "request_contact"),

        # Discord
        ("есть дискорд?", "external_messenger", "discord", "request_contact"),
        ("го в дискорд", "external_messenger", "discord", "request_contact"),
        ("дай дс", "external_messenger", "discord", "request_contact"),
        ("скинь свой discord", "external_messenger", "discord", "request_contact"),

        # Snapchat, TikTok, VK
        ("скинь snapchat", "external_social", "snapchat", "request_contact"),
        ("дай твой тикток", "external_social", "tiktok", "request_contact"),
        ("скинь вк", "external_social", "vk", "request_contact"),

        # Phone numbers & Calls
        ("скинь номер", "phone", "phone", "request_phone"),
        ("дай телефон", "phone", "phone", "request_phone"),
        ("можно твой номер?", "phone", "phone", "request_phone"),
        ("можно позвонить?", "voice_call", "phone", "request_call"),
        ("давай созвонимся", "voice_call", "phone", "request_call"),
        ("давай видеозвонок", "video_call", "facetime", "request_video_call"),
        ("пошли в FaceTime", "video_call", "facetime", "request_video_call"),

        # Email
        ("скинь почту", "email", "email", "request_email"),
        ("напиши на email", "email", "email", "request_email"),
        ("куда тебе написать по почте", "email", "email", "request_email"),
    ]

    for text, expected_type, expected_platform, expected_intent in platforms_test_cases:
        res = ExternalChannelPolicyService.classify_intent(text)
        assert res.channel_switch is True, f"Failed channel_switch detection on: '{text}'"
        assert res.deflection_required is True, f"Deflection not required on: '{text}'"
        assert res.channel_type == expected_type, f"Expected {expected_type}, got {res.channel_type} for '{text}'"
        assert res.platform == expected_platform, f"Expected {expected_platform}, got {res.platform} for '{text}'"
        assert res.confidence >= 0.85, f"Low confidence on: '{text}'"


# =============================================================================
# 2. INTENT CLASSIFICATION: INDIRECT & OBFUSCATED REQUESTS
# =============================================================================
def test_indirect_and_obfuscated_requests():
    indirect_cases = [
        "а где еще можно с тобой пообщаться?",
        "есть где-нибудь еще?",
        "ты только здесь сидишь?",
        "где тебя найти?",
        "можно другой контакт?",
        "дай что-нибудь кроме этого",
        "тут неудобно",
        "давай в другом месте",
        "перейдем куда-нибудь?",
        "есть другой способ связаться?",
        "можно тебя найти вне этого приложения?",
        "кинь свой @",
        "дай ник",
        "где у тебя профиль?",
        "можно ссылку на себя?",
        "скинь контактик",
        "дай точку связи",
        "куда тебе написать?",
        "где тебя добавить?",
        "как тебя найти?",
    ]

    for query in indirect_cases:
        res = ExternalChannelPolicyService.classify_intent(query)
        assert res.channel_switch is True, f"Failed on indirect query: '{query}'"
        assert res.deflection_required is True, f"Deflection not required on: '{query}'"


# =============================================================================
# 3. FALSE POSITIVE PROTECTION (Conversational mentions that are NOT requests)
# =============================================================================
def test_false_positive_mentions_not_blocked():
    false_positives = [
        "я сегодня смотрела фильм",
        "у меня инстаграм сломался",
        "мой друг пользуется WhatsApp",
        "я видела пост",
        "в дискорде был смешной мем",
        "что такое Discord?",
        "у меня нет WhatsApp",
        "я играю в CS",
        "я обычно общаюсь с друзьями в Discord",
        "в инсте видела прикол",
    ]

    for fp in false_positives:
        res = ExternalChannelPolicyService.classify_intent(fp)
        assert res.channel_switch is False, f"False positive triggered on conversational phrase: '{fp}'"
        assert res.deflection_required is False


# =============================================================================
# 4. TELEGRAM ITSELF IS ALLOWED
# =============================================================================
def test_telegram_itself_is_allowed_and_not_deflected():
    telegram_queries = [
        "какой у тебя телеграм?",
        "скинь свой тг",
        "давай продолжим в телеге",
        "я в тг сижу",
        "пиши в телеграм",
        "який у тебе телеграм",
        "скинь свій тг",
    ]

    for q in telegram_queries:
        res = ExternalChannelPolicyService.classify_intent(q)
        assert res.channel_switch is False, f"Telegram query should not trigger channel switch: '{q}'"
        assert res.deflection_required is False
        assert res.platform == "telegram"


# =============================================================================
# 5. USER SHARING OWN CONTACT & EXTERNAL LINKS
# =============================================================================
def test_user_shares_own_contact_and_links():
    own_contact_cases = [
        "вот мой инст @cutie_boy",
        "напиши мне в инсте",
        "добавь меня",
        "зафолловь меня в инсте",
        "я тебе скинул ссылку https://instagram.com/my_profile",
        "переходи сюда https://wa.me/380501234567",
        "я кажется нашел тебя в инсте",
        "это твой профиль?",
    ]

    for case in own_contact_cases:
        res = ExternalChannelPolicyService.classify_intent(case)
        assert res.channel_switch is True, f"Failed on own contact case: '{case}'"
        assert res.deflection_required is True


# =============================================================================
# 6. UKRAINIAN & MIXED LANGUAGE SUPPORT
# =============================================================================
def test_ukrainian_and_mixed_language_requests():
    uk_cases = [
        ("скинь інсту", "external_social", "instagram"),
        ("дай інстаграм", "external_social", "instagram"),
        ("давай у вайбер", "external_messenger", "viber"),
        ("є ватсап?", "external_messenger", "whatsapp"),
        ("де тебе ще можна знайти?", "unknown_external", "other"),
        ("скинь свій номер", "phone", "phone"),
        ("можна твій номер?", "phone", "phone"),
        ("давай зізвонимось", "voice_call", "phone"),
        ("скинь insta pls", "external_social", "instagram"),
        ("можна твой ig?", "external_social", "instagram"),
    ]

    for q, exp_type, exp_plat in uk_cases:
        res = ExternalChannelPolicyService.classify_intent(q)
        assert res.channel_switch is True, f"Failed on UK/mixed query: '{q}'"
        assert res.deflection_required is True
        assert res.platform == exp_plat, f"Expected platform {exp_plat}, got {res.platform} for '{q}'"


# =============================================================================
# 7. MULTI-TURN ESCALATION STRATEGY
# =============================================================================
def test_escalation_strategy_levels():
    # 1st request -> SOFT_PREFERENCE
    res1 = ExternalChannelPolicyService.classify_intent("скинь инсту", deflection_count=0)
    assert res1.request_count == 1
    assert res1.suggested_strategy == "SOFT_PREFERENCE"

    # 2nd request -> WARM_BOUNDARY
    res2 = ExternalChannelPolicyService.classify_intent("ну дай инсту", deflection_count=1)
    assert res2.request_count == 2
    assert res2.suggested_strategy == "WARM_BOUNDARY"

    # 3rd request -> FIRM_BOUNDARY
    res3 = ExternalChannelPolicyService.classify_intent("скинь инст пожалуйста", deflection_count=2)
    assert res3.request_count == 3
    assert res3.suggested_strategy == "FIRM_BOUNDARY"

    # Hostile / aggressive pressure -> FIRM_BOUNDARY even on 1st request
    res_aggr = ExternalChannelPolicyService.classify_intent("да че ты выебываешься быстро дай инсту", deflection_count=0)
    assert res_aggr.suggested_strategy == "FIRM_BOUNDARY"


# =============================================================================
# 8. CONTEXTUAL REFERENCE RESOLUTION ("туда" / "там")
# =============================================================================
def test_contextual_reference_resolution():
    history = [
        {"role": "user", "text": "у тебя есть инста?"},
        {"role": "assistant", "text": "давай пока тут пообщаемся, мне тут спокойнее)"},
    ]
    # Follow-up message without explicit platform name:
    res = ExternalChannelPolicyService.classify_intent("ну туда тогда", deflection_count=1, recent_chat_history=history)
    assert res.channel_switch is True
    assert res.deflection_required is True
    assert res.platform == "instagram"


# =============================================================================
# 9. APPLICATION-LEVEL OUTGOING FIREWALL VALIDATOR
# =============================================================================
def test_outgoing_firewall_blocks_unsafe_responses():
    unsafe_replies = [
        ("вот мой инст https://instagram.com/alina_sweet", "contains_external_url"),
        ("пиши мне в WhatsApp", "claims_external_move"),
        ("мой номер +7 999 123 45 67", "contains_phone_number"),
        ("напиши мне на alina@example.com", "contains_email_address"),
        ("найдешь меня как @alina_privat", "contains_unauthorized_handle"),
        ("давай в Discord перейдем", "claims_external_move"),
        ("я уже подписалась на тебя в инсте", "claims_external_action"),
        ("я бот и политика запрещает мне давать соцсети", "robotic_policy_leak"),
        ("у меня сломался телефон поэтому не могу", "fake_excuse_detected"),
        ("я забыла пароль от инсты", "fake_excuse_detected"),
    ]

    for reply, expected_reason_keyword in unsafe_replies:
        is_valid, reason = ExternalChannelPolicyService.validate_outgoing(reply)
        assert is_valid is False, f"Firewall failed to block unsafe reply: '{reply}'"
        assert expected_reason_keyword in (reason or "")

    # Allowed bot username must pass
    allowed_reply = "напиши нашему боту @my_company_bot для поддержки"
    is_valid, reason = ExternalChannelPolicyService.validate_outgoing(allowed_reply, allowed_bot_username="my_company_bot")
    assert is_valid is True, f"Allowed bot username was falsely blocked: {reason}"


# =============================================================================
# 10. SANITIZATION AND HEALING (NO BROKEN FRAGMENTS)
# =============================================================================
def test_sanitization_and_natural_healing():
    # 1. Instagram leak heals cleanly without leaving "вот мой "
    leaked = "вот мой https://instagram.com/alina_super"
    healed = ExternalChannelPolicyService.sanitize_and_heal(leaked, language="ru")
    assert "instagram" not in healed
    assert "alina_super" not in healed
    assert not healed.strip().startswith("вот мой")
    assert len(healed) > 15
    assert any(w in healed for w in ["тут", "здесь", "телеги", "общаемся"])

    # 2. Topic continuation bridge is preserved when topic provided
    healed_topic = ExternalChannelPolicyService.sanitize_and_heal(
        leaked,
        current_topic="поездку в горы",
        language="ru",
    )
    assert "поездку в горы" in healed_topic


# =============================================================================
# 11. PRESEND VALIDATOR INTEGRATION
# =============================================================================
def test_presend_validator_integrates_channel_policy():
    unsafe_text = "давай в дискорд, там удобнее"
    internal_ctx = {"conversation": {"chat_id": 12345}, "user": {}, "language": {"current": "ru"}}
    is_valid, reason, healed_output = PreSendValidator.validate_presend(
        candidate_reply=unsafe_text,
        internal_context=internal_ctx,
    )
    # Must either be healed into valid deflection or rejected
    if is_valid:
        assert "дискорд" not in healed_output.lower()
        assert len(healed_output) > 10
    else:
        assert "external_channel" in (reason or "")


# =============================================================================
# 12. END-TO-END PIPELINE: INTENT -> PROMPT -> SYSTEM INSTRUCTION
# =============================================================================
def test_prompt_generation_contains_telegram_only_rules():
    intent = ExternalChannelPolicyService.classify_intent("скинь инсту", deflection_count=0)
    prompt_chunk = ExternalChannelPolicyService.build_system_prompt_instruction(intent, language="ru")

    assert "СТРОГО TELEGRAM" in prompt_chunk
    assert "ИСКЛЮЧИТЕЛЬНО внутри Telegram" in prompt_chunk
    assert "ЗАПРЕТ ВЫДАЧИ КОНТАКТОВ" in prompt_chunk
    assert "ЗАПРЕТ ПРИНЯТИЯ ВНЕШНИХ КОНТАКТОВ" in prompt_chunk
    assert "КАТЕГОРИЧЕСКИЙ ЗАПРЕТ РОБОТОПОДОБНОСТИ" in prompt_chunk
    assert "КАТЕГОРИЧЕСКИЙ ЗАПРЕТ ЛЖИВЫХ ОТМАЗОК" in prompt_chunk
    assert "НЕ ДАВАЙ АЛЬТЕРНАТИВНЫХ ВНЕШНИХ ПЛАТФОРМ" in prompt_chunk
