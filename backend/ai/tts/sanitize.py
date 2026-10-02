"""Streaming markdown sanitizer for the voice pipeline.

Speaking raw markdown produces garbage ("star star bold star star") and every marker is
billed against Deepgram's 2400 chars/min cap. The hard part is the TOKEN STREAM: a token
can be a bare ``*`` or half a code fence, so naive per-token regex destroys meaning. Hence
a state machine with a HOLDBACK TAIL: only the prefix that cannot be part of an
unterminated construct is emitted, the rest is re-examined on the next push.

Deliberately NOT done: numbers are never spelled out (Aura-2 is tuned for numerals, dates,
currency, emails and URLs, so ``2026-09-29`` and ``$1.50`` pass through and the MODEL
pronounces them), and Spanish punctuation is never dropped (``¿``/``¡`` survive).
"""

import re
import unicodedata

# --- block-level constructs (only ever match at the start of a line) ---------

# Heading, blockquote, bullet, ordered item. The trailing space is required so "1." mid-sentence and "-5 grados" keep their meaning.
_BLOCK_PREFIX = re.compile(r"^(?:#{1,6}[ \t]+|>+[ \t]?|[-*+][ \t]+|\d{1,9}[.)][ \t]+)")

_RULE = re.compile(r"^[-*_=~]{3,}$")

# Code fence; Markdown allows 3+ of either char.
_FENCE = re.compile(r"^([`~]{3,})")

_TABLE_SEPARATOR = re.compile(r"^[\s|:\-]+$")


# --- inline constructs -------------------------------------------------------

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# A reference/footnote label with no target: keep the text, drop the brackets.
_LABEL = re.compile(r"\[([^\]]*)\]")
# Bare URLs are unreadable aloud; trailing punctuation stays out so "see https://x.dev." still ends the sentence.
_BARE_URL = re.compile(r"(?:https?://|www\.)\S*[^\s.,;:!?)\]]")
_INLINE_CODE = re.compile(r"`+([^`]*)`+")
_STRIKE = re.compile(r"~~(.+?)~~")
_BOLD = re.compile(r"(\*\*\*|\*\*|___|__)(?=\S)(.+?)(?<=\S)\1", re.S)
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?![\w*])")
# Intraword underscores (snake_case, my_module.py) are never emphasis: a "_" with a word char on either side is rejected.
_ITALIC_US = re.compile(r"(?<!\w)_(?=\S)([^_]+?)(?<=\S)_(?!\w)")
# A "*" padded with spaces is multiplication, not emphasis. U+00D7 reads as "por" and keeps the ASCII asterisk out of speech.
_MULT_STAR = re.compile(r"(?<=\s)\*(?=\s)")
_TRAILING_MARK = re.compile(r"(\*{1,3}|_{1,3}|~{1,2}|`{1,3}|\]\(|\[)")

_SPACES = re.compile(r"[ \t]{2,}")


# --- glyphs no voice can render ---------------------------------------------

# Dropped: So, Sk, Cs, Co, Cn, Cf. Kept: letters, digits, punctuation, whitespace and Sm, which is what preserves "¿", "¡", "…", "-", "%", "$" and "×".
_DROP_CATEGORIES = frozenset({"So", "Sk", "Cs", "Co", "Cn", "Cf"})

# After one of these a break needs a plain space, not a new sentence: the chunk already carries the punctuation.
_TERMINATORS = ".!?…"

# Same for a mid-clause mark: "costo" + "alto" is one idea and a period there is misheard as an abbreviation.
_JOIN_SPACE = ",;:—"

# A holdback that reaches this far back is not emphasis we are waiting on.
_MAX_HOLDBACK = 200


def _drop_non_speech(text: str) -> str:
    """Erase glyphs no voice can render, keeping every speech-bearing one."""
    return "".join(
        ch for ch in text
        if unicodedata.category(ch) not in _DROP_CATEGORIES
    )


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _is_emphasis_candidate(line: str, start: int, end: int, char: str) -> bool:
    """Report whether the run at ``[start:end)`` could open an emphasis span.

    Three exclusions: a single "_" between word chars is ``snake_case`` (2+ is exempt, because ``__init__`` really is bold in CommonMark); a single "_" alone at line start is an identifier, and holding it pins the line forever; a marker padded by whitespace on BOTH sides is punctuation, which separates ``* item`` and ``2 * 3`` from ``*emphasis*``. Line end counts as whitespace: a lone marker there is far more often a bullet than an emphasis closing three lines on.
    """
    run = end - start
    before = line[start - 1] if start > 0 else ""
    after = line[end] if end < len(line) else ""
    if char == "_" and run == 1:
        if _is_word_char(before) and _is_word_char(after):
            return False
        if not before and not after:
            return False
    left = before or " "
    right = after or " "
    if left.isspace() and right.isspace():
        return False
    return True


def _has_closer(line: str, start: int, end: int) -> bool:
    """Report whether a closing marker of the same run follows on this line."""
    run = line[start:end]
    char = run[0]
    index = end
    while index < len(line):
        found = _TRAILING_MARK.search(line, index)
        if found is None:
            return False
        if found.group() == run and _is_emphasis_candidate(
            line, found.start(), found.end(), char
        ):
            return True
        index = found.end()
    return False


def _unclosed_link(line: str) -> int | None:
    """Return the index where a link construct opens without closing.

    "[" without "](target)" and "](" without ")" continue onto the next line and must be held, or the link text is spoken and the target read out as a URL.
    """
    for match in _TRAILING_MARK.finditer(line):
        marker = match.group()
        if marker == "[":
            if "]" not in line[match.end():]:
                return match.start()
        elif marker == "](":
            if ")" not in line[match.end():]:
                return line.rfind("[", 0, match.start()) if "[" in line[:match.start()] else match.start()
    return None


def _holdback_index(line: str) -> int:
    """Return where the trailing remainder of ``line`` must be held back (0 = safe).

    A non-zero value indexes a marker opening a construct not closed on this line. Markers are PAIRED left to right, never scanned for a lonely one: hunting for one with no closer finds the CLOSING run of a complete construct, which turned "**bold**" into "bold" plus a stuck "**" tail.
    """
    unclosed_link = _unclosed_link(line)
    if unclosed_link is not None:
        return unclosed_link

    stack: list[tuple[int, str]] = []
    for match in _TRAILING_MARK.finditer(line):
        marker = match.group()
        if marker in ("[", "]("):
            continue  # already handled by the link check above
        if not _is_emphasis_candidate(line, match.start(), match.end(), marker[0]):
            continue
        if stack and stack[-1][1] == marker:
            stack.pop()
        else:
            stack.append((match.start(), marker))
    if not stack:
        return 0
    index, marker = stack[0]
    # A single "_" or "*" at line start with nothing left to close it is an identifier or a list bullet, not a construct still being typed; holding it would pin the rest of the reply behind a marker that is never coming.
    if index == 0 and len(marker) == 1 and marker in "_*":
        return 0
    if index > _MAX_HOLDBACK:
        return 0
    return index


def _clean_inline(text: str) -> str:
    """Strip inline markdown from a fragment that has no open constructs."""
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _BARE_URL.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _STRIKE.sub(r"\1", text)
    text = _BOLD.sub(r"\2", text)
    text = _ITALIC_STAR.sub(r"\1", text)
    text = _ITALIC_US.sub(r"\1", text)
    text = _MULT_STAR.sub(" × ", text)
    text = _LABEL.sub(r"\1", text)
    text = text.replace("|", " ").replace("`", " ")
    text = _SPACES.sub(" ", _drop_non_speech(text))
    return text.strip()


def _is_table_row(line: str) -> bool:
    """A table row needs at least two cells to be worth collapsing."""
    return line.count("|") >= 2 and (line.startswith("|") or " | " in line)


def _clean_line(line: str) -> str:
    """Return the speakable text of one complete line, or "" if it has none."""
    line = line.strip()
    if not line or _RULE.match(line):
        return ""
    stripped = _BLOCK_PREFIX.sub("", line, count=1).strip()
    if not stripped or _RULE.match(stripped):
        return ""
    if _is_table_row(stripped):
        if _TABLE_SEPARATOR.match(stripped):
            return ""
        # Keep the cells, drop the pipes: a comma list is intelligible aloud, one read pipe by pipe is not.
        cells = [c.strip() for c in stripped.strip("|").split("|") if c.strip()]
        return ", ".join(cells)
    return _clean_inline(stripped)


class MarkdownSanitizer:
    """Turn a markdown token stream into speakable text, incrementally.

    ``push()`` returns what became safe to emit, ``flush()`` the held-back tail; output is always safe to hand to a TTS provider.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._fence: str | None = None
        self._line_start = True
        self._last_char = ""
        self._prev_char = ""
        self._splitter = PhraseSplitter()
        self.chars_in = 0
        self.chars_out = 0
        self.waits = 0
        # Watermarks for the per-chunk [PERF] delta line.
        self._reported_in = 0
        self._reported_out = 0

    def push(self, text: str) -> list[str]:
        """Consume a token (or fragment); return the sentences ready to speak."""
        if not text:
            return []
        self.chars_in += len(text)
        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        return self._phrases(self._drain(final=False))

    def release(self) -> list[str]:
        """Drain everything held WITHOUT closing the stream (state survives).

        For an inference boundary: the agent is about to spend seconds in tool calls, so an unterminated fragment ("Voy a revisar") must be spoken now, not after the silence.
        """
        return self._phrases(self._drain(final=False, force=True)) + self._phrases_final()

    def flush(self) -> list[str]:
        """Close the stream and return every remaining sentence."""
        return self._phrases(self._drain(final=True)) + self._phrases_final()

    def _phrases(self, text: str) -> list[str]:
        # The sanitizer already decided these boundaries, so the splitter is told to trust them; without that a cleaned "Listo." keeps its trailing dot forever.
        return [
            p
            for p in (
                _normalize_phrase(phrase)
                for phrase in self._splitter.take(text, decided=True)
            )
            if p
        ]

    def _phrases_final(self) -> list[str]:
        """Emit the splitter's unterminated remainder at end of stream."""
        rest = _normalize_phrase(self._splitter.drain())
        return [rest] if rest else []

    @property
    def held_chars(self) -> int:
        """Characters still in the holdback tail, for instrumentation."""
        return len(self._buffer)

    @property
    def saved_chars(self) -> int:
        return self.chars_in - self.chars_out

    def _drain(self, final: bool, force: bool = False) -> str:
        out: list[str] = []
        while self._buffer and self._consume(out, final=final, force=force):
            pass
        text = "\n".join(p for p in out if p.strip())
        if text:
            text = self._glue(text)
        self.chars_out += len(text)
        return text

    def _glue(self, text: str) -> str:
        """Prefix the boundary the previous chunk implies; only this seam knows its end."""
        if not self._last_char:
            self._remember(text)
            return text
        if (
            self._last_char == "."
            and self._prev_char.isdigit()
            and text[0].isdigit()
        ):
            # A period between digits is a decimal point, so the seam stays invisible.
            prefix = ""
        elif self._last_char in _TERMINATORS or self._last_char in _JOIN_SPACE:
            prefix = " "
        else:
            prefix = ". "
        self._remember(text)
        return prefix + text

    def _remember(self, text: str) -> None:
        self._last_char = text[-1]
        self._prev_char = text[-2] if len(text) > 1 else ""

    def _consume(self, out: list[str], final: bool, force: bool = False) -> bool:
        """Process one unit. Return False to stop draining."""
        if self._fence is not None:
            return self._consume_fenced(out, final=final)
        return self._consume_prose(out, final=final, force=force)

    def _consume_fenced(self, out: list[str], final: bool) -> bool:
        """Drop code-block content, watching only for the closing fence."""
        newline = self._buffer.find("\n")
        if newline == -1:
            # Nothing inside a fence is spoken, so the buffer is reduced to the trailing fence run: that keeps a long unterminated block from being rescanned.
            self._buffer = "" if final else self._partial_fence_at_end()
            return False
        line = self._buffer[:newline]
        self._buffer = self._buffer[newline + 1:]
        stripped = line.strip()
        if _FENCE.match(stripped) and stripped.startswith(self._fence[0]):
            self._fence = None
            self._line_start = True
        return True

    def _partial_fence_at_end(self) -> str:
        """Return the trailing run of fence characters, which may close the block."""
        if not self._fence:
            return ""
        char = self._fence[0]
        run = ""
        for candidate in reversed(self._buffer):
            if candidate != char:
                break
            run = candidate + run
        return run

    def _release_sentences(self, out: list[str]) -> bool:
        """Emit every decided sentence in the buffer; keep the rest for the next push.

        Returns False when the buffer yields nothing, which stops the drain loop.
        """
        # A block prefix ("1. ", "- ", "# ") only opens a line and its own "." is not a sentence end; cutting there hands _clean_line a fragment it can no longer read as a list item.
        guard = 0
        prefix = _BLOCK_PREFIX.match(self._buffer)
        if prefix and self._line_start:
            guard = prefix.end()
        if self._line_start:
            # An unclosed "**" or "[text](" must not be cut in half: the no-newline path holds the same constructs the line path does.
            held = _holdback_index(self._buffer.split("\n", 1)[0])
            if held:
                guard = max(guard, held)
        cut = 0
        for index in range(guard, len(self._buffer)):
            if is_sentence_end(self._buffer, index):
                cut = index + 1
        if cut == 0:
            self.waits += 1
            return False
        head, self._buffer = self._buffer[:cut], self._buffer[cut:]
        self._line_start = False
        segment = _clean_line(head)
        if segment.strip(_PHRASE_NOISE):
            out.append(segment)
        return True

    def _consume_prose(self, out: list[str], final: bool, force: bool = False) -> bool:
        newline = self._buffer.find("\n")
        if newline == -1 and not final:
            if force:
                segment = _clean_line(self._buffer)
                self._buffer = ""
                if segment.strip(_PHRASE_NOISE):
                    out.append(segment)
                return False
            # No newline yet: release the prefix ending on a DECIDED terminator, keep any undecided tail. An LLM writes a whole reply on one line and never promises a "\n", so waiting for one held every sentence until the stream ended.
            return self._release_sentences(out)
        if newline == -1:
            line, self._buffer = self._buffer, ""
        else:
            line = self._buffer[:newline]
            self._buffer = self._buffer[newline + 1:]

        stripped = line.strip()
        fence = _FENCE.match(stripped) if self._line_start else None
        if fence:
            # A fence is unambiguous on a complete line, so it is matched before the holdback scan: otherwise its own backtick run looks like an unterminated code span and the block is never entered.
            self._fence = fence.group(1)
            self._line_start = True
            return True

        if self._line_start:
            held = _holdback_index(line)
            if held:
                out.append(_clean_line(line[:held]))
                self._buffer = line[held:] + self._buffer
                self._line_start = False
                self.waits += 1
                return False

        out.append(_clean_line(line))
        self._line_start = True
        return True


_SENTENCE_ENDINGS = (".", "?", "!", "\n")
_PHRASE_NOISE = "".join(_SENTENCE_ENDINGS)


def _normalize_phrase(phrase: str) -> str:
    """Collapse decorative punctuation on a phrase's edges: the splitter finds boundaries but does not clean, so "...Hola..." would reach the provider with its dots intact."""
    text = phrase.strip().lstrip(_PHRASE_NOISE).strip()
    run = len(text) - len(text.rstrip(_PHRASE_NOISE))
    if run > 1:
        text = text[:-run + 1]
    return text


def is_sentence_end(buffer: str, index: int, decided: bool = False) -> bool:
    """Report whether ``buffer[index]`` terminates a spoken phrase.

    A terminator only ends a sentence when whitespace follows or the buffer ends there, which keeps dots inside a URL ("x.dev") and decimals ("3.5") from cutting the stream. A "." ending the buffer is undecided whatever precedes it: the next token can still turn "x." into "x.dev" or "2." into "2.5".
    """
    char = buffer[index]
    if char not in _SENTENCE_ENDINGS:
        return False
    if char != ".":
        return True
    # Undecided at the very end of the buffer: streaming stays conservative and release()/flush() do the forcing at a real boundary.
    if index + 1 == len(buffer):
        return decided
    # Only "." needs the whitespace guard: it is the one terminator that appears INSIDE a token, so a dot followed by a letter or digit belongs to a decimal, domain or version.
    if not buffer[index + 1].isspace():
        return False
    prev_is_digit = index > 0 and buffer[index - 1].isdigit()
    if not prev_is_digit:
        return True
    return not buffer[index + 1].isdigit()


class PhraseSplitter:
    """Cut sanitized text into complete speakable phrases.

    The scan resumes where the last call stopped, so a long terminator-free run stays linear instead of being re-walked every token.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._scan = 0

    def take(self, text: str, decided: bool = False) -> list[str]:
        self._buffer += text
        phrases: list[str] = []
        start = 0
        index = min(max(self._scan, 0), len(self._buffer))
        resume = index
        while index < len(self._buffer):
            if not is_sentence_end(self._buffer, index, decided):
                resume = index
                index += 1
                continue
            segment = self._buffer[start:index + 1].strip()
            start = index + 1
            index = start
            resume = start
            if segment.strip(_PHRASE_NOISE):
                phrases.append(segment)
        self._buffer = self._buffer[start:]
        self._scan = max(resume - start, 0)
        return phrases

    def release(self) -> str:
        """Return the unterminated remainder without consuming it."""
        return self._buffer

    def drain(self) -> str:
        """Return and clear the remainder."""
        text, self._buffer = self._buffer, ""
        self._scan = 0
        return text


def sanitize_stream_report(sanitizer: MarkdownSanitizer, label: str = "tts") -> None:
    """Emit the cumulative [PERF] line for the whole stream (the final one)."""
    if sanitizer.chars_in <= 0:
        return
    pct = 100.0 * sanitizer.saved_chars / sanitizer.chars_in
    print(
        f"[PERF] {label}_sanitize: chars_in={sanitizer.chars_in} "
        f"chars_out={sanitizer.chars_out} saved={sanitizer.saved_chars} "
        f"({pct:.1f}%) waits={sanitizer.waits} held={sanitizer.held_chars}"
    )


# Chunks are LLM tokens, so a per-token line is one log line per ~4 characters. Throttled
# to one line per ``_CHUNK_REPORT_CHARS`` of INPUT: still shows the markdown ratio drifting
# (a rising holdback or a fence that never closes shows as falling chars_out). The
# cumulative report above always runs.
_CHUNK_REPORT_CHARS = 200


def sanitize_chunk_report(
    sanitizer: MarkdownSanitizer, label: str = "tts", force: bool = False
) -> None:
    """Emit a per-chunk [PERF] line reporting DELTAS since the last call.

    Deltas are what make this readable mid-stream: a chunk that is almost entirely markup
    shows ``chars_out=0 saved=N`` at once, the signal that the audio will be thin.
    """
    delta_in = sanitizer.chars_in - sanitizer._reported_in
    if delta_in <= 0 and not force:
        return
    if not force and delta_in < _CHUNK_REPORT_CHARS:
        sanitizer._reported_in = sanitizer.chars_in
        return
    delta_out = sanitizer.chars_out - sanitizer._reported_out
    held = sanitizer.held_chars
    sanitizer._reported_in = sanitizer.chars_in
    sanitizer._reported_out = sanitizer.chars_out
    print(
        f"[PERF] {label}_sanitize_chunk: in={delta_in} out={delta_out} "
        f"held={held} held_pct={100.0 * held / delta_in if delta_in else 0.0:.0f}%"
    )


def sanitize(text: str) -> str:
    """Sanitize a complete string in one shot (tests and one-shot callers)."""
    sanitizer = MarkdownSanitizer()
    return " ".join(sanitizer.push(text) + sanitizer.flush())


def sanitize_stream(chunks: list[str]) -> list[str]:
    """Sanitize a token list, returning the sentences in order."""
    sanitizer = MarkdownSanitizer()
    out: list[str] = []
    for chunk in chunks:
        out.extend(sanitizer.push(chunk))
    out.extend(sanitizer.flush())
    return out
