from ..config import WEB_SEARCH_SYSTEM_PROMPT, SUMMARY_MAX_CHARS, get_model_config
from .websearch import fetch_pages, search
import asyncio

async def WebSearch(query: str):
    '''
    Searches the internet and returns a written summary of the sources it found.
    Use it for facts you do not have or that may be out of date: recent events, versions, prices, documentation.
    It does not invent dates, so pass the period the user means ("last week") instead of guessing one.
    The result is text to relay: do not read the source URLs out loud.
    Args:
        query: the web search
    '''
    from ..agent import openai_agent
    results = await asyncio.to_thread(search, query)
    if not results:
        return f"No search results for: {query}"

    pages = await fetch_pages(results)
    if not pages:
        return f"No sources could be retrieved for: {query}"

    body = "\n\n".join(f"[{p.n}] {p.title} — {p.url}\n{p.text}" for p in pages)
    messages = [
        {
            'role': 'developer',
            'content': WEB_SEARCH_SYSTEM_PROMPT
        },
        {
            'role': 'user',
            'content': f'query: {query}'
        },
        {
            'role': 'developer',
            'content': f'Websites:\n\n{body}'
        },
    ]
    result = ""
    truncated = False
    async for token in openai_agent(
        messages=messages,
        model=get_model_config()['searcher'],
        max_rounds=1,
        tool_schemas=[]):
        # The agent yields the literal string 'ERROR_TOKEN' when a round raises,
        # which happens AFTER whatever already streamed, so the text may be partial.
        if isinstance(token, str):
            truncated = True
            break
        if token['type'] == "agent":
            result += token['content']

    # Pages were fetched above, so an empty result is a summarizer failure, not
    # a missing-source one: say so instead of blaming the sources.
    if not result.strip():
        return f"Fetched {len(pages)} sources for '{query}' but the summary could not be produced."
    if len(result) > SUMMARY_MAX_CHARS:
        # Clamp the prose only: the Sources block is the whole point of the tool,
        # so it is appended in full even if it alone runs long.
        split = result.rfind("Sources:")
        if split == -1:
            cut = result.rfind(' ', 0, SUMMARY_MAX_CHARS)
            result = result[:cut if cut > 0 else SUMMARY_MAX_CHARS] + '...'
        else:
            prose = result[:split].rstrip()
            if len(prose) > SUMMARY_MAX_CHARS:
                cut = prose.rfind(' ', 0, SUMMARY_MAX_CHARS)
                prose = prose[:cut if cut > 0 else SUMMARY_MAX_CHARS] + '...'
            result = f"{prose}\n\n{result[split:]}"
    if truncated:
        result += "\n\n(incomplete: the summary was cut short by a model error)"
    return result
