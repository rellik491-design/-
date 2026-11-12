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
        based on conversational context:
        - Joyful/flirt/laughter: lower stability (0.38) & higher style (0.30) for lively pitch, giggles, breathing.
        - Curious/questioning: stability 0.42 & style 0.26 for natural questioning inflections.
        - Serious/tired: stability 0.50 & style 0.15 for calmer, softer, slower cadence.
        - Standard casual: stability 0.44 & style 0.22 for balanced, warm human speech.
        """
        from elevenlabs import VoiceSettings
        text_low = text.lower()

        # 1. Joyful / Flirt / Laughter / Good news
        if any(w in text_low for w in ["ахах", "хах", "хихи", "класс", "супер", "круто", "ура", "мило", "обожаю", "ого", "красиво", "отлично", "кайф"]):
            return VoiceSettings(
                stability=0.38,
                similarity_boost=0.76,
                style=0.30,
                use_speaker_boost=True,
            )
        # 2. Curious / Questioning / Interested
        elif "?" in text or any(w in text_low for w in ["серьезно", "правда", "а ты", "расскажи", "почему", "зачем", "где ты", "как ты", "что думаешь"]):
            return VoiceSettings(
                stability=0.42,
                similarity_boost=0.78,
                style=0.26,
                use_speaker_boost=True,
            )
        # 3. Serious / Tired / Calmer tone
        elif any(w in text_low for w in ["устала", "без сил", "тяжелый", "завал", "болею", "грустно", "не знаю", "спать", "до завтра"]):
            return VoiceSettings(
                stability=0.50,
                similarity_boost=0.80,
                style=0.15,
                use_speaker_boost=True,
            )
        # 4. Natural balanced conversational tone
        else:
            return VoiceSettings(
                stability=0.44,
                similarity_boost=0.78,
                style=0.22,
                use_speaker_boost=True,
            )

    @classmethod
    def prepare_text_for_tts(cls, text: str) -> str:
        """
        Prepares conversational Russian text for ultra-realistic ElevenLabs TTS:
        - Removes emojis, brackets ))), stars *, URLs and @mentions
        - Converts numbers into Russian words
        - Enforces 'ё' on frequent words to prevent wrong stress
        - Enhances natural breathing punctuation (..., —, commas)
        - Inserts organic breath pauses and hesitation markers
        """
        # 1. Strip URLs and @mentions
        clean = re.sub(r"https?://\S+|t\.me/\S+|@\w+", "", text)
        # 2. Strip emojis
        clean = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27ff\u2300-\u23ff\u2b50\u200d\ufe0f]", "", clean)
        # 3. Strip asterisks and combining stress marks, and replace '!' with '.'
        clean = clean.replace("*", "").replace("\u0301", "").replace("!", ".")
        # 4. Convert smileys )) and (( into conversational pauses and soft chuckles
        clean = re.sub(r"\){3,}", "... ахах... ", clean)
        clean = re.sub(r"\){1,2}", "... ", clean)
        clean = re.sub(r"\({2,}", "... ой... ", clean)
        clean = re.sub(r"\({1}", "... ", clean)
        # 5. Convert numbers to words
        clean = cls.digits_to_words(clean)

        # Helper for case-preserving replacement
        def apply_replacements(s: str, mapping: Dict[str, str]) -> str:
            for pattern, rep in mapping.items():
                def make_repl(target_rep: str):
                    def repl_fn(m):
                        val = m.group(0)
                        if val.isupper():
                            return target_rep.upper()
                        elif val[0].isupper():
                            return target_rep.capitalize()
                        return target_rep
                    return repl_fn
                s = re.sub(pattern, make_repl(rep), s, flags=re.IGNORECASE)
            return s

        # Stage 1: Auto-replace 'е' -> 'ё' ('ё' in ElevenLabs always pulls stress 100%)
        # "все" -> "всё", кроме ("все мы", "все они", "все вы", "все люди")
        def vse_repl(m):
            w = m.group(0)
            return "Всё" if w[0].isupper() else "всё"

        clean = re.sub(r"\b([Вв])се\b(?!\s+(?:мы|они|вы|люди))\b", vse_repl, clean)

        yo_map = {
            r"\bеще\b": "ещё",
            r"\bче\b": "чё",
            r"\bчет\b": "чёт",
            r"\bниче\b": "ничё",
            r"\bпридет\b": "придёт",
            r"\bпридешь\b": "придёшь",
            r"\bуйдет\b": "уйдёт",
            r"\bпойдет\b": "пойдёт",
            r"\bзаймет\b": "займёт",
            r"\bначнет\b": "начнёт",
            r"\bпоймет\b": "поймёт",
            r"\bждет\b": "ждёт",
            r"\bждешь\b": "ждёшь",
            r"\bзовет\b": "зовёт",
            r"\bживет\b": "живёт",
            r"\bберет\b": "берёт",
            r"\bберешь\b": "берёшь",
            r"\bпьешь\b": "пьёшь",
            r"\bпьет\b": "пьёт",
            r"\bпьем\b": "пьём",
            r"\bнайдет\b": "найдёт",
            r"\bпойдем\b": "пойдём",
            r"\bидем\b": "идём",
            r"\bмое\b": "моё",
            r"\bтвое\b": "твоё",
            r"\bсвое\b": "своё",
            r"\bее\b": "её",
            r"\bнее\b": "неё",
            r"\bокей\b": "окэй",
            r"\bчерт\b": "чёрт",
        }
        clean = apply_replacements(clean, yo_map)

        # Stage 2: Auto-replace trap homographs with 100% unambiguous spoken synonyms
        homograph_map = {
            r"\bсбегаю\b": "схожу",
            r"\bсбегать\b": "сходить",
            r"\bстоит\b": "надо",
            r"\bзамок\b": "замочек",
            r"\bплачу\b": "реву",
            r"\bпишу\b": "напишу",
        }
        clean = apply_replacements(clean, homograph_map)

        # 7. Normalize hyphens to em-dashes for intonation accents
        clean = re.sub(r"\s+-\s+", " — ", clean)

        # 8. Natural sighs and breathing intake for realistic ElevenLabs speech
        clean = clean.strip()
        if clean and not clean.startswith("...") and not clean.startswith("—"):
            clean = f"... {clean}"

        # Soft pauses and breath intake before natural coordinating words
        clean = re.sub(r",\s+(но|а|слушай|давай|просто|блин|кстати|короче|в общем)\b", r"... \1", clean)
        clean = re.sub(r"(?i)\bахахах+\b", "... хах...", clean)
        clean = re.sub(r"(?i)\bахах\b", "... хах...", clean)
        clean = re.sub(r"(?i)\bхахах\b", "... хах...", clean)

        # Clean up punctuation collisions around ellipses
        clean = re.sub(r"\.\.\.\s*,", "...", clean)
        clean = re.sub(r",\s*\.\.\.", "...", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        clean = re.sub(r"\.{4,}", "...", clean)
        return clean

    @classmethod
    def apply_phone_mic_filter(cls, raw_mp3: bytes) -> bytes:
        """
        Applies authentic iPhone microphone acoustics in Telegram without barrel or muffled effect:
        - highpass=f=75: soft cut of low rumble (< 75 Hz)
        - lowpass=f=15500: wide acoustic band up to 15.5 kHz
        - equalizer=f=3500: presence boost at 3.5 kHz
        - acompressor: gentle dynamic leveling of speech and breathing
        - -ac 1: strict mono, standard phone mic
        - -ar 48000: Telegram Opus sample rate
        - -c:a libopus -b:a 48k -vbr on -application voip: real iPhone voice note bitrate & voice optimization
        """
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f_in:
            f_in.write(raw_mp3)
            in_path = f_in.name

        out_path = in_path.replace(".mp3", ".ogg")
        cmd = [
            ffmpeg_exe,
            "-y",
            "-i", in_path,
            "-af", (
                "highpass=f=80,"
                "lowpass=f=16000,"
                "equalizer=f=3200:width_type=q:width=1.2:g=2.5,"
                "acompressor=threshold=-18dB:ratio=2.5:attack=15:release=120"
            ),
            "-ac", "1",
            "-ar", "48000",
            "-c:a", "libopus",
            "-b:a", "48k",
            "-vbr", "on",
            "-application", "voip",
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
        return raw_mp3

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
    async def generate_voice_bytes(cls, text: str, voice_name: Optional[str] = None) -> bytes:
        """
        Generates ultra-realistic female voice audio bytes using ElevenLabs SDK with smartphone mic acoustics.
        Produces natural Russian intonations, cadence, breathing and phone acoustic filter without foreign accent.
        """
        spoken_text = cls.prepare_text_for_tts(text)
        if not spoken_text:
            spoken_text = text

        # 1. Primary: Official ElevenLabs SDK (eleven_multilingual_v2 with native Russian female voice)
        elevenlabs_key = settings.ELEVENLABS_API_KEY
        if elevenlabs_key:
            voice_id = settings.ELEVENLABS_VOICE_ID or "oik5x6jBI6Xj6MPIciZU"

            def _synthesize_elevenlabs() -> Optional[bytes]:
                try:
                    from elevenlabs.client import ElevenLabs
                    from elevenlabs import VoiceSettings

                    client = ElevenLabs(api_key=elevenlabs_key)
                    dynamic_vs = cls.get_dynamic_voice_settings(spoken_text)
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
    ) -> bool:
        """
        Sends genuine voice note directly via Telegram Bot API sendVoice.
        Emulates realistic recording duration with record_voice chat action.
        On error, safely falls back to text reply without crashing.
        """
        if not bot_token or bot_token in ("mock_token", "test_token"):
            return True

        try:
            # 1. Realistic recording duration simulation: min 1.5s, max 4.0s
            if text:
                duration = min(max(len(text) / 22.0, 1.5), 4.0)
                await cls.send_chat_action(bot_token, chat_id, "record_voice", business_connection_id)
                await asyncio.sleep(duration)

            audio_bytes = None
            if voice_tag:
                audio_bytes = cls.get_voice_asset(voice_tag)

            if not audio_bytes and text:
                audio_bytes = await cls.generate_voice_bytes(text)

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
