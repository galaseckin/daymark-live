#!/usr/bin/env python3
"""DAYMARK v3.8.2 • live-rendered Kindle Paperwhite 2 landscape dashboard.

Generates both a 1024x758 design preview and a physically rotated
758x1024 framebuffer image (so a portrait-orientation Kindle can be
turned clockwise and used as a landscape desk display).

Demo: python3 daymark.py --demo
Live: python3 daymark.py
USB install: add --install (the Kindle must be mounted at /Volumes/Kindle)
Data is fetched on GitHub Actions (or a personal server). Kindle periodically downloads
a PNG. Never silently substitute made-up numbers in live mode.
"""
from __future__ import annotations
import argparse
import io
import hashlib
import datetime as dt
import json
import math
import os
import pathlib
import shutil
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE = pathlib.Path(__file__).resolve().parent
W,H=1024,758
TZ=ZoneInfo('Europe/Amsterdam')
WHITE=255; BLACK=17; INK=31; GRAY=95; MID=151; BORDER=207; PALE=246
DAYS=['maandag','dinsdag','woensdag','donderdag','vrijdag','zaterdag','zondag']
MONTHS=['jan','feb','mrt','apr','mei','jun','jul','aug','sep','okt','nov','dec']
DEFAULT_CONFIG={
    "city":"Eindhoven",
    "weather_lat":51.4416,
    "weather_lon":5.4697,
    "origin_lat":51.44148,
    "origin_lon":5.40563,
    "origin_area":"Meerhoven (Meerrijk)",
    "utrecht_lat":52.0854,
    "utrecht_lon":5.17556,
    "breda_lat":51.5888,
    "breda_lon":4.7769,
    "maastricht_lat":50.8514,
    "maastricht_lon":5.6910,
    "notes":"Google Maps Routes API met GOOGLE_MAPS_API_KEY als GitHub Secret. Breda en Maastricht staan standaard op openbare stadscentra; UU op Heidelberglaan 8. Geen kantooradressen of thuisadres openbaar maken."
}


def get_config():
    p=BASE/'config.json'
    if not p.exists():
        p.write_text(json.dumps(DEFAULT_CONFIG,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    config={**DEFAULT_CONFIG, **json.loads(p.read_text(encoding='utf-8'))}
    env_keys={'origin_lat':'DAYMARK_ORIGIN_LAT', 'origin_lon':'DAYMARK_ORIGIN_LON',
              'breda_lat':'DAYMARK_BREDA_LAT', 'breda_lon':'DAYMARK_BREDA_LON',
              'maastricht_lat':'DAYMARK_MAASTRICHT_LAT', 'maastricht_lon':'DAYMARK_MAASTRICHT_LON',
              'utrecht_lat':'DAYMARK_UTRECHT_LAT','utrecht_lon':'DAYMARK_UTRECHT_LON'}
    # Existing GitHub Secrets might still contain Eindhoven-Centrum coordinates.
    # Ignore them by default: use a public neighborhood point in Meerhoven.
    # To enable a private custom origin later, opt in using DAYMARK_ORIGIN_MODE=custom.
    if os.environ.get('DAYMARK_ORIGIN_MODE') != 'custom':
        config['origin_lat'], config['origin_lon'] = 51.44148, 5.40563
        config['origin_area'] = 'Meerhoven (Meerrijk)'
        env_keys.pop('origin_lat',None); env_keys.pop('origin_lon',None)
    if config.get('utrecht_lat') is None: config['utrecht_lat']=52.0854
    if config.get('utrecht_lon') is None: config['utrecht_lon']=5.17556
    for prop, env in env_keys.items():
        if os.environ.get(env):
            try:config[prop]=float(os.environ[env])
            except ValueError:print('Ongeldige coordinaat in '+env)
    return config


def get_json(url, timeout=9):
    request=urllib.request.Request(url,headers={'User-Agent':'DAYMARK/2.0 personal e-ink display','Accept':'application/json'})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        return json.load(response)


def local_date(value):
    try:
        return dt.datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(TZ)
    except (TypeError,AttributeError,ValueError): return None


def weather_description(code, is_day=True):
    """Human-readable WMO conditions; clear nights are NOT 'Zonnig'."""
    if code == 0: return 'Zonnig' if is_day else 'Helder'
    if code in (1,2): return 'Licht bewolkt'
    if code == 3: return 'Bewolkt'
    if code in (45,48): return 'Mist'
    if code in (51,53,55,56,57): return 'Motregen'
    if code in (61,63,65,66,67,80,81,82): return 'Regen'
    if code in (71,73,75,77,85,86): return 'Sneeuw'
    if code in (95,96,99): return 'Onweer'
    return 'Wisselvallig' if code is not None else 'Onbekend'


def weather(cfg):
    # Today + 3 calendar days ahead, in Eindhoven local time.
    params={'latitude':cfg['weather_lat'],'longitude':cfg['weather_lon'],
            'current':'temperature_2m,weather_code,apparent_temperature,wind_speed_10m,is_day',
            'daily':'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max',
            'timezone':'Europe/Amsterdam','forecast_days':4}
    obj=get_json('https://api.open-meteo.com/v1/forecast?'+urllib.parse.urlencode(params))
    cur=obj.get('current') or {}; daily=obj.get('daily') or {}
    code=cur.get('weather_code'); is_day=cur.get('is_day')==1
    def at(key,index):
        values=daily.get(key) or []
        return values[index] if index < len(values) else None
    future=[]
    for index in range(1,4):
        date_text=at('time',index)
        try: date=dt.date.fromisoformat(date_text)
        except (TypeError,ValueError): continue
        dcode=at('weather_code',index)
        future.append({'date':date, 'code':dcode,
            'desc':weather_description(dcode),
            'high':at('temperature_2m_max',index),
            'low':at('temperature_2m_min',index),
            'rain':at('precipitation_probability_max',index)})
    # Current.time from Open-Meteo is already local due timezone param.
    observed=cur.get('time')
    try: observed_at=dt.datetime.fromisoformat(observed).replace(tzinfo=TZ)
    except (ValueError,TypeError): observed_at=None
    return {'temp':cur.get('temperature_2m'), 'feels':cur.get('apparent_temperature'),
            'desc':weather_description(code,is_day), 'code':code,'is_day':is_day,
            'observed_at':observed_at,'high':at('temperature_2m_max',0),
            'low':at('temperature_2m_min',0),'rain':at('precipitation_probability_max',0),
            'wind':cur.get('wind_speed_10m'),'forecast':future}


def football(now):
    """Galatasaray, first live ESPN; fallback to dated, verified fixture cache.

    The ESPN API is unofficial and can return 403 or incomplete schedules in
    GitHub Actions. Curated fixtures are NOT described as live; their source is
    exposed in status.json so a changed kickoff is never silently concealed.
    """
    leagues = [('tur.1', 'Süper Lig'), ('uefa.champions', 'Champions League')]
    events = {}
    successful_sources = 0
    source_errors = []

    def collect(payload, league, league_name):
        for event in payload.get('events', []) or []:
            if not isinstance(event, dict) or not event.get('id'):
                continue
            key = f"{league}:{event['id']}"
            if key not in events or len(str(event)) > len(str(events[key][0])):
                events[key] = (event, league_name)

    for league, league_name in leagues:
        # Some GitHub Actions IPs are blocked by site.api.espn.com (HTTP 403).
        # Try ESPN's web API host first, then original. Unofficial interfaces.
        def fetch(path):
            errors = []
            for host in ('site.web.api.espn.com', 'site.api.espn.com'):
                try:
                    return get_json(f'https://{host}/apis/site/v2/sports/soccer/{league}/{path}', timeout=8)
                except Exception as ex:
                    errors.append(str(ex))
            raise RuntimeError(' | '.join(errors)[:240])

        # Entire-season responses sometimes contain only completed games.
        for query in ('', f'?season={now.year}', f'?season={now.year+1}'):
            try:
                collect(fetch(f'teams/432/schedule{query}'), league, league_name)
                successful_sources += 1
            except Exception as ex:
                source_errors.append(f'{league} team {query}: {ex}')

        # ESPN removed the YYYYMMDD-YYYYMMDD range form in September 2026:
        # it now returns HTTP 400. Month queries YYYYMM are still supported.
        # Look across the previous, current, and next two calendar months;
        # raise the 100-event default cap with limit=500. No date ranges.
        month_number = now.year * 12 + (now.month - 1)
        for month_offset in (-1, 0, 1, 2):
            index = month_number + month_offset
            month = f'{index // 12:04d}{index % 12 + 1:02d}'
            params = urllib.parse.urlencode({'dates': month, 'limit': 500})
            try:
                collect(fetch('scoreboard?' + params), league, league_name)
                successful_sources += 1
            except Exception as ex:
                source_errors.append(f'{league} scoreboard {month}: {ex}')

    def is_gal(side):
        team = (side.get('team') or {}) if isinstance(side,dict) else {}
        name = str(team.get('displayName') or team.get('name') or '').casefold()
        return str(team.get('id')) == '432' or 'galatasaray' in name

    def score(side):
        value = side.get('score')
        if isinstance(value,dict):
            value=value.get('displayValue',value.get('value'))
        return str(value) if value is not None else '?'

    def logo(side):
        team=side.get('team') or {}
        logos=team.get('logos') or []
        return (logos[0].get('href') if logos and isinstance(logos[0],dict) else None) or team.get('logo')

    past, future = [], []
    for event, league_name in events.values():
        when=local_date(event.get('date'))
        if when is None: continue
        comps=event.get('competitions') or []
        if not comps or not isinstance(comps[0],dict): continue
        sides=comps[0].get('competitors') or []
        if len(sides)!=2: continue
        us=next((s for s in sides if is_gal(s)),None)
        them=next((s for s in sides if not is_gal(s)),None)
        if us is None or them is None:continue
        status=(event.get('status') or {}).get('type') or (comps[0].get('status') or {}).get('type') or {}
        state=status.get('state')
        finished=status.get('completed') is True or state=='post'
        live=state=='in'
        item={
          'opponent':str((them.get('team') or {}).get('displayName') or 'Tegenstander'),
          'opponent_id':str((them.get('team') or {}).get('id') or ''),
          'date':when,'league':league_name,'home':us.get('homeAway')=='home',
          'score':f'{score(us)} - {score(them)}',
          'gs_logo':None,'opponent_logo':logo(them),
          'live':live,'source':'espn_live_feed',
        }
        if finished: past.append(item)
        elif live or when>=now-dt.timedelta(hours=3):future.append(item)

    # Curated fallback, checked 2026-10-30. Official Galatasaray UCL schedule,
    # VfB Stuttgart confirmation, plus public/TFF-derived Super Lig fixtures.
    # All kickoff times below are EUROPE/AMSTERDAM, not Turkey time.
    # Do not silently treat this cached calendar as a realtime source.
    confirmed=[
      ('2026-11-03T18:45:00','VfB Stuttgart','Champions League', True,'134'),
      ('2026-11-07T17:00:00','Amedspor','Süper Lig', True,''),
      ('2026-11-21T14:00:00','Samsunspor','Süper Lig', True,''),
      ('2026-11-24T18:45:00','Aston Villa','Champions League', True,''),
      ('2026-11-30T19:00:00','Beşiktaş','Süper Lig', False,''),
      ('2026-12-08T21:00:00','AEK Athens','Champions League', False,''),
      ('2027-01-19T18:45:00','Feyenoord','Champions League',True,''),
      ('2027-01-27T21:00:00','Paris Saint-Germain','Champions League',False,''),
    ]
    # 2026-10-30 22:06 local: Konyaspor-Galatasaray finished 0-4;
    # keep as last-match fallback only if no newer ESPN result exists.
    previous={'opponent':'Konyaspor', 'opponent_id':'7648',
      'date':dt.datetime(2026,10,30,20,0,tzinfo=TZ),
      'league':'Süper Lig','home':False,'score':'4 - 0',
      'gs_logo':None,
      'opponent_logo':'https://a.espncdn.com/i/teamlogos/soccer/500/7648.png',
      'live':False,'source':'verified_result_cache_2026-10-30'}
    # Score in UI always Galatasaray first; 4-0 Galatasaray victory.
    if now>=dt.datetime(2026,10,30,22,6,tzinfo=TZ):past.append(previous)
    for iso, opponent, league, home, teamid in confirmed:
        when=dt.datetime.fromisoformat(iso).replace(tzinfo=TZ)
        if when<now-dt.timedelta(hours=3):continue
        future.append({'opponent':opponent,'opponent_id':teamid,
            'opponent_logo':f'https://a.espncdn.com/i/teamlogos/soccer/500/{teamid}.png' if teamid else None,
            'gs_logo':None,'date':when,'league':league,'home':home,
            'live':False,'source':'verified_schedule_cache_2026-10-30'})

    # Prefer freshest match; if two sources say same opponent/day, use API.
    # Cached fixtures only fill gaps; no events fabricated in live mode.
    def normalize(opponent):
        return ''.join(ch for ch in opponent.casefold() if ch.isalnum())
    # Group by opponent and local calendar date. Prefer updated API kickoff
    # over the cached kickoff even when a confirmed time has changed.
    preferred={}
    for fixture in future:
        key=(normalize(fixture['opponent']),fixture['date'].date())
        older=preferred.get(key)
        if older is None or (fixture['source']=='espn_live_feed' and older['source']!='espn_live_feed'):
            preferred[key]=fixture
    dedup=sorted(preferred.values(),key=lambda fixture:fixture['date'])
    past.sort(key=lambda e:(e['date'],e['source']=='espn_live_feed'), reverse=True)
    if not dedup:
        print('Geen toekomstige Galatasaray-wedstrijd gevonden; ESPN en gecontroleerde cache ontbreken.')
    return {'last':past[0] if past else None,
            'next':dedup[0] if dedup else None,
            'sources_ok':successful_sources,
            'events_checked':len(events),
            'feed_errors':source_errors[:3]}


def weekend_football(now):
    """Fetch real ESPN fixtures and scores for the entire Dutch weekend.

    A Saturday/Sunday split across two calendar months MUST query both months;
    ESPN's monthly scoreboard does not include next month's fixtures.
    """
    if now.weekday() not in (5, 6):
        return {'matches': [], 'sources_ok': 0, 'errors': [], 'months': []}
    saturday = (now - dt.timedelta(days=now.weekday() - 5)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    monday = saturday + dt.timedelta(days=2)
    months = sorted({saturday.strftime('%Y%m'),
                     (monday - dt.timedelta(seconds=1)).strftime('%Y%m')})
    leagues = [('tur.1', 'SÜPER LIG'), ('eng.1', 'PREMIER LEAGUE'),
               ('ger.1', 'BUNDESLIGA'), ('esp.1', 'LA LIGA')]
    matches_by_id = {}
    errors = []
    ok = 0
    for league, league_name in leagues:
        for month in months:
            params = urllib.parse.urlencode({'dates': month, 'limit': 500})
            payload = None
            failures = []
            for host in ('site.web.api.espn.com', 'site.api.espn.com'):
                try:
                    payload = get_json(
                        f'https://{host}/apis/site/v2/sports/soccer/{league}/scoreboard?{params}',
                        timeout=8)
                    ok += 1
                    break
                except Exception as exc:
                    failures.append(f'{host}: {type(exc).__name__}')
            if payload is None:
                errors.append(f'{league}/{month}: '+', '.join(failures))
                continue
            for ev in payload.get('events', []) or []:
                when = local_date(ev.get('date'))
                if not when or not saturday <= when < monday:
                    continue
                comp = (ev.get('competitions') or [{}])[0]
                sides = comp.get('competitors') or []
                if len(sides) != 2:
                    continue
                home = next((v for v in sides if v.get('homeAway') == 'home'), sides[0])
                away = next((v for v in sides if v.get('homeAway') == 'away'), sides[1])
                h, a = home.get('team') or {}, away.get('team') or {}
                hn = str(h.get('displayName') or h.get('name') or '')
                an = str(a.get('displayName') or a.get('name') or '')
                if not hn or not an or 'galatasaray' in (hn+' '+an).casefold():
                    continue  # Galatasaray has its own panel.
                status = ev.get('status') or {}
                state = (status.get('type') or {}).get('state', 'pre')
                def score(side):
                    value = side.get('score')
                    if isinstance(value, dict):
                        value = value.get('displayValue', value.get('value'))
                    return str(value) if value is not None else None
                hs, as_ = score(home), score(away)
                result = f'{hs}-{as_}' if state in ('in', 'post') and hs is not None and as_ is not None else None
                # ESPN status clocks vary by league; show a generic label if
                # the match clock is unavailable, rather than guessing a minute.
                match_state = 'LIVE' if state == 'in' else 'FT' if state == 'post' else None
                if state == 'in':
                    clock = str(status.get('displayClock') or '')
                    if ':' in clock and clock.split(':', 1)[0].isdigit():
                        match_state = clock.split(':', 1)[0] + "'"
                    elif (status.get('type') or {}).get('name') == 'STATUS_HALFTIME':
                        match_state = 'RUST'
                identifier = str(ev.get('id') or f'{league}:{hn}:{an}:{when.isoformat()}')
                matches_by_id[(league, identifier)] = {
                    'home': hn, 'away': an, 'date': when, 'league': league_name,
                    'fener': 'fenerbah' in (hn+' '+an).casefold(),
                    'state': state, 'state_label': match_state, 'score': result,
                    'id': identifier}
    matches = list(matches_by_id.values())
    big = ('liverpool', 'arsenal', 'manchester', 'chelsea', 'tottenham', 'bayern',
           'dortmund', 'barcelona', 'real madrid', 'atlético', 'atletico', 'newcastle')
    def priority(match):
        names = (match['home']+' '+match['away']).casefold()
        return (-sum(v in names for v in big), match['date'], match['id'])
    matches.sort(key=priority)
    # Reserve one place for a Turkish Super Lig match (if present).
    # All other slots still follow the existing big-match ranking.
    turkish = [m for m in matches if m['league'] == 'SÜPER LIG']
    selected = turkish[:1]
    selected_ids = {(m['league'],m['id']) for m in selected}
    selected += [m for m in matches if (m['league'],m['id']) not in selected_ids][:5-len(selected)]
    picked = sorted(selected, key=lambda match: (match['date'], match['id']))
    return {'matches': picked, 'sources_ok': ok, 'errors': errors, 'months': months}

def superlig_top3():
    """Current Turkish Super Lig standings from ESPN; empty on failed fetch."""
    errors=[]
    for host in ('site.web.api.espn.com','site.api.espn.com'):
        for path in ('standings','standings?season='+str(dt.datetime.now(TZ).year)):
            try:
                payload=get_json(f'https://{host}/apis/v2/sports/soccer/tur.1/{path}',timeout=8)
                # ESPN v2 standings response commonly: children[].standings.entries
                groups=payload.get('children') or []
                entries=[]
                for group in groups:
                    entries.extend((group.get('standings') or {}).get('entries') or [])
                if not entries:
                    entries=(payload.get('standings') or {}).get('entries') or []
                rows=[]
                for entry in entries:
                    team=entry.get('team') or {}
                    name=team.get('shortDisplayName') or team.get('displayName') or team.get('name')
                    stats=entry.get('stats') or []
                    pts=next((v.get('value') for v in stats if v.get('name') in ('points','point')),None)
                    rank=next((v.get('value') for v in stats if v.get('name') in ('rank','ranking')),None)
                    if name and pts is not None:
                        rows.append({'name':str(name),'points':int(float(pts)),
                                     'rank':int(float(rank)) if rank is not None else None})
                if rows:
                    if all(r['rank'] is not None for r in rows):
                        rows.sort(key=lambda r:r['rank'])
                    else:
                        rows.sort(key=lambda r:r['points'],reverse=True)
                    return {'teams':rows[:3],'available':True,'error':None}
                errors.append('Geen ranglijstregels')
            except Exception as exc:
                errors.append(type(exc).__name__+': '+str(exc)[:75])
    return {'teams':[],'available':False,'error':'; '.join(errors[-2:])}


def birthday_notice(now):
    """Filiz: 12 November; show 7-day countdown on weekdays, always on weekends."""
    birthday=dt.date(now.year,11,12)
    if now.date()>birthday:birthday=dt.date(now.year+1,11,12)
    days=(birthday-now.date()).days
    return {'name':'Filiz','date':birthday,'days':days,'show':now.weekday()>=5 or days<=7}



# KNVB/Sportlink pages often render the team schedule dynamically and may not
# expose a reliable machine-readable public endpoint. Prefer an explicit
# verified JSON list, with an optional externally maintained JSON feed.
# Never fabricate dates, opponents, or statuses. The label records provenance.
DBS_FIXTURES_FILE = BASE / 'dbs_fixtures.json'
DBS_FEED_ENV = 'DBS_FIXTURES_URL'
DBS_TEAM_NAME = 'DBS O16-1'
DBS_CLUB_LOGO_SOURCES = {
    'DBS': 'https://www.hollandsevelden.nl/images/club_logo_van_voetbalvereniging_dbs_uit_eindhoven.png',
    'NIEUW WOENSEL': 'https://www.hollandsevelden.nl/images/club_logo_van_voetbalvereniging_nieuw_woensel_uit_eindhoven.png',
}

def dbs_fixture_list(now, cfg=None):
    """Next verified DBS O16-1 fixture; no past match or guessed fixture.

    An optionally configured JSON feed must supply a list or {'fixtures': list};
    otherwise use the curated fixture file shipped alongside this script.
    Feed failures fall back to the curated list. Entries are ISO time in NL.
    """
    def validated_entries(payload, source):
        entries = payload if isinstance(payload,list) else (payload.get('fixtures',[]) if isinstance(payload,dict) else [])
        clean=[]
        for entry in entries:
            if not isinstance(entry,dict):continue
            when=local_date(entry.get('kickoff'))
            if when is None:continue
            home=str(entry.get('home') or '').strip()
            away=str(entry.get('away') or '').strip()
            if not home or not away:continue
            # Only fixtures involving the correct age category (avoid DBS 1).
            if 'dbs o16-1' not in (home+' | '+away).casefold():continue
            if when + dt.timedelta(hours=2) < now:continue
            clean.append({'date':when, 'home':home, 'away':away,
                          'opponent':away if home.casefold()=='dbs o16-1' else home,
                          'home_game':home.casefold()=='dbs o16-1',
                          'venue':str(entry.get('venue') or '').strip(),
                          'source':source})
        return clean
    choices=[]
    feed_url=os.environ.get(DBS_FEED_ENV,'').strip()
    if feed_url.startswith('https://'):
        try:
            choices=validated_entries(get_json(feed_url,timeout=8),'externe feed')
        except Exception as exc:
            print('WAARSCHUWING: DBS-feed niet beschikbaar:',type(exc).__name__)
    if not choices:
        try:
            choices=validated_entries(json.loads(DBS_FIXTURES_FILE.read_text(encoding='utf-8')),'handmatig bevestigd')
        except (OSError,ValueError) as exc:
            print('WAARSCHUWING: DBS-fixures niet beschikbaar:',type(exc).__name__)
    if not choices:
        return {'fixture':None,'available':False,'source':None}
    chosen=min(choices,key=lambda e:e['date'])
    return {'fixture':chosen,'available':True,'source':chosen['source']}


def dbs_club_logo(club):
    """Load real static crests if available; never manufacture a fake crest.

    Use server/club_logos/*.png as highest priority. If absent, a known public
    image URL is attempted (only when GitHub builds the new PNG, not on Kindle).
    If a source fails, draw_crest uses a clearly marked letter fallback.
    """
    key=str(club).casefold()
    if 'nieuw woensel' in key:
        name='nieuw_woensel'; url=DBS_CLUB_LOGO_SOURCES['NIEUW WOENSEL']
    elif 'dbs' in key:
        name='dbs'; url=DBS_CLUB_LOGO_SOURCES['DBS']
    else:
        return None
    local=BASE/'club_logos'/(name+'.png')
    if local.is_file():
        try:return Image.open(local).convert('RGBA')
        except Exception:pass
    return logo_image(url,name)


def stock(symbol):
    url='https://query1.finance.yahoo.com/v8/finance/chart/'+urllib.parse.quote(symbol,safe='')+'?range=1mo&interval=1d'
    raw=get_json(url)
    result=((raw.get('chart') or {}).get('result') or [None])[0]
    if not result:raise RuntimeError('Geen beursdata')
    meta=result.get('meta') or {}
    vals=(((result.get('indicators') or {}).get('quote') or [{}])[0]).get('close') or []
    vals=[float(x) for x in vals if isinstance(x,(float,int))]
    price=meta.get('regularMarketPrice')
    if price is None and vals:price=vals[-1]
    prev=meta.get('previousClose') or meta.get('chartPreviousClose')
    if prev is None and len(vals)>1:prev=vals[-2]
    change=(float(price)-float(prev))/float(prev)*100 if price is not None and prev else None
    timestamp=meta.get('regularMarketTime')
    return {'price':float(price) if price is not None else None,
            'change':change,'points':vals[-16:],
            'asof':dt.datetime.fromtimestamp(timestamp,TZ) if timestamp else None}


def gold():
    # Direct EUR per kg quote; no USD/oz conversion and no fabricated currency rates.
    d=get_json('https://goldprice.corgano.com/api/v1/latest?currency=EUR')['data']
    eur=d['currencies']['EUR']
    return {'price':float(eur['per_kg']),'change':d.get('change_pct'),
            'asof':local_date(d.get('as_of')),
            'source':'Gold price data by Corgano'}


def route_period(now):
    """Switch the direction by EUROPE/AMSTERDAM wall-clock time, including DST.

    Every day:
      00:00 - 14:59: Meerhoven -> 3 cities
      15:00 - 20:59: 3 cities -> Meerhoven
      21:00 - 23:59: Meerhoven -> 3 cities
    """
    local = now.astimezone(TZ)
    mins = local.hour * 60 + local.minute
    return 'terug' if 15 * 60 <= mins < 21 * 60 else 'heen'


def route_next_switch(now):
    """Display the next switching time in local NL time, not UTC."""
    local=now.astimezone(TZ)
    if local.hour < 15:
        return dt.datetime(local.year,local.month,local.day,15,0,tzinfo=TZ)
    if local.hour < 21:
        return dt.datetime(local.year,local.month,local.day,21,0,tzinfo=TZ)
    next_day=local.date()+dt.timedelta(days=1)
    return dt.datetime(next_day.year,next_day.month,next_day.day,15,0,tzinfo=TZ)


def route_points(cfg, name, now):
    """Produce a city route in the correct direction; missing coordinates remain unset."""
    central=[cfg.get('origin_lat'),cfg.get('origin_lon')]
    # Destination: public city centers for Breda/Maastricht, UU Science Park for Utrecht.
    other=[cfg.get(f'{name}_lat'),cfg.get(f'{name}_lon')]
    if any(x is None for x in central+other):return None
    return (other,central) if route_period(now)=='terug' else (central,other)


def commute(cfg, name, now):
    """Get current traffic-aware driving duration from the official Google Routes API.

    Requires Routes API enabled, billing set up and a restricted Google API key.
    No key or missing destination => empty result, NEVER a dummy duration.
    """
    key=os.environ.get('GOOGLE_MAPS_API_KEY')
    points=route_points(cfg,name,now)
    if not key or points is None:return None
    origin,dest=points
    def waypoint(latlon):
        return {'location': {'latLng': {'latitude':latlon[0], 'longitude':latlon[1]}}}
    request_obj={
        'origin':waypoint(origin), 'destination':waypoint(dest),
        'travelMode':'DRIVE', 'routingPreference':'TRAFFIC_AWARE',
        'languageCode':'nl-NL', 'units':'METRIC',
    }
    body=json.dumps(request_obj).encode('utf-8')
    req=urllib.request.Request(
        'https://routes.googleapis.com/directions/v2:computeRoutes',
        data=body, method='POST',
        headers={'Content-Type':'application/json','X-Goog-Api-Key':key,
                 'X-Goog-FieldMask':'routes.duration,routes.staticDuration'})
    with urllib.request.urlopen(req, timeout=12) as response:
        payload=json.load(response)
    routes=payload.get('routes') or []
    if not routes or not routes[0].get('duration'):
        raise RuntimeError('Google Maps heeft geen route of reistijd teruggegeven')
    seconds_text=routes[0]['duration']
    if not isinstance(seconds_text,str) or not seconds_text.endswith('s'):
        raise ValueError('Google Maps reistijdformaat onbekend')
    return max(1,round(float(seconds_text[:-1])/60))


def demo_data():
    now=dt.datetime.now(TZ)
    return {'now':now,'demo':True,'weather':{'temp':17,'desc':'Licht bewolkt','code':2,'is_day':True,'high':19,'low':10,'rain':20,'wind':9,'feels':16,
       'observed_at':now,'forecast':[{'date':(now+dt.timedelta(days=i)).date(),'code':c,'high':hi,'low':lo,'rain':rain} for i,(c,hi,lo,rain) in enumerate([(2,18,9,30),(61,16,8,80),(1,17,7,15)],1)]},
            'football':{'last':{'opponent':'Fenerbahce (demo)','opponent_id':'436','opponent_logo':'https://a.espncdn.com/i/teamlogos/soccer/500/436.png','gs_logo':GS_LOGO_URL,'score':'2 - 1','league':'Demo-wedstrijd','date':now-dt.timedelta(days=2),'home':True},
                        'next':{'opponent':'Konyaspor (demo)','opponent_id':'7648','opponent_logo':'https://a.espncdn.com/i/teamlogos/soccer/500/7648.png','gs_logo':GS_LOGO_URL,'date':now+dt.timedelta(days=3),'league':'Demo-wedstrijd','home':True}},
            'stocks':{'NVDA':{'price':210.48,'change':1.2,'points':[2,3,4,4,6,6,5,7,8]},
                      'PLTR':{'price':153.60,'change':-0.4,'points':[4,5,6,5,5,4,7,6,5]},
                      'AAPL':{'price':284.13,'change':0.8,'points':[2,4,3,5,7,6,8,8,9]}},
            'gold':{'price':128300.0,'change':0.3},
            'weekend':{'matches':[],'sources_ok':0,'errors':[]},
            'standings':{'teams':[],'available':False},
            'dbs':dbs_fixture_list(now),
            'routes':{'breda':43,'utrecht':72,'maastricht':66},
            'errors':[]}


def live_data(cfg):
    now=dt.datetime.now(TZ);errors=[]
    def attempt(name,fn,default):
        try:return fn()
        except Exception as exc:
            errors.append(f'{name}: {type(exc).__name__}: {exc}')
            return default
    return {'now':now,'demo':False,
            'weather':attempt('weer',lambda:weather(cfg),{}),
            'football':attempt('voetbal',lambda:football(now),{}),
            'stocks':{name:attempt(name,lambda n=name:stock(n),{}) for name in ('NVDA','PLTR','AAPL')},
            'gold':attempt('goud',gold,{}),
            'weekend':attempt('weekend_football',lambda:weekend_football(now),{'matches':[]}),
            'standings':attempt('superlig_top3',superlig_top3,{'teams':[],'available':False}) if now.weekday()>=5 else {'teams':[],'available':False},
            'dbs':dbs_fixture_list(now) if now.weekday()>=5 else {'fixture':None,'available':False,'source':None},
            'routes':{name:attempt(name,lambda n=name:commute(cfg,n,now),None) for name in ('breda','utrecht','maastricht')},
            'errors':errors}


def get_font(size,kind='regular'):
    fonts={
      'regular':['/System/Library/Fonts/Supplemental/Arial.ttf','/usr/share/fonts/truetype/lato/Lato-Regular.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'],
      'bold':['/System/Library/Fonts/Supplemental/Arial Bold.ttf','/usr/share/fonts/truetype/lato/Lato-Bold.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'],
      'heavy':['/System/Library/Fonts/Supplemental/Arial Bold.ttf','/usr/share/fonts/truetype/lato/Lato-Heavy.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']}
    for path in fonts[kind]:
        if pathlib.Path(path).is_file():return ImageFont.truetype(path,size)
    return ImageFont.load_default()



# All logo downloading runs on the MAC at render time. Kindle itself never
# downloads an image; it only displays our offline 758 x 1024 PNG.
LOGO_DIR=BASE/'logos_cache'
GS_LOGO_URL='https://commons.wikimedia.org/wiki/Special:FilePath/Galatasaray_S.K._Logo_2026_5-stars_Monochrome.png'

def logo_image(url, ident):
    """Fetch+cache opponent ESPN PNG logos or the verified CC0 five-star GS crest."""
    approved = (url == GS_LOGO_URL or
                url.startswith('https://a.espncdn.com/') or
                url in DBS_CLUB_LOGO_SOURCES.values())
    if not url or not approved:
        return None
    LOGO_DIR.mkdir(exist_ok=True)
    ident=''.join(ch for ch in str(ident) if ch.isalnum() or ch in '_-')[:40] or 'unknown'
    cache=LOGO_DIR/(ident+'_'+hashlib.sha256(url.encode()).hexdigest()[:12]+'.png')
    try:
        if cache.is_file():
            return Image.open(cache).convert('RGBA')
        req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 DAYMARK private e-ink dashboard'})
        with urllib.request.urlopen(req,timeout=4) as resp:
            raw=resp.read(2_000_001)
        if len(raw)>2_000_000: return None
        logo=Image.open(io.BytesIO(raw)).convert('RGBA')
        if logo.width>1600 or logo.height>1600: logo.thumbnail((800,800))
        logo.save(cache)
        return logo
    except Exception:
        return None


def draw_crest(im,d,x,y,w,h,img,name):
    """High contrast greyscale crest, centered. Monogram only on failed fetch."""
    if img is None:
        # Deliberately a clearly distinguishable fallback, NOT an official logo.
        d.ellipse((x+6,y+5,x+w-6,y+h-5),outline=31,width=3)
        monogram=('GS' if name=='GALATASARAY' else 'FB' if name.lower().startswith('fenerbahce') else 'K' if name.lower().startswith('konyaspor') else ''.join(word[0] for word in name.replace('(demo)','').split() if word)[:2].upper())
        f=get_font(21,'bold')
        d.text((x+w/2,y+h/2),monogram or '?',font=f,fill=31,anchor='mm')
        if name=='GALATASARAY':
            d.text((x+w/2,y+2),'★★★★★',font=get_font(10,'bold'),fill=17,anchor='mt')
        return
    source=img.copy()
    source.thumbnail((w-4,h-4),Image.Resampling.LANCZOS)
    canvas=Image.new('RGBA',source.size,(255,255,255,255))
    canvas.alpha_composite(source)
    mono=ImageOps.autocontrast(canvas.convert('L'))
    # Important: grayscale avoids losing red/yellow areas on the 16-shade screen.
    im.paste(mono,(x+(w-mono.width)//2,y+(h-mono.height)//2))

def render(data,cfg):
    im=Image.new('L',(W,H),WHITE); d=ImageDraw.Draw(im)
    fonts={}
    def f(n,k='regular'):
        if (n,k) not in fonts:fonts[n,k]=get_font(n,k)
        return fonts[n,k]
    def t(x,y,s,size=20,weight='regular',color=BLACK,anchor=None):
        d.text((x,y),str(s),font=f(size,weight),fill=color,anchor=anchor)
    def fit(s,n,maxw,weight='regular'):
        s=str(s)
        if d.textbbox((0,0),s,font=f(n,weight))[2]<=maxw:return s
        while s and d.textbbox((0,0),s+'…',font=f(n,weight))[2]>maxw:s=s[:-1]
        return s+'…'
    def label(x,y,s):t(x,y,s,15,'bold',GRAY)
    def rule(x1,y1,x2,y2,fill=BORDER,width=2):d.line((x1,y1,x2,y2),fill=fill,width=width)
    def panel(x1,y1,x2,y2):d.rounded_rectangle((x1,y1,x2,y2),radius=15,fill=WHITE,outline=BORDER,width=2)
    def light_panel(x1,y1,x2,y2):d.rounded_rectangle((x1,y1,x2,y2),radius=14,fill=PALE)
    def num(x):return f'{round(x):d}' if isinstance(x,(int,float)) else '—'
    def signed(x):return f'{x:+.1f}%' if isinstance(x,(int,float)) else '—'
    def euro(x):return ('€'+format(round(x),',').replace(',','.')) if isinstance(x,(int,float)) else '—'
    def spark(vals,x,y,w,h):
        if len(vals)<2:
            rule(x,y+h/2,x+w,y+h/2,fill=BORDER,width=2);return
        lo,hi=min(vals),max(vals)
        if hi==lo:hi=lo+1
        pts=[(x+w*i/(len(vals)-1), y+h-3-(a-lo)/(hi-lo)*(h-6)) for i,a in enumerate(vals)]
        d.line(pts,fill=INK,width=3,joint='curve')
        px,py=pts[-1];d.ellipse((px-3,py-3,px+3,py+3),fill=INK)
    def weather_icon(cx, cy, code, is_day=True, scale=1.0):
        """Compact monochrome WMO icon; clouds are one continuous silhouette."""
        def point(x, y):
            return (round(cx+x*scale), round(cy+y*scale))
        width = max(1, round(2.6*scale))
        def line(points, thickness=3):
            d.line([point(x,y) for x,y in points], fill=INK,
                   width=max(1,round(thickness*scale)), joint='curve')
        def bezier(p0,p1,p2,p3,steps=10):
            result=[]
            for i in range(steps+1):
                t=i/steps;u=1-t
                result.append((u**3*p0[0]+3*u*u*t*p1[0]+3*u*t*t*p2[0]+t**3*p3[0],
                               u**3*p0[1]+3*u*u*t*p1[1]+3*u*t*t*p2[1]+t**3*p3[1]))
            return result
        def cloud_shape():
            # Single closed outline; no overlapping ellipse borders.
            pts=[]
            for curve in (
                ((-24,21),(-36,21),(-37,5),(-25,1)),
                ((-25,1),(-23,-9),(-13,-14),(-3,-9)),
                ((-3,-9),(5,-28),(25,-19),(25,-3)),
                ((25,-3),(40,0),(38,21),(24,21))):
                pts.extend(bezier(*curve)[1:] if pts else bezier(*curve))
            pts.append((-24,21))
            coords=[point(x,y) for x,y in pts]
            d.polygon(coords, fill=WHITE)
            d.line(coords+[coords[0]], fill=INK, width=width, joint='curve')
        if code in (45,48):
            for y in (-13,0,13): line([(-29,y),(29,y)],4)
            return
        cloudy=code in (2,3,51,53,55,56,57,61,63,65,66,67,71,73,75,77,80,81,82,85,86,95,96,99)
        if code in (0,1,2):
            if is_day:
                d.ellipse((*point(-26,-26),*point(3,3)),outline=INK,width=width)
                for angle in range(0,360,45):
                    rad=math.radians(angle)
                    line([(-11+math.cos(rad)*22,-11+math.sin(rad)*22),
                          (-11+math.cos(rad)*29,-11+math.sin(rad)*29)],2)
            else:
                d.ellipse((*point(-25,-25),*point(7,7)), fill=INK)
                d.ellipse((*point(-16,-31),*point(16,1)), fill=WHITE)
        if cloudy:
            cloud_shape()
        if code in (51,53,55,56,57,61,63,65,66,67,80,81,82):
            for x in (-12,0,12):line([(x,28),(x-5,40)],3)
        elif code in (71,73,75,77,85,86):
            for x in (-13,1,15):
                d.ellipse((*point(x-2,29),*point(x+2,33)),fill=INK)
        elif code in (95,96,99):
            line([(4,22),(-5,35),(5,35),(-4,47)],4)
        if code is None:
            d.text((cx,cy),'?',font=f(26,'bold'),fill=INK,anchor='mm')

    now=data['now']; we=data.get('weather',{}); fb=data.get('football',{});
    stocks=data.get('stocks',{}); gd=data.get('gold',{}); routes=data.get('routes',{})
    gs_logo=logo_image(GS_LOGO_URL,'galatasaray_five_stars_2026')
    # Do not silently substitute an obsolete (4-star) logo. Explicit five stars only.
    if gs_logo is None:
        print('WAARSCHUWING: vijfsterrenlogo ontbreekt; duidelijke 5-sterren-tekstfallback (geen oud logo).')
    # TOP / identity bar
    t(33,18,'DAYMARK',33,'heavy')
    t(34,61,'LIVE DAILY BRIEF',12,'bold',GRAY)
    t(992,24,f'{DAYS[now.weekday()].upper()}  {now.day} {MONTHS[now.month-1].upper()} {now.year}',17,'bold',INK,anchor='ra')
    t(992,56,f'BIJGEWERKT  {now:%H:%M}  ·  EUROPE/AMSTERDAM',12,'bold',GRAY,anchor='ra')
    rule(33,94,991,94,INK,3)
    if data['demo']:
        d.rounded_rectangle((365,60,667,84),radius=5,fill=BLACK)
        t(516,72,'DEMO / ALLE CIJFERS FICTIEF',12,'bold',WHITE,anchor='mm')
    # Three columns
    col_y1,col_y2=111,570
    xw=(33,345);xf=(356,674);xm=(685,991)
    for bounds in (xw,xf,xm):panel(bounds[0],col_y1,bounds[1],col_y2)
    # WEATHER: current condition + the three upcoming days (no fabricated forecast)
    x=51
    label(x,133,'WEER  /  '+str(cfg['city']).upper())
    temp=we.get('temp')
    t(x,167,f'{num(temp)}°',91,'heavy')
    weather_icon(279,207,we.get('code'),we.get('is_day',True),1.13)
    t(x,272,fit(we.get('desc') or 'Geen weergegevens',23,277,'bold'),23,'bold',INK)
    t(x,308,f"MAX {num(we.get('high'))}°   MIN {num(we.get('low'))}°   REGEN {num(we.get('rain'))}%",14,'bold',GRAY)
    rule(51,339,326,339)
    label(x,354,'KOMENDE 3 DAGEN')
    future=we.get('forecast') or []
    for i in range(3):
        yy=390+i*49
        if i>0: rule(51,yy-9,325,yy-9,BORDER,1)
        item=future[i] if i<len(future) else {}
        day=item.get('date')
        day_label=f'{DAYS[day.weekday()][:2].upper()} {day.day}' if isinstance(day,dt.date) else '—'
        t(52,yy,day_label,18,'bold')
        weather_icon(157,yy+10,item.get('code'),True,0.42)
        t(251,yy,f"{num(item.get('high'))}°/{num(item.get('low'))}°",19,'bold',anchor='ra')
        t(326,yy+4,f"{num(item.get('rain'))}%",15,'bold',GRAY,anchor='ra')
    obs=we.get('observed_at')
    stamp=f'WEERUPDATE {obs:%H:%M}' if isinstance(obs,dt.datetime) else 'WEERBRON OPEN-METEO'
    t(51,545,stamp,11,'regular',GRAY)
    # FOOTBALL: club crest + opponent crest on BOTH fixtures
    x=374
    label(x,133,'GALATASARAY  /  FOOTBALL')
    rule(374,173,656,173)
    last=fb.get('last')
    label(x,188,'LAATSTE WEDSTRIJD')
    if last:
        op=logo_image(last.get('opponent_logo'), 'op'+str(last.get('opponent_id')))
        draw_crest(im,d,377,217,73,90,gs_logo,'GALATASARAY')
        draw_crest(im,d,580,217,73,90,op,last.get('opponent',''))
        t(514,248,last.get('score','—'),36,'heavy',anchor='mm')
        t(514,299,fit(last.get('opponent','—'),17,260,'bold'),17,'bold',anchor='mm')
        at=last.get('date')
        meta=(f'{at.day} {MONTHS[at.month-1]}  ·  ' if isinstance(at,dt.datetime) else '')+last.get('league','')
        t(514,325,fit(meta,12,266),12,'regular',GRAY,anchor='mm')
    else:
        draw_crest(im,d,377,217,73,90,gs_logo,'GALATASARAY')
        t(475,255,'Geen uitslag beschikbaar',16,'bold')
    rule(374,356,656,356)
    label(x,372,'VOLGENDE WEDSTRIJD')
    upcoming=fb.get('next')
    if upcoming:
        op=logo_image(upcoming.get('opponent_logo'), 'op'+str(upcoming.get('opponent_id')))
        draw_crest(im,d,377,395,73,90,gs_logo,'GALATASARAY')
        draw_crest(im,d,580,395,73,90,op,upcoming.get('opponent',''))
        at=upcoming.get('date')
        if isinstance(at,dt.datetime):
            t(514,431,f'{at:%H:%M}',29,'heavy',anchor='mm')
            days=(at-now).total_seconds()/86400
            until=('VANDAAG' if at.date()==now.date() else
                   'MORGEN' if at.date()==(now+dt.timedelta(days=1)).date() else f'OVER {max(0,math.ceil(days))} DAGEN')
            t(514,494,f'{at.day} {MONTHS[at.month-1]}  ·  '+until,15,'bold',INK,anchor='mm')
        else:t(514,431,'TIJD ONBEKEND',15,'bold',anchor='mm')
        t(514,473,fit(upcoming.get('opponent','—'),18,266,'bold'),18,'bold',anchor='mm')
        t(514,534,fit((upcoming.get('league','') + (' · FIKSTUR-CACHE' if upcoming.get('source','').startswith('verified_') else '')),12,266),12,'regular',GRAY,anchor='mm')
    else:
        draw_crest(im,d,377,395,73,90,gs_logo,'GALATASARAY')
        t(475,435,'Nog geen fixture',16,'bold')
        t(475,455,'Bron niet beschikbaar',12,'regular',GRAY)
    # WEEKEND: replace market panel with five real football fixtures.
    # Weekdays retain the existing market layout unchanged.
    if now.weekday() in (5,6):
        label(704,133,'FOOTBALL WEEKEND  /  5 MATCHES')
        games=(data.get('weekend') or {}).get('matches') or []
        if not games:
            t(704,194,'Geen bevestigde',20,'bold')
            t(704,224,'wedstrijden beschikbaar',17,'regular',GRAY)
            t(704,267,'ESPN-data tijdelijk',14,'regular',GRAY)
            t(704,290,'niet beschikbaar',14,'regular',GRAY)
        for i,game in enumerate(games[:5]):
            yy=171+i*77
            if i:rule(704,yy-12,973,yy-12)
            names=game['home']+' - '+game['away']
            t(704,yy,fit(names,17,268,'bold'),17,'bold')
            stamp=game['date'].strftime('%a %H:%M').upper()
            result=game.get('score')
            t(704,yy+35,game['league'],11,'regular',GRAY)
            state_label=game.get('state_label')
            match_label=(f'{state_label}  {result}' if state_label and result else
                         result or stamp)
            t(973,yy+34,match_label,13,'bold',INK,anchor='ra')
        t(704,541,f"ESPN  ·  CHECK {now:%H:%M} NL  ·  ±30 MIN",10,'regular',GRAY)
    else:
        # MARKETS
        x=704
        label(x,133,'MARKTEN  /  USD')
        tx=704
        for i,(ticker,name) in enumerate([('NVDA','NVIDIA'),('PLTR','PALANTIR'),('AAPL','APPLE')]):
            yy=171+i*80
            if i>0:rule(704,yy-9,973,yy-9)
            quote=stocks.get(ticker,{})
            t(tx,yy,ticker,24,'heavy')
            t(tx,yy+31,name,13,'bold',GRAY)
            p=quote.get('price');delta=quote.get('change')
            t(972,yy+2,f'${p:,.2f}' if isinstance(p,(int,float)) else '—',21,'bold',INK,anchor='ra')
            t(847,yy+33,signed(delta),15,'bold',GRAY,anchor='ra')
            spark(quote.get('points') or [],868,yy+34,105,25)
        rule(704,403,973,403,INK,2)
        label(704,419,'GOUD  /  EUR PER KG')
        t(704,450,euro(gd.get('price')),32,'heavy')
        t(704,497,signed(gd.get('change'))+'  ·  SPOT',15,'bold',GRAY)
        t(704,537,'Gold price data by Corgano',11,'regular',GRAY)
    # WEEKEND LOWER BAR: birthday / Super Lig Top 3 / next DBS O16-1.
    panel(33,587,991,703)
    if now.weekday() in (5,6):
        label(52,598,'WEEKEND  /  VERJAARDAG · SÜPER LIG TOP 3 · DBS O16-1')
        for sep in (356,668):rule(sep,633,sep,688)

        # Two E Ink-friendly balloons: one dark, one white outlined.
        def birthday_balloons(x,y):
            d.ellipse((x,y,x+18,y+24),fill=INK,outline=INK,width=2)
            d.ellipse((x+24,y-1,x+43,y+24),fill=WHITE,outline=INK,width=2)
            d.polygon([(x+7,y+23),(x+11,y+27),(x+15,y+23)],fill=INK)
            d.polygon([(x+31,y+23),(x+34,y+27),(x+37,y+23)],outline=INK)
            d.line([(x+11,y+27),(x+10,y+36),(x+15,y+42)],fill=INK,width=1)
            d.line([(x+34,y+27),(x+35,y+35),(x+29,y+42)],fill=INK,width=1)
        birthday=birthday_notice(now)
        birthday_balloons(52,637)
        t(107,635,'FILIZ  ·  12 NOVEMBER',16,'bold')
        t(107,666,'VANDAAG!' if birthday['days']==0 else f"OVER {birthday['days']} DAGEN",16,'heavy')

        teams=(data.get('standings') or {}).get('teams') or []
        t(373,634,'TOP 3',12,'bold',GRAY)
        if teams:
            for i,team in enumerate(teams[:3]):
                yy=651+i*16
                name=fit(team['name'],12,193,'bold')
                t(374,yy,f"{i+1}. {name}",12,'bold' if 'galatasaray' in team['name'].casefold() else 'regular')
                t(651,yy,str(team['points'])+' PT',12,'bold',INK,anchor='ra')
        else:
            t(374,660,'Stand niet beschikbaar',14,'bold',GRAY)

        entry=(data.get('dbs') or {}).get('fixture')
        if entry:
            # Actual club crests when available; otherwise labelled monograms.
            home,away=entry['home'],entry['away']
            crest1=dbs_club_logo(home)
            crest2=dbs_club_logo(away)
            draw_crest(im,d,684,640,43,47,crest1,home)
            draw_crest(im,d,947,640,36,47,crest2,away)
            at=entry['date']
            t(836,639, f"{DAYS[at.weekday()][:2].upper()} {at.day:02d} {MONTHS[at.month-1].upper()} · {at:%H:%M}",15,'heavy',INK,anchor='mt')
            desc=fit(entry['opponent'].upper(),13,198,'bold')
            t(836,662,desc,13,'bold',INK,anchor='mt')
            t(836,681,'THUIS' if entry['home_game'] else 'UIT',11,'bold',GRAY,anchor='mt')
        else:
            t(685,645,'DBS O16-1',16,'bold')
            t(685,670,'Geen bevestigde match',13,'regular',GRAY)
    else:
        # COMMUTE LOWER BAR: departure Meerhoven, Utrecht University as center column
        panel(33,587,991,703)
        label(52,598,'ROUTES  /  '+('NAAR MEERHOVEN' if route_period(now)=='terug' else 'VANAF MEERHOVEN')+'  ·  Google Maps')
        for sep in (356,668):rule(sep,633,sep,688)
        for i,(name,title) in enumerate([('breda','BREDA'),('utrecht','UTRECHT · UU'),('maastricht','MAASTRICHT')]):
            left=(52,373,684)[i]
            right=(337,649,974)[i]
            t(left,638,title,18 if i!=1 else 17,'bold',GRAY)
            minutes=routes.get(name)
            t(right,630,f'{minutes} MIN' if minutes is not None else '— MIN',30,'heavy',INK,anchor='ra')
            sub='AUTO + VERKEER' if minutes is not None else 'NOG NIET INGESTELD'
            if name=='utrecht':sub='UU SCIENCE PARK' if minutes is not None else 'UU SCIENCE PARK'
            t(left,676,sub,12,'bold',GRAY)
        # Birthday notice is added only during the 7-day countdown.
        birthday=birthday_notice(now)
        if birthday['show']:
            # Small header text; preserve all three existing route metrics.
            t(974,600,'FILIZ 12 NOV · '+('VANDAAG' if birthday['days']==0 else f"OVER {birthday['days']} DAGEN"),11,'bold',INK,anchor='ra')
    # footer
    rule(33,718,991,718,INK,2)
    t(34,730,'DAYMARK V3.8.2  •  PAPERWHITE 2  •  LANDSCAPE',12,'bold',GRAY)
    foot='DEMO • NIET LIVE' if data['demo'] else 'DATA KAN VERTRAAGD ZIJN • ONTBREKEND = —'
    t(991,731,foot,12,'bold',GRAY,anchor='ra')
    return im


def save_images(image):
    preview=BASE/'daymark_v2_preview.png'
    right=BASE/'daymark_v2_kindle_right.png'
    left=BASE/'daymark_v2_kindle_left.png'
    image.save(preview,optimize=True)
    # Counter-clockwise rotate content, user then turns the physical Kindle clockwise.
    image.rotate(90,expand=True).save(right,optimize=True)
    image.rotate(-90,expand=True).save(left,optimize=True)
    assert Image.open(preview).size==(1024,758)
    assert Image.open(right).size==(758,1024)
    return preview,right,left


def install(kindle,preview,right,left):
    k=pathlib.Path(kindle)
    if not (k/'documents').is_dir():
        raise SystemExit('Kindle niet gemount of /documents ontbreekt. Sluit USB aan en probeer opnieuw.')
    # Never modify jailbreak or system files; only files owned by DAYMARK.
    shutil.copy2(right,k/'daymark_landscape.png')
    shutil.copy2(left,k/'daymark_landscape_left.png')
    print('Kindle-afbeelding bijgewerkt: '+str(k/'daymark_landscape.png'))
    print('Opstartmethode NIET gewijzigd. Gebruik uitsluitend de reeds geteste DAYMARK v2.1 75-sec launcher zolang permanente werking niet is beveiligd.')
    print('Draai Kindle 90 graden met de klok mee. Bij een omgekeerd beeld: gebruik daymark_landscape_left.png.')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--demo',action='store_true',help='voorbeeldcijfers met duidelijk DEMO-label')
    p.add_argument('--install',action='store_true',help='kopieer landscape-afbeelding en launcher naar Kindle via USB')
    p.add_argument('--kindle',default='/Volumes/Kindle')
    p.add_argument('--output-dir',help='map voor de publieke Kindle-PNG (GitHub Pages)')
    args=p.parse_args()
    cfg=get_config()
    data=demo_data() if args.demo else live_data(cfg)
    img=render(data,cfg)
    preview,right,left=save_images(img)
    errs=BASE/'last_errors.log'
    errs.write_text('\n'.join(data.get('errors',[]))+'\n',encoding='utf-8')
    print(f'Gemaakt: {preview} (1024x758), Kindle-image: {right} (758x1024)')
    if data['errors']:print('Sommige live bronnen ontbreken. Zie last_errors.log; er zijn geen dummywaarden gebruikt.')
    if args.output_dir:
        dest=pathlib.Path(args.output_dir)
        dest.mkdir(parents=True,exist_ok=True)
        shutil.copy2(right,dest/'daymark.png')
        if (BASE/'ATTRIBUTION.md').exists():
            shutil.copy2(BASE/'ATTRIBUTION.md',dest/'ATTRIBUTION.md')
        status={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                'mode':'demo' if args.demo else 'live',
                'sources_missing':data.get('errors',[]),
                'routes_configured':all(data['routes'].get(n) is not None for n in ('breda','utrecht','maastricht')),
                'route_origin':'Meerhoven (Meerrijk) — openbaar wijkmiddelpunt' if route_period(data['now'])=='heen' else 'Breda / Utrecht UU / Maastricht',
                'route_direction':route_period(data['now']),
                'route_direction_label':'VANAF MEERHOVEN' if route_period(data['now'])=='heen' else 'NAAR MEERHOVEN',
                'route_next_switch_nl':route_next_switch(data['now']).isoformat(),
                'route_provider':'Google Maps Routes API',
                'route_destination_type':'openbare locaties (Breda/Maastricht centrum; UU Science Park)',
                'route_key_configured':bool(os.environ.get('GOOGLE_MAPS_API_KEY')), 
                'route_destinations':['Breda centrum','Universiteit Utrecht - Heidelberglaan 8','Maastricht centrum'] if route_period(data['now'])=='heen' else ['Meerhoven (vanaf Breda)','Meerhoven (vanaf Universiteit Utrecht)','Meerhoven (vanaf Maastricht)'],
                'weather_temperature':data.get('weather',{}).get('temp'),
                'weather_description':data.get('weather',{}).get('desc'),
                'weather_is_day':data.get('weather',{}).get('is_day'),
                'weather_observed_at_nl':(data.get('weather',{}).get('observed_at') or '').isoformat() if isinstance(data.get('weather',{}).get('observed_at'),dt.datetime) else None,
                'weather_forecast_days':len(data.get('weather',{}).get('forecast') or []),
                'route_minutes':data.get('routes',{}),
                'football_last_available':bool(data.get('football',{}).get('last')),
                'football_next_available':bool(data.get('football',{}).get('next')),
                'football_events_checked':data.get('football',{}).get('events_checked',0),
                'football_next_source':(data.get('football',{}).get('next') or {}).get('source'),
                'football_next_opponent':(data.get('football',{}).get('next') or {}).get('opponent'),
                'football_next_kickoff_nl':(data.get('football',{}).get('next') or {}).get('date').isoformat() if (data.get('football',{}).get('next') or {}).get('date') else None,
                'football_feed_sources_ok':data.get('football',{}).get('sources_ok',0),
                'football_feed_errors':data.get('football',{}).get('feed_errors',[]),
                'weekend_matches':len((data.get('weekend') or {}).get('matches') or []),
                'weekend_sources_ok':(data.get('weekend') or {}).get('sources_ok',0),
                'weekend_errors':(data.get('weekend') or {}).get('errors',[]),
                'weekend_queried_months':(data.get('weekend') or {}).get('months',[]),
                'weekend_selected_matches':[{'id':m['id'],'home':m['home'],
                    'away':m['away'],'state':m['state'],'score':m['score']}
                    for m in (data.get('weekend') or {}).get('matches',[])],
                'standings_available':(data.get('standings') or {}).get('available',False),
                'standings_error':(data.get('standings') or {}).get('error'),
                'birthday_days':birthday_notice(data['now'])['days'],
                'dbs_next_kickoff_nl':(data.get('dbs',{}).get('fixture') or {}).get('date').isoformat() if (data.get('dbs',{}).get('fixture') or {}).get('date') else None,
                'dbs_next_opponent':(data.get('dbs',{}).get('fixture') or {}).get('opponent'),
                'dbs_schedule_source':data.get('dbs',{}).get('source')}
        (dest/'status.json').write_text(json.dumps(status,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print('Web feed:',dest/'daymark.png')
    if args.install:install(args.kindle,preview,right,left)

if __name__=='__main__':main()
