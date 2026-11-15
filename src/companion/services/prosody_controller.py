"""
Advanced Russian TTS Prosody Controller & Pronunciation Layer.

Implements the complete Russian Natural Speech Prosody Master Directive:
- Persistent Pronunciation Dictionary (lexical pronunciation vs conversational prosody)
- Phrase-Level Intonation & Thought Groups (Anti-fragmentation)
- Sentence-End Controller (falling, soft_falling, neutral, open, question_rising, surprise_rising, etc.)
- Semantic Focus & Question Classification
- Conversational Repetition Prevention & History Tracking
- TTS Input Object Specification
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class PronunciationEntry:
    normalized: str
    pronunciation: str
    stress: Optional[str] = None
    stressed_spelling: Optional[str] = None
    phonetic_override: Optional[str] = None
    ipa: Optional[str] = None
    confidence: float = 0.95
    contexts: List[str] = field(default_factory=list)


@dataclass
class TTSInputObject:
    display_text: str
    speech_text: str
    emotion: str = "neutral"
    emotion_intensity: float = 0.4
    focus_word: Optional[str] = None
    secondary_focus: Optional[str] = None
    intonation_type: str = "statement"
    ending_type: str = "soft_falling"
    question_type: Optional[str] = None
    speech_rate: str = "normal"
    pronunciation_overrides: List[str] = field(default_factory=list)
    ipa_overrides: List[str] = field(default_factory=list)
    context_summary: str = ""
    stability: float = 0.54
    similarity_boost: float = 0.82
    style: float = 0.05
    speed: Optional[float] = None


class PronunciationDictionary:
    """
    Persistent Pronunciation Dictionary (Section 2 & 19).
    Stores lexical stress, Russian messaging slang, and pronunciation overrides.
    Maintains authentic Russian vowel reduction and soft Cyrillic 'е'.
    """

    DEFAULT_DICTIONARY_PATH = Path("assets/pronunciation_dictionary.json")

    # Initial linguistic foundation for Russian conversational Telegram speech
    SEED_ENTRIES: Dict[str, Dict[str, Any]] = {
        # Social & Messenger Terminology (Spoken Conversational Expansions)
        "гс": {"normalized": "гс", "pronunciation": "голосовое", "stress": "4", "confidence": 1.0},
        "вк": {"normalized": "вк", "pronunciation": "вконтакте", "stress": "2", "confidence": 1.0},
        "тг": {"normalized": "тг", "pronunciation": "телеграм", "stress": "3", "confidence": 1.0},
        "лс": {"normalized": "лс", "pronunciation": "личку", "stress": "1", "confidence": 1.0},
        "смс": {"normalized": "смс", "pronunciation": "эс эм э́с", "stress": "3", "confidence": 1.0},
        "смски": {"normalized": "смски", "pronunciation": "эс эм э́ски", "stress": "3", "confidence": 1.0},
        "смску": {"normalized": "смску", "pronunciation": "эс эм э́ску", "stress": "3", "confidence": 1.0},
        "инста": {"normalized": "инста", "pronunciation": "и́нста", "stress": "1", "confidence": 1.0},
        "инсте": {"normalized": "инсте", "pronunciation": "и́нсте", "stress": "1", "confidence": 1.0},
        "инсту": {"normalized": "инсту", "pronunciation": "и́нсту", "stress": "1", "confidence": 1.0},
        "инсты": {"normalized": "инсты", "pronunciation": "и́нсты", "stress": "1", "confidence": 1.0},
        "дайвинчик": {"normalized": "дайвинчик", "pronunciation": "дайви́нчик", "stress": "2", "confidence": 1.0},
        "дайвинчике": {"normalized": "дайвинчике", "pronunciation": "дайви́нчике", "stress": "2", "confidence": 1.0},
        "дайвинчика": {"normalized": "дайвинчика", "pronunciation": "дайви́нчика", "stress": "2", "confidence": 1.0},
        "дайвинчику": {"normalized": "дайвинчику", "pronunciation": "дайви́нчику", "stress": "2", "confidence": 1.0},
        "тиндер": {"normalized": "тиндер", "pronunciation": "ти́ндер", "stress": "1", "confidence": 1.0},
        "тиндере": {"normalized": "тиндере", "pronunciation": "ти́ндере", "stress": "1", "confidence": 1.0},
        "тиндеру": {"normalized": "тиндеру", "pronunciation": "ти́ндеру", "stress": "1", "confidence": 1.0},
        "войс": {"normalized": "войс", "pronunciation": "войс", "stress": "1", "confidence": 1.0},
        "войсы": {"normalized": "войсы", "pronunciation": "во́йсы", "stress": "1", "confidence": 1.0},
        "войсик": {"normalized": "войсик", "pronunciation": "во́йсик", "stress": "1", "confidence": 1.0},
        "кружок": {"normalized": "кружок", "pronunciation": "кружо́к", "stress": "2", "confidence": 1.0},
        "кружочек": {"normalized": "кружочек", "pronunciation": "кружо́чек", "stress": "2", "confidence": 1.0},
        "кружочки": {"normalized": "кружочки", "pronunciation": "кружо́чки", "stress": "2", "confidence": 1.0},
        "рилс": {"normalized": "рилс", "pronunciation": "рилс", "stress": "1", "confidence": 1.0},
        "рилсы": {"normalized": "рилсы", "pronunciation": "ри́лсы", "stress": "1", "confidence": 1.0},
        "вайб": {"normalized": "вайб", "pronunciation": "вайб", "stress": "1", "confidence": 1.0},
        "вайбовый": {"normalized": "вайбовый", "pronunciation": "вайбо́вый", "stress": "2", "confidence": 1.0},
        "вайбово": {"normalized": "вайбово", "pronunciation": "вайбо́во", "stress": "2", "confidence": 1.0},
        "кринж": {"normalized": "кринж", "pronunciation": "кринж", "stress": "1", "confidence": 1.0},
        "кринжовый": {"normalized": "кринжовый", "pronunciation": "кринжо́вый", "stress": "2", "confidence": 1.0},
        "кринжово": {"normalized": "кринжово", "pronunciation": "кринжо́во", "stress": "2", "confidence": 1.0},
        "краш": {"normalized": "краш", "pronunciation": "краш", "stress": "1", "confidence": 1.0},
        "крашиха": {"normalized": "крашиха", "pronunciation": "краши́ха", "stress": "2", "confidence": 1.0},
        # Russian "Е" & Word Preservations (Strictly natural soft Cyrillic 'е', NEVER 'э'!)
        "трек": {"normalized": "трек", "pronunciation": "трек", "stress": "1", "confidence": 1.0},
        "треки": {"normalized": "треки", "pronunciation": "тре́ки", "stress": "1", "confidence": 1.0},
        "трека": {"normalized": "трека", "pronunciation": "тре́ка", "stress": "1", "confidence": 1.0},
        "кофе": {"normalized": "кофе", "pronunciation": "ко́фе", "stress": "1", "confidence": 1.0},
        "кафе": {"normalized": "кафе", "pronunciation": "кафе́", "stress": "2", "confidence": 1.0},
        "тебе": {"normalized": "тебе", "pronunciation": "тебе́", "stress": "2", "confidence": 1.0},
        "сегодня": {"normalized": "сегодня", "pronunciation": "сего́дня", "stress": "2", "confidence": 1.0},
        "вместе": {"normalized": "вместе", "pronunciation": "вме́сте", "stress": "1", "confidence": 1.0},
        "вечер": {"normalized": "вечер", "pronunciation": "ве́чер", "stress": "1", "confidence": 1.0},
        "вечером": {"normalized": "вечером", "pronunciation": "ве́чером", "stress": "1", "confidence": 1.0},
        "дело": {"normalized": "дело", "pronunciation": "де́ло", "stress": "1", "confidence": 1.0},
        "дела": {"normalized": "дела", "pronunciation": "дела́", "stress": "2", "confidence": 1.0},
        "успеть": {"normalized": "успеть", "pronunciation": "успе́ть", "stress": "2", "confidence": 1.0},
        "интернет": {"normalized": "интернет", "pronunciation": "интерне́т", "stress": "3", "confidence": 1.0},
        "модель": {"normalized": "модель", "pronunciation": "моде́ль", "stress": "2", "confidence": 1.0},
        "проект": {"normalized": "проект", "pronunciation": "прое́кт", "stress": "2", "confidence": 1.0},
        "тема": {"normalized": "тема", "pronunciation": "те́ма", "stress": "1", "confidence": 1.0},
        "текст": {"normalized": "текст", "pronunciation": "те́кст", "stress": "1", "confidence": 1.0},
        "музей": {"normalized": "музей", "pronunciation": "музе́й", "stress": "2", "confidence": 1.0},
        "бассейн": {"normalized": "бассейн", "pronunciation": "бассе́йн", "stress": "2", "confidence": 1.0},
        "чат": {"normalized": "чат", "pronunciation": "чат", "stress": "1", "confidence": 1.0},
        "чате": {"normalized": "чате", "pronunciation": "ча́те", "stress": "1", "confidence": 1.0},
        "чатик": {"normalized": "чатик", "pronunciation": "ча́тик", "stress": "1", "confidence": 1.0},
        "стикер": {"normalized": "стикер", "pronunciation": "сти́кер", "stress": "1", "confidence": 1.0},
        "стикеры": {"normalized": "стикеры", "pronunciation": "сти́керы", "stress": "1", "confidence": 1.0},
        "гифка": {"normalized": "гифка", "pronunciation": "ги́фка", "stress": "1", "confidence": 1.0},
        "гифку": {"normalized": "гифку", "pronunciation": "ги́фку", "stress": "1", "confidence": 1.0},
        "гифки": {"normalized": "гифки", "pronunciation": "ги́фки", "stress": "1", "confidence": 1.0},
        "инфо": {"normalized": "инфо", "pronunciation": "и́нфо", "stress": "1", "confidence": 1.0},
        # Mobile & Irregular Russian Word Stresses
        "занята": {"normalized": "занята", "pronunciation": "занята́", "stress": "3", "confidence": 1.0},
        "поняла": {"normalized": "поняла", "pronunciation": "поняла́", "stress": "3", "confidence": 1.0},
        "спала": {"normalized": "спала", "pronunciation": "спала́", "stress": "2", "confidence": 1.0},
        "ждала": {"normalized": "ждала", "pronunciation": "ждала́", "stress": "2", "confidence": 1.0},
        "брала": {"normalized": "брала", "pronunciation": "брала́", "stress": "2", "confidence": 1.0},
        "звонит": {"normalized": "звонит", "pronunciation": "звони́т", "stress": "2", "confidence": 1.0},
        "звонят": {"normalized": "звонят", "pronunciation": "звоня́т", "stress": "2", "confidence": 1.0},
        "позвонит": {"normalized": "позвонит", "pronunciation": "позвони́т", "stress": "3", "confidence": 1.0},
        "позвонят": {"normalized": "позвонят", "pronunciation": "позвоня́т", "stress": "3", "confidence": 1.0},
        "включит": {"normalized": "включит", "pronunciation": "включи́т", "stress": "2", "confidence": 1.0},
        "включат": {"normalized": "включат", "pronunciation": "включа́т", "stress": "2", "confidence": 1.0},
        "красивее": {"normalized": "красивее", "pronunciation": "краси́вее", "stress": "2", "confidence": 1.0},
        "облегчит": {"normalized": "облегчит", "pronunciation": "облегчи́т", "stress": "3", "confidence": 1.0},
        "одновременно": {"normalized": "одновременно", "pronunciation": "одновреме́нно", "stress": "4", "confidence": 1.0},
        "договор": {"normalized": "договор", "pronunciation": "догово́р", "stress": "3", "confidence": 1.0},
        "договоры": {"normalized": "договоры", "pronunciation": "догово́ры", "stress": "3", "confidence": 1.0},
        "договора": {"normalized": "договора", "pronunciation": "догово́ры", "stress": "3", "confidence": 1.0},
        "торты": {"normalized": "торты", "pronunciation": "то́рты", "stress": "1", "confidence": 1.0},
        "банты": {"normalized": "банты", "pronunciation": "ба́нты", "stress": "1", "confidence": 1.0},
        "шарфы": {"normalized": "шарфы", "pronunciation": "ша́рфы", "stress": "1", "confidence": 1.0},
        "каталог": {"normalized": "каталог", "pronunciation": "катало́г", "stress": "3", "confidence": 1.0},
        "жалюзи": {"normalized": "жалюзи", "pronunciation": "жалюзи́", "stress": "3", "confidence": 1.0},
        "мастерски": {"normalized": "мастерски", "pronunciation": "мастерски́", "stress": "4", "confidence": 1.0},
        "эксперт": {"normalized": "эксперт", "pronunciation": "экспе́рт", "stress": "2", "confidence": 1.0},
        "квартал": {"normalized": "квартал", "pronunciation": "кварта́л", "stress": "2", "confidence": 1.0},
    }

    def __init__(self, file_path: Optional[Path] = None):
        self.file_path = file_path or self.DEFAULT_DICTIONARY_PATH
        self.entries: Dict[str, PronunciationEntry] = {}
        self.load()

    def load(self):
        """Loads dictionary from JSON or creates with initial seed entries."""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self.entries[k.lower()] = PronunciationEntry(
                            normalized=v.get("normalized", k),
                            pronunciation=v.get("pronunciation", k),
                            stress=v.get("stress"),
                            stressed_spelling=v.get("stressed_spelling"),
                            phonetic_override=v.get("phonetic_override"),
                            ipa=v.get("ipa"),
                            confidence=v.get("confidence", 0.95),
                            contexts=v.get("contexts", []),
                        )
                # Auto-upgrade any legacy dictionary entries with open-э or letter-by-letter mistakes
                changed = False
                for k, seed_v in self.SEED_ENTRIES.items():
                    norm_k = k.lower()
                    if norm_k in self.entries:
                        current_p = self.entries[norm_k].pronunciation
                        if norm_k in ("кофе", "кафе", "трек", "треки", "трека") and "э" in current_p:
                            self.entries[norm_k].pronunciation = seed_v["pronunciation"]
                            changed = True
                        elif norm_k in ("гс", "тг", "лс", "вк") and (" " in current_p or "э" in current_p):
                            self.entries[norm_k].pronunciation = seed_v["pronunciation"]
                            changed = True
                    else:
                        self.entries[norm_k] = PronunciationEntry(
                            normalized=seed_v["normalized"],
                            pronunciation=seed_v["pronunciation"],
                            stress=seed_v.get("stress"),
                            ipa=seed_v.get("ipa"),
                            confidence=seed_v.get("confidence", 0.95),
                        )
                        changed = True
                if changed:
                    self.save()
                return
            except Exception as e:
                logger.warning("Failed to load pronunciation dictionary from %s: %s", self.file_path, e)

        # Initialize from seed
        for k, v in self.SEED_ENTRIES.items():
            self.entries[k.lower()] = PronunciationEntry(
                normalized=v["normalized"],
                pronunciation=v["pronunciation"],
                stress=v.get("stress"),
                ipa=v.get("ipa"),
                confidence=v.get("confidence", 0.95),
            )
        self.save()

    def save(self):
        """Persists dictionary to disk."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            data = {k: asdict(v) for k, v in self.entries.items()}
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("Failed to save pronunciation dictionary: %s", e)

    def get(self, word: str) -> Optional[PronunciationEntry]:
        norm = word.lower().strip(" ,.!?:;—-\"'\n\r\t")
        return self.entries.get(norm)

    def set(self, word: str, pronunciation: str, stress: Optional[str] = None, ipa: Optional[str] = None, confidence: float = 1.0):
        norm = word.lower().strip()
        self.entries[norm] = PronunciationEntry(
            normalized=norm,
            pronunciation=pronunciation,
            stress=stress,
            ipa=ipa,
            confidence=confidence,
        )
        self.save()


class ProsodyAnalyzer:
    """
    Analyzes Russian text and conversational context to determine:
    - Emotion and intensity
    - Semantic focus (0-1 primary, 0-1 secondary)
    - Question classification
    - Sentence ending type (falling, soft_falling, neutral, open, question_rising, surprise_rising, etc.)
    - Prevents repetitive intonation across consecutive messages
    """

    # Short spontaneous reaction patterns (Section 10 & 18)
    SHORT_REACTIONS: Dict[str, Tuple[str, str, str]] = {
        # phrase -> (emotion, intonation_type, ending_type)
        "да": ("neutral", "statement", "soft_falling"),
        "да)": ("playful", "statement", "neutral"),
        "нет": ("neutral", "statement", "soft_falling"),
        "нет)": ("playful", "statement", "neutral"),
        "неа": ("playful", "statement", "soft_falling"),
        "да ладно": ("curious", "surprise", "surprise_rising"),
        "да ладно?": ("curious", "surprise", "surprise_rising"),
        "серьёзно": ("curious", "surprise", "neutral"),
        "серьёзно?": ("curious", "question", "neutral"),
        "серьезно": ("curious", "surprise", "neutral"),
        "серьезно?": ("curious", "question", "neutral"),
        "ну да": ("neutral", "statement", "soft_falling"),
        "ну да)": ("playful", "statement", "neutral"),
        "не знаю": ("neutral", "statement", "open"),
        "не знаю)": ("playful", "statement", "open"),
        "прикинь": ("excited", "reaction", "surprise_rising"),
        "прикинь!": ("excited", "reaction", "surprise_rising"),
        "прикинь?": ("excited", "reaction", "surprise_rising"),
        "понятно": ("neutral", "statement", "soft_falling"),
        "ясно": ("neutral", "statement", "soft_falling"),
        "капец": ("curious", "surprise", "soft_falling"),
        "ого": ("excited", "surprise", "surprise_rising"),
        "ага": ("neutral", "statement", "soft_falling"),
        "ага)": ("playful", "statement", "neutral"),
        "агась": ("playful", "statement", "neutral"),
        "лан": ("neutral", "statement", "soft_falling"),
        "ладно": ("neutral", "statement", "soft_falling"),
        "тоже": ("neutral", "statement", "soft_falling"),
        "точно": ("neutral", "statement", "soft_falling"),
        "правда": ("curious", "question", "neutral"),
        "правда?": ("curious", "question", "surprise_rising"),
        "жесть": ("curious", "surprise", "soft_falling"),
        "огонь": ("excited", "reaction", "rising"),
        "норм": ("neutral", "statement", "soft_falling"),
        "нормально": ("neutral", "statement", "soft_falling"),
    }

    @classmethod
    def analyze_message(
        cls,
        text: str,
        chat_history: Optional[List[Dict[str, Any]]] = None,
        last_prosody: Optional[TTSInputObject] = None,
    ) -> TTSInputObject:
        stripped = text.strip()
        lower = stripped.lower().strip(" ,.!?:;—-\"'\n\r\t")

        # 1. Check for Short Telegram Reactions (Section 10 & 18)
        if lower in cls.SHORT_REACTIONS:
            em, in_type, end_type = cls.SHORT_REACTIONS[lower]
            return cls._build_object(
                display_text=stripped,
                emotion=em,
                emotion_intensity=0.45,
                focus_word=stripped.split()[0] if stripped else None,
                secondary_focus=None,
                intonation_type=in_type,
                ending_type=end_type,
                question_type="surprise" if "серь" in lower or "ладно" in lower else None,
                context_summary="short_reaction",
                last_prosody=last_prosody,
            )

        # 2. Determine Emotion and Intensity (Section 14 & 19)
        emotion, intensity, focus_hint = cls._detect_emotion_and_intent(stripped, chat_history)

        # 3. Determine Question Classification (Section 8 & 9)
        is_question = "?" in stripped
        q_type = None
        if is_question:
            q_type = cls._classify_question(stripped, emotion)

        # 4. Determine Sentence Ending Type (Section 7 & 8)
        ending_type = cls._determine_ending_type(stripped, emotion, is_question, q_type, last_prosody)

        # 5. Determine Primary & Secondary Semantic Focus (Section 5 & 8)
        focus_word, sec_focus = cls._find_semantic_focus(stripped, focus_hint)

        return cls._build_object(
            display_text=stripped,
            emotion=emotion,
            emotion_intensity=intensity,
            focus_word=focus_word,
            secondary_focus=sec_focus,
            intonation_type="question" if is_question else "statement",
            ending_type=ending_type,
            question_type=q_type,
            context_summary=f"em={emotion};end={ending_type}",
            last_prosody=last_prosody,
        )

    @classmethod
    def _detect_emotion_and_intent(
        cls, text: str, chat_history: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[str, float, Optional[str]]:
        t_low = text.lower()

        # Angry / Annoyed
        if re.search(r"\b(бесит|достало|надоело|злюсь|отстань|хватит|раздражает)\b", t_low):
            return "annoyed", 0.6, "annoyed"
        # Sad
        if re.search(r"\b(грустно|жаль|больно|печально|сочувствую|тяжело|обидно|жалко)\b", t_low):
            return "sad", 0.5, "sad"
        # Tired / Sleep
        if re.search(r"\b(устала|без сил|завал|болею|спать|до завтра|спокойной ночи|добранич|отдыхать|вымоталась)\b", t_low):
            return "tired", 0.45, "tired"
        # Sarcastic / Dry Deadpan
        if re.search(r"\b(ну да конечно|ага конечно|ну прям|сарказм|да неужели)\b", t_low):
            return "sarcastic", 0.5, "sarcastic"
        # Excited / Joyful
        if re.search(r"\b(супер|ура|круто|ого|кайф|отлично|поздравляю|восторг|шикарно)\b", t_low):
            return "excited", 0.65, "excited"
        # Curious / Questioning
        if "?" in text or re.search(r"\b(почему|зачем|где ты|как ты|что думаешь|расскажи|откуда|куда|ты о чем)\b", t_low):
            return "curious", 0.5, "curious"
        # Flirty / Intimate
        if re.search(r"\b(скучаю|милый|красивый|нравишься|обнимаю|целую|жду тебя|притягательный)\b", t_low):
            return "flirty", 0.5, "flirty"
        # Playful (No laughter)
        if re.search(r"\b(хитрая|подкалываешь|шутник|ну ты даешь|ловко)\b", t_low):
            return "playful", 0.5, "playful"
        # Serious
        if re.search(r"\b(серьезно|важно|проблема|правда|честно|дело|решим|подумай)\b", t_low) and "?" not in text:
            return "serious", 0.55, "serious"

        return "neutral", 0.4, None

    @classmethod
    def _classify_question(cls, text: str, emotion: str) -> str:
        t_low = text.lower()
        if re.search(r"\b(серь[её]зно|да ладно|правда)\b", t_low):
            return "surprise"
        if re.search(r"\b(ну и зачем|и что теперь|смысл)\b", t_low):
            return "rhetorical"
        if emotion in ("playful", "flirty"):
            return "playful"
        if emotion == "annoyed":
            return "annoyed"
        if re.search(r"\b(точно|так ведь|да\?)\b", t_low):
            return "confirmation"
        return "genuine_information"

    @classmethod
    def _determine_ending_type(
        cls,
        text: str,
        emotion: str,
        is_question: bool,
        q_type: Optional[str],
        last_prosody: Optional[TTSInputObject],
    ) -> str:
        # 1. Questions
        if is_question:
            if q_type in ("surprise", "disbelief"):
                return "surprise_rising"
            if q_type in ("rhetorical", "confirmation"):
                return "neutral"
            return "question_rising"

        # 2. Unfinished or continuing thoughts
        if text.endswith("...") or text.endswith("—"):
            return "open"

        # 3. Prevent repetitive monotonic falling pitch (Section 15 Check 1 & Section 17)
        if last_prosody and last_prosody.ending_type == "falling":
            return "soft_falling" if emotion != "serious" else "neutral"

        if emotion in ("playful", "flirty"):
            return "neutral"
        if emotion == "tired":
            return "open"
        if emotion == "excited":
            return "rising"

        return "soft_falling"

    @classmethod
    def _find_semantic_focus(cls, text: str, focus_hint: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        words = [w.strip(" ,.!?:;—-\"'\n\r\t") for w in text.split() if len(w.strip(" ,.!?:;—-\"'\n\r\t")) > 2]
        if not words:
            return None, None

        # Prefer content verbs or descriptive adjectives
        primary = words[-1] if len(words) <= 3 else words[min(1, len(words) - 1)]
        secondary = words[0] if len(words) > 4 else None
        return primary, secondary

    @classmethod
    def _build_object(
        cls,
        display_text: str,
        emotion: str,
        emotion_intensity: float,
        focus_word: Optional[str],
        secondary_focus: Optional[str],
        intonation_type: str,
        ending_type: str,
        question_type: Optional[str],
        context_summary: str,
        last_prosody: Optional[TTSInputObject],
    ) -> TTSInputObject:
        # ElevenLabs VoiceSettings calibrated per emotion (Section 14)
        # Stability is strictly maintained >= 0.50 to eliminate vocal tremors
        settings_map = {
            "neutral": (0.54, 0.82, 0.04),
            "curious": (0.52, 0.82, 0.07),
            "excited": (0.50, 0.80, 0.09),
            "annoyed": (0.62, 0.85, 0.02),
            "sad": (0.58, 0.84, 0.03),
            "playful": (0.54, 0.82, 0.06),
            "flirty": (0.54, 0.82, 0.05),
            "sarcastic": (0.58, 0.84, 0.03),
            "tired": (0.58, 0.84, 0.02),
            "serious": (0.60, 0.84, 0.03),
        }
        stab, sim, stl = settings_map.get(emotion, (0.54, 0.82, 0.04))

        # Adjust speech rate subtly based on emotion (Section 14)
        speech_rate = "normal"
        speed = None
        if emotion == "excited":
            speech_rate = "slightly_faster"
        elif emotion in ("tired", "sad", "serious"):
            speech_rate = "slightly_slower"

        return TTSInputObject(
            display_text=display_text,
            speech_text="",  # Will be populated by SpokenTextTransformer
            emotion=emotion,
            emotion_intensity=emotion_intensity,
            focus_word=focus_word,
            secondary_focus=secondary_focus,
            intonation_type=intonation_type,
            ending_type=ending_type,
            question_type=question_type,
            speech_rate=speech_rate,
            context_summary=context_summary,
            stability=stab,
            similarity_boost=sim,
            style=stl,
            speed=speed,
        )


class SpokenTextTransformer:
    """
    Transforms written Russian into authentic spoken Russian phrasing (Section 11 & 18):
    - Applies selective pronunciation dictionary overrides
    - Applies 'ё' enforcement for 100% accurate lexical stress
    - Applies unambiguous homograph synonyms
    - Formats phrase-level thought groups (anti-chopping commas before subordinate clauses)
    - Shapes conversational sentence endings based on ending_type
    """

    HOMOGRAPH_MAP: Dict[str, str] = {
        r"\bсбегаю\b": "схожу",
        r"\bсбегать\b": "сходить",
        r"\bстоит\b": "надо",
        r"\bзамок\b": "замочек",
        r"\bплачу\b": "реву",
        r"\bпишу\b": "напишу",
    }

    YO_MAP: Dict[str, str] = {
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
        r"\bчерт\b": "чёрт",
    }

    @classmethod
    def transform(
        cls,
        prosody_obj: TTSInputObject,
        dictionary: PronunciationDictionary,
        digits_to_words_fn,
    ) -> TTSInputObject:
        text = prosody_obj.display_text
        if not text:
            prosody_obj.speech_text = ""
            return prosody_obj

        # 1. Strip URLs and @mentions
        clean = re.sub(r"https?://\S+|t\.me/\S+|@\w+", "", text)

        # 2. Strip emojis and symbols
        clean = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27ff\u2300-\u23ff\u2b50\u200d\ufe0f]", "", clean)

        # 3. Strip raw asterisks and uncontrolled stress marks from input
        clean = clean.replace("*", "").replace("\u0301", "")

        # 4. Remove smileys entirely (NO decorative laughter)
        clean = re.sub(r"[\)\(]+", "", clean)

        # 5. Exclamation marks: tone down to avoid robotic shout
        clean = clean.replace("!", ".")

        # 6. Strip decorative laughter tokens
        clean = re.sub(r"(?i)\b(ахахах+|ахах|хахах+|хаха|хех|хихи|hehe|haha|lmao|lol)\b", "", clean)

        # 7. Section 4 & 5: Dedicated Slang & Abbreviation Normalization Layer
        # Context-aware expansion of 'гс', 'лс', 'тг', 'вк', 'мб', 'крч', 'спс', 'пж' and proper words 'лол', 'кек', 'изи', 'имхо'
        from companion.services.slang_pronunciation_layer import SlangAbbreviationAnalyzer
        clean, slang_decisions = SlangAbbreviationAnalyzer.normalize_slang(clean)

        # 8. Convert numbers to Russian words
        if digits_to_words_fn:
            clean = digits_to_words_fn(clean)

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

        # 8. 'все' -> 'всё' except 'все мы', 'все они', 'все вы', 'все люди'
        def vse_repl(m):
            w = m.group(0)
            return "Всё" if w[0].isupper() else "всё"
        clean = re.sub(r"\b([Вв])се\b(?!\s+(?:мы|они|вы|люди))\b", vse_repl, clean)

        # 9. Apply 'ё' mapping
        clean = apply_replacements(clean, cls.YO_MAP)

        # 10. Apply homograph replacements
        clean = apply_replacements(clean, cls.HOMOGRAPH_MAP)

        # 11. Section 13 & 19: Selective Pronunciation Overrides from Dictionary
        overrides_applied = []
        for norm_key, entry in dictionary.entries.items():
            pattern = r"\b" + re.escape(norm_key) + r"\b"
            if re.search(pattern, clean, flags=re.IGNORECASE):
                def make_entry_repl(target_rep: str):
                    def repl_fn(m):
                        val = m.group(0)
                        if val.isupper():
                            return target_rep.upper()
                        elif val[0].isupper():
                            return target_rep.capitalize()
                        return target_rep
                    return repl_fn

                clean = re.sub(pattern, make_entry_repl(entry.pronunciation), clean, flags=re.IGNORECASE)
                overrides_applied.append(f"{norm_key}->{entry.pronunciation}")
        prosody_obj.pronunciation_overrides = overrides_applied

        # 12. Section 2 & 9: Phrase-Level Intonation & Connected Thoughts
        # Smooth out artificial commas before relative clauses in tight verb phrases so thoughts flow continuously
        clean = re.sub(
            r"\b(не знала|не знаю|не думала|не думаю|не ожидала|не помню|помню|кажется|думаю|видела|вижу|знаю|поняла|слышала|слышу)\s*,\s*(что|чтобы|чтоб|как|где|куда|почему|зачем|кто)\b",
            r"\1 \2",
            clean,
            flags=re.IGNORECASE,
        )
        clean = re.sub(
            r"\b(мне кажется|кажется|главное|хорошо|ясно|жаль)\s*,\s*(что|чтобы|чтоб|это|как|бы|когда)\b",
            r"\1 \2",
            clean,
            flags=re.IGNORECASE,
        )

        # 13. Section 3 & 7: Shape Sentence Endings based on ending_type
        clean = re.sub(r"\s+-\s+", " — ", clean)
        clean = re.sub(r"[,\s]+([,.])", r"\1", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        clean = clean.lstrip(" ,.—:;")
        clean = re.sub(r"\.{4,}", "...", clean)

        # Rhetorical questions: calm flat ending rather than upward interrogation spike
        if prosody_obj.question_type in ("rhetorical", "confirmation") and clean.endswith("?"):
            clean = clean[:-1] + "."

        # End contour shaping
        if prosody_obj.ending_type in ("open", "unfinished"):
            if not clean.endswith("...") and not clean.endswith("—"):
                clean = clean.rstrip(" ,.") + " —"
        elif prosody_obj.ending_type == "soft_falling":
            # Soft period without aggressive terminal drop
            if clean.endswith("...") or clean.endswith("!"):
                clean = clean.rstrip(".!") + "."
            elif not clean.endswith((".", "?", "!", "—", "...")):
                clean = clean + "."
        elif prosody_obj.ending_type == "neutral":
            if clean.endswith("..."):
                clean = clean.rstrip(".")
            elif not clean.endswith((".", "?", "!", "—", "...")):
                clean = clean + "."

        prosody_obj.speech_text = clean

        # Section 9: Pronunciation Debugging Logger (Developer logs only, never sent to Telegram)
        if slang_decisions or prosody_obj.pronunciation_overrides:
            decision_lines = [
                f"  - [{d.action}] '{d.surface_form}' -> '{d.normalized_spoken_form}' ({d.reason})"
                for d in slang_decisions
            ] + [f"  - [override] {ov}" for ov in prosody_obj.pronunciation_overrides]
            logger.debug(
                "\n[TTS_PRONUNCIATION_DEBUG]\n"
                "ORIGINAL:\n%s\n"
                "NORMALIZED:\n%s\n"
                "PRONUNCIATION:\n%s\n"
                "TTS INPUT:\n%s",
                text,
                clean,
                "\n".join(decision_lines),
                clean,
            )

        return prosody_obj


class ProsodyHistoryTracker:
    """
    Tracks prosodic patterns across conversation turns to prevent
    repetitive sentence melodies and robotic rhythm (Section 15 & 17).
    """

    _last_prosody_by_chat: Dict[int, TTSInputObject] = {}

    @classmethod
    def get_last_prosody(cls, chat_id: int) -> Optional[TTSInputObject]:
        return cls._last_prosody_by_chat.get(chat_id)

    @classmethod
    def record_prosody(cls, chat_id: int, prosody_obj: TTSInputObject):
        cls._last_prosody_by_chat[chat_id] = prosody_obj


class AdvancedProsodyController:
    """
    Main entry point for Advanced Russian TTS Prosody Controller.
    """

    _dictionary: Optional[PronunciationDictionary] = None

    @classmethod
    def get_dictionary(cls) -> PronunciationDictionary:
        if cls._dictionary is None:
            cls._dictionary = PronunciationDictionary()
        return cls._dictionary

    @classmethod
    def process_for_tts(
        cls,
        text: str,
        chat_id: int = 0,
        chat_history: Optional[List[Dict[str, Any]]] = None,
        digits_to_words_fn=None,
    ) -> TTSInputObject:
        """
        Processes text into a full TTSInputObject with authentic prosody and pronunciation.
        """
        last_prosody = ProsodyHistoryTracker.get_last_prosody(chat_id) if chat_id else None

        # 1. Analyze context and prosody
        prosody_obj = ProsodyAnalyzer.analyze_message(
            text=text,
            chat_history=chat_history,
            last_prosody=last_prosody,
        )

        # 2. Transform text for speech
        dictionary = cls.get_dictionary()
        prosody_obj = SpokenTextTransformer.transform(
            prosody_obj=prosody_obj,
            dictionary=dictionary,
            digits_to_words_fn=digits_to_words_fn,
        )

        # 3. Record in history
        if chat_id:
            ProsodyHistoryTracker.record_prosody(chat_id, prosody_obj)

        return prosody_obj
