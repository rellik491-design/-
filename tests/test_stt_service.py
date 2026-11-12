import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from companion.models import (
    init_db,
    async_session_maker,
    User,
    Persona,
    Contact,
    AccountSession,
    PlanDuration,
)
from companion.models.enums import AccountStatus, ContactStatus
from companion.services.stt_service import STTService
from companion.services.subscription_service import SubscriptionService
from companion.bot.business_bot import BusinessChatBot
from companion.bot.userbot_handler import UserbotHandler
from companion.bot.telegram_bot_engine import TelegramBotEngine


def test_stt_clean_transcript():
    assert STTService.clean_transcript("") == ""
    assert STTService.clean_transcript('   "Привет, как дела?"  ') == "Привет, как дела?"
    assert STTService.clean_transcript("«Давай сходим на кофе»") == "Давай сходим на кофе"
    assert STTService.clean_transcript("Транскрипция: приветик, ты где?") == "приветик, ты где?"
    assert STTService.clean_transcript("[Голосовое сообщение]: ахах, понятно всё)") == "ахах, понятно всё)"
    assert STTService.clean_transcript("```text\nПривет, я только освободился\n```") == "Привет, я только освободился"


@pytest.mark.asyncio
async def test_stt_transcribe_audio_bytes_mock_gemini():
    mock_resp = MagicMock()
    mock_resp.text = '  "Давай сегодня вечером встретимся в центре"  '

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        fake_audio = b"FAKE_OGG_BYTES_OPUS"
        with patch.object(STTService, "_transcribe_via_groq", return_value=None):
            result = await STTService.transcribe_audio_bytes(fake_audio, mime_type="audio/ogg")
            assert result == "Давай сегодня вечером встретимся в центре"


@pytest.mark.asyncio
async def test_stt_transcribe_telegram_voice_mock():
    fake_bytes = b"OGG_BINARY_DATA"
    with patch.object(STTService, "download_telegram_file", new_callable=AsyncMock) as mock_download:
        mock_download.return_value = fake_bytes
        with patch.object(STTService, "transcribe_audio_bytes", new_callable=AsyncMock) as mock_transcribe:
            mock_transcribe.return_value = "Привет, ты чем сейчас занята?"

            text, data = await STTService.transcribe_telegram_voice(
                bot_token="test_token",
                file_id="voice_file_12345",
            )
            assert text == "Привет, ты чем сейчас занята?"
            assert data == fake_bytes
            mock_download.assert_called_once_with("test_token", "voice_file_12345")


@pytest.mark.asyncio
async def test_business_chat_incoming_voice_message_pipeline():
    """
    Tests end-to-end receipt of an incoming voice note from a male interlocutor:
    1. STT transcribes the audio note to text.
    2. Intent service detects meetup proposal from the spoken words.
    3. AI generates natural reply taking voice into account.
    4. Admin receives high-priority alert with exact speech transcript and duration.
    """
    await init_db()

    async with async_session_maker() as session:
        # 1. Setup user & active subscription
        from sqlalchemy import select
        stmt_u = select(User).where(User.telegram_id == 777000111)
        user = (await session.execute(stmt_u)).scalars().first()
        if not user:
            user = User(telegram_id=777000111, username="owner_business_voice")
            session.add(user)
            await session.commit()
            await session.refresh(user)

        await SubscriptionService.activate_plan(session, user.id, PlanDuration.DAYS_14)

        # 2. Setup connected account
        stmt_acc = select(AccountSession).where(AccountSession.business_connection_id == "conn_voice_test_1")
        account = (await session.execute(stmt_acc)).scalars().first()
        if not account:
            account = AccountSession(
                user_id=user.id,
                business_connection_id="conn_voice_test_1",
                tg_user_id=123999,
                is_active=True,
                status=AccountStatus.CONNECTED,
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
        else:
            account.is_active = True
            account.status = AccountStatus.CONNECTED
            await session.commit()

        # Clean any existing contact for clean run
        stmt_c = select(Contact).where(Contact.tg_id == 888111222)
        old_c = (await session.execute(stmt_c)).scalars().first()
        if old_c:
            await session.delete(old_c)
            await session.commit()

        # 3. Simulate incoming business_message with voice
        voice_update = {
            "business_connection_id": "conn_voice_test_1",
            "chat": {"id": 888111222},
            "from": {"id": 888111222, "first_name": "Максим", "username": "max_contact"},
            "text": "",  # Empty text because user sent a voice note!
            "voice": {
                "file_id": "tg_voice_file_abc",
                "duration": 7,
                "mime_type": "audio/ogg",
            },
        }

        # Mock STT to transcribe the man's speech
        spoken_words = "Привет! Слушай, давай сегодня поужинаем вместе в ресторанчике?"
        with patch.object(STTService, "transcribe_telegram_voice", new_callable=AsyncMock) as mock_stt:
            mock_stt.return_value = (spoken_words, b"fake_bytes")

            with patch("companion.bot.admin_notifier.AdminNotifier.send_date_proposed_alert", new_callable=AsyncMock) as mock_alert:
                mock_alert.return_value = True

                with patch("companion.services.ai_service.AIService.generate_reply", new_callable=AsyncMock) as mock_ai:
                    async def fake_reply(*args, **kwargs):
                        c = kwargs.get("contact") or (args[1] if len(args) > 1 else None)
                        if c:
                            setattr(c, "last_event", "DATE_PROPOSED")
                        return "ахах ты быстрый какой) мы только познакомились, давай хоть пообщаемся немного) ты чем вообще занимаешься?"

                    mock_ai.side_effect = fake_reply

                    res = await BusinessChatBot.process_business_message(
                        session=session,
                        message_data=voice_update,
                        bot_token="test_token",
                        simulate_delays=False,
                    )

                    assert res["action"] == "reply"
                    assert "быстрый какой" in res["reply_text"]
                    assert mock_stt.called

                    # Verify that the alert to owner received the voice note duration & transcript
                    assert mock_alert.called
                    alert_args = mock_alert.call_args[1]
                    assert "7 сек" in alert_args["user_message"]
                    assert spoken_words in alert_args["user_message"]


@pytest.mark.asyncio
async def test_userbot_handler_voice_metadata_handling():
    await init_db()

    async with async_session_maker() as session:
        from sqlalchemy import select
        stmt_u = select(User).where(User.telegram_id == 555666777)
        user = (await session.execute(stmt_u)).scalars().first()
        if not user:
            user = User(telegram_id=555666777, username="sub_owner_voice")
            session.add(user)
            await session.commit()
            await session.refresh(user)

        await SubscriptionService.activate_plan(session, user.id, PlanDuration.DAYS_7)

        with patch("companion.services.ai_service.AIService.generate_reply", new_callable=AsyncMock) as mock_ai:
            mock_ai.return_value = "послушала твое голосовое) классный голос кстати)"

            res = await UserbotHandler.process_incoming_message(
                session=session,
                user_id=user.id,
                sender_tg_id=1234567,
                sender_username="guy1",
                sender_first_name="Иван",
                text="Привет, как проходит твой день?",
                is_voice=True,
                voice_duration=15,
            )

            assert res["action"] == "reply"
            assert res["is_voice"] is True
            assert res["voice_duration"] == 15
            assert mock_ai.called
            call_kwargs = mock_ai.call_args[1]
            assert call_kwargs.get("is_voice") is True
            assert call_kwargs.get("voice_duration") == 15


@pytest.mark.asyncio
async def test_telegram_bot_engine_voice_stt_interaction():
    await init_db()

    bot = TelegramBotEngine("mock_token", 8501711204)
    bot.send_message = AsyncMock(return_value=True)
    bot.edit_message = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)

    # 1. Test voice note filling legend wizard by voice!
    uid = 8501711204
    bot._user_input_states[uid] = "WAIT_LEGEND_WIZARD"

    voice_msg = {
        "chat": {"id": uid},
        "from": {"id": uid, "username": "admin"},
        "voice": {"file_id": "wizard_voice_id", "duration": 10, "mime_type": "audio/ogg"},
    }

    voice_transcript = "Меня зовут Алина, мне 22 года, живу в Москве, учусь на дизайне и люблю кофе"
    with patch.object(STTService, "transcribe_telegram_voice", new_callable=AsyncMock) as mock_stt:
        mock_stt.return_value = (voice_transcript, b"audio_bytes")

        with patch("companion.services.ai_service.AIService.parse_legend_wizard", new_callable=AsyncMock) as mock_wizard:
            mock_wizard.return_value = {
                "name": "Алина",
                "age": 22,
                "city": "Москва",
                "study_work_hobbies": "учусь на дизайне и люблю кофе",
            }

            await bot.handle_message(voice_msg)

            assert mock_stt.called
            assert mock_wizard.called
            assert bot.send_message.called
            sent_texts = [call[1].get("text") or call[0][1] for call in bot.send_message.call_args_list]
            assert any("Распознано" in t for t in sent_texts)
            assert any("Анкета девушки успешно заполнена" in t for t in sent_texts)

    # 2. Test STT Info callback in voice menu
    cq_stt_info = {
        "id": "cq_stt_1",
        "data": "voice_stt_info",
        "message": {"message_id": 101, "chat": {"id": uid}},
        "from": {"id": uid, "username": "admin"},
    }
    await bot.handle_callback(cq_stt_info)
    assert bot.edit_message.called
    stt_info_text = bot.edit_message.call_args[0][2]
    assert "РАСПОЗНАВАНИЕ ГОЛОСА (SPEECH-TO-TEXT)" in stt_info_text

    # 3. Test stt_transcribe_ callback button
    with patch.object(STTService, "transcribe_telegram_voice", new_callable=AsyncMock) as mock_stt:
        mock_stt.return_value = ("Привет, как дела?", b"audio_bytes")
        cq_transcribe = {
            "id": "cq_stt_2",
            "data": "stt_transcribe_sample_file_id",
            "message": {"message_id": 102, "chat": {"id": uid}},
            "from": {"id": uid, "username": "admin"},
        }
        await bot.handle_callback(cq_transcribe)
        assert bot.send_message.called
        res_texts = [call[1].get("text") or call[0][1] for call in bot.send_message.call_args_list]
        assert any("РЕЧЬ УСПЕШНО РАСПОЗНАНА" in t for t in res_texts)
        assert any("Привет, как дела?" in t for t in res_texts)
