import pytest
from pathlib import Path
import tempfile
from companion.services.prosody_controller import (
    PronunciationDictionary,
    ProsodyAnalyzer,
    SpokenTextTransformer,
    ProsodyHistoryTracker,
    AdvancedProsodyController,
    TTSInputObject,
)


def test_pronunciation_dictionary_seed_and_persistence():
    with tempfile.TemporaryDirectory() as tmp_dir:
        dict_path = Path(tmp_dir) / "test_dict.json"
        pd = PronunciationDictionary(file_path=dict_path)

        # 1. Verify seed entries loaded
        assert pd.get("гс") is not None
        assert pd.get("гс").pronunciation == "гэ э́с"
        assert pd.get("тг").pronunciation == "тэ гэ́"
        assert pd.get("занята").pronunciation == "занята́"
        assert pd.get("договор").pronunciation == "догово́р"
        assert pd.get("кофе").pronunciation == "ко́фэ"

        # 2. Add custom entry and check disk persistence
        pd.set("нейросеть", "нейросэ́ть", stress="3")
        assert dict_path.exists()

        # Reload in a new instance
        pd2 = PronunciationDictionary(file_path=dict_path)
        entry = pd2.get("нейросеть")
        assert entry is not None
        assert entry.pronunciation == "нейросэ́ть"
        assert entry.stress == "3"


def test_short_telegram_reactions():
    reactions = [
        ("да ладно?", "curious", "surprise_rising"),
        ("серьёзно?", "curious", "neutral"),
        ("ну да", "neutral", "soft_falling"),
        ("не знаю", "neutral", "open"),
        ("прикинь", "excited", "surprise_rising"),
        ("понятно", "neutral", "soft_falling"),
        ("ага", "neutral", "soft_falling"),
        ("капец", "curious", "soft_falling"),
        ("ого", "excited", "surprise_rising"),
        ("да", "neutral", "soft_falling"),
        ("нет", "neutral", "soft_falling"),
    ]
    for text, expected_emotion, expected_ending in reactions:
        obj = ProsodyAnalyzer.analyze_message(text)
        assert obj.emotion == expected_emotion, f"Failed emotion for {text}"
        assert obj.ending_type == expected_ending, f"Failed ending for {text}"


def test_question_classification():
    # 1. Surprise question
    q_surp = ProsodyAnalyzer.analyze_message("Ты серьёзно это сделал?")
    assert q_surp.question_type == "surprise"
    assert q_surp.ending_type == "surprise_rising"

    # 2. Rhetorical question
    q_rhet = ProsodyAnalyzer.analyze_message("Ну и зачем это было нужно?")
    assert q_rhet.question_type == "rhetorical"
    assert q_rhet.ending_type == "neutral"

    # 3. Information question
    q_info = ProsodyAnalyzer.analyze_message("Во сколько ты сегодня освободишься?")
    assert q_info.question_type == "genuine_information"
    assert q_info.ending_type == "question_rising"


def test_rhetorical_question_spoken_transformation():
    # Rhetorical questions should not end with an upward question mark spike
    obj = AdvancedProsodyController.process_for_tts("Ну и зачем ты это сказал?")
    assert obj.question_type == "rhetorical"
    assert not obj.speech_text.endswith("?")
    assert obj.speech_text.endswith(".")


def test_connected_thoughts_flow():
    # Smooths out chopping commas before subordinate clauses
    inputs_and_expected = [
        ("Да я просто не ожидала, что ты реально это сделаешь.", "не ожидала что"),
        ("Ну я не знаю, мне кажется, это как-то странно.", "мне кажется это"),
        ("Я не думала, что всё так повернётся.", "не думала что"),
        ("Я видела, как ты на меня посмотрел.", "видела как"),
        ("Главное, чтобы ты успел вовремя.", "главное чтобы"),
    ]
    for raw, expected_sub in inputs_and_expected:
        res = AdvancedProsodyController.process_for_tts(raw).speech_text
        assert expected_sub.lower() in res.lower(), f"Expected '{expected_sub}' in '{res}'"


def test_prosody_history_prevents_repetitive_falling():
    chat_id = 999999
    # First message: statement -> soft_falling
    obj1 = AdvancedProsodyController.process_for_tts("Я сегодня дома весь день.", chat_id=chat_id)
    assert obj1.ending_type in ("soft_falling", "neutral")

    # If last was forced falling, next non-serious message should be soft_falling/neutral, avoiding monotonic drops
    obj2 = TTSInputObject(display_text="test", speech_text="test", ending_type="falling")
    ProsodyHistoryTracker.record_prosody(chat_id, obj2)

    obj3 = AdvancedProsodyController.process_for_tts("Потом чай попью.", chat_id=chat_id)
    assert obj3.ending_type != "falling"


def test_tts_input_object_full_spec():
    text = "Слушай, ну я только что домой зашла, кофе пью и смотрю дайвинчик!"
    obj = AdvancedProsodyController.process_for_tts(text, chat_id=123)

    assert isinstance(obj, TTSInputObject)
    assert obj.display_text == text
    assert "ко́фэ" in obj.speech_text
    assert "дайви́нчик" in obj.speech_text
    # Exclamation mark transformed to avoid screaming
    assert "!" not in obj.speech_text
    assert obj.stability >= 0.50
    assert obj.similarity_boost >= 0.80
    assert obj.style <= 0.15
