"""
Unit Tests for Strict Meeting Negotiation Lock (Sections 1–21).
Validates that in-person meeting proposals strictly resolve to CINEMA or QUEST,
enforces the state machine, prevents accidental agreements, and ensures natural soft redirects.
"""

from datetime import date, datetime, timezone
import pytest

from companion.ai.context_manager import ContextManager
from companion.models.entities import Contact, Persona
from companion.services.meeting_planner_service import (
    STATE_ACTIVITY_CONFIRMED,
    STATE_BOOKING_HANDOFF,
    STATE_CINEMA_OR_QUEST_PROPOSED,
    STATE_DATE_TIME_NEGOTIATION,
    STATE_MEETING_PROPOSED,
    STATE_NO_MEETING,
    STATE_NON_PREFERRED_ACTIVITY,
    STATE_SOFT_REDIRECT,
    STATE_VENUE_SELECTION,
    MeetingPlannerResolution,
    MeetingPlannerService,
)
from companion.services.presend_validator import PresendValidator


def _build_test_persona():
    p = Persona(
        user_id=1,
        name="Алина",
        age=22,
        city="Самара",
        country="Россия",
        timezone_name="Europe/Samara",
    )
    p.set_activity_catalog(
        {
            "activities": [
                {
                    "type": "quest",
                    "name": "Тайна Старого Дома",
                    "venue": "КвестЛаб Самара",
                    "city": "Самара",
                    "genre": "horror",
                    "duration_minutes": 60,
                },
                {
                    "type": "quest",
                    "name": "Ограбление Банка",
                    "venue": "Квест Хаус",
                    "city": "Самара",
                    "genre": "adventure",
                    "duration_minutes": 60,
                },
                {
                    "type": "cinema",
                    "name": "Киномакс Самара",
                    "venue": "Киномакс Аврора",
                    "city": "Самара",
                    "films": ["Дюна 2", "Аватар"],
                    "schedule": [{"date": "2026-11-20", "time": "19:00", "available": True}],
                },
            ]
        }
    )
    return p


def _build_test_contact():
    return Contact(
        user_id=1,
        tg_id=999111,
        first_name="Иван",
        username="ivan_samara",
        current_city="Самара",
        current_country="Россия",
        detected_timezone="Europe/Samara",
        total_messages_in=10,
    )


# ==============================================================================
# 1. ABSOLUTE MEETING RULE (Section 1) & CLASSIFICATION (Section 14)
# ==============================================================================

@pytest.mark.parametrize(
    "incoming_text,expected_category",
    [
        ("давай просто встретимся", "generic"),
        ("давай погуляем", "walk"),
        ("пойдём в парк", "park"),
        ("го на набережную прогуляемся", ("park", "walk")),
        ("давай кофе попьём", "coffee"),
        ("пошли выпьем кофе в кофейне", "coffee"),
        ("погнали в бар по бокалу пропустим", "bar"),
        ("давай в ресторан сходим поужинать", "restaurant"),
        ("поехали ко мне в гости", "home"),
        ("давай просто покатаемся на машине", "drive"),
        ("го в торговый центр походим", "mall"),
        ("давай где-нибудь посидим поболтаем", ("restaurant", "generic")),
    ],
)
def test_non_preferred_activities_trigger_redirect(incoming_text, expected_category):
    cls_res = MeetingPlannerService.classify_meeting_proposal(incoming_text)
    assert cls_res["meeting_proposed"] is True
    assert cls_res["redirect_required"] is True
    assert cls_res["preferred_activity"] is None
    if isinstance(expected_category, tuple):
        assert cls_res["proposed_activity"] in expected_category
    else:
        assert cls_res["proposed_activity"] == expected_category
    assert "cinema" in cls_res["suggestions"]
    assert "quest" in cls_res["suggestions"]


def test_preferred_activities_do_not_trigger_redirect():
    # Cinema
    c_res = MeetingPlannerService.classify_meeting_proposal("давай в кино на фильм сходим")
    assert c_res["meeting_proposed"] is True
    assert c_res["redirect_required"] is False
    assert c_res["preferred_activity"] == "cinema"

    # Quest
    q_res = MeetingPlannerService.classify_meeting_proposal("погнали на какой-нибудь квест")
    assert q_res["meeting_proposed"] is True
    assert q_res["redirect_required"] is False
    assert q_res["preferred_activity"] == "quest"


# ==============================================================================
# 2. STATE MACHINE PROGRESSION & APPLICATION-LEVEL VALIDATION (Sections 12 & 13)
# ==============================================================================

def test_non_preferred_activity_never_transitions_to_activity_confirmed():
    persona = _build_test_persona()
    contact = _build_test_contact()
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    # 1. User proposes generic walk
    r1 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="давай просто погуляем в парке",
        now=now,
    )
    assert r1.meeting_state in (STATE_NON_PREFERRED_ACTIVITY, STATE_SOFT_REDIRECT)
    assert r1.redirect_required is True
    assert r1.activity_type is None
    assert r1.meeting_confirmed is False
    assert r1.handoff_required is False

    # 2. User tries to confirm date/time for the park ("в субботу в 19:00 в парке, скинь геопозицию")
    r2 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="ну так что, давай в субботу в 19:00 в парке, скинь геопозицию",
        now=now,
    )
    # MUST NOT transition to ACTIVITY_CONFIRMED or BOOKING_HANDOFF!
    assert r2.meeting_state in (STATE_NON_PREFERRED_ACTIVITY, STATE_SOFT_REDIRECT)
    assert r2.activity_type is None
    assert r2.meeting_confirmed is False
    assert r2.handoff_required is False
    assert r2.meeting_control == "AUTO"


def test_state_machine_happy_path_with_soft_redirect():
    persona = _build_test_persona()
    contact = _build_test_contact()
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    # Turn 1: User proposes coffee -> NON_PREFERRED_ACTIVITY / SOFT_REDIRECT
    r1 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="давай кофе попьём",
        now=now,
    )
    assert r1.meeting_state in (STATE_NON_PREFERRED_ACTIVITY, STATE_SOFT_REDIRECT)
    assert r1.redirect_required is True
    assert r1.proposed_activity == "coffee"
    assert r1.meeting_confirmed is False

    # Turn 2: User agrees to cinema compromise ("давай лучше в кино тогда") -> ACTIVITY_CONFIRMED
    r2 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="ну давай в кино тогда",
        now=now,
    )
    assert r2.meeting_state == STATE_ACTIVITY_CONFIRMED
    assert r2.activity_type == "cinema"
    assert r2.meeting_confirmed is True
    assert r2.redirect_required is False
    assert len(r2.selected_options) > 0

    # Turn 3: Negotiate date & time -> DATE_TIME_NEGOTIATION
    r3 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="давай в пятницу в 19:00",
        now=now,
    )
    assert r3.meeting_state == STATE_DATE_TIME_NEGOTIATION
    assert r3.meeting_date == "2026-11-20"
    assert r3.meeting_time == "19:00"

    # Turn 4: User requests booking/link -> BOOKING_HANDOFF (MEETING_HANDOFF_REQUIRED)
    r4 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="отлично, скинь ссылку на билеты",
        now=now,
    )
    assert r4.meeting_state == STATE_BOOKING_HANDOFF
    assert r4.handoff_required is True
    assert r4.meeting_control == "OWNER"
    assert r4.handoff_payload["activity_type"] == "cinema"


# ==============================================================================
# 3. DO NOT LOOP (Section 15)
# ==============================================================================

def test_loop_prevention_when_user_rejects_cinema():
    persona = _build_test_persona()
    contact = _build_test_contact()
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    # 1. User proposed walk
    MeetingPlannerService.evaluate_meeting_turn(persona, contact, "давай погуляем", now=now)

    # 2. User specifically rejects cinema: "в кино не хочу"
    r2 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="в кино не хочу",
        now=now,
    )
    assert "cinema" in r2.rejected_activities
    assert r2.suggestions == ["quest"]
    prompt_block = MeetingPlannerService.format_meeting_planner_prompt_block(r2, persona)
    assert "Собеседник не хочет в кино! Предложи ТОЛЬКО квест" in prompt_block

    # 3. User agrees to quest
    r3 = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="ну ладно, давай на квест",
        now=now,
    )
    assert r3.meeting_state == STATE_ACTIVITY_CONFIRMED
    assert r3.activity_type == "quest"
    assert r3.meeting_confirmed is True


def test_loop_prevention_when_user_rejects_quest():
    persona = _build_test_persona()
    contact = _build_test_contact()
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    # 1. User proposed meeting
    MeetingPlannerService.evaluate_meeting_turn(persona, contact, "давай встретимся", now=now)

    # 2. User rejects quest: "я боюсь квестов, на квест не пойду"
    r = MeetingPlannerService.evaluate_meeting_turn(
        persona=persona,
        contact=contact,
        incoming_message="я боюсь квестов, на квест не пойду",
        now=now,
    )
    assert "quest" in r.rejected_activities
    assert r.suggestions == ["cinema"]
    prompt_block = MeetingPlannerService.format_meeting_planner_prompt_block(r, persona)
    assert "Собеседник не хочет на квест! Предложи ТОЛЬКО кино" in prompt_block


# ==============================================================================
# 4. COMPROMISE LOGIC (Section 11)
# ==============================================================================

def test_compromise_secondary_activity_allowed():
    # Cinema + coffee -> allowed because primary is cinema
    res1 = MeetingPlannerService.classify_meeting_proposal("давай сходим в кино, а потом по кофе")
    assert res1["preferred_activity"] == "cinema"
    assert res1["redirect_required"] is False

    # Quest + walk -> allowed because primary is quest
    res2 = MeetingPlannerService.classify_meeting_proposal("давай на квест сходим, а после можно пройтись")
    assert res2["preferred_activity"] == "quest"
    assert res2["redirect_required"] is False


# ==============================================================================
# 5. NO "ACCIDENTAL YES" & HEALING (Section 14 & PresendValidator)
# ==============================================================================

def test_heal_accidental_yes_to_park_or_walk():
    res = MeetingPlannerResolution(
        meeting_proposed=True,
        proposed_activity="walk",
        redirect_required=True,
        suggestions=["cinema", "quest"],
    )

    # Accidental yes from bot
    bad_bot_reply = "Да, давай в парк погуляем!"
    healed = MeetingPlannerService.heal_accidental_yes(bad_bot_reply, res)
    assert "парк" not in healed.lower() or "кино" in healed.lower() or "квест" in healed.lower()
    assert ("кино" in healed.lower() or "квест" in healed.lower())


def test_heal_hard_refusal():
    res = MeetingPlannerResolution(
        meeting_proposed=True,
        proposed_activity="coffee",
        redirect_required=True,
        suggestions=["cinema", "quest"],
    )

    hard_refusal_bot_reply = "Нет, я не хочу пить кофе. Я согласна только на кино."
    healed = MeetingPlannerService.heal_accidental_yes(hard_refusal_bot_reply, res)
    assert "нет, я не хочу" not in healed.lower()
    assert "я согласна только" not in healed.lower()
    assert ("кино" in healed.lower() or "квест" in healed.lower())


def test_presend_validator_intercepts_accidental_yes_in_meeting_flow():
    ctx = {
        "meeting_planner": {
            "stage": "INTEREST_SHOWN",
            "redirect_required": True,
            "resolution": MeetingPlannerResolution(
                meeting_proposed=True,
                proposed_activity="walk",
                redirect_required=True,
                suggestions=["cinema", "quest"],
            ),
        }
    }

    accidental_yes = "Давай просто погуляем, я не против"
    is_valid, err, sanitized = PresendValidator.validate_presend(
        candidate_reply=accidental_yes,
        internal_context=ctx,
        target_sender_type="user",
    )
    assert is_valid is True
    # The healed text must redirect toward cinema or quest
    assert ("кино" in sanitized.lower() or "квест" in sanitized.lower())


# ==============================================================================
# 6. URL LOCK & SANITIZATION (Sections 18 & 19)
# ==============================================================================

def test_url_lock_strips_urls_and_link_placeholders():
    raw_reply = "Супер, вот ссылка: https://booking.com/quest [ссылка] договорились!"
    cleaned = MeetingPlannerService.sanitize_outgoing_meeting_reply(raw_reply)
    assert "http" not in cleaned
    assert "[ссылка]" not in cleaned
