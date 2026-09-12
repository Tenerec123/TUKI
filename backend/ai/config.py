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
You are in audio mode, so the user will hear only your final text, the last one with no tool calls.
Rules: Make a short summary of the response, as short as possible, plain text, no md, no latex, just words.
If many text needed, create a note with the extra info and say it to the user
The text sent to you has been converted to text with a light audio to text model, so assume that might be errors.
'''

WEB_SEARCH_SYSTEM_PROMPT = '''
You are a web search summarizer.
Respond only what is asked in the query as short as possible.
If you have not found all data asked, give what you have and say what lacks.
'''

def get_model_config() -> dict:
    defaults = {
        'orchestrator': 'openai/gpt-5.6-luna',
        'searcher': 'google/gemini-2.5-flash-lite',
        'stt': 'nvidia/parakeet-tdt-0.6b-v3',
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