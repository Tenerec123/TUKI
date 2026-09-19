from ..database import SessionLocal
from ..models import Config

MAX_AGENTIC_ROUNDS = 10

SYSTEM_PROMPT = '''
T.U.K.I. — productivity assistant. Tone: direct, technical, robot.
User: developer. Lang: Spanish/English.
Rules: No raw JSON in responses. No tool calls in visible text. Use $ for LaTeX.
Tool Calls:
-- OpenAI SDK tool calling format.
-- Don't invent id's, get them with the get tools.
-- Use parallel tool calling as much as you can.
-- Don't use it if tool B call depends on tool A result.
-- If you have all the info to respond or you've executed the order, don't use any tool and respond with text
-- You can return text in the tool calling inferences if necessary
-- If the task involves dates, deadlines, or time, call GetCurrentTime first. NEVER INVENT DATES
-- Update tools only change the fields you send. null or absent = leave unchanged. There is NO way to unassign a project from a task/routine.
'''

AUDIO_SYSTEM_PROMPT = SYSTEM_PROMPT + '''
You are in audio mode.
Rules: Make a short summary of the response, as short as possible, PLAIN TEXT, markdown or latext FORBIDDEN.
The literal output is converted to audio, have that in count to create the response (e.g dos punto cero instead of 2.0 or raíz de dos instead of sqrt(2))
If many text needed, create a note with the extra info and say it to the user.
NEVER USE . IF IT'S NOT TO FINISH A PHRASE
Speech structure (streamed TTS): your answer is synthesized SENTENCE BY SENTENCE and spoken as soon as each sentence is ready, while you keep generating the rest.
Rules:
-- Write short, separate sentences. End every complete thought with terminal punctuation (. ! ?) or a newline; each sentence is synthesized and spoken on its own.
-- Keep each sentence concise: one complete thought per sentence.
-- If the task will take time or requires executing actions, output a short acknowledgment sentence IMMEDIATELY before doing the work (e.g., "Claro, ahora te lo hago." / "Un momento."), then a short completion sentence when done (e.g., "Listo, ya está hecho."). This gives the user instant spoken feedback while the work happens.
'''

WEB_SEARCH_SYSTEM_PROMPT = '''
You are a web search summarizer.
Respond only what is asked in the query as short as possible.
If you have not found all data asked, give what you have and say what lacks.
'''

# Hardcoded provider pins: when the orchestrator is one of these base models,
# OpenRouter is asked to try this provider FIRST, falling back to other
# providers automatically if it fails. Matches OpenRouter's provider routing
# ("provider.order" request field), not the deprecated ":provider" suffix.
ORCHESTRATOR_PROVIDER_PINS = {
    "openai/gpt-oss-120b": "cerebras",
}


def get_orchestrator_provider_pin(model: str) -> str | None:
    """Return the pinned provider for an orchestrator model, if any.

    Only the base model slug matters: any routing/catalog suffix on the
    configured value (e.g. ":exacto") is stripped before lookup, so the pin
    applies no matter how the model id is written.
    """
    base = model.split(":")[0]
    return ORCHESTRATOR_PROVIDER_PINS.get(base)


def get_model_config() -> dict:
    defaults = {
        'orchestrator': 'openai/gpt-oss-120b',
        'searcher': 'google/gemini-2.5-flash-lite',
        'stt': 'nvidia/parakeet-tdt-0.6b-v3',
        'stt_provider': 'openrouter',  # 'openrouter' or 'deepgram'
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
        print(f"[CONFIG] Error reading model config: {e}")
    finally:
        db.close()
    return defaults 