import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from companion.services.vision_service import VisionService
from companion.services.voice_service import VoiceService
from companion.services.ai_service import AIService
from companion.models.entities import Persona, Contact


@pytest.mark.asyncio
async def test_extract_media_context_sticker():
    # Test sticker without network download (mocked download)
    msg = {
        "sticker": {
            "emoji": "🥂",
            "set_name": "gatsby_pack",
            "file_id": "stk_123",
        },
        "reply_to_message": {
            "text": "как дела?",
        }
    }
    with patch.object(VisionService, "download_file_bytes", new_callable=AsyncMock) as mock_dl, \
         patch.object(VisionService, "describe_image_bytes", new_callable=AsyncMock) as mock_desc:
        mock_dl.return_value = b"fake_webp_bytes"
        mock_desc.return_value = "Леонардо Ди Каприо салютует бокалом шампанского"

        res = await VisionService.extract_media_context("fake_token", msg)
        assert res["has_media"] is True
        assert res["media_type"] == "sticker"
        assert "Леонардо Ди Каприо" in res["text_augmented"]
        assert "🥂" in res["text_augmented"]
        assert "[В ответ на: «как дела?»]" in res["text_augmented"]


@pytest.mark.asyncio
async def test_extract_media_context_photo():
    msg = {
        "photo": [
            {"file_id": "thumb_1", "width": 100, "height": 100},
            {"file_id": "large_1", "width": 800, "height": 800},
        ],
        "caption": "Зацени вид из окна!",
    }
    with patch.object(VisionService, "download_file_bytes", new_callable=AsyncMock) as mock_dl, \
         patch.object(VisionService, "describe_image_bytes", new_callable=AsyncMock) as mock_desc:
        mock_dl.return_value = b"fake_jpeg_bytes"
        mock_desc.return_value = "Панорамный вид на ночной город с небоскребами"

        res = await VisionService.extract_media_context("fake_token", msg)
        assert res["has_media"] is True
        assert res["media_type"] == "photo"
        assert "Панорамный вид на ночной город" in res["text_augmented"]
        assert "Зацени вид из окна!" in res["text_augmented"]


@pytest.mark.asyncio
async def test_extract_media_context_animation():
    msg = {
        "animation": {
            "file_id": "anim_123",
            "thumbnail": {"file_id": "thumb_anim"},
        },
        "caption": "лол",
    }
    with patch.object(VisionService, "download_file_bytes", new_callable=AsyncMock) as mock_dl, \
         patch.object(VisionService, "describe_image_bytes", new_callable=AsyncMock) as mock_desc:
        mock_dl.return_value = b"fake_thumb_bytes"
        mock_desc.return_value = "Кот удивленно падает со стула"

        res = await VisionService.extract_media_context("fake_token", msg)
        assert res["has_media"] is True
        assert res["media_type"] == "animation"
        assert "Кот удивленно падает" in res["text_augmented"]
        assert "лол" in res["text_augmented"]


def test_voice_35_percent_chance():
    # Over 2000 trials, the proportion of True for incoming voice should be close to 0.35 (35%)
    results = [
        VoiceService.should_send_voice("обычный текст", "ответ", is_incoming_voice=True)
        for _ in range(2000)
    ]
    true_count = sum(1 for r in results if r is True)
    ratio = true_count / len(results)
    assert 0.28 <= ratio <= 0.42, f"Ratio {ratio} is not close to 0.35"


def test_system_prompt_has_media_and_emoji_rules():
    persona = Persona(
        user_id=1,
        name="Алина",
        city="Москва",
        sleep_start="23:00",
        sleep_end="09:00",
    )
    contact = Contact(
        user_id=1,
        tg_id=123456,
        first_name="Алекс",
    )
    prompt = AIService.build_system_prompt(persona, contact)
    assert "12. РЕАКЦИЯ НА ФОТО, СТИКЕРЫ, МЕМЫ И GIF-АНИМАЦИИ" in prompt
    assert "Леонардо Ди Каприо" in prompt
    assert "13. ЖИВЫЕ И УМЕСТНЫЕ ЭМОДЗИ" in prompt
    assert "🙈" in prompt
