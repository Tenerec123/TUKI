import asyncio

import pytest
from sqlalchemy import MetaData

# Importing the app's credential module imports models.py, whose module-level
# create_all would otherwise require the developer's PostgreSQL instance during
# test collection. Suppress only that bootstrap side effect while loading Jev.
_create_all = MetaData.create_all
MetaData.create_all = lambda self, *args, **kwargs: None
try:
    from backend.ai.voice import fast_path
finally:
    MetaData.create_all = _create_all


@pytest.mark.parametrize("request_fails", [False, True])
def test_jev_failure_skips_fast_path(monkeypatch, request_fails) -> None:
    monkeypatch.setattr(fast_path, "_quick_intent", lambda transcript: None)
    if request_fails:
        monkeypatch.setattr(fast_path, "get_provider_key", lambda provider: "configured")
        monkeypatch.setattr(
            fast_path.urllib.request,
            "urlopen",
            lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("Jev unavailable")),
        )
    else:
        monkeypatch.setattr(
            fast_path,
            "get_provider_key",
            lambda provider: (_ for _ in ()).throw(RuntimeError("OpenRouter key missing")),
        )
        monkeypatch.setattr(
            fast_path.urllib.request,
            "urlopen",
            lambda *args, **kwargs: pytest.fail("must not request Jev without an OpenRouter key"),
        )

    result = asyncio.run(fast_path.route_fast_path("what should I write?"))

    assert result is None
