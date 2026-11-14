import asyncio
import io
import logging
import os
import random
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
import imageio_ffmpeg

from companion.config import settings
from companion.models.entities import Subscription

logger = logging.getLogger(__name__)


class VoiceService:
    DEFAULT_FEMALE_VOICE = "ru-RU-SvetlanaNeural"
    VOICE_PACK_DIR = Path("assets/voice_pack")

    # RUSSIAN NATURAL SPEECH PROSODY MASTER PROMPT DIRECTIVE
    RUSSIAN_NATURAL_SPEECH_PROSODY_MASTER_PROMPT = """
    PRIMARY OBJECTIVE:
    Generate Russian speech that sounds like a real person spontaneously speaking,
    not like a TTS system reading written text.
    Natural conversational delivery is more important than robotic clarity.

    1. PHRASE-LEVEL INTONATION & CONNECTED THOUGHTS:
    Speak in thought groups, not isolated chopped sentences.
    Avoid mechanical sentence segmentation.

    2. VARIED SENTENCE ENDINGS:
    Do not end every sentence with the same falling pitch (↓↓↓).
    Differentiate statement, question, unfinished thought, continuation, reaction,
    doubt, sarcasm, and playfulness.

    3. NATURAL QUESTION CONTOURS:
    Do not raise every question with an exaggerated upward spike (↑↑↑).
    Intonation follows intent (curiosity, surprise, teasing, rhetorical).

    4. RUSSIAN WORD STRESS & PRONUNCIATION DICTIONARY:
    Lexical stress must be authentic. Use pronunciation layer for slang,
    messaging acronyms (гс, вк, тг, лс, инста), borrowed words, and verbs.

    5. PHRASE STRESS ≠ WORD STRESS:
    Identify semantic focus of the thought (1-2 important words).
    Do not emphasize every word.

    6. SHORT TELEGRAM REACTIONS:
    Spontaneous, effortless conversational reactions (ну да, серьёзно?, не знаю, да ладно, прикинь).

    7. NO ROBOTIC RHYTHM & NO AI VOICE ACTING:
    No fake smiles, fake breaths, fake giggles, or exaggerated pitch acrobatics.
    Real person thinking and speaking in real time.
    """

    # Section 19: Pronunciation Correction Layer for Russian Conversational Speech
    PRONUNCIATION_DICTIONARY: Dict[str, str] = {
        # Social & Telegram Messenger Slang
        r"\bгс\b": "гэ э́с",
        r"\bвк\b": "вэ ка́",
        r"\bтг\b": "тэ гэ́",
        r"\bлс\b": "эл э́с",
        r"\bсмс\b": "эс эм э́с",
        r"\bсмски\b": "эс эм э́ски",
        r"\bсмску\b": "эс эм э́ску",
        r"\bинста\b": "и́нста",
        r"\bинсте\b": "и́нсте",
        r"\bинсту\b": "и́нсту",
        r"\bдайвинчик\b": "дайви́нчик",
        r"\bдайвинчике\b": "дайви́нчике",
        r"\bдайвинчика\b": "дайви́нчика",
        r"\bдайвинчику\b": "дайви́нчику",
        r"\bтиндер\b": "ти́ндер",
        r"\bтиндере\b": "ти́ндере",
        r"\bтиндеру\b": "ти́ндеру",
        r"\bвойс\b": "войс",
        r"\bвойсы\b": "во́йсы",
        r"\bвойсик\b": "во́йсик",
        r"\bкружок\b": "кружо́к",
        r"\bкружочек\b": "кружо́чек",
        r"\bкружочки\b": "кружо́чки",
        r"\bрилс\b": "рилс",
        r"\bрилсы\b": "ри́лсы",
        r"\bвайб\b": "вайб",
        r"\bвайбовый\b": "вайбо́вый",
        r"\bвайбово\b": "вайбо́во",
        r"\bкринж\b": "кринж",
        r"\bкринжовый\b": "кринжо́вый",
        r"\bкринжово\b": "кринжо́во",
        r"\bкраш\b": "краш",
        r"\bкрашиха\b": "краши́ха",
        r"\bтрек\b": "трэк",
        r"\bтреки\b": "трэ́ки",
        r"\bтрека\b": "трэ́ка",
        r"\bчат\b": "чат",
        r"\bчате\b": "ча́те",
        r"\bчатик\b": "ча́тик",
        r"\bстикер\b": "сти́кер",
        r"\bстикеры\b": "сти́керы",
        r"\bгифка\b": "ги́фка",
        r"\bгифку\b": "ги́фку",
        r"\bгифки\b": "ги́фки",
        r"\bкофе\b": "ко́фэ",
        r"\bкафе\b": "кафэ́",
        r"\bинфо\b": "и́нфо",
        # Irregular & Mobile Russian Stresses
        r"\bзанята\b": "занята́",
        r"\bпоняла\b": "поняла́",
        r"\bспала\b": "спала́",
        r"\bждала\b": "ждала́",
        r"\bбрала\b": "брала́",
        r"\bзвонит\b": "звони́т",
        r"\bзвонят\b": "звоня́т",
        r"\bпозвонит\b": "позвони́т",
        r"\bпозвонят\b": "позвоня́т",
        r"\bвключит\b": "включи́т",
        r"\bвключат\b": "включа́т",
        r"\bкрасивее\b": "краси́вее",
        r"\bоблегчит\b": "облегчи́т",
        r"\bдоговор\b": "догово́р",
        r"\bдоговоры\b": "догово́ры",
        r"\bдоговора\b": "догово́ры",
        r"\bторты\b": "то́рты",
        r"\bбанты\b": "ба́нты",
        r"\bшарфы\b": "ша́рфы",
    }

    @classmethod
    def get_voice_pack_dir(cls) -> Path:
        cls.VOICE_PACK_DIR.mkdir(parents=True, exist_ok=True)
        return cls.VOICE_PACK_DIR

    @classmethod
    def get_voice_asset(cls, tag: str) -> Optional[bytes]:
        """Loads pre-recorded authentic human voice note by tag."""
        folder = cls.get_voice_pack_dir()
        for ext in (".ogg", ".oga", ".wav", ".mp3"):
            f_path = folder / f"{tag}{ext}"
            if f_path.exists():
                with open(f_path, "rb") as f:
                    return f.read()
        return None

    @classmethod
    def save_voice_asset(cls, tag: str, raw_bytes: bytes) -> str:
        """Saves a new human voice note (e.g. sent by admin in Telegram) into the voice pack."""
        folder = cls.get_voice_pack_dir()
        opus_bytes = cls.transcode_to_telegram_opus(raw_bytes)
        target_path = folder / f"{tag}.ogg"
        with open(target_path, "wb") as f:
            f.write(opus_bytes)
        return str(target_path)

    @classmethod
    async def clone_voice_from_sample(
        cls,
        api_key: str,
        sample_path: Optional[Path] = None,
        voice_name: str = "Alina_Companion",
    ) -> tuple[bool, str, Optional[str]]:
        """
        Performs Instant Voice Cloning via ElevenLabs API using the real girl audio sample.
        Returns: (success, message, voice_id)
        """
        path = sample_path or (cls.get_voice_pack_dir() / "sarcastic_name.ogg")
        if not path.exists():
            return False, "Файл аудио-образца не найден на диске.", None

        url = "https://api.elevenlabs.io/v1/voices/add"
        headers = {"xi-api-key": api_key}
        data = {
            "name": voice_name,
            "description": "Young Russian female casual conversational voice from companion sample",
        }

        with open(path, "rb") as f:
            audio_bytes = f.read()

        files = {"files": (path.name, audio_bytes, "audio/ogg")}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(url, headers=headers, data=data, files=files)
                if res.status_code == 200:
                    voice_data = res.json()
                    voice_id = voice_data.get("voice_id")
                    if voice_id:
                        cls._update_env_voice_credentials(api_key, voice_id)
                        return True, "Голос успешно клонирован в ElevenLabs!", voice_id
                else:
                    return False, f"Ошибка ElevenLabs ({res.status_code}): {res.text}", None
        except Exception as e:
            return False, f"Ошибка подключения к ElevenLabs: {e}", None

        return False, "Не удалось получить voice_id от ElevenLabs.", None

    @classmethod
    def _update_env_voice_credentials(cls, api_key: str, voice_id: str):
        """Updates .env file and in-memory settings."""
        env_path = Path(".env")
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            if "ELEVENLABS_API_KEY=" in content:
                content = re.sub(r"ELEVENLABS_API_KEY=.*", f"ELEVENLABS_API_KEY={api_key}", content)
            else:
                content += f"\nELEVENLABS_API_KEY={api_key}"

            if "ELEVENLABS_VOICE_ID=" in content:
                content = re.sub(r"ELEVENLABS_VOICE_ID=.*", f"ELEVENLABS_VOICE_ID={voice_id}", content)
            else:
                content += f"\nELEVENLABS_VOICE_ID={voice_id}"

            env_path.write_text(content, encoding="utf-8")

        settings.ELEVENLABS_API_KEY = api_key
        settings.ELEVENLABS_VOICE_ID = voice_id

    VOICE_TRIGGERS = [
        "гс", "голос", "голосовое", "войс", "запиши",
        "скажи голосом", "послушать тебя", "ты бот",
        "ты реальная", "настоящая", "докажи",
    ]

    @classmethod
    def should_send_voice(cls, user_message: str, bot_reply: str, is_incoming_voice: bool = False) -> bool:
        """
        Decides whether to send a voice message to balance immersion and save ElevenLabs credits:
        1. If the man sent a voice note (is_incoming_voice=True):
           Responds with a real voice note with 35% probability (0.35)!
        2. Returns True if user asks for voice or questions authenticity.
        3. Or randomly returns True with probability settings.VOICE_CHANCE (5%).
        """
        if is_incoming_voice:
            return random.random() < 0.35

        msg_low = user_message.lower()
        for trigger in cls.VOICE_TRIGGERS:
            if trigger in msg_low:
                return True
        chance = getattr(settings, "VOICE_CHANCE", 0.05)
        return random.random() < chance

    @classmethod
    def shorten_for_voice(cls, text: str, max_chars: int = 180) -> str:
        """
        If text is too long for voice (>220 chars), shortens it to first 1-2 sentences
        or up to 150-180 chars for natural conversational brevity.
        """
        if len(text) <= 220:
            return text
        sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
        shortened = ""
        for s in sentences:
            if len(shortened) + len(s) + 1 <= max_chars:
                shortened = f"{shortened} {s}".strip() if shortened else s
            else:
                break
        if not shortened:
            shortened = text[:max_chars].rsplit(" ", 1)[0] + "..."
        return shortened

    @classmethod
    def digits_to_words(cls, text: str) -> str:
        """Converts numbers (0-999) to natural Russian words so TTS never reads raw digits."""
        units = {
            0: "ноль", 1: "один", 2: "два", 3: "три", 4: "четыре", 5: "пять",
            6: "шесть", 7: "семь", 8: "восемь", 9: "девять", 10: "десять",
            11: "одиннадцать", 12: "двенадцать", 13: "тринадцать", 14: "четырнадцать",
            15: "пятнадцать", 16: "шестнадцать", 17: "семнадцать", 18: "восемнадцать",
            19: "девятнадцать",
        }
        tens = {
            20: "двадцать", 30: "тридцать", 40: "сорок", 50: "пятьдесят",
            60: "шестьдесят", 70: "семьдесят", 80: "восемьдесят", 90: "девяносто",
        }
        hundreds = {
            100: "сто", 200: "двести", 300: "триста", 400: "четыреста",
            500: "пятьсот", 600: "шестьсот", 700: "семьсот", 800: "восемьсот", 900: "девятьсот"
        }

        def repl(m):
            val = int(m.group(0))
            if val in units: return units[val]
            if val in tens: return tens[val]
            if val in hundreds: return hundreds[val]
            if 20 < val < 100:
                return f"{tens[val // 10 * 10]} {units[val % 10]}"
            if 100 < val < 1000:
                rem = val % 100
                h = hundreds[val // 100 * 100]
                if rem in units: return f"{h} {units[rem]}"
                if rem in tens: return f"{h} {tens[rem]}"
                return f"{h} {tens[rem // 10 * 10]} {units[rem % 10]}"
            return str(val)

        return re.sub(r"\b\d{1,3}\b", repl, text)

    @classmethod
    def get_dynamic_voice_settings(cls, text: str):
        """
        Dynamically calibrates ElevenLabs voice stability, style, and expression
        based on conversational context, adhering to:
        - NEGATIVE VOICE DIRECTION — STRICT (no decorative laughter, no trembling, stability >= 0.50)
        - MASTER VOICE GENERATION DIRECTIVE (Lika QSaGDZOf5RWf5LPBHH15)
        """
        from elevenlabs import VoiceSettings
        text_low = text.lower()

        # Check mood/intent using clean regex word boundaries \b to avoid substring collisions:
        # 1. Serious / Grounded
        if re.search(r"\b(серьезно|важно|проблема|правда|честно|дело|решим|подумай)\b", text_low) and "?" not in text:
            return VoiceSettings(
                stability=0.60,
                similarity_boost=0.84,
                style=0.03,
                use_speaker_boost=True,
            )
        # 2. Sad / Sympathetic
        elif re.search(r"\b(грустно|жаль|больно|печально|сочувствую|тяжело|обидно|жалко)\b", text_low):
            return VoiceSettings(
                stability=0.58,
                similarity_boost=0.84,
                style=0.04,
                use_speaker_boost=True,
            )
        # 3. Annoyed / Angry
        elif re.search(r"\b(бесит|достало|надоело|злюсь|отстань|хватит|раздражает)\b", text_low):
            return VoiceSettings(
                stability=0.62,
                similarity_boost=0.85,
                style=0.02,
                use_speaker_boost=True,
            )
        # 4. Tired / Night / Sleep
        elif re.search(r"\b(устала|без сил|завал|болею|спать|до завтра|спокойной ночи|добранич|отдыхать|вымоталась)\b", text_low):
            return VoiceSettings(
                stability=0.58,
                similarity_boost=0.84,
                style=0.03,
                use_speaker_boost=True,
            )
        # 5. Sarcastic / Dry deadpan (strictly NO laughter)
        elif re.search(r"\b(ну да конечно|ага конечно|ну прям|сарказм|да неужели)\b", text_low):
            return VoiceSettings(
                stability=0.58,
                similarity_boost=0.84,
                style=0.04,
                use_speaker_boost=True,
            )
        # 6. Curious / Questioning
        elif "?" in text or re.search(r"\b(почему|зачем|где ты|как ты|что думаешь|расскажи|откуда|куда|ты о чем)\b", text_low):
            return VoiceSettings(
                stability=0.52,
                similarity_boost=0.82,
                style=0.08,
                use_speaker_boost=True,
            )
        # 7. Excited / Joyful (buoyant, but stability strictly >= 0.50)
        elif re.search(r"\b(супер|ура|круто|ого|кайф|отлично|поздравляю|восторг|шикарно)\b", text_low):
            return VoiceSettings(
                stability=0.50,
                similarity_boost=0.80,
                style=0.10,
                use_speaker_boost=True,
            )
        # 8. Flirty / Warm & intimate (clean, no over-breathiness)
        elif re.search(r"\b(скучаю|милый|красивый|нравишься|обнимаю|целую|жду тебя|притягательный)\b", text_low):
            return VoiceSettings(
                stability=0.54,
                similarity_boost=0.82,
                style=0.06,
                use_speaker_boost=True,
            )
        # 9. Playful (strictly NO laughter, subtle conversational smile)
        elif re.search(r"\b(хитрая|подкалываешь|шутник|ну ты даешь|ловко)\b", text_low):
            return VoiceSettings(
                stability=0.54,
                similarity_boost=0.82,
                style=0.07,
                use_speaker_boost=True,
            )
        # 10. Default Natural Casual / Neutral
        else:
            return VoiceSettings(
                stability=0.54,
                similarity_boost=0.82,
                style=0.05,
                use_speaker_boost=True,
            )

    @classmethod
    def prepare_text_for_tts(cls, text: str) -> str:
        """
        Prepares conversational Russian text for ultra-realistic ElevenLabs TTS
        via the Advanced Prosody Controller & Pronunciation Dictionary.
        """
        if not text:
            return ""
        from companion.services.prosody_controller import AdvancedProsodyController

        prosody_obj = AdvancedProsodyController.process_for_tts(
            text=text,
            digits_to_words_fn=cls.digits_to_words,
        )
        return prosody_obj.speech_text

    # Studio-grade Mastering Equalizer & Subtle Smartphone Recording Texture:
    # 1. Low Cut 62Hz 24dB/oct (cascaded 2-pole highpass)
    # 2. Bell 194Hz -2.33dB, Bell 437Hz -1.12dB, Bell 800Hz -1.45dB, Bell 2841Hz +1dB, High Shelf 6678Hz +0.60dB
    # 3. Softened extreme highs (lowpass 12.5kHz)
    # 4. Subtle harmonic density (asoftclip type=atan param=0.85)
    # 5. Slight natural dynamic compression (acompressor ratio=2.2 makeup=1.2dB)
    # 6. Very subtle low-level mic noise floor (-66dB pink noise bed)
    # 7. Safe peak limiting (alimiter -1.0dB)
    MASTERING_EQ_FILTER = (
        "highpass=f=62:poles=2,highpass=f=62:poles=2,"
        "equalizer=f=194:width_type=q:width=1.0:gain=-2.33,"
        "equalizer=f=437:width_type=q:width=1.0:gain=-1.12,"
        "equalizer=f=800:width_type=q:width=1.0:gain=-1.45,"
        "equalizer=f=2841:width_type=q:width=1.0:gain=1.0,"
        "highshelf=f=6678:gain=0.60"
    )

    SUBTLE_TEXTURE_FILTER_COMPLEX = (
        f"[0:a]{MASTERING_EQ_FILTER},"
        "acompressor=threshold=-18dB:ratio=2.6:attack=12:release=90:makeup=8.0dB,"
        "asoftclip=type=atan:param=1.10,"
        "lowpass=f=12500[voice];"
        "anoisesrc=color=pink:amplitude=0.007:sample_rate=48000,highpass=f=100,lowpass=f=7200[noise];"
        "[voice][noise]amix=inputs=2:duration=first:dropout_transition=0:weights=1.0 1.0:normalize=0,"
        "alimiter=limit=-1.0dB:attack=5:release=50[out]"
    )

    @classmethod
    def apply_mastering_equalizer(cls, raw_audio: bytes) -> bytes:
        """
        Fast studio-grade post-processing equalizer & subtle recording texture chain:
        - Low Cut 62HZ 24dB/oct (two cascaded 2-pole highpass filters at 62 Hz)
        - Bell 194hz -2.33dB, Bell 437hz -1.12dB, Bell 800hz -1.45dB, Bell 2841hz +1dB, High Shelf 6678hz +0.60db
        - Subtle smartphone recording texture: harmonic density (softclip), gentle compression,
          softened extreme highs, very subtle low-level mic noise floor (-66dB pink bed) and limiter.
        Converts any audio stream to Telegram-native 48kHz mono libopus Ogg voice note.
        """
        if not raw_audio or len(raw_audio) < 10:
            return raw_audio

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f_in:
            f_in.write(raw_audio)
            in_path = f_in.name

        out_path = in_path + ".ogg"
        cmd = [
            ffmpeg_exe,
            "-y",
            "-i", in_path,
            "-filter_complex", cls.SUBTLE_TEXTURE_FILTER_COMPLEX,
            "-map", "[out]",
            "-c:a", "libopus",
            "-b:a", "48k",
            "-vbr", "on",
            "-ar", "48000",
            "-ac", "1",
            "-application", "voip",
            out_path,
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0 and os.path.exists(out_path):
                with open(out_path, "rb") as f_out:
                    return f_out.read()
        except Exception as e:
            logger.error("Error in apply_mastering_equalizer: %s", e)
        finally:
            if os.path.exists(in_path):
                try:
                    os.remove(in_path)
                except OSError:
                    pass
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass

        return raw_audio

    @classmethod
    def apply_phone_mic_filter(cls, raw_mp3: bytes) -> bytes:
        """
        Applies authentic smartphone acoustic mastering using the custom equalizer chain.
        """
        return cls.apply_mastering_equalizer(raw_mp3)

    @classmethod
    def humanize_text_for_speech(cls, text: str) -> str:
        return cls.prepare_text_for_tts(text)

    @classmethod
    def transcode_to_telegram_opus(cls, raw_audio: bytes, prepend_breath: bool = False) -> bytes:
        """
        Converts any input audio stream (MP3/WAV/OGG) to genuine Telegram Voice OGG Opus:
        - Codec: libopus
        - Sample rate: 48000 Hz
        - Channels: 1 (mono)
        - Application: voip (optimized for human speech)
        - Audio Filter: clean smartphone acoustics (80Hz highpass, 16kHz lowpass, presence boost, gentle compression)
        - Full Ogg Opus granule headers so Telegram generates jumping voice waveform bars!
        """
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f_in:
            f_in.write(raw_audio)
            in_path = f_in.name

        out_path = in_path.replace(".mp3", ".ogg")
        breath_wav = cls.get_voice_pack_dir() / "breath.wav"

        if prepend_breath and breath_wav.exists():
            cmd = [
                ffmpeg_exe,
                "-y",
                "-i",
                str(breath_wav),
                "-i",
                in_path,
                "-filter_complex",
                "[0:a][1:a]concat=n=2:v=0:a=1[a];[a]highpass=f=80,lowpass=f=16000,equalizer=f=3200:width_type=q:width=1.2:g=2.5,acompressor=threshold=-18dB:ratio=2.5:attack=15:release=120[out]",
                "-map",
                "[out]",
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                "-vbr",
                "on",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-application",
                "voip",
                out_path,
            ]
        else:
            cmd = [
                ffmpeg_exe,
                "-y",
                "-i",
                in_path,
                "-af",
                (
                    "highpass=f=80,"
                    "lowpass=f=16000,"
                    "equalizer=f=3200:width_type=q:width=1.2:g=2.5,"
                    "acompressor=threshold=-18dB:ratio=2.5:attack=15:release=120"
                ),
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                "-vbr",
                "on",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-application",
                "voip",
                out_path,
            ]

        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0 and os.path.exists(out_path):
                with open(out_path, "rb") as f_out:
                    opus_bytes = f_out.read()
                return opus_bytes
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)

        return raw_audio

    @classmethod
    def pcm_to_telegram_opus(cls, pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
        """
        Converts raw 16-bit mono PCM stream (from Gemini 2.0 Flash Audio)
        to genuine Telegram OGG Opus (48kHz mono, VOIP optimized).
        """
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as f_in:
            f_in.write(pcm_bytes)
            in_path = f_in.name

        out_path = in_path.replace(".pcm", ".ogg")
        cmd = [
            ffmpeg_exe,
            "-y",
            "-f", "s16le",
            "-ar", str(sample_rate),
            "-af", (
                "highpass=f=200,"
                "lowpass=f=5500,"
                "equalizer=f=1000:width_type=q:width=1.5:g=4.0,"
                "aecho=0.8:0.88:10:0.12"
            ),
            "-c:a", "libopus",
            "-b:a", "18k",
            "-vbr", "on",
            "-ar", "24000",
            "-ac", "1",
            out_path,
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0 and os.path.exists(out_path):
                with open(out_path, "rb") as f_out:
                    return f_out.read()
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)
        return pcm_bytes


    @classmethod
    async def can_send_voice(cls, session: AsyncSession, subscription: Subscription) -> bool:
        """Checks and resets daily voice limit for subscription."""
        now = datetime.now(timezone.utc)
        today = now.date()

        if subscription.last_reset_date:
            last_date = (
                subscription.last_reset_date.date()
                if isinstance(subscription.last_reset_date, datetime)
                else subscription.last_reset_date
            )
            if last_date != today:
                subscription.voice_used_today = 0
                subscription.last_reset_date = now
        else:
            subscription.last_reset_date = now

        if subscription.voice_used_today >= subscription.max_daily_voice:
            return False

        return True

    @classmethod
    async def record_voice_used(cls, session: AsyncSession, subscription: Subscription) -> None:
        """Increments voice count for today."""
        subscription.voice_used_today += 1
        await session.commit()

    @classmethod
    async def send_chat_action(
        cls,
        bot_token: str,
        chat_id: int,
        action: str = "record_voice",
        business_connection_id: Optional[str] = None,
    ) -> bool:
        """Sends chat action status like 'record_voice' or 'typing'."""
        url = f"https://api.telegram.org/bot{bot_token}/sendChatAction"
        payload = {"chat_id": chat_id, "action": action}
        if business_connection_id:
            payload["business_connection_id"] = str(business_connection_id)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                return res.status_code == 200
        except Exception:
            return False

    @classmethod
    async def send_text_fallback(
        cls,
        bot_token: str,
        chat_id: int,
        text: str,
        business_connection_id: Optional[str] = None,
        reply_markup: Optional[dict] = None,
    ) -> bool:
        """Fallback to regular text message if voice synthesis or sending fails."""
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if business_connection_id:
            payload["business_connection_id"] = str(business_connection_id)
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                return res.status_code == 200
        except Exception:
            return False

    @classmethod
    async def generate_voice_bytes(
        cls,
        text: str,
        voice_name: Optional[str] = None,
        chat_id: int = 0,
        chat_history: Optional[List[Dict[str, Any]]] = None,
    ) -> bytes:
        """
        Generates ultra-realistic female voice audio bytes using ElevenLabs SDK with smartphone mic acoustics.
        Routes speech through AdvancedProsodyController for authentic Russian intonations, stress, and cadence.
        """
        from companion.services.prosody_controller import AdvancedProsodyController

        prosody_obj = AdvancedProsodyController.process_for_tts(
            text=text,
            chat_id=chat_id,
            chat_history=chat_history,
            digits_to_words_fn=cls.digits_to_words,
        )
        spoken_text = prosody_obj.speech_text or text

        # 1. Primary: Official ElevenLabs SDK (eleven_multilingual_v2 with native Russian female voice)
        elevenlabs_key = settings.ELEVENLABS_API_KEY
        if elevenlabs_key:
            voice_id = settings.ELEVENLABS_VOICE_ID or "WmNeiBsHQpj7dNcrBhAX"

            def _synthesize_elevenlabs() -> Optional[bytes]:
                try:
                    from elevenlabs.client import ElevenLabs
                    from elevenlabs import VoiceSettings

                    client = ElevenLabs(api_key=elevenlabs_key)
                    dynamic_vs = VoiceSettings(
                        stability=prosody_obj.stability,
                        similarity_boost=prosody_obj.similarity_boost,
                        style=prosody_obj.style,
                        use_speaker_boost=True,
                    )
                    models_to_try = ["eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_flash_v2_5"]
                    for model in models_to_try:
                        try:
                            audio_stream = client.text_to_speech.convert(
                                voice_id=voice_id,
                                text=spoken_text,
                                model_id=model,
                                language_code="ru",
                                voice_settings=dynamic_vs,
                            )
                            raw_mp3 = b"".join(audio_stream)
                            if raw_mp3:
                                return raw_mp3
                        except Exception as err:
                            logger.warning("ElevenLabs model %s failed: %s (%s)", model, type(err).__name__, err)
                except Exception as ex:
                    logger.warning("Failed to initialize ElevenLabs client: %s (%s)", type(ex).__name__, ex)
                return None

            try:
                raw_mp3 = await asyncio.to_thread(_synthesize_elevenlabs)
                if raw_mp3:
                    return cls.apply_phone_mic_filter(raw_mp3)
            except Exception as e:
                logger.warning("ElevenLabs SDK generation failed: %s (%s). Trying fallback.", type(e).__name__, e)

        # 2. High-fidelity Edge Neural TTS fallback (ru-RU-SvetlanaNeural)
        import edge_tts
        target_voice = voice_name or cls.DEFAULT_FEMALE_VOICE
        communicate = edge_tts.Communicate(
            spoken_text,
            target_voice,
            rate="-5%",
            pitch="+2Hz",
        )
        audio_stream = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_stream.write(chunk["data"])
        raw_mp3 = audio_stream.getvalue()

        return cls.apply_phone_mic_filter(raw_mp3)

    @classmethod
    async def send_telegram_voice_note(
        cls,
        bot_token: str,
        chat_id: int,
        text: Optional[str] = None,
        voice_tag: Optional[str] = None,
        caption: Optional[str] = None,
        reply_markup: Optional[dict] = None,
        business_connection_id: Optional[str] = None,
        chat_history: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        Sends genuine voice note directly via Telegram Bot API sendVoice.
        Emulates realistic recording duration with record_voice chat action.
        On error, safely falls back to text reply without crashing.
        """
        if not bot_token or bot_token in ("mock_token", "test_token"):
            return True

        try:
            audio_bytes = None
            if voice_tag:
                raw_tag_bytes = cls.get_voice_asset(voice_tag)
                if raw_tag_bytes:
                    audio_bytes = cls.apply_mastering_equalizer(raw_tag_bytes)
                if audio_bytes:
                    await cls.send_chat_action(bot_token, chat_id, "record_voice", business_connection_id)
                    await asyncio.sleep(random.uniform(0.8, 1.5))

            if not audio_bytes and text:
                # Concurrent generation & record_voice action:
                # The chat shows "записывает голосовое..." WHILE ElevenLabs is generating in parallel,
                # eliminating 3-5 seconds of dead waiting latency!
                action_task = asyncio.create_task(
                    cls.send_chat_action(bot_token, chat_id, "record_voice", business_connection_id)
                )
                min_wait = min(max(len(text) / 35.0, 1.0), 2.5)
                audio_task = asyncio.create_task(
                    cls.generate_voice_bytes(
                        text=text,
                        chat_id=chat_id,
                        chat_history=chat_history,
                    )
                )

                # Wait for both generation and minimal realistic voice recording duration
                await asyncio.gather(action_task, asyncio.sleep(min_wait))
                audio_bytes = await audio_task

            if not audio_bytes:
                if text:
                    return await cls.send_text_fallback(bot_token, chat_id, text, business_connection_id, reply_markup)
                return False

            url = f"https://api.telegram.org/bot{bot_token}/sendVoice"
            files = {"voice": ("voice.ogg", audio_bytes, "audio/ogg")}
            data = {"chat_id": str(chat_id)}
            if business_connection_id:
                data["business_connection_id"] = str(business_connection_id)
            if caption:
                data["caption"] = caption
                data["parse_mode"] = "HTML"
            if reply_markup:
                import json
                data["reply_markup"] = json.dumps(reply_markup)

            async with httpx.AsyncClient(timeout=40.0) as client:
                res = await client.post(url, data=data, files=files)
                if res.status_code == 200:
                    return True
                else:
                    logger.warning("Telegram sendVoice failed with status %s: %s", res.status_code, res.text)
                    if text:
                        return await cls.send_text_fallback(bot_token, chat_id, text, business_connection_id, reply_markup)
                    return False
        except Exception as e:
            logger.error("Exception in send_telegram_voice_note: %s (%s). Falling back to text.", type(e).__name__, e, exc_info=True)
            if text:
                return await cls.send_text_fallback(bot_token, chat_id, text, business_connection_id, reply_markup)
            return False
