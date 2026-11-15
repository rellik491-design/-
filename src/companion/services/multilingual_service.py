"""
MultilingualService: High-accuracy language detection, Ukrainian & Russian slang normalization,
code-switching analysis, and rolling language preference tracking.

Supports:
- Ukrainian (standard, colloquial, slang, typos, transliteration)
- Russian (standard, colloquial, slang)
- Mixed Russian + Ukrainian (Surzhyk, code-switching)
- English / Other / Unknown
- Context-aware resolution for short ambiguous messages
- Non-destructive slang normalization (preserves original transcripts)
- Rolling language memory per contact
- Standardized developer debug logging
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


@dataclass
class LanguageDetectionResult:
    language: str  # "ru", "uk", "mixed", "other", "unknown"
    confidence: float  # 0.0 - 1.0
    is_transliterated: bool = False
    language_pattern: str = "monolingual"  # "monolingual", "mixed", "code_switching"
    detected_tokens: Dict[str, List[str]] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedTextResult:
    original_text: str
    normalized_text: str
    language: str
    slang_detected: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class DynamicLanguageProfile:
    primary_language: str  # "ru", "uk", "en", "mixed"
    secondary_language: Optional[str] = None
    language_confidence: float = 0.95
    style: str = "casual"  # "casual", "formal", "playful"
    slang_level: float = 0.5  # 0.0 - 1.0
    profanity_level: float = 0.0  # 0.0 - 1.0
    emoji_level: float = 0.3  # 0.0 - 1.0
    formality: float = 0.1  # 0.0 - 1.0
    transliteration: bool = False
    script: str = "cyrillic"  # "cyrillic", "latin"
    is_explicit_override: bool = False
    override_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_language": self.primary_language,
            "secondary_language": self.secondary_language,
            "language_confidence": round(self.language_confidence, 2),
            "style": self.style,
            "slang_level": round(self.slang_level, 2),
            "profanity_level": round(self.profanity_level, 2),
            "emoji_level": round(self.emoji_level, 2),
            "formality": round(self.formality, 2),
            "transliteration": self.transliteration,
            "script": self.script,
        }


class MultilingualService:
    """
    Core multilingual recognition and normalization layer.
    Strictly additive: does not alter raw audio or original message transcripts.
    """

    # Distinctive Cyrillic characters
    UK_CHARACTERS = set("іїєґІЇЄҐ")
    RU_CHARACTERS = set("ыэъёЫЭЪЁ")

    # Apostrophe used in Ukrainian orthography
    APOSTROPHE_PATTERN = re.compile(r"\b[а-яА-Яa-zA-Z]+['’][а-яА-Яa-zA-Z]+\b")

    # Explicit language override requests (Priority 1)
    EXPLICIT_LANGUAGE_OVERRIDES = [
        # Ukrainian overrides
        (re.compile(r"(?i)\b(?:пиши|отвечай|ответь|говори|давай|перейди|спілкуйся|розмовляй|відповідай)\s+(?:на\s+)?(?:украинск(?:ом|ий)|українськ(?:ою|а)|солов['’]їн(?:ою|а)|укр)\b"), "uk"),
        (re.compile(r"(?i)\b(?:можемо|давай)\s+(?:українською|солов['’]їною)\b"), "uk"),
        # English overrides
        (re.compile(r"(?i)\b(?:speak|talk|write|answer)\s+(?:in\s+)?english\b"), "en"),
        (re.compile(r"(?i)\b(?:can\s+we\s+speak|let['’]?s\s+speak|switch\s+to)\s+english\b"), "en"),
        (re.compile(r"(?i)\b(?:пиши|отвечай|ответь|говори|давай|перейди)\s+(?:на\s+)?(?:английск(?:ом|ий)|инглиш|english)\b"), "en"),
        (re.compile(r"(?i)\benglish\s+please\b"), "en"),
        # Russian overrides
        (re.compile(r"(?i)\b(?:пиши|отвечай|ответь|говори|давай|перейди)\s+(?:по-русски|на\s+русск(?:ом|ий)|русский)\b"), "ru"),
    ]

    SLANG_TOKENS = {
        "пон", "жиза", "рофл", "кринж", "крінж", "кринжатина", "вайб", "краш", "чилл", "чіл",
        "чилить", "го", "ща", "щас", "ток", "форсить", "чекать", "чекати", "рил", "ріл",
        "база", "стэнить", "дед инсайд", "душный", "душнила", "пруфы", "пруфи", "чел",
        "бро", "луки", "рандом", "хайп", "зашквар", "тильт", "флекс", "кринжово", "крінжово",
        "вайбово", "имба", "імба", "тю", "та", "тіп", "чувак"
    }

    ABBREVIATIONS = {
        "гс", "хз", "мб", "спс", "пж", "пжлст", "чзх", "лмк", "нзч"
    }

    PROFANITY_PATTERNS = [
        re.compile(r"(?i)\b(?:бля|блять|блядь|нахуй|похуй|заебись|заєбись|хуй|хуя|хуе|пиздец|піздець|пизда|ебать|єбать|ебан|ебал|сука|ебало|охуеть|нахер|пох)\b"),
    ]

    # Strong Ukrainian tokens & word stems (lowercase)
    UK_STRONG_TOKENS = {
        # Greetings & goodbyes
        "привіт", "вітаю", "бувай", "папа", "побачимось", "зустрінемось",
        "на добраніч", "доброго ранку", "добрий день", "добрий вечір", "до побачення",
        # Question words & adverbs
        "як", "що", "шо", "чому", "навіщо", "коли", "де", "звідки", "куди", "хто",
        "який", "яка", "яке", "які", "скільки",
        # Adverbs & particles
        "зараз", "тепер", "вже", "ще", "тут", "там", "це", "сьогодні", "вчора", "завтра",
        "разом", "дуже", "трохи", "годі", "добре", "чудово", "гарно", "файно",
        "дійсно", "справді", "звісно", "мабуть", "певно", "можливо", "нічого", "ніяк",
        "ніде", "ніколи", "нікуди", "отож", "отже", "дарма",
        # Pronouns
        "я", "ти", "він", "вона", "воно", "вони", "ми", "ви", "мене", "тебе",
        "його", "її", "нас", "вас", "їх", "мені", "тобі", "йому", "їй", "нам",
        "вам", "їм", "мною", "тобою", "ним", "нею", "нами", "вами", "ними",
        "мій", "моя", "моє", "мої", "твій", "твоя", "твоє", "твої", "свій",
        "своя", "своє", "свої", "наш", "наша", "наше", "наші", "ваш", "ваша",
        # Verbs & modal expressions
        "бути", "є", "був", "була", "було", "були", "буду", "будеш", "буде", "будемо",
        "будете", "будуть", "маю", "маєш", "має", "маємо", "мають", "робити", "роблю",
        "робиш", "робить", "робимо", "робите", "роблять", "знаю", "знаєш", "знає",
        "знаємо", "знають", "кажу", "кажеш", "каже", "кажемо", "кажуть", "бачу",
        "бачиш", "бачить", "бачимо", "бачать", "чую", "чуєш", "чує", "пішов", "пішла",
        "пішли", "прийшов", "прийшла", "прийду", "прийдеш", "прийде", "чекаю", "чекаєш",
        "чекає", "пиши", "дзвони", "хочу", "хочеш", "хоче", "хочемо", "хочете", "хочуть",
        "можу", "можеш", "може", "можемо", "можуть", "треба", "потрібно", "можна",
        "немає", "нема", "відпочиваю", "працюю", "навчаюсь", "гуляю", "слухаю", "дивлюсь",
        "займаюсь", "розповідай", "розкажи", "допоможи", "дякую", "будь ласка",
        # Nouns
        "справи", "робота", "навчання", "універ", "хлопець", "дівчина", "людина", "люди",
        "кава", "чайок", "година", "хвилина", "день", "вечір", "ніч", "ранок",
        "тиждень", "місяць", "рік", "місто", "вулиця", "дім", "будинок", "вдома",
        "настрій", "думка", "думки", "розмова", "повідомлення", "голосове", "кружечок",
        "світлина", "відос", "гарнюня", "сонечко", "красуня",
    }

    # Strong Russian tokens & word stems (lowercase)
    RU_STRONG_TOKENS = {
        # Greetings & goodbyes
        "привет", "здравствуй", "здравствуйте", "пока", "до встречи", "увидимся",
        "спокойной ночи", "доброе утро", "добрый день", "добрый вечер",
        # Question words & adverbs
        "как", "что", "почему", "зачем", "когда", "где", "откуда", "куда", "кто",
        "какой", "какая", "какое", "какие", "сколько",
        # Adverbs & particles
        "сейчас", "теперь", "уже", "еще", "здесь", "тут", "это", "сегодня", "вчера", "завтра",
        "вместе", "очень", "немного", "хватит", "хорошо", "отлично", "красиво",
        "действительно", "конечно", "наверное", "возможно", "ничего", "никак",
        "нигде", "никогда", "никуда",
        # Pronouns
        "я", "ты", "он", "она", "оно", "они", "мы", "вы", "меня", "тебя", "его", "ее",
        "нас", "вас", "их", "мне", "тебе", "ему", "ей", "нам", "вам", "им",
        "мной", "тобой", "нами", "вами", "ими", "мой", "моя", "мое", "мои",
        "твой", "твоя", "твое", "твои", "свой", "своя", "свое", "свои",
        # Verbs & modal expressions
        "быть", "был", "была", "было", "были", "буду", "будешь", "будет", "будем",
        "будете", "будут", "делать", "делаю", "делаешь", "делает", "делаем", "делаете",
        "делают", "знаю", "знаешь", "знает", "знаем", "знают", "говорю", "говоришь",
        "говорит", "говорим", "говорят", "вижу", "видишь", "видит", "видим", "видят",
        "слышу", "слышишь", "слышит", "пошел", "пошла", "пошли", "пришел", "пришла",
        "приду", "придешь", "придет", "жду", "ждешь", "ждет", "пиши", "звони",
        "хочу", "хочешь", "хочет", "хотим", "хотите", "хотят", "могу", "можешь", "может",
        "можем", "могут", "надо", "нужно", "можно", "нет", "нету", "отдыхаю", "работаю",
        "учусь", "гуляю", "слушаю", "смотрю", "занимаюсь", "рассказывай", "расскажи",
        "помоги", "спасибо", "пожалуйста",
        # Nouns
        "дела", "работа", "учеба", "универ", "парень", "девушка", "человек", "люди",
        "кофе", "чаек", "час", "минута", "день", "вечер", "ночь", "утро",
        "неделя", "месяц", "год", "город", "улица", "дом", "дома", "настроение",
        "мысль", "мысли", "разговор", "сообщение", "голосовое", "кружок",
        "красотка", "милая",
    }

    # Transliterated Ukrainian markers in Latin alphabet
    UK_TRANSLIT_PATTERNS = [
        re.compile(r"\b(?:pryvit|privit|dobryi\s+den|dobroho\s+ranku|yak\s+spravy|yak\s+ty|sho\s+robysh|shcho\s+robysh|shcho\s+novogo|chomu|navishcho|dyakuyu|bud\s+laska|zaraz|vdoma|mene\s+zvaty|vse\s+dobre|do\s+zustrichi|buvai)\b", re.IGNORECASE),
    ]

    # Transliterated Russian markers in Latin alphabet
    RU_TRANSLIT_PATTERNS = [
        re.compile(r"\b(?:privet|kak\s+dela|kak\s+ty|kak\s+tvoi|tvoi\s+dela|chto\s+delaesh|cho\s+delaesh|chto\s+novogo|dobryi\s+den|dobroe\s+utro|spokoinoi\s+nochi|spasibo|pozhaluista|segodnya|vchera|zavtra|seichas|doma|delat|horosho|otlichno|konechno|ponyatno|ladno|davai|poka|tvoi|tvoe|tebe|menya)\b", re.IGNORECASE),
    ]

    # Ukrainian slang & abbreviations for semantic normalization
    UK_SLANG_NORMALIZATION_MAP = [
        # Conversational abbreviations & slang
        (r"\bшо\b", "що", "шо"),
        (r"\bщас\b", "зараз", "щас"),
        (r"\bзараз\b", "зараз", "зараз"),
        (r"\bкароче\b", "короче", "кароче"),
        (r"\bкороч\b", "короче", "короч"),
        (r"\bкрч\b", "короче", "крч"),
        (r"\bта\b", "та", "та"),
        (r"\bага\b", "так", "ага"),
        (r"\bну\s+да\b", "ну так", "ну да"),
        (r"\bжиза\b", "життєво", "жиза"),
        (r"\bімба\b", "дуже круто", "імба"),
        (r"\bімбово\b", "дуже круто", "імбово"),
        (r"\bкрінж\b", "крінж", "крінж"),
        (r"\bкрінжово\b", "крінжово", "крінжово"),
        (r"\bлол\b", "смішно", "лол"),
        (r"\bкек\b", "кек", "кек"),
        (r"\bхз\b", "не знаю", "хз"),
        (r"\bмб\b", "може бути", "мб"),
        (r"\bпж\b", "будь ласка", "пж"),
        (r"\bплз\b", "будь ласка", "плз"),
        (r"\bспс\b", "дякую", "спс"),
        (r"\bдяк\b", "дякую", "дяк"),
        (r"\bвсм\b", "в сенсі", "всм"),
        (r"\bчзх\b", "що за хрінь", "чзх"),
        (r"\bнорм\b", "нормально", "норм"),
        (r"\bок\b", "добре", "ок"),
        (r"\bпон\b", "зрозуміло", "пон"),
        (r"\bв\s+пп\b", "в особисті повідомлення", "в пп"),
        (r"\bв\s+лс\b", "в особисті повідомлення", "в лс"),
        (r"\bв\s+тг\b", "в телеграм", "в тг"),
        (r"\bгс\b", "голосове повідомлення", "гс"),
        (r"\bгску\b", "голосове повідомлення", "гску"),
        (r"\bвідос\b", "відео", "відос"),
        (r"\bвидос\b", "відео", "видос"),
        (r"\bвайб\b", "атмосфера", "вайб"),
        (r"\bтопчик\b", "чудово", "топчик"),
    ]

    # Russian slang & abbreviations for semantic normalization
    RU_SLANG_NORMALIZATION_MAP = [
        (r"\bщас\b", "сейчас", "щас"),
        (r"\bкрч\b", "короче", "крч"),
        (r"\bкороч\b", "короче", "короч"),
        (r"\bспс\b", "спасибо", "спс"),
        (r"\bпж\b", "пожалуйста", "пж"),
        (r"\bплз\b", "пожалуйста", "плз"),
        (r"\bмб\b", "может быть", "мб"),
        (r"\bхз\b", "не знаю", "хз"),
        (r"\bвсм\b", "в смысле", "всм"),
        (r"\bчзх\b", "что за хрень", "чзх"),
        (r"\bнорм\b", "нормально", "норм"),
        (r"\bв\s+лс\b", "в личку", "в лс"),
        (r"\bв\s+тг\b", "в телеграм", "в тг"),
        (r"\bгс\b", "голосовое", "гс"),
        (r"\bгску\b", "голосовуху", "гску"),
        (r"\bвидос\b", "видео", "видос"),
        (r"\bпон\b", "понял", "пон"),
    ]

    @classmethod
    def detect_language(
        cls,
        text: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        recent_language: Optional[str] = None,
    ) -> LanguageDetectionResult:
        """
        Detects text language with context-aware disambiguation.
        Classifications: "ru", "uk", "mixed", "other", "unknown".
        """
        if not text or not text.strip():
            return LanguageDetectionResult(
                language="unknown",
                confidence=0.0,
                details={"reason": "empty_text"},
            )

        raw = text.strip()
        low = raw.lower()

        # 1. Check for transliterated Ukrainian (Latin script)
        has_latin = bool(re.search(r"[a-zA-Z]", raw))
        has_cyrillic = bool(re.search(r"[\u0400-\u04FF]", raw))

        if has_latin and not has_cyrillic:
            for pat in cls.UK_TRANSLIT_PATTERNS:
                if pat.search(low):
                    return LanguageDetectionResult(
                        language="uk",
                        confidence=0.92,
                        is_transliterated=True,
                        language_pattern="monolingual",
                        detected_tokens={"uk": [pat.pattern]},
                        details={"translit": True, "script": "latin"},
                    )

            for pat in cls.RU_TRANSLIT_PATTERNS:
                if pat.search(low):
                    return LanguageDetectionResult(
                        language="ru",
                        confidence=0.92,
                        is_transliterated=True,
                        language_pattern="monolingual",
                        detected_tokens={"ru": [pat.pattern]},
                        details={"translit": True, "script": "latin"},
                    )

            # Check if general English / Other
            words = low.split()
            english_markers = {"hey", "hello", "hi", "how", "are", "you", "what", "is", "your", "name", "from", "where", "good", "morning", "night", "thanks", "please", "can", "could", "would", "the", "and"}
            if any(w in english_markers for w in words):
                return LanguageDetectionResult(
                    language="other",
                    confidence=0.90,
                    details={"script": "latin", "language": "en"},
                )

        # 2. Cyrillic alphabet analysis
        text_chars = set(raw)
        has_uk_chars = bool(text_chars.intersection(cls.UK_CHARACTERS)) or bool(cls.APOSTROPHE_PATTERN.search(raw))
        has_ru_chars = bool(text_chars.intersection(cls.RU_CHARACTERS))

        # Split into words/tokens
        tokens = re.findall(r"[\w'’]+", low)
        clean_tokens = [t.strip("'’") for t in tokens if t]

        uk_found: List[str] = []
        ru_found: List[str] = []

        # Explicit multi-word token scanning (e.g. "як справи", "що робиш", "до зустрічі")
        multi_word_uk = ["як справи", "що робиш", "шо робиш", "до зустрічі", "доброго ранку", "на добраніч", "будь ласка"]
        multi_word_ru = ["как дела", "что делаешь", "до встречи", "доброе утро", "спокойной ночи", "пожалуйста"]

        for mw in multi_word_uk:
            if mw in low:
                uk_found.append(mw)
        for mw in multi_word_ru:
            if mw in low:
                ru_found.append(mw)

        # Token-by-token matching
        for token in clean_tokens:
            is_uk = token in cls.UK_STRONG_TOKENS or any(c in cls.UK_CHARACTERS for c in token)
            is_ru = token in cls.RU_STRONG_TOKENS or any(c in cls.RU_CHARACTERS for c in token)

            if is_uk and not is_ru:
                uk_found.append(token)
            elif is_ru and not is_uk:
                ru_found.append(token)
            elif is_uk and is_ru:
                # Ambiguous token present in both (like 'я', 'ти' vs 'ты', etc.)
                pass

        # 3. Detect mixed language (Surzhyk / code-switching)
        # Examples from requirements:
        # "та я сейчас подойду, не переживай" -> "та" (uk) + "сейчас подойду не переживай" (ru) -> mixed
        # "я щас прийду, все нормально" -> "прийду" (uk) + "щас" / "все нормально" (ru) -> mixed
        # "короче я вже не знаю шо робити" -> "короче" (ru) + "вже", "шо", "робити" (uk) -> mixed
        # "та я зараз не знаю, шо делать" -> "та", "зараз", "шо" (uk) + "делать" (ru) -> mixed
        is_mixed_sentence = (
            (has_uk_chars and has_ru_chars)
            or (len(uk_found) >= 1 and len(ru_found) >= 1)
            or (bool(re.search(r"\b(та|всьо|шо|щас|зараз|прийду|вже|робити)\b", low)) and bool(re.search(r"\b(ты|как|сам|сейчас|подойду|дела|делать|делаю|переживай|жди|короче)\b", low)))
            or (bool(re.search(r"\bта\b", low)) and bool(re.search(r"\bсейчас|подойду|дела(ть|ю)|переживай\b", low)))
            or (bool(re.search(r"\bприйду\b", low)) and bool(re.search(r"\bщас|сейчас|нормально\b", low)))
            or (bool(re.search(r"\b(вже|робити|зараз|шо)\b", low)) and bool(re.search(r"\b(короче|делать|щас|сейчас)\b", low)))
        )

        if is_mixed_sentence:
            confidence = min(0.70 + 0.10 * (len(uk_found) + len(ru_found)), 0.98)
            return LanguageDetectionResult(
                language="mixed",
                confidence=confidence,
                language_pattern="code_switching",
                detected_tokens={"uk": uk_found, "ru": ru_found},
                details={"reason": "surzhyk_or_code_switching"},
            )

        # 4. Pure Ukrainian vs Pure Russian based on characters and tokens
        if has_uk_chars and not has_ru_chars:
            confidence = min(0.85 + 0.05 * len(uk_found), 0.99)
            return LanguageDetectionResult(
                language="uk",
                confidence=confidence,
                detected_tokens={"uk": uk_found},
            )

        if has_ru_chars and not has_uk_chars:
            confidence = min(0.85 + 0.05 * len(ru_found), 0.99)
            return LanguageDetectionResult(
                language="ru",
                confidence=confidence,
                detected_tokens={"ru": ru_found},
            )

        # 5. Token frequency weighting
        if uk_found and not ru_found:
            confidence = min(0.80 + 0.05 * len(uk_found), 0.98)
            return LanguageDetectionResult(
                language="uk",
                confidence=confidence,
                detected_tokens={"uk": uk_found},
            )

        if ru_found and not uk_found:
            confidence = min(0.80 + 0.05 * len(ru_found), 0.98)
            return LanguageDetectionResult(
                language="ru",
                confidence=confidence,
                detected_tokens={"ru": ru_found},
            )

        # 6. Context-Aware Disambiguation for Short / Ambiguous Messages
        # E.g. "ти де?", "ага, добре", "ну да", "та ладно", "як ти?", "ок, чекаю"
        context_lang = cls._infer_contextual_language(conversation_history, recent_language)

        # Special check for "ти де?" ("ти" is Ukrainian pronoun, "де" is Ukrainian adverb)
        if clean_tokens in [["ти", "де"], ["де", "ти"]]:
            return LanguageDetectionResult(
                language="uk",
                confidence=0.90 if context_lang == "uk" else 0.80,
                detected_tokens={"uk": clean_tokens},
                details={"resolved_via": "uk_tokens_with_context"},
            )

        if clean_tokens in [["як", "ти"], ["ти", "як"]]:
            return LanguageDetectionResult(
                language="uk",
                confidence=0.88 if context_lang == "uk" else 0.75,
                detected_tokens={"uk": clean_tokens},
            )

        if context_lang in ("uk", "ru", "mixed"):
            return LanguageDetectionResult(
                language=context_lang,
                confidence=0.75,
                details={"resolved_via": "conversation_context", "context_lang": context_lang},
            )

        # Default fallback
        if has_cyrillic:
            return LanguageDetectionResult(
                language="ru",
                confidence=0.60,
                details={"fallback": "cyrillic_default"},
            )

        return LanguageDetectionResult(
            language="unknown",
            confidence=0.0,
            details={"fallback": "no_language_match"},
        )

    @classmethod
    def _infer_contextual_language(
        cls,
        history: Optional[List[Dict[str, Any]]] = None,
        recent_language: Optional[str] = None,
    ) -> Optional[str]:
        """Infers the active conversation language from recent messages or contact state."""
        if recent_language and recent_language in ("uk", "ru", "mixed"):
            return recent_language

        if not history:
            return None

        # Look at last 5 user messages
        user_msgs = []
        for msg in reversed(history):
            role = msg.get("role") or msg.get("sender_role") or ""
            if role in ("user", "human"):
                parts = msg.get("parts") or [msg.get("text", "")]
                text = " ".join(str(p) for p in parts if p)
                if text:
                    user_msgs.append(text)
                if len(user_msgs) >= 5:
                    break

        uk_count = 0
        ru_count = 0
        for text in user_msgs:
            low = text.lower()
            if any(c in low for c in cls.UK_CHARACTERS) or any(w in low for w in ["привіт", "як", "шо", "зараз", "дякую", "добре"]):
                uk_count += 1
            elif any(c in low for c in cls.RU_CHARACTERS) or any(w in low for w in ["привет", "как", "что", "сейчас", "спасибо"]):
                ru_count += 1

        if uk_count > 0 and ru_count > 0:
            return "mixed"
        if uk_count > ru_count:
            return "uk"
        if ru_count > uk_count:
            return "ru"

        return None

    @classmethod
    def normalize_slang(
        cls,
        text: str,
        language: str = "auto",
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        recent_language: Optional[str] = None,
    ) -> NormalizedTextResult:
        """
        Non-destructive slang & abbreviation normalizer.
        Generates an expanded semantic representation for internal reasoning
        WITHOUT overwriting the original transcript.
        """
        if not text:
            return NormalizedTextResult(original_text="", normalized_text="", language="unknown")

        target_lang = language
        if target_lang == "auto":
            det = cls.detect_language(text, conversation_history, recent_language)
            target_lang = det.language

        normalized = text
        slang_found: List[Dict[str, str]] = []

        mapping = cls.UK_SLANG_NORMALIZATION_MAP if target_lang == "uk" else cls.RU_SLANG_NORMALIZATION_MAP
        if target_lang == "mixed":
            # For mixed speech, combine both mappings prioritizing Ukrainian slang
            mapping = cls.UK_SLANG_NORMALIZATION_MAP + cls.RU_SLANG_NORMALIZATION_MAP

        for pattern, replacement, label in mapping:
            if re.search(pattern, normalized, flags=re.IGNORECASE):
                normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
                slang_found.append({"raw": label, "expanded": replacement})

        return NormalizedTextResult(
            original_text=text,
            normalized_text=normalized.strip(),
            language=target_lang,
            slang_detected=slang_found,
        )

    @classmethod
    def update_language_preference(
        cls,
        current_pref_json: Optional[Union[str, Dict[str, float]]],
        incoming_language: str,
        confidence: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Maintains rolling weighted language preference per contact (Section 10).
        Recent messages have more weight than very old messages.
        Returns updated preference dictionary and current user language.
        """
        if incoming_language in ("en", "other"):
            return {
                "language_preference": {"en": 1.0, "ru": 0.0, "uk": 0.0, "mixed": 0.0},
                "current_user_language": "en",
                "recent_language": incoming_language,
                "language_pattern": "other",
            }

        if not current_pref_json:
            init_lang = incoming_language if incoming_language in ("ru", "uk", "mixed") else "ru"
            initial_pref = {"ru": 0.0, "uk": 0.0, "mixed": 0.0}
            initial_pref[init_lang] = 1.0
            return {
                "language_preference": initial_pref,
                "current_user_language": init_lang,
                "recent_language": incoming_language,
                "language_pattern": f"monolingual_{init_lang}" if init_lang in ("ru", "uk") else "mixed",
            }

        prefs: Dict[str, float] = {"ru": 0.0, "uk": 0.0, "mixed": 0.0}
        if isinstance(current_pref_json, str):
            try:
                loaded = json.loads(current_pref_json)
                if isinstance(loaded, dict):
                    prefs.update(loaded)
            except Exception:
                pass
        elif isinstance(current_pref_json, dict):
            prefs.update(current_pref_json)

        # Decay weights and boost incoming language
        decay = 0.70
        boost = 0.30 * max(min(confidence, 1.0), 0.5)

        for lang in ["ru", "uk", "mixed"]:
            prefs[lang] = prefs.get(lang, 0.0) * decay

        if incoming_language in prefs:
            prefs[incoming_language] += boost
        elif incoming_language in ("ru", "uk", "mixed"):
            prefs[incoming_language] = boost

        # Normalize to sum = 1.0
        total = sum(prefs.values())
        if total > 0:
            for k in prefs:
                prefs[k] = round(prefs[k] / total, 3)

        # Determine current user language and pattern
        ru_val = prefs.get("ru", 0.0)
        uk_val = prefs.get("uk", 0.0)
        mixed_val = prefs.get("mixed", 0.0)

        if mixed_val >= 0.35 or (abs(ru_val - uk_val) < 0.15 and ru_val > 0.3 and uk_val > 0.3):
            lang_pattern = "mixed"
            current_lang = "mixed"
        elif uk_val > ru_val:
            lang_pattern = "monolingual_uk"
            current_lang = "uk"
        else:
            lang_pattern = "monolingual_ru"
            current_lang = "ru"

        return {
            "language_preference": prefs,
            "current_user_language": current_lang,
            "recent_language": incoming_language,
            "language_pattern": lang_pattern,
        }

    @classmethod
    def analyze_dynamic_language(
        cls,
        text: str,
        conversation_history: Optional[List[Any]] = None,
        contact: Optional[Any] = None,
    ) -> DynamicLanguageProfile:
        """
        Analyzes incoming text and context across the 6-level weighted priority:
        1. Explicit user request (e.g. "ответь на английском", "speak english", "пиши українською")
        2. Current incoming message
        3. Recent messages in conversation
        4. Established conversation language
        5. Long-term user profile preference
        6. Default (Russian)

        Also analyzes slang level, profanity intensity, abbreviations, script (cyrillic vs latin/translit),
        and formality.
        """
        cleaned_text = (text or "").strip()
        low = cleaned_text.lower()

        # Priority 1: Explicit user request
        for pattern, override_lang in cls.EXPLICIT_LANGUAGE_OVERRIDES:
            if pattern.search(low):
                return DynamicLanguageProfile(
                    primary_language=override_lang,
                    secondary_language=getattr(contact, "language", None) if contact else None,
                    language_confidence=0.99,
                    style="casual",
                    is_explicit_override=True,
                    override_reason=f"Explicit user request to switch language to '{override_lang}'",
                )

        # Priority 2: Current incoming message
        det_res = cls.detect_language(
            cleaned_text,
            conversation_history=conversation_history,
            recent_language=getattr(contact, "language", None) if contact else None,
        )

        # Priority 3, 4, 5, 6 Resolution
        chosen_lang = "ru"
        confidence = 0.85
        sec_lang = None

        if det_res.language in ("uk", "en", "mixed", "ru") and det_res.confidence >= 0.70:
            chosen_lang = "en" if det_res.language == "other" or det_res.details.get("language") == "en" else det_res.language
            confidence = det_res.confidence
        elif len(cleaned_text.split()) <= 2:
            # Short ambiguous message (e.g. "ок", "норм", "да", "привет", "ясно") -> Priority 3, 4, 5
            ctx_lang = cls._infer_contextual_language(conversation_history, getattr(contact, "language", None) if contact else None)
            if ctx_lang:
                chosen_lang = ctx_lang
                confidence = 0.80
            elif contact and getattr(contact, "recent_language", None):
                chosen_lang = contact.recent_language
                confidence = 0.75
            elif contact and getattr(contact, "language", None):
                chosen_lang = contact.language
                confidence = 0.70
            else:
                chosen_lang = det_res.language if det_res.language in ("uk", "ru", "mixed", "en") else "ru"
                confidence = max(det_res.confidence, 0.60)
        else:
            if det_res.language in ("uk", "ru", "mixed"):
                chosen_lang = det_res.language
                confidence = det_res.confidence
            elif det_res.language in ("other", "en") or det_res.details.get("language") == "en":
                chosen_lang = "en"
                confidence = det_res.confidence
            elif contact and getattr(contact, "language", None):
                chosen_lang = contact.language
                confidence = 0.70

        # Script & Transliteration analysis
        has_latin = bool(re.search(r"[a-zA-Z]", cleaned_text))
        has_cyrillic = bool(re.search(r"[\u0400-\u04FF]", cleaned_text))
        is_translit = False
        script = "cyrillic"

        if has_latin and not has_cyrillic:
            script = "latin"
            if chosen_lang in ("ru", "uk", "mixed") or det_res.is_transliterated:
                is_translit = True

        # Slang & Abbreviations analysis
        tokens = set(re.findall(r"[\w'’]+", low))
        slang_matches = tokens.intersection(cls.SLANG_TOKENS)
        abbr_matches = tokens.intersection(cls.ABBREVIATIONS)
        total_slang = len(slang_matches) + len(abbr_matches)

        if total_slang >= 2:
            slang_level = 0.85
        elif total_slang == 1:
            slang_level = 0.50
        else:
            slang_level = 0.15

        # Profanity intensity analysis
        profanity_count = 0
        for pat in cls.PROFANITY_PATTERNS:
            profanity_count += len(pat.findall(low))
        profanity_level = min(1.0, round(0.4 * profanity_count, 2)) if profanity_count > 0 else 0.0

        # Formality analysis
        formal_markers = ["здравствуйте", "добрый день", "доброго дня", "ви", "вы", "вас", "вам", "вами"]
        is_formal = any(m in tokens for m in formal_markers) and profanity_level == 0.0 and total_slang == 0
        formality = 0.85 if is_formal else 0.10

        # Emoji / Smiley analysis
        no_brackets = getattr(contact, "no_brackets", False) if contact else False
        if no_brackets:
            emoji_level = 0.0
        else:
            bracket_count = len(re.findall(r"[\)\(]{1,3}", cleaned_text))
            emoji_count = len(re.findall(r"[\U00010000-\U0010ffff]", cleaned_text))
            emoji_level = min(1.0, round(0.2 * (bracket_count + emoji_count), 2))

        # Overall style
        if formality >= 0.6:
            style = "formal"
        elif slang_level >= 0.5 or emoji_level >= 0.4:
            style = "playful"
        else:
            style = "casual"

        return DynamicLanguageProfile(
            primary_language=chosen_lang,
            secondary_language=sec_lang,
            language_confidence=confidence,
            style=style,
            slang_level=slang_level,
            profanity_level=profanity_level,
            emoji_level=emoji_level,
            formality=formality,
            transliteration=is_translit,
            script=script,
            is_explicit_override=False,
            override_reason="",
        )

    @classmethod
    def log_developer_debug(
        cls,
        message_id: Any,
        message_type: str,
        language: str,
        language_confidence: float,
        transcript: str,
        normalized_text: str,
        stt_confidence: float,
    ) -> None:
        """
        Standardized developer debug output (Section 14).
        Never displayed to the end-user.
        """
        debug_output = (
            "\n" + "=" * 50 + "\n"
            f"[MULTILINGUAL DEBUG]\n"
            f"MESSAGE: {message_id}\n"
            f"TYPE: {message_type}\n"
            f"DETECTED LANGUAGE: {language}\n"
            f"LANGUAGE CONFIDENCE: {language_confidence:.2f}\n"
            f'TRANSCRIPT: "{transcript}"\n'
            f'NORMALIZED INTERNAL FORM: "{normalized_text}"\n'
            f"STT CONFIDENCE: {stt_confidence:.2f}\n"
            + "=" * 50
        )
        logger.info(debug_output)
