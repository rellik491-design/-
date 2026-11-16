from datetime import date, datetime, timedelta, timezone
import pytest

from companion.ai.context_manager import ContextManager
from companion.bot.admin_notifier import AdminNotifier
from companion.models.entities import Contact, Persona
from companion.services.meeting_planner_service import MeetingPlannerService
from companion.services.output_sanitizer import OutputSanitizer
from companion.services.persona_lore_service import PersonaLoreService


def _build_persona_with_lore_and_catalog():
    p = Persona(
        user_id=1,
        name="Алина",
        age=22,
        city="London",
        country="United Kingdom",
        timezone_name="Europe/London",
    )
    p.set_city_connections(
        [
            {
                "city": "Manchester",
                "country": "United Kingdom",
                "person": {"relation": "aunt", "name": None},
                "history": {"previously_lived_there": True, "lived_with_person": True},
                "travel": {
                    "planned": True,
                    "destination": "Manchester",
                    "arrival_date": "2026-11-20",
                },
            },
            {
                "city": "Liverpool",
                "country": "United Kingdom",
                "person": {"relation": "grandmother", "name": None},
                "history": {"previously_lived_there": False, "lived_with_person": False},
                "travel": {
                    "planned": True,
                    "destination": "Liverpool",
                    "arrival_date": "2026-11-28",
                },
            },
        ]
    )
    p.set_activity_catalog(
        {
            "activities": [
                {
                    "type": "quest",
                    "name": "Asylum Horror Escape",
                    "venue": "Breakout Manchester",
                    "city": "Manchester",
                    "genre": "horror",
                    "difficulty": "medium",
                    "duration_minutes": 60,
                    "price": "£25",
                    "booking_url": "https://breakoutmanchester.com/asylum",
                    "available_times": ["18:00", "19:30"],
                },
                {
                    "type": "quest",
                    "name": "Sherlock's Laugh",
                    "venue": "Lucardo Manchester",
                    "city": "Manchester",
                    "genre": "comedy",
                    "difficulty": "easy",
                    "duration_minutes": 60,
                    "price": "£22",
                    "booking_url": "https://lucardo.com/sherlock",
                    "available_times": ["17:00", "19:00"],
                },
                {
                    "type": "cinema",
                    "name": "Odeon Great Northern",
                    "venue": "Odeon Great Northern",
                    "city": "Manchester",
                    "films": ["Dune: Part Two", "Interstellar IMAX"],
                    "booking_url": "https://odeon.co.uk/manchester",
                    "schedule": [{"date": "2026-11-21", "time": "19:00", "available": True}],
                },
                {
                    "type": "cinema",
                    "name": "Expired Cinema Manchester",
                    "city": "Manchester",
                    "is_playing": False,
                    "schedule": [{"date": "2020-01-01", "time": "18:00"}],
                },
                {
                    "type": "quest",
                    "name": "London Dungeon Quest",
                    "city": "London",
                    "genre": "horror",
                    "duration_minutes": 60,
                },
            ]
        }
    )
    return p


def _build_contact(city="Manchester", country="United Kingdom"):
    return Contact(
        user_id=1,
        tg_id=777888,
        first_name="Dmytro",
        username="dmytro_uk",
        origin_country="Ukraine",
        current_city=city,
        current_country=country,
        detected_timezone="Europe/London",
        total_messages_in=5,
    )


def test_nested_lore_schema_and_multiple_cities_isolation():
    persona = _build_persona_with_lore_and_catalog()
    contact_man = _build_contact(city="Manchester")
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    res_man = PersonaLoreService.resolve_geographic_connection(
        persona=persona,
        contact=contact_man,
        incoming_message="я в Манчестере живу",
        now=now,
    )
    assert res_man.matched is True
    assert res_man.connection["relative"] == "aunt"
    assert res_man.travel["trip_state"] == "PLANNED"
    assert res_man.travel["days_until"] == 4
    assert res_man.natural_meeting_opportunity is True

    contact_liv = _build_contact(city="Liverpool")
    res_liv = PersonaLoreService.resolve_geographic_connection(
        persona=persona,
        contact=contact_liv,
        incoming_message="я сейчас в Ливерпуле",
        now=now,
    )
    assert res_liv.matched is True
    assert res_liv.connection["relative"] == "grandmother"
    assert res_liv.travel["destination"] == "Liverpool"


def test_city_scoped_catalog_and_genre_synchronization():
    persona = _build_persona_with_lore_and_catalog()
    today = date(2026, 11, 16)

    # 1. Must never return London quest or expired cinema for Manchester
    man_opts = MeetingPlannerService.get_catalog_for_city(
        persona=persona,
        meeting_city="Manchester",
        today=today,
    )
    names = [o["name"] for o in man_opts]
    assert "London Dungeon Quest" not in names
    assert "Expired Cinema Manchester" not in names
    assert len(man_opts) <= 3

    # 2. User dislikes horror and wants comedy
    pref, disliked = MeetingPlannerService.detect_genre_preferences(
        "я не люблю страшное, давай что-нибудь весёлое"
    )
    assert "horror" in disliked
    assert pref == "comedy"

    filtered_quests = MeetingPlannerService.get_catalog_for_city(
        persona=persona,
        meeting_city="Manchester",
        activity_type="quest",
        preferred_genre=pref,
        disliked_genres=disliked,
        today=today,
    )
    assert len(filtered_quests) == 1
    assert filtered_quests[0]["name"] == "Sherlock's Laugh"


def test_gradual_funnel_and_owner_handoff_state_machine():
    persona = _build_persona_with_lore_and_catalog()
    contact = _build_contact(city="Manchester")
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)  # Monday

    # Turn 1: User proposes meeting -> INTEREST_SHOWN + natural trip bridge
    r1 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="давай как-нибудь встретимся",
        now=now,
    )
    assert r1.stage == "INTEREST_SHOWN"
    assert r1.meeting_control == "AUTO"
    assert r1.natural_trip_bridge is True
    block1 = MeetingPlannerService.format_meeting_planner_prompt_block(r1, persona)
    assert "ЗАПРЕТ НА БАНАЛЬНЫЕ ПРОГУЛКИ" in block1
    assert "СВЯЗКА С ПОЕЗДКОЙ ПЕРСОНАЖА" in block1

    # Turn 2: User chooses quest & horror -> OPTIONS_DISCUSSED (city-scoped)
    r2 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="давай на квест, я люблю что-нибудь страшное",
        now=now,
    )
    assert r2.stage == "OPTIONS_DISCUSSED"
    assert r2.activity_type == "quest"
    assert r2.preferred_genre == "horror"
    assert r2.selected_options[0]["name"] == "Asylum Horror Escape"

    # Turn 3: User agrees on date & time ("в субботу в 19:00, скинь ссылку") -> MEETING_HANDOFF_REQUIRED
    r3 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="о, давай в субботу в 19:00, скинь ссылку на бронь",
        now=now,
    )
    assert r3.handoff_required is True
    assert r3.stage == "MEETING_HANDOFF_REQUIRED"
    assert r3.meeting_control == "OWNER"
    assert r3.meeting_date == "2026-11-21"
    assert r3.meeting_time == "19:00"
    assert r3.handoff_payload is not None
    assert r3.handoff_payload["event"] == "MEETING_HANDOFF_REQUIRED"
    assert r3.handoff_payload["meeting_city"] == "Manchester"
    assert r3.handoff_payload["persona_connection"] == "aunt"
    assert r3.handoff_payload["persona_trip"] == "2026-11-20"

    # Format owner notification
    alert_html = AdminNotifier.format_meeting_handoff_alert(
        r3.handoff_payload,
        contact_name="Dmytro",
        contact_username="dmytro_uk",
    )
    assert "MEETING HANDOFF" in alert_html
    assert "Manchester" in alert_html
    assert "Asylum Horror Escape" in alert_html
    assert "2026-11-21" in alert_html
    assert "19:00" in alert_html

    # Turn 4: While meeting_control == OWNER, autonomous logistics remain locked, until owner returns control
    r4 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="ну что там с билетами?",
        now=now,
    )
    assert r4.meeting_control == "OWNER"

    returned = MeetingPlannerService.return_control_to_auto(contact)
    assert returned["meeting_control"] == "RETURNED_TO_AUTO"


def test_no_fake_links_and_no_internal_marker_leakage():
    raw_reply = (
        "[TRIP_STATE: SOON] [CITY_CONNECTION: AUNT] [MEETING_HANDOFF_REQUIRED] "
        "О, супер, вот ссылка на бронь https://example.com/booking увидимся!"
    )
    cleaned_mp = MeetingPlannerService.sanitize_outgoing_meeting_reply(raw_reply)
    assert "https://example.com/booking" not in cleaned_mp
    assert "TRIP_STATE" not in cleaned_mp
    assert "CITY_CONNECTION" not in cleaned_mp
    assert "MEETING_HANDOFF_REQUIRED" not in cleaned_mp

    cleaned_os = OutputSanitizer.sanitize_outgoing_text(raw_reply)
    assert "TRIP_STATE" not in cleaned_os
    assert "CITY_CONNECTION" not in cleaned_os
    assert "MEETING_HANDOFF_REQUIRED" not in cleaned_os
