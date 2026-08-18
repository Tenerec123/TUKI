from ..database import SessionLocal
from ..models import Config

MAX_AGENTIC_ROUNDS = 10

SYSTEM_PROMPT = '''
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

WEB_SEARCH_SYSTEM_PROMPT = '''
You are a web search subagent.
Respond the orhcestrator question by searching on the internet.
URLs with title and snippet provided. If it's enough to answer the question, make a text response directly with no WebFetch.
If more data needed, use the WebFetch tool over some the URLs provided you consider the most useful to ask the question.
Use parallel tool call if you need more than one WebFetch.
After getting the web data, you must respond mandatory, no more calls.
If you haven't found the answer, respond that or at least say what you've found even if it's not all the data asked.
Respond only what is asked as short as possible.'''

def get_model_config() -> dict:
    defaults = {
        'orchestrator': 'openai/gpt-5.6-luna',
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
        print(f"[CONFIG] Error reading model config: {e}")
    finally:
        db.close()
    return defaults 