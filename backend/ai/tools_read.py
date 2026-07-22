"""Read-only tool functions for the AI agent.

Each function has a standardized docstring:
    - First line(s): description
    - Args: (optional, only for non-obvious parameters)
"""

import os
from ..schemas import TaskSchema, ProjectSchema, RoutineSchema
from ..database import SessionLocal
from ..logic.tasks import get_all_tasks_logic, search_tasks_logic
from ..logic.projects import get_all_project_logic, search_projects_logic
from ..logic.routines import get_all_routine_logic, search_routines_logic
import yfinance as yf


# ── Compact serialization for tool results ──────────────────────────────
# Short keys reduce token cost while staying native JSON for the model.
# Key map: id→i, name→n, description→d, priority→p, deadline→dl,
#          finished→done, project_id→proj, frequency→freq, init_date→ini,
#          icon→ic, parent_id→par, sub_tasks→tasks, sub_routines→routines,
#          sub_projects→children

_TASK_K = {"id": "i", "name": "n", "description": "d", "priority": "p",
           "deadline": "dl", "finished": "done", "project_id": "proj"}
_ROUTINE_K = {"id": "i", "name": "n", "description": "d", "priority": "p",
              "frequency": "freq", "init_date": "ini", "project_id": "proj", "icon": "ic"}
_PROJECT_K = {"id": "i", "name": "n", "description": "d", "priority": "p",
              "parent_id": "par", "sub_tasks": "tasks", "sub_routines": "routines",
              "sub_projects": "children"}


def _compact(d: dict, key_map: dict) -> dict:
    """Rename keys using key_map. Drop keys not in key_map."""
    return {key_map.get(k, k): v for k, v in d.items() if k in key_map}


def _compact_list(items: list, key_map: dict) -> list:
    return [_compact(item, key_map) for item in items]


def GetAllTasks():
    '''
    Returns all tasks.
'''
    with SessionLocal() as db:
        tasks = get_all_tasks_logic(first_n=None, db=db)
        return _compact_list([TaskSchema.model_validate(t).model_dump() for t in tasks], _TASK_K)


def SearchTasks(text: str, limit: int = 5):
    '''
    Semantic search. Returns most relevant results.
'''
    with SessionLocal() as db:
        tasks = search_tasks_logic(text=text, limit=limit, db=db)
        return _compact_list([TaskSchema.model_validate(t).model_dump() for t in tasks], _TASK_K)


def GetAllProjects():
    '''
    Returns all projects.
'''
    with SessionLocal() as db:
        projects = get_all_project_logic(first_n=None, db=db)
        return _compact_list([ProjectSchema.model_validate(p).model_dump() for p in projects], _PROJECT_K)


def SearchProjects(text: str, limit: int = 5):
    '''
    Semantic search. Returns most relevant results.
'''
    with SessionLocal() as db:
        projects = search_projects_logic(text=text, limit=limit, db=db)
        return _compact_list([ProjectSchema.model_validate(p).model_dump() for p in projects], _PROJECT_K)


def GetAllRoutines():
    '''
    Returns all routines.
'''
    with SessionLocal() as db:
        routines = get_all_routine_logic(db=db)
        return _compact_list([RoutineSchema.model_validate(r).model_dump() for r in routines], _ROUTINE_K)


def SearchRoutines(text: str, limit: int = 5):
    '''
    Semantic search. Returns most relevant results.
'''
    with SessionLocal() as db:
        routines = search_routines_logic(text=text, limit=limit, db=db)
        return _compact_list([RoutineSchema.model_validate(r).model_dump() for r in routines], _ROUTINE_K)


def Weather(city: str = None):
    '''
    Current weather and 3-day forecast. Defaults to WEATHER_DEFAULT_CITY.
    Args:
        city: City name (optional).
    '''
    import urllib.request
    import json
    import urllib.parse

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


def WebSearch(query: str, max_results: int = 5):
    '''
    DuckDuckGo search. Returns title, snippet, URL.
'''
    import logging
    logging.getLogger('primp').setLevel(logging.WARNING)
    from ddgs import DDGS
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
        return [
            {'title': r['title'], 'url': r['href'], 'snippet': r['body']}
            for r in results
        ]


def CheckEmail(max_unreads: int = 5):
    '''
    Unread emails from IMAP inbox.
    Args:
        max_unreads: Max emails to fetch (default 5).
    '''
    import imaplib
    import email as email_lib

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
