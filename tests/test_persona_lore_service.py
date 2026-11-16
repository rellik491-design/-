from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import pytest

from companion.ai.context_manager import ContextManager
from companion.models.entities import Contact, Persona
from companion.services.persona_lore_service import PersonaLoreService


def _make_persona(connections=None, city="London", country="United Kingdom"):
    p = Persona(
        user_id=1,
        name="Алина",
        age=22,
        city=city,
        country=country,
    )
    if connections is not None:
        p.set_city_connections(connections)
    return p


def _make_contact(current_city="Manchester", current_country="United Kingdom", total_in=5):
    return Contact(
        user_id=1,
        tg_id=123456,
        first_name="Alex",
        current_city=current_city,
        current_country=current_country,
        total_messages_in=total_in,
    )


def test_no_invented_lore_when_unconfigured():
    persona = _make_persona(connections=[])
    contact = _make_contact(current_city="Manchester", current_country="United Kingdom")

    res = PersonaLoreService.resolve_geographic_connection(
        persona=persona,
        contact=contact,
        incoming_message="я вообще сейчас в Манчестере",
    )
    assert res.matched is False
    assert res.geographic_relation["persona_has_family_connection"] is False
    assert PersonaLoreService.format_persona_lore_prompt_block(res, persona) == ""


def test_same_country_different_city_and_configured_connection():
    today = date(2026, 11, 15)
    visit_dt = (today + timedelta(days=3)).isoformat()
    persona = _make_persona(
        city="London",
        country="United Kingdom",
        connections=[
            {
                "city": "Manchester",
                "country": "United Kingdom",
                "relative": "aunt",
                "history": "persona previously lived with her aunt there",
                "planned_visit": True,
                "visit_date": visit_dt,
            }
        ],
    )
    contact = _make_contact(current_city="Manchester", current_country="United Kingdom")

    res = PersonaLoreService.resolve_geographic_connection(
        persona=persona,
        contact=contact,
        incoming_message="я вообще сейчас в Манчестере",
        now=datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc),
    )
    assert res.matched is True
    assert res.geographic_relation == {
        "same_country": True,
        "same_city": False,
        "persona_has_family_connection": True,
        "persona_upcoming_visit": True,
    }
    assert res.travel["status"] == "soon"
    assert res.travel["days_until"] == 3
    assert res.timing_phrase == "через пару-тройку дней"
    assert res.relevant_now is True

    block = PersonaLoreService.format_persona_lore_prompt_block(res, persona)
    assert "PERSONA LORE" in block
    assert "Manchester" in block
    assert "через пару-тройку дней" in block
    assert "мы обе живём в Manchester" in block  # negative guard rule


def test_different_country_and_same_city_do_not_trigger():
    persona = _make_persona(
        city="London",
        country="United Kingdom",
        connections=[
            {
                "city": "Manchester",
                "country": "United Kingdom",
                "relative": "aunt",
                "history": "lived with aunt",
            }
        ],
    )

    paris_contact = _make_contact(current_city="Paris", current_country="France")
    res_paris = PersonaLoreService.resolve_geographic_connection(
        persona=persona,
        contact=paris_contact,
        incoming_message="я в Париже сейчас",
    )
    assert res_paris.matched is False
    assert res_paris.geographic_relation["same_country"] is False

    london_contact = _make_contact(current_city="London", current_country="United Kingdom")
    res_london = PersonaLoreService.resolve_geographic_connection(
        persona=persona,
        contact=london_contact,
        incoming_message="я в Лондоне",
    )
    assert res_london.matched is False
    assert res_london.geographic_relation["same_city"] is True


def test_dynamic_date_calculation_and_status_transitions():
    base_today = date(2026, 11, 15)
    conn = {"city": "Manchester", "country": "United Kingdom", "relative": "aunt", "planned_visit": True}

    cases = [
        (7, "planned", "на следующей неделе"),
        (4, "planned", "через несколько дней"),
        (3, "soon", "через пару-тройку дней"),
        (2, "soon", "через пару дней"),
        (1, "soon", "завтра"),
        (0, "arrived", "я уже там"),
        (-2, "completed", None),
    ]
    for delta, expected_status, expected_phrase in cases:
        c = dict(conn, visit_date=(base_today + timedelta(days=delta)).isoformat())
        travel = PersonaLoreService.compute_travel_state(c, today=base_today)
        assert travel is not None
        assert travel["status"] == expected_status
        assert travel["days_until"] == delta
        assert PersonaLoreService.get_visit_timing_phrase(delta, expected_status) == expected_phrase


def test_opportunistic_relevance_not_forced_on_unrelated_topic():
    persona = _make_persona(
        city="London",
        country="United Kingdom",
        connections=[
            {
                "city": "Manchester",
                "country": "United Kingdom",
                "relative": "aunt",
                "history": "lived with aunt",
                "planned_visit": True,
                "visit_date": "2026-11-18",
            }
        ],
    )
    contact = _make_contact(current_city="Manchester", current_country="United Kingdom")

    res = PersonaLoreService.resolve_geographic_connection(
        persona=persona,
        contact=contact,
        incoming_message="у меня сегодня сложный код-ревью на работе",
        active_thread="work_career",
        now=datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc),
    )
    assert res.matched is True
    assert res.topic_relevant is False
    assert res.relevant_now is False
    assert PersonaLoreService.format_persona_lore_prompt_block(res, persona) == ""


def test_disclosure_tracking_and_cooldown_prevents_repetition():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    persona = _make_persona(
        city="London",
        country="United Kingdom",
        connections=[
            {
                "city": "Manchester",
                "country": "United Kingdom",
                "relative": "aunt",
                "history": "lived with aunt",
                "planned_visit": True,
                "visit_date": "2026-11-18",
            }
        ],
    )
    contact = _make_contact(current_city="Manchester", current_country="United Kingdom", total_in=4)

    res1 = PersonaLoreService.resolve_geographic_connection(
        persona=persona,
        contact=contact,
        incoming_message="я вообще сейчас в Манчестере",
        now=now,
    )
    assert res1.relevant_now is True

    recorded = PersonaLoreService.record_lore_disclosure_from_reply(
        contact=contact,
        reply_text="о, Манчестер? я там раньше с тётей жила ахах, и через пару-тройку дней туда поеду",
        resolution=res1,
        now=now,
    )
    assert recorded is True
    state = contact.get_persona_lore_state()
    assert "city_connection_last_mentioned" in state
    assert "family_connection_last_mentioned" in state
    assert "visit_last_mentioned" in state

    # Two messages later (1 hour later) -> cooldown active!
    contact.total_messages_in = 6
    res2 = PersonaLoreService.resolve_geographic_connection(
        persona=persona,
        contact=contact,
        incoming_message="в Манчестере сегодня дождь",
        now=now + timedelta(hours=1),
    )
    assert res2.cooldown_active is True
    assert res2.relevant_now is False
    block2 = PersonaLoreService.format_persona_lore_prompt_block(res2, persona)
    assert "ЗАПРЕТ ПОВТОРА" in block2


@pytest.mark.asyncio
async def test_context_manager_integrates_persona_lore():
    persona = _make_persona(
        city="London",
        country="United Kingdom",
        connections=[
            {
                "city": "Manchester",
                "country": "United Kingdom",
                "relative": "aunt",
                "history": "lived with aunt there",
                "planned_visit": True,
                "visit_date": (date.today() + timedelta(days=2)).isoformat(),
            }
        ],
    )
    contact = _make_contact(current_city="Manchester", current_country="United Kingdom", total_in=3)

    pkg = await ContextManager.build_seven_level_context(
        persona=persona,
        contact=contact,
        new_incoming_message="я сейчас в Манчестере, погода так себе",
        session=None,
        explicit_recent_history=[],
    )
    lore_meta = pkg["internal_context"]["persona_lore"]
    assert lore_meta["matched"] is True
    assert lore_meta["relevant_now"] is True
    assert lore_meta["travel"]["days_until"] == 2
    assert "PERSONA LORE" in pkg["prompt_contents"]
