"""
TimeEngine: Deterministic real-time clock and relative date resolution service.
Provides exact timestamp, timezone awareness, and absolute resolution for
relative temporal expressions ('tomorrow', 'yesterday', 'in 2 hours', 'next Monday',
midnight boundaries).
Prevents LLM from guessing time or calculating dates.
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple
import pytz

from companion.services.timezone_service import TimezoneService


class TimeEngine:
    """
    Deterministic Time & Date Engine (Sections 21–24 & 55).
    Provides:
    - Real-time clock with timezone resolution.
    - Priority-based timezone selection:
      explicit user timezone -> contact profile timezone -> persona/chat timezone -> configured default.
    - Deterministic resolution of relative dates:
      'завтра', 'вчера', 'послезавтра', 'позавчера', 'через 2 часа', 'следующий понедельник',
      midnight transitions.
    """

    WEEKDAY_RU = {
        0: "понедельник",
        1: "вторник",
        2: "среда",
        3: "четверг",
        4: "пятница",
        5: "суббота",
        6: "воскресенье",
    }

    WEEKDAY_NAME_TO_INT = {
        "понедельник": 0,
        "вторник": 1,
        "среда": 2,
        "среду": 2,
        "четверг": 3,
        "пятница": 4,
        "пятницу": 4,
        "суббота": 5,
        "субботу": 5,
        "воскресенье": 6,
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }

    @classmethod
    def resolve_effective_timezone(
        cls,
        explicit_tz: Optional[str] = None,
        contact_tz: Optional[str] = None,
        chat_or_persona_tz: Optional[str] = None,
        default_tz: str = "Europe/Moscow",
    ) -> str:
        """
        Priority-based timezone selection (Section 22):
        1. explicit user timezone
        2. known user profile / contact timezone
        3. chat / persona account timezone
        4. configured default timezone
        """
        for tz in (explicit_tz, contact_tz, chat_or_persona_tz):
            if tz and tz.strip():
                try:
                    pytz.timezone(tz.strip())
                    return tz.strip()
                except Exception:
                    resolved = TimezoneService.resolve_timezone_for_city(tz.strip())
                    if resolved:
                        return resolved
        return default_tz

    @classmethod
    def get_clock(
        cls,
        tz_name: str = "Europe/Moscow",
        ref_utc_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Deterministic real-time clock tool (Section 21).
        Returns exact timestamp, ISO string, timezone, local date, local time, day of week.
        """
        ref = ref_utc_dt or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = pytz.utc.localize(ref)

        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.timezone("Europe/Moscow")

        local_dt = ref.astimezone(tz)
        weekday_idx = local_dt.weekday()
        weekday_name = cls.WEEKDAY_RU.get(weekday_idx, "сегодня")

        hour = local_dt.hour
        is_day = 8 <= hour < 22
        is_night = hour >= 23 or hour < 7
        is_morning = 7 <= hour < 12
        is_evening = 18 <= hour < 23

        time_of_day = "утро" if is_morning else ("день" if (12 <= hour < 18) else ("вечер" if is_evening else "ночь"))

        return {
            "timestamp_utc": int(ref.timestamp()),
            "iso_utc": ref.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timezone": tz_name,
            "local_datetime": local_dt,
            "local_date": local_dt.strftime("%Y-%m-%d"),
            "local_time": local_dt.strftime("%H:%M"),
            "day_of_week": weekday_name,
            "weekday_idx": weekday_idx,
            "year": local_dt.year,
            "month": local_dt.month,
            "day": local_dt.day,
            "is_day": is_day,
            "is_night": is_night,
            "time_of_day": time_of_day,
        }

    @classmethod
    def resolve_relative_date(
        cls,
        expression: str,
        tz_name: str = "Europe/Moscow",
        ref_utc_dt: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Converts relative dates & times deterministically (Sections 23, 24, 55):
        - 'завтра' / 'tomorrow' -> exact YYYY-MM-DD
        - 'вчера' / 'yesterday' -> exact YYYY-MM-DD
        - 'послезавтра' / 'day after tomorrow' -> exact YYYY-MM-DD
        - 'позавчера' -> exact YYYY-MM-DD
        - 'через N часа/часов/минут' / 'in N hours' -> exact YYYY-MM-DD HH:MM
        - 'следующий понедельник/вторник/...' -> exact YYYY-MM-DD
        - Handles midnight boundaries cleanly based on target timezone.
        """
        if not expression:
            return None

        clock = cls.get_clock(tz_name=tz_name, ref_utc_dt=ref_utc_dt)
        local_now: datetime = clock["local_datetime"]
        expr_low = expression.strip().lower()

        # 1. "Завтра" / "Tomorrow"
        if re.search(r"\b(?:завтра|tomorrow)\b", expr_low):
            target_dt = local_now + timedelta(days=1)
            return {
                "matched_expression": "завтра",
                "resolved_date": target_dt.strftime("%Y-%m-%d"),
                "resolved_datetime": target_dt.strftime("%Y-%m-%d %H:%M"),
                "day_of_week": cls.WEEKDAY_RU.get(target_dt.weekday(), ""),
                "offset_days": 1,
            }

        # 2. "Послезавтра" / "Day after tomorrow"
        if re.search(r"\b(?:послезавтра|day\s+after\s+tomorrow)\b", expr_low):
            target_dt = local_now + timedelta(days=2)
            return {
                "matched_expression": "послезавтра",
                "resolved_date": target_dt.strftime("%Y-%m-%d"),
                "resolved_datetime": target_dt.strftime("%Y-%m-%d %H:%M"),
                "day_of_week": cls.WEEKDAY_RU.get(target_dt.weekday(), ""),
                "offset_days": 2,
            }

        # 3. "Вчера" / "Yesterday"
        if re.search(r"\b(?:вчера|yesterday)\b", expr_low):
            target_dt = local_now - timedelta(days=1)
            return {
                "matched_expression": "вчера",
                "resolved_date": target_dt.strftime("%Y-%m-%d"),
                "resolved_datetime": target_dt.strftime("%Y-%m-%d %H:%M"),
                "day_of_week": cls.WEEKDAY_RU.get(target_dt.weekday(), ""),
                "offset_days": -1,
            }

        # 4. "Позавчера"
        if re.search(r"\b(?:позавчера)\b", expr_low):
            target_dt = local_now - timedelta(days=2)
            return {
                "matched_expression": "позавчера",
                "resolved_date": target_dt.strftime("%Y-%m-%d"),
                "resolved_datetime": target_dt.strftime("%Y-%m-%d %H:%M"),
                "day_of_week": cls.WEEKDAY_RU.get(target_dt.weekday(), ""),
                "offset_days": -2,
            }

        # 5. "Через N часов" / "in N hours"
        m_hours = re.search(r"\b(?:через|in)\s+(\d+)\s*(?:час|часа|часов|hours?)\b", expr_low)
        if m_hours:
            hours = int(m_hours.group(1))
            target_dt = local_now + timedelta(hours=hours)
            return {
                "matched_expression": f"через {hours} ч.",
                "resolved_date": target_dt.strftime("%Y-%m-%d"),
                "resolved_datetime": target_dt.strftime("%Y-%m-%d %H:%M"),
                "resolved_time": target_dt.strftime("%H:%M"),
                "day_of_week": cls.WEEKDAY_RU.get(target_dt.weekday(), ""),
                "offset_hours": hours,
            }

        # 6. "Через N минут" / "in N minutes"
        m_mins = re.search(r"\b(?:через|in)\s+(\d+)\s*(?:минут|минуты|минуту|mins?|minutes?)\b", expr_low)
        if m_mins:
            mins = int(m_mins.group(1))
            target_dt = local_now + timedelta(minutes=mins)
            return {
                "matched_expression": f"через {mins} мин.",
                "resolved_date": target_dt.strftime("%Y-%m-%d"),
                "resolved_datetime": target_dt.strftime("%Y-%m-%d %H:%M"),
                "resolved_time": target_dt.strftime("%H:%M"),
                "day_of_week": cls.WEEKDAY_RU.get(target_dt.weekday(), ""),
                "offset_minutes": mins,
            }

        # 7. Next specific weekday: "в следующий понедельник" / "в субботу"
        m_wday = re.search(
            r"\b(?:в|во|следующ\w*|next)\s+([а-яёa-z]+)\b",
            expr_low,
        )
        if m_wday:
            raw_wday = m_wday.group(1).lower()
            if raw_wday in cls.WEEKDAY_NAME_TO_INT:
                target_wday = cls.WEEKDAY_NAME_TO_INT[raw_wday]
                curr_wday = local_now.weekday()
                days_ahead = (target_wday - curr_wday) % 7
                if days_ahead == 0:
                    days_ahead = 7  # Next week
                target_dt = local_now + timedelta(days=days_ahead)
                return {
                    "matched_expression": f"в {raw_wday}",
                    "resolved_date": target_dt.strftime("%Y-%m-%d"),
                    "resolved_datetime": target_dt.strftime("%Y-%m-%d %H:%M"),
                    "day_of_week": cls.WEEKDAY_RU.get(target_dt.weekday(), ""),
                    "offset_days": days_ahead,
                }

        return None

    @classmethod
    def format_time_awareness_prompt(
        cls,
        tz_name: str = "Europe/Moscow",
        ref_utc_dt: Optional[datetime] = None,
        incoming_message: Optional[str] = None,
    ) -> str:
        """
        Builds a deterministic, complete time-awareness block for prompt injection (Section 21).
        Contains exact current time, day of week, date, and pre-resolved relative dates.
        """
        clock = cls.get_clock(tz_name=tz_name, ref_utc_dt=ref_utc_dt)
        local_date = clock["local_date"]
        local_time = clock["local_time"]
        day_of_week = clock["day_of_week"]
        time_of_day = clock["time_of_day"]

        tomorrow_dt = clock["local_datetime"] + timedelta(days=1)
        yesterday_dt = clock["local_datetime"] - timedelta(days=1)
        tomorrow_date = tomorrow_dt.strftime("%Y-%m-%d")
        yesterday_date = yesterday_dt.strftime("%Y-%m-%d")
        tomorrow_day = cls.WEEKDAY_RU.get(tomorrow_dt.weekday(), "")

        relative_note = ""
        if incoming_message:
            resolved = cls.resolve_relative_date(incoming_message, tz_name=tz_name, ref_utc_dt=ref_utc_dt)
            if resolved:
                relative_note = (
                    f"\n• ДЕТЕРМИНИРОВАННЫЙ РАСЧЕТ ДАТЫ ДЛЯ «{resolved['matched_expression']}»: "
                    f"Точная дата: {resolved['resolved_date']} ({resolved['day_of_week']})"
                    + (f", точное время: {resolved['resolved_time']}" if "resolved_time" in resolved else "")
                )

        return (
            f"[ДЕТЕРМИНИРОВАННЫЙ РЕАЛЬНЫЙ СИСТЕМНЫЙ ТАЙМЕР]:\n"
            f"• Точное локальное время: {local_time} ({time_of_day})\n"
            f"• Точная текущая дата: {local_date}\n"
            f"• День недели: {day_of_week}\n"
            f"• Часовой пояс: {tz_name}\n"
            f"• Вчера было: {yesterday_date}, завтра будет: {tomorrow_date} ({tomorrow_day})"
            f"{relative_note}\n"
            f"СТРОЖАЙШЕЕ ПРАВИЛО: Ошибаться во времени, дате и дне недели категорически запрещено!"
        )
