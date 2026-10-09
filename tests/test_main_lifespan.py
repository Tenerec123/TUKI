"""Boot contract: the app must become ready even when embedding warmup fails.

The warmup exists to move torch's CPU init off the request path; its failure
path is the whole reason it cannot break boot, so it gets pinned here.
"""

import asyncio


def test_lifespan_completes_when_embedding_warmup_raises(monkeypatch):
    import backend.main as main
    from backend.main import api, lifespan

    monkeypatch.setattr(main, "ensure_wake_models", lambda: None)

    def _boom():
        raise RuntimeError("weights unavailable")

    monkeypatch.setattr(main, "get_embedding_model", _boom)

    async def scenario():
        async with lifespan(api):
            return "serving"

    assert asyncio.run(scenario()) == "serving"


def test_lifespan_survives_a_stalled_warmup(monkeypatch):
    import time as _time

    import backend.main as main
    from backend.main import api, lifespan

    monkeypatch.setattr(main, "ensure_wake_models", lambda: None)
    monkeypatch.setattr(main, "EMBEDDING_WARMUP_TIMEOUT_S", 0.05)

    class _HangingModel:
        def encode(self, _text):
            _time.sleep(0.5)
            return [[0.0]]

    monkeypatch.setattr(main, "get_embedding_model", lambda: _HangingModel())

    async def scenario():
        async with lifespan(api):
            return "serving"

    assert asyncio.run(scenario()) == "serving"
