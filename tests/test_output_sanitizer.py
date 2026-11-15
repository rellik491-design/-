import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from companion.bot.business_bot import BusinessChatBot
from companion.services.output_sanitizer import OutputSanitizer
from companion.services.presend_validator import PreSendValidator


def test_html_xml_tag_removal_and_examples():
    # Section 2 exact bug examples
    assert OutputSanitizer.sanitize_outgoing_text("<div>Ну у меня такой же 😁 не высокий я</div>") == "Ну у меня такой же 😁 не высокий я"
    assert OutputSanitizer.sanitize_outgoing_text("<div>\nНу у меня такой же 😁 не высокий я\n</div>") == "Ну у меня такой же 😁 не высокий я"
    assert (
        OutputSanitizer.sanitize_outgoing_text("<div>Ну у меня такой же 😁 не высокий я</div><div>ну почему сразу...</div>")
        == "Ну у меня такой же 😁 не высокий я\nну почему сразу..."
    )
    assert (
        OutputSanitizer.sanitize_outgoing_text("ну почему сразу невысокий нормальный рост абсолютно у меня куча знакомых такого же роста</div>")
        == "ну почему сразу невысокий нормальный рост абсолютно у меня куча знакомых такого же роста"
    )

    # All standard + arbitrary/custom tags
    raw = (
        "<span>Привет</span> <b>тест</b> <strong>сильно</strong> <i>курсив</i> "
        "<em>акцент</em> <u>подч</u> <s>зач</s> <a href='https://x.com'>ссылка</a> "
        "<something>кастом</something> <foo>бар</foo>"
    )
    cleaned = OutputSanitizer.sanitize_outgoing_text(raw)
    assert cleaned == "Привет тест сильно курсив акцент подч зач ссылка кастом бар"
    assert "<" not in cleaned
    assert ">" not in cleaned


def test_block_tags_preserve_structure():
    assert OutputSanitizer.sanitize_outgoing_text("<div>Hello</div><div>World</div>") == "Hello\nWorld"
    assert OutputSanitizer.sanitize_outgoing_text("<p>Hello</p><p>World</p>") == "Hello\n\nWorld"
    assert OutputSanitizer.sanitize_outgoing_text("Строка 1<br>Строка 2<br/>Строка 3") == "Строка 1\nСтрока 2\nСтрока 3"
    assert OutputSanitizer.sanitize_outgoing_text("<ul><li>Раз</li><li>Два</li></ul>") == "Раз\nДва"


def test_do_not_delete_actual_content():
    assert OutputSanitizer.sanitize_outgoing_text("<div>привет 😭</div>") == "привет 😭"
    assert OutputSanitizer.sanitize_outgoing_text("<blockquote>да ладно тебе 😊</blockquote>") == "да ладно тебе 😊"


def test_html_entities_and_no_reconstructed_markup():
    assert OutputSanitizer.sanitize_outgoing_text("Tom &amp; Jerry &quot;hi&quot; &#39;ok&#39;") == "Tom & Jerry \"hi\" 'ok'"
    assert OutputSanitizer.sanitize_outgoing_text("5 &lt; 10 и 10 &gt; 5") == "5 < 10 и 10 > 5"
    # Encoded HTML tags must NOT leak as raw tags after decoding
    assert OutputSanitizer.sanitize_outgoing_text("&lt;div&gt;привет&lt;/div&gt;") == "привет"
    assert OutputSanitizer.sanitize_outgoing_text("&lt;script&gt;alert(1)&lt;/script&gt;привет") == "alert(1)привет"


def test_markdown_leakage_removal():
    assert OutputSanitizer.sanitize_outgoing_text("**жирный** и __подчеркнутый__ и ~~зачеркнутый~~") == "жирный и подчеркнутый и зачеркнутый"
    assert OutputSanitizer.sanitize_outgoing_text("напиши `привет` или ```text\nвот так\n```") == "напиши привет или вот так"
    assert OutputSanitizer.sanitize_outgoing_text("### Заголовок\nобычный [текст](https://example.com)") == "Заголовок\nобычный текст"


def test_internal_structured_json_and_markers():
    # Dict / JSON structured output extraction
    raw_json = json.dumps({
        "text": "<div>привет</div>",
        "emotion": "playful",
        "intent": "reply",
        "confidence": 0.94,
    }, ensure_ascii=False)
    assert OutputSanitizer.sanitize_outgoing_text(raw_json) == "привет"

    # Fenced JSON extraction
    fenced = f"```json\n{raw_json}\n```"
    assert OutputSanitizer.sanitize_outgoing_text(fenced) == "привет"

    # JSON without natural-language text field must be dropped
    assert OutputSanitizer.sanitize_outgoing_text('{"emotion": "happy", "confidence": 0.9}') == ""

    # Internal markers & thinking blocks
    with_markers = (
        "<thinking>User is in London, respond warmly</thinking>"
        "[EVENT: greeting][DEBUG: temp=0.7][SYSTEM: ok][MEMORY: saved]"
        "[STATE: chatting][INTENT: reply][REASON: natural][TOOL: none]"
        "привет, как твой вечер?)"
    )
    assert OutputSanitizer.sanitize_outgoing_text(with_markers) == "привет, как твой вечер?)"


def test_preserves_normal_human_punctuation():
    assert OutputSanitizer.sanitize_outgoing_text("привет :) как ты ;) <3") == "привет :) как ты ;) <3"
    assert OutputSanitizer.sanitize_outgoing_text("люблю тебя <33") == "люблю тебя <33"


def test_final_outgoing_validation_and_presend():
    assert OutputSanitizer.validate_final_outgoing("привет, как дела?")[0] is True
    assert OutputSanitizer.validate_final_outgoing("")[0] is False
    assert OutputSanitizer.validate_final_outgoing("<div>привет</div>")[0] is False
    assert OutputSanitizer.validate_final_outgoing("[EVENT: test] привет")[0] is False
    assert OutputSanitizer.validate_final_outgoing('{"text": "hi"}')[0] is False

    ok, reason, cleaned = PreSendValidator.validate_presend(
        candidate_reply="<div>ну привет 😊</div>[DEBUG: x]",
        internal_context={"conversation": {"chat_id": 123}, "message_control": {}},
        chat_id=123,
    )
    assert ok is True
    assert cleaned == "ну привет 😊"


@pytest.mark.asyncio
async def test_business_bot_send_sanitizes_before_telegram():
    captured_payloads = []

    class FakeResp:
        status_code = 200
        text = '{"ok": true, "result": {"message_id": 777}}'

        def json(self):
            return {"ok": True, "result": {"message_id": 777}}

    async def fake_post(url, json=None, **kwargs):
        captured_payloads.append(json)
        return FakeResp()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=fake_post)

    with patch("companion.bot.business_bot.httpx.AsyncClient", return_value=mock_client):
        ok = await BusinessChatBot.send_business_message(
            bot_token="123:ABC",
            business_connection_id="conn_1",
            chat_id=1001,
            text="<div>Ну у меня такой же 😁</div><div>не высокий я</div>[EVENT: test]",
        )
        assert ok is True
        assert len(captured_payloads) == 1
        sent = captured_payloads[0]
        assert sent["text"] == "Ну у меня такой же 😁\nне высокий я"
        assert "parse_mode" not in sent  # Plain text mode (parse_mode = None)

        # Empty/markup-only message must NOT be sent
        ok_empty = await BusinessChatBot.send_business_message(
            bot_token="123:ABC",
            business_connection_id="conn_1",
            chat_id=1001,
            text="<div></div>[DEBUG: empty]",
        )
        assert ok_empty is False
        assert len(captured_payloads) == 1
