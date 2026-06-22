"""Read-only tool functions for the AI agent.

Each function has a standardized docstring:
    - Description (everything before 'Type:')
    - Type: read
    - Args: (optional, only for non-obvious parameters)
"""

import os
from ...schemas import TaskSchema, ProjectSchema, RoutineSchema
from ...database import SessionLocal
from ..tasks_logic import get_all_tasks_logic, search_tasks_logic
from ..projects_logic import get_all_project_logic, search_projects_logic
from ..routines_logic import get_all_routine_logic, search_routines_logic
import yfinance as yf


def GetAllTasks():
    '''
    Returns all tasks in the database.
    Type: read
    '''
    with SessionLocal() as db:
        tasks = get_all_tasks_logic(first_n=None, db=db)
        return [TaskSchema.model_validate(t).model_dump() for t in tasks]


def SearchTasks(text: str, limit: int = 5):
    '''
    Searches tasks by semantic similarity to natural language.
    Returns the most relevant tasks ranked by relevance (cosine distance).
    Type: read
    '''
    with SessionLocal() as db:
        tasks = search_tasks_logic(text=text, limit=limit, db=db)
        return [TaskSchema.model_validate(t).model_dump() for t in tasks]


def GetAllProjects():
    '''
    Returns all projects in the database.
    Type: read
    '''
    with SessionLocal() as db:
        projects = get_all_project_logic(first_n=None, db=db)
        return [ProjectSchema.model_validate(p).model_dump() for p in projects]


def SearchProjects(text: str, limit: int = 5):
    '''
    Searches projects by semantic similarity to natural language.
    Returns the most relevant projects ranked by relevance (cosine distance).
    Type: read
    '''
    with SessionLocal() as db:
        projects = search_projects_logic(text=text, limit=limit, db=db)
        return [ProjectSchema.model_validate(p).model_dump() for p in projects]


def GetAllRoutines():
    '''
    Returns all routines in the database.
    Type: read
    '''
    with SessionLocal() as db:
        routines = get_all_routine_logic(db=db)
        return [RoutineSchema.model_validate(r).model_dump() for r in routines]


def SearchRoutines(text: str, limit: int = 5):
    '''
    Searches routines by semantic similarity to natural language.
    Returns the most relevant routines ranked by relevance (cosine distance).
    Type: read
    '''
    with SessionLocal() as db:
        routines = search_routines_logic(text=text, limit=limit, db=db)
        return [RoutineSchema.model_validate(r).model_dump() for r in routines]


def Weather(city: str = None):
    '''
    Gets current weather and forecast for a city.
    If no city is provided, uses the default from WEATHER_DEFAULT_CITY in .env.
    Returns current conditions, temperature, humidity, wind, visibility, UV, and short forecast for the next 3 days.
    Type: read
    Args:
        city: City name. Optional — defaults to configured city if omitted.
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
    Searches the web using DuckDuckGo.
    Returns title, snippet, and URL for each result.
    Use for current events, recent technical info, or anything the model's training data might not cover.
    Type: read
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
    Checks the configured email inbox for unread messages.
    Returns sender, subject, and a snippet for each unread.
    Supports any IMAP server (Gmail, Outlook, custom).
    Type: read
    Args:
        max_unreads: Maximum number of unread emails to fetch (default 5).
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
    Gets current stock data and recent price history for a ticker symbol.
    Returns price, market cap, P/E ratio, 52-week range, volume, and 1-month history.
    Type: read
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
