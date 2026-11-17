import re
from dataclasses import dataclass, field
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
    preferred_category: Optional[str] = None
    preferred_tags: List[str] = field(default_factory=list)
    is_repeat_request: bool = False
    confidence: float = 0.0
    reason: str = ""


@dataclass
class VoiceIntent:
    requested: bool
    preferred_tag: str = "greeting"  # greeting, meetup, sarcastic_name, etc.


@dataclass
class ContactDeflectionIntent:
    contact_request: bool
    contact_type: str = "none"  # "instagram", "phone", "whatsapp", "telegram_username", "other_social", "other"
    request_intensity: str = "soft"  # "soft", "normal", "persistent", "pressuring"
    deflection_required: bool = False
    request_count: int = 1
    trigger: Optional[str] = None
    suggested_deflection_tone: str = "playful_dodge"  # "playful_dodge", "stronger_dodge", "calm_boundary", "firm_boundary"
    reason: str = ""
    channel_switch_intent: Optional[Any] = None


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

        # Українські пропозиції зустрічі / побачення / кави
        (r"\b(давай\s+зустрін(емось|утись)|може\s+зустрін(емось|утись)|коли\s+зустрін(емось|утись)|хочу\s+побачит(ись|ися))\b", "direct_meet", 0.95),
        (r"\b(підемо\s+на\s+побачення|сходи(мо|ти)\s+на\s+побачення)\b", "direct_meet", 0.99),
        (r"\b(вип['’]ємо\s+кави|поп['’]ємо\s+кави|сходи(мо|ти)\s+на\s+каву|погуля(ємо|ти)|пішли\s+гуляти|сходи(мо|ти)\s+в\s+ресторан|сходи(мо|ти)\s+в\s+кафе)\b", "coffee_dinner", 0.95),
        (r"\b(які\s+плани\s+на\s+вечір|що\s+робиш\s+сьогодні\s+ввечері|вільна\s+сьогодні|вільна\s+ввечері)\b", "location_ask", 0.80),
        (r"\b(скинь\s+(свій\s+)?номер|дай\s+(свій\s+)?номер|напиши\s+(свій\s+)?номер)\b", "number_request", 0.90),
    ]

    # Explicit agreement to a proposed meeting / hangout / quest / coffee (RU + UK)
    MEETUP_AGREEMENT_PATTERNS = [
        r"\b(я\s+за|я\s+не\s+против|погнали|погнали\s+в\s+квест|пошли|пошли\s+сходим|давай\s+сходим|можем\s+сходить|я\s+согласен|договорились|с\s+удовольствием|звучит\s+круто|звучит\s+заманчиво|я\s+в\s+деле|я\s+свободен|могу\s+в\s+субботу|могу\s+в\s+воскресенье|давай\s+в\s+субботу|давай\s+в\s+воскресенье|давай\s+на\s+выходных|давай\s+с\s+тобой|я\s+только\s+за|отличная\s+идея|хорошая\s+идея|крутая\s+идея)\b",
        r"\b(я\s+не\s+проти|пішли\s+сходимо|давай\s+сходимо|можемо\s+сходити|я\s+згоден|домовились|із\s+задоволенням|звучить\s+заманливо|я\s+в\s+ділі|я\s+вільний|можу\s+в\s+суботу|можу\s+в\s+неділю|чудова\s+ідея|гарна\s+ідея|я\s+тільки\s+за)\b",
    ]

    # Photo request patterns (RU + UK)
    PHOTO_PATTERNS = [
        (r"\b(скинь|покажи|кинь|отправь|надішли|сделай)(\s+[\w\-]+)?\s+(фот(ку|о)|селфи|селфі|світлину|картинк|снимок)\b", 0.95),
        (r"\b(как\s+ты\s+выглядишь|як\s+ти\s+виглядаєш|покажи\s+себя|покажи\s+себе|скинь\s+себя|скинь\s+лицо|покажи\s+лицо)\b", 0.90),
        (r"\b(скинь\s+свое\s+фото|скинь\s+фотку|пришли\s+фото|покажи\s+фото)\b", 0.95),
        (r"\b(где\s+ты|что\s+вокруг|покажи\s+где\s+ты|скинь\s+вид)\b", 0.80),
    ]

    # Explicit request for the SAME/PREVIOUS photo (Section 13: Absolute no-repeat rule)
    SAME_PHOTO_PATTERNS = [
        r"\b(ту\s+же\s+фотку|снова\s+то\s+фото|еще\s+раз\s+то\s+фото|повтори\s+фото|скинь\s+еще\s+раз\s+ту)\b",
        r"\b(ту\s+саму\s+фотку|знову\s+те\s+фото|ще\s+раз\s+те\s+фото)\b",
    ]

    # Photo category keywords
    PHOTO_CATEGORY_KEYWORDS = {
        "gym": ["зал", "спорт", "тренировк", "треня", "спортзал", "тренуванн"],
        "home": ["дома", "домашн", "кровать", "пижам", "уют", "вдома", "ліжко", "комнат"],
        "coffee": ["кофе", "кафе", "чай", "кава", "латте", "капучино", "чашк"],
        "food": ["еда", "обед", "ужин", "завтрак", "пирож", "десерт", "вкусн", "їжа", "обід", "вечеря"],
        "outdoors": ["улиц", "парк", "погод", "солнц", "дождь", "воздух", "вулиц"],
        "city": ["город", "прогулк", "гуля", "центр", "архитектур", "місто"],
        "pet": ["кот", "котик", "кошка", "собак", "пес", "питомец", "тваринк"],
        "outfit": ["платье", "лук", "шмот", "наряд", "одежд", "одяг"],
        "travel": ["поезд", "самолет", "вокзал", "дорог", "путешеств", "мандрівк"],
        "hobby": ["рисун", "дизайн", "книг", "выставк"],
        "selfie": ["селфи", "селфі", "себя", "себе", "лицо", "мордашку"],
    }

    # Stop words / aggressive pushback / abuse (RU + UK)
    STOP_PATTERNS = [
        r"\b(отстань|отвали|не\s+пиши\s+мне|заебал|заблокирую|в\s+чс|ты\s+бот|хватит\s+спамить)\b",
        r"\b(відчепись|відвали|не\s+пиши\s+мені|заблокую|ти\s+бот|годі\s+спамити)\b",
        r"\b(нахуй|пошел\s+нахуй|пошла\s+нахуй|пішов\s+нахуй|пішла\s+нахуй|сука|шлюха|тварь|мразь|хуйло|уебок|уебище|пидор)\b",
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
    def detect_meetup_agreement(cls, text: str) -> bool:
        """
        Detects if the interlocutor actively agrees to meet, hang out, go to a quest or coffee.
        """
        cleaned = text.lower()
        for pattern in cls.MEETUP_AGREEMENT_PATTERNS:
            if re.search(pattern, cleaned):
                return True
        return False

    @classmethod
    def detect_photo_request(cls, text: str) -> PhotoIntent:
        """
        Detects if the user is asking for a photo, infers preferred category/tags,
        and detects if the user is asking for the exact same previous photo.
        """
        cleaned = text.lower()

        # Check for request of the SAME/PREVIOUS photo
        for pattern in cls.SAME_PHOTO_PATTERNS:
            if re.search(pattern, cleaned):
                return PhotoIntent(
                    requested=True,
                    is_repeat_request=True,
                    confidence=0.95,
                    reason="User explicitly requested the same photo again. Absolute no-repeat rule applies.",
                )

        is_requested = False
        confidence = 0.0
        for pattern, conf in cls.PHOTO_PATTERNS:
            if re.search(pattern, cleaned):
                is_requested = True
                confidence = max(confidence, conf)

        if not is_requested:
            return PhotoIntent(requested=False)

        # Infer category and tags
        chosen_cat = None
        matched_tags = []
        for cat, keywords in cls.PHOTO_CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in cleaned:
                    if not chosen_cat:
                        chosen_cat = cat
                    matched_tags.append(kw)

        chosen_tag = chosen_cat or "general"
        return PhotoIntent(
            requested=True,
            preferred_tag=chosen_tag,
            preferred_category=chosen_cat or "general",
            preferred_tags=matched_tags,
            is_repeat_request=False,
            confidence=confidence,
            reason="explicit_user_request",
        )

    @classmethod
    def is_stop_word_present(cls, text: str) -> bool:
        """Detects rejection, anger, or bot accusations."""
        cleaned = text.lower()
        for pattern in cls.STOP_PATTERNS:
            if re.search(pattern, cleaned):
                return True
        return False

    VOICE_PATTERNS = [
        r"\b(скинь|запиши|отправь|кинь|надішли)\s+(войс|голосовуху|голосове|гс)\b",
        r"\b(скажи\s+голосом|хочу\s+(услышать|послушать|почути|послухати)\s+голос|поговори\s+голосом)\b",
        r"\b(ты\s+точно\s+девушка|ти\s+точно\s+дівчина|ты\s+не\s+бот|ти\s+не\s+бот|докажи\s+(что|що)\s+не\s+бот|запиши\s+(кружок|кружечок))\b",
    ]

    @classmethod
    def detect_voice_request(cls, text: str) -> VoiceIntent:
        """Detects when an interlocutor asks for a voice message or questions authenticity."""
        cleaned = text.lower()
        for pattern in cls.VOICE_PATTERNS:
            if re.search(pattern, cleaned):
                if any(w in cleaned for w in ["кофе", "кава", "встрет", "зустрін", "увид", "побач", "где", "де", "когда", "коли"]):
                    return VoiceIntent(requested=True, preferred_tag="meetup")
                if any(w in cleaned for w in ["издева", "знуща", "бот", "фейк", "докажи", "точно", "кружок", "кружечок"]):
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
        # Українські запити без дужок / смайликів
        r"(?i)\b(без\s+дужок|не\s+став\s+дужки|годі\s+(?:ставити\s+)?дуж(?:ки|ок|ками)?|навіщо\s+дужки)\b",
        r"(?i)\b(прибери\s+дужки|перестань\s+ставити\s+дужки|пиши\s+без\s+дужок|давай\s+без\s+дужок)\b",
        r"(?i)\b(без\s+смайликів|годі\s+смайликів|не\s+став\s+смайлики)\b",
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

    # =========================================================================
    # ANTI-INSTAGRAM & PERSONAL CONTACT DEFLECTION PATTERNS
    # =========================================================================
    INSTAGRAM_DEFLECTION_PATTERNS = [
        # Direct Instagram requests (RU + UK + EN)
        (r"\b(инст[аеуыо]?|инстаграм|инстаграмм|instagram|insta|ig|інст[ауіе]?|інстаграм)\b", "instagram", 0.95),
        (r"\b(скинь|кинь|дай|напиши|покажи|какой|можно|где|пойдем\s+в|пойдём\s+в|давай\s+в|перейдем\s+в|перейдём\s+в|пішли\s+в|давай\s+в)\s+.*(инст|instagram|insta|ig|інст)\b", "instagram", 0.98),
        (r"\b(ссылк[уа]\s+на\s+(инст|профиль)|профиль\s+в\s+инсте|твой\s+(инст|instagram)|твій\s+інст)\b", "instagram", 0.95),
        # Existence inquiries ("у тебя вообще есть инста?")
        (r"\b(у\s+тебя\s+(вообще\s+)?есть\s+инст|ты\s+есть\s+в\s+инст|сидишь\s+в\s+инст|ведешь\s+инст|чи\s+є\s+(в\s+тебе\s+)?інст|ти\s+є\s+в\s+інст)\b", "instagram", 0.90),
    ]

    OTHER_CONTACT_DEFLECTION_PATTERNS = [
        # Usernames, Telegram @ outside chat, handles
        (r"\b(дай|скинь|твой|напиши|кинь|какой)\s+@", "telegram_username", 0.95),
        (r"\b(дай|скинь|напиши|какой|твой|кинь)\s+(свой\s+)?(юзернейм|ник|username|тг\s+ник|профиль|контакт)\b", "telegram_username", 0.90),
        (r"\b(дай|скинь|напиши|какой|твой|кинь)\s+(свій\s+)?(юзернейм|нік|username|профіль)\b", "telegram_username", 0.90),
        # Phone numbers & WhatsApp
        (r"\b(номер\s+телефона|твой\s+номер|твій\s+номер|скинь\s+(свой\s+)?номер|дай\s+(свой\s+)?номер|напиши\s+цифры)\b", "phone", 0.95),
        (r"\b(ватсап|вацап|whatsapp|вотсап|вотсапп)\b", "whatsapp", 0.95),
        (r"\b(тикток|tiktok|снапчат|snapchat)\b", "other_social", 0.90),
        # Where to find you
        (r"\b(где\s+тебя\s+найти|как\s+тебя\s+найти|как\s+тебя\s+искать|де\s+тебе\s+знайти|як\s+тебе\s+знайти)\b", "other_social", 0.85),
        # Manipulation / framing attempts
        (r"\b(ну\s+просто\s+ник|хотя\s+бы\s+первые\s+буквы|я\s+никому\s+не\s+скажу|это\s+же\s+не\s+номер|ну\s+кинь\s+ссылку|да\s+я\s+просто\s+посмотрю|это\s+же\s+безопасно|ты\s+же\s+мне\s+доверяешь|если\s+нравлюсь\s+дай|докажи\s+что\s+нравлюсь|это\s+всего\s+лишь\s+инста)\b", "instagram", 0.95),
    ]

    @classmethod
    def detect_contact_request(
        cls,
        text: str,
        contact_deflection_count: int = 0,
        recent_chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> ContactDeflectionIntent:
        """
        Classifies incoming messages for personal contact requests (Instagram, WhatsApp,
        usernames, phone numbers, external messengers, calls, links).
        Enforces Telegram-Only policy and multi-level deflection progression.
        """
        cleaned = (text or "").lower()

        matched_type = None
        matched_trigger = None
        confidence = 0.0

        for pattern, c_type, conf in cls.INSTAGRAM_DEFLECTION_PATTERNS:
            m = re.search(pattern, cleaned)
            if m:
                matched_type = c_type
                matched_trigger = m.group(0)
                confidence = conf
                break

        if not matched_type:
            for pattern, c_type, conf in cls.OTHER_CONTACT_DEFLECTION_PATTERNS:
                m = re.search(pattern, cleaned)
                if m:
                    matched_type = c_type
                    matched_trigger = m.group(0)
                    confidence = conf
                    break

        # Also delegate to ExternalChannelPolicyService for comprehensive multi-platform coverage
        from companion.services.external_channel_policy_service import (
            ExternalChannelPolicyService,
            ChannelSwitchIntent,
        )

        cs_intent = ExternalChannelPolicyService.classify_intent(
            text=text,
            deflection_count=contact_deflection_count,
            recent_chat_history=recent_chat_history,
        )

        if not matched_type:
            if cs_intent.channel_switch and cs_intent.deflection_required:
                # Map platform / channel_type to contact_type
                if cs_intent.platform == "instagram":
                    matched_type = "instagram"
                elif cs_intent.platform in ("whatsapp", "wa"):
                    matched_type = "whatsapp"
                elif cs_intent.platform == "phone":
                    matched_type = "phone"
                elif cs_intent.intent in ("request_username", "obfuscated_username"):
                    matched_type = "telegram_username"
                else:
                    matched_type = cs_intent.platform or cs_intent.channel_type or "other_social"
                matched_trigger = cs_intent.trigger or text[:30]
                confidence = cs_intent.confidence

        if not matched_type:
            return ContactDeflectionIntent(
                contact_request=False,
                deflection_required=False,
                channel_switch_intent=cs_intent,
            )

        req_count = contact_deflection_count + 1

        if req_count <= 1:
            intensity = "soft"
            tone = "playful_dodge"
        elif req_count == 2:
            intensity = "normal"
            tone = "stronger_dodge"
        elif req_count == 3:
            intensity = "persistent"
            tone = "calm_boundary"
        else:
            intensity = "pressuring"
            tone = "firm_boundary"

        return ContactDeflectionIntent(
            contact_request=True,
            contact_type=matched_type,
            request_intensity=intensity,
            deflection_required=True,
            request_count=req_count,
            trigger=matched_trigger,
            suggested_deflection_tone=tone,
            reason=f"Detected {matched_type} request: '{matched_trigger}' (count={req_count})",
            channel_switch_intent=cs_intent,
        )


