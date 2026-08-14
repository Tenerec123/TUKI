import os
import json
from datetime import datetime
from ..schemas import ConversationSchema
from .tools import ALL_TOOLS_SCHEMAS, execute_tool_call
from .prompt_router import classify, BASE_RULES
from openai import AsyncOpenAI

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

MAX_TOOL_ROUNDS = 3

# ── Phase-specific prompts ──────────────────────────────────────────────
# Each phase gets appended to BASE_RULES (identity, tone, priority, formatting)

PHASE_PROMPTS = {
    'read': """
[CURRENT PHASE: DATA GATHERING]
Your ONLY job right now is to gather data from the database using read-only tools.
Call the tools you need (GetAllTasks, GetAllProjects, GetAllRoutines) to get the information required.
WRITE TOOLS (Create/Update/Delete) ARE OFF-LIMITS IN THIS PHASE — do NOT call them.
Do NOT write any response text to the user yet.
When you have all the data you need, stop calling tools and briefly summarize what you found.
""",
    'write': """
[CURRENT PHASE: EXECUTION]
You already have data context from the previous phase.
Use the Create/Update/Delete tools to make the changes the user requested.
Do NOT call read-only tools unless strictly necessary.

IMPORTANT — You can call MULTIPLE tools in a single response.
For example, to create 10 tasks call CreateTask 10 times in parallel here.
Do NOT create items one-by-one across multiple rounds — batch them.

Only re-read data if absolutely necessary.

When all changes are done, respond with a brief confirmation of what was completed.
If any tool returned an error, try again with corrected arguments.
""",
    'respond_query': """
[CURRENT PHASE: RESPONSE]
You have the data the user requested. Present it clearly and concisely.
Format lists, dates, and priorities for readability.
""",
    'respond_execution': """
[CURRENT PHASE: RESPONSE]
Summarize what was done for the user. Confirms the changes made.
Keep it concise and technical.
""",
    'chat': """
[CURRENT PHASE: CHAT]
Respond to the user naturally. No tools needed.
""",
    'unsure': """
You have full freedom. Use tools if the user needs data or actions.
Respond naturally if it's general conversation. Decide based on context.
"""
}

# ── Helpers ─────────────────────────────────────────────────────────────

def _build_messages(conversation: ConversationSchema, guided_prompt: str, tool_history: list | None = None, base_prompt: str = BASE_RULES) -> list:
    """Build the full message list in one place.

    Order: system prompt, conversation history, tool history, phase prompt, time.
    """
    msgs = [{'role': 'developer', 'content': base_prompt}]
    for msg in conversation.messages:
        msgs.append({'role': 'user' if msg.is_user else 'assistant', 'content': msg.text})
    if tool_history:
        msgs.extend(tool_history)
    msgs.append({'role': 'developer', 'content': guided_prompt})
    msgs.append({'role': 'developer', 'content': f'[TIME] {datetime.now().isoformat()}'})
    return msgs


def _get_tool_history(full_msgs: list, base_len: int) -> list:
    """Extract tool call/result messages added during a phase.

    full_msgs layout:
      [0..base_len-1] = developer prompt + conversation + guided prompt + time (pre-phase)
      [base_len..] = tool calls / results added during the phase

    base_len must be captured BEFORE the phase runs (the list is mutated in place).
    """
    return full_msgs[base_len:]


async def _tool_round(messages: list, model: str, tool_schemas: list, session_id: str = "") -> bool:
    """Execute ONE round of model inference with tools.

    - If the model calls tools → executes them, appends results to `messages`.
    - If the model responds with text → appends the text message.
    Returns True if tools were called (caller should loop), False otherwise.

    session_id enables OpenRouter provider sticky routing from the first
    request: all calls of a conversation stick to one provider, keeping its
    KV cache warm (DeepSeek cache reads cost 0.1x).
    """
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tool_schemas,
        tool_choice="auto",
        parallel_tool_calls=True,
        stream=False,
        extra_body={"session_id": session_id},
    )
    _log_cache_usage(response.usage, model)

    # OpenRouter may return choices=None on internal errors
    if not response.choices:
        _log("→ No choices in response (OpenRouter error)")
        messages.append({"role": "assistant", "content": "[The AI service returned an empty response. Please try again.]"})
        return False

    msg = response.choices[0].message

    # No tools → model responded with text, phase is done
    if not msg.tool_calls:
        text_preview = (msg.content or "")[:100]
        _log(f"→ No tools, text response: '{text_preview}'")
        messages.append({"role": "assistant", "content": msg.content or ""})
        return False

    # Build and append assistant message FIRST (tool_calls must exist before their results)
    tool_names = [tc.function.name for tc in msg.tool_calls]
    _log(f"→ Tools called: {tool_names[:5]}")
    assistant = {"role": "assistant", "content": msg.content or None, "tool_calls": []}
    for tc in msg.tool_calls:
        args_preview = tc.function.arguments[:80]
        _log(f"   ├─ {tc.function.name}({args_preview})")
        tc_data = {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        assistant["tool_calls"].append(tc_data)

    messages.append(assistant)

    # THEN execute tools and append results
    for tc in msg.tool_calls:
        result = execute_tool_call(tc.function.name, tc.function.arguments)
        result_preview = result[:100]
        _log(f"   └─ {tc.function.name} → {result_preview}")
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "name": tc.function.name,
            "content": result,
        })

    return True


async def _tool_phase(messages: list, model: str, tool_schemas: list, phase_name: str = "", max_rounds: int = None, error_retry: bool = False, session_id: str = "") -> tuple:
    """Run tool rounds until done.

    - If the model responds with text → phase done, that text IS the response.
    - If the model calls tools → execute them. If error_retry=True and any tool
      errored, allow one extra round to retry. Otherwise, stop.

    Returns (messages, final_text) where final_text is the assistant's text
    if the phase ended naturally, or None if it ended with tool calls.
    """
    limit = max_rounds if max_rounds is not None else MAX_TOOL_ROUNDS
    _log(f"═══ TOOL PHASE [{phase_name}] starting (max {limit} rounds)")
    for i in range(limit):
        prev_len = len(messages)
        _log(f"── Round {i+1}/{limit}")
        tools_called = await _tool_round(messages, model, tool_schemas, session_id)

        if not tools_called:
            _log(f"═══ TOOL PHASE [{phase_name}] done (text response)")
            break

        # Tools were called — check for errors
        had_error = False
        for m in messages[prev_len:]:
            if m.get("role") == "tool":
                c = m.get("content", "")
                if isinstance(c, str) and ('"Error' in c[:20] or '"Execution Error' in c[:25]):
                    had_error = True
                    break

        if error_retry and had_error:
            _log(f"   -> Tools errored, retrying round {i+2}...")
            continue

        # No errors — one round was enough
        _log(f"═══ TOOL PHASE [{phase_name}] done (tools ok)")
        break
    else:
        _log(f"═══ TOOL PHASE [{phase_name}] max rounds ({limit}) reached")

    # Extract final text if the phase ended with a natural response
    final_text = None
    last = messages[-1] if messages else None
    if last and last.get("role") == "assistant" and last.get("content"):
        final_text = last["content"]
        if final_text:
            _log(f"═══ TOOL PHASE [{phase_name}] captured final text ({len(final_text)} chars)")

    return messages, final_text


async def _stream_response(messages: list, model: str, phase_label: str = "", tools: list = None, tool_choice: str = "auto", session_id: str = ""):
    """Stream a text response.

    tools are sent on every stream (with tool_choice="none" when the phase
    must not call tools) so the [system + tools] prefix stays byte-identical
    across all requests and remains KV-cacheable.
    """
    _log(f"═══ STREAM [{phase_label}] starting")
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        stream=True,
        extra_body={"session_id": session_id},
    )
    token_count = 0
    async for chunk in stream:
        # OpenRouter sends usage on the final chunk (may arrive without choices)
        if chunk.usage:
            _log_cache_usage(chunk.usage, phase_label)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            token_count += 1
            yield delta.content
    _log(f"═══ STREAM [{phase_label}] done — {token_count} tokens yielded")


# ── Model Config ────────────────────────────────────────────────────────

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
        'orchestrator': 'deepseek/deepseek-v4-flash',
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


# ── Route-specific paths ────────────────────────────────────────────────

async def normal_path(conversation: ConversationSchema, model_config: dict):
    """Pure chat — one streaming phase, no tools."""
    _log("═══ PATH: normal")
    msgs = _build_messages(conversation, PHASE_PROMPTS['chat'])
    async for token in _stream_response(msgs, model_config['orchestrator'], "chat", tools=ALL_TOOLS_SCHEMAS, tool_choice="none", session_id=str(conversation.id)):
        yield token


async def query_path(conversation: ConversationSchema, model_config: dict):
    """Phase 1: Read data (no stream)  →  Phase 2: Stream response."""
    _log("═══ PATH: query")

    read_base = _build_messages(conversation, PHASE_PROMPTS['read'])
    read_base_len = len(read_base)
    read_msgs, final_text = await _tool_phase(
        read_base,
        model_config['orchestrator'], ALL_TOOLS_SCHEMAS, "read", max_rounds=1,
        session_id=str(conversation.id),
    )

    if final_text:
        yield final_text
        return

    tool_hist = _get_tool_history(read_msgs, read_base_len)
    respond_msgs = _build_messages(conversation, PHASE_PROMPTS['respond_query'], tool_history=tool_hist)

    async for token in _stream_response(respond_msgs, model_config['orchestrator'], "respond-query", tools=ALL_TOOLS_SCHEMAS, tool_choice="none", session_id=str(conversation.id)):
        yield token


async def execution_path(conversation: ConversationSchema, model_config: dict):
    """Phase 1: Read  →  Phase 2: Write  →  Phase 3: Stream response (optional)."""
    _log("═══ PATH: execution")

    read_base = _build_messages(conversation, PHASE_PROMPTS['read'])
    read_base_len = len(read_base)
    read_msgs, _ = await _tool_phase(
        read_base,
        model_config['orchestrator'], ALL_TOOLS_SCHEMAS, "read", max_rounds=1,
        session_id=str(conversation.id),
    )

    read_hist = _get_tool_history(read_msgs, read_base_len)
    write_base = _build_messages(conversation, PHASE_PROMPTS['write'], tool_history=read_hist)
    write_base_len = len(write_base)
    write_msgs, final_text = await _tool_phase(write_base, model_config['orchestrator'], ALL_TOOLS_SCHEMAS, "write", max_rounds=1, error_retry=True, session_id=str(conversation.id))

    # If the model already responded with text, that IS the response — no extra inference
    if final_text:
        yield final_text
        return

    write_hist = _get_tool_history(write_msgs, write_base_len)
    respond_msgs = _build_messages(conversation, PHASE_PROMPTS['respond_execution'], tool_history=read_hist + write_hist)

    async for token in _stream_response(respond_msgs, model_config['orchestrator'], "respond-execution", tools=ALL_TOOLS_SCHEMAS, tool_choice="none", session_id=str(conversation.id)):
        yield token


async def unsure_path(conversation: ConversationSchema, model_config: dict):
    """Full freedom — model decides everything."""
    _log("═══ PATH: unsure")

    base = _build_messages(conversation, PHASE_PROMPTS['unsure'])
    base_len = len(base)
    msgs, final_text = await _tool_phase(
        base,
        model_config['orchestrator'], ALL_TOOLS_SCHEMAS, "unsure",
        session_id=str(conversation.id),
    )

    if final_text:
        yield final_text
        return

    tool_hist = _get_tool_history(msgs, base_len)
    respond_msgs = _build_messages(conversation, PHASE_PROMPTS['respond_query'], tool_history=tool_hist)

    async for token in _stream_response(respond_msgs, model_config['orchestrator'], "respond-unsure", tools=ALL_TOOLS_SCHEMAS, tool_choice="none", session_id=str(conversation.id)):
        yield token


# ── Public entry point ──────────────────────────────────────────────────

async def openai_agent(conversation: ConversationSchema, model_config: dict):
    """
    Main entry point. Routes the conversation through the appropriate
    execution path based on the router's classification.
    
    Yields tokens (text chunks) for the final response phase.
    Tool phases are silent — no tokens yielded.
    """
    msg_preview = conversation.messages[-1].text[:120]
    _log(f"═══════════════════════════════════════════════")
    _log(f"AGENT START — model_config={model_config}")
    try:
        route = classify(conversation)
        _log(f"AGENT route={route}")

        if route == 'normal':
            async for token in normal_path(conversation, model_config):
                yield token
        elif route == 'query':
            async for token in query_path(conversation, model_config):
                yield token
        elif route == 'execution':
            async for token in execution_path(conversation, model_config):
                yield token
        else:
            async for token in unsure_path(conversation, model_config):
                yield token

        _log("AGENT END — OK")

    except Exception as e:
        _log(f"AGENT ERROR: {e}")
        import traceback
        traceback.print_exc()
        yield 'ERROR_TOKEN'
