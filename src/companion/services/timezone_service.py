import re
import random
from datetime import datetime, time, timedelta
from typing import Optional, Tuple, Dict, Any, List
import pytz


# Comprehensive mapping of World & Russian/CIS cities and countries to IANA timezones
# Fully supports automatic Daylight Saving Time (summer / winter time) transitions
CITY_TIMEZONE_MAP = {
    # --- Americas (GMT-5 / GMT-4 DST, GMT-8 / GMT-7 DST, etc.) ---
    "нью-йорк": "America/New_York",
    "new york": "America/New_York",
    "ньюйорк": "America/New_York",
    "сша": "America/New_York",
    "штаты": "America/New_York",
    "америка": "America/New_York",
    "usa": "America/New_York",
    "майами": "America/New_York",
    "miami": "America/New_York",
    "вашингтон": "America/New_York",
    "washington": "America/New_York",
    "бостон": "America/New_York",
    "чикаго": "America/Chicago",
    "лос-анджелес": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "калифорния": "America/Los_Angeles",
    "сан-франциско": "America/Los_Angeles",
    "торонто": "America/Toronto",
    "toronto": "America/Toronto",
    "монреаль": "America/Montreal",
    "montreal": "America/Montreal",
    "канада": "America/Toronto",
    "ванкувер": "America/Vancouver",
    "vancouver": "America/Vancouver",

    # --- Western & Central Europe (GMT+0 / GMT+1 DST, GMT+1 / GMT+2 DST) ---
    "лондон": "Europe/London",
    "london": "Europe/London",
    "великобритания": "Europe/London",
    "британия": "Europe/London",
    "англия": "Europe/London",
    "uk": "Europe/London",
    "манчестер": "Europe/London",
    "париж": "Europe/Paris",
    "paris": "Europe/Paris",
    "франция": "Europe/Paris",
    "ницца": "Europe/Paris",
    "берлин": "Europe/Berlin",
    "berlin": "Europe/Berlin",
    "германия": "Europe/Berlin",
    "мюнхен": "Europe/Berlin",
    "франкфурт": "Europe/Berlin",
    "рим": "Europe/Rome",
    "италия": "Europe/Rome",
    "милан": "Europe/Rome",
    "мадрид": "Europe/Madrid",
    "испания": "Europe/Madrid",
    "барселона": "Europe/Madrid",
    "варшава": "Europe/Warsaw",
    "warsaw": "Europe/Warsaw",
    "польша": "Europe/Warsaw",
    "краков": "Europe/Warsaw",
    "прага": "Europe/Prague",
    "чехия": "Europe/Prague",
    "вена": "Europe/Vienna",
    "австрия": "Europe/Vienna",
    "амстердам": "Europe/Amsterdam",
    "нидерланды": "Europe/Amsterdam",
    "брюссель": "Europe/Brussels",
    "бельгия": "Europe/Brussels",
    "цюрих": "Europe/Zurich",
    "швейцария": "Europe/Zurich",
    "лиссабон": "Europe/Lisbon",
    "португалия": "Europe/Lisbon",

    # --- Eastern Europe (GMT+2 / GMT+3 DST) ---
    "киев": "Europe/Kyiv",
    "kyiv": "Europe/Kyiv",
    "kiev": "Europe/Kyiv",
    "украина": "Europe/Kyiv",
    "одесса": "Europe/Kyiv",
    "львов": "Europe/Kyiv",
    "харьков": "Europe/Kyiv",
    "днепр": "Europe/Kyiv",
    "бухарест": "Europe/Bucharest",
    "румыния": "Europe/Bucharest",
    "софия": "Europe/Sofia",
    "болгария": "Europe/Sofia",
    "афины": "Europe/Athens",
    "греция": "Europe/Athens",
    "рига": "Europe/Riga",
    "латвия": "Europe/Riga",
    "вильнюс": "Europe/Vilnius",
    "литва": "Europe/Vilnius",
    "таллин": "Europe/Tallinn",
    "эстония": "Europe/Tallinn",
    "кишинев": "Europe/Chisinau",
    "молдова": "Europe/Chisinau",
    "хельсинки": "Europe/Helsinki",
    "финляндия": "Europe/Helsinki",

    # --- Russia & Belarus ---
    # UTC+2
    "калининград": "Europe/Kaliningrad",
    # UTC+3
    "москва": "Europe/Moscow",
    "питер": "Europe/Moscow",
    "петербург": "Europe/Moscow",
    "санкт-петербург": "Europe/Moscow",
    "казань": "Europe/Moscow",
    "нижний новгород": "Europe/Moscow",
    "ростов": "Europe/Moscow",
    "краснодар": "Europe/Moscow",
    "сочи": "Europe/Moscow",
    "воронеж": "Europe/Moscow",
    "ярославль": "Europe/Moscow",
    "рязань": "Europe/Moscow",
    "тула": "Europe/Moscow",
    "минск": "Europe/Minsk",
    "беларусь": "Europe/Minsk",
    # UTC+4
    "самара": "Europe/Samara",
    "саратов": "Europe/Samara",
    "ульяновск": "Europe/Samara",
    "ижевск": "Europe/Samara",
    "тольятти": "Europe/Samara",
    # UTC+5
    "екатеринбург": "Asia/Yekaterinburg",
    "челябинск": "Asia/Yekaterinburg",
    "уфа": "Asia/Yekaterinburg",
    "пермь": "Asia/Yekaterinburg",
    "тюмень": "Asia/Yekaterinburg",
    "курган": "Asia/Yekaterinburg",
    "оренбург": "Asia/Yekaterinburg",
    # UTC+6
    "омск": "Asia/Omsk",
    # UTC+7
    "новосибирск": "Asia/Novosibirsk",
    "красноярск": "Asia/Krasnoyarsk",
    "барнаул": "Asia/Novosibirsk",
    "томск": "Asia/Novosibirsk",
    "кемерово": "Asia/Novosibirsk",
    "новокузнецк": "Asia/Novosibirsk",
    # UTC+8
    "иркутск": "Asia/Irkutsk",
    "улан-удэ": "Asia/Irkutsk",
    # UTC+9
    "якутск": "Asia/Yakutsk",
    "чита": "Asia/Yakutsk",
    # UTC+10
    "владивосток": "Asia/Vladivostok",
    "хабаровск": "Asia/Vladivostok",
    # UTC+11
    "магадан": "Asia/Magadan",
    "сахалин": "Asia/Sakhalin",
    "южно-сахалинск": "Asia/Sakhalin",
    # UTC+12
    "камчатка": "Asia/Kamchatka",
    "петропавловск-камчатский": "Asia/Kamchatka",

    # --- Caucasus & Central Asia & Middle East ---
    "дубай": "Asia/Dubai",
    "dubai": "Asia/Dubai",
    "оаэ": "Asia/Dubai",
    "эмираты": "Asia/Dubai",
    "абу-даби": "Asia/Dubai",
    "стамбул": "Europe/Istanbul",
    "турция": "Europe/Istanbul",
    "анталья": "Europe/Istanbul",
    "анталия": "Europe/Istanbul",
    "тбилиси": "Asia/Tbilisi",
    "грузия": "Asia/Tbilisi",
    "батуми": "Asia/Tbilisi",
    "ереван": "Asia/Yerevan",
    "армения": "Asia/Yerevan",
    "баку": "Asia/Baku",
    "азербайджан": "Asia/Baku",
    "алматы": "Asia/Almaty",
    "астана": "Asia/Almaty",
    "казахстан": "Asia/Almaty",
    "шымкент": "Asia/Almaty",
    "ташкент": "Asia/Tashkent",
    "узбекистан": "Asia/Tashkent",
    "самарканд": "Asia/Tashkent",
    "бишкек": "Asia/Bishkek",
    "кыргызстан": "Asia/Bishkek",
    "киргизия": "Asia/Bishkek",
    "пхукет": "Asia/Bangkok",
    "таиланд": "Asia/Bangkok",
    "бали": "Asia/Makassar",
    "индонезия": "Asia/Makassar",
}

OFFSET_ALIASES = {
    "gmt-5": "America/New_York",
    "utc-5": "America/New_York",
    "gmt-4": "America/New_York",
    "utc-4": "America/New_York",
    "gmt+0": "Europe/London",
    "utc+0": "Europe/London",
    "gmt+1": "Europe/Berlin",
    "utc+1": "Europe/Berlin",
    "gmt+2": "Europe/Kyiv",
    "utc+2": "Europe/Kyiv",
    "gmt+3": "Europe/Moscow",
    "utc+3": "Europe/Moscow",
    "gmt+4": "Europe/Samara",
    "utc+4": "Europe/Samara",
    "gmt+5": "Asia/Yekaterinburg",
    "utc+5": "Asia/Yekaterinburg",
    "gmt+6": "Asia/Omsk",
    "utc+6": "Asia/Omsk",
    "gmt+7": "Asia/Novosibirsk",
    "utc+7": "Asia/Novosibirsk",
    "gmt+8": "Asia/Irkutsk",
    "utc+8": "Asia/Irkutsk",
}


class TimezoneService:
    @classmethod
    def resolve_timezone_for_city(cls, city: Optional[str]) -> str:
        """
        Resolves IANA timezone name for a given city or region.
        Automatically handles summer/winter Daylight Saving Time (DST).
        Defaults to 'Europe/Moscow'.
        """
        if not city:
            return "Europe/Moscow"

        cleaned = city.strip().lower()

        # 1. Direct offset aliases (e.g. 'gmt-5', 'utc+2')
        clean_no_spaces = cleaned.replace(" ", "")
        if clean_no_spaces in OFFSET_ALIASES:
            return OFFSET_ALIASES[clean_no_spaces]

        # 2. Direct city/country map
        if cleaned in CITY_TIMEZONE_MAP:
            return CITY_TIMEZONE_MAP[cleaned]

        for c_name, tz in CITY_TIMEZONE_MAP.items():
            if c_name in cleaned or cleaned in c_name:
                return tz

        # 3. Direct IANA validation via pytz
        try:
            pytz.timezone(city)
            return city
        except Exception:
            pass

        return "Europe/Moscow"

    @staticmethod
    def parse_time_str(time_str: str) -> time:
        """Parses 'HH:MM' string to time object."""
        parts = time_str.strip().split(":")
        return time(hour=int(parts[0]), minute=int(parts[1]))

    @classmethod
    def get_local_datetime(cls, tz_name: str, ref_utc_dt: Optional[datetime] = None) -> datetime:
        """
        Gets current datetime in the specified timezone with automatic
        Daylight Saving Time (DST) adjustment for summer/winter transitions.
        """
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.timezone("Europe/Moscow")

        if ref_utc_dt is None:
            return datetime.now(tz)
        if ref_utc_dt.tzinfo is None:
            ref_utc_dt = pytz.utc.localize(ref_utc_dt)
        return ref_utc_dt.astimezone(tz)

    @classmethod
    def get_timezone_display(cls, tz_name: str, ref_utc_dt: Optional[datetime] = None) -> str:
        """
        Returns human-readable timezone string with current GMT offset and DST status.
        E.g. 'America/New_York (GMT-5, зимнее время)' or 'Europe/London (GMT+1, летнее время BST)'.
        """
        local_dt = cls.get_local_datetime(tz_name, ref_utc_dt)
        utc_offset = local_dt.utcoffset()
        total_seconds = int(utc_offset.total_seconds()) if utc_offset else 0
        sign = "+" if total_seconds >= 0 else "-"
        hours = abs(total_seconds) // 3600
        mins = (abs(total_seconds) % 3600) // 60
        gmt_str = f"GMT{sign}{hours}" if mins == 0 else f"GMT{sign}{hours}:{mins:02d}"

        is_dst = bool(local_dt.dst())
        dst_label = "летнее время" if is_dst else "зимнее / стандартное время"
        return f"{tz_name} ({gmt_str}, {dst_label})"

    @classmethod
    def is_sleeping_hours(
        cls,
        tz_name: str,
        sleep_start_str: str = "23:00",
        sleep_end_str: str = "09:00",
        ref_utc_dt: Optional[datetime] = None,
    ) -> bool:
        """
        Determines whether the current local time in tz_name falls within the base sleep window
        (default 23:00 to 09:00, ~9 hours of rest).
        """
        local_dt = cls.get_local_datetime(tz_name, ref_utc_dt)
        current_time = local_dt.time()

        start = cls.parse_time_str(sleep_start_str)
        end = cls.parse_time_str(sleep_end_str)

        if start <= end:
            return start <= current_time <= end
        else:
            return current_time >= start or current_time <= end

    @classmethod
    def analyze_dialogue_sleep_state(
        cls,
        persona_tz: str,
        base_sleep_start: str = "23:00",
        base_sleep_end: str = "09:00",
        contact_last_outgoing_at: Optional[datetime] = None,
        contact_last_incoming_at: Optional[datetime] = None,
        incoming_message: Optional[str] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
        ref_utc_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Dynamic per-dialogue sleep and bedtime analyzer:
        - Common baseline schedule: ~23:00 to 09:00 (~9h duration).
        - Daytime (09:00 - 22:30): Active and awake.
        - Late Evening (22:30 - 01:30):
          * If active engaging dialogue (recent ping-pong <= 20 min):
            She stays up chatting later (until ~23:45 - 00:15) before wrapping up!
          * If cold / dormant / new dialogue:
            She goes to sleep earlier (~23:00) and does not reply until morning.
          * If interlocutor says goodnight: wraps up immediately.
        - Deep Night (01:30 - 08:30): Deep sleep, never sends unsolicited pings or instant replies.
        - Morning Wake-up (08:45 - 09:30): Wakes up naturally, ready for morning greeting.
        """
        local_dt = cls.get_local_datetime(persona_tz, ref_utc_dt)
        float_hour = local_dt.hour + local_dt.minute / 60.0
        time_str = local_dt.strftime("%H:%M")

        cleaned_msg = (incoming_message or "").lower().strip()
        is_goodnight_msg = any(
            phrase in cleaned_msg
            for phrase in ["спокойной ночи", "сладких снов", "я спать", "пойду спать", "ложусь спать", "до завтра"]
        )

        # 1. Daytime: 09:00 - 22:30 -> Awake
        if 9.0 <= float_hour < 22.5:
            return {
                "is_sleeping": False,
                "is_winding_down": False,
                "should_say_goodnight": is_goodnight_msg,
                "local_time_str": time_str,
                "phase": "daytime",
            }

        # 2. Deep Night: 01:30 - 08:30 -> Asleep
        if 1.5 <= float_hour < 8.5:
            return {
                "is_sleeping": True,
                "is_winding_down": False,
                "should_say_goodnight": False,
                "local_time_str": time_str,
                "phase": "deep_night",
            }

        # 3. Morning wake-up window: 08:30 - 09:00
        if 8.5 <= float_hour < 9.0:
            return {
                "is_sleeping": False,
                "is_winding_down": False,
                "should_say_goodnight": False,
                "local_time_str": time_str,
                "phase": "morning_wakeup",
            }

        # 4. Late Evening / Night transition window: 22:30 - 01:30
        # Determine whether dialogue is active and engaging
        now_dt = ref_utc_dt or datetime.now(pytz.utc)
        if now_dt.tzinfo is None:
            now_dt = pytz.utc.localize(now_dt)

        is_active_dialogue = False
        if contact_last_outgoing_at:
            last_out = contact_last_outgoing_at
            if last_out.tzinfo is None:
                last_out = pytz.utc.localize(last_out)
            gap_minutes = abs((now_dt - last_out).total_seconds()) / 60.0
            if gap_minutes <= 25.0:
                is_active_dialogue = True

        if is_goodnight_msg:
            # Sincere sweet goodnight reply, then she goes to sleep
            return {
                "is_sleeping": False,
                "is_winding_down": True,
                "should_say_goodnight": True,
                "local_time_str": time_str,
                "phase": "evening_goodnight",
            }

        if is_active_dialogue:
            # Lively discussion: can stay up chatting a bit later (up to 23:45–00:15)
            if float_hour < 23.75 or (float_hour >= 22.5 and float_hour < 24.0):
                return {
                    "is_sleeping": False,
                    "is_winding_down": float_hour >= 23.0,
                    "should_say_goodnight": False,
                    "local_time_str": time_str,
                    "phase": "evening_active_chat",
                }
            elif 0.0 <= float_hour < 0.35:  # up to 00:20
                return {
                    "is_sleeping": False,
                    "is_winding_down": True,
                    "should_say_goodnight": True,
                    "local_time_str": time_str,
                    "phase": "evening_wrapping_up",
                }
            else:
                # Past 00:30: she went to sleep
                return {
                    "is_sleeping": True,
                    "is_winding_down": False,
                    "should_say_goodnight": False,
                    "local_time_str": time_str,
                    "phase": "night_sleep",
                }
        else:
            # Cold / new / dormant chat: regular bedtime around 23:00
            if float_hour >= 23.0 or float_hour < 8.5:
                return {
                    "is_sleeping": True,
                    "is_winding_down": False,
                    "should_say_goodnight": False,
                    "local_time_str": time_str,
                    "phase": "night_sleep_cold",
                }
            else:
                return {
                    "is_sleeping": False,
                    "is_winding_down": True,
                    "should_say_goodnight": False,
                    "local_time_str": time_str,
                    "phase": "evening_wind_down",
                }

    @classmethod
    def calculate_next_morning_time(
        cls, tz_name: str, wake_hour: int = 9, ref_utc_dt: Optional[datetime] = None
    ) -> datetime:
        """
        Calculates next morning wake-up time in UTC (e.g. 09:15 - 09:40 with natural jitter).
        """
        local_dt = cls.get_local_datetime(tz_name, ref_utc_dt)
        wake_minute = random.randint(12, 38)
        
        target_local = local_dt.replace(hour=wake_hour, minute=wake_minute, second=0, microsecond=0)
        if target_local <= local_dt:
            target_local += timedelta(days=1)

        try:
            tz = pytz.timezone(tz_name)
            if hasattr(tz, "localize"):
                # Avoid ambiguity during DST transitions
                target_local = tz.normalize(target_local)
        except Exception:
            pass

        return target_local.astimezone(pytz.utc)

    @classmethod
    def is_safe_to_ping(
        cls,
        persona_tz: str,
        persona_sleep_start: str = "23:00",
        persona_sleep_end: str = "09:00",
        contact_tz: Optional[str] = None,
        contact_sleep_start: str = "23:00",
        contact_sleep_end: str = "09:00",
        ref_utc_dt: Optional[datetime] = None,
    ) -> bool:
        """
        Strict check: Proactive messages (like 'Привет, как ты?') can ONLY be sent if:
        1. It is NOT sleep time for the persona (~23:00 - 09:00).
        2. It is NOT sleep time for the contact (if contact timezone is known).
        """
        if cls.is_sleeping_hours(persona_tz, persona_sleep_start, persona_sleep_end, ref_utc_dt):
            return False

        effective_contact_tz = contact_tz or persona_tz
        if cls.is_sleeping_hours(effective_contact_tz, contact_sleep_start, contact_sleep_end, ref_utc_dt):
            return False

        return True

    @classmethod
    def detect_city_and_timezone(cls, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Analyzes user message to detect mentions of cities or countries,
        accounting for Russian noun declensions (e.g., 'из Казани', 'в Киеве', 'в Лондоне', 'в Штатах').
        Returns (canonical_city_title, timezone_name) or (None, None).
        """
        cleaned = text.lower()
        for city_name, tz in CITY_TIMEZONE_MAP.items():
            if len(city_name) <= 2:
                pattern = r"\b" + re.escape(city_name) + r"\b"
            elif city_name.endswith(("а", "я", "ь", "е", "й")):
                stem = city_name[:-1]
                pattern = r"\b" + re.escape(stem) + r"[аяыиеюуойьех]*\b"
            else:
                stem = city_name
                pattern = r"\b" + re.escape(stem) + r"[аеуомах]*\b"

            if re.search(pattern, cleaned):
                canonical = city_name.capitalize()
                if city_name == "санкт-петербург":
                    canonical = "Санкт-Петербург"
                elif city_name == "питер":
                    canonical = "Питер"
                elif city_name == "нижний новгород":
                    canonical = "Нижний Новгород"
                elif city_name in ("нью-йорк", "new york", "ньюйорк"):
                    canonical = "Нью-Йорк"
                elif city_name in ("сша", "штаты", "америка", "usa"):
                    canonical = "США"
                elif city_name in ("великобритания", "британия", "англия", "uk"):
                    canonical = "Великобритания"
                elif city_name in ("украина", "киев", "kyiv", "kiev"):
                    canonical = "Киев"
                elif city_name in ("дубай", "dubai", "оаэ", "эмираты", "абу-даби"):
                    canonical = "Дубай"
                return canonical, tz
        return None, None

    @classmethod
    def format_time_context(
        cls, persona_tz: str, contact_tz: Optional[str] = None, ref_utc_dt: Optional[datetime] = None
    ) -> str:
        """
        Generates prompt context describing current time and timezone for persona and contact.
        """
        persona_dt = cls.get_local_datetime(persona_tz, ref_utc_dt)
        persona_time_str = persona_dt.strftime("%H:%M")
        persona_display = cls.get_timezone_display(persona_tz, ref_utc_dt)

        effective_contact_tz = contact_tz or persona_tz
        contact_dt = cls.get_local_datetime(effective_contact_tz, ref_utc_dt)
        contact_time_str = contact_dt.strftime("%H:%M")
        contact_display = cls.get_timezone_display(effective_contact_tz, ref_utc_dt)

        if effective_contact_tz == persona_tz:
            return f"Локальное время в твоем городе: {persona_time_str} ({persona_display})."

        return (
            f"Твое локальное время: {persona_time_str} ({persona_display}). "
            f"Локальное время собеседника: {contact_time_str} ({contact_display})."
        )
