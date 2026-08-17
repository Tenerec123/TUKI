import os
from datetime import datetime
from .tools import execute_tool_call
from openai import AsyncOpenAI
import traceback
def _log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def _log_cache_usage(usage, label: str = ""):
    """Log prompt-cache metrics from a usage object.

    OpenRouter reports cache activity via prompt_tokens_details.cached_tokens:
    > 0 means the provider reused a cached prefix (cheaper and faster).
    """
    if usage is None:
        return
    try:
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        if cached:
            pct = 100.0 * cached / prompt if prompt else 0.0
            _log(f"→ [CACHE:{label}] hit {cached}/{prompt} tokens ({pct:.0f}%)")
        else:
            _log(f"→ [CACHE:{label}] miss (cached=0, prompt={prompt})")
    except Exception:
        pass

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ['OPENROUTER_API_KEY']
)

async def _agentic_round(messages: list, model: str, tool_schemas: list, session_id: str = "-1", label: str = ""):
    extra_body = {"reasoning": {"effort": "none"}}
    if session_id != "-1": extra_body['session_id'] = session_id
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tool_schemas,
        tool_choice="auto",
        parallel_tool_calls=True,
        stream=True,
        extra_body=extra_body,
    )
    calls: dict[int, dict] = {}
    text: str = ""
    finish = None
    async for chunk in stream:
        # OpenRouter sends usage on the final chunk (may arrive without choices)
        if chunk.usage:
            _log_cache_usage(chunk.usage, label)
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        if delta.content and delta.content.strip():
            text+=delta.content
            yield {"type":"agent","content":delta.content}
        if delta.tool_calls:
            for tc in delta.tool_calls:
                calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    calls[tc.index]["id"] = tc.id
                if tc.function and tc.function.name:
                    calls[tc.index]["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    calls[tc.index]["arguments"] += tc.function.arguments
        if choice.finish_reason:
            finish = choice.finish_reason
    messages.append({"role": "assistant", "content": text})

    if calls:
        tool_names = [tc['name'] for tc in calls.values()]
        _log(f"→ Tools called: {tool_names[:5]}")
        assistant = {"role": "assistant", "tool_calls": []}
        for tc in calls.values():
            id = tc['id']
            name = tc['name']
            args = tc['arguments']
            args_preview = args[:80]
            _log(f"   ├─ {name}({args_preview})")
            tc_data = {
                "id": id,
                "type": "function",
                "function": {"name": name, "arguments": args},
            }
            yield {"type":"tool_call","content":{'id':id, 'name':name, 'args':args}}
            assistant["tool_calls"].append(tc_data)

        messages.append(assistant)
        
        # THEN execute tools and append results
        for tc in calls.values():
            id = tc['id']
            name = tc['name']
            args = tc['arguments']
            result = execute_tool_call(name, args)
            result_preview = result[:100]
            _log(f"   └─ {name} → {result_preview}")
            messages.append({
                "role": "tool",
                "tool_call_id": id,
                "name": name,
                "content": result,
            })
            yield {"type":"tool_result","content":{'id':id, 'name':name, 'result':result}}
 
    if finish == "stop" or len(calls) == 0: yield {"type":"finish", "content":""}
    
async def openai_agent(messages:list, model, max_rounds, tool_schemas: list, conv_id:int=-1):
    _log(f"═══════════════════════════════════════════════")
    _log(f"AGENT START — model={model}")
    try:
        for i in range(max_rounds):
            _log(f"── Round {i+1}/{max_rounds}")
            async for token in _agentic_round(messages, model, tool_schemas, str(conv_id), f"round-{i+1}/{max_rounds}"):
                yield token
                if token['type'] == "finish": break
            else: continue
            break
        _log("AGENT END — OK")
    except Exception as e:
        _log(f"AGENT ERROR: {e}")
        traceback.print_exc()
        yield 'ERROR_TOKEN'