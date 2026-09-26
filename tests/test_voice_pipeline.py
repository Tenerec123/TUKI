"""Tests for the pure helpers of the streaming voice pipeline.

Both critical defects found in this layer are regressions here: an empty
phrase reaching TTS (provider 4xx, whole utterance lost) and a phrase whose
completeness marker was destroyed before the sibilant gate could see it.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # runnable without installing the package
    sys.path.insert(0, str(REPO_ROOT))

from backend.ai.tts import _stretch_final_sibilant, mark_phrase_complete
from backend.ai.voice_agent import _is_sentence_end, _split_phrases


# --- _is_sentence_end -------------------------------------------------------

def test_terminators_end_a_phrase():
    for text in ("Hola.", "Hola?", "Hola!", "Hola\n"):
        assert _is_sentence_end(text, len(text) - 1), text
    for text in ("Hola,", "Hola ", "Hola", "Hola-"):
        assert not _is_sentence_end(text, len(text) - 1), text


def test_decimals_do_not_end_a_phrase():
    assert not _is_sentence_end("3.5", 1)
    assert not _is_sentence_end("2.5", 1)
    assert not _is_sentence_end("2.5 kg", 1)


def test_trailing_dot_after_digit_is_undecided():
    # "2." so far: the next token may still be the fractional part.
    assert not _is_sentence_end("2.", 1)
    assert _is_sentence_end("2. ", 1)
    assert _is_sentence_end("2. kilos", 1)


# --- _split_phrases ---------------------------------------------------------

def test_multi_sentence_chunk_yields_one_phrase_each():
    phrases, rest, _ = _split_phrases("Uno. Dos! Tres?")
    assert phrases == ["Uno.", "Dos!", "Tres?"]
    assert rest == ""


def test_split_stops_at_the_first_terminator():
    phrases, rest, _ = _split_phrases("Uno. Dos")
    assert phrases == ["Uno."]
    assert rest == " Dos"


def test_decimal_is_not_split_and_is_deferred():
    phrases, rest, resume = _split_phrases("El peso es 2.")
    assert phrases == []
    assert rest == "El peso es 2."
    # The trailing "." is the single character a later token can still decide.
    assert resume == len(rest) - 1
    phrases, rest, _ = _split_phrases(rest + "5 kg", resume)
    assert phrases == []
    assert rest == "El peso es 2.5 kg"


def test_deferred_decimal_is_flushed_by_the_next_token():
    _, rest, resume = _split_phrases("El peso es 2.")
    phrases, rest, _ = _split_phrases(rest + " kg", resume)
    assert phrases == ["El peso es 2."]
    assert rest == " kg"


def test_punctuation_only_segments_are_dropped():
    # Split at the first terminator, so only the text up to it is spoken and
    # the trailing ".." is discarded as punctuation.
    assert _split_phrases("...Hola...")[0] == ["Hola."]
    phrases, rest, _ = _split_phrases("...???")
    assert phrases == []
    assert rest == ""
    assert _split_phrases("..Hola.")[0] == ["Hola."]


def test_resuming_the_scan_matches_a_full_rescan():
    """The scan offset must never skip a terminator a fresh walk would find."""
    chunks = ["El ", "peso ", "es ", "2.5 ", "kilos.\n", "Segundo", " dia."]
    expected = ["El peso es 2.5 kilos.", "Segundo dia."]

    buffer, scan_from, streamed = "", 0, []
    for chunk in chunks:
        buffer += chunk
        phrases, buffer, scan_from = _split_phrases(buffer, scan_from)
        streamed.extend(phrases)
    assert streamed == expected

    buffer, rescanned = "", []
    for chunk in chunks:
        buffer += chunk
        phrases, buffer, _ = _split_phrases(buffer)
        rescanned.extend(phrases)
    assert rescanned == expected


# --- completeness marking ---------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Hola.", "Hola."),                    # already complete
    ("¿Que tal?", "¿Que tal?"),
    ("Hola…", "Hola…"),
    ("Buenos dias", "Buenos dias."),       # no visible terminator to read
    ("Buenos dias\n", "Buenos dias."),
    ("Buenos dias   ", "Buenos dias."),
    ("", ""),
])
def test_mark_phrase_complete(text, expected):
    assert mark_phrase_complete(text) == expected


# --- sibilant transform -----------------------------------------------------

def test_gate_rejects_an_open_ended_fragment():
    assert _stretch_final_sibilant("las") == "las"
    assert _stretch_final_sibilant("Buenos dias") == "Buenos dias"


def test_gate_accepts_a_complete_phrase():
    assert _stretch_final_sibilant("Dos.") == "Doss."
    assert _stretch_final_sibilant("Sus pasos.") == "Sus pasoss."
    assert _stretch_final_sibilant("Menos...") == "Menoss..."


def test_phrase_without_final_sibilant_is_untouched():
    assert _stretch_final_sibilant("El reloj.") == "El reloj."


def test_z_branch_falls_back_to_ellipsis():
    # Known accepted behavior: the z branch replaces the phrase with an
    # ellipsis (dropping the terminator). Pinned so it cannot change silently.
    assert _stretch_final_sibilant("Buzz.") == "Buzz..."


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_gate_ignores_text_without_content(text):
    assert _stretch_final_sibilant(text) == text


# --- regressions ------------------------------------------------------------

def _drain(text):
    """Phrases the pipeline would synthesize for ``text``.

    Mirrors the producer: the splitter first, then the trailing remainder
    flushed as the last phrase when the stream ends.
    """
    phrases, rest, _ = _split_phrases(text)
    if rest.strip():
        phrases.append(mark_phrase_complete(rest.strip()))
    return phrases


def test_b1_crlf_never_produces_an_empty_phrase():
    """An empty phrase reached the provider, which 4xx'd and lost the utterance."""
    phrases, rest, _ = _split_phrases("Hola\r\n\r\nHola.")
    assert phrases == ["Hola.", "Hola."]
    assert rest == ""
    assert all(phrase.strip() for phrase in phrases)


def test_b2_newline_terminated_phrase_survives_the_sibilant_gate():
    """A "\\n"-terminated line used to arrive unmarked, so the gate skipped it."""
    phrases, rest, _ = _split_phrases("Buenos dias\n")
    assert phrases == ["Buenos dias."]
    assert rest == ""
    assert _stretch_final_sibilant(phrases[0]) == "Buenos diass."


def test_b2_every_emitted_phrase_passes_the_gate():
    """No phrase handed to TTS may be left unmarked for the sibilant workaround."""
    for text in ("Buenos dias\n", "Sus pasos.\n", "El reloj\n", "2.5 kg\n", "Final sin punto"):
        phrases = _drain(text)
        assert phrases, text
        for phrase in phrases:
            assert phrase.rstrip("!?").endswith((".", "…")), phrase
