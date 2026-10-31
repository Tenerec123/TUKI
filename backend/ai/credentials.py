"""Provider credential lookup and OpenAI-compatible client construction."""

from typing import Any

from openai import AsyncOpenAI

from ..database import SessionLocal
from ..models import APIKey

CHAT_COMPLETION_BASE_URLS = {
    'openrouter': 'https://openrouter.ai/api/v1',
    'opencode-go': 'https://opencode.ai/inference/go/openai/v1',
}


class ProviderCredentialError(ValueError):
    """Raised when a provider has not been configured with an API key."""


def get_provider_key(provider: str) -> str:
    """Return one provider key from the database without exposing it to callers."""
    with SessionLocal() as db:
        row = db.query(APIKey).where(APIKey.provider == provider).first()
        if row is None or not row.key.strip():
            raise ProviderCredentialError(
                f'API key for provider "{provider}" is not configured'
            )
        return row.key


def create_chat_client(provider: str) -> AsyncOpenAI:
    """Create an OpenAI SDK client for a supported Chat Completions provider."""
    base_url = CHAT_COMPLETION_BASE_URLS.get(provider)
    if base_url is None:
        raise ValueError(f'Provider "{provider}" does not support Chat Completions')

    client_options: dict[str, Any] = {
        'api_key': get_provider_key(provider),
        'base_url': base_url,
    }
    if provider == 'opencode-go':
        # OpenCode Go asks non-OpenCode clients to identify themselves.
        client_options['default_headers'] = {'User-Agent': 'TUKI/1.0'}
    return AsyncOpenAI(**client_options)
