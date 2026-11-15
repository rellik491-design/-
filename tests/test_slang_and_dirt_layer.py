import pytest
from companion.services.voice_service import VoiceService
from companion.services.slang_pronunciation_layer import SlangAbbreviationAnalyzer
from companion.services.prosody_controller import AdvancedProsodyController, TTSInputObject


def test_slang_layer_gs_grammatical_cases():
    cases = [
        ("скинь гс пожалуйста", "скинь голосовое пожалуйста"),
        ("я тебе гс отправила уже", "я тебе голосовое отправила уже"),
        ("запиши мне гс", "запиши мне голосовое"),
        ("я в гс тебе всё рассказала", "я в голосовом тебе всё рассказала"),
        ("ответь на гс", "ответь на голосовое"),
        ("с твоим гс день лучше", "с твоим голосовым день лучше"),
        ("твое гс такое приятное", "твоё голосовое такое приятное"),
        ("твои гс слушаю", "твои голосовые слушаю"),
        ("скинь гску", "скинь голосовуху"),
        ("скинь гски", "скинь голосовые"),
    ]
    for raw, expected in cases:
        norm, decisions = SlangAbbreviationAnalyzer.normalize_slang(raw)
        assert expected.lower() in norm.lower(), f"Failed for '{raw}': got '{norm}', expected '{expected}'"
        assert "гэ эс" not in norm.lower()
        assert "г-с" not in norm.lower()


def test_slang_layer_general_telegram_abbreviations():
    cases = [
        ("напиши мне в лс", "в личку"),
        ("скинь в тг", "в телегу"),
        ("мой тг в профиле", "мой телеграм"),
        ("я мб опоздаю", "может быть"),
        ("крч я освободилась", "короче"),
        ("щас чай налью", "сейчас"),
        ("спс большое", "спасибо"),
        ("помоги пж", "пожалуйста"),
        ("я хз что делать", "не знаю"),
        ("всм ты не знал?", "в смысле"),
        ("чзх происходит", "что за хрень"),
    ]
    for raw, expected_substr in cases:
        norm, _ = SlangAbbreviationAnalyzer.normalize_slang(raw)
        assert expected_substr.lower() in norm.lower(), f"Expected '{expected_substr}' in '{norm}'"


def test_slang_words_preserved_as_words():
    # Words like 'лол', 'кек', 'изи', 'имхо', 'имба', 'кринж', 'видос' must NOT be spelled letter-by-letter
    words = [
        ("ахах это реально лол", "лол"),
        ("ну это чисто кек", "кек"),
        ("экзамен сдала изи", "и́зи"),
        ("имхо это лучший вариант", "и́мхо"),
        ("это просто имба", "имба́"),
        ("какой-то кринж", "кринж"),
        ("классный видос", "видо́с"),
        ("поставь на аву", "а́ву"),
        ("жизненный мем", "мем"),
    ]
    for raw, expected_form in words:
        norm, _ = SlangAbbreviationAnalyzer.normalize_slang(raw)
        assert expected_form.lower() in norm.lower(), f"Expected '{expected_form}' in '{norm}'"


def test_russian_e_and_lexical_stress_protection():
    """
    CRITICAL: The letter 'Е' must NOT be mechanically turned into open 'Э' sound.
    Cyrillic words must keep natural Russian soft consonants and accurate lexical stress.
    """
    words_and_forbidden = [
        ("пойдем выпьем кофе в кафе", "ко́фе", "ко́фэ"),
        ("пойдем выпьем кофе в кафе", "кафе́", "кафэ́"),
        ("послушай этот трек", "трек", "трэк"),
        ("окей, я тебя поняла", "окей", "окэй"),
        ("я тебе напишу сегодня", "тебе́", "тэбэ"),
        ("вместе проведем этот вечер", "вме́сте", "вмэстэ"),
        ("красивее платья я не видела", "краси́вее", None),
        ("он мне обязательно позвонит", "позвони́т", None),
        ("я была очень занята", "занята́", None),
        ("ты меня правильно поняла", "поняла́", None),
    ]

    for raw, expected_match, forbidden_substr in words_and_forbidden:
        prep = VoiceService.prepare_text_for_tts(raw)
        if expected_match:
            assert expected_match.lower() in prep.lower(), f"Expected '{expected_match}' in '{prep}'"
        if forbidden_substr:
            assert forbidden_substr.lower() not in prep.lower(), f"Forbidden '{forbidden_substr}' found in '{prep}'"


def test_smartphone_dirt_filter_specifications():
    tex = VoiceService.SUBTLE_TEXTURE_FILTER_COMPLEX
    eq = VoiceService.MASTERING_EQ_FILTER

    # 1. 6-band EQ
    assert "highpass=f=62:poles=2" in eq
    assert "equalizer=f=194" in eq
    assert "equalizer=f=437" in eq
    assert "equalizer=f=800" in eq
    assert "equalizer=f=2841" in eq
    assert "highshelf=f=6678" in eq

    # 2. Enhanced smartphone texture parameters
    assert "asoftclip=type=atan:param=1.18" in tex
    assert "lowpass=f=11800" in tex
    assert "anoisesrc=color=pink:amplitude=0.009" in tex
    assert "highpass=f=120" in tex
    assert "lowpass=f=6500" in tex
    assert "acompressor=threshold=-19dB:ratio=2.7:attack=12:release=100:makeup=7.5dB" in tex
    assert "alimiter=limit=-1.0dB" in tex


def test_pronunciation_debug_logging(caplog):
    import logging
    with caplog.at_level(logging.DEBUG):
        res = VoiceService.prepare_text_for_tts("скинь гс в тг пж")
        assert "голосовое" in res
        assert any(w in res for w in ("в телегу", "в телеге", "телеграм"))
        assert "пожалуйста" in res


def test_smartphone_microphone_filter_preset_c_specifications():
    """
    Validates the new authentic smartphone microphone processing preset (Preset C):
    - Reduces studio condenser brightness (highshelf 7.5kHz -3.5dB, lowpass 10.8kHz)
    - Authentic smartphone acoustic coloration (HPF 82Hz poles=2, 280Hz -1.2dB dip, 3.4kHz +0.6dB bump)
    - Dynamic compression: ratio 2.4:1, attack 15ms, release 110ms, threshold -20dB, makeup 3.5dB
    - Mild harmonic saturation: asoftclip atan param=1.08
    - Microphone pink noise bed: 120-6500Hz
    - Peak limiting: alimiter -1.0dB
    """
    eq = VoiceService.SMARTPHONE_EQ_FILTER
    mic = VoiceService.SMARTPHONE_MIC_FILTER_COMPLEX

    assert "highpass=f=82:poles=2" in eq
    assert "equalizer=f=280:width_type=q:width=1.2:gain=-1.2" in eq
    assert "equalizer=f=3400:width_type=q:width=1.4:gain=0.6" in eq
    assert "highshelf=f=7500:gain=-3.5" in eq
    assert "lowpass=f=10800:poles=2" in eq

    assert "acompressor=threshold=-20dB:ratio=2.4:attack=15:release=110:makeup=3.5dB" in mic
    assert "asoftclip=type=atan:param=1.08" in mic
    assert "anoisesrc=color=pink:amplitude=0.0075" in mic
    assert "highpass=f=120,lowpass=f=6500" in mic
    assert "alimiter=limit=-1.0dB:attack=5:release=50[out]" in mic


@pytest.mark.asyncio
async def test_ab_presets_execution_and_ogg_opus_output(tmp_path):
    """
    Verifies that Presets A, B, and C can process raw audio into valid OGG Opus.
    """
    import subprocess
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=800:duration=1", "-f", "mp3", "pipe:1"]
    raw_mp3 = subprocess.run(cmd, capture_output=True).stdout
    assert len(raw_mp3) > 100

    out_a = VoiceService.apply_mastering_equalizer(raw_mp3, preset="A")
    out_b = VoiceService.apply_mastering_equalizer(raw_mp3, preset="B")
    out_c = VoiceService.apply_mastering_equalizer(raw_mp3, preset="C")

    assert out_a.startswith(b"OggS")
    assert out_b.startswith(b"OggS")
    assert out_c.startswith(b"OggS")
    assert len(out_c) > 100
