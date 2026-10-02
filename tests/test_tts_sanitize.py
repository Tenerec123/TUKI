"""Tests for the streaming markdown sanitizer.

The sanitizer is the highest-risk piece of the TTS swap: it runs on a token
stream, so every case here is checked TWICE — once as one shot, and once fed
ONE CHARACTER AT A TIME, which is the worst case a token stream can produce.
A construct split mid-marker ("**bo" + "ld**") must survive that split.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ai.tts.sanitize import MarkdownSanitizer, sanitize


def charwise(text: str) -> str:
    """Sanitize ``text`` one character at a time — the worst-case token stream."""
    sanitizer = MarkdownSanitizer()
    out: list[str] = []
    for ch in text:
        out.extend(sanitizer.push(ch))
    out.extend(sanitizer.flush())
    return " ".join(out)


def both_ways(text: str) -> str:
    """Assert one-shot and char-wise agree, then return the result."""
    once = sanitize(text)
    assert charwise(text) == once, f"one-shot {once!r} != char-wise {charwise(text)!r}"
    return once


# --- strip, keep the meaning -----------------------------------------------

@pytest.mark.parametrize("src,expected", [
    ("**bold**", "bold"),
    ("*italic*", "italic"),
    ("_italic_", "italic"),
    ("__bold__", "bold"),
    ("***both***", "both"),
    ("~~strike~~", "strike"),
    ("# Titulo", "Titulo"),
    ("###### Sub", "Sub"),
    ("- uno", "uno"),
    ("* bullet", "bullet"),
    ("+ mas", "mas"),
    ("1. primero", "primero"),
    ("> cita", "cita"),
    ("`codigo`", "codigo"),
    ("[texto](https://x.dev)", "texto"),
])
def test_markdown_is_stripped(src, expected):
    assert both_ways(src) == expected


def test_bare_url_is_dropped_but_the_sentence_survives():
    assert both_ways("Ver https://x.dev ahora") == "Ver ahora"


def test_fenced_code_block_content_is_dropped():
    text = "Antes.\n```python\nprint('hola')\n```\nDespues."
    assert both_ways(text) == "Antes. Despues."


def test_horizontal_rule_is_dropped():
    # The rule is a block break, so the two sides become separate sentences.
    assert both_ways("a\n---\nb") == "a. b"


def test_table_collapses_to_a_comma_list():
    text = "| Nombre | Edad |\n|---|---|\n| Ana | 30 |"
    assert both_ways(text) == "Nombre, Edad. Ana, 30"


# --- DO NOT BREAK: numbers, dates, Spanish ---------------------------------

@pytest.mark.parametrize("src", [
    "2026-09-29",          # ISO date: let Aura-2 pronounce it
    "3-4",                 # range
    "3.5",                 # decimal
    "2.0",
    "$1.50",               # currency
    "10%",                 # percentage
    "-5 grados",           # negative number, not a bullet
    "¿Que tal?",           # Spanish opening punctuation
    "¡Genial!",
])
def test_speech_bearing_characters_survive(src):
    assert both_ways(src) == src


def test_snake_case_is_not_emphasis():
    assert both_ways("snake_case y my_module.py") == "snake_case y my_module.py"


def test_init_dunder_is_rendered_as_bold_not_as_a_word():
    # CommonMark really does read __init__ as bold, so the LLM meant bold.
    assert both_ways("__init__") == "init"


def test_multiplication_asterisk_is_not_emphasis():
    # A padded "*" is arithmetic; a padded "*" at line start is a bullet.
    assert both_ways("2 * 3") == "2 × 3"


def test_decimal_split_across_two_tokens_is_not_broken():
    # The seam lands between "2." and "5": the previous chunk ends on a period,
    # so the glue inserts nothing and the number stays intact.
    assert both_ways("El peso es 2.5 kg") == "El peso es 2.5 kg"


def test_emoji_are_dropped():
    assert both_ways("Listo 🎉🔥 ahora") == "Listo ahora"


def test_bare_pipe_is_dropped():
    assert both_ways("a | b") == "a b"


# --- line-break handling ----------------------------------------------------

def test_paragraph_break_becomes_a_sentence_boundary():
    assert both_ways("Uno\n\nDos") == "Uno. Dos"


def test_line_break_after_a_comma_does_not_become_a_period():
    assert both_ways("uno,\ndos") == "uno, dos"


def test_line_break_after_a_period_inserts_nothing():
    assert both_ways("Uno.\nDos") == "Uno. Dos"


# --- streaming invariants ---------------------------------------------------

def test_chunks_concatenate_into_the_same_text_as_one_shot():
    text = (
        "Hola **mundo**, esto es **una prueba** de *markdown* con `codigo` "
        "y [enlace](https://a.dev). El plazo es 2026-09-29 y son las 3.5."
    )
    sanitizer = MarkdownSanitizer()
    pieces: list[str] = []
    for i in range(0, len(text), 3):
        pieces.extend(sanitizer.push(text[i:i + 3]))
    pieces.extend(sanitizer.flush())
    assert " ".join(p for p in pieces if p) == sanitize(text)


def test_holdback_never_loses_text():
    """Whatever is emitted plus whatever is held must equal the input."""
    text = "**nada** de esto se **pierde** y `codigo` tampoco"
    sanitizer = MarkdownSanitizer()
    pieces: list[str] = []
    for char in text:
        pieces.extend(sanitizer.push(char))
    pieces.extend(sanitizer.flush())
    assert " ".join(p for p in pieces if p) == sanitize(text)
    # And the counters must agree with reality.
    assert sanitizer.chars_in == len(text)
    assert sanitizer.chars_out == sum(len(p) for p in pieces)


def test_sanitizer_reports_savings():
    sanitizer = MarkdownSanitizer()
    sanitizer.push("**a** and `b`")
    sanitizer.flush()
    assert sanitizer.chars_in == 13
    assert sanitizer.chars_out < sanitizer.chars_in
    assert sanitizer.saved_chars == sanitizer.chars_in - sanitizer.chars_out


def test_empty_input_is_safe():
    sanitizer = MarkdownSanitizer()
    assert sanitizer.push("") == []
    assert sanitizer.flush() == []
    assert sanitizer.chars_out == 0
