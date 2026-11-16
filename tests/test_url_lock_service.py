import pytest

from companion.bot.response_planner import ResponsePlanner
from companion.services.output_sanitizer import OutputSanitizer
from companion.services.presend_validator import PreSendValidator
from companion.services.url_lock_service import ApprovedUrlObject, UrlLockService


@pytest.mark.parametrize(
    "placeholder",
    [
        "[ссылка]",
        "[link]",
        "[ссылочка]",
        "[тут ссылка]",
        "[здесь ссылка]",
        "[тык]",
        "[кликни сюда]",
        "[забронировать]",
        "[booking link]",
        "[ссылка на бронирование]",
        "<URL>",
        "<LINK>",
        "[url]",
        "[URL]",
        "[ссылка здесь]",
        "[ссылка ниже]",
        "[ссылка выше]",
        "[здесь будет ссылка]",
        "[вот ссылка]",
        "[ссылка на сайт]",
        "[ссылка 😭]",
        "[ ссылка ]",
        "[ССЫЛКА]",
        "(ссылка)",
    ],
)
def test_zero_url_placeholders_blocked_and_sanitized(placeholder):
    assert UrlLockService.no_forbidden_url_placeholder(f"Привет {placeholder} пока") is False
    ok, reason = OutputSanitizer.validate_final_outgoing(f"Привет {placeholder}")
    assert ok is False
    assert reason in ("forbidden_url_placeholder_detected", "raw_html_xml_detected")

    cleaned = OutputSanitizer.sanitize_outgoing_text(f"Давай сходим в квест {placeholder}")
    assert placeholder not in cleaned
    assert "[ссылка]" not in cleaned.lower()
    assert "ссылка" not in cleaned.lower()
    ok_after, _ = OutputSanitizer.validate_final_outgoing(cleaned)
    assert ok_after is True


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://example.com",
        "http://example.com",
        "www.example.com",
        "example.com/path",
        "t.me/some_user",
        "telegram.me/some_user",
        "https://venue-name.co.uk/book",
    ],
)
def test_real_urls_detected_and_stripped_without_placeholder(raw_url):
    assert UrlLockService.no_unapproved_url(f"Вот вариант {raw_url}") is False
    cleaned = OutputSanitizer.sanitize_outgoing_text(f"Вот классный вариант: {raw_url}")
    assert raw_url not in cleaned
    assert "[ссылка]" not in cleaned.lower()
    ok, _ = OutputSanitizer.validate_final_outgoing(cleaned)
    assert ok is True


def test_markdown_and_html_link_bypasses_blocked():
    md_cases = [
        "Давай сюда [забронировать](https://example.com)",
        "Глянь [забронировать] (https://example.com/book)",
        "Тут [смотреть](www.example.com)",
        "Нажми [тык](example.com/quest)",
    ]
    for sample in md_cases:
        assert UrlLockService.no_markdown_url(sample) is False
        cleaned = OutputSanitizer.sanitize_outgoing_text(sample)
        assert "example.com" not in cleaned
        assert "[забронировать]" not in cleaned
        assert "[тык]" not in cleaned
        ok, _ = OutputSanitizer.validate_final_outgoing(cleaned)
        assert ok is True

    html_cases = [
        'Посмотри <a href="https://example.com">здесь</a> пожалуйста',
        "Квест <a href='https://example.com'>ссылка</a> крутой",
    ]
    for sample in html_cases:
        assert UrlLockService.no_html_href(sample) is False
        cleaned = OutputSanitizer.sanitize_outgoing_text(sample)
        assert "example.com" not in cleaned
        assert "<a" not in cleaned
        assert "href" not in cleaned
        ok, _ = OutputSanitizer.validate_final_outgoing(cleaned)
        assert ok is True


@pytest.mark.parametrize(
    "obfuscated",
    [
        "hxxp://example.com/book",
        "hxxps://quest.co.uk/tickets",
        "example[.]com/booking",
        "example dot com",
        "example точка com",
    ],
)
def test_obfuscated_urls_blocked(obfuscated):
    assert UrlLockService.no_obfuscated_url(f"Пиши на {obfuscated}") is False
    cleaned = OutputSanitizer.sanitize_outgoing_text(f"Давай глянем {obfuscated}")
    assert obfuscated not in cleaned
    ok, _ = OutputSanitizer.validate_final_outgoing(cleaned)
    assert ok is True


def test_never_replaces_blocked_url_with_placeholder_and_triggers_handoff():
    bad_ai = "О, супер, вот ссылка на квест: https://quest.example/book"
    internal_ctx = {
        "conversation": {"chat_id": 123},
        "meeting_planner": {"stage": "CONFIRMATION", "meeting_control": "AUTO", "handoff_required": False},
    }
    is_valid, reason, sanitized = PreSendValidator.validate_presend(
        candidate_reply=bad_ai,
        internal_context=internal_ctx,
        chat_id=123,
    )
    assert is_valid is True
    assert "https://" not in sanitized
    assert "[ссылка]" not in sanitized
    assert "вот ссылка" not in sanitized.lower()
    assert internal_ctx["meeting_planner"]["handoff_required"] is True
    assert internal_ctx["meeting_planner"]["stage"] == "MEETING_HANDOFF_REQUIRED"
    assert internal_ctx["meeting_planner"]["meeting_control"] == "OWNER"


def test_approved_url_context_validation():
    approved_manchester = ApprovedUrlObject(
        url="https://verified-booking-provider.com/manchester-quest-x",
        purpose="quest_booking",
        city="Manchester",
        venue="Quest X",
        verified=True,
        allowed=True,
    )

    # Valid when city, venue, and purpose match
    assert (
        UrlLockService.validate_approved_url(
            approved_manchester,
            meeting_city="Manchester",
            venue="Quest X",
            purpose="quest_booking",
        )
        is True
    )

    # Rejected when meeting_city is London!
    assert (
        UrlLockService.validate_approved_url(
            approved_manchester,
            meeting_city="London",
            venue="Quest X",
            purpose="quest_booking",
        )
        is False
    )

    # Rejected when venue does not match!
    assert (
        UrlLockService.validate_approved_url(
            approved_manchester,
            meeting_city="Manchester",
            venue="Cinema Y",
            purpose="quest_booking",
        )
        is False
    )


def test_response_planner_output_contract_has_links_array():
    plan = ResponsePlanner.plan_response(
        raw_reply_text="давай тогда на этот квест",
        incoming_batch=[{"message_id": 10, "text": "давай тогда на этот квест"}],
    )
    assert len(plan["messages"]) == 1
    assert plan["messages"][0]["links"] == []
