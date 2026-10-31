"""DBS JO16-1 fixtures from official Sportlink Club.Dataservice.

Only uses an authorized Club.Dataservice client ID configured via environment.
On source failures, callers may fall back to their existing verified fixture JSON.
Opponent logo links use official voetbal.nl logo API + club relation codes.
No HTML scraping, club-name guessing, expiring signed image URLs or dummy matches.
"""
from __future__ import annotations
import datetime as dt
import json
import re
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

NL = ZoneInfo('Europe/Amsterdam')
TEAM = re.compile(r'(?<!\w)DBS\s+(?:J)?O16\s*[-–]\s*1\b', re.I)
CANCELLED = ('afgelast', 'uitgesteld', 'geannuleerd', 'vervallen', 'cancelled', 'canceled', 'postponed')
CODE = re.compile(r'[A-Za-z0-9_-]{2,32}\Z')


def is_team(name):
    return bool(TEAM.search(str(name or '')))


def kickoff_date(row):
    """Parse common Sportlink ISO/dutch date/time field combinations."""
    raw = row.get('wedstrijddatetime') or row.get('wedstrijddatum') or row.get('kickoff') or row.get('datum')
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith('/Date('):
        match = re.match(r'/Date\((\d{10,13})', raw)
        if not match:
            return None
        epoch = int(match.group(1))
        return dt.datetime.fromtimestamp(epoch / (1000 if epoch > 10**11 else 1), tz=dt.timezone.utc).astimezone(NL)
    parsed = None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        for fmt in ('%d-%m-%Y', '%d-%m-%Y %H:%M', '%d/%m/%Y', '%Y%m%d'):
            try:
                parsed = dt.datetime.strptime(raw, fmt)
                break
            except ValueError:
                pass
    if parsed is None:
        return None
    timepart = str(row.get('aanvangstijd') or row.get('tijd') or '').strip()
    # Sportlink may supply a date at 00:00 plus an explicit separate start time.
    if parsed.hour == 0 and parsed.minute == 0 and timepart:
        mt = re.match(r'^(\d{1,2}):(\d\d)(?::\d\d)?$', timepart)
        if mt and int(mt.group(1)) <= 23 and int(mt.group(2)) <= 59:
            parsed = parsed.replace(hour=int(mt.group(1)), minute=int(mt.group(2)))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NL)
    return parsed.astimezone(NL)


def sanitize_clubcode(value):
    code = str(value or '').strip()
    return code if CODE.fullmatch(code) else None


def club_logo_url(code):
    code = sanitize_clubcode(code)
    if not code:
        return None
    return 'https://logoapi.voetbal.nl/logo.php?' + urllib.parse.urlencode({'clubcode': code})


def normalize_fixtures(payload, now, source):
    """Return verified DBS entries, sorted. Ignore other teams and canceled fixtures."""
    if isinstance(payload, dict):
        payload = payload.get('fixtures', payload.get('programma', payload.get('matches', [])))
    if not isinstance(payload, list):
        return []
    result=[]
    for row in payload:
        if not isinstance(row, dict):
            continue
        home = str(row.get('thuisteam') or row.get('home') or '').strip()
        away = str(row.get('uitteam') or row.get('away') or '').strip()
        ishome, isaway = is_team(home), is_team(away)
        if ishome == isaway:  # other DBS team or ambiguous/bad record
            continue
        status = str(row.get('status') or '').lower()
        if any(word in status for word in CANCELLED):
            continue
        when = kickoff_date(row)
        if when is None or when + dt.timedelta(hours=2) < now:
            continue
        home_code = sanitize_clubcode(row.get('thuisteamclubrelatiecode') or row.get('home_club_code'))
        away_code = sanitize_clubcode(row.get('uitteamclubrelatiecode') or row.get('away_club_code'))
        result.append({'date':when, 'home':home, 'away':away,
                       'opponent':away if ishome else home,
                       'home_game':ishome,
                       'venue':str(row.get('accommodatie') or row.get('venue') or '').strip(),
                       'home_club_code':home_code, 'away_club_code':away_code,
                       'source':source})
    return sorted(result, key=lambda x:x['date'])


def fetch_sportlink(client_id, team_code=None, days=42, fetch_json=None):
    """Fetch club/team fixture list; requires an authorized Club.Dataservice ID.

    Never log the request URL: it contains the client's ID.
    """
    if not client_id or not re.fullmatch(r'[A-Za-z0-9_-]{5,100}', client_id):
        raise ValueError('Missing/invalid Sportlink ClientID')
    params={'client_id':client_id,'aantaldagen':min(max(int(days),1),42),
            'aantalregels':500,'sorteervolgorde':'datum'}
    if team_code:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', team_code):
            raise ValueError('Invalid team code')
        params['teamcode']=team_code
    url='https://data.sportlink.com/programma?'+urllib.parse.urlencode(params)
    if fetch_json is not None:
        return fetch_json(url)
    req=urllib.request.Request(url,headers={
        'User-Agent':'DAYMARK private home display/3.8.3','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=12) as resp:
        raw=resp.read(2_000_001)
    if len(raw)>2_000_000:
        raise ValueError('Sportlink response too large')
    return json.loads(raw)


def next_fixture(now, client_id=None, team_code=None, external_feed=None,
                 manual_data=None, sportlink_fetch=None, json_fetch=None):
    """Prefer official Sportlink, then optional authorized JSON feed, then manual.

    When official source returns a valid empty programme, do NOT resurrect manual
    entries: an absence may mean a cancellation or rescheduling.
    """
    source=None
    official_error=None
    if client_id:
        try:
            feed = fetch_sportlink(client_id,team_code,fetch_json=sportlink_fetch)
            if not isinstance(feed,(list,dict)):
                raise ValueError('Unexpected Sportlink response')
            if isinstance(feed, dict) and not isinstance(feed.get('programma',feed.get('fixtures')),list):
                raise ValueError('Unexpected Sportlink response format')
            matches=normalize_fixtures(feed,now,'Sportlink Club.Dataservice')
            return {'fixture':matches[0] if matches else None,'available':bool(matches),
                    'source':'Sportlink Club.Dataservice','error':None}
        except Exception as exc:
            # Do not print exception details containing URL or client_id.
            official_error=type(exc).__name__
    if external_feed and external_feed.startswith('https://') and json_fetch:
        try:
            feed=json_fetch(external_feed)
            matches=normalize_fixtures(feed,now,'externe JSON-feed')
            return {'fixture':matches[0] if matches else None,'available':bool(matches),
                    'source':'externe JSON-feed','error':official_error}
        except Exception:
            pass
    matches=normalize_fixtures(manual_data or [],now,'handmatig bevestigd')
    return {'fixture':matches[0] if matches else None,'available':bool(matches),
            'source':'handmatig bevestigd' if matches else None,'error':official_error}
