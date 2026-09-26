import os
import asyncio
import time
from datetime import datetime
from .tools.discovery import execute_tool_call
from .config import get_model_config, get_orchestrator_provider_pin
from . import cost_tracker
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

async def _agentic_round(messages: list, model: str, tool_schemas: list, is_last: bool = False, session_id: str = "-1", label: str = ""):
    cfg = get_model_config()
    effort = 'none'
    if model.split(":")[0] == cfg.get('orchestrator', '').split(":")[0]:
        effort = cfg.get('orchestrator_effort', 'none')
    extra_body: dict = {}
    if effort and effort != 'none':
        extra_body['reasoning'] = {'effort': effort}
    if session_id != "-1": extra_body['session_id'] = session_id
    provider_pin = get_orchestrator_provider_pin(model)
    if provider_pin:
        # Preferred provider first; OpenRouter falls back to others on error.
        extra_body['provider'] = {"order": [provider_pin]}
    t_round = time.perf_counter()
    ttft_text = None
    ttft_tool = None
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tool_schemas,
        tool_choice="none" if is_last else "auto",
        parallel_tool_calls=True,
        stream=True,
        extra_body=extra_body,
    )
    calls: dict[int, dict] = {}
    assistant_msg = None
    finish = None
    async for chunk in stream:
        # OpenRouter sends usage on the final chunk (may arrive without choices)
        if chunk.usage:
            _log_cache_usage(chunk.usage, label)
            # Accumulate BEFORE the no-choices guard: the final usage chunk
            # often arrives without choices and must still register its cost.
            # This single point covers orchestrator rounds AND the nested
            # searcher (WebSearch runs its own openai_agent -> _agentic_round).
            cost_tracker.accumulate(
                getattr(chunk.usage, "cost", None) or getattr(chunk.usage, "total_cost", None)
            )
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        if delta.content and delta.content.strip():
            if ttft_text is None:
                # Same non-blank test as the yield below, so TTFT marks the
                # first delta the voice pipeline would actually speak.
                ttft_text = (time.perf_counter() - t_round) * 1000
            if assistant_msg is None:
                assistant_msg = {'role':'assistant', 'content':''}
                messages.append(assistant_msg)
            assistant_msg['content'] += delta.content
            yield {"type":"agent","content":delta.content}
        if delta.tool_calls:
            if ttft_tool is None:
                ttft_tool = (time.perf_counter() - t_round) * 1000
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
    t_stream_end = time.perf_counter()

    # Debug: log any text the model emitted in THIS round (per-round text may
    # arrive interleaved with tool calls; without this line the agent logs
    # never show it).
    if assistant_msg is not None and assistant_msg.get("content"):
        _log(f"   text({label}): {assistant_msg['content'][:120]!r}")

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
        
        # THEN execute tools in parallel (TaskGroup) and append results in order
        tasks = []
        async with asyncio.TaskGroup() as tg:
            for tc in calls.values():
                tasks.append(tg.create_task(execute_tool_call(tc['id'], tc['name'], tc['arguments'])))

        for task in tasks:
            id, name, result = task.result()
            result_preview = result[:100]
            _log(f"   └─ {name} → {result_preview}")
            messages.append({
                "role": "tool",
                "tool_call_id": id,
                "name": name,
                "content": result,
            })
            yield {"type":"tool_result","content":{'id':id, 'name':name, 'result':result}}

    t_round_end = time.perf_counter()
    # text=no marks a silent tool-only round: gpt-oss-120b emits no text while
    # gpt-6-luna does, so a silent round is a round the user waits through
    # hearing nothing.
    _log(f"[PERF] agent: round={label} round_ms={(t_round_end - t_round) * 1000:.1f} "
         f"ttft_text={f'{ttft_text:.1f}' if ttft_text is not None else '-'} "
         f"ttft_tool={f'{ttft_tool:.1f}' if ttft_tool is not None else '-'} "
         f"stream_ms={(t_stream_end - t_round) * 1000:.1f} "
         f"tools_ms={(t_round_end - t_stream_end) * 1000:.1f} "
         f"text={'yes' if assistant_msg else 'no'} tool_calls={len(calls)}")

    if finish == "stop" or len(calls) == 0: yield {"type":"finish", "content":""}
    
async def openai_agent(messages:list, model:str, max_rounds:int, tool_schemas: list, conv_id:int=-1):
    _log(f"═══════════════════════════════════════════════")
    _log(f"AGENT START — model={model}")
    t_agent = time.perf_counter()
    try:
        for i in range(max_rounds):
            t_loop = time.perf_counter()
            _log(f"── Round {i+1}/{max_rounds}")
            _log(f"[PERF] agent: round={i+1}/{max_rounds} start=+{(t_loop - t_agent) * 1000:.1f}ms")
            is_last = i == max_rounds - 1
            async for token in _agentic_round(messages, model, tool_schemas, is_last=is_last, session_id= str(conv_id), label= f"round-{i+1}/{max_rounds}"):
                yield token
                if token['type'] == "finish": break
            else: continue
            break
        _log(f"[PERF] agent: AGENT_END total_ms={(time.perf_counter() - t_agent) * 1000:.1f}ms")
        _log("AGENT END — OK")
    except Exception as e:
        _log(f"AGENT ERROR: {e}")
        traceback.print_exc()
        yield 'ERROR_TOKEN'