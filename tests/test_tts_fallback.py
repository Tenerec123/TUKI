import asyncio

import pytest
from sqlalchemy.schema import MetaData

# Model import performs table bootstrap in production. Keep these tests focused
# on TTS selection/session behavior without requiring the development database.
MetaData.create_all = lambda self, *args, **kwargs: None

from backend.ai.credentials import ProviderCredentialError
import backend.ai.tts as tts_module
from backend.ai.tts.base import TTSTurnEnd


def test_default_prefers_deepgram_when_both_keys_are_configured(monkeypatch):
    monkeypatch.setattr(tts_module, "get_app_setting", lambda key, default=None: default)
    monkeypatch.setattr(tts_module, "get_provider_key", lambda provider: f"{provider}-key")

    provider = tts_module.get_tts_provider()

    assert provider.primary_name == "deepgram"
    assert provider.fallback_name == "openrouter"


def test_configured_preference_is_used_but_missing_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(tts_module, "get_app_setting", lambda key, default=None: "openrouter")
    monkeypatch.setattr(tts_module, "get_provider_key", lambda provider: f"{provider}-key")
    assert tts_module.get_tts_provider().primary_name == "openrouter"

    monkeypatch.setattr(
        tts_module,
        "get_provider_key",
        lambda provider: (_ for _ in ()).throw(ProviderCredentialError("missing")),
    )
    with pytest.raises(ProviderCredentialError):
        tts_module.get_tts_provider("deepgram")


class _FakeSession:
    def __init__(
        self, *, start_error=None, flush_error=None, audio=b"fallback",
        audio_error=None, early_eof=False, emit_marker=True,
    ):
        self.start_error = start_error
        self.flush_error = flush_error
        self.audio_payload = audio
        self.audio_error = audio_error
        self.early_eof = early_eof
        self.emit_marker = emit_marker
        self.items = []
        self.text = []
        self.started = False
        self.flush_calls = 0
        self.sample_rate = 24000

    async def start(self):
        if self.start_error:
            raise self.start_error
        self.started = True

    async def push_text(self, text):
        self.text.append(text)

    async def flush(self):
        self.flush_calls += 1
        if self.flush_error:
            error, self.flush_error = self.flush_error, None
            raise error
        if self.items:
            return
        self.items = [self.audio_payload]
        if self.emit_marker:
            self.items.append(TTSTurnEnd(index=1, chars=1, audio_ms=1))

    async def clear(self):
        self.items = []

    async def audio(self):
        for item in self.items:
            yield item
        self.items = []
        if self.audio_error:
            raise self.audio_error
        if self.early_eof:
            return

    async def close(self):
        pass


class _FakeProvider:
    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


def test_session_start_failure_fails_over_without_losing_the_turn(monkeypatch):
    primary = _FakeSession(start_error=RuntimeError("deepgram down"))
    fallback = _FakeSession(audio=b"openrouter")
    provider = tts_module.FailoverTTSProvider(
        "deepgram", _FakeProvider(primary), "openrouter", _FakeProvider(fallback)
    )
    session = provider.session()

    async def scenario():
        await session.start()
        await session.push_text("Hola")
        await session.flush()
        return [item async for item in session.audio()]

    assert asyncio.run(scenario()) == [b"openrouter", TTSTurnEnd(index=1, chars=1, audio_ms=1)]
    assert fallback.text == ["Hola"]


@pytest.mark.parametrize(
    "reader_failure",
    [RuntimeError("reader failed"), "early_eof"],
)
def test_partial_reader_failure_retries_one_complete_turn(reader_failure):
    primary = _FakeSession(
        audio=b"partial",
        audio_error=(reader_failure if isinstance(reader_failure, Exception) else None),
        early_eof=reader_failure == "early_eof",
        emit_marker=False,
    )
    fallback = _FakeSession(audio=b"complete")
    primary.items = [b"partial"]
    fallback.items = [b"complete", TTSTurnEnd(index=1, chars=1, audio_ms=1)]
    provider = tts_module.FailoverTTSProvider(
        "deepgram", _FakeProvider(primary), "openrouter", _FakeProvider(fallback)
    )
    session = provider.session()

    async def scenario():
        await session.start()
        await session.push_text("Hola")
        await session.flush()
        return [item async for item in session.audio()]

    assert asyncio.run(scenario()) == [
        b"complete", TTSTurnEnd(index=1, chars=1, audio_ms=1)
    ]
    assert fallback.text == ["Hola"]
    assert fallback.flush_calls == 1


def test_read_failure_flushes_fallback_when_it_only_synthesizes_on_flush():
    primary = _FakeSession(audio=b"partial", emit_marker=False)
    fallback = _FakeSession(audio=b"complete")
    primary.items = [b"partial"]
    provider = tts_module.FailoverTTSProvider(
        "deepgram", _FakeProvider(primary), "openrouter", _FakeProvider(fallback)
    )
    session = provider.session()

    async def scenario():
        await session.start()
        await session.push_text("Hola")
        await session.flush()
        return [item async for item in session.audio()]

    assert asyncio.run(scenario()) == [
        b"complete", TTSTurnEnd(index=1, chars=1, audio_ms=1)
    ]
    assert fallback.flush_calls == 1


def test_first_marker_does_not_discard_a_queued_second_turn():
    primary = _FakeSession(audio_error=RuntimeError("reader failed"))
    primary.items = [
        b"first",
        TTSTurnEnd(index=1, chars=1, audio_ms=1),
        b"partial-second",
    ]
    fallback = _FakeSession(audio=b"second")
    provider = tts_module.FailoverTTSProvider(
        "deepgram", _FakeProvider(primary), "openrouter", _FakeProvider(fallback)
    )
    session = provider.session()

    async def scenario():
        await session.start()
        await session.push_text("Uno")
        await session.flush()
        await session.push_text("Dos")
        await session.flush()
        return [item async for item in session.audio()]

    assert asyncio.run(scenario()) == [
        b"first",
        TTSTurnEnd(index=1, chars=1, audio_ms=1),
        b"second",
        TTSTurnEnd(index=1, chars=1, audio_ms=1),
    ]
    assert fallback.text == ["Dos"]


def test_coalesced_marker_retires_all_represented_turns():
    primary = _FakeSession(audio_error=RuntimeError("reader failed"))
    primary.items = [
        b"first-two",
        TTSTurnEnd(index=1, chars=len("UnoDos"), audio_ms=1),
        b"partial-third",
    ]
    fallback = _FakeSession(audio=b"third")
    provider = tts_module.FailoverTTSProvider(
        "deepgram", _FakeProvider(primary), "openrouter", _FakeProvider(fallback)
    )
    session = provider.session()

    async def scenario():
        await session.start()
        for text in ("Uno", "Dos", "Tres"):
            await session.push_text(text)
            await session.flush()
        return [item async for item in session.audio()]

    assert asyncio.run(scenario()) == [
        b"first-two",
        TTSTurnEnd(index=1, chars=len("UnoDos"), audio_ms=1),
        b"third",
        TTSTurnEnd(index=1, chars=1, audio_ms=1),
    ]
    assert fallback.text == ["Tres"]
    assert fallback.flush_calls == 1


def test_flush_failure_retries_unemitted_turn_on_the_other_provider():
    primary = _FakeSession(flush_error=RuntimeError("synthesis failed"))
    fallback = _FakeSession(audio=b"openrouter")
    provider = tts_module.FailoverTTSProvider(
        "deepgram", _FakeProvider(primary), "openrouter", _FakeProvider(fallback)
    )
    session = provider.session()

    async def scenario():
        await session.start()
        await session.push_text("Hola")
        await session.flush()
        return [item async for item in session.audio()]

    assert asyncio.run(scenario()) == [b"openrouter", TTSTurnEnd(index=1, chars=1, audio_ms=1)]
    assert primary.text == ["Hola"]
    assert fallback.text == ["Hola"]
