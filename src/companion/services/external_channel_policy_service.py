"""
ExternalChannelPolicyService: Strict Telegram-Only Communication Policy Engine.
Enforces the 140 requirements of the Master Task:
- Telegram is the only supported communication channel.
- Detect intent, not only keywords (differentiates conversational mentions from migration requests).
- Multi-tier classification: direct, indirect, implied, slang, transliteration, obfuscated, Ukrainian, mixed.
- Never provide or accept external contact details (ALLOW_EXTERNAL_CONTACT = False).
- Never confirm external identity or claim external actions (add/follow/message/visit).
- Never sound robotic, cite system policies, mention bots, or invent fake excuses.
- Conversational multi-level escalation: SOFT_PREFERENCE -> WARM_BOUNDARY -> FIRM_BOUNDARY.
- Application-level pre-send outgoing firewall with bounded regeneration and natural healing.
"""

import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ChannelSwitchIntent:
    """
    Structured internal classification for channel-switch detection.
    Never exposed directly to the user.
    """
    channel_switch: bool
    channel_type: str = "none"
    # "telegram", "external_social", "external_messenger", "phone", "email",
    # "video_call", "voice_call", "dating_platform", "external_community",
    # "external_website", "unknown_external", "none"
    platform: Optional[str] = None
    # "instagram", "whatsapp", "signal", "viber", "discord", "snapchat", "tiktok",
    # "twitter", "x", "reddit", "vk", "telegram", "wechat", "line", "email", "phone",
    # "facetime", "skype", "steam", "other", None
    intent: str = "none"
    # "request_contact", "request_username", "request_phone", "request_email",
    # "request_profile", "request_link", "send_own_contact", "request_call",
    # "request_video_call", "request_move_conversation", "request_find_profile",
    # "request_add_friend", "request_follow", "request_continue_elsewhere",
    # "share_external_link", "suggest_external_platform", "inquiry_usage", "unknown", "none"
    confidence: float = 0.0
    deflection_required: bool = False
    suggested_strategy: str = "SOFT_PREFERENCE"
    # "SOFT_PREFERENCE", "PLAYFUL_DEFLECTION", "LIGHT_TEASING", "TOPIC_CONTINUATION",
    # "CASUAL_BOUNDARY", "WARM_BOUNDARY", "FIRM_BOUNDARY"
    request_count: int = 1
    trigger: Optional[str] = None
    extracted_contact: Optional[str] = None
    reason: str = ""


class ExternalChannelPolicyService:
    """
    Core service enforcing the Telegram-Only Communication Policy across
    semantic classification, multi-level escalation, system prompt guidance,
    and application-level firewall.
    """

    # Default application security boundary
    ALLOW_EXTERNAL_CONTACT: bool = False
    ALLOW_PERSONAL_EXTERNAL_URLS: bool = False
    ALLOW_SOCIAL_URLS: bool = False
    ALLOW_PERSONAL_SOCIAL_HANDLES: bool = False
    ALLOW_PERSONAL_PHONE: bool = False
    ALLOW_PERSONAL_EMAIL: bool = False
    EXTERNAL_IDENTITY_CONFIRMATION: bool = False

    # -------------------------------------------------------------------------
    # 1. FALSE POSITIVE FILTERS (Conversational mentions that are NOT requests)
    # -------------------------------------------------------------------------
    FALSE_POSITIVE_PATTERNS = [
        # Stories, facts, movies, friends, general mentions
        r"(?i)\b(смотрел[аи]?\s+(фильм|кино|сериал|видео|тизер))\b",
        r"(?i)\b(мой\s+друг|моя\s+подруга|знакомый|коллега|брат|сестра)\s+(пользуется|сидит\s+в|пишет\s+в|есть\s+в)\b",
        r"(?i)\b(у\s+меня\s+(сломался|глючит|не\s+работает|завис|удалился|слетел)\s+(инст[а-я]*|ватсап|дискорд|вайбер|телег[а-я]*))\b",
        r"(?i)\b(в\s+(дискорде|инсте|ватсапе|вайбере|твиттере|х|тт|тикток[еа]?)\s+(был[оа]?\s+)?(смешной\s+)?(мем|пост|рилс|видос|новость|прикол))\b",
        r"(?i)\b(видела?\s+(пост|мем|рилс|сторис|видео|новость))\b",
        r"(?i)\b(что\s+такое\s+(дискорд|discord|ватсап|whatsapp|сигнал|signal|вайбер|viber|твич|twitch))\b",
        r"(?i)\b(у\s+меня\s+нет\s+(ватсапа|whatsapp|инст[а-я]*|дискорда|сигнала|вайбера))\b",
        r"(?i)\b(я\s+играю\s+в\s+(кс|cs|доту|dota|пабг|pubg|майнкрафт|minecraft|roblox|роблокс|игры|танки))\b",
        r"(?i)\b(я\s+обычно\s+общаюсь\s+с\s+друзьями\s+в\s+(дискорде|discord|ватсапе))\b",
        r"(?i)\b(у\s+нас\s+(в\s+городе|в\s+стране|в\s+регионе)\s+(заблокировали|забанили|глушат|не\s+работает))\b",
        r"(?i)\b(просто\s+спросил[а]?\s+для\s+справки|просто\s+интересно\s+было)\b",
    ]

    # -------------------------------------------------------------------------
    # 2. TELEGRAM-SPECIFIC CHECKS (Telegram itself is allowed!)
    # -------------------------------------------------------------------------
    TELEGRAM_PATTERNS = [
        r"(?i)\b(какой\s+(у\s+тебя\s+)?(тг|телег[а-я]*|telegram)|скинь\s+(свой\s+)?(тг|телег[а-я]*|telegram)|дай\s+(свой\s+)?(тг|телег[а-я]*)|твой\s+(тг|телег[а-я]*|telegram))\b",
        r"(?i)\b(давай\s+в\s+(тг|телегу|телеграм|telegram)|го\s+в\s+(тг|телегу|телеграм)|продолжим\s+в\s+(тг|телеге|телеграме))\b",
        r"(?i)\b(я\s+в\s+(тг|телеге|телеграме)|пиши\s+в\s+(тг|телегу|телеграм))\b",
        r"(?i)\b(який\s+(у\s+тебе\s+)?(тг|телеграм)|скинь\s+(свій\s+)?(тг|телеграм)|дай\s+(свій\s+)?(тг|телеграм)|твій\s+(тг|телеграм))\b",
    ]

    # -------------------------------------------------------------------------
    # 3. DIRECT & INDIRECT EXTERNAL CHANNEL DETECTION PATTERNS
    # -------------------------------------------------------------------------

    # Explicit direct requests per platform
    PLATFORM_EXPLICIT_PATTERNS: List[Tuple[str, str, str, str, float]] = [
        # Instagram (RU, UK, EN, translit, slang)
        (
            r"(?i)\b(скинь|кинь|дай|напиши|покажи|какой|можно|где|пойд[её]м\s+в|давай\s+в|перейд[её]м\s+в|пішли\s+в|хочу|го\s+в|гоу\s+в)\s+.*?\b(инст[аеуыоі]?|инстаграм|инстаграмм|instagram|insta|ig|інст[ауіе]?|інстаграм)\b",
            "external_social", "instagram", "request_contact", 0.98
        ),
        (
            r"(?i)\b(инст[аеуыоі]?|инстаграм|инстаграмм|instagram|insta|ig|інст[ауіе]?|інстаграм)\s+(дай|скинь|кинь|напиши|покажи|можно)\b",
            "external_social", "instagram", "request_contact", 0.98
        ),
        (
            r"(?i)\b(дай\s+инст|дай\s+інст|скинь\s+инст|скинь\s+інст|какой\s+инст|який\s+інст|го\s+в\s+инст|го\s+в\s+инсту|давай\s+в\s+инст[у]?|кидай\s+инст|скинь\s+insta\s+pls)\b",
            "external_social", "instagram", "request_contact", 0.98
        ),
        (
            r"(?i)\b(ссылк[уа]\s+на\s+(инст|профиль)|профиль\s+в\s+инсте|твой\s+(инст|instagram|ig)|твій\s+інст|ник\s+в\s+инст[еа]?|нік\s+в\s+інст[іа]?)\b",
            "external_social", "instagram", "request_profile", 0.95
        ),
        # Inquiring presence in Instagram ("у тебя есть инста?")
        (
            r"(?i)\b(у\s+тебя\s+(вообще\s+)?есть\s+инст[а-яa-z]*|ты\s+есть\s+в\s+инст[а-яa-z]*|сидишь\s+в\s+инст[а-яa-z]*|ведешь\s+инст[а-яa-z]*|чи\s+є\s+(в\s+тебе\s+)?інст[а-яa-z]*|ти\s+є\s+в\s+інст[а-яa-z]*|ты\s+пользуешься\s+инстаграм[а-яa-z]*|ти\s+користуєшся\s+інстаграм[а-яa-z]*)\b",
            "external_social", "instagram", "inquiry_usage", 0.92
        ),

        # WhatsApp (RU, UK, translit)
        (
            r"(?i)\b(скинь|кинь|дай|напиши|покажи|какой|можно|перейд[её]м\s+в|давай\s+в|го\s+в|пішли\s+в)\s+.*?\b(ватсап|вацап|whatsapp|вотсап|вотсапп|wapp|wa)\b",
            "external_messenger", "whatsapp", "request_contact", 0.98
        ),
        (
            r"(?i)\b(ватсап|вацап|whatsapp|вотсап|wapp|wa)\s+(дай|скинь|кинь|напиши|можно)\b",
            "external_messenger", "whatsapp", "request_contact", 0.98
        ),
        (
            r"(?i)\b(есть\s+ватсап|є\s+ватсап|есть\s+whatsapp|сидишь\s+в\s+ватсап|пиши\s+мне\s+в\s+ватсап|давай\s+в\s+whatsapp)\b",
            "external_messenger", "whatsapp", "request_move_conversation", 0.95
        ),

        # Signal
        (
            r"(?i)\b(скинь|кинь|дай|напиши|перейд[её]м\s+в|давай\s+в|го\s+в|сидишь\s+в|есть|є)\s+.*?\b(сигнал|signal)\b",
            "external_messenger", "signal", "request_contact", 0.95
        ),

        # Viber (RU + UK)
        (
            r"(?i)\b(скинь|кинь|дай|напиши|перейд[её]м\s+в|давай\s+в|го\s+в|сидишь\s+в|есть|є|давай\s+у)\s+.*?\b(вайбер|viber)\b",
            "external_messenger", "viber", "request_contact", 0.95
        ),

        # Discord
        (
            r"(?i)\b(скинь|кинь|дай|напиши|перейд[её]м\s+в|давай\s+в|го\s+в|пошли\s+в|есть|є|сидишь\s+в)\s+.*?\b(дискорд|discord|дс)\b",
            "external_messenger", "discord", "request_contact", 0.95
        ),
        (
            r"(?i)\b(дай\s+дс|скинь\s+дс|го\s+в\s+дс|давай\s+в\s+дс|твой\s+дс|твій\s+дс)\b",
            "external_messenger", "discord", "request_contact", 0.95
        ),

        # Snapchat
        (
            r"(?i)\b(скинь|кинь|дай|напиши|перейд[её]м\s+в|давай\s+в|го\s+в|сидишь\s+в|есть|є)\s+.*?\b(снап|snap|snapchat|снапчат)\b",
            "external_social", "snapchat", "request_contact", 0.95
        ),

        # TikTok (RU + UK)
        (
            r"(?i)\b(скинь|кинь|дай|напиши|покажи|твой|твій|ссылк[ау]|профиль)\s+.*?\b(тикток|тиктока|tiktok|тт)\b",
            "external_social", "tiktok", "request_contact", 0.95
        ),

        # VK / Facebook / Twitter / X / Reddit
        (
            r"(?i)\b(скинь|кинь|дай|напиши|твой|твій|профиль|страницу|сторінку)\s+.*?\b(вк|vk|вконтакте|facebook|фейсбук|фб|fb|твиттер|twitter|реддит|reddit)\b",
            "external_social", "vk", "request_contact", 0.95
        ),

        # Phone numbers & SMS
        (
            r"(?i)\b(скинь\s+(свой\s+)?номер|дай\s+(свой\s+)?номер|напиши\s+(свой\s+)?номер|можно\s+(твой\s+)?номер|твой\s+номер\s+телефона)\b",
            "phone", "phone", "request_phone", 0.98
        ),
        (
            r"(?i)\b(скинь\s+(свій\s+)?номер|дай\s+(свій\s+)?номер|напиши\s+(свій\s+)?номер|можна\s+(твій\s+)?номер|твій\s+номер\s+телефону)\b",
            "phone", "phone", "request_phone", 0.98
        ),
        (
            r"(?i)\b(скинь\s+телефон|дай\s+телефон|можно\s+твой\s+мобильный|напиши\s+мне\s+по\s+номеру|напиши\s+смс|скинь\s+цифры)\b",
            "phone", "phone", "request_phone", 0.95
        ),

        # Voice & video calls
        (
            r"(?i)\b(давай\s+созвонимся|можно\s+позвонить|позвони\s+мне|позвоню\s+тебе|можно\s+тебе\s+набрать|наберу\s+тебя|хочу\s+позвонить)\b",
            "voice_call", "phone", "request_call", 0.95
        ),
        (
            r"(?i)\b(давай\s+зізвонимось|можна\s+подзвонити|подзвони\s+мені|подзвоню\s+тобі|можна\s+набрати|хочу\s+подзвонити)\b",
            "voice_call", "phone", "request_call", 0.95
        ),
        (
            r"(?i)\b(давай\s+видеозвонок|давай\s+по\s+видео|видеосвязь|видеосвязи|видео\s+звонок|відеодзвінок|созвонимся\s+по\s+видео)\b",
            "video_call", "facetime", "request_video_call", 0.98
        ),
        (
            r"(?i)\b(пошли\s+в\s+facetime|созвонимся\s+в\s+whatsapp|созвонимся\s+в\s+дискорд|го\s+в\s+скайп|давай\s+в\s+скайп)\b",
            "video_call", "facetime", "request_video_call", 0.98
        ),

        # Email
        (
            r"(?i)\b(скинь\s+(свой\s+)?(почту|email|e-mail|мыло)|дай\s+(свою\s+)?(почту|email|e-mail|мыло)|куда\s+тебе\s+написать\s+по\s+почте)\b",
            "email", "email", "request_email", 0.98
        ),
        (
            r"(?i)\b(скинь\s+(свою\s+)?(пошту|email)|дай\s+(свою\s+)?(пошту|email)|напиши\s+на\s+email)\b",
            "email", "email", "request_email", 0.98
        ),

        # External gaming / communities (Steam, etc. to chat)
        (
            r"(?i)\b(добавлю\s+тебя\s+в\s+steam|добавляйся\s+в\s+steam|пошли\s+в\s+стим|там\s+будем\s+общаться|го\s+в\s+стим)\b",
            "external_community", "steam", "request_move_conversation", 0.90
        ),
    ]

    # Indirect and implied requests (no specific platform name)
    INDIRECT_REQUEST_PATTERNS = [
        r"(?i)\b(а\s+где\s+ещ[её]\s+можно\s+(с\s+тобой\s+)?пообщаться|где\s+ещ[её]\s+ты\s+сидишь|где\s+ещ[её]\s+бываешь)\b",
        r"(?i)\b(а\s+де\s+ще\s+можна\s+(з\s+тобою\s+)?поспілкуватися|де\s+ще\s+ти\s+сидиш|де\s+ще\s+буваєш)\b",
        r"(?i)\b(есть\s+где-нибудь\s+ещ[её]|ты\s+только\s+здесь\s+сидишь|ти\s+тільки\s+тут\s+сидиш|ты\s+только\s+тут\s+общаешься)\b",
        r"(?i)\b(где\s+тебя\s+(ещ[её]\s+)?(можно\s+)?найти|де\s+тебе\s+(ще\s+)?(можна\s+)?знайти|как\s+тебя\s+найти|як\s+тебе\s+знайти)\b",
        r"(?i)\b(можно\s+другой\s+контакт|дай\s+что-нибудь\s+кроме\s+этого|можна\s+інший\s+контакт|дай\s+інший\s+спосіб)\b",
        r"(?i)\b(тут\s+неудобно|тут\s+незручно|давай\s+в\s+другом\s+месте|давай\s+в\s+іншому\s+місці|перейд[её]м\s+куда-нибудь|перейдемо\s+кудись)\b",
        r"(?i)\b(есть\s+другой\s+способ\s+связаться|є\s+інший\s+спосіб\s+зв['’]язатися|можно\s+тебя\s+найти\s+вне\s+этого\s+приложения)\b",
        r"(?i)\b(дай\s+точку\s+связи|куда\s+тебе\s+написать(\s+ещ[её])?|где\s+тебя\s+добавить|скинь\s+контактик|скинь\s+контакт)\b",
        r"(?i)\b(где\s+твои\s+сторис|где\s+твои\s+фотки|а\s+профиль\s+посмотреть\s+можно|где\s+ты\s+выкладываешься)\b",
    ]

    # Obfuscated contact requests ("кинь свой @", "дай ник")
    OBFUSCATED_PATTERNS = [
        r"(?i)\b(кинь|дай|скинь|напиши)\s+(свой\s+)?(@|собаку)",
        r"(?i)\b(дай|скинь|напиши|кинь)\s+(свой\s+)?ник\b",
        r"(?i)\b(дай|скинь|напиши|кинь)\s+(свій\s+)?нік\b",
        r"(?i)\b(где\s+у\s+тебя\s+профиль|можно\s+ссылку\s+на\s+себя|де\s+в\s+тебе\s+профіль)\b",
        r"(?i)\b(скинь\s+ник,\s*я\s+тебя\s+добавлю|дай\s+ник,\s*добавлю)\b",
    ]

    # User offering / sending their own contact ("вот мой инст @xxx", "мой номер...")
    USER_PROVIDES_CONTACT_PATTERNS = [
        (r"(?i)\b(вот\s+мой\s+(инст[а-я]*|instagram|вайбер|ватсап|дискорд|номер|тг|контакт)|ось\s+мій\s+(інст[а-я]*|номер|вайбер|ватсап))\b", "send_own_contact"),
        (r"(?i)\b(напиши\s+мне\s+(в\s+инст[еа]?|в\s+ватсап|в\s+вайбер|в\s+дискорд|на\s+номер|туда))\b", "request_move_conversation"),
        (r"(?i)\b(добавь\s+меня|зафолловь|подпишись\s+на\s+меня|підпишись|додай\s+мене)\b", "request_follow"),
        (r"(?i)\b(я\s+тебе\s+скинул\s+ссылку|переходи\s+сюда|давай\s+продолжим\s+там|давай\s+там\s+продолжим)\b", "request_continue_elsewhere"),
        (r"(?i)\b(я\s+(кажется\s+)?нашел\s+тебя\s+в\s+(инсте|инстаграме|соцсетях)|это\s+твой\s+(инст|профиль|аккаунт))\b", "request_find_profile"),
    ]

    # Obfuscated phone patterns (words / spaced digits: e.g. "восемь девять...", "+44...", "zero seven...")
    OBFUSCATED_PHONE_REGEX = re.compile(
        r"(?i)(?:\b(?:плюс|plus|\+)?\s*(?:семь|восемь|ноль|one|seven|eight|zero|\d)[\s\-\.\(\)]{0,3}){7,15}\b"
    )

    # Obfuscated email patterns ("name [at] gmail [dot] com", "name собака mail")
    OBFUSCATED_EMAIL_REGEX = re.compile(
        r"(?i)\b[a-zA-Z0-9_\.\-]+(?:\s*\[at\]\s*|\s*\(at\)\s*|\s*собака\s*|\s*@\s*)[a-zA-Z0-9_\.\-]+(?:\s*\[dot\]\s*|\s*\(dot\)\s*|\s*точка\s*|\s*\.\s*)[a-zA-Z]{2,6}\b"
    )

    # External URLs (Instagram, WhatsApp, Discord, etc.)
    EXTERNAL_URL_REGEX = re.compile(
        r"(?i)\bhttps?://(?:www\.)?(?:instagram\.com|instagr\.am|wa\.me|api\.whatsapp\.com|discord\.(?:gg|com)|tiktok\.com|facebook\.com|fb\.me|t\.me/(?!joinchat)[a-zA-Z0-9_+]{5,}|vk\.com|twitter\.com|x\.com)/[^\s]+"
    )

    # -------------------------------------------------------------------------
    # 4. CLASSIFICATION METHOD
    # -------------------------------------------------------------------------
    @classmethod
    def classify_intent(
        cls,
        text: str,
        deflection_count: int = 0,
        recent_chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> ChannelSwitchIntent:
        """
        Classifies whether an incoming message is a channel-switching attempt or
        external contact exchange. Understands intent rather than just keywords,
        safeguarding false positives and supporting multi-turn reference resolution.
        """
        if not text or not str(text).strip():
            return ChannelSwitchIntent(channel_switch=False, confidence=0.0)

        raw_text = str(text).strip()
        cleaned = raw_text.lower()

        # Step 0: Check FALSE POSITIVES (conversational mentions that are NOT requests)
        for fp_pat in cls.FALSE_POSITIVE_PATTERNS:
            if re.search(fp_pat, cleaned):
                # Verify if this is pure conversational mention and NOT combined with "скинь свой"
                if not any(req_w in cleaned for req_w in ["дай", "скинь", "кинь", "перейдем", "го в", "давай в"]):
                    logger.debug("[CHANNEL_POLICY] Classified as benign conversational mention (false positive): '%s'", raw_text)
                    return ChannelSwitchIntent(
                        channel_switch=False,
                        confidence=0.85,
                        reason="Conversational mention of platform without migration request",
                    )

        # Step 1: Telegram check - Telegram itself is ALLOWED and must NOT be blocked!
        # If user asks for Telegram ("какой у тебя тг", "скинь свой тг", "давай в тг")
        is_telegram_mention = False
        for tg_pat in cls.TELEGRAM_PATTERNS:
            if re.search(tg_pat, cleaned):
                is_telegram_mention = True
                break

        if is_telegram_mention:
            logger.info("[CHANNEL_POLICY] User mentioned Telegram inside Telegram chat: '%s'", raw_text)
            return ChannelSwitchIntent(
                channel_switch=False,
                channel_type="telegram",
                platform="telegram",
                intent="suggest_external_platform",
                confidence=0.95,
                deflection_required=False,
                reason="Telegram communication itself is permitted inside Telegram",
            )

        # Step 2: Check User Providing Own Contact ("вот мой инст @...", "добавь меня")
        for u_pat, u_intent in cls.USER_PROVIDES_CONTACT_PATTERNS:
            m = re.search(u_pat, cleaned)
            if m:
                # Extract potential @handle or phone if present
                handle_m = re.search(r"@([a-zA-Z0-9_\.]{3,32})", raw_text)
                extracted = f"@{handle_m.group(1)}" if handle_m else None
                req_count = deflection_count + 1
                strategy = cls._determine_strategy(req_count, cleaned)
                return ChannelSwitchIntent(
                    channel_switch=True,
                    channel_type="external_social",
                    platform="instagram" if "инст" in cleaned else "other",
                    intent=u_intent,
                    confidence=0.96,
                    deflection_required=True,
                    suggested_strategy=strategy,
                    request_count=req_count,
                    trigger=m.group(0),
                    extracted_contact=extracted,
                    reason=f"User offered external contact / interaction: '{m.group(0)}'",
                )

        # Step 3: Check External URLs
        url_match = cls.EXTERNAL_URL_REGEX.search(raw_text)
        if url_match:
            req_count = deflection_count + 1
            strategy = cls._determine_strategy(req_count, cleaned)
            return ChannelSwitchIntent(
                channel_switch=True,
                channel_type="external_website",
                platform="other",
                intent="share_external_link",
                confidence=0.99,
                deflection_required=True,
                suggested_strategy=strategy,
                request_count=req_count,
                trigger=url_match.group(0),
                reason=f"External link detected: '{url_match.group(0)}'",
            )

        # Step 4: Check Explicit Platform Requests
        for pattern, ch_type, platform, intent_name, conf in cls.PLATFORM_EXPLICIT_PATTERNS:
            m = re.search(pattern, cleaned)
            if m:
                req_count = deflection_count + 1
                strategy = cls._determine_strategy(req_count, cleaned)
                return ChannelSwitchIntent(
                    channel_switch=True,
                    channel_type=ch_type,
                    platform=platform,
                    intent=intent_name,
                    confidence=conf,
                    deflection_required=True,
                    suggested_strategy=strategy,
                    request_count=req_count,
                    trigger=m.group(0),
                    reason=f"Explicit {platform} request detected: '{m.group(0)}'",
                )

        # Step 5: Check Obfuscated Contact Requests ("кинь свой @", "дай ник")
        for ob_pat in cls.OBFUSCATED_PATTERNS:
            m = re.search(ob_pat, cleaned)
            if m:
                req_count = deflection_count + 1
                strategy = cls._determine_strategy(req_count, cleaned)
                return ChannelSwitchIntent(
                    channel_switch=True,
                    channel_type="unknown_external",
                    platform="other",
                    intent="request_username",
                    confidence=0.92,
                    deflection_required=True,
                    suggested_strategy=strategy,
                    request_count=req_count,
                    trigger=m.group(0),
                    reason=f"Obfuscated username / handle request: '{m.group(0)}'",
                )

        # Step 6: Check Indirect Migration Requests ("где еще можно пообщаться", "тут неудобно")
        for ind_pat in cls.INDIRECT_REQUEST_PATTERNS:
            m = re.search(ind_pat, cleaned)
            if m:
                req_count = deflection_count + 1
                strategy = cls._determine_strategy(req_count, cleaned)
                return ChannelSwitchIntent(
                    channel_switch=True,
                    channel_type="unknown_external",
                    platform="other",
                    intent="request_move_conversation",
                    confidence=0.88,
                    deflection_required=True,
                    suggested_strategy=strategy,
                    request_count=req_count,
                    trigger=m.group(0),
                    reason=f"Indirect channel-switch inquiry: '{m.group(0)}'",
                )

        # Step 7: Check Contextual Reference Resolution ("у тебя есть инста?" -> "ну туда тогда" / "го туда")
        if recent_chat_history and any(w in cleaned for w in ["туда", "там", "туди", "там продовжимо"]):
            for turn in reversed(recent_chat_history[-5:]):
                prev_text = (turn.get("text") or turn.get("content") or "").lower()
                for pat, ch_type, plat, _, _ in cls.PLATFORM_EXPLICIT_PATTERNS:
                    if re.search(pat, prev_text):
                        req_count = deflection_count + 1
                        strategy = cls._determine_strategy(req_count, cleaned)
                        return ChannelSwitchIntent(
                            channel_switch=True,
                            channel_type=ch_type,
                            platform=plat,
                            intent="request_continue_elsewhere",
                            confidence=0.88,
                            deflection_required=True,
                            suggested_strategy=strategy,
                            request_count=req_count,
                            trigger=raw_text,
                            reason=f"Contextual reference to prior platform ({plat}): '{raw_text}'",
                        )
                # Keyword fallback in previous message
                for kw, ch_type, plat in [
                    ("инст", "external_social", "instagram"),
                    ("інст", "external_social", "instagram"),
                    ("ватсап", "external_messenger", "whatsapp"),
                    ("whatsapp", "external_messenger", "whatsapp"),
                    ("дискорд", "external_messenger", "discord"),
                    ("discord", "external_messenger", "discord"),
                    ("вайбер", "external_messenger", "viber"),
                    ("viber", "external_messenger", "viber"),
                ]:
                    if kw in prev_text:
                        req_count = deflection_count + 1
                        strategy = cls._determine_strategy(req_count, cleaned)
                        return ChannelSwitchIntent(
                            channel_switch=True,
                            channel_type=ch_type,
                            platform=plat,
                            intent="request_continue_elsewhere",
                            confidence=0.85,
                            deflection_required=True,
                            suggested_strategy=strategy,
                            request_count=req_count,
                            trigger=raw_text,
                            reason=f"Contextual reference to prior keyword ({kw}): '{raw_text}'",
                        )

        # Step 8: Obfuscated phone or email in incoming message
        if cls.OBFUSCATED_EMAIL_REGEX.search(cleaned):
            req_count = deflection_count + 1
            strategy = cls._determine_strategy(req_count, cleaned)
            return ChannelSwitchIntent(
                channel_switch=True,
                channel_type="email",
                platform="email",
                intent="send_own_contact",
                confidence=0.90,
                deflection_required=True,
                suggested_strategy=strategy,
                request_count=req_count,
                trigger="obfuscated_email",
                reason="Obfuscated email address detected",
            )

        return ChannelSwitchIntent(channel_switch=False, confidence=0.0)

    # -------------------------------------------------------------------------
    # 5. STRATEGY DETERMINATION & MULTI-TURN ESCALATION
    # -------------------------------------------------------------------------
    @classmethod
    def _determine_strategy(cls, count: int, cleaned_text: str) -> str:
        """
        Determines the conversational deflection strategy:
        - 1st request: SOFT_PREFERENCE / PLAYFUL_DEFLECTION
        - 2nd request: WARM_BOUNDARY
        - 3rd+ request: FIRM_BOUNDARY
        - Hostile / aggressive pressure: CALM FIRM_BOUNDARY
        """
        # Check aggressive pressure keywords (RU + UK)
        is_aggressive = any(
            w in cleaned_text
            for w in [
                "выебываешься", "ломаешься", "быстро дай", "че ты", "чого ти",
                "заебала", "заебал", "бесишь", "дай уже", "че сложно",
                "шо складно", "не выпендривайся"
            ]
        )
        if is_aggressive:
            return "FIRM_BOUNDARY"

        if count <= 1:
            return "SOFT_PREFERENCE"
        elif count == 2:
            return "WARM_BOUNDARY"
        else:
            return "FIRM_BOUNDARY"

    # -------------------------------------------------------------------------
    # 6. APPLICATION-LEVEL OUTGOING FIREWALL VALIDATOR
    # -------------------------------------------------------------------------
    @classmethod
    def validate_outgoing(
        cls,
        text: str,
        allowed_bot_username: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Deterministic application-level firewall (Section 66 & 112).
        Validates that candidate reply contains ZERO external contacts:
        1. No external URLs (instagram.com, wa.me, discord.gg, unauthorized t.me/...)
        2. No social handles (@username other than allowed bot)
        3. No phone numbers (+7..., +380..., +44..., 89...)
        4. No emails
        5. No claims to move externally ("пиши в ватсап", "го в дискорд")
        6. No claims to have followed/added/visited external profile ("я добавила тебя", "подписалась")
        7. No robotic refusals ("я бот", "политика запрещает", "по правилам мне нельзя")
        8. No fake excuses ("телефон сломался", "пароль забыла")

        Returns: (is_valid: bool, rejection_reason: Optional[str])
        """
        if not text or not str(text).strip():
            return True, None

        raw = str(text)
        low = raw.lower()
        allowed = (allowed_bot_username or "").lstrip("@").lower()

        # 1. External URLs
        if cls.EXTERNAL_URL_REGEX.search(raw):
            return False, "contains_external_url"

        if re.search(r"(?i)\b(?:https?://)?(?:www\.)?(?:instagram\.com|wa\.me|discord\.gg|tiktok\.com|facebook\.com)/[a-zA-Z0-9_\.]+", raw):
            return False, "contains_social_url"

        # 2. Email addresses
        if re.search(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b", raw):
            return False, "contains_email_address"

        # 3. Unauthorized @usernames
        all_handles = re.findall(r"(?<![a-zA-Z0-9_.])@([a-zA-Z0-9_]{3,32})", raw)
        for h in all_handles:
            if h.lower() != allowed:
                return False, f"contains_unauthorized_handle:@{h}"

        # 4. Phone numbers
        # E.g. +7 (999) 123-45-67, +380 50 123 45 67, 89261234567, etc.
        phone_match = re.search(r"(?:\+?[1-9]\d{0,2})?[\s\-\(]*\d{3}[\s\-\)]*\d{2,3}[\s\-]*\d{2}[\s\-]*\d{2}", raw)
        if phone_match and len(re.sub(r"\D", "", phone_match.group(0))) >= 7:
            # Check if this isn't just a harmless year like 2026
            digits_only = re.sub(r"\D", "", phone_match.group(0))
            if len(digits_only) >= 9:
                return False, "contains_phone_number"

        # 5. External migration agreements or outgoing invitations
        agree_move_patterns = [
            r"(?i)\b(перейд[её]м\s+в\s+(ватсап|вацап|whatsapp|вотсап|дискорд|discord|инст[а-я]*|instagram|insta|вайбер|viber|сигнал|signal))\b",
            r"(?i)\b(пиши\s+(мне\s+)?в\s+(ватсап|вацап|whatsapp|вотсап|дискорд|discord|вайбер|viber|инст[а-я]*|instagram|insta))\b",
            r"(?i)\b(го\s+в\s+(ватсап|вацап|whatsapp|вотсап|дискорд|discord|инст[а-я]*|instagram|insta|вайбер|viber))\b",
            r"(?i)\b(давай\s+в\s+(ватсап|вацап|whatsapp|вотсап|дискорд|discord|инст[а-я]*|instagram|insta|вайбер|viber))\b",
            r"(?i)\b(давай\s+в\s+(дискорд|discord|ватсап|whatsapp|инст[а-я]*|instagram)\s+(перейд[её]м|пойдем|пойдём))\b",
            r"(?i)\b(созвонимся\s+в\s+(ватсап|whatsapp|дискорд|discord|facetime|скайп|skype))\b",
        ]
        for pat in agree_move_patterns:
            if re.search(pat, low):
                return False, f"claims_external_move:{pat}"

        # 6. Claims of external action (anti-hallucination)
        claims_action = [
            r"(?i)\b(я\s+(уже\s+)?(подписалась|добавила|написала|зафолловила|чекаю)\s+(на\s+тебя\s+в|в\s+инсте|в\s+дискорде|в\s+ватсапе))\b",
            r"(?i)\b(я\s+(уже\s+)?посмотрела\s+твой\s+(инст|профиль|аккаунт))\b",
            r"(?i)\b(я\s+(уже\s+)?нашла\s+твой\s+(инст|профиль|аккаунт))\b",
        ]
        for pat in claims_action:
            if re.search(pat, low):
                return False, f"claims_external_action:{pat}"

        # 7. Robotic / policy excuses
        robotic_patterns = [
            r"(?i)\b(я\s+(являюсь\s+)?ботом|как\s+бот\s+я|мой\s+разработчик|политика\s+(конфиденциальности\s+)?запрещает)\b",
            r"(?i)\b(согласно\s+правилам\s+я\s+не|система\s+не\s+позволяет|мне\s+запрещено\s+давать)\b",
            r"(?i)\b(я\s+не\s+могу\s+общаться\s+вне\s+telegram|telegram-only)\b",
        ]
        for pat in robotic_patterns:
            if re.search(pat, low):
                return False, f"robotic_policy_leak:{pat}"

        # 8. Fake manufactured excuses
        fake_excuses = [
            r"(?i)\b(у\s+меня\s+(сломался|разбился)\s+телефон)\b",
            r"(?i)\b(я\s+забыла\s+пароль\s+от\s+(инст[а-я]*|аккаунта))\b",
            r"(?i)\b(меня\s+заблокировали\s+в\s+инсте)\b",
        ]
        for pat in fake_excuses:
            if re.search(pat, low):
                return False, f"fake_excuse_detected:{pat}"

        return True, None

    # -------------------------------------------------------------------------
    # 7. NATURAL DEFLECTION PATTERN LIBRARY & CONTEXTUAL HEALER
    # -------------------------------------------------------------------------
    DEFLECTION_POOL = {
        "SOFT_PREFERENCE": {
            "ru": [
                "давай здесь пообщаемся, мне тут намного комфортнее 😌",
                "ахах, куда ты меня так торопишься утащить из телеги) давай пока здесь пообщаемся 😌",
                "давай лучше тут в телеге пообщаемся, мне здесь спокойнее)",
                "не спеши меня из телеги уводить 😄 давай пока здесь пообщаемся",
                "мне как-то комфортнее здесь общаться, тут же нормально всё)",
            ],
            "uk": [
                "давай тут поспілкуємося, мені так набагато комфортніше 😌",
                "ахах, куди ти мене так поспішаєш потягнути з телеграма) давай поки тут поспілкуємось 😌",
                "давай краще тут у телеграмі поспілкуємося, мені тут спокійніше)",
                "не поспішай мене з телеграма вести 😄 давай поки тут поспілкуємося",
                "мені якось комфортніше тут спілкуватися, тут же все добре)",
            ],
        },
        "WARM_BOUNDARY": {
            "ru": [
                "я же сказала, давай пока здесь пообщаемся) не хочу расползаться по приложениям 😄",
                "ну вот опять ты про другие соцсети) давай сначала тут в телеге нормально познакомимся и пообщаемся",
                "давай без этого пока, мне правда комфортнее в телеге оставаться и общаться)",
                "не торопи события, мы и тут вроде неплохо общаемся 😉",
            ],
            "uk": [
                "я ж казала, давай поки тут поспілкуємося) не хочу розпорошуватися по додатках 😄",
                "ну от знову ти про інші соцмережі) давай спочатку тут нормально познайомимось і поспілкуємося",
                "давай без цього поки, мені правда комфортніше в телеграмі залишатися і спілкуватися)",
                "не квап події, ми й тут наче непогано спілкуємось 😉",
            ],
        },
        "FIRM_BOUNDARY": {
            "ru": [
                "слушай, я за пределы этого общения уходить не хочу. давай общаться здесь, если хочешь)",
                "не, правда, давай без этого. мне комфортно оставить наше общение именно тут в телеге.",
                "я спокойно отношусь к тебе, просто личными контактами не делюсь. давай тут продолжим общаться.",
            ],
            "uk": [
                "слухай, я за межі цього спілкування виходити не хочу. давай спілкуватися тут, якщо хочеш)",
                "ні, правда, давай без цього. мені комфортно залишити наше спілкування саме тут у телеграмі.",
                "я добре ставлюся до тебе, просто особистими контактами не ділюся. давай тут продовжимо спілкуватися.",
            ],
        },
    }

    @classmethod
    def sanitize_and_heal(
        cls,
        raw_text: str,
        current_topic: Optional[str] = None,
        language: str = "ru",
        strategy: str = "SOFT_PREFERENCE",
        allowed_bot_username: Optional[str] = None,
    ) -> str:
        """
        Heals an unsafe or leaking text into a socially natural, human-like deflection
        without leaving broken sentences like 'вот мой ' or robotic boilerplate.
        """
        allowed = (allowed_bot_username or "").lstrip("@").lower()

        # 1. Clean out URLs
        cleaned = cls.EXTERNAL_URL_REGEX.sub("", raw_text)
        cleaned = re.sub(
            r"(?i)\b(?:https?://)?(?:www\.)?(?:instagram\.com|wa\.me|discord\.gg|tiktok\.com|facebook\.com)/[a-zA-Z0-9_\.]+",
            "",
            cleaned,
        )

        # 2. Clean out unauthorized handles
        def _strip_handle(m):
            h = m.group(1)
            return m.group(0) if h.lower() == allowed else ""
        cleaned = re.sub(r"@([a-zA-Z0-9_]{3,32})", _strip_handle, cleaned)

        # 3. Clean out phone numbers
        cleaned = re.sub(r"(?:\+?[1-9]\d{0,2})?[\s\-\(]*\d{3}[\s\-\)]*\d{2,3}[\s\-]*\d{2}[\s\-]*\d{2}", "", cleaned)

        # 4. Clean whitespace
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        # If the text became too broken, empty, or still fails validation, substitute with natural deflection
        is_clean, _ = cls.validate_outgoing(cleaned, allowed_bot_username=allowed)
        if not is_clean or len(cleaned) < 10 or cleaned.lower().startswith(("вот мой", "ось мій", "пиши в", "держи")):
            lang_key = "uk" if language == "uk" else "ru"
            strat_key = strategy if strategy in cls.DEFLECTION_POOL else "SOFT_PREFERENCE"
            pool = cls.DEFLECTION_POOL.get(strat_key, {}).get(lang_key) or cls.DEFLECTION_POOL["SOFT_PREFERENCE"]["ru"]
            healed = random.choice(pool)
            if current_topic and len(current_topic) > 5:
                bridge = f" кстати, ты так и не дорассказал про {current_topic}" if lang_key == "ru" else f" до речі, ти так і не довів до кінця про {current_topic}"
                healed += bridge
            return healed

        return cleaned

    # -------------------------------------------------------------------------
    # 8. SYSTEM PROMPT BUILDER
    # -------------------------------------------------------------------------
    @classmethod
    def build_system_prompt_instruction(
        cls,
        intent: ChannelSwitchIntent,
        language: str = "ru",
        current_topic: Optional[str] = None,
    ) -> str:
        """
        Builds the detailed behavioral prompt injection enforcing the Telegram-Only
        Communication Policy according to the Master Task requirements.
        """
        count = intent.request_count
        strat = intent.suggested_strategy
        plat = intent.platform or "внешняя платформа"
        is_uk = language == "uk"

        instruction = (
            f"\n[ПОЛИТИКА ОБЩЕНИЯ: СТРОГО TELEGRAM (УРОВЕНЬ {count}, СТРАТЕГИЯ: {strat})]:\n"
            f"• Входящий запрос: попытка перевести диалог в {plat} (или обмен внешними контактами).\n"
            f"• АБСОЛЮТНОЕ ПРАВИЛО: общение происходит ИСКЛЮЧИТЕЛЬНО внутри Telegram. Ты НИКОГДА не переходишь в другие соцсети, мессенджеры, звонки или сайты!\n"
            f"• ЗАПРЕТ ВЫДАЧИ КОНТАКТОВ: категорически запрещено давать, придумывать или намекать на свои ники, номера телефонов, почту или соцсети.\n"
            f"• ЗАПРЕТ ПРИНЯТИЯ ВНЕШНИХ КОНТАКТОВ: если собеседник прислал свой контакт («вот мой инст @...», номер и т.д.) — не обещай подписаться, добавить или написать туда. Отреагируй тепло, но мягко верни разговор в Telegram («скидывай лучше сюда фотки/новости, тут посмотрю»).\n"
            f"• КАТЕГОРИЧЕСКИЙ ЗАПРЕТ РОБОТОПОДОБНОСТИ И СИСТЕМНЫХ ПРАВИЛ: никаких «я бот», «система не позволяет», «политика запрещает», «мне нельзя»!\n"
            f"• КАТЕГОРИЧЕСКИЙ ЗАПРЕТ ЛЖИВЫХ ОТМАЗОК: никаких «пароль забыла», «телефон сломался», «забанили»!\n"
            f"• НЕ ДАВАЙ АЛЬТЕРНАТИВНЫХ ВНЕШНИХ ПЛАТФОРМ: нельзя предлагать WhatsApp взамен Инсты. Единственное место — здесь, в Telegram.\n"
            f"• ПОВЕДЕНИЕ В ДИАЛОГЕ:\n"
            f"  1. Мягко отбей запрос (с юмором, лёгким флиртом или спокойным комфортом: «давай пока тут) мне так спокойнее»).\n"
            f"  2. СРАЗУ же продолжи текущую тему разговора или задай естественный вопрос по контексту, чтобы диалог не прерывался.\n"
            f"  3. При настойчивом давлении (уровень {count} >= 3) — сохраняй спокойную личную границу без злости и без чтения морали."
        )
        return instruction
