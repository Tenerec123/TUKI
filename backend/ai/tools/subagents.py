from ..config import WEB_SEARCH_SYSTEM_PROMPT, get_model_config
async def WebSearch(query: str):
    '''
    Asks for data which a subagent will return summarized from the Internet.
    Args:
        query: explain here what you want, the format and other details 
'''
    from ddgs import DDGS
    from ..agent import openai_agent
    from .discovery import ALL_TOOL_SCHEMAS
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=5))
        first_search = [{'title': r['title'], 'url': r['href'], 'snippet': r['body']}
            for r in results]

    messages = [
        {
            'role':'developer',
            'content':WEB_SEARCH_SYSTEM_PROMPT
        },
        {
            'role':'developer',
            'content':f'Initial search results:\n{first_search}'
        },
        {
            'role':'user',
            'content':query
        }
    ]
    result = ""
    async for token in openai_agent(
        messages=messages,
        model=get_model_config()['searcher'],
        max_rounds=2,
        tool_schemas=[ts for ts in ALL_TOOL_SCHEMAS if ts['function']['name'] in {'WebFetch'}]):
        if token['type'] == "agent":
            result += token['content']
        elif token['type'] == "tool_result":
            result = ""  # discard pre-tool chatter
    return result