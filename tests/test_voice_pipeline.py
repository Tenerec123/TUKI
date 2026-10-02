import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # runnable without installing the package
    sys.path.insert(0, str(REPO_ROOT))

from backend.ai.tts.sanitize import MarkdownSanitizer, PhraseSplitter, is_sentence_end


# --- is_sentence_end --------------------------------------------------------

def test_terminators_end_a_phrase():
    for text in ("Hola?", "Hola!", "Hola\n"):
        assert is_sentence_end(text, len(text) - 1), text
    for text in ("Hola,", "Hola ", "Hola", "Hola-"):
        assert not is_sentence_end(text, len(text) - 1), text
    # A dot followed by whitespace is unambiguous: the edge case is only the dot
    # itself sitting on the buffer boundary.
    assert is_sentence_end("Hola. ", 4)


def test_a_trailing_dot_waits_for_the_next_token():
    """A "." on the buffer edge is undecided: it can still become "x.dev".

    Streaming stays conservative here and release()/flush() do the forcing, so a
    dot that looks like a sentence end mid-token never cuts a URL in half.
    """
    assert not is_sentence_end("Hola.", 4)
    assert is_sentence_end("Hola.", 4, decided=True)
    assert not is_sentence_end("https://x.", 9)
    assert not is_sentence_end("El peso es 2.", 11)


def test_decimals_do_not_end_a_phrase():
    assert not is_sentence_end("3.5", 1)
    assert not is_sentence_end("2.5", 1)
    assert not is_sentence_end("2.5 kg", 1)


def test_trailing_dot_after_digit_is_undecided():
    # "2." so far: the next token may still be the fractional part.
    assert not is_sentence_end("2.", 1)
    assert is_sentence_end("2. ", 1)
    assert is_sentence_end("2. kilos", 1)


def test_dots_inside_a_url_do_not_end_a_phrase():
    # A bare URL is stripped before speech, but the splitter still sees the raw
    # text first: a dot with no whitespace after it is mid-token, not a boundary.
    for text, index in (("x.dev", 1), ("https://a.dev/b", 9), ("ver 3.14.15 ok", 12)):
        assert not is_sentence_end(text, index), text


# --- PhraseSplitter ---------------------------------------------------------

def _take(chunks):
    splitter = PhraseSplitter()
    out = []
    for chunk in chunks:
        out.extend(splitter.take(chunk))
    return out, splitter.release()


def test_multi_sentence_chunk_yields_one_phrase_each():
    phrases, _ = _take(["Uno. Dos! Tres?"])
    assert phrases == ["Uno.", "Dos!", "Tres?"]


def test_decimal_is_not_split_and_is_deferred():
    phrases, rest = _take(["El peso es 2."])
    assert phrases == []
    assert rest == "El peso es 2."
    # The trailing "." is the single character a later token can still decide.
    phrases, rest = _take(["El peso es 2.", "5 kg"])
    assert phrases == []
    assert rest == "El peso es 2.5 kg"


def test_deferred_decimal_is_resolved_by_the_next_token():
    phrases, _ = _take(["El peso es 2.", " kg"])
    assert phrases == ["El peso es 2."]


def test_punctuation_only_segments_are_dropped():
    # The splitter only finds boundaries; cleaning is the sanitizer's job, so the
    # leading dots are stripped through the full pipeline.
    assert _take(["...???"]) == ([], "")
    assert _drain("...Hola... ") == ["Hola."]
    assert _drain("..Hola. ") == ["Hola."]


def test_resuming_the_scan_matches_a_full_rescan():
    """The scan offset must never skip a terminator a fresh walk would find."""
    chunks = ["El ", "peso ", "es ", "2.5 ", "kilos.\n", "Segundo", " dia."]
    expected = ["El peso es 2.5 kilos.", "Segundo dia."]

    streamed, rest = _take(chunks)
    assert streamed + [r for r in [rest] if r] == expected

    full = PhraseSplitter()
    rescanned = []
    for chunk in chunks:
        rescanned.extend(full.take(chunk))
    rescanned.extend(p for p in [full.drain().strip()] if p)
    assert rescanned == expected


# --- streaming release (the regression this whole move exists for) ----------

def test_sentences_release_without_waiting_for_a_newline():
    """An LLM writes a whole reply on one line and never promises a "\\n".

    Holding prose until a newline meant every sentence was spoken only after the
    model had finished, so the audio was all-or-nothing regardless of how fast
    the provider was.
    """
    sanitizer = MarkdownSanitizer()
    assert sanitizer.push("Listo. ") == ["Listo."]
    assert sanitizer.push("Cree las diez tareas. ") == ["Cree las diez tareas."]
    # No trailing space: the dot sits on the buffer edge, so it waits for proof
    # and then closes the turn at flush.
    assert sanitizer.push("Todo listo.") == []
    assert sanitizer.flush() == ["Todo listo."]


def test_release_drains_a_boundary_without_closing_the_sanitizer():
    """release() must not corrupt state: the next inference resumes cleanly."""
    sanitizer = MarkdownSanitizer()
    assert sanitizer.push("Voy a revisar") == []
    assert sanitizer.release() == ["Voy a revisar"]
    # The next inference continues as a new turn; nothing merges across the seam.
    assert sanitizer.push(" tus tareas.") == []
    assert sanitizer.flush() == ["tus tareas."]


def test_flush_emits_an_unterminated_tail():
    sanitizer = MarkdownSanitizer()
    assert sanitizer.push("sin punto") == []
    assert sanitizer.flush() == ["sin punto"]


def test_flush_never_repeats_what_push_already_emitted():
    """The producer keeps the last batch around after sending it.

    Its tail handler used to re-append that batch, which made TTS speak the last
    sentence of every turn twice, so the contract is pinned here.
    """
    sanitizer = MarkdownSanitizer()
    sent = list(sanitizer.push("Voy a revisar. "))
    sent += list(sanitizer.push("Segundo dia. "))
    tail = sanitizer.flush()
    assert sent == ["Voy a revisar.", "Segundo dia."]
    assert tail == []


def test_a_turn_survives_an_inference_end_with_no_trailing_punctuation():
    """inference_end is the only thing that can rescue a dotless tail."""
    sanitizer = MarkdownSanitizer()
    assert sanitizer.push("Listo") == []
    assert sanitizer.release() == ["Listo"]
    assert sanitizer.flush() == []


def test_markdown_is_cleaned_at_a_sentence_boundary():
    from backend.ai.tts.sanitize import sanitize

    assert sanitize("**bold** y [link](http://x.dev)") == "bold y link"
    assert sanitize("Costo 2.0 hoy.") == "Costo 2.0 hoy."


def test_a_fence_open_across_a_release_does_not_corrupt_later_text():
    sanitizer = MarkdownSanitizer()
    sanitizer.push("```py\n")
    sanitizer.push("print(1)\n")
    sanitizer.release()
    assert sanitizer.flush() == []


# --- regressions ------------------------------------------------------------

def _drain(text):
    """Turns the pipeline would push for ``text``."""
    sanitizer = MarkdownSanitizer()
    return sanitizer.push(text) + sanitizer.flush()


def test_b1_crlf_never_produces_an_empty_phrase():
    """An empty turn reaching the provider is a 4xx and loses the utterance."""
    phrases = _drain("Hola\r\n\r\nHola.")
    assert all(p.strip() for p in phrases), phrases
    # Each phrase is its own TTS turn, so the only requirement is that no turn is
    # empty and that no "\r" leaks into the text the provider receives.
    assert " ".join(phrases) == "Hola. Hola."
    assert not any("\r" in p for p in phrases), phrases


@pytest.mark.parametrize("text", ["", "   ", "\n", "....", "?!"])
def test_drain_never_yields_an_empty_turn(text):
    """An empty Speak message is a guaranteed provider error, so guard it."""
    for phrase in _drain(text):
        assert phrase.strip(), repr(text)
        assert phrase.strip(" \t\r\n.!?¿¡…"), repr(text)


def test_every_flushed_unit_carries_speech_content():
    """Whatever reaches push_text must be speakable, not just non-empty."""
    for text in ("Buenos dias\n", "Sus pasos.\n", "El reloj\n", "2.5 kg\n", "Final sin punto"):
        turns = _drain(text)
        assert turns, text
        for turn in turns:
            assert turn.strip(), text
            assert any(ch.isalnum() for ch in turn), turn
