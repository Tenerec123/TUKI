import os
from datetime import datetime
from zoneinfo import ZoneInfo
from ...schemas import TaskSchema, ProjectSchema, RoutineSchema, EventSchema, BlockRead, BlockExceptionRead, BlockOccurrenceRead
from ...database import SessionLocal
from ...logic.tasks import get_all_tasks_logic, search_tasks_logic
from ...logic.projects import get_all_project_logic, search_projects_logic
from ...logic.routines import get_all_routine_logic, search_routines_logic
from ...logic.events import get_all_events_logic, search_events_logic
from ...logic.blocks import get_all_blocks_logic, search_blocks_logic, get_block_exceptions_logic, get_block_occurrences_logic
from ...logic.notes import get_note_logic, search_notes_logic
import urllib.request
import json
import urllib.parse
import imaplib
import email as email_lib
from .websearch import fetch_page
from .dates import parse_relative_datetime
import yfinance as yf
from fastapi import HTTPException


def GetCurrentTime():
    '''
    Current date and time in the user's timezone (Europe/Madrid by default).
    Call this only to report the current date/time to the user — never to compute dates for other tools.
    Returns day of week, date, time, and UTC offset.
'''
    tz = ZoneInfo(os.environ.get('TIMEZONE', 'Europe/Madrid'))
    now = datetime.now(tz)
    offset = now.strftime('%z')
    return (f"{now.strftime('%A, %B %d, %Y, %H:%M')} "
            f"{now.tzname()} (UTC{offset[:3]}:{offset[3:]})")


def GetAllTasks():
    '''
    Returns all tasks, ordered by unfinished first, then priority (critical first), then deadline.
'''
    with SessionLocal() as db:
        tasks = get_all_tasks_logic(first_n=None, db=db)
        return [TaskSchema.model_validate(t).model_dump() for t in tasks]


def SearchTasks(text: str, limit: int = 5):
    '''
    Semantic search. Returns most relevant results.
'''
    with SessionLocal() as db:
        tasks = search_tasks_logic(text=text, limit=limit, db=db)
        return [TaskSchema.model_validate(t).model_dump() for t in tasks]


def GetAllProjects():
    '''
    Returns all projects.
'''
    with SessionLocal() as db:
        projects = get_all_project_logic(first_n=None, db=db)
        return [ProjectSchema.model_validate(p).model_dump() for p in projects]


def SearchProjects(text: str, limit: int = 5):
    '''
    Semantic search. Returns most relevant results.
'''
    with SessionLocal() as db:
        projects = search_projects_logic(text=text, limit=limit, db=db)
        return [ProjectSchema.model_validate(p).model_dump() for p in projects]


def GetAllRoutines():
    '''
    Returns all routines.
'''
    with SessionLocal() as db:
        routines = get_all_routine_logic(db=db)
        return [RoutineSchema.model_validate(r).model_dump() for r in routines]


def SearchRoutines(text: str, limit: int = 5):
    '''
    Semantic search. Returns most relevant results.
    '''
    with SessionLocal() as db:
        routines = search_routines_logic(text=text, limit=limit, db=db)
        return [RoutineSchema.model_validate(r).model_dump() for r in routines]


def GetAllEvents(first_n: int = None):
    '''
    Returns all events (timed commitments such as meetings or appointments).
    Ordered by start time, soonest first.
    Args:
        first_n: Max number of events to return (optional). null/absent = all events.
    '''
    with SessionLocal() as db:
        events = get_all_events_logic(first_n=first_n, db=db)
        return [EventSchema.model_validate(e).model_dump() for e in events]


def SearchEvents(text: str, limit: int = 5):
    '''
    Semantic search over events. Returns most relevant results.
    Args:
        text: Search query describing the event to find.
        limit: Max events to return (default 5).
    '''
    with SessionLocal() as db:
        events = search_events_logic(text=text, limit=limit, db=db)
        return [EventSchema.model_validate(e).model_dump() for e in events]


def GetAllBlocks(first_n: int = None) -> list[dict]:
    '''
    Returns recurring time blocks ordered by their start time.
    Args:
        first_n: Maximum number of blocks to return. null/absent returns all blocks.
    '''
    with SessionLocal() as db:
        blocks = get_all_blocks_logic(first_n=first_n, db=db)
        return [BlockRead.model_validate(block).model_dump() for block in blocks]


def SearchBlocks(text: str, limit: int = 5) -> list[dict]:
    '''
    Semantic search over recurring time blocks. Returns the most relevant results.
    Args:
        text: Search query describing the block to find.
        limit: Maximum number of blocks to return (default 5).
    '''
    with SessionLocal() as db:
        blocks = search_blocks_logic(text=text, limit=limit, db=db)
        return [BlockRead.model_validate(block).model_dump() for block in blocks]


def GetBlockOccurrences(block_id: int, from_datetime: str = None, to_datetime: str = None) -> list[dict] | str:
    '''
    Expands a recurring block into concrete occurrences. Defaults to the next year.
    Datetimes are naive local time and accept relative forms such as "tomorrow 09:00"
    or ISO-8601 local datetimes.
    Args:
        block_id: ID of the block.
        from_datetime: Inclusive start of the window (optional).
        to_datetime: Inclusive end of the window (optional).
    '''
    def parse_bound(value: str, name: str) -> datetime:
        try:
            iso_bound = datetime.fromisoformat(value)
        except ValueError:
            iso_bound = None
        if iso_bound is not None and iso_bound.tzinfo is not None:
            raise ValueError(f"{name} must be naive local time (no offset)")

        bound = parse_relative_datetime(value)
        if bound.tzinfo is not None:
            raise ValueError(f"{name} must be naive local time (no offset)")
        return bound

    try:
        start = None if from_datetime is None else parse_bound(from_datetime, "from")
        end = None if to_datetime is None else parse_bound(to_datetime, "to")
        with SessionLocal() as db:
            occurrences = get_block_occurrences_logic(
                id=block_id,
                window_start=start,
                window_end=end,
                db=db,
            )
            return [item.model_dump() for item in occurrences]
    except (ValueError, HTTPException) as error:
        return f"Error getting block occurrences: {error}"


def GetBlockExceptions(block_id: int) -> list[dict]:
    '''
    Returns the exceptions configured for a recurring block.
    Args:
        block_id: ID of the block.
    '''
    with SessionLocal() as db:
        exceptions = get_block_exceptions_logic(id=block_id, db=db)
        return [BlockExceptionRead.model_validate(item).model_dump() for item in exceptions]


def Weather(city: str = None):
    '''
    Current weather and 3-day forecast. Defaults to WEATHER_DEFAULT_CITY.
    Args:
        city: City name (optional).
    '''

    if city is None:
        city = os.environ.get('WEATHER_DEFAULT_CITY', '')
        if not city:
            return {'error': 'No city specified and WEATHER_DEFAULT_CITY not set in .env'}

    try:
        encoded = urllib.parse.quote(city)
        url = f"https://wttr.in/{encoded}?format=j1"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())

        current = data['current_condition'][0]
        result = {
            'city': data['nearest_area'][0]['areaName'][0]['value'],
            'country': data['nearest_area'][0]['country'][0]['value'],
            'temp_c': current['temp_C'],
            'feels_like_c': current['FeelsLikeC'],
            'humidity': current['humidity'],
            'wind_kph': current['windspeedKmph'],
            'wind_dir': current['winddir16Point'],
            'visibility_km': current['visibility'],
            'uv_index': current['uvIndex'],
            'condition': current['weatherDesc'][0]['value'],
            'forecast': []
        }

        for day in data['weather'][:3]:
            result['forecast'].append({
                'date': day['date'],
                'max_temp': day['maxtempC'],
                'min_temp': day['mintempC'],
                'condition': day['hourly'][0]['weatherDesc'][0]['value'],
                'chance_of_rain': day['hourly'][0]['chanceofrain']
            })

        return result
    except Exception as e:
        return {'error': f'Failed to get weather: {str(e)}'}


def CheckEmail(max_unreads: int = 5):
    '''
    Unread emails from IMAP inbox.
    Args:
        max_unreads: Max emails to fetch (default 5).
    '''

    server = os.environ.get('IMAP_SERVER', 'imap.gmail.com')
    user = os.environ.get('EMAIL_USER', '')
    passwd = os.environ.get('EMAIL_PASS', '')

    if not user or not passwd:
        return {'error': 'Email not configured. Set EMAIL_USER and EMAIL_PASS in .env'}

    try:
        mail = imaplib.IMAP4_SSL(server)
        mail.login(user, passwd)
        mail.select('INBOX')

        _, data = mail.search(None, 'UNSEEN')
        ids = data[0].split() if data[0] else []
        results = []

        for i in ids[-max_unreads:]:
            _, msg_data = mail.fetch(i, '(RFC822)')
            msg = email_lib.message_from_bytes(msg_data[0][1])
            payload = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/plain':
                        payload = part.get_payload(decode=True).decode('utf-8', errors='replace')[:200]
                        break
            else:
                payload = msg.get_payload(decode=True).decode('utf-8', errors='replace')[:200]
            results.append({
                'from': msg.get('From', ''),
                'subject': msg.get('Subject', ''),
                'date': msg.get('Date', ''),
                'snippet': payload.strip().replace('\n', ' ')[:200]
            })

        mail.logout()
        return {'unread_count': len(ids), 'emails': results}
    except Exception as e:
        return {'error': f'Failed to check email: {str(e)}'}


def Stocks(stock: str):
    '''
    Stock quote and 1-month history for a ticker.
'''
    try:
        dat = yf.Ticker(stock)
        info = dat.info
        return {
            'price': info.get('currentPrice'),
            'market_cap': info.get('marketCap'),
            'pe_ratio': info.get('forwardPE'),
            'dividend_yield': info.get('dividendYield'),
            '52w_high': info.get('fiftyTwoWeekHigh'),
            '52w_low': info.get('fiftyTwoWeekLow'),
            'volume': info.get('volume'),
            'avg_volume': info.get('averageVolume'),
            'previous_close': info.get('previousClose'),
            'open': info.get('open'),
            'day_range': f"{info.get('dayLow')} - {info.get('dayHigh')}",
            'sector': info.get('sector'),
            'industry': info.get('industry'),
            'employees': info.get('fullTimeEmployees'),
            'exchange': info.get('exchange'),
            'currency': info.get('currency'),
            'short_name': info.get('shortName'),
            'long_name': info.get('longName'),
            'history_1mo': dat.history(period='1mo').reset_index().to_dict(orient='records'),
        }
    except Exception as e:
        return {'error': f'Failed to get stock data: {str(e)}'}


def ReadNote(query: str, limit: int = 1):
    '''
    Semantic search through notes. Returns matching notes with their content.
    Args:
        query: Search query to find relevant notes.
        limit: Max notes to return (default 1).
    '''
    with SessionLocal() as db:
        notes = search_notes_logic(text=query, limit=limit, permission=True, db=db)
        results = []
        for note in notes:
            try:
                content = get_note_logic(id=note.id, db=db)
            except Exception:
                content = ""
            results.append({
                "id": note.id,
                "name": note.title,
                "path": note.path or "",
                "content": content,
            })
        return results


def DraftReadNote(query: str, limit: int = 1):
    '''
    Semantic search through notes. Returns matching notes with their content.
    Args:
        query: Search query to find relevant notes.
        limit: Max notes to return (default 1).
    '''
    with SessionLocal() as db:
        notes = search_notes_logic(text=query, permission=True, limit=limit, db=db)
        results = []
        for note in notes:
            try:
                content = get_note_logic(id=note.id, db=db)
            except Exception:
                content = ""
            results.append({
                "i": note.id,
                "n": note.title,
                "p": note.path or "",
                "content": content,
            })
        return results

async def WebFetch(url: str):
    '''
    Fetch a web page and return its main text content, clean of HTML.
    '''
    text = await fetch_page(url)
    return text or f"Error: Could not extract content from {url}"
