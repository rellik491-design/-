import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from companion.config import settings
from companion.services.voice_service import VoiceService


async def main():
    print("=" * 60)
    print("🧬 АВТОМАТИЧЕСКОЕ КЛОНИРОВАНИЕ ГОЛОСА В ELEVENLABS")
    print("=" * 60)

    api_key = sys.argv[1].strip() if len(sys.argv) > 1 else settings.ELEVENLABS_API_KEY
    if not api_key:
        print("❌ Ошибка: Укажите ElevenLabs API Key.")
        print("Пример: uv run python scripts/clone_voice.py sk_1234567890abcdef...")
        return

    sample_file = Path("assets/voice_pack/sarcastic_name.ogg")
    if not sample_file.exists():
        print(f"❌ Файл образца не найден: {sample_file}")
        return

    print(f"📁 Исходный аудио-образец: {sample_file} ({sample_file.stat().st_size} байт)")
    print("⏳ Отправляю запрос на Instant Voice Cloning в ElevenLabs API...")

    success, msg, voice_id = await VoiceService.clone_voice_from_sample(
        api_key=api_key, sample_path=sample_file, voice_name="Alina_Companion"
    )

    if not success:
        print(f"❌ Не удалось клонировать голос: {msg}")
        return

    print(f"✅ {msg}")
    print(f"🔑 Voice ID: {voice_id}")
    print("📄 Файл .env успешно обновлен новыми данными.")

    # Generate test sentence
    test_phrase = "Ой, ну привет) Я теперь говорю своим настоящим живым голосом! Как тебе результат, узнаёшь меня?))"
    print(f"\n🎙 Синтезирую контрольную фразу клонированным голосом: «{test_phrase}»...")

    audio_bytes = await VoiceService.generate_voice_bytes(test_phrase)
    if audio_bytes and settings.ADMIN_BOT_TOKEN and settings.ADMIN_TELEGRAM_ID:
        print(f"📨 Отправляю тестовое голосовое в Telegram Администратору (ID: {settings.ADMIN_TELEGRAM_ID})...")
        ok = await VoiceService.send_telegram_voice_note(
            bot_token=settings.ADMIN_BOT_TOKEN,
            chat_id=settings.ADMIN_TELEGRAM_ID,
            text=test_phrase,
            caption="🧬 Тест клонированного голоса (ElevenLabs Instant Voice Cloning)",
        )
        print("Результат отправки:", "✅ Доставлено в Telegram!" if ok else "❌ Ошибка доставки")

    print("\n" + "=" * 60)
    print("🎉 Клонирование завершено! Бот теперь использует этот живой голос.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
