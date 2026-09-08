from ..config import WEB_SEARCH_SYSTEM_PROMPT, get_model_config
from .read import WebFetch
async def WebSearch(query: str):
    '''
    Asks for data which a subagent will return summarized from the Internet.
    You can use recent, last week, last month instead of today, but DONT INVENT DATES
    Args:
        query: the web search
'''
    from ddgs import DDGS
    from ..agent import openai_agent
    from .discovery import ALL_TOOL_SCHEMAS
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=5))

    messages = [
        {
            'role':'developer',
            'content':WEB_SEARCH_SYSTEM_PROMPT
        },
        {
            'role':'user',
            'content':f'query: {query}'
        },
        {
            'role':'developer',
            'content':f"Websites:\n{'\n\n'.join([await WebFetch(r['href']) for r in results])}"
        },
    ]
    result = ""
    async for token in openai_agent(
        messages=messages,
        model=get_model_config()['searcher'],
        max_rounds=1,
        tool_schemas=[]):
        if token['type'] == "agent":
            result += token['content']
    return result
