"""
Meeting Planner Service — Cinema / Quest Preference, Synchronized City-Scoped Catalog,
Geographic Persona Lore Bridge, and Owner Handoff State Machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
import re
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from companion.services.persona_lore_service import GeoConnectionResolution, PersonaLoreService


MEETING_CONTROL_STATES = ("AUTO", "HANDOFF_PENDING", "OWNER", "RETURNED_TO_AUTO")

FUNNEL_STAGES = (
    "IDLE",
    "INTEREST_SHOWN",
    "PREFERENCE_DISCOVERY",
    "OPTIONS_DISCUSSED",
    "CONFIRMATION",
    "MEETING_HANDOFF_REQUIRED",
)

PROPOSAL_PATTERNS = [
    r"\b(давай|может|го|хочу|можем|не\s+хочешь)\b.*\b(встрет(имся|иться)|увид(имся|еться)|пересеч(емся|ься)|сходи(м|ть)|погуля(ем|ть)|посид(им|еть))\b",
    r"\b(встрет(имся|иться)|увид(имся|еться)|пересеч(емся|ься)|пойдем\s+на\s+свидание|сходи(м|ть)\s+на\s+свидание)\b",
    r"\b(давай|може|хочу)\b.*\b(зустрін(емось|утись)|побачит(ись|ися)|підемо\s+на\s+побачення|сходи(мо|ти))\b",
    r"\b(let'?s\s+meet|want\s+to\s+meet|hang\s+out|go\s+out\s+together|see\s+each\s+other)\b",
]

DECLINE_OR_UNCERTAIN_PATTERNS = [
    r"\b(не\s+хочу\s+встречаться|не\s+готов\s+встречаться|пока\s+не\s+хочу|давай\s+потом|не\s+знаю\s+пока)\b",
    r"\b(не\s+могу\s+встретиться|не\s+получится\s+встретиться)\b",
]

EXPLICIT_WALK_PROPOSAL_PATTERNS = [
    r"\b(именно\s+погулять|просто\s+погулять\s+в\s+парке|хочу\s+просто\s+погулять|давай\s+только\s+погуляем)\b",
]

CINEMA_PREF_PATTERNS = [
    r"\b(кино|фильм|кинотеатр|сеанс|посмотреть\s+фильм|cinema|movie|film)\b",
]

QUEST_PREF_PATTERNS = [
    r"\b(квест|эскейп|хоррор-квест|загадк|головоломк|активн|quest|escape\s+room|puzzle)\b",
]

GENRE_KEYWORDS: Dict[str, List[str]] = {
    "horror": ["страшн", "хоррор", "ужас", "пугающ", "мистик", "триллер", "horror", "scary", "spooky"],
    "comedy": ["весёл", "весел", "смешн", "комеди", "лёгк", "легк", "позитив", "comedy", "funny", "fun"],
    "detective": ["детектив", "расследован", "тайна", "загадк", "шерлок", "detective", "mystery"],
    "adventure": ["приключ", "экшен", "динамичн", "активн", "adventure", "action"],
    "psychological": ["психолог", "умн", "сложн", "атмосферн", "psychological", "mind"],
}

BOOKING_OR_LINK_TRIGGER_PATTERNS = [
    r"\b(скинь\s+ссылк|дай\s+ссылк|где\s+купить|где\s+забронировать|забронируй|купим\s+билет|купить\s+билет)\b",
    r"\b(бронируй|бронь|оплат|ссылка\s+на\s+бронь|билеты|тогда\s+договорились|берём\s+этот|идем\s+туда)\b",
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
    "п'ятницю": 4,
    "суботу": 5,
    "неділю": 6,
}

URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)


@dataclass
class MeetingPlannerResolution:
    meeting_control: str = "AUTO"
    stage: str = "IDLE"
    meeting_city: Optional[str] = None
    user_current_city: Optional[str] = None
    persona_home_city: Optional[str] = None
    activity_type: Optional[str] = None  # "cinema" | "quest" | None
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


class MeetingPlannerService:
    """Manages cinema/quest meeting preference, city-scoped catalog sync, and owner handoff."""

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

        # Check negative constructions like "не люблю страшное"
        if re.search(r"не\s+(люблю|хочу|нрав\w*)\s+(страшн\w*|хоррор\w*|ужас\w*)", cleaned):
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
        if contact and hasattr(contact, "set_meeting_state"):
            contact.set_meeting_state(state)
        return state

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
        Evaluates the incoming user message against the gradual meeting funnel,
        city-scoped activity catalog, persona lore trip state, and handoff triggers.
        """
        res = MeetingPlannerResolution()
        text = (incoming_message or "").strip()
        lower = text.lower()

        saved = {}
        if contact and hasattr(contact, "get_meeting_state") and callable(contact.get_meeting_state):
            saved = dict(contact.get_meeting_state() or {})

        control = saved.get("meeting_control", "AUTO")
        stage = saved.get("stage", "IDLE")

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
        res.activity_type = saved.get("activity_type")
        res.preferred_genre = saved.get("preferred_genre")
        res.disliked_genres = list(saved.get("disliked_genres") or [])
        res.agreed_venue = saved.get("agreed_venue")
        res.agreed_item_name = saved.get("agreed_item_name")
        res.meeting_date = saved.get("meeting_date")
        res.meeting_time = saved.get("meeting_time")

        # Check Persona Lore bridge
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

        # Detect preferences from current message
        pref_genre, disliked = cls.detect_genre_preferences(lower)
        if pref_genre:
            res.preferred_genre = pref_genre
        for d in disliked:
            if d not in res.disliked_genres:
                res.disliked_genres.append(d)

        wants_quest = any(re.search(pat, lower) for pat in QUEST_PREF_PATTERNS)
        wants_cinema = any(re.search(pat, lower) for pat in CINEMA_PREF_PATTERNS)
        if wants_quest and not wants_cinema:
            res.activity_type = "quest"
        elif wants_cinema and not wants_quest:
            res.activity_type = "cinema"

        # Resolve date & time if mentioned
        dt_iso, tm_str = cls.resolve_meeting_datetime(lower, tz_name=eff_tz, now=now)
        if dt_iso:
            res.meeting_date = dt_iso
        if tm_str:
            res.meeting_time = tm_str

        is_proposal = any(re.search(pat, lower) for pat in PROPOSAL_PATTERNS)
        asks_where = bool(re.search(r"\b(куда|что\s+предлагаешь|какие\s+варианты|какой\s+квест|какой\s+фильм)\b", lower))
        explicit_booking_trigger = any(re.search(pat, lower) for pat in BOOKING_OR_LINK_TRIGGER_PATTERNS)

        # Match specific catalog options for meeting_city
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

        # Check if user selected/named one of the catalog options or agreed after options were discussed
        for opt in options:
            opt_name = (opt.get("name") or "").strip()
            if opt_name and opt_name.lower() in lower:
                res.agreed_venue = opt.get("venue") or opt_name
                res.agreed_item_name = opt_name
                res.activity_type = res.activity_type or opt.get("type") or opt.get("activity_type")
                break

        # Gradual state transitions
        if stage == "IDLE":
            if is_proposal:
                res.stage = "INTEREST_SHOWN"
                if res.activity_type:
                    res.stage = "OPTIONS_DISCUSSED" if options else "PREFERENCE_DISCOVERY"
        elif stage in ("INTEREST_SHOWN", "PREFERENCE_DISCOVERY"):
            if res.activity_type or asks_where or pref_genre:
                res.stage = "OPTIONS_DISCUSSED" if options else "PREFERENCE_DISCOVERY"
        elif stage == "OPTIONS_DISCUSSED":
            if res.agreed_item_name or res.meeting_date or res.meeting_time or explicit_booking_trigger:
                res.stage = "CONFIRMATION"
                if not res.agreed_item_name and options:
                    top = options[0]
                    res.agreed_item_name = top.get("name")
                    res.agreed_venue = top.get("venue") or top.get("name")
                    res.activity_type = res.activity_type or top.get("type") or top.get("activity_type")

        # Check if handoff is required now:
        # Either user explicitly asked for booking/link/tickets, OR we have agreed option + date/time in CONFIRMATION
        if explicit_booking_trigger and (stage != "IDLE" or is_proposal or res.activity_type):
            res.handoff_required = True
            res.handoff_reason = "booking/link required"
        elif res.stage == "CONFIRMATION" and res.meeting_date and res.meeting_time and (res.agreed_item_name or res.activity_type):
            res.handoff_required = True
            res.handoff_reason = "final meeting logistics confirmed (date, time, activity); booking/link required"

        if res.handoff_required:
            res.stage = "MEETING_HANDOFF_REQUIRED"
            res.meeting_control = "OWNER"
            if not res.agreed_item_name and options:
                top = options[0]
                res.agreed_item_name = top.get("name")
                res.agreed_venue = top.get("venue") or top.get("name")
                res.activity_type = res.activity_type or top.get("type") or top.get("activity_type")

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
                "activity_type": res.activity_type or "quest_or_cinema",
                "activity": res.agreed_item_name or res.activity_type or "cinema/quest",
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
            "meeting_city": res.meeting_city,
            "activity_type": res.activity_type,
            "preferred_genre": res.preferred_genre,
            "disliked_genres": res.disliked_genres,
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

    @classmethod
    def format_meeting_planner_prompt_block(
        cls,
        resolution: MeetingPlannerResolution,
        persona: Any,
    ) -> str:
        """Formats the situational prompt instructions for the current meeting funnel stage."""
        if resolution.user_declined_or_uncertain:
            return (
                "[MEETING PLANNER — УВАЖЕНИЕ ГРАНИЦ]:\n"
                "• Собеседник сомневается или не готов к встрече. НЕ дави и НЕ уговаривай! Спокойно продолжай обычный разговор."
            )

        if resolution.meeting_control == "OWNER" or resolution.stage == "MEETING_HANDOFF_REQUIRED":
            return (
                "[MEETING PLANNER — КОНТРОЛЬ ПЕРЕДАН ВЛАДЕЛЬЦУ (OWNER HANDOFF)]:\n"
                "• Логистика встречи (бронь, билеты, ссылка, точное время) передана владельцу.\n"
                "• СТРОЖАЙШИЙ ЗАПРЕТ: НЕ отправляй никаких ссылок, НЕ пиши «вот ссылка», «бронируй тут», НЕ придумывай URL и НЕ продолжай самостоятельно согласовывать покупку билетов!\n"
                "• Если нужно ответить на финальное согласие — скажи коротко и тепло (например: «супер, тогда я сейчас гляну точное расписание/билеты и скину 😌»), а на отвлечённые темы общайся как обычно."
            )

        if resolution.stage == "IDLE":
            return ""

        city_str = resolution.meeting_city or "городе собеседника"
        lines: List[str] = [
            f"[MEETING PLANNER — ЭТАП ВОРОНКИ ВСТРЕЧИ: {resolution.stage} (ГОРОД ВСТРЕЧИ: {city_str})]:",
            "• ПРИОРИТЕТ ДОСУГА: Ты предпочитаешь 1) 🎬 кино или 2) 🧩 квест / эскейп-рум вместо банальной прогулки в парке.",
            "• ЗАПРЕТ НА БАНАЛЬНЫЕ ПРОГУЛКИ: НЕ предлагай сама «давай просто погуляем в парке» / «пройдёмся по городу». Выражай своё предпочтение естественно, как личное желание («я бы лучше на фильм или какой-нибудь квест сходила», «я бы не просто гулять пошла, хочется что-нибудь поделать»).",
            "• ЗАПРЕТ РОБОТИЗИРОВАННОСТИ: Никогда не говори «согласно настройкам я выбираю квест» и не выдавай внутренние теги.",
        ]

        if resolution.natural_trip_bridge and resolution.trip_timing_phrase:
            lines.append(
                f"• СВЯЗКА С ПОЕЗДКОЙ ПЕРСОНАЖА: У тебя уже запланирована поездка в {city_str} ({resolution.trip_timing_phrase}). "
                f"Естественно свяжи встречу с этим («давай, я как раз {resolution.trip_timing_phrase} там буду»), НЕ говори «я специально ради тебя приеду»."
            )

        if resolution.stage == "INTEREST_SHOWN":
            lines.append(
                "• ЗАДАЧА НА ЭТОТ ХОД: Прояви тёплый интерес к встрече и мягко предложи сходить в кино или на квест. Спроси, что ему ближе — кино или что-нибудь активное вроде квеста. НЕ вываливай сразу список заведений и ссылок!"
            )
        elif resolution.stage == "PREFERENCE_DISCOVERY":
            lines.append(
                "• ЗАДАЧА НА ЭТОТ ХОД: Уточни его вкусы (какой жанр фильма или квеста нравится — хоррор, детектив, комедия/приключения), прежде чем называть конкретное место."
            )
        elif resolution.stage == "OPTIONS_DISCUSSED":
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
                lines.append("• ДОСТУПНЫЕ ВАРИАНТЫ В КАТАЛОГЕ ДЛЯ ГОРОДА " + city_str + " (ОБСУДИ 1–2 ВАРИАНТА ЕСТЕСТВЕННО, БЕЗ ССЫЛОК!):\n" + "\n".join(opt_lines))
            else:
                lines.append(
                    f"• В каталоге для города {city_str} пока нет синхронизированных названий — обсуди идею пойти в кино или на квест в целом, НЕ выдумывай названия несуществующих заведений, расписание или ссылки!"
                )
        elif resolution.stage == "CONFIRMATION":
            lines.append(
                f"• ЗАДАЧА НА ЭТОТ ХОД: Вариант выбран ({resolution.agreed_item_name or resolution.activity_type}). "
                f"Уточни недостающие детали (день/время: сейчас дата={resolution.meeting_date or 'не уточнена'}, время={resolution.meeting_time or 'не уточнено'}). "
                "НЕ отправляй никаких ссылок!"
            )

        lines.append("• СТРОЖАЙШИЙ ЗАПРЕТ НА ФЕЙКОВЫЕ ССЫЛКИ: Никогда не придумывай и не отправляй URL-ссылки!")
        return "\n".join(lines)

    @classmethod
    def sanitize_outgoing_meeting_reply(
        cls,
        reply_text: str,
        resolution: Optional[MeetingPlannerResolution] = None,
    ) -> str:
        """
        Ensures the autonomous bot NEVER sends fake booking links or any URLs after handoff,
        and strips leaked internal state tokens.
        """
        if not reply_text:
            return ""

        cleaned = reply_text
        # Strip any leaked internal state identifiers
        cleaned = re.sub(
            r"\[?(?:MEETING_HANDOFF_REQUIRED|CITY_CONNECTION|PERSONA_LORE|TRIP_STATE|ACTIVITY_CATALOG|OWNER_HANDOFF)(?::[^\]]*)?\]?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

        # Strip any URLs from autonomous meeting replies (links are strictly handled by owner after handoff)
        if URL_RE.search(cleaned):
            cleaned = URL_RE.sub("", cleaned)

        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        return cleaned
