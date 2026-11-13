import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from companion.services.stt_service import STTService
from companion.bot.response_planner import ResponsePlanner

@pytest.mark.asyncio
async def test_stt_service_elevenlabs_success():
    """Verify that ElevenLabs Scribe STT is invoked first and returns clean transcription."""
    mock_res = MagicMock()
    mock_res.text = "  А когда ты свободна?  "

    mock_client = MagicMock()
    mock_client.speech_to_text.convert = AsyncMock(return_value=mock_res)

    with patch("elevenlabs.client.AsyncElevenLabs", return_value=mock_client):
        text = await STTService.transcribe_audio_bytes(
            audio_bytes=b"fake_oga_bytes",
            mime_type="audio/ogg"
        )
        assert text == "А когда ты свободна?"
        mock_client.speech_to_text.convert.assert_called_once()

@pytest.mark.asyncio
async def test_stt_service_elevenlabs_fallback_to_gemini_when_eleven_fails():
    """If ElevenLabs throws an error, it seamlessly falls back to Gemini STT."""
    mock_client = MagicMock()
    mock_client.speech_to_text.convert = AsyncMock(side_effect=Exception("ElevenLabs timeout"))

    mock_gemini_res = MagicMock()
    mock_gemini_res.text = "Привет, я слышу тебя"

    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content = AsyncMock(return_value=mock_gemini_res)

    with patch("elevenlabs.client.AsyncElevenLabs", return_value=mock_client):
        with patch("google.genai.Client", return_value=mock_genai_client):
            text = await STTService.transcribe_audio_bytes(
                audio_bytes=b"fake_oga_bytes",
                mime_type="audio/ogg"
            )
            assert text == "Привет, я слышу тебя"

@pytest.mark.asyncio
async def test_stt_clean_transcript():
    """Verify that introductory AI labels or quotes are cleanly removed."""
    raw = '```\n[Голосовое сообщение]: "Привет, ты где?"\n```'
    cleaned = STTService.clean_transcript(raw)
    assert cleaned == "Привет, ты где?"
