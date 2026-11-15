import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath("src"))

from companion.services.voice_service import VoiceService

async def main():
    test_phrases = [
        "скинь мне гс пожалуйста, я сейчас кофе пью в кафе и смотрю дайвинчик",
        "я тебе в гс всё подробно рассказала, напиши мне потом в лс или в тг",
        "лол, это реально изи, мб завтра вместе погуляем?",
    ]

    print("=== TESTING SLANG, 'Е' & VOICE PREPARATION ===")
    for phrase in test_phrases:
        prep = VoiceService.prepare_text_for_tts(phrase)
        print(f"RAW:  '{phrase}'")
        print(f"PREP: '{prep}'\n")
        assert "гэ эс" not in prep
        assert "ко́фэ" not in prep
        assert "кафэ́" not in prep

    print("=== TESTING REAL ELEVENLABS TTS + SMARTPHONE DIRT LAYER ===")
    test_phrase = "скинь мне гс в тг, я сейчас кофе пью и смотрю дайвинчик"
    audio_bytes = await VoiceService.generate_voice_bytes(test_phrase)
    print(f"Generated audio bytes: {len(audio_bytes)}")
    assert len(audio_bytes) > 1000
    assert audio_bytes[:4] == b"OggS"

    out_file = "assets/voice_pack/test_smartphone_voice.ogg"
    with open(out_file, "wb") as f:
        f.write(audio_bytes)
    print(f"Successfully saved sample to: {out_file}")
    print("ALL CHECKS COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(main())
