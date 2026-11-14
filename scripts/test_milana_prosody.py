import asyncio
from companion.config import settings
from companion.services.voice_service import VoiceService
from companion.services.prosody_controller import AdvancedProsodyController

async def main():
    chat_id = settings.ADMIN_TELEGRAM_ID
    bot_token = settings.ADMIN_BOT_TOKEN
    test_phrase = (
        "Да я просто не ожидала, что ты мне напишешь. "
        "Ну да, я только домой зашла, кофе пью и смотрю дайвинчик. "
        "Занята немного была, скинь мне гс в тг, ладно?"
    )
    
    print(f"Testing Advanced Prosody Controller with text:\n{test_phrase}")
    obj = AdvancedProsodyController.process_for_tts(test_phrase, chat_id=chat_id)
    print("\n[PROSODY ANALYSIS]")
    print(f"Speech Text: {obj.speech_text}")
    print(f"Emotion: {obj.emotion} (intensity {obj.emotion_intensity})")
    print(f"Ending Type: {obj.ending_type}")
    print(f"Question Type: {obj.question_type}")
    print(f"Stability: {obj.stability}, Style: {obj.style}")
    print(f"Overrides: {obj.pronunciation_overrides}")

    print("\nGenerating and sending voice note to admin...")
    success = await VoiceService.send_telegram_voice_note(
        bot_token=bot_token,
        chat_id=chat_id,
        text=test_phrase,
        caption="🎙 <b>Milana • Advanced Prosody Controller Test</b>\n<i>«Да я просто не ожидала, что ты мне напишешь...»</i>",
    )
    print(f"Voice note delivered successfully: {success}")

if __name__ == "__main__":
    asyncio.run(main())
