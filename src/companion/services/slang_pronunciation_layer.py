"""
Dedicated Slang, Abbreviation & Pronunciation Normalization Layer.

Pipelines conversational Telegram responses before TTS:
AI Response
  -> Slang / Abbreviation Semantic Normalization
  -> Russian 'Е' & Lexical Stress Protection
  -> Pronunciation Dictionary Overrides
  -> Prosody Controller & Thought Flow
  -> ElevenLabs TTS
  -> Subtle Smartphone Microphone Audio Processing
  -> Telegram Voice Note
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SlangDecision:
    surface_form: str
    normalized_spoken_form: str
    reason: str
    confidence: float = 0.95
    action: str = "expand"  # "expand" | "pronounce_as_word" | "lexical_stress" | "keep"


class SlangAbbreviationAnalyzer:
    """
    Context-aware Telegram slang & abbreviation analyzer.
    Understands chat acronyms (гс, лс, тг, вк, мб, крч, спс, пж, хз, всм)
    and expands them into grammatically authentic spoken forms, while preserving
    lexical words (лол, кек, кринж, вайб, изи, имхо, имба) without spelling them out letter-by-letter.
    """

    # Verbs that typically take direct object in accusative ("голосовое")
    TRANSITIVE_VOICE_VERBS = (
        r"(?:скинь|скинуть|скинула|скину|скинет|скиньте|"
        r"запиши|записать|записала|запишу|запишет|запишите|"
        r"отправь|отправить|отправила|отправлю|отправит|отправьте|"
        r"послушай|послушать|послушала|послушаю|послушает|послушайте|"
        r"слушай|слушать|слушаю|слушает|слушайте|"
        r"пришли|прислать|прислала|пришлю|пришлет|пришлите|"
        r"запишем|скинем|отправим|послушаем|"
        r"хочу|жду|люблю|жду|обожаю)"
    )

    @classmethod
    def normalize_slang(cls, text: str) -> Tuple[str, List[SlangDecision]]:
        """
        Contextually normalizes Telegram chat abbreviations into natural spoken Russian.
        Returns (normalized_text, list_of_decisions).
        """
        if not text:
            return text, []

        decisions: List[SlangDecision] = []
        result = text

        # ---------------------------------------------------------
        # 1. "ГС" (Telegram voice message) — Highly contextual
        # ---------------------------------------------------------
        # A. Prepositional / Locative: "в гс" / "во гс" -> "в голосовом"
        def repl_v_gs(m):
            prep = m.group(1)
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{prep} голосовом",
                    reason="Prepositional locative Telegram voice note",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{prep} голосовом"

        result = re.sub(r"(?i)\b(в|во)\s+гс\b", repl_v_gs, result)

        # B. Directional: "на гс" -> "на голосовое"
        def repl_na_gs(m):
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form="на голосовое",
                    reason="Directional response to voice note",
                    confidence=0.99,
                    action="expand",
                )
            )
            return "на голосовое"

        result = re.sub(r"(?i)\bна\s+гс\b", repl_na_gs, result)

        # C. Instrumental: "с гс", "с твоим гс", "твоим гс" -> "с голосовым" / "твоим голосовым"
        def repl_instr_pron_gs(m):
            prep = m.group(1) or ""
            pron = m.group(2)
            prefix = f"{prep} ".lstrip()
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{prefix}{pron} голосовым",
                    reason="Instrumental pronoun agreement with voice note",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{prefix}{pron} голосовым"

        result = re.sub(r"(?i)\b(?:(с|со)\s+)?(твоим|моим|этим)\s+гс\b", repl_instr_pron_gs, result)

        def repl_s_gs(m):
            prep = m.group(1)
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{prep} голосовым",
                    reason="Instrumental voice note",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{prep} голосовым"

        result = re.sub(r"(?i)\b(с|со)\s+гс\b", repl_s_gs, result)

        # D. Genitive: "после гс", "до гс", "без гс", "от гс", "из гс" -> "после голосового"
        def repl_gen_gs(m):
            prep = m.group(1)
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{prep} голосового",
                    reason="Genitive voice note",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{prep} голосового"

        result = re.sub(r"(?i)\b(после|до|без|от|из)\s+гс\b", repl_gen_gs, result)

        # E. Demonstrative / Possessive agreement:
        # "твое гс", "твоё гс", "мое гс", "моё гс", "это гс" -> "твоё голосовое"
        def repl_pron_gs(m):
            pron = m.group(1)
            # normalize "твое" -> "твоё"
            pron_spoken = "твоё" if pron.lower() in ("твое", "твоё") else ("моё" if pron.lower() in ("мое", "моё") else "это")
            if pron[0].isupper():
                pron_spoken = pron_spoken.capitalize()
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{pron_spoken} голосовое",
                    reason="Neuter pronoun agreement with voice note",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{pron_spoken} голосовое"

        result = re.sub(r"(?i)\b(тво[её]|мо[её]|это)\s+гс\b", repl_pron_gs, result)

        # "твоем гс", "моем гс", "этом гс" -> "твоём голосовом"
        def repl_prep_pron_gs(m):
            pron = m.group(1)
            pron_spoken = "твоём" if "тво" in pron.lower() else ("моём" if "мо" in pron.lower() else "этом")
            if pron[0].isupper():
                pron_spoken = pron_spoken.capitalize()
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{pron_spoken} голосовом",
                    reason="Prepositional pronoun agreement with voice note",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{pron_spoken} голосовом"

        result = re.sub(r"(?i)\b(тво[её]м|мо[её]м|этом)\s+гс\b", repl_prep_pron_gs, result)

        # "твои гс", "мои гс", "эти гс", "все гс" -> "твои голосовые"
        def repl_plur_pron_gs(m):
            pron = m.group(1)
            pron_spoken = pron.lower()
            if pron[0].isupper():
                pron_spoken = pron_spoken.capitalize()
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{pron_spoken} голосовые",
                    reason="Plural pronoun agreement with voice notes",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{pron_spoken} голосовые"

        result = re.sub(r"(?i)\b(твои|мои|эти|все)\s+гс\b", repl_plur_pron_gs, result)

        # "много гс", "куча гс", "столько гс" -> "... голосовых"
        def repl_quant_gs(m):
            quant = m.group(1)
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{quant} голосовых",
                    reason="Quantifier genitive plural with voice notes",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{quant} голосовых"

        result = re.sub(r"(?i)\b(много|куча|столько|несколько)\s+гс\b", repl_quant_gs, result)

        # F. Verb + гс ("скинь гс", "запиши гс", "отправила гс", etc.)
        def repl_verb_gs(m):
            verb = m.group(1)
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{verb} голосовое",
                    reason="Transitive verb + voice note direct object",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{verb} голосовое"

        result = re.sub(rf"(?i)\b({cls.TRANSITIVE_VOICE_VERBS})\s+гс\b", repl_verb_gs, result)

        # G. Diminutives & plurals: "гску", "гс-ку", "гсочку", "гски"
        def repl_dim_gs(m):
            token = m.group(0)
            low = token.lower().replace("-", "")
            if "ку" in low:
                spoken = "голосовуху"
            elif "ки" in low:
                spoken = "голосовые"
            elif "очку" in low or "ку" in low:
                spoken = "голосовушку"
            else:
                spoken = "голосовое"
            decisions.append(
                SlangDecision(
                    surface_form=token,
                    normalized_spoken_form=spoken,
                    reason="Slang diminutive voice note suffix",
                    confidence=0.99,
                    action="expand",
                )
            )
            return spoken

        result = re.sub(r"(?i)\bгс[-_]?(ку|ки|очку)\b", repl_dim_gs, result)

        # H. Any remaining standalone "гс" -> "голосовое" (NEVER "гэ эс"!)
        def repl_standalone_gs(m):
            decisions.append(
                SlangDecision(
                    surface_form="гс",
                    normalized_spoken_form="голосовое",
                    reason="Standalone Telegram voice abbreviation",
                    confidence=0.98,
                    action="expand",
                )
            )
            return "голосовое"

        result = re.sub(r"(?i)\bгс\b", repl_standalone_gs, result)

        # ---------------------------------------------------------
        # 2. "ЛС" (личные сообщения / личка)
        # ---------------------------------------------------------
        def repl_v_ls(m):
            prep = m.group(1)
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{prep} личку",
                    reason="Directional/locative direct message",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{prep} личку"

        result = re.sub(r"(?i)\b(в|во)\s+лс\b", repl_v_ls, result)

        def repl_iz_ls(m):
            decisions.append(
                SlangDecision(
                    surface_form="из лс",
                    normalized_spoken_form="из лички",
                    reason="Ablative direct message",
                    confidence=0.99,
                    action="expand",
                )
            )
            return "из лички"

        result = re.sub(r"(?i)\bиз\s+лс\b", repl_iz_ls, result)

        def repl_check_ls(m):
            verb = m.group(1)
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form=f"{verb} личку",
                    reason="Imperative check DMs",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{verb} личку"

        result = re.sub(r"(?i)\b(чекни|чекай|проверь|глянь)\s+лс\b", repl_check_ls, result)

        def repl_standalone_ls(m):
            decisions.append(
                SlangDecision(
                    surface_form="лс",
                    normalized_spoken_form="личку",
                    reason="Telegram DM shorthand",
                    confidence=0.95,
                    action="expand",
                )
            )
            return "личку"

        result = re.sub(r"(?i)\bлс\b", repl_standalone_ls, result)

        # ---------------------------------------------------------
        # 3. "ТГ" (Telegram)
        # ---------------------------------------------------------
        def repl_into_tg(m):
            verb = m.group(1)
            decisions.append(
                SlangDecision(
                    surface_form=f"{verb} в тг",
                    normalized_spoken_form=f"{verb} в телегу",
                    reason="Directional Telegram shorthand",
                    confidence=0.99,
                    action="expand",
                )
            )
            return f"{verb} в телегу"

        result = re.sub(r"(?i)\b(скинь|кинь|напиши|перейдем|перейдём|заходи)\s+в\s+тг\b", repl_into_tg, result)

        def repl_v_tg(m):
            decisions.append(
                SlangDecision(
                    surface_form="в тг",
                    normalized_spoken_form="в телеге",
                    reason="Locative Telegram shorthand",
                    confidence=0.99,
                    action="expand",
                )
            )
            return "в телеге"

        result = re.sub(r"(?i)\bв\s+тг\b", repl_v_tg, result)

        def repl_my_tg(m):
            pron = m.group(1)
            spoken = "мой телеграм" if "мо" in pron.lower() else "твой телеграм"
            decisions.append(
                SlangDecision(
                    surface_form=f"{pron} тг",
                    normalized_spoken_form=spoken,
                    reason="Telegram handle shorthand",
                    confidence=0.99,
                    action="expand",
                )
            )
            return spoken

        result = re.sub(r"(?i)\b(мой|твой)\s+тг\b", repl_my_tg, result)

        def repl_tg_channel(m):
            decisions.append(
                SlangDecision(
                    surface_form=m.group(0),
                    normalized_spoken_form="телеграм-канал",
                    reason="Telegram channel shorthand",
                    confidence=0.99,
                    action="expand",
                )
            )
            return "телеграм-канал"

        result = re.sub(r"(?i)\bтг[- ]?канал\b", repl_tg_channel, result)

        def repl_standalone_tg(m):
            decisions.append(
                SlangDecision(
                    surface_form="тг",
                    normalized_spoken_form="телеграм",
                    reason="Standalone Telegram abbreviation",
                    confidence=0.95,
                    action="expand",
                )
            )
            return "телеграм"

        result = re.sub(r"(?i)\bтг\b", repl_standalone_tg, result)

        # ---------------------------------------------------------
        # 4. Common Conversational Chat Abbreviations
        # ---------------------------------------------------------
        chat_expansions = [
            (r"\bмб\b", "может быть", "Conversational 'может быть'", 0.98),
            (r"\b(крч|кароч)\b", "короче", "Conversational 'короче'", 0.98),
            (r"\b(щас|ща)\b", "сейчас", "Conversational 'сейчас'", 0.95),
            (r"\bспс\b", "спасибо", "Conversational 'спасибо'", 0.99),
            (r"\b(пж|пжл|плз|плиз)\b", "пожалуйста", "Conversational 'пожалуйста'", 0.98),
            (r"\bя\s+хз\b", "я не знаю", "Conversational 'я не знаю'", 0.99),
            (r"\bхз\b", "не знаю", "Conversational 'не знаю'", 0.95),
            (r"\bвсм\b", "в смысле", "Conversational 'в смысле'", 0.99),
            (r"\bчзх\b", "что за хрень", "Conversational 'что за хрень'", 0.99),
            (r"\b(норм|нормик)\b", "нормально", "Conversational 'нормально'", 0.95),
            (r"\b(споки|спк)\b", "спокойной ночи", "Conversational 'спокойной ночи'", 0.95),
            (r"\bнзч\b", "не за что", "Conversational 'не за что'", 0.99),
            (r"\bафк\b", "отошла", "Conversational gaming/chat AFK", 0.95),
            (r"\bбрб\b", "скоро буду", "Conversational BRB", 0.95),
            (r"\bвк\b", "вконтакте", "VKontakte social network", 0.95),
        ]

        for pattern, replacement, reason, conf in chat_expansions:
            if re.search(pattern, result, flags=re.IGNORECASE):
                decisions.append(
                    SlangDecision(
                        surface_form=re.findall(pattern, result, flags=re.IGNORECASE)[0] if isinstance(re.findall(pattern, result, flags=re.IGNORECASE)[0], str) else "",
                        normalized_spoken_form=replacement,
                        reason=reason,
                        confidence=conf,
                        action="expand",
                    )
                )
                result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

        # ---------------------------------------------------------
        # 5. Natural Words to KEEP as Words (with proper Russian lexical stress)
        # (Must NOT be spelled out letter-by-letter or mispronounced with 'э')
        # ---------------------------------------------------------
        words_to_pronounce_naturally = [
            (r"\bлол\b", "лол", "Slang 'лол' as spoken word", 0.99),
            (r"\bкек\b", "кек", "Slang 'кек' as spoken word", 0.99),
            (r"\bизи\b", "и́зи", "Slang 'изи' with stress on first syllable", 0.99),
            (r"\bимхо\b", "и́мхо", "Slang 'имхо' with stress on first syllable", 0.99),
            (r"\bимба\b", "имба́", "Slang 'имба' with stress on second syllable", 0.99),
            (r"\bкринж\b", "кринж", "Slang 'кринж' as spoken word", 0.99),
            (r"\bкринжово\b", "кринжо́во", "Slang 'кринжово' with lexical stress", 0.99),
            (r"\bвайб\b", "вайб", "Slang 'вайб' as spoken word", 0.99),
            (r"\bвайбово\b", "вайбо́во", "Slang 'вайбово' with lexical stress", 0.99),
            (r"\bкраш\b", "краш", "Slang 'краш' as spoken word", 0.99),
            (r"\bвидос\b", "видо́с", "Slang 'видос' with lexical stress on о", 0.99),
            (r"\bвидосы\b", "видо́сы", "Slang 'видосы' with lexical stress on о", 0.99),
            (r"\bвидосик\b", "видо́сик", "Slang 'видосик' with lexical stress on о", 0.99),
            (r"\bаву\b", "а́ву", "Slang 'аву' with lexical stress on а", 0.99),
            (r"\bаватарка\b", "авата́рка", "Slang 'аватарка' with lexical stress", 0.99),
            (r"\bаватарку\b", "авата́рку", "Slang 'аватарку' with lexical stress", 0.99),
            (r"\bмем\b", "мем", "Slang 'мем' with soft Cyrillic е (not мэм)", 0.99),
            (r"\bмемы\b", "ме́мы", "Slang 'мемы' with soft Cyrillic е", 0.99),
            (r"\bмемчик\b", "ме́мчик", "Slang 'мемчик' with soft Cyrillic е", 0.99),
        ]

        for pattern, replacement, reason, conf in words_to_pronounce_naturally:
            if re.search(pattern, result, flags=re.IGNORECASE):
                decisions.append(
                    SlangDecision(
                        surface_form=re.findall(pattern, result, flags=re.IGNORECASE)[0],
                        normalized_spoken_form=replacement,
                        reason=reason,
                        confidence=conf,
                        action="pronounce_as_word",
                    )
                )
                result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

        return result, decisions
