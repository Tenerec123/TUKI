import os
import json
from datetime import datetime
from ..schemas import ConversationSchema
from .tools import ALL_TOOLS_SCHEMAS, execute_tool_call
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

BASE_RULES = '''
T.U.K.I. — productivity assistant. Tone: direct, technical.
User: developer. Lang: Spanish/English.
Rules: No raw JSON in responses. No tool calls in visible text. Use $ for LaTeX.
Tool Calls:
-- OpenAI SDK tool calling format.
-- Don't invent id's, get them with the get tools.
-- Use parallel tool calling as much as you can.
-- Don't use it if tool B call depends on tool A result.
-- If you have all the info to respond or you've executed the order, don't use any tool and respond with text
-- You can return text in the tool calling inferences if necessary
-- If the task involves dates, deadlines, or time, call GetCurrentTime first.
'''

MAX_TOOL_ROUNDS = 10

def _build_messages(conversation: ConversationSchema, base_prompt: str = BASE_RULES) -> list:
    msgs = [{'role': 'developer', 'content': base_prompt}]
    for msg in conversation.messages:
        if not (msg.text or '').strip():
            continue
        if msg.type == 'prompt':
            msgs.append({'role': 'user', 'content': msg.text})
        elif msg.type == 'tool':
            try:
                tc = json.loads(msg.text)
                msgs.append({
                    'role': 'assistant',
                    'tool_calls': [{
                        'id': tc['id'],
                        'type': 'function',
                        'function': {'name': tc['name'], 'arguments': tc['args']},
                    }],
                    'content': None,
                })
                msgs.append({
                    'role': 'tool',
                    'tool_call_id': tc['id'],
                    'name': tc['name'],
                    'content': tc['result'],
                })
            except (json.JSONDecodeError, KeyError):
                msgs.append({'role': 'assistant', 'content': msg.text})
        else:
            msgs.append({'role': 'assistant', 'content': msg.text})
    return msgs


async def _agentic_round(messages: list, model: str, tool_schemas: list, session_id: str = "", label: str = ""):
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tool_schemas,
        tool_choice="auto",
        parallel_tool_calls=True,
        stream=True,
        extra_body={"session_id": session_id, "reasoning": {"effort": "none"}},
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
    
async def openai_agent(messages, model_config: dict, conv_id:int, max_rounds: int = None, tool_schemas: list = ALL_TOOLS_SCHEMAS):
    _log(f"═══════════════════════════════════════════════")
    _log(f"AGENT START — model_config={model_config}")
    try:
        limit = max_rounds if max_rounds is not None else MAX_TOOL_ROUNDS
        for i in range(limit):
            _log(f"── Round {i+1}/{limit}")
            async for token in _agentic_round(messages, model_config['orchestrator'], tool_schemas, str(conv_id), f"round-{i+1}/{limit}"):
                yield token
                if token['type'] == "finish": break
            else: continue
            break
        _log("AGENT END — OK")
    except Exception as e:
        _log(f"AGENT ERROR: {e}")
        traceback.print_exc()
        yield 'ERROR_TOKEN'

def get_model_config() -> dict:
    """Read the orchestrator model from DB, falling back to defaults.

    The whole conversation runs on ONE model (`orchestrator`) so the shared
    [system + history] prefix stays KV-cacheable across phases. `searcher`
    is stored for future clean-context subagents (not used yet).
    Legacy per-phase keys (get_data/exec_tools/final_resp/general) are
    collapsed into `orchestrator`.
    """
    from ..database import SessionLocal
    from ..models import Config

    defaults = {
        'orchestrator': 'openai/gpt-5.6-luna-pro',
        'searcher': 'google/gemini-2.5-flash-lite',
    }

    try: 
        db = SessionLocal()
        rows = db.query(Config).all()
        values = {row.key: row.value for row in rows}
        # Legacy migration: use the old 'general' (chat) value as orchestrator.
        if 'orchestrator' not in values and 'general' in values:
            values['orchestrator'] = values['general']
        for key in defaults:
            if key in values:
                defaults[key] = values[key]
    except Exception as e:
        _log(f"[CONFIG] Error reading model config: {e}")
    finally:
        db.close()

    return defaults 