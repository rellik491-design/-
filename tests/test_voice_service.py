import pytest
from companion.services.voice_service import VoiceService


def test_should_send_voice_triggers():
    # Direct requests or authenticity doubts should return True
    assert VoiceService.should_send_voice("Скинь гс плиз", "Хорошо, сейчас запишу") is True
    assert VoiceService.should_send_voice("Запиши голосовое", "Без проблем") is True
    assert VoiceService.should_send_voice("Хочу услышать твой голос", "Держи") is True
    assert VoiceService.should_send_voice("Ты бот или человек?", "Конечно не бот") is True
    assert VoiceService.should_send_voice("Ты реальная вообще?", "Да, настоящая") is True
    assert VoiceService.should_send_voice("Докажи что ты живая", "Слушай войс") is True


def test_shorten_for_voice():
    short_text = "Привет! Как твои дела сегодня?"
    assert VoiceService.shorten_for_voice(short_text, max_chars=180) == short_text

    long_text = (
        "Слушай, ну я только что домой зашла, вообще без каких-либо сил сегодня. "
        "Блин, давай я сейчас быстренько в душ сбегаю — и минут через двадцать тебе сама напишу, хорошо? "
        "А потом мы с тобой подробно всё обсудим и выберем фильм на вечер, который оба давно хотели посмотреть."
    )
    assert len(long_text) > 220
    shortened = VoiceService.shorten_for_voice(long_text, max_chars=180)
    assert len(shortened) <= 180
    assert "Слушай, ну я только что домой зашла" in shortened
    assert "Блин, давай я сейчас быстренько в душ сбегаю" in shortened


def test_digits_to_words():
    text = "Буду через 5 минут или через 20 минут"
    converted = VoiceService.digits_to_words(text)
    assert "пять" in converted
    assert "двадцать" in converted
    assert "5" not in converted
    assert "20" not in converted

    text2 = "У меня 100 рублей и 42 дела"
    converted2 = VoiceService.digits_to_words(text2)
    assert "сто" in converted2
    assert "сорок два" in converted2


def test_prepare_text_for_tts():
    raw = "Привет! 😊 еще не все готово)) че делаешь? *улыбается* https://example.com"
    prepared = VoiceService.prepare_text_for_tts(raw)

    # Emojis stripped
    assert "😊" not in prepared
    # URLs stripped
    assert "https://" not in prepared
    # Asterisks stripped
    assert "*" not in prepared
    # ё-substitutions
    assert "ещё" in prepared
    assert "всё" in prepared
    assert "чё" in prepared
    # Additional stress markers
    test_stress = VoiceService.prepare_text_for_tts("ниче, он скоро придет и все пойдет как надо, это твое и мое дело")
    assert "ничё" in test_stress
    assert "придёт" in test_stress
    assert "пойдёт" in test_stress
    assert "твоё" in test_stress
    assert "моё" in test_stress
    # Smileys converted to conversational pause
    assert "))" not in prepared
    # Exclamation marks converted to periods to keep calm intonation
    assert "!" not in prepared


def test_safe_phonetics_and_homographs():
    # 1. 'все' -> 'всё' except 'все мы', 'все они', 'все вы', 'все люди'
    res_vse_all = VoiceService.prepare_text_for_tts("все готово, все хорошо")
    assert "всё готово" in res_vse_all
    assert "всё хорошо" in res_vse_all

    res_vse_we = VoiceService.prepare_text_for_tts("все мы знаем, все они согласны, все вы молодцы, все люди разные")
    assert "все мы" in res_vse_we
    assert "все люди" in res_vse_we
    assert "все они" in res_vse_we
    assert "все вы" in res_vse_we

    # 2. Homographs replacement to unambiguous spoken synonyms
    homo_input = "Я сейчас в душ сбегаю, а потом тебе пишу, не стоит грустить, я от смеха плачу, открой замок"
    homo_res = VoiceService.prepare_text_for_tts(homo_input)
    assert "схожу" in homo_res
    assert "сбегаю" not in homo_res
    assert "напишу" in homo_res
    assert "не надо" in homo_res
    assert "реву" in homo_res
    assert "плачу" not in homo_res
    assert "замочек" in homo_res
    assert "замок" not in homo_res

    # 3. Unicode stress marks removal
    stress_input = "сбе́гаю домо́й"
    stress_res = VoiceService.prepare_text_for_tts(stress_input)
    assert "\u0301" not in stress_res
    assert "схожу" in stress_res

    # 4. 'окей' -> 'окэй'
    ok_res = VoiceService.prepare_text_for_tts("окей, договорились")
    assert "окэй" in ok_res


def test_get_dynamic_voice_settings():
    joy_vs = VoiceService.get_dynamic_voice_settings("ахах, классная шутка, супер!")
    assert joy_vs.stability == 0.38
    assert joy_vs.style == 0.30
    assert joy_vs.use_speaker_boost is True

    curious_vs = VoiceService.get_dynamic_voice_settings("серьезно? а ты откуда сам?")
    assert curious_vs.stability == 0.42
    assert curious_vs.style == 0.26

    tired_vs = VoiceService.get_dynamic_voice_settings("я сегодня так устала, вообще без сил, пойду спать")
    assert tired_vs.stability == 0.50
    assert tired_vs.style == 0.15

    casual_vs = VoiceService.get_dynamic_voice_settings("да вот чай пью, музыку включила")
    assert casual_vs.stability == 0.44
    assert casual_vs.style == 0.22


def test_natural_breathing_and_pauses():
    text = "да вот дома, а ты чем занят? ахахах, ну ты даешь"
    prepared = VoiceService.prepare_text_for_tts(text)

    # Starts with a soft breathing pause
    assert prepared.startswith("...")
    # Laughter transformed into natural chuckles and sighs
    assert "... хах..." in prepared
    # Conjunction breathing pause
    assert "... а ты" in prepared
    # No punctuation collisions like '...,' or ', ...'
    assert "...," not in prepared
    assert ", ..." not in prepared
    assert ",..." not in prepared


def test_incoming_voice_probability():
    from unittest.mock import patch

    with patch("random.random", return_value=0.20):
        # 0.20 < 0.35 -> True
        assert VoiceService.should_send_voice("привет", "привет", is_incoming_voice=True) is True

    with patch("random.random", return_value=0.50):
        # 0.50 >= 0.35 -> False
        assert VoiceService.should_send_voice("привет", "привет", is_incoming_voice=True) is False


def test_mastering_equalizer_post_processing():
    """
    Verifies user's exact mastering equalizer chain:
    - Low Cut 62HZ 24dB/oct (cascaded 2-pole highpass)
    - Bell 194hz -2.33dB
    - Bell 437hz -1.12dB
    - Bell 800hz -1.45dB
    - Bell 2841hz +1dB
    - High Shelf 6678hz +0.60db
    - Output is genuine Telegram OGG Opus format
    """
    import subprocess
    import imageio_ffmpeg
    import time

    # Generate a small sine audio sample in memory
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=1000:duration=1", "-f", "mp3", "pipe:1"]
    raw_mp3 = subprocess.run(cmd, capture_output=True).stdout
    assert len(raw_mp3) > 100

    t0 = time.perf_counter()
    mastered = VoiceService.apply_mastering_equalizer(raw_mp3)
    duration_s = time.perf_counter() - t0

    # Must be valid OGG Opus with Telegram voice format
    assert len(mastered) > 100
    assert mastered[:4] == b"OggS"
    # Fast processing (< 600ms)
    assert duration_s < 0.60

    # Check filter string contains exact user specifications
    eq_str = VoiceService.MASTERING_EQ_FILTER
    assert "highpass=f=62:poles=2,highpass=f=62:poles=2" in eq_str
    assert "equalizer=f=194:width_type=q:width=1.0:gain=-2.33" in eq_str
    assert "equalizer=f=437:width_type=q:width=1.0:gain=-1.12" in eq_str
    assert "equalizer=f=800:width_type=q:width=1.0:gain=-1.45" in eq_str
    assert "equalizer=f=2841:width_type=q:width=1.0:gain=1.0" in eq_str
    assert "highshelf=f=6678:gain=0.60" in eq_str



