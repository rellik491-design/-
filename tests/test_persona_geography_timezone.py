import pytest
from datetime import datetime, time, timezone
import pytz

from companion.models.entities import Persona
from companion.services.timezone_service import TimezoneService, GeoLocationInfo
from companion.bot.telegram_bot_engine import TelegramBotEngine


def test_01_los_angeles_normalization_and_timezone():
    """1. Los Angeles aliases properly resolve to America/Los_Angeles, USA, California."""
    for query in ["Лос Анджелес", "Лос-Анджелес", "лос анджелес", "Los Angeles", "los angeles", "LA", "la"]:
        tz = TimezoneService.resolve_timezone_for_city(query)
        assert tz == "America/Los_Angeles", f"Failed for query '{query}'"

    geo = TimezoneService.get_geo_info("Лос Анджелес")
    assert geo is not None
    assert geo.canonical_city == "Лос-Анджелес"
    assert geo.country == "США"
    assert geo.region == "Калифорния"
    assert geo.timezone == "America/Los_Angeles"


def test_02_strip_contaminated_timezone_from_city():
    """2. Stripping contaminated (Europe/Moscow) and setting correct geography."""
    raw = "Лос Анджелес, Уест-Голливуд (Europe/Moscow)"
    cleaned = TimezoneService.parse_and_clean_location(raw)

    assert cleaned["city"] == "Лос-Анджелес"
    assert cleaned["district"] == "Уэст-Голливуд"
    assert cleaned["country"] == "США"
    assert cleaned["region"] == "Калифорния"
    assert cleaned["timezone_name"] == "America/Los_Angeles"
    assert "Europe/Moscow" not in cleaned["city"]
    assert "Europe/Moscow" not in cleaned["district"]
    assert "(" not in cleaned["city"]
    assert "(" not in cleaned["district"]


def test_03_london_hampstead_timezone():
    """3. London, Hampstead (Europe/London) cleanly separated."""
    raw = "London, Hampstead (Europe/London)"
    cleaned = TimezoneService.parse_and_clean_location(raw)

    assert cleaned["city"] == "London"
    assert cleaned["district"] == "Hampstead"
    assert cleaned["country"] == "Великобритания"
    assert cleaned["timezone_name"] == "Europe/London"
    assert "Europe/London" not in cleaned["city"]
    assert "Europe/London" not in cleaned["district"]


def test_04_manual_sleep_schedule_midnight_crossover_los_angeles():
    """4. Local sleep schedule in Los Angeles (23:00 - 08:30) evaluated at 4 critical points."""
    la_tz = pytz.timezone("America/Los_Angeles")
    # Winter date (standard time PST)
    base_date = datetime(2026, 1, 15)

    # 1. 23:30 LA time -> Sleeping (True)
    dt_2330 = la_tz.localize(datetime(2026, 1, 15, 23, 30, 0))
    assert TimezoneService.is_persona_sleeping("America/Los_Angeles", "23:00", "08:30", dt_2330) is True

    # 2. 02:00 LA time -> Sleeping (True)
    dt_0200 = la_tz.localize(datetime(2026, 1, 16, 2, 0, 0))
    assert TimezoneService.is_persona_sleeping("America/Los_Angeles", "23:00", "08:30", dt_0200) is True

    # 3. 08:30 LA time -> Awake / Waking up (False)
    dt_0830 = la_tz.localize(datetime(2026, 1, 16, 8, 30, 0))
    assert TimezoneService.is_persona_sleeping("America/Los_Angeles", "23:00", "08:30", dt_0830) is False

    # 4. 15:00 LA time -> Awake (False)
    dt_1500 = la_tz.localize(datetime(2026, 1, 16, 15, 0, 0))
    assert TimezoneService.is_persona_sleeping("America/Los_Angeles", "23:00", "08:30", dt_1500) is False


def test_05_changing_city_to_london_preserves_sleep_hours_in_new_timezone():
    """5. Moving persona from LA to London keeps 23:00-08:30 sleep hours, evaluated in London time."""
    persona = Persona(
        name="Карина",
        city="Лос-Анджелес",
        timezone_name="America/Los_Angeles",
        sleep_start="23:00",
        sleep_end="08:30",
        timezone_source="AUTO",
    )

    # Move to London
    geo = TimezoneService.parse_and_clean_location("London, Hampstead")
    persona.city = geo["city"]
    persona.district = geo["district"]
    persona.country = geo["country"]
    persona.timezone_name = geo["timezone_name"]

    assert persona.timezone_name == "Europe/London"
    assert persona.sleep_start == "23:00"
    assert persona.sleep_end == "08:30"

    london_tz = pytz.timezone("Europe/London")
    # At 02:00 London time: she is sleeping in London
    dt_london_0200 = london_tz.localize(datetime(2026, 1, 16, 2, 0, 0))
    assert TimezoneService.is_persona_sleeping(persona.timezone_name, persona.sleep_start, persona.sleep_end, dt_london_0200) is True

    # Note that at 02:00 London time, in Los Angeles it is 18:00 (awake in LA).
    # Since she lives in London, she is asleep!
    dt_utc = dt_london_0200.astimezone(pytz.utc)
    assert TimezoneService.is_persona_sleeping("America/Los_Angeles", "23:00", "08:30", dt_utc) is False
    assert TimezoneService.is_persona_sleeping(persona.timezone_name, persona.sleep_start, persona.sleep_end, dt_utc) is True


def test_06_sleep_schedule_validation_and_parsing():
    """6. Validates HH:MM parsing and same-day vs overnight intervals."""
    # Overnight
    assert TimezoneService.parse_time_str("23:00") == time(23, 0)
    assert TimezoneService.parse_time_str("08:30") == time(8, 30)

    # Same day window: 00:00 to 08:00
    tz = "Europe/Moscow"
    msk = pytz.timezone(tz)
    dt_night = msk.localize(datetime(2026, 1, 1, 3, 0))
    dt_day = msk.localize(datetime(2026, 1, 1, 12, 0))

    assert TimezoneService.is_persona_sleeping(tz, "00:00", "08:00", dt_night) is True
    assert TimezoneService.is_persona_sleeping(tz, "00:00", "08:00", dt_day) is False


def test_07_validate_persona_location():
    """7. validate_persona_location rejects timezone in city/district and verifies IANA timezone."""
    # Valid location
    validated = TimezoneService.validate_persona_location(
        city="Лос-Анджелес",
        district="Уэст-Голливуд",
        country="США",
        region="Калифорния",
        timezone_name="America/Los_Angeles",
    )
    assert validated["city"] == "Лос-Анджелес"
    assert validated["timezone_name"] == "America/Los_Angeles"

    # Leak in city -> ValueError
    with pytest.raises(ValueError, match="Timezone identifier detected"):
        TimezoneService.validate_persona_location(city="Лос-Анджелес (Europe/Moscow)", timezone_name="America/Los_Angeles")

    # Continent in city -> ValueError
    with pytest.raises(ValueError, match="Continent name detected"):
        TimezoneService.validate_persona_location(city="Лос-Анджелес, Северная Америка", timezone_name="America/Los_Angeles")

    # Invalid IANA timezone -> ValueError
    with pytest.raises(ValueError, match="Invalid IANA timezone"):
        TimezoneService.validate_persona_location(city="Москва", timezone_name="Fake/NonExistent_Zone")


def test_08_server_vs_persona_timezone_separation():
    """8. Sleep schedule evaluated on persona local time, regardless of server UTC/MSK time."""
    # Persona in Los Angeles
    # At 02:00 LA time (PST = UTC-8), UTC time is 10:00, MSK time is 13:00 (midday!).
    utc_dt = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    # In MSK time it is 13:00 (awake), but in persona's LA time it is 02:00 (asleep!)
    assert TimezoneService.is_persona_sleeping("Europe/Moscow", "23:00", "08:30", utc_dt) is False
    assert TimezoneService.is_persona_sleeping("America/Los_Angeles", "23:00", "08:30", utc_dt) is True


def test_09_origin_vs_current_location_separation():
    """9. Origin birth city (Харьков) kept separate from current location (Лос-Анджелес)."""
    persona = Persona(
        name="Дарья",
        birth_city="Харьков",
        city="Лос-Анджелес",
        district="Уэст-Голливуд",
        country="США",
        region="Калифорния",
        timezone_name="America/Los_Angeles",
    )
    assert persona.birth_city == "Харьков"
    assert persona.city == "Лос-Анджелес"
    assert persona.country == "США"
    assert persona.region == "Калифорния"


def test_10_daylight_saving_time_automatic_transitions():
    """10. Automatic Summer (PDT) vs Winter (PST) in Los Angeles."""
    summer_utc = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    winter_utc = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    la_summer = TimezoneService.get_timezone_display("America/Los_Angeles", summer_utc)
    la_winter = TimezoneService.get_timezone_display("America/Los_Angeles", winter_utc)

    assert "GMT-7" in la_summer
    assert "летнее время" in la_summer
    assert "GMT-8" in la_winter
    assert "зимнее" in la_winter


def test_11_format_legend_card_clean_separation():
    """11. format_legend_card renders clean separate lines without leaked timezone in city."""
    engine = TelegramBotEngine(token="mock_token", admin_id=123)
    persona = Persona(
        name="Дарья",
        age=25,
        city="Лос-Анджелес",
        district="Уэст-Голливуд",
        country="США",
        region="Калифорния",
        birth_city="Харьков",
        timezone_name="America/Los_Angeles",
        sleep_start="23:00",
        sleep_end="08:30",
    )

    card = engine.format_legend_card(persona)

    # Must contain clean city & district
    assert "• <b>Город и район:</b> Лос-Анджелес, Уэст-Голливуд" in card
    # Must NOT have (Europe/Moscow) or (America/Los_Angeles) inside city line
    assert "(Europe/Moscow)" not in card
    assert "Лос-Анджелес, Уэст-Голливуд (" not in card

    # Must have dedicated country and region line
    assert "• <b>Страна / регион:</b> США, Калифорния" in card

    # Must have dedicated timezone line
    assert "• <b>Часовой пояс:</b> 🕐 <code>America/Los_Angeles</code>" in card

    # Must have dedicated origin line
    assert "• <b>Откуда родом:</b> Харьков" in card

    # Must have local sleep notice
    assert "• <b>Режим сна:</b> 🌙 23:00 – 08:30 <i>(по местному времени)</i>" in card
