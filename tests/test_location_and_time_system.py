from datetime import datetime, timezone
import pytest
import pytz

from companion.models import Contact, Persona
from companion.services.location_context_service import (
    LocationContextService,
    PersonaLocationProfile,
    CityKnowledgeCatalog,
    CountryKnowledgeCatalog,
)
from companion.services.ai_service import AIService


def test_persona_location_profile_comes_strictly_from_persona():
    # Section 1: Location comes from bot persona, never guessed, never inferred from user
    persona = Persona(
        name="Карина",
        city="Kyiv",
        country="Ukraine",
        country_code="UA",
        timezone_name="Europe/Kyiv",
    )
    loc = LocationContextService.get_persona_location(persona)
    assert loc.city == "Kyiv"
    assert loc.country == "Ukraine"
    assert loc.country_code == "UA"
    assert loc.timezone == "Europe/Kyiv"
    assert loc.temporary is False

    loc_dict = loc.to_dict()
    assert "persona_location" in loc_dict
    assert loc_dict["persona_location"]["city"] == "Kyiv"
    assert loc_dict["persona_location"]["timezone"] == "Europe/Kyiv"


def test_timezone_derived_from_location_and_dst():
    # Section 2 & 15: IANA timezone derived from location with dynamic DST
    summer_ref = datetime(2026, 7, 15, 12, 0, 0, tzinfo=pytz.utc)
    winter_ref = datetime(2026, 1, 15, 12, 0, 0, tzinfo=pytz.utc)

    persona_kyiv = Persona(city="Kyiv", timezone_name="Europe/Kyiv")
    ctx_summer = LocationContextService.build_time_context(persona_kyiv, ref_utc_dt=summer_ref)
    ctx_winter = LocationContextService.build_time_context(persona_kyiv, ref_utc_dt=winter_ref)

    # Kyiv is EEST (UTC+3) in summer, EET (UTC+2) in winter
    assert ctx_summer.utc_offset == "+03:00"
    assert ctx_winter.utc_offset == "+02:00"
    assert ctx_summer.timezone == "Europe/Kyiv"


def test_never_stores_current_time_as_persistent_memory():
    # Section 3: Verify Persona does NOT store a static current time string
    persona = Persona(city="Москва", timezone_name="Europe/Moscow")
    assert not hasattr(persona, "current_time")
    
    # Dynamic calculation at two different times
    t1 = datetime(2026, 11, 14, 10, 0, 0, tzinfo=pytz.utc)
    t2 = datetime(2026, 11, 14, 15, 30, 0, tzinfo=pytz.utc)

    ctx1 = LocationContextService.build_time_context(persona, ref_utc_dt=t1)
    ctx2 = LocationContextService.build_time_context(persona, ref_utc_dt=t2)

    assert ctx1.persona_local_time == "13:00"  # Moscow is UTC+3
    assert ctx2.persona_local_time == "18:30"


def test_time_context_day_periods_and_weekend():
    # Section 4 & 5: Conversational day periods and weekend calculation
    # 05:00–10:59 → morning
    # 11:00–16:59 → daytime
    # 17:00–21:59 → evening
    # 22:00–00:59 → late evening
    # 01:00–04:59 → night

    assert LocationContextService.calculate_day_period(6) == "morning"
    assert LocationContextService.calculate_day_period(10) == "morning"
    assert LocationContextService.calculate_day_period(11) == "daytime"
    assert LocationContextService.calculate_day_period(14) == "daytime"
    assert LocationContextService.calculate_day_period(16) == "daytime"
    assert LocationContextService.calculate_day_period(17) == "evening"
    assert LocationContextService.calculate_day_period(21) == "evening"
    assert LocationContextService.calculate_day_period(22) == "late evening"
    assert LocationContextService.calculate_day_period(0) == "late evening"
    assert LocationContextService.calculate_day_period(1) == "night"
    assert LocationContextService.calculate_day_period(3) == "night"
    assert LocationContextService.calculate_day_period(4) == "night"

    # Test full TimeContext for a Saturday late evening in Kyiv
    # 2026-11-14 21:41 UTC is 23:41 in Kyiv (UTC+2) -> Saturday, late evening, weekend=True
    ref_sat_night = datetime(2026, 11, 14, 21, 41, 0, tzinfo=pytz.utc)
    persona = Persona(city="Kyiv", country="Ukraine", timezone_name="Europe/Kyiv")
    ctx = LocationContextService.build_time_context(persona, ref_utc_dt=ref_sat_night)

    assert ctx.persona_local_time == "23:41"
    assert ctx.persona_local_date == "2026-11-14"
    assert ctx.day_of_week == "Saturday"
    assert ctx.timezone == "Europe/Kyiv"
    assert ctx.utc_offset == "+02:00"
    assert ctx.day_period == "late evening"
    assert ctx.is_weekend is True
    assert ctx.formatted_date_eng == "Saturday, November 14, 2026"


def test_persona_time_vs_user_time_separation():
    # Section 7 & 8: Persona time vs User time separate clocks & priority
    persona = Persona(city="Kyiv", country="Ukraine", timezone_name="Europe/Kyiv")
    ref_dt = datetime(2026, 11, 14, 21, 15, 0, tzinfo=pytz.utc)

    # Case 1: User timezone explicitly known (Europe/Paris)
    contact_paris = Contact(
        tg_id=12345,
        detected_city="Paris",
        detected_timezone="Europe/Paris",
        language="uk",  # Ukrainian speaker living in Paris!
    )
    ctx1 = LocationContextService.build_time_context(persona, contact_paris, ref_utc_dt=ref_dt)
    assert ctx1.persona_local_time == "23:15"
    assert ctx1.user_timezone == "Europe/Paris"
    assert ctx1.user_local_time == "22:15"
    assert ctx1.time_diff_hours == -1.0

    # Case 2: User timezone unknown (Russian speaker, but timezone not specified)
    # Section 8: NEVER guess timezone from language (Russian != Russia, Ukrainian != Ukraine)
    contact_unknown = Contact(
        tg_id=67890,
        detected_city=None,
        detected_timezone=None,
        language="ru",
    )
    ctx2 = LocationContextService.build_time_context(persona, contact_unknown, ref_utc_dt=ref_dt)
    assert ctx2.persona_local_time == "23:15"
    assert ctx2.user_timezone == "unknown"
    assert ctx2.user_local_time is None


def test_persona_routine_schedule():
    # Section 10: Persona routine / schedule
    persona = Persona(
        city="Москва",
        wake_time="09:00",
        sleep_time="23:30",
        work_start="10:00",
        work_end="18:00",
    )
    routine = LocationContextService.get_persona_routine(persona)
    assert routine.wake_time == "09:00"
    assert routine.sleep_time == "23:30"
    assert routine.work_start == "10:00"
    assert routine.work_end == "18:00"

    routine_dict = routine.to_dict()
    assert routine_dict["routine"]["wake_time"] == "09:00"


def test_city_and_country_knowledge_catalog():
    # Section 11 & 12: Basic city & country factual profiles
    kyiv_prof = CityKnowledgeCatalog.get_city_profile("Kyiv")
    assert kyiv_prof.name == "Kyiv"
    assert kyiv_prof.country == "Ukraine"
    assert kyiv_prof.currency_code == "UAH"
    assert "Dnieper" in kyiv_prof.known_for or "Podil" in kyiv_prof.known_for
    assert "Печерск" in kyiv_prof.districts
    assert "Крещатик" in kyiv_prof.landmarks

    moscow_prof = CityKnowledgeCatalog.get_city_profile("Москва")
    assert moscow_prof.country == "Россия"
    assert moscow_prof.currency_code == "RUB"
    assert "Патриаршие пруды" in moscow_prof.landmarks

    warsaw_prof = CityKnowledgeCatalog.get_city_profile("Варшава")
    assert warsaw_prof.currency_code == "PLN"
    assert "Wisła" in warsaw_prof.landmarks

    # Country catalog
    ua_country = CountryKnowledgeCatalog.get_country_profile("Ukraine")
    assert ua_country.capital == "Kyiv"
    assert ua_country.currency_code == "UAH"
    assert "Ukrainian" in ua_country.language_context


def test_temporary_trip_location():
    # Section 15: Temporary trip support
    persona = Persona(
        name="Алина",
        city="Kyiv",
        country="Ukraine",
        country_code="UA",
        timezone_name="Europe/Kyiv",
    )

    # Home location initially
    loc_home = LocationContextService.get_persona_location(persona)
    assert loc_home.city == "Kyiv"
    assert loc_home.temporary is False

    # Start temporary trip to Warsaw
    persona.set_temporary_trip("Warsaw", "Poland")
    loc_trip = LocationContextService.get_persona_location(persona)
    assert loc_trip.city == "Warsaw"
    assert loc_trip.country == "Poland"
    assert loc_trip.timezone == "Europe/Warsaw"
    assert loc_trip.temporary is True
    assert loc_trip.home_location is not None
    assert loc_trip.home_location["city"] == "Kyiv"

    # Clear trip
    persona.clear_temporary_trip()
    loc_back = LocationContextService.get_persona_location(persona)
    assert loc_back.city == "Kyiv"
    assert loc_back.temporary is False


def test_section_17_response_context_prompt_injection():
    # Section 17: Compact response context block
    persona = Persona(
        name="Карина",
        city="Kyiv",
        country="Ukraine",
        country_code="UA",
        timezone_name="Europe/Kyiv",
    )
    contact = Contact(tg_id=999, first_name="Денис")
    ref_dt = datetime(2026, 11, 14, 21, 41, 0, tzinfo=pytz.utc)

    block = LocationContextService.build_response_context_block(persona, contact, ref_utc_dt=ref_dt)
    
    # Exact required labels from Section 17
    assert "PERSONA LOCATION:\nKyiv, Ukraine" in block
    assert "PERSONA TIMEZONE:\nEurope/Kyiv" in block
    assert "PERSONA LOCAL TIME:\n23:41" in block
    assert "DATE:\nSaturday, November 14, 2026" in block
    assert "DAY PERIOD:\nlate evening" in block
    assert "WEEKEND:\nyes" in block
    assert "USER TIMEZONE:\nunknown" in block
    assert "CITY CONTEXT:\nKyiv" in block

    # Test integration inside AIService.build_system_prompt
    prompt = AIService.build_system_prompt(persona, contact, "привет", now=ref_dt)
    assert "PERSONA LOCATION:\nKyiv, Ukraine" in prompt
    assert "PERSONA TIMEZONE:\nEurope/Kyiv" in prompt
    assert "PERSONA LOCAL TIME:\n23:41" in prompt
    assert "DATE:\nSaturday, November 14, 2026" in prompt
    assert "DAY PERIOD:\nlate evening" in prompt
    assert "WEEKEND:\nyes" in prompt
    assert "CITY CONTEXT:\nKyiv" in prompt


def test_temporal_phrase_resolution_to_iso():
    # Section 18: Time-aware memory and events
    # Resolves relative temporal phrases into absolute ISO timestamps
    ref_dt = datetime(2026, 11, 14, 18, 0, 0, tzinfo=pytz.utc)  # Saturday 20:00 Kyiv
    tz = "Europe/Kyiv"

    # "завтра в 10" -> tomorrow (Sunday 2026-11-15) at 10:00:00+02:00
    res1 = LocationContextService.resolve_temporal_phrase("давай завтра в 10 встретимся", tz_name=tz, ref_utc_dt=ref_dt)
    assert res1 is not None
    assert res1["resolved_date"] == "2026-11-15"
    assert res1["resolved_time"] == "10:00"
    assert res1["resolved_timestamp"] == "2026-11-15T10:00:00+02:00"
    assert res1["original_phrase"] == "давай завтра в 10 встретимся"

    # "через два часа" -> today at 22:00:00+02:00
    res2 = LocationContextService.resolve_temporal_phrase("спишемся через 2 часа", tz_name=tz, ref_utc_dt=ref_dt)
    assert res2 is not None
    assert res2["resolved_date"] == "2026-11-14"
    assert res2["resolved_time"] == "22:00"
    assert res2["resolved_timestamp"] == "2026-11-14T22:00:00+02:00"

    # "послезавтра" -> Monday 2026-11-16
    res3 = LocationContextService.resolve_temporal_phrase("можем послезавтра", tz_name=tz, ref_utc_dt=ref_dt)
    assert res3 is not None
    assert res3["resolved_date"] == "2026-11-16"
    assert res3["offset_days"] == 2
