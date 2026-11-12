import pytest
from companion.services.intent_service import IntentService


def test_detect_meetup_proposals():
    samples_positive = [
        "Слушай, а давай встретимся на днях?",
        "Может сходим на кофе завтра?",
        "Пойдем на свидание в субботу)",
        "Давай поужинаем сегодня в центре",
        "Скинь свой номер телефона, наберу",
        "Когда увидимся в реале?",
    ]
    for msg in samples_positive:
        intent = IntentService.detect_meetup_proposal(msg)
        assert intent.is_proposal is True, f"Failed to detect meetup in: '{msg}'"
        assert intent.confidence >= 0.8


def test_detect_meetup_negative():
    samples_negative = [
        "Я вчера встретил друга в метро",
        "Кофе очень вкусный в этой кофейне",
        "Смотрю сериал дома",
        "Как твои дела?",
    ]
    for msg in samples_negative:
        intent = IntentService.detect_meetup_proposal(msg)
        assert intent.is_proposal is False, f"False positive in: '{msg}'"


def test_detect_photo_requests():
    req1 = IntentService.detect_photo_request("Скинь фотку, интересно как ты выглядишь")
    assert req1.requested is True
    assert req1.preferred_tag == "general"

    req2 = IntentService.detect_photo_request("Скинь фотку из зала, как тренировка прошла?")
    assert req2.requested is True
    assert req2.preferred_tag == "gym"

    req3 = IntentService.detect_photo_request("Ты дома? Скинь домашнее селфи")
    assert req3.requested is True
    assert req3.preferred_tag == "home"

    req_none = IntentService.detect_photo_request("Хорошо, спишемся позже")
    assert req_none.requested is False


def test_stop_words():
    assert IntentService.is_stop_word_present("Отстань от меня, достала") is True
    assert IntentService.is_stop_word_present("Ты бот какой-то") is True
    assert IntentService.is_stop_word_present("Привет, рад знакомству") is False


def test_detect_voice_requests():
    v1 = IntentService.detect_voice_request("Скинь войс, хочу послушать твой голос")
    assert v1.requested is True
    assert v1.preferred_tag == "greeting"

    v2 = IntentService.detect_voice_request("Ты точно не фейк? Докажи что не бот, запиши голосовое")
    assert v2.requested is True
    assert v2.preferred_tag == "sarcastic_name"

    v3 = IntentService.detect_voice_request("А где на кофе встретимся? Запиши голосовуху")
    assert v3.requested is True
    assert v3.preferred_tag == "meetup"

    v_none = IntentService.detect_voice_request("Привет, хорошего вечера")
    assert v_none.requested is False


def test_detect_agreements_and_closures():
    # In one hour
    assert IntentService.detect_agreement_or_pause("Давай спишемся через часик, я отойду") == "in_one_hour"
    assert IntentService.detect_agreement_or_pause("напишу через час") == "in_one_hour"

    # Tomorrow
    assert IntentService.detect_agreement_or_pause("Ладно, до завтра тогда!") == "tomorrow"
    assert IntentService.detect_agreement_or_pause("Давай завтра спишемся") == "tomorrow"

    # Goodnight
    assert IntentService.detect_agreement_or_pause("Спокойной ночи, я спать пошел") == "goodnight"
    assert IntentService.detect_agreement_or_pause("сладких снов") == "goodnight"

    # Closure
    assert IntentService.detect_agreement_or_pause("ок") == "closure"
    assert IntentService.detect_agreement_or_pause("понял") == "closure"
    assert IntentService.detect_agreement_or_pause("ясно") == "closure"

    # Regular message
    assert IntentService.detect_agreement_or_pause("Привет! Как твои дела сегодня?") is None

