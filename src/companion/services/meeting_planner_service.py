"""
Meeting Planner Service — Strict Meeting Negotiation Lock, Cinema / Quest Preference,
Synchronized City-Scoped Catalog, Geographic Persona Lore Bridge, and Owner Handoff State Machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from companion.services.persona_lore_service import GeoConnectionResolution, PersonaLoreService

logger = logging.getLogger(__name__)

# ==============================================================================
# 1. MEETING CONTROL STATES & FORMAL STATE MACHINE (Sections 12 & 13)
# ==============================================================================

MEETING_CONTROL_STATES = ("AUTO", "HANDOFF_PENDING", "OWNER", "RETURNED_TO_AUTO")

# Section 12 explicit states
STATE_NO_MEETING = "NO_MEETING"
STATE_MEETING_PROPOSED = "MEETING_PROPOSED"
STATE_NON_PREFERRED_ACTIVITY = "NON_PREFERRED_ACTIVITY"
STATE_SOFT_REDIRECT = "SOFT_REDIRECT"
STATE_CINEMA_OR_QUEST_PROPOSED = "CINEMA_OR_QUEST_PROPOSED"
STATE_USER_SELECTS = "USER_SELECTS"
STATE_ACTIVITY_CONFIRMED = "ACTIVITY_CONFIRMED"
STATE_DATE_TIME_NEGOTIATION = "DATE_TIME_NEGOTIATION"
STATE_VENUE_SELECTION = "VENUE_SELECTION"
STATE_BOOKING_HANDOFF = "BOOKING_HANDOFF"

# Backward-compatible funnel stages
FUNNEL_STAGES = (
    "IDLE",
    "INTEREST_SHOWN",
    "PREFERENCE_DISCOVERY",
    "OPTIONS_DISCUSSED",
    "CONFIRMATION",
    "MEETING_HANDOFF_REQUIRED",
    # Aliases
    STATE_NO_MEETING,
    STATE_MEETING_PROPOSED,
    STATE_NON_PREFERRED_ACTIVITY,
    STATE_SOFT_REDIRECT,
    STATE_CINEMA_OR_QUEST_PROPOSED,
    STATE_USER_SELECTS,
    STATE_ACTIVITY_CONFIRMED,
    STATE_DATE_TIME_NEGOTIATION,
    STATE_VENUE_SELECTION,
    STATE_BOOKING_HANDOFF,
)

# Section 1 & 3: The ONLY acceptable autonomous end states
PREFERRED_ACTIVITIES = ("cinema", "quest")

# Section 1: Non-preferred activity detection patterns
NON_PREFERRED_ACTIVITY_PATTERNS: Dict[str, List[str]] = {
    "walk": [
        r"\b(погуля(ем|ть|й|йте|емо|ти)|прогул(ка|ке|ку|ки|кою)|пройтись|прогуля(ться|емся)|пройтись\s+по\s+городу|побродить|по\s+набережной|walk|stroll)\b",
        r"\b(просто\s+погулять|хочу\s+погулять|давай\s+погуляем|давай\s+пройдемся|давай\s+пройдёмся|пішли\s+гуляти|давай\s+погуляємо)\b",
    ],
    "park": [
        r"\b(парк[еауо]?|набережн(ая|ой|ую|ій|ою)|сквер[еауо]?|алле[ею]|в\s+парк|в\s+парке|у\s+парку|park|embankment)\b",
    ],
    "coffee": [
        r"\b(кофе|кофейн[еюя]|по(пьем|пить|пьём)\s+кофе|вы(пьем|пить|пьём)\s+кофе|сходи(м|ть)\s+на\s+кофе|по\s+кофе|по\s+чашке\s+кофе|чашечк[уе]\s+кофе|кав[ауіе]|вип['’]ємо\s+кави|поп['’]ємо\s+кави|сходи(мо|ти)\s+на\s+каву|coffee|cafe)\b",
    ],
    "restaurant": [
        r"\b(ресторан[еау]?|по(ужинаем|ужинать)|по(обедаем|обедать)|поесть|перекусить|посидеть\s+где-нибудь|посидим\s+где-нибудь|посидеть\s+в\s+кафе|пиццери[ию]|суши|бургер|столовк[еу]|кафешк[еу]|повечеряти|пообідати|поїсти|restaurant|dinner|lunch|eat)\b",
    ],
    "bar": [
        r"\b(бар[еау]?|паб[еау]?|вы(пьем|пить|пьём)|пив[оа]|коктейл[ьи]|клуб[еау]?|по\s+бокалу|алкогол[ья]|тусовк[еу]|випити|вип['’]ємо\s+вина|bar|pub|drinks|club)\b",
    ],
    "home": [
        r"\b(по\s+домам|приехать\s+друг\s+к\s+другу|к\s+тебе|ко\s+мне|в\s+гости|домой|переночевать|снять\s+квартиру|на\s+квартиру|до\s+мене|до\s+тебе|в\s+гості|home|my\s+place|your\s+place)\b",
    ],
    "drive": [
        r"\b(машин[аеу]|поката(емся|ться)|прокати(ться|мся)|на\s+машине|покатушк[и]?|покататься\s+по\s+городу|поката(ємось|тися)|на\s+машині|drive|car|ride)\b",
    ],
    "mall": [
        r"\b(торгов(ый|ом)\s+центр[е]?|тц|молл[е]?|по\s+магазинам|шопинг|пошопиться|торгов(ий|ому)\s+центр[і]?|mall|shopping)\b",
    ],
    "generic": [
        r"\b(просто\s+встрет(имся|иться)|пересеч(емся|ься)|увид(имся|еться)|свидани[еи]|где-нибудь\s+пересечься|встреч[ау]|просто\s+зустрін(емось|утись)|побачит(ись|ися)|побачення|hang\s+out|meet\s+up)\b",
    ],
}

PROPOSAL_PATTERNS = [
    r"\b(давай|может|го|хочу|можем|не\s+хочешь|пойдем|пойдём|пошли|сгоняем)\b.*\b(встрет(имся|иться)|увид(имся|еться)|пересеч(емся|ься)|сходи(м|ть)|погуля(ем|ть)|посид(им|еть)|по(пить|пьем|пьём)|в\s+кино|на\s+квест|в\s+парк|в\s+кафе)\b",
    r"\b(встрет(имся|иться)|увид(имся|еться)|пересеч(емся|ься)|пойдем\s+на\s+свидание|сходи(м|ть)\s+на\s+свидание)\b",
    r"\b(давай|може|хочу)\b.*\b(зустрін(емось|утись)|побачит(ись|ися)|підемо\s+на\s+побачення|сходи(мо|ти))\b",
    r"\b(let'?s\s+meet|want\s+to\s+meet|hang\s+out|go\s+out\s+together|see\s+each\s+other)\b",
]

DECLINE_OR_UNCERTAIN_PATTERNS = [
    r"\b(не\s+хочу\s+встречаться|не\s+готов\s+встречаться|пока\s+не\s+хочу|давай\s+потом|не\s+знаю\s+пока)\b",
    r"\b(не\s+могу\s+встретиться|не\s+получится\s+встретиться|не\s+хочу\s+нікуди|не\s+можу\s+зустрітися)\b",
]

CINEMA_PREF_PATTERNS = [
    r"\b(кино|фильм|кинотеатр|сеанс|посмотреть\s+фильм|кіно|фільм|кінотеатр|cinema|movie|film)\b",
]

QUEST_PREF_PATTERNS = [
    r"\b(квест|эскейп|эскейп-рум|хоррор-квест|загадк|головоломк|перформанс|квест-кімнат|quest|escape\s+room|puzzle)\b",
]

# Section 15: Loop prevention patterns (detecting rejection of specific option)
CINEMA_REJECTION_PATTERNS = [
    r"\b(не\s+хочу\s+(в\s+)?кино|в\s+кино\s+не\s+хочу|кино\s+не\s+хочу|не\s+люблю\s+кино|в\s+кино\s+скучно|терпеть\s+не\s+могу\s+кино|только\s+не\s+кино|не\s+кино|фильм\s+не\s+хочу|не\s+люблю\s+фильмы|не\s+хочу\s+в\s+кіно)\b",
]

QUEST_REJECTION_PATTERNS = [
    r"\b(не\s+хочу\s+(на\s+)?квест|на\s+квест\s+не\s+хочу|квест\s+не\s+хочу|не\s+люблю\s+квесты|боюсь\s+квестов|только\s+не\s+квест|не\s+квест|на\s+квест\s+не\s+пойду|не\s+хочу\s+на\s+квест)\b",
]

# Section 8: User says "anything is fine"
USER_OPEN_TO_ANY_PATTERNS = [
    r"\b(мне\s+(всё|все)\s+равно|мне\s+без\s+разницы|без\s+разницы|куда\s+хочешь|выбирай\s+сама|на\s+твой\s+вкус|как\s+хочешь|на\s+твоё\s+усмотрение|на\s+твое\s+усмотрение|любой\s+вариант|мені\s+все\s+одно|мені\s+байдуже)\b",
]

# Section 9: User asks "what do you want?"
USER_ASKS_PREFERENCE_PATTERNS = [
    r"\b(что\s+ты\s+хочешь|куда\s+ты\s+хочешь|а\s+ты\s+куда\s+хочешь|а\s+тебе\s+куда|что\s+сама\s+хочешь|куда\s+сама\s+хочешь|что\s+предлагаешь|куда\s+предлагаешь|чого\s+ти\s+хочеш|куди\s+ти\s+хочеш|що\s+пропонуєш)\b",
]

# Section 4: User asks "why?"
USER_ASKS_WHY_PATTERNS = [
    r"\b(а\s+почему|почему\s+(не|именно)|чем\s+(тебе\s+)?(прогулка|парк|кофе)\s+не|почему\s+кино|почему\s+квест|а\s+чому|чому)\b",
]

# Section 10: User insists on their original format
USER_INSISTS_PATTERNS = [
    r"\b(да\s+просто\s+погуляем|давай\s+просто\s+погуляем|да\s+ладно\s+тебе\s+погуляем|я\s+хочу\s+просто\s+погулять|давай\s+вс[её]-таки\s+погуляем|давай\s+вс[её]-таки\s+кофе|вс[её]\s+же\s+погуляем|та\s+давай\s+просто\s+погуляємо)\b",
]

GENRE_KEYWORDS: Dict[str, List[str]] = {
    "horror": ["страшн", "хоррор", "ужас", "пугающ", "мистик", "триллер", "жахи", "horror", "scary", "spooky"],
    "comedy": ["весёл", "весел", "смешн", "комеди", "лёгк", "легк", "позитив", "смішн", "comedy", "funny", "fun"],
    "detective": ["детектив", "расследован", "тайна", "загадк", "шерлок", "розслідуван", "detective", "mystery"],
    "adventure": ["приключ", "экшен", "динамичн", "активн", "пригод", "екшен", "adventure", "action"],
    "psychological": ["психолог", "умн", "сложн", "атмосферн", "психологіч", "psychological", "mind"],
}

BOOKING_OR_LINK_TRIGGER_PATTERNS = [
    r"\b(скинь\s+ссылк|дай\s+ссылк|где\s+купить|где\s+забронировать|забронируй|купим\s+билет|купить\s+билет|надішли\s+посилання|скинь\s+посилання)\b",
    r"\b(бронируй|бронь|оплат|ссылка\s+на\s+бронь|билеты|квитки|тогда\s+договорились|берём\s+этот|идем\s+туда|йдемо\s+туди)\b",
    r"\b(send\s+link|booking\s+link|book\s+it|buy\s+tickets|reserve)\b",
]

WEEKDAY_MAP = {
    "понедельник": 0,
    "вторник": 1,
    "среду": 2,
    "среда": 2,
    "четверг": 3,
    "пятницу": 4,
    "пятница": 4,
    "субботу": 5,
    "суббота": 5,
    "воскресенье": 6,
    "понеділок": 0,
    "вівторок": 1,
    "середу": 2,
    "четвер": 3,
    "п'ятницю": 4,
    "суботу": 5,
    "неділю": 6,
}

URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)


@dataclass
class MeetingPlannerResolution:
    meeting_control: str = "AUTO"
    stage: str = "IDLE"  # Backward-compatible funnel stage
    meeting_state: str = STATE_NO_MEETING  # Section 12 explicit state
    meeting_city: Optional[str] = None
    user_current_city: Optional[str] = None
    persona_home_city: Optional[str] = None
    activity_type: Optional[str] = None  # Strictly "cinema" | "quest" | None
    meeting_confirmed: bool = False  # Section 13: Strictly False unless activity_type in ("cinema", "quest")
    meeting_proposed: bool = False
    proposed_activity: Optional[str] = None
    redirect_required: bool = False
    suggestions: List[str] = field(default_factory=lambda: ["cinema", "quest"])
    rejected_activities: List[str] = field(default_factory=list)
    preferred_genre: Optional[str] = None
    disliked_genres: List[str] = field(default_factory=list)
    selected_options: List[Dict[str, Any]] = field(default_factory=list)
    agreed_venue: Optional[str] = None
    agreed_item_name: Optional[str] = None
    meeting_date: Optional[str] = None
    meeting_time: Optional[str] = None
    timezone: str = "Europe/Moscow"
    handoff_required: bool = False
    handoff_reason: Optional[str] = None
    handoff_payload: Optional[Dict[str, Any]] = None
    natural_trip_bridge: bool = False
    trip_timing_phrase: Optional[str] = None
    user_declined_or_uncertain: bool = False
    user_open_to_any: bool = False
    user_asks_preference: bool = False
    user_asks_why: bool = False
    user_insists: bool = False


class MeetingPlannerService:
    """
    Manages strict cinema/quest meeting negotiation lock, city-scoped catalog sync,
    geographic persona lore bridge, and owner handoff state machine.
    """

    # ==========================================================================
    # SECTION 14: PROPOSED ACTIVITY CLASSIFICATION
    # ==========================================================================

    @classmethod
    def classify_meeting_proposal(
        cls,
        text: str,
        current_rejected: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Recognizes the proposed meeting activity before generating text or transitioning state.
        Ensures generic proposals (walk, coffee, park, etc.) trigger redirect_required=True.
        """
        cleaned = (text or "").lower()
        rejected = list(current_rejected or [])

        # Check explicit rejection of cinema / quest
        cinema_rej = any(re.search(pat, cleaned) for pat in CINEMA_REJECTION_PATTERNS)
        quest_rej = any(re.search(pat, cleaned) for pat in QUEST_REJECTION_PATTERNS)

        if cinema_rej and "cinema" not in rejected:
            rejected.append("cinema")
        if quest_rej and "quest" not in rejected:
            rejected.append("quest")

        user_declined = any(re.search(pat, cleaned) for pat in DECLINE_OR_UNCERTAIN_PATTERNS)
        open_to_any = any(re.search(pat, cleaned) for pat in USER_OPEN_TO_ANY_PATTERNS)
        asks_pref = any(re.search(pat, cleaned) for pat in USER_ASKS_PREFERENCE_PATTERNS)
        asks_why = any(re.search(pat, cleaned) for pat in USER_ASKS_WHY_PATTERNS)
        user_insists = any(re.search(pat, cleaned) for pat in USER_INSISTS_PATTERNS)

        # Check preferred activities
        wants_cinema = any(re.search(pat, cleaned) for pat in CINEMA_PREF_PATTERNS) and not cinema_rej
        wants_quest = any(re.search(pat, cleaned) for pat in QUEST_PREF_PATTERNS) and not quest_rej

        # Check non-preferred activities
        detected_non_pref: Optional[str] = None
        for act_cat, patterns in NON_PREFERRED_ACTIVITY_PATTERNS.items():
            if any(re.search(pat, cleaned) for pat in patterns):
                detected_non_pref = act_cat
                break

        is_proposal = bool(
            wants_cinema
            or wants_quest
            or detected_non_pref
            or open_to_any
            or asks_pref
            or any(re.search(pat, cleaned) for pat in PROPOSAL_PATTERNS)
        )

        preferred_act: Optional[str] = None
        proposed_act: Optional[str] = None

        if wants_quest and not wants_cinema:
            preferred_act = "quest"
            proposed_act = "quest"
        elif wants_cinema and not wants_quest:
            preferred_act = "cinema"
            proposed_act = "cinema"
        elif wants_cinema and wants_quest:
            preferred_act = "quest"  # Default preference
            proposed_act = "cinema_or_quest"
        elif detected_non_pref:
            proposed_act = detected_non_pref
        elif open_to_any:
            proposed_act = "any"
        elif is_proposal:
            proposed_act = "generic"

        # Section 1 & 14: redirect_required is True if meeting proposed and not strictly cinema or quest
        redirect_required = bool(
            is_proposal
            and not user_declined
            and preferred_act not in PREFERRED_ACTIVITIES
        )

        # Section 15: calculate suggestions without looping on rejected options
        suggestions = [a for a in ["cinema", "quest"] if a not in rejected]
        if not suggestions:
            # If user rejected both, fallback to alternating gently
            suggestions = ["cinema", "quest"]

        return {
            "meeting_proposed": is_proposal,
            "proposed_activity": proposed_act,
            "preferred_activity": preferred_act,
            "redirect_required": redirect_required,
            "suggestions": suggestions,
            "rejected_activities": rejected,
            "user_declined": user_declined,
            "user_open_to_any": open_to_any,
            "user_asks_preference": asks_pref,
            "user_asks_why": asks_why,
            "user_insists": user_insists,
            "cinema_rejected": cinema_rej,
            "quest_rejected": quest_rej,
        }

    # ==========================================================================
    # SECTION 16: CITY-SPECIFIC CATALOG SYNCHRONIZATION
    # ==========================================================================

    @classmethod
    def get_catalog_for_city(
        cls,
        persona: Any,
        meeting_city: Optional[str],
        activity_type: Optional[str] = None,
        preferred_genre: Optional[str] = None,
        disliked_genres: Optional[List[str]] = None,
        today: Optional[date] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Returns 1–3 synchronized, city-scoped options from the persona's configured catalog.
        Strictly enforces activity.city == meeting_city and filters out expired cinema screenings.
        """
        if not persona or not meeting_city:
            return []

        raw_catalog: List[Dict[str, Any]] = []
        if hasattr(persona, "get_activity_catalog") and callable(persona.get_activity_catalog):
            raw_catalog = persona.get_activity_catalog()
        elif getattr(persona, "activity_catalog", None) is not None:
            cat_attr = getattr(persona, "activity_catalog")
            if isinstance(cat_attr, dict):
                raw_catalog = cat_attr.get("activities", [])
            elif isinstance(cat_attr, list):
                raw_catalog = cat_attr

        if not isinstance(raw_catalog, list) or not raw_catalog:
            return []

        norm_target_city = PersonaLoreService.normalize_city(meeting_city)
        if not norm_target_city:
            return []

        ref_today = today or datetime.now(timezone.utc).date()
        disliked = set(disliked_genres or [])
        matched: List[Tuple[int, Dict[str, Any]]] = []

        for item in raw_catalog:
            if not isinstance(item, dict):
                continue
            item_city = PersonaLoreService.normalize_city(item.get("city"))
            if item_city != norm_target_city:
                continue

            item_type = (item.get("type") or item.get("activity_type") or "").strip().lower()
            if activity_type and item_type and item_type != activity_type:
                continue

            # Cinema synchronization: skip films explicitly marked as no longer playing or past schedule
            if item_type == "cinema":
                if item.get("is_playing") is False or item.get("available") is False:
                    continue
                schedule = item.get("schedule")
                if isinstance(schedule, list) and schedule:
                    valid_slots = []
                    for slot in schedule:
                        if isinstance(slot, dict):
                            slot_date_str = slot.get("date")
                            if slot_date_str:
                                try:
                                    slot_dt = datetime.strptime(slot_date_str[:10], "%Y-%m-%d").date()
                                    if slot_dt < ref_today:
                                        continue
                                except ValueError:
                                    pass
                            if slot.get("available", True) is False:
                                continue
                            valid_slots.append(slot)
                        elif isinstance(slot, str):
                            valid_slots.append(slot)
                    if not valid_slots:
                        continue
                    item = dict(item, schedule=valid_slots)

            # Quest genre filtering & scoring
            item_genre = (item.get("genre") or "").strip().lower()
            if item_genre and item_genre in disliked:
                continue

            score = 1
            if preferred_genre and item_genre == preferred_genre:
                score += 5
            if item_type in ("quest", "cinema"):
                score += 2

            matched.append((score, item))

        matched.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in matched[: max(1, min(limit, 3))]]

    @classmethod
    def detect_genre_preferences(cls, text: str) -> Tuple[Optional[str], List[str]]:
        """Detects preferred and disliked quest/film genres from user text."""
        cleaned = (text or "").lower()
        preferred: Optional[str] = None
        disliked: List[str] = []

        if re.search(r"не\s+(люблю|хочу|нрав\w*)\s+(страшн\w*|хоррор\w*|ужас\w*|жахи)", cleaned):
            disliked.append("horror")

        for genre, kw_list in GENRE_KEYWORDS.items():
            if genre in disliked:
                continue
            if any(kw in cleaned for kw in kw_list):
                preferred = genre
                break

        return preferred, disliked

    @classmethod
    def resolve_meeting_datetime(
        cls,
        text: str,
        tz_name: str = "Europe/Moscow",
        now: Optional[datetime] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Resolves relative date/time expressions ('завтра', 'в субботу', 'через 3 дня', 'в 19:00')
        into (YYYY-MM-DD, HH:MM) in the meeting city's local timezone.
        """
        cleaned = (text or "").lower()
        if not cleaned.strip():
            return None, None

        ref_utc = now or datetime.now(timezone.utc)
        if ref_utc.tzinfo is None:
            ref_utc = ref_utc.replace(tzinfo=timezone.utc)
        try:
            local_now = ref_utc.astimezone(ZoneInfo(tz_name))
        except Exception:
            local_now = ref_utc
        local_today = local_now.date()

        resolved_date: Optional[date] = None

        # Explicit YYYY-MM-DD
        iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", cleaned)
        if iso_match:
            try:
                resolved_date = datetime.strptime(iso_match.group(1), "%Y-%m-%d").date()
            except ValueError:
                pass

        if resolved_date is None:
            in_days_match = re.search(r"через\s+(\d+)\s+дн", cleaned)
            if in_days_match:
                resolved_date = local_today + timedelta(days=int(in_days_match.group(1)))
            elif "послезавтра" in cleaned or "післязавтра" in cleaned:
                resolved_date = local_today + timedelta(days=2)
            elif "завтра" in cleaned or "tomorrow" in cleaned:
                resolved_date = local_today + timedelta(days=1)
            elif "сегодня" in cleaned or "сьогодні" in cleaned or "today" in cleaned:
                resolved_date = local_today
            elif "через три дня" in cleaned:
                resolved_date = local_today + timedelta(days=3)
            elif "через пару дней" in cleaned:
                resolved_date = local_today + timedelta(days=2)
            else:
                for wd_word, target_wd in WEEKDAY_MAP.items():
                    if re.search(rf"\bв[о]?\s+{wd_word}\b", cleaned) or re.search(rf"\bна\s+{wd_word}\b", cleaned):
                        days_ahead = (target_wd - local_today.weekday()) % 7
                        if days_ahead == 0:
                            days_ahead = 7
                        resolved_date = local_today + timedelta(days=days_ahead)
                        break

        resolved_time: Optional[str] = None
        time_match = re.search(r"\b(?:в\s*)?([01]?\d|2[0-3]):([0-5]\d)\b", cleaned)
        if time_match:
            resolved_time = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
        else:
            hour_match = re.search(r"\bв\s+(\d{1,2})\s*(?:час\w*|вечера|дня)?\b", cleaned)
            if hour_match:
                hr = int(hour_match.group(1))
                if 1 <= hr <= 11 and ("вечер" in cleaned or hr >= 5):
                    hr += 12
                if 0 <= hr <= 23:
                    resolved_time = f"{hr:02d}:00"
            elif "вечером" in cleaned or "ввечері" in cleaned:
                resolved_time = "19:00"
            elif "днём" in cleaned or "днем" in cleaned:
                resolved_time = "15:00"

        return (resolved_date.isoformat() if resolved_date else None), resolved_time

    @classmethod
    def return_control_to_auto(cls, contact: Any) -> Dict[str, Any]:
        """Allows the human owner to explicitly return meeting control to AUTO (RETURNED_TO_AUTO)."""
        state = contact.get_meeting_state() if (contact and hasattr(contact, "get_meeting_state")) else {}
        state = dict(state or {})
        state["meeting_control"] = "RETURNED_TO_AUTO"
        if state.get("stage") == "MEETING_HANDOFF_REQUIRED":
            state["stage"] = "CONFIRMATION"
        if state.get("meeting_state") == STATE_BOOKING_HANDOFF:
            state["meeting_state"] = STATE_VENUE_SELECTION
        if contact and hasattr(contact, "set_meeting_state"):
            contact.set_meeting_state(state)
        return state

    # ==========================================================================
    # SECTION 12 & 13: STATE MACHINE TRANSITIONS & STRICT INVARIANTS
    # ==========================================================================

    @classmethod
    def evaluate_meeting_turn(
        cls,
        persona: Any,
        contact: Any,
        incoming_message: str,
        user_geo_profile: Any = None,
        lore_resolution: Optional[GeoConnectionResolution] = None,
        now: Optional[datetime] = None,
    ) -> MeetingPlannerResolution:
        """
        Evaluates the incoming user message against the strict meeting state machine:
        NO_MEETING -> MEETING_PROPOSED -> NON_PREFERRED_ACTIVITY -> SOFT_REDIRECT ->
        CINEMA_OR_QUEST_PROPOSED -> USER_SELECTS -> ACTIVITY_CONFIRMED ->
        DATE_TIME_NEGOTIATION -> VENUE_SELECTION -> BOOKING_HANDOFF.
        """
        res = MeetingPlannerResolution()
        text = (incoming_message or "").strip()
        lower = text.lower()

        saved = {}
        if contact and hasattr(contact, "get_meeting_state") and callable(contact.get_meeting_state):
            saved = dict(contact.get_meeting_state() or {})

        control = saved.get("meeting_control", "AUTO")
        stage = saved.get("stage", "IDLE")
        m_state = saved.get("meeting_state", STATE_NO_MEETING if stage == "IDLE" else STATE_MEETING_PROPOSED)

        # Determine user current city and timezone
        user_city = None
        user_tz = None
        if user_geo_profile is not None:
            if isinstance(user_geo_profile, dict):
                curr = user_geo_profile.get("current_location") or {}
                user_city = curr.get("city") or user_geo_profile.get("current_city")
                tz_blk = user_geo_profile.get("timezone")
                user_tz = tz_blk.get("value") if isinstance(tz_blk, dict) else tz_blk
            else:
                user_city = getattr(user_geo_profile, "current_city", None)
                user_tz = getattr(user_geo_profile, "timezone", None)
        if contact is not None:
            user_city = user_city or getattr(contact, "current_city", None) or getattr(contact, "detected_city", None)
            user_tz = user_tz or getattr(contact, "detected_timezone", None)

        persona_home = getattr(persona, "city", None) if persona else None
        eff_tz = user_tz or (getattr(persona, "timezone_name", "Europe/Moscow") if persona else "Europe/Moscow")

        res.user_current_city = user_city
        res.persona_home_city = persona_home
        res.meeting_city = saved.get("meeting_city") or user_city or persona_home
        res.timezone = eff_tz
        res.meeting_control = control
        res.stage = stage
        res.meeting_state = m_state
        res.activity_type = saved.get("activity_type")
        res.meeting_confirmed = bool(saved.get("meeting_confirmed", False))
        res.preferred_genre = saved.get("preferred_genre")
        res.disliked_genres = list(saved.get("disliked_genres") or [])
        res.rejected_activities = list(saved.get("rejected_activities") or [])
        res.agreed_venue = saved.get("agreed_venue")
        res.agreed_item_name = saved.get("agreed_item_name")
        res.meeting_date = saved.get("meeting_date")
        res.meeting_time = saved.get("meeting_time")

        # Check Persona Lore bridge (Section 17)
        if lore_resolution is None and persona:
            lore_resolution = PersonaLoreService.resolve_geographic_connection(
                persona=persona,
                contact=contact,
                user_geo_profile=user_geo_profile,
                incoming_message=incoming_message,
                now=now,
            )
        if lore_resolution and lore_resolution.natural_meeting_opportunity:
            res.natural_trip_bridge = True
            res.trip_timing_phrase = lore_resolution.timing_phrase

        # If owner already holds meeting control, do not advance autonomous meeting logistics
        if control == "OWNER":
            return res

        # Check decline or uncertainty -> do NOT push
        if any(re.search(pat, lower) for pat in DECLINE_OR_UNCERTAIN_PATTERNS):
            res.user_declined_or_uncertain = True
            return res

        # Run Section 14 Classifier
        classification = cls.classify_meeting_proposal(lower, current_rejected=res.rejected_activities)
        res.meeting_proposed = classification["meeting_proposed"]
        res.proposed_activity = classification["proposed_activity"]
        res.redirect_required = classification["redirect_required"]
        res.suggestions = classification["suggestions"]
        res.rejected_activities = classification["rejected_activities"]
        res.user_open_to_any = classification["user_open_to_any"]
        res.user_asks_preference = classification["user_asks_preference"]
        res.user_asks_why = classification["user_asks_why"]
        res.user_insists = classification["user_insists"]

        # Detect genre preferences from current message
        pref_genre, disliked = cls.detect_genre_preferences(lower)
        if pref_genre:
            res.preferred_genre = pref_genre
        for d in disliked:
            if d not in res.disliked_genres:
                res.disliked_genres.append(d)

        # Resolve date & time if mentioned
        dt_iso, tm_str = cls.resolve_meeting_datetime(lower, tz_name=eff_tz, now=now)
        if dt_iso:
            res.meeting_date = dt_iso
        if tm_str:
            res.meeting_time = tm_str

        asks_where = bool(re.search(r"\b(куда|что\s+предлагаешь|какие\s+варианты|какой\s+квест|какой\s+фильм)\b", lower))
        explicit_booking_trigger = any(re.search(pat, lower) for pat in BOOKING_OR_LINK_TRIGGER_PATTERNS)

        # Update preferred activity if user chose cinema or quest
        if classification["preferred_activity"] in PREFERRED_ACTIVITIES:
            res.activity_type = classification["preferred_activity"]

        # Match catalog options for meeting_city
        options = cls.get_catalog_for_city(
            persona=persona,
            meeting_city=res.meeting_city,
            activity_type=res.activity_type,
            preferred_genre=res.preferred_genre,
            disliked_genres=res.disliked_genres,
            today=(now or datetime.now(timezone.utc)).date(),
            limit=3,
        )
        res.selected_options = options

        # Check if user named an option from catalog
        for opt in options:
            opt_name = (opt.get("name") or "").strip()
            if opt_name and opt_name.lower() in lower:
                res.agreed_venue = opt.get("venue") or opt_name
                res.agreed_item_name = opt_name
                res.activity_type = res.activity_type or opt.get("type") or opt.get("activity_type")
                break

        # ----------------------------------------------------------------------
        # STATE MACHINE TRANSITIONS (Section 12)
        # ----------------------------------------------------------------------

        # Initial transition from IDLE / NO_MEETING
        if stage == "IDLE" or m_state == STATE_NO_MEETING:
            if res.meeting_proposed:
                res.meeting_state = STATE_MEETING_PROPOSED
                res.stage = "INTEREST_SHOWN"
                if res.activity_type in PREFERRED_ACTIVITIES:
                    res.meeting_state = STATE_ACTIVITY_CONFIRMED
                    res.stage = "OPTIONS_DISCUSSED" if options else "PREFERENCE_DISCOVERY"
                elif res.proposed_activity and res.proposed_activity not in PREFERRED_ACTIVITIES:
                    res.meeting_state = STATE_NON_PREFERRED_ACTIVITY
                    res.stage = "INTEREST_SHOWN"
                    res.redirect_required = True
                elif res.user_open_to_any or res.user_asks_preference:
                    res.meeting_state = STATE_CINEMA_OR_QUEST_PROPOSED
                    res.stage = "PREFERENCE_DISCOVERY"
                    res.redirect_required = True

        elif m_state in (STATE_MEETING_PROPOSED, STATE_NON_PREFERRED_ACTIVITY, STATE_SOFT_REDIRECT, STATE_CINEMA_OR_QUEST_PROPOSED):
            if res.activity_type in PREFERRED_ACTIVITIES:
                res.meeting_state = STATE_ACTIVITY_CONFIRMED
                res.stage = "OPTIONS_DISCUSSED" if options else "PREFERENCE_DISCOVERY"
                res.redirect_required = False
            elif classification["cinema_rejected"] or classification["quest_rejected"]:
                res.meeting_state = STATE_CINEMA_OR_QUEST_PROPOSED
                res.stage = "PREFERENCE_DISCOVERY"
                res.redirect_required = True
            elif res.user_open_to_any or res.user_asks_preference or asks_where or pref_genre:
                res.meeting_state = STATE_CINEMA_OR_QUEST_PROPOSED
                res.stage = "OPTIONS_DISCUSSED" if options else "PREFERENCE_DISCOVERY"
                res.redirect_required = True
            elif res.user_insists or (res.proposed_activity and res.proposed_activity not in PREFERRED_ACTIVITIES):
                res.meeting_state = STATE_SOFT_REDIRECT
                res.stage = "INTEREST_SHOWN"
                res.redirect_required = True

        elif m_state in (STATE_USER_SELECTS, STATE_ACTIVITY_CONFIRMED) or stage == "OPTIONS_DISCUSSED":
            if res.activity_type in PREFERRED_ACTIVITIES:
                if res.agreed_item_name or options:
                    if not res.agreed_item_name and options:
                        top = options[0]
                        res.agreed_item_name = top.get("name")
                        res.agreed_venue = top.get("venue") or top.get("name")
                if res.meeting_date or res.meeting_time:
                    res.meeting_state = STATE_DATE_TIME_NEGOTIATION
                    res.stage = "CONFIRMATION"
                elif res.agreed_venue:
                    res.meeting_state = STATE_VENUE_SELECTION
                    res.stage = "OPTIONS_DISCUSSED"
            else:
                # If user somehow rejected or redirected back to non-preferred
                res.meeting_state = STATE_NON_PREFERRED_ACTIVITY
                res.stage = "INTEREST_SHOWN"
                res.redirect_required = True

        elif m_state in (STATE_DATE_TIME_NEGOTIATION, STATE_VENUE_SELECTION) or stage == "CONFIRMATION":
            if res.activity_type in PREFERRED_ACTIVITIES:
                if not res.agreed_item_name and options:
                    top = options[0]
                    res.agreed_item_name = top.get("name")
                    res.agreed_venue = top.get("venue") or top.get("name")

        # ----------------------------------------------------------------------
        # SECTION 13: CRITICAL APPLICATION-LEVEL VALIDATION
        # ----------------------------------------------------------------------
        # Before marking meeting_confirmed = true, validate meeting.activity_type IN ["cinema", "quest"].
        # The state NON_PREFERRED_ACTIVITY must NEVER transition directly to ACTIVITY_CONFIRMED.
        if res.activity_type in PREFERRED_ACTIVITIES:
            res.meeting_confirmed = True
            res.redirect_required = False
        else:
            res.meeting_confirmed = False
            # If activity is not cinema/quest, NEVER allow ACTIVITY_CONFIRMED or handoff
            if res.meeting_state in (STATE_ACTIVITY_CONFIRMED, STATE_DATE_TIME_NEGOTIATION, STATE_VENUE_SELECTION, STATE_BOOKING_HANDOFF):
                res.meeting_state = STATE_SOFT_REDIRECT
                res.stage = "INTEREST_SHOWN"
                res.redirect_required = True

        # ----------------------------------------------------------------------
        # SECTION 18: BOOKING HANDOFF TRIGGER
        # ----------------------------------------------------------------------
        # Handoff can ONLY be triggered if activity_type is confirmed as cinema or quest!
        if res.activity_type in PREFERRED_ACTIVITIES:
            has_booking_demand = explicit_booking_trigger and (res.stage != "IDLE" or res.meeting_proposed or res.activity_type)
            if has_booking_demand:
                res.handoff_required = True
                res.handoff_reason = "booking/link required"
                res.meeting_state = STATE_BOOKING_HANDOFF
                res.stage = "MEETING_HANDOFF_REQUIRED"
                res.meeting_control = "OWNER"

                if not res.agreed_item_name and options:
                    top = options[0]
                    res.agreed_item_name = top.get("name")
                    res.agreed_venue = top.get("venue") or top.get("name")

                conn_rel = None
                trip_arr = None
                if lore_resolution and lore_resolution.matched and lore_resolution.connection:
                    conn_rel = lore_resolution.connection.get("relative")
                    if lore_resolution.travel:
                        trip_arr = lore_resolution.travel.get("arrival_date") or lore_resolution.travel.get("departure_date")

                res.handoff_payload = {
                    "event": "MEETING_HANDOFF_REQUIRED",
                    "chat_id": str(getattr(contact, "tg_id", "") or getattr(contact, "id", "")),
                    "user_id": str(getattr(contact, "tg_id", "") or getattr(contact, "id", "")),
                    "current_city": res.user_current_city,
                    "city": res.meeting_city,
                    "meeting_city": res.meeting_city,
                    "activity_type": res.activity_type,
                    "activity": res.agreed_item_name or res.activity_type,
                    "venue": res.agreed_venue or "to be finalized by owner",
                    "date": res.meeting_date or "to be finalized",
                    "time": res.meeting_time or "to be finalized",
                    "persona_connection": conn_rel,
                    "persona_trip": trip_arr,
                    "reason": res.handoff_reason,
                }

        # Persist updated meeting state on Contact
        new_state = {
            "meeting_control": res.meeting_control,
            "stage": res.stage,
            "meeting_state": res.meeting_state,
            "meeting_city": res.meeting_city,
            "activity_type": res.activity_type,
            "meeting_confirmed": res.meeting_confirmed,
            "redirect_required": res.redirect_required,
            "preferred_genre": res.preferred_genre,
            "disliked_genres": res.disliked_genres,
            "rejected_activities": res.rejected_activities,
            "agreed_venue": res.agreed_venue,
            "agreed_item_name": res.agreed_item_name,
            "meeting_date": res.meeting_date,
            "meeting_time": res.meeting_time,
            "timezone": res.timezone,
            "handoff_payload": res.handoff_payload,
        }
        if contact and hasattr(contact, "set_meeting_state") and callable(contact.set_meeting_state):
            contact.set_meeting_state(new_state)

        return res

    # ==========================================================================
    # SECTIONS 2, 4, 11, 20: SITUATIONAL PROMPT INSTRUCTIONS
    # ==========================================================================

    @classmethod
    def format_meeting_planner_prompt_block(
        cls,
        resolution: MeetingPlannerResolution,
        persona: Any,
    ) -> str:
        """Formats the situational prompt instructions for the current meeting state."""
        if resolution.user_declined_or_uncertain:
            return (
                "[MEETING PLANNER — УВАЖЕНИЕ ГРАНИЦ]:\n"
                "• Собеседник сомневается или не готов к встрече. НЕ дави и НЕ уговаривай! Спокойно продолжай обычный разговор."
            )

        if resolution.meeting_control == "OWNER" or resolution.stage == "MEETING_HANDOFF_REQUIRED" or resolution.meeting_state == STATE_BOOKING_HANDOFF:
            return (
                "[STRICT MEETING NEGOTIATION LOCK — КОНТРОЛЬ ПЕРЕДАН ВЛАДЕЛЬЦУ (OWNER HANDOFF)]:\n"
                "• Логистика встречи (бронь, билеты, ссылка, точное время) передана владельцу.\n"
                "• СТРОЖАЙШИЙ ЗАПРЕТ: НЕ отправляй никаких ссылок, НЕ пиши «вот ссылка», «бронируй тут», НЕ придумывай URL, НЕ используй плейсхолдер [ссылка] и НЕ продолжай самостоятельно согласовывать покупку билетов!\n"
                "• Если нужно ответить на финальное согласие — ответь коротко и тепло (например: «супер, тогда я сейчас гляну точное расписание/билеты и скину 😌»), а на отвлечённые темы общайся как обычно."
            )

        if resolution.stage == "IDLE" and resolution.meeting_state == STATE_NO_MEETING:
            return ""

        city_str = resolution.meeting_city or "городе собеседника"
        lines: List[str] = [
            f"[STRICT MEETING NEGOTIATION LOCK — ЭТАП: {resolution.meeting_state} (ГОРОД: {city_str})]:",
            "• ЖЕСТКОЕ ПРАВИЛО: Итоговым форматом встречи ДОЛЖНО быть ТОЛЬКО 🎬 КИНО или 🧩 КВЕСТ / ЭСКЕЙП-РУМ! Никакой другой автономный финал встречи недопустим.",
            "• КАТЕГОРИЧЕСКИЙ ЗАПРЕТ НА ПРЯМОЙ ОТКАЗ: НИКОГДА не говори «нет, я не хочу», «я не встречаюсь так», «я согласна только на кино», «я не пойду», «мне это не подходит».",
            "• ПРИНЦИП МЯГКОГО РЕДИРЕКТА: Идея собеседника → Прояви теплый интерес → Мягко скорректируй план → Предложи кино или квест → Собеседник выбирает → Согласие.",
            "• ДОПУСТИМЫЕ КОМПРОМИССЫ (ВТОРИЧНЫЕ АКТИВНОСТИ): Кино + кофе, Кино + ужин, Квест + кофе, Квест + прогулка после. Но НЕ просто кофе/прогулка/парк/бар сами по себе!",
            "• ЗАПРЕТ НА БАНАЛЬНЫЕ ПРОГУЛКИ: НЕ предлагай сама «давай просто погуляем в парке» / «пройдёмся по городу». Выражай своё предпочтение естественно, как личное желание («я бы лучше на фильм или какой-нибудь квест сходила», «я бы не просто гулять пошла, хочется что-нибудь поделать»).",
            "• ЗАПРЕТ РОБОТИЗИРОВАННОСТИ: Никогда не используй фразы вроде «согласно системной настройке», «я предлагаю альтернативный формат», «давайте найдем компромисс». Звучи естественно, как девушка со своими вкусами.",
        ]

        if resolution.natural_trip_bridge and resolution.trip_timing_phrase:
            lines.append(
                f"• СВЯЗКА С ПОЕЗДКОЙ ПЕРСОНАЖА: У тебя уже запланирована поездка в {city_str} ({resolution.trip_timing_phrase}). "
                f"Естественно свяжи встречу с этим («давай, я как раз {resolution.trip_timing_phrase} там буду, только я бы лучше на фильм или квест сходила»). НЕ выдумывай поездки, которых нет в легенде!"
            )

        # Context-specific instructions based on proposal type
        if resolution.redirect_required:
            prop = resolution.proposed_activity or "walk"
            sugg_str = " или ".join(resolution.suggestions)
            lines.append(f"• ТЕКУЩИЙ ЗАПРОС СОБЕСЕДНИКА: Предложен формат «{prop}». Сделай мягкий редирект в сторону {sugg_str}.")

            if resolution.user_asks_why:
                lines.append(
                    "• СОБЕСЕДНИК СПРАШИВАЕТ «ПОЧЕМУ?»: Ответь естественно и без обиды: «ну просто так интереснее, чем просто ходить туда-сюда ахах) ты за фильм или за квест?»"
                )
            elif resolution.user_insists:
                lines.append(
                    "• СОБЕСЕДНИК НАСТАИВАЕТ НА СВОЕМ: Сохраняй спокойствие: подтверди его мысль → объясни личное предпочтение → предложи компромисс: «я поняла тебя 😭 просто мне как-то веселее когда мы куда-нибудь реально идём, давай хотя бы на фильм или квест?»"
                )
            elif resolution.user_open_to_any:
                lines.append(
                    "• СОБЕСЕДНИК ГОВОРИТ «МНЕ ВСЁ РАВНО / ВЫБИРАЙ САМА»: Выбери сама между кино и квестом: «тогда давай на квест, мне кажется веселее будет) или ты больше за фильм?»"
                )
            elif resolution.user_asks_preference:
                lines.append(
                    "• СОБЕСЕДНИК СПРАШИВАЕТ «ЧТО ТЫ ХОЧЕШЬ?»: Вырази предпочтение: «я бы на кино сходила или какой-нибудь квест выбрала, хочется не просто сидеть, а поделать что-то интересное)»"
                )
            elif prop in ("walk", "park"):
                lines.append(
                    "• ПРИМЕР ПЕРЕНАПРАВЛЕНИЯ С ПРОГУЛКИ/ПАРКА: «давай 😌 только я бы лучше куда-нибудь с движухой, можно в кино или на квест» (или «можно, но я бы лучше какой-нибудь квест устроила 😭 или давай в кино, а потом уже можно пройтись»)."
                )
            elif prop == "coffee":
                lines.append(
                    "• ПРИМЕР ПЕРЕНАПРАВЛЕНИЯ С КОФЕ: «можно, но я бы лучше сначала куда-нибудь сходила, в кино например или на квест, а кофе можно и потом выпить ☕»."
                )
            elif prop == "restaurant":
                lines.append(
                    "• ПРИМЕР ПЕРЕНАПРАВЛЕНИЯ С РЕСТОРАНА: «я бы лучше сначала на фильм или квест сходила, а потом можно где-нибудь посидеть вкусно поесть)»."
                )
            elif prop == "bar":
                lines.append(
                    "• ПРИМЕР ПЕРЕНАПРАВЛЕНИЯ С БАРА: «слушай, я бы лучше сначала в кино или на квест сходила, а посидеть где-нибудь можно и после)»."
                )
            elif prop == "home":
                lines.append(
                    "• ПРИМЕР ПЕРЕНАПРАВЛЕНИЯ С ДОМА/ГОСТЕЙ: «в гости это как-то слишком быстро ахах) давай лучше для начала в кино или на квест сходим?»"
                )
            elif prop == "drive":
                lines.append(
                    "• ПРИМЕР ПЕРЕНАПРАВЛЕНИЯ С ПОЕЗДКИ НА МАШИНЕ: «кататься прикольно, но я бы лучше сначала на фильм или квест сходила, чтобы интереснее было)»."
                )

            # Section 15: Loop prevention
            if "cinema" in resolution.rejected_activities:
                lines.append("• ВНИМАНИЕ: Собеседник не хочет в кино! Предложи ТОЛЬКО квест («тогда давай на квест? там точно не соскучимся»). Не повторяй кино!")
            elif "quest" in resolution.rejected_activities:
                lines.append("• ВНИМАНИЕ: Собеседник не хочет на квест! Предложи ТОЛЬКО кино («тогда давай в кино на что-нибудь интересное»). Не повторяй квест!")

        elif resolution.stage in ("OPTIONS_DISCUSSED", "PREFERENCE_DISCOVERY") or resolution.meeting_state in (STATE_ACTIVITY_CONFIRMED, STATE_VENUE_SELECTION):
            act_type = str(resolution.activity_type or "кино или квест").upper()
            lines.append(f"• ФОРМАТ СОГЛАСОВАН: {act_type}.")
            if resolution.selected_options:
                opt_lines = []
                for idx, opt in enumerate(resolution.selected_options[:3], 1):
                    o_type = opt.get("type") or opt.get("activity_type") or "activity"
                    o_name = opt.get("name") or "Вариант"
                    o_genre = f", жанр: {opt.get('genre')}" if opt.get("genre") else ""
                    o_dur = f", ~{opt.get('duration_minutes')} мин" if opt.get("duration_minutes") else ""
                    o_films = f", фильмы: {', '.join(opt.get('films', [])[:3])}" if opt.get("films") else ""
                    o_unverified = " (расписание требует уточнения)" if opt.get("schedule_verified") is False else ""
                    opt_lines.append(f"  {idx}. [{o_type}] {o_name} ({opt.get('city')}){o_genre}{o_dur}{o_films}{o_unverified}")
                lines.append(f"• ВАРИАНТЫ ИЗ КАТАЛОГА ДЛЯ {city_str} (ОБСУДИ 1–2 ВАРИАНТА ЕСТЕСТВЕННО, БЕЗ ССЫЛОК!):\n" + "\n".join(opt_lines))
            else:
                lines.append(f"• В каталоге для {city_str} нет конкретных названий — обсуждай фильм или квест в целом, НЕ придумывай фейковые названия, адреса или ссылки!")

        elif resolution.stage == "CONFIRMATION" or resolution.meeting_state == STATE_DATE_TIME_NEGOTIATION:
            lines.append(
                f"• ЗАДАЧА: Вариант выбран ({resolution.agreed_item_name or resolution.activity_type}). "
                f"Уточни день/время (текущая дата={resolution.meeting_date or 'не уточнена'}, время={resolution.meeting_time or 'не уточнено'}). "
                "НЕ отправляй никаких ссылок!"
            )

        lines.append("• СТРОЖАЙШИЙ ЗАПРЕТ НА ССЫЛКИ И ПЛЕЙСХОЛДЕРЫ: НИКОГДА не отправляй URL и не пиши «[ссылка]»!")
        return "\n".join(lines)

    # ==========================================================================
    # SECTION 14: ACCIDENTAL YES INTERCEPTOR & HEALER
    # ==========================================================================

    @classmethod
    def heal_accidental_yes(
        cls,
        reply_text: str,
        resolution: Optional[MeetingPlannerResolution] = None,
    ) -> str:
        """
        Intercepts and heals responses where the bot accidentally agrees directly to a
        non-preferred format (walk, coffee, park, etc.) without redirecting to cinema/quest,
        or where the bot makes a hard refusal.
        Transforms it into a warm, natural personal preference response.
        """
        if not reply_text:
            return ""

        cleaned = reply_text.strip()
        low = cleaned.lower()

        # If already mentions cinema or quest, it is properly negotiating
        has_cinema_or_quest = any(w in low for w in ("кино", "фильм", "сеанс", "квест", "эскейп", "cinema", "movie", "quest"))

        # Check for hard refusal words (Section 2)
        hard_refusal_patterns = [
            r"\b(нет,?\s+я\s+не\s+хочу|я\s+не\s+встречаюсь\s+так|я\s+согласна\s+только\s+на\s+кино|я\s+не\s+пойду|мне\s+это\s+не\s+подходит|я\s+не\s+пойду\s+в\s+парк|я\s+не\s+пью\s+кофе)\b",
        ]
        has_hard_refusal = any(re.search(pat, low) for pat in hard_refusal_patterns)

        # Check for accidental agreement to non-preferred activities (Section 14)
        accidental_yes_patterns = [
            r"\b(давай|погнали|го|пошли|окей|хорошо|я\s+за|согласна|буду\s+рада)\b.*\b(в\s+парк|в\s+парке|погуля(ем|ть)|прогул|по\s+набережной|кофе|по\s+кофе|в\s+кафе|посидим\s+в\s+кафе|в\s+ресторан|поесть|в\s+бар|по\s+бокалу|покататься|по\s+домам|в\s+гости|в\s+тц)\b",
            r"\b(да,?\s+давай\s+в\s+парк|давай\s+кофе|окей,?\s+встретимся\s+там|давай\s+просто\s+погуляем|давай\s+встретимся\s+в\s+кафе)\b",
        ]
        has_accidental_yes = (not has_cinema_or_quest) and any(re.search(pat, low) for pat in accidental_yes_patterns)

        if not has_hard_refusal and not has_accidental_yes:
            return cleaned

        logger.warning(
            "[MEETING_PLANNER] Intercepted %s in outgoing reply: '%s'. Healing with natural soft redirect.",
            "hard refusal" if has_hard_refusal else "accidental yes",
            cleaned[:80],
        )

        prop = getattr(resolution, "proposed_activity", None) or "walk"
        rej = getattr(resolution, "rejected_activities", []) or []

        only_quest = "cinema" in rej
        only_cinema = "quest" in rej

        if prop in ("walk", "park"):
            if only_quest:
                return "давай 😌 только я бы лучше куда-нибудь с движухой, можно на какой-нибудь квест?"
            elif only_cinema:
                return "давай 😌 только я бы лучше куда-нибудь с движухой, можно в кино на фильм"
            else:
                return "давай 😌 только я бы лучше куда-нибудь с движухой, можно в кино или на квест"

        elif prop == "coffee":
            if only_quest:
                return "кофе можно потом, а давай сначала на какой-нибудь квест?"
            elif only_cinema:
                return "можно, но я бы лучше сначала куда-нибудь сходила, в кино например, а кофе потом ☕"
            else:
                return "можно, но я бы лучше сначала куда-нибудь сходила, в кино например или на квест, а кофе потом ☕"

        elif prop == "restaurant":
            if only_quest:
                return "я бы лучше сначала на квест сходила, а потом можно где-нибудь посидеть вкусно поесть)"
            else:
                return "я бы лучше сначала на фильм сходила, а потом можно где-нибудь посидеть вкусно поесть)"

        elif prop == "bar":
            return "слушай, я бы лучше сначала в кино или на квест сходила, а посидеть где-нибудь можно и после)"

        elif prop == "drive":
            return "кататься прикольно, но я бы лучше сначала на фильм или квест сходила, чтобы интереснее было)"

        elif prop == "home":
            return "в гости это как-то слишком быстро ахах) давай лучше для начала в кино или на квест сходим?"

        elif prop == "mall":
            return "по тц бродить скучновато) давай лучше в кино там же сходим или на квест какой-нибудь?"

        # Default natural redirect
        if only_quest:
            return "давай) только я бы лучше на какой-нибудь квест сходила, чтобы интереснее было)"
        elif only_cinema:
            return "давай) только я бы лучше на фильм в кино сходила, чтобы интереснее было)"
        return "давай 😌 только я бы лучше куда-нибудь с движухой, можно в кино или на квест"

    @classmethod
    def sanitize_outgoing_meeting_reply(
        cls,
        reply_text: str,
        resolution: Optional[MeetingPlannerResolution] = None,
    ) -> str:
        """
        Ensures the autonomous bot NEVER sends fake booking links or any URLs after handoff,
        heals accidental yes or hard refusals during negotiation, and strips leaked internal state tokens.
        """
        if not reply_text:
            return ""

        cleaned = reply_text
        # Strip any leaked internal state identifiers
        cleaned = re.sub(
            r"\[?(?:STRICT_MEETING_NEGOTIATION_LOCK|MEETING_HANDOFF_REQUIRED|BOOKING_HANDOFF|CITY_CONNECTION|PERSONA_LORE|TRIP_STATE|ACTIVITY_CATALOG|OWNER_HANDOFF|NON_PREFERRED_ACTIVITY|SOFT_REDIRECT|ACTIVITY_CONFIRMED)(?::[^\]]*)?\]?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

        # Strip any URLs or fake link placeholders
        if URL_RE.search(cleaned):
            cleaned = URL_RE.sub("", cleaned)
        cleaned = re.sub(r"\[(?:ссылка|link|посилання)\]", "", cleaned, flags=re.IGNORECASE)

        # Check and heal accidental yes or hard refusal if redirection is active
        if resolution and (resolution.redirect_required or resolution.stage in ("INTEREST_SHOWN", "PREFERENCE_DISCOVERY")):
            cleaned = cls.heal_accidental_yes(cleaned, resolution)

        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        return cleaned
