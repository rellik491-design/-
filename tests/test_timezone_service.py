from datetime import datetime, timezone
import pytest
from companion.services.timezone_service import TimezoneService


def test_detect_city_and_timezone():
    city, tz = TimezoneService.detect_city_and_timezone("Привет! Я сам из Екатеринбурга")
    assert city == "Екатеринбург"
    assert tz == "Asia/Yekaterinburg"

    city2, tz2 = TimezoneService.detect_city_and_timezone("Вчера гулял в Питере, красиво очень")
    assert city2 == "Питер"
    assert tz2 == "Europe/Moscow"

    city3, tz3 = TimezoneService.detect_city_and_timezone("Привет из Владивостока!")
    assert city3 == "Владивосток"
    assert tz3 == "Asia/Vladivostok"

    city4, tz4 = TimezoneService.detect_city_and_timezone("Я сейчас живу и учусь в Монреале")
    assert city4 == "Монреаль"
    assert tz4 == "America/Montreal"

    assert TimezoneService.resolve_timezone_for_city("Монреаль") == "America/Montreal"
    assert TimezoneService.resolve_timezone_for_city("Москва") == "Europe/Moscow"
    assert TimezoneService.resolve_timezone_for_city("Дубай") == "Asia/Dubai"
    assert TimezoneService.resolve_timezone_for_city("Нью-Йорк") == "America/New_York"
    assert TimezoneService.resolve_timezone_for_city("Великобритания") == "Europe/London"
    assert TimezoneService.resolve_timezone_for_city("Киев") == "Europe/Kyiv"
    assert TimezoneService.resolve_timezone_for_city("GMT-5") == "America/New_York"
    assert TimezoneService.resolve_timezone_for_city("GMT+0") == "Europe/London"
    assert TimezoneService.resolve_timezone_for_city("GMT+2") == "Europe/Kyiv"

    city_none, tz_none = TimezoneService.detect_city_and_timezone("Просто сижу пью чай")
    assert city_none is None
    assert tz_none is None

    # Test declension detection for various CIS & international cities
    city_dubai, tz_dubai = TimezoneService.detect_city_and_timezone("я сейчас в Дубае отдыхаю")
    assert city_dubai == "Дубай"
    assert tz_dubai == "Asia/Dubai"

    city_kiev, tz_kiev = TimezoneService.detect_city_and_timezone("я сам из Киева вообще")
    assert city_kiev == "Киев"
    assert tz_kiev == "Europe/Kyiv"

    city_warsaw, tz_warsaw = TimezoneService.detect_city_and_timezone("уже год живу в Варшаве")
    assert city_warsaw == "Варшава"
    assert tz_warsaw == "Europe/Warsaw"

    # Ensure humorous/non-city replies return None and never hallucinate
    city_joke, tz_joke = TimezoneService.detect_city_and_timezone("убираюсь в туалетах нарколаборатории, а кем ты работаешь голова?")
    assert city_joke is None
    assert tz_joke is None


def test_ai_service_geo_context_and_no_city_hallucination():
    from companion.models import Contact, Persona
    from companion.services.ai_service import AIService

    persona = Persona(name="Алина", age=22, city="Москва")
    
    # 1. Contact without known city: Must forbid hallucinating cities and instruct to ask
    contact_unknown = Contact(tg_id=777, first_name="Алекс", detected_city=None)
    prompt_unknown = AIService.build_system_prompt(persona, contact_unknown, "привет")
    assert "Город проживания собеседника: НЕИЗВЕСТЕН" in prompt_unknown
    assert "КАТЕГОРИЧЕСКИЙ СТРОЖАЙШИЙ ЗАПРЕТ: НИ В КОЕМ СЛУЧАЕ НЕ ПРИДУМЫВАЙ" in prompt_unknown
    assert "Строжайше запрещено называть Нью-Йорк" in prompt_unknown
    assert "откуда он родом и где живет сейчас" in prompt_unknown

    # 2. Contact with confirmed city: Must state confirmed city
    contact_known = Contact(tg_id=888, first_name="Максим", detected_city="Киев", detected_timezone="Europe/Kyiv")
    prompt_known = AIService.build_system_prompt(persona, contact_known, "привет")
    assert "Подтвержденный город проживания собеседника: Киев" in prompt_known
    assert "Статус города: ПОДТВЕРЖДЕН" in prompt_known
    assert "Подстраивайся под его локацию Киев" in prompt_known



def test_is_sleeping_hours_overnight():
    # 23:00 to 09:00 sleep schedule (~9 hours)
    # 02:00 in Moscow (UTC+3) -> UTC 23:00 (previous day)
    utc_night = datetime(2026, 10, 25, 23, 0, 0, tzinfo=timezone.utc)
    assert TimezoneService.is_sleeping_hours("Europe/Moscow", "23:00", "09:00", utc_night) is True

    # 14:00 in Moscow (UTC+3) -> UTC 11:00
    utc_day = datetime(2026, 10, 25, 11, 0, 0, tzinfo=timezone.utc)
    assert TimezoneService.is_sleeping_hours("Europe/Moscow", "23:00", "09:00", utc_day) is False

    # 08:15 in Moscow (still sleeping) -> UTC 05:15
    utc_early = datetime(2026, 10, 25, 5, 15, 0, tzinfo=timezone.utc)
    assert TimezoneService.is_sleeping_hours("Europe/Moscow", "23:00", "09:00", utc_early) is True

    # 09:15 in Moscow (awake) -> UTC 06:15
    utc_awake = datetime(2026, 10, 25, 6, 15, 0, tzinfo=timezone.utc)
    assert TimezoneService.is_sleeping_hours("Europe/Moscow", "23:00", "09:00", utc_awake) is False


def test_dynamic_per_dialogue_bedtime():
    import pytz
    from datetime import timedelta

    msk_tz = pytz.timezone("Europe/Moscow")
    # 23:15 MSK (late evening)
    evening_dt = msk_tz.localize(datetime(2026, 11, 12, 23, 15, 0))

    # Case 1: Active dialogue (partner sent message 5 minutes ago)
    state_active = TimezoneService.analyze_dialogue_sleep_state(
        persona_tz="Europe/Moscow",
        contact_last_outgoing_at=evening_dt - timedelta(minutes=5),
        ref_utc_dt=evening_dt,
    )
    assert state_active["is_sleeping"] is False
    assert state_active["phase"] == "evening_active_chat"

    # Case 2: Cold/dormant dialogue (last outgoing was 2 hours ago)
    state_cold = TimezoneService.analyze_dialogue_sleep_state(
        persona_tz="Europe/Moscow",
        contact_last_outgoing_at=evening_dt - timedelta(hours=2),
        ref_utc_dt=evening_dt,
    )
    assert state_cold["is_sleeping"] is True
    assert state_cold["phase"] == "night_sleep_cold"

    # Case 3: Goodnight message received
    state_gn = TimezoneService.analyze_dialogue_sleep_state(
        persona_tz="Europe/Moscow",
        incoming_message="спокойной ночи, сладких снов",
        ref_utc_dt=evening_dt,
    )
    assert state_gn["should_say_goodnight"] is True
    assert state_gn["is_sleeping"] is False


def test_daylight_saving_time_transitions():
    import pytz

    # London: Summer (BST = GMT+1), Winter (GMT = GMT+0)
    summer_dt = datetime(2026, 7, 1, 12, 0, 0, tzinfo=pytz.utc)
    winter_dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=pytz.utc)

    london_summer = TimezoneService.get_timezone_display("Europe/London", summer_dt)
    london_winter = TimezoneService.get_timezone_display("Europe/London", winter_dt)
    assert "GMT+1" in london_summer
    assert "GMT+0" in london_winter

    # New York: Summer (EDT = GMT-4), Winter (EST = GMT-5)
    ny_summer = TimezoneService.get_timezone_display("America/New_York", summer_dt)
    ny_winter = TimezoneService.get_timezone_display("America/New_York", winter_dt)
    assert "GMT-4" in ny_summer
    assert "GMT-5" in ny_winter


def test_is_safe_to_ping_both_parties():
    # Scenario: Persona is in Moscow (UTC+3), Contact is in Vladivostok (UTC+10)
    # When it's 18:00 in Moscow -> it is 01:00 (next day) in Vladivostok!
    # Persona is awake, but Contact is sleeping!
    # Ping MUST NOT be sent!
    utc_time = datetime(2026, 10, 25, 15, 0, 0, tzinfo=timezone.utc) # 18:00 MSK, 01:00 VVO

    safe = TimezoneService.is_safe_to_ping(
        persona_tz="Europe/Moscow",
        contact_tz="Asia/Vladivostok",
        ref_utc_dt=utc_time,
    )
    assert safe is False, "Should be unsafe to ping when interlocutor is sleeping in Vladivostok"
