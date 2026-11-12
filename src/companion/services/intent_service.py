import re
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class MeetupIntent:
    is_proposal: bool
    confidence: float
    trigger: Optional[str] = None
    category: str = "none"  # "direct_meet", "coffee_dinner", "number_request", "location_ask"


@dataclass
class PhotoIntent:
    requested: bool
    preferred_tag: str = "general"


@dataclass
class VoiceIntent:
    requested: bool
    preferred_tag: str = "greeting"  # greeting, meetup, sarcastic_name, etc.


class IntentService:
    # Triggers for in-person meetup / dates
    MEETUP_PATTERNS = [
        # Прямые предложения встречи
        (r"\b(давай\s+встрет(имся|иться)|го\s+встрет(имся|иться)|может\s+встрет(имся|иться))\b", "direct_meet", 0.95),
        (r"\b(когда\s+встрет(имся|иться)|когда\s+увид(имся|еться))\b", "direct_meet", 0.95),
        (r"\b(может\s+пересеч(емся|ься)|давай\s+пересеч(емся|ься)|го\s+пересеч(емся|ься))\b", "direct_meet", 0.90),
        (r"\b(хочу\s+увид(еть|еться)|хочу\s+встрет(иться|ить))\b", "direct_meet", 0.90),
        (r"\b(пойдем\s+на\s+свидание|сходи(м|ть)\s+на\s+свидание)\b", "direct_meet", 0.99),
        
        # Кофе, ужин, бар, прогулка
        (r"\b(по(пьем|пить)\s+кофе|вы(пьем|пить)\s+кофе|сходи(м|ть)\s+на\s+кофе|позови\s+на\s+кофе)\b", "coffee_dinner", 0.95),
        (r"\b(по(ужинаем|ужинать)|по(обедаем|обедать)|сходи(м|ть)\s+в\s+ресторан|сходи(м|ть)\s+в\s+кафе)\b", "coffee_dinner", 0.95),
        (r"\b(вы(пьем|пить)\s+по\s+бокалу|вы(пьем|пить)\s+вина|сходи(м|ть)\s+в\s+бар)\b", "coffee_dinner", 0.90),
        (r"\b(по(гуляем|гулять)|сходи(м|ть)\s+погулять)\b", "coffee_dinner", 0.85),
        
        # Предложения планов на вечер
        (r"\b(какие\s+планы\s+на\s+вечер|что\s+делаешь\s+сегодня\s+вечером|свободна\s+сегодня|свободна\s+вечером)\b", "location_ask", 0.80),
        
        # Запрос номера / мессенджера для связи в реале
        (r"\b(скинь\s+(свой\s+)?номер|дай\s+(свой\s+)?номер|напиши\s+(свой\s+)?номер)\b", "number_request", 0.90),
        (r"\b(скинь\s+(ватсап|вотсап|тг|номер\s+телефона)|номер\s+телефона\s+скинь)\b", "number_request", 0.90),
    ]

    # Photo request patterns
    PHOTO_PATTERNS = [
        (r"\b(скинь|покажи|кинь|отправь|сделай)(\s+[\w\-]+)?\s+(фот(ку|о)|селфи)\b", 0.95),
        (r"\b(как\s+ты\s+выглядишь|покажи\s+себя|скинь\s+себя)\b", 0.90),
    ]

    # Photo tags keywords
    PHOTO_TAG_KEYWORDS = {
        "home": ["дома", "домашн", "кровать", "пижам", "уют"],
        "cafe": ["кофе", "кафе", "ресторан", "чай", "обед"],
        "gym": ["зал", "спорт", "тренировк", "фитнес"],
        "walk": ["прогулк", "улиц", "парк", "город", "гуля"],
        "car": ["машин", "авто", "рул"],
    }

    # Stop words / aggressive pushback / abuse
    STOP_PATTERNS = [
        r"\b(отстань|отвали|не\s+пиши\s+мне|заебал|заблокирую|в\s+чс|ты\s+бот|хватит\s+спамить)\b",
        r"\b(нахуй|пошел\s+нахуй|пошла\s+нахуй|сука|шлюха|тварь|мразь|хуйло|уебок|уебище|пидор)\b",
    ]

    @classmethod
    def detect_meetup_proposal(cls, text: str) -> MeetupIntent:
        """
        Analyzes if the incoming message is an invitation to meet, go on a date,
        or exchange direct phone numbers.
        """
        cleaned = text.lower()
        for pattern, category, confidence in cls.MEETUP_PATTERNS:
            match = re.search(pattern, cleaned)
            if match:
                return MeetupIntent(
                    is_proposal=True,
                    confidence=confidence,
                    trigger=match.group(0),
                    category=category,
                )
        return MeetupIntent(is_proposal=False, confidence=0.0)

    @classmethod
    def detect_photo_request(cls, text: str) -> PhotoIntent:
        """
        Detects if the user is asking for a photo and infers preferred context/tag.
        """
        cleaned = text.lower()
        is_requested = False
        for pattern, _ in cls.PHOTO_PATTERNS:
            if re.search(pattern, cleaned):
                is_requested = True
                break

        if not is_requested:
            return PhotoIntent(requested=False)

        # Infer tag
        chosen_tag = "general"
        for tag, keywords in cls.PHOTO_TAG_KEYWORDS.items():
            for kw in keywords:
                if kw in cleaned:
                    chosen_tag = tag
                    break
            if chosen_tag != "general":
                break

        return PhotoIntent(requested=True, preferred_tag=chosen_tag)

    @classmethod
    def is_stop_word_present(cls, text: str) -> bool:
        """Detects rejection, anger, or bot accusations."""
        cleaned = text.lower()
        for pattern in cls.STOP_PATTERNS:
            if re.search(pattern, cleaned):
                return True
        return False

    VOICE_PATTERNS = [
        r"\b(скинь|запиши|отправь|кинь)\s+(войс|голосовуху|голосовое|гс)\b",
        r"\b(скажи\s+голосом|хочу\s+(услышать|послушать)\s+голос|поговори\s+голосом)\b",
        r"\b(ты\s+точно\s+девушка|ты\s+не\s+бот|докажи\s+что\s+не\s+бот|запиши\s+кружок)\b",
    ]

    @classmethod
    def detect_voice_request(cls, text: str) -> VoiceIntent:
        """Detects when an interlocutor asks for a voice message or questions authenticity."""
        cleaned = text.lower()
        for pattern in cls.VOICE_PATTERNS:
            if re.search(pattern, cleaned):
                if any(w in cleaned for w in ["кофе", "встрет", "увид", "где", "когда"]):
                    return VoiceIntent(requested=True, preferred_tag="meetup")
                if any(w in cleaned for w in ["издева", "бот", "фейк", "докажи", "точно", "кружок"]):
                    return VoiceIntent(requested=True, preferred_tag="sarcastic_name")
                return VoiceIntent(requested=True, preferred_tag="greeting")
        return VoiceIntent(requested=False)

    NO_BRACKETS_PATTERNS = [
        r"(?i)\b(без\s+скоб(?:ок|очек)?|не\s+(?:ставь|пиши|общайся|используй)\s+(?:со?\s+)?скоб(?:ки|ок|очки|очек|ками|очками)?)\b",
        r"(?i)\b(хватит\s+(?:ставить\s+)?скоб(?:ки|ок|очки|очек|ками|очками)?|зачем\s+(?:везде\s+)?скоб(?:ки|очки)?)\b",
        r"(?i)\b(почему\s+(?:ты\s+)?(?:постоянно\s+|везде\s+)?(?:со?\s+)?скоб(?:ками|очками|ки|очки)?)\b",
        r"(?i)\b(убери\s+скоб(?:ки|очки)?|перестань\s+(?:ставить\s+)?скоб(?:ки|очки)?)\b",
        r"(?i)\b(попробуй\s+(?:писать\s+)?без\s+скоб(?:ок|очек)?|пиши\s+без\s+скоб(?:ок|очек)?)\b",
        r"(?i)\b(давай\s+без\s+скоб(?:ок|очек)?|хватит\s+скобочек)\b",
        r"(?i)\b(без\s+смайл(?:ов|иков)?|хватит\s+смайл(?:ов|иков)?|не\s+ставь\s+смайл(?:ы|ики)?)\b",
    ]

    @classmethod
    def detect_no_brackets_request(cls, text: str) -> bool:
        """
        Detects if the interlocutor asks or demands to write without brackets/smiles.
        Used to personalize and strictly adapt the communication style per contact.
        """
        cleaned = text.strip()
        for pattern in cls.NO_BRACKETS_PATTERNS:
            if re.search(pattern, cleaned):
                return True
        return False

    AGREEMENT_PATTERNS = [
        (r"(?i)\b(?:через\s+час(?:ик)?|спишемся\s+через\s+час(?:ик)?|напишу\s+через\s+час(?:ик)?|отойду\s+на\s+час(?:ик)?|буду\s+через\s+час(?:ик)?)\b", "in_one_hour"),
        (r"(?i)\b(?:до\s+завтра|завтра\s+спишемся|спишемся\s+завтра|поговорим\s+завтра|завтра\s+напишу|завтра\s+договорим|завтра\s+расскажу|завтра\s+наберу)\b", "tomorrow"),
        (r"(?i)\b(?:спокойной\s+ночи|сладких\s+снов|добрых\s+снов|я\s+спать|пойду\s+спать|ложусь\s+спать|пора\s+спать)\b", "goodnight"),
    ]

    CLOSURE_PATTERNS = [
        r"^(?:ок|ясно|понял|хорошо|ага|ладн|ладно|понятно|сяп|спс|пон)[.!, ]*$",
    ]

    @classmethod
    def detect_agreement_or_pause(cls, text: str) -> Optional[str]:
        """
        Detects implicit or explicit conversational timing agreements:
        - 'in_one_hour': partner requested to reconnect in ~1 hour.
        - 'tomorrow': partner wants to talk tomorrow.
        - 'goodnight': partner is going to sleep tonight.
        - 'closure': brief acknowledgments that don't need immediate follow-up.
        """
        cleaned = text.strip()
        for pattern, category in cls.AGREEMENT_PATTERNS:
            if re.search(pattern, cleaned):
                return category

        for pattern in cls.CLOSURE_PATTERNS:
            if re.search(pattern, cleaned, flags=re.IGNORECASE):
                return "closure"

        return None


