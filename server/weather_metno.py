"""MET Norway (Locationforecast 2.0) forecast for DAYMARK.

No extra pip dependencies. Uses public GitHub Pages for a persistent, public-only
cache because GitHub Actions runners are ephemeral. Does not invent weather data.

For non-Nordic locations, follow MET Norway's >=3-hour interval for identical
requests, respect Expires, and use conditional requests after that time.
Credit: Weather forecast from the Norwegian Meteorological Institute (MET Norway).
"""
from __future__ import annotations
import datetime as dt
import email.utils
import gzip
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Europe/Amsterdam')
UTC = dt.timezone.utc
VERSION = 2
CACHE_URL = 'https://galaseckin.github.io/daymark-live/weather_cache.json'
ENDPOINT = 'https://api.met.no/weatherapi/locationforecast/2.0/compact'
UA = 'DAYMARK/3.9.3 (https://github.com/galaseckin/daymark-live)'
MIN_POLL = dt.timedelta(hours=3)  # MET Norway non-Nordic locationforecast rule
MAX_STALE = dt.timedelta(hours=12)


def _aware(value):
    value = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timestamp zonder timezone')
    return value.astimezone(UTC)


def _num(value, low=-100, high=100):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and low <= value <= high else None


def _symbol_to_wmo(symbol):
    name = (symbol or '').lower()
    if 'thunder' in name:
        return 95
    if 'snow' in name:
        return 85 if 'showers' in name else 71
    if 'sleet' in name:
        return 85 if 'showers' in name else 67
    if 'fog' in name:
        return 45
    if 'rain' in name:
        if 'showers' in name:
            return 82 if 'heavy' in name else 80
        return 65 if 'heavy' in name else (61 if 'light' in name else 63)
    if 'cloudy' in name:
        return 2 if 'partly' in name else 3
    if 'fair' in name:
        return 1
    if 'clearsky' in name:
        return 0
    return None


def _describe(code, is_day=True):
    if code == 0: return 'Zonnig' if is_day else 'Helder'
    if code in (1, 2): return 'Licht bewolkt'
    if code == 3: return 'Bewolkt'
    if code == 45: return 'Mist'
    if code in (61, 63, 65, 67, 80, 82): return 'Regen'
    if code in (71, 85): return 'Sneeuw'
    if code == 95: return 'Onweer'
    return 'Wisselvallig'


def _period(item):
    data = item.get('data') or {}
    for key in ('next_1_hours', 'next_6_hours', 'next_12_hours'):
        val = data.get(key)
        if val:
            return val
    return {}


def _summarize(items, now):
    """Recompute today and next 3 days from raw saved forecast on EVERY run.

    This matters across midnight: a cached forecast must not show yesterday.
    The values are a model prediction, not a live weather-station reading.
    """
    rows = []
    for item in items:
        if not isinstance(item, dict): continue
        try:
            t = _aware(item['time']).astimezone(TZ)
            d = item['data']
            temp = _num(d['instant']['details']['air_temperature'], -80, 65)
            if temp is None: continue
            per = _period(item)
            sym = (per.get('summary') or {}).get('symbol_code') or ''
            pop = _num((per.get('details') or {}).get('probability_of_precipitation'), 0, 100)
            ws = _num(d['instant']['details'].get('wind_speed'), 0, 150)
            rows.append((t, temp, sym, pop, ws))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    if not rows: raise ValueError('MET Norway heeft geen bruikbare temperatuurgegevens')
    rows.sort(key=lambda row: row[0])
    # Current forecast should be reasonably near now, not a distant future sample.
    nearest = min(rows, key=lambda row: abs((row[0] - now).total_seconds()))
    if abs((nearest[0] - now).total_seconds()) > 3 * 3600:
        raise ValueError('MET Norway voorspelling dekt het huidige tijdstip niet')
    t, temp, sym, _, wind = nearest
    is_day = sym.endswith('_day') if (sym.endswith('_day') or sym.endswith('_night')) else 7 <= t.hour < 19
    current_code = _symbol_to_wmo(sym)
    # For unknown symbols, keep code=None so DAYMARK does not invent cloudiness.
    daily = {}
    for when, value, symbol, probability, _ in rows:
        day = when.date()
        if now.date() <= day <= now.date() + dt.timedelta(days=3):
            daily.setdefault(day, []).append((when, value, symbol, probability))
    if any(now.date() + dt.timedelta(days=i) not in daily for i in range(4)):
        raise ValueError('MET Norway mist een of meer van de komende vier dagen')

    def one_day(date):
        vals = daily[date]
        # Symbol from near midday; robust for 6-hour forecast steps.
        midday = dt.datetime.combine(date, dt.time(12), TZ)
        symbol = min(vals, key=lambda x: abs((x[0] - midday).total_seconds()))[2]
        code = _symbol_to_wmo(symbol)
        probs = [v[3] for v in vals if v[3] is not None]
        return {'date': date, 'code': code, 'desc': _describe(code),
                'high': max(v[1] for v in vals), 'low': min(v[1] for v in vals),
                'rain': max(probs) if probs else None}
    today = one_day(now.date())
    result = {'temp': temp, 'feels': None, 'desc': _describe(current_code, is_day),
              'code': current_code, 'is_day': is_day,
              'observed_at': t,  # forecast VALID TIME, not a measurement time
              'high': today['high'], 'low': today['low'], 'rain': today['rain'],
              'wind': round(wind * 3.6, 1) if wind is not None else None,
              'forecast': [one_day(now.date() + dt.timedelta(days=i)) for i in (1, 2, 3)],
              'source': 'met-norway'}
    return result


def _valid_cache(record, cfg, now):
    if not isinstance(record, dict) or record.get('version') != VERSION or record.get('source') != 'met-norway':
        return False
    coord = [round(float(cfg['weather_lat']), 4), round(float(cfg['weather_lon']), 4)]
    if record.get('coordinates') != coord or not isinstance(record.get('timeseries'), list):
        return False
    try:
        age = now.astimezone(UTC) - _aware(record['fetched_at_utc'])
        return dt.timedelta(0) <= age <= MAX_STALE and len(record['timeseries']) >= 10
    except (KeyError, ValueError, TypeError):
        return False


def _read_url(req, timeout=12):
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000: raise ValueError('MET Norway antwoord te groot')
        if response.headers.get('Content-Encoding', '').lower() == 'gzip':
            raw = gzip.decompress(raw)
        data = json.loads(raw)
        return data, response.headers


def get_published_cache(now):
    """Fetch last published public cache from GitHub Pages, if present."""
    url = CACHE_URL + '?v=' + str(int(now.timestamp()) // 60)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Cache-Control':'no-cache',
                                               'Accept':'application/json'})
    try:
        result, _ = _read_url(req, timeout=8)
        return result if isinstance(result, dict) and result.get('version') == VERSION else None
    except (OSError, ValueError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def _next_refresh(now, headers):
    interval = now + MIN_POLL
    exp = headers.get('Expires')
    if exp:
        try:
            parsed = email.utils.parsedate_to_datetime(exp).astimezone(UTC)
            if parsed > interval: interval = parsed
        except (TypeError, ValueError, OverflowError):
            pass
    return interval


def _fetch(cfg, now, prior):
    # MET requires <=4 decimals for cache sharing.
    coords = [round(float(cfg['weather_lat']), 4), round(float(cfg['weather_lon']), 4)]
    url = ENDPOINT + '?' + urllib.parse.urlencode({'lat':coords[0], 'lon':coords[1]})
    headers={'User-Agent': UA, 'Accept': 'application/json', 'Accept-Encoding':'gzip'}
    if prior and prior.get('last_modified'):
        headers['If-Modified-Since'] = prior['last_modified']
    req = urllib.request.Request(url, headers=headers)
    try:
        payload, response_headers = _read_url(req, timeout=14)
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and prior:
            return {**prior, 'fetched_at_utc':now.astimezone(UTC).isoformat(),
                    'next_refresh_at_utc':_next_refresh(now.astimezone(UTC), exc.headers).isoformat()}
        raise
    properties = (payload or {}).get('properties') or {}
    updated = (properties.get('meta') or {}).get('updated_at')
    if updated:
        provider_age = now.astimezone(UTC) - _aware(updated)
        if provider_age > dt.timedelta(hours=24) or provider_age < dt.timedelta(hours=-1):
            raise ValueError('MET Norway weermodel is niet actueel')
    items = properties.get('timeseries')
    if not isinstance(items, list) or len(items) < 10:
        raise ValueError('MET Norway gaf geen complete tijdreeks terug')
    # Verify before caching. If malformed, the last known-good snapshot is kept.
    _summarize(items, now)
    return {'version':VERSION, 'source':'met-norway', 'coordinates':coords,
            'fetched_at_utc':now.astimezone(UTC).isoformat(),
            'next_refresh_at_utc':_next_refresh(now.astimezone(UTC), response_headers).isoformat(),
            'last_modified':response_headers.get('Last-Modified'), 'timeseries':items}


def resolve(cfg, now, prior=None):
    """Return (weather, cache_record, optional_error).

    Do not contact MET again before provider's Expires or 3h minimum.
    When the provider is down, use at most 12h old model data, prominently
    marked stale. Never silently fabricate a forecast.
    """
    now = now.astimezone(TZ)
    valid = _valid_cache(prior, cfg, now)
    previous = prior if valid else None
    if valid:
        try:
            if now.astimezone(UTC) < _aware(prior['next_refresh_at_utc']):
                result = _summarize(prior['timeseries'], now)
                result['cache_age_minutes'] = round((now.astimezone(UTC)-_aware(prior['fetched_at_utc'])).total_seconds()/60)
                return result, prior, None
        except (KeyError, ValueError, TypeError):
            pass
    try:
        record = _fetch(cfg, now, previous)
        result = _summarize(record['timeseries'], now)
        result['cache_age_minutes'] = 0
        return result, record, None
    except (OSError, ValueError, KeyError, TypeError, urllib.error.HTTPError) as error:
        if previous:
            try:
                result = _summarize(previous['timeseries'], now)
                result['source'] = 'cached'
                result['cache_age_minutes'] = round((now.astimezone(UTC)-_aware(previous['fetched_at_utc'])).total_seconds()/60)
                return result, previous, f'MET Norway tijdelijk onbereikbaar: {type(error).__name__}: {error}'
            except (ValueError, KeyError, TypeError):
                pass
        return {}, previous, f'MET Norway onbereikbaar en geen geldige cache: {type(error).__name__}: {error}'
