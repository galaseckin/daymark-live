"""DAYMARK AIRSPACE 2.1: named cities, route integrity + two rotating flight cards.

Drop-in replacement for server/airspace.py; weather, skyline, football and Kindle
worker are not modified. Data sources:
  - adsb.fi: near-aircraft positions, type codes, community military flag
    (personal, non-commercial; attribution must remain visible).
  - OpenSky: fallback if adsb.fi is unavailable.
  - adsb.lol: optional crowd-sourced *route indication* for featured flights;
    never displayed as verified flight itinerary.

Only public Meerhoven *neighborhood* coordinates are transmitted. Flight data is
not used to make assertions of actual passengers aboard an aircraft.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import urllib.parse
import urllib.request

CENTER_LAT = 51.44148   # neighborhood centre, NOT an exact residential address
CENTER_LON = 5.40563
RADIUS_KM = 30.0
RADIUS_NM = 17              # provider's radius is in nautical miles (~31.5 km)

# Airline prefixes are a *heuristic* for regularly operated airline flights,
# NOT evidence that passengers are aboard a particular aircraft.
PASSENGER_OPERATORS = frozenset(
    'RYR KLM TRA TFL TUI EZY EJU EWG DLH BAW BEL AUA SAS THY WZZ WUK '
    'VLG SWR TVF UAE QTR NAX FIN LOT TAP IBE ITY AFR DAL UAL AAL EIN '
    'PGT CFG TSC NEO EXS VIR KAL AEE AIC SIA CPA ETH RAM AEA'.split()
)
CARGO_OPERATORS = frozenset(
    'FDX UPS BCS DHK CLX GTI ABX BOX CKS SRR MPH TAY NPT CWC ASL'.split()
)


# Fallback only: use the airport cities supplied by the route provider first.
# This compact, locally curated map avoids any extra network requests or package
# requirements; unknown codes are not invented as airport names.
CITY_FALLBACK = {
    'EIN':'Eindhoven','AMS':'Amsterdam','RTM':'Rotterdam','MST':'Maastricht',
    'GRQ':'Groningen','BRU':'Brussel','CRL':'Charleroi','ANR':'Antwerpen',
    'DUS':'Düsseldorf','CGN':'Keulen','FRA':'Frankfurt','MUC':'München',
    'HAM':'Hamburg','BER':'Berlijn','STR':'Stuttgart','NRN':'Weeze',
    'LHR':'Londen Heathrow','LGW':'Londen Gatwick','STN':'Londen Stansted',
    'LTN':'Londen Luton','LCY':'Londen City','MAN':'Manchester',
    'EDI':'Edinburgh','DUB':'Dublin','ORK':'Cork','CDG':'Parijs',
    'ORY':'Parijs','BVA':'Beauvais','NCE':'Nice','LYS':'Lyon',
    'MRS':'Marseille','TLS':'Toulouse','BCN':'Barcelona','MAD':'Madrid',
    'ALC':'Alicante','AGP':'Málaga','PMI':'Palma de Mallorca',
    'IBZ':'Ibiza','VLC':'Valencia','SVQ':'Sevilla','GRO':'Girona',
    'REU':'Reus','LPA':'Gran Canaria','TFS':'Tenerife Zuid',
    'TFN':'Tenerife Noord','ACE':'Lanzarote','FUE':'Fuerteventura',
    'LIS':'Lissabon','OPO':'Porto','FAO':'Faro','FCO':'Rome',
    'CIA':'Rome Ciampino','MXP':'Milaan','LIN':'Milaan Linate',
    'BGY':'Bergamo','VCE':'Venetië','TSF':'Treviso','NAP':'Napels',
    'BLQ':'Bologna','FLR':'Florence','CTA':'Catania','PMO':'Palermo',
    'ATH':'Athene','HER':'Heraklion','CHQ':'Chania','RHO':'Rhodos',
    'CFU':'Corfu','SKG':'Thessaloniki','AYT':'Antalya',
    'IST':'Istanbul','SAW':'Istanbul Sabiha Gökçen','ESB':'Ankara',
    'ADB':'Izmir','DLM':'Dalaman','BJV':'Bodrum','BUD':'Boedapest',
    'PRG':'Praag','VIE':'Wenen','WAW':'Warschau','KRK':'Krakau',
    'GDN':'Gdańsk','CPH':'Kopenhagen','OSL':'Oslo',
    'ARN':'Stockholm','HEL':'Helsinki','ZRH':'Zürich','GVA':'Genève',
    'BSL':'Basel','BEG':'Belgrado','ZAG':'Zagreb','SPU':'Split',
    'DBV':'Dubrovnik','TIA':'Tirana','OTP':'Boekarest','SOF':'Sofia',
    'RIX':'Riga','VNO':'Vilnius','KEF':'Keflavík',
    'RAK':'Marrakesh','CMN':'Casablanca','TUN':'Tunis','DXB':'Dubai',
    'DOH':'Doha','AUH':'Abu Dhabi','JFK':'New York',
    'EWR':'Newark','LAX':'Los Angeles','ORD':'Chicago',
    'HND':'Tokio','NRT':'Tokio Narita','AKL':'Auckland',
}

# Friendly type names for the most common types; fallback keeps raw ICAO type.
TYPE_LABELS = {
    'B738':'Boeing 737-800','B737':'Boeing 737-700',
    'B38M':'Boeing 737 MAX 8','B39M':'Boeing 737 MAX 9',
    'B739':'Boeing 737-900','B752':'Boeing 757-200',
    'B763':'Boeing 767-300','B77W':'Boeing 777-300ER',
    'B788':'Boeing 787-8','B789':'Boeing 787-9',
    'A319':'Airbus A319','A320':'Airbus A320',
    'A321':'Airbus A321','A20N':'Airbus A320neo',
    'A21N':'Airbus A321neo','A333':'Airbus A330-300',
    'A359':'Airbus A350-900','E190':'Embraer E190',
    'E195':'Embraer E195','E290':'Embraer E190-E2',
    'AT76':'ATR 72','DH8D':'Dash 8 Q400',
    'C130':'C-130 Hercules','A400':'Airbus A400M',
    'C17':'Boeing C-17','F16':'F-16',
}


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def haversine_km(lat1, lon1, lat2, lon2):
    a1, a2 = math.radians(lat1), math.radians(lat2)
    da, dl = a2-a1, math.radians(lon2-lon1)
    h = math.sin(da/2)**2 + math.cos(a1)*math.cos(a2)*math.sin(dl/2)**2
    return 12742.0176 * math.asin(min(1.0, math.sqrt(max(0.0,h))))


def _get_json(url, *, timeout=7):
    req = urllib.request.Request(url, headers={
        'Accept':'application/json', 'User-Agent':'DAYMARK-Airspace/2.1 personal e-ink dashboard'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(2_500_001)
    if len(raw)>2_500_000: raise ValueError('Antwoord te groot')
    return json.loads(raw)


def _post_json(url, payload, *, timeout=5):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
        headers={'Accept':'application/json', 'Content-Type':'application/json',
                 'User-Agent':'DAYMARK-Airspace/2.1 personal e-ink dashboard'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(250_001)
    if len(raw)>250_000: raise ValueError('Routeantwoord te groot')
    return json.loads(raw)


def _sanitize_token(value, maxlen=12):
    return re.sub(r'[^A-Z0-9-]', '', str(value or '').upper().strip())[:maxlen]


def _classify(callsign, typ, *, military=False):
    """Do not claim the aircraft is transporting passengers unless that is known."""
    if military: return 'MILITAIR'
    prefix = _sanitize_token(callsign)[:3]
    if prefix in CARGO_OPERATORS: return 'CARGO'
    # Known airline prefix; label carefully as an *airline flight*, not a
    # verified passenger manifest or guaranteed passenger-carrying operation.
    if prefix in PASSENGER_OPERATORS:
        return 'LIJNVLUCHT'
    return 'OVERIG'


def _item(icao, callsign, typ, lat, lon, alt_ft, speed_kt, track, category, age):
    return {'icao24':_sanitize_token(icao,8).lower(),
            'callsign':_sanitize_token(callsign,12) or _sanitize_token(icao,8) or 'ONBEKEND',
            'type':_sanitize_token(typ,9), 'route':'', 'route_kind':'',
            'lat':lat, 'lon':lon, 'distance_km':round(haversine_km(CENTER_LAT,CENTER_LON,lat,lon),1),
            'alt_ft':round(alt_ft) if alt_ft is not None else None,
            'speed_kt':round(speed_kt) if speed_kt is not None else None,
            'track':(track%360) if track is not None else None,
            'category':category, 'position_age_s':round(max(0,age))}


def _stamp(timestamp, now):
    sec = _finite(timestamp)
    if sec is None: return now
    if sec>1e11: sec/=1000  # ADS-B Exchange-style now is milliseconds
    if abs(now.timestamp()-sec)>300: raise ValueError('Verouderde ADS-B snapshot')
    return dt.datetime.fromtimestamp(sec, dt.timezone.utc)


def parse_adsbfi(payload, *, now_utc=None):
    now_utc=now_utc or dt.datetime.now(dt.timezone.utc)
    if not isinstance(payload,dict) or not isinstance(payload.get('ac'),list):
        raise ValueError('ADSB.fi antwoord zonder ac-lijst')
    when=_stamp(payload.get('now'),now_utc)
    flights=[]
    for row in payload['ac']:
        if not isinstance(row,dict): continue
        lat=_finite(row.get('lat')); lon=_finite(row.get('lon'))
        if lat is None or lon is None or not (-90<=lat<=90 and -180<=lon<=180): continue
        if haversine_km(CENTER_LAT,CENTER_LON,lat,lon)>RADIUS_KM: continue
        alt_raw=row.get('alt_baro')
        if alt_raw=='ground' or alt_raw=='GROUND': continue
        age=_finite(row.get('seen_pos'))
        if age is None: age=_finite(row.get('seen'))
        if age is None or age>180 or age<0: continue
        # The ADS-B dbFlags military bit is database classification, not an
        # inference based on callsign/nationality.
        try: flags=int(row.get('dbFlags') or 0)
        except (TypeError,ValueError): flags=0
        typ=_sanitize_token(row.get('t'),9)
        callsign=_sanitize_token(row.get('flight'),12)
        category=_classify(callsign,typ,military=bool(flags&1))
        altitude=_finite(alt_raw)
        if altitude is None: altitude=_finite(row.get('alt_geom'))
        flight=_item(row.get('hex'),callsign,typ,lat,lon,altitude,
                     _finite(row.get('gs')),_finite(row.get('track')),category,age)
        flights.append(flight)
    flights.sort(key=lambda a:(a['distance_km'],a['callsign']))
    return {'status':'ok','source':'adsb.fi','flights':flights,'count':len(flights),
            'observed_at_utc':when,'error':None}


def parse_snapshot(payload, *, now_utc=None, radius_km=RADIUS_KM):
    """OpenSky fallback; cannot reliably identify flight category/type/routes."""
    now_utc=now_utc or dt.datetime.now(dt.timezone.utc)
    if not isinstance(payload,dict) or not isinstance(payload.get('states'),list):
        raise ValueError('OpenSky antwoord zonder states')
    when=_stamp(payload.get('time'),now_utc)
    result=[]
    for row in payload['states']:
        if not isinstance(row,list) or len(row)<11: continue
        ts=_finite(row[3]); lon=_finite(row[5]); lat=_finite(row[6])
        if ts is None or lat is None or lon is None: continue
        if abs(now_utc.timestamp()-ts)>180: continue
        if not (-90<=lat<=90 and -180<=lon<=180) or row[8] is True: continue
        if haversine_km(CENTER_LAT,CENTER_LON,lat,lon)>radius_km: continue
        alt=_finite(row[7]); speed=_finite(row[9])
        result.append(_item(row[0],row[1],'',lat,lon,alt*3.28084 if alt is not None else None,
                            speed*1.94384 if speed is not None else None,
                            _finite(row[10]),'OVERIG',max(0,now_utc.timestamp()-ts)))
    result.sort(key=lambda a:(a['distance_km'],a['callsign']))
    return {'status':'ok','source':'OpenSky fallback','flights':result,
            'count':len(result),'observed_at_utc':when,'error':None}


def _rotation_slot(now_utc):
    """Github run number changes per run. Local fallback changes each 30 min."""
    run = os.environ.get('GITHUB_RUN_NUMBER','')
    if run.isdigit(): return int(run)
    return int(now_utc.timestamp()//1800)


def pick_featured(flights, slot):
    """Prefer a changing commercial-airline flight and an unusual second plane.

    When there are multiple suitable airline flights, the selected index changes
    with the monotonically increasing GitHub run number (stable input pool).
    """
    if not flights: return []
    airline=sorted((x for x in flights if x.get('category')=='LIJNVLUCHT'),
                   key=lambda x:(x['callsign'],x.get('icao24','')))
    if airline: first=airline[slot%len(airline)]
    else:
        civil=sorted((x for x in flights if x.get('category')!='MILITAIR'),
                     key=lambda x:(x.get('distance_km',999),x['callsign']))
        first=(civil or flights)[slot%len(civil or flights)]
    remaining=[a for a in flights if a is not first]
    unusual=[a for a in remaining if a.get('category') in ('MILITAIR','CARGO')]
    if unusual:
        unusual.sort(key=lambda a: (0 if a['category']=='MILITAIR' else 1,a.get('distance_km',999)))
        second=unusual[0]
    else:
        # If no special aircraft, show a different airliner if available.
        secondary_airline=sorted((a for a in remaining if a.get('category')=='LIJNVLUCHT'),
                                 key=lambda x:(x['callsign'],x.get('icao24','')))
        other=secondary_airline or sorted(remaining,key=lambda a:a.get('distance_km',999))
        second=other[(slot//max(1,len(airline)))%len(other)] if other else None
    return [first]+([second] if second else [])



def _clean_city(value):
    """Airport-provider municipality/location, not airport code or arbitrary text."""
    if not isinstance(value,str): return ''
    value=' '.join(value.split())
    if not value or len(value)>80: return ''
    if re.search(r'[<>\x00-\x1f]',value): return ''
    if value.upper() in ('UNKNOWN','N/A','NULL','NONE','UNDEFINED'): return ''
    if re.fullmatch(r'[A-Z]{3,4}',value): return ''
    return value


def parse_routeset_details(payload, requested_callsigns):
    """Return one plausible, unambiguous city pair per callsign.

    adsb.lol routeset returns _airport_codes_iata and often two _airports
    records with .location. Those city names were previously discarded.
    A route from a callsign database is ALWAYS an indication, never proof of
    the actual active flight plan. Explicitly implausible/conflicting legs
    are withheld instead of published as a known destination.
    """
    allowed={_sanitize_token(c,12) for c in requested_callsigns}
    entries=payload if isinstance(payload,list) else []
    per_call={}
    for entry in entries:
        if not isinstance(entry,dict): continue
        cs=_sanitize_token(entry.get('callsign'),12)
        if not cs or cs not in allowed: continue
        # Some routeset responses flag paths inconsistent with current position.
        plausible=entry.get('plausible')
        if plausible is False or plausible == 0: continue
        codes=str(entry.get('_airport_codes_iata') or '').strip().upper().split('-')
        if len(codes)!=2 or any(not re.fullmatch(r'[A-Z]{3}',c) for c in codes):
            continue
        if codes[0]==codes[1]: continue
        airports=entry.get('_airports')
        if not isinstance(airports,list) or len(airports)!=2:
            airports=[{},{}]
        names=[]
        for i,code in enumerate(codes):
            ap=airports[i] if isinstance(airports[i],dict) else {}
            # Mismatched airport code means we must not attach a wrong city.
            provided=_sanitize_token(ap.get('iata'),3)
            if provided and provided!=code:
                names=[];break
            city=(_clean_city(ap.get('location')) or
                  _clean_city(ap.get('municipality')) or
                  CITY_FALLBACK.get(code,''))
            if not city: names=[];break
            names.append(city)
        if len(names)!=2: continue
        details={'codes':tuple(codes),'from':names[0],'to':names[1]}
        per_call.setdefault(cs,[]).append(details)
    found={}
    for cs,items in per_call.items():
        pairs={x['codes'] for x in items}
        if len(pairs)!=1: continue  # conflicting routes on repeated callsign
        found[cs]=items[0]
    return found


def parse_routeset(payload, requested_callsigns):
    """Backward-compatible route-code parser for earlier AIRSPACE tests."""
    details=parse_routeset_details(payload,requested_callsigns)
    return {cs:' → '.join(d['codes']) for cs,d in details.items()}


def enrich_featured_routes(featured):
    """One batched route lookup for selected aircraft, optional and bounded."""
    candidates=[]
    for plane in featured:
        plane['route_lookup']='not_requested'
        cs=_sanitize_token(plane.get('callsign'))
        if cs and re.search(r'\d',cs) and plane.get('lat') is not None:
            candidates.append(plane)
    if not candidates: return
    planes=[{'callsign':a['callsign'],'lat':a['lat'],'lng':a['lon']} for a in candidates]
    try:
        response=_post_json('https://api.adsb.lol/api/0/routeset',{'planes':planes},timeout=6)
        routes=parse_routeset_details(response,[a['callsign'] for a in candidates])
    except Exception as exc:
        for a in candidates: a['route_lookup']='api_error'
        print('[DAYMARK AIRSPACE] routebron niet bereikbaar:',type(exc).__name__)
        return
    for a in candidates:
        details=routes.get(a['callsign'])
        if details:
            a['route']=' → '.join(details['codes'])
            a['route_from']=details['from']
            a['route_to']=details['to']
            a['route_kind']='indicatie'
            a['route_lookup']='found'
        else:
            a['route_lookup']='not_found_or_unreliable'
    print(f'[DAYMARK AIRSPACE] route-indicaties: {sum(x["route_lookup"]=="found" for x in candidates)}/{len(candidates)}')


def fetch(now_utc=None):
    now_utc=now_utc or dt.datetime.now(dt.timezone.utc)
    try:
        # Original provider uses nautical miles. Server-side filtering is 30 km.
        url=(f'https://opendata.adsb.fi/api/v3/lat/{CENTER_LAT:.5f}'
             f'/lon/{CENTER_LON:.5f}/dist/{RADIUS_NM}')
        result=parse_adsbfi(_get_json(url),now_utc=now_utc)
    except Exception as primary_error:
        lat_delta=RADIUS_KM/110.574
        lon_delta=RADIUS_KM/(111.320*math.cos(math.radians(CENTER_LAT)))
        query=urllib.parse.urlencode({'lamin':round(CENTER_LAT-lat_delta,5),
                                     'lamax':round(CENTER_LAT+lat_delta,5),
                                     'lomin':round(CENTER_LON-lon_delta,5),
                                     'lomax':round(CENTER_LON+lon_delta,5)})
        try:
            result=parse_snapshot(_get_json('https://opensky-network.org/api/states/all?'+query),now_utc=now_utc)
        except Exception as fallback_error:
            return {'status':'error','source':'adsb.fi / OpenSky','flights':[],
                    'featured':[],'count':None,'observed_at_utc':None,
                    'error':f'{type(primary_error).__name__} / {type(fallback_error).__name__}'}
    slot=_rotation_slot(now_utc)
    result['featured']=pick_featured(result['flights'],slot)
    result['rotation_slot']=slot
    # No third-party route lookup if there are no eligible planes or no route.
    if result['featured']:
        enrich_featured_routes(result['featured'])
    return result


def demo(now):
    """Explicit --demo only; no fabricated values in normal fetch()."""
    now_utc=now.astimezone(dt.timezone.utc)
    fixtures=[
        (51.490,5.489,'RYR1234','B738',7400,232,230,'LIJNVLUCHT','EIN → PMI'),
        (51.371,5.326,'TUI834','B38M',9400,260,160,'LIJNVLUCHT','EIN → ALC'),
        (51.520,5.520,'KLM992','E190',11000,280,290,'LIJNVLUCHT','AMS → BRU'),
        (51.490,5.220,'NAF21','C130',2800,158,95,'MILITAIR',''),
        (51.319,5.399,'BCS6','B752',11500,290,140,'CARGO',''),
    ]
    flights=[]
    for lat,lon,cs,typ,alt,spd,track,cat,route in fixtures:
        a=_item('ab1234',cs,typ,lat,lon,alt,spd,track,cat,8)
        a['route']=route; a['route_kind']='indicatie' if route else ''
        if route:
            parts=[x.strip() for x in route.replace('→','-').split('-')]
            if len(parts)==2:
                a['route_from']=CITY_FALLBACK.get(parts[0],'')
                a['route_to']=CITY_FALLBACK.get(parts[1],'')
        flights.append(a)
    flights.sort(key=lambda a:a['distance_km'])
    slot=_rotation_slot(now_utc)
    return {'status':'demo','source':'DEMO','flights':flights,'featured':pick_featured(flights,slot),
            'count':len(flights),'rotation_slot':slot,'observed_at_utc':now_utc,'error':None}



def _fitted_label(draw,font,txt,size,maxw,minsize=11):
    text=str(txt or '')
    while size>minsize and draw.textbbox((0,0),text,font=font(size,'bold'))[2]>maxw:
        size-=1
    if draw.textbbox((0,0),text,font=font(size,'bold'))[2]<=maxw:
        return text,size
    # If still too wide, elide a proper place name rather than showing codes.
    while text and draw.textbbox((0,0),text+'…',font=font(size,'bold'))[2]>maxw:
        text=text[:-1]
    return text+'…',size


def render_bar(im,d,data,*,font,color_ink=31,color_gray=95,color_border=207,
               pale=246,white=255,local_tz=None):
    """DAYMARK's exact 958×116 strip. No change outside these pixels."""
    def t(x,y,txt,size=12,kind='regular',fill=color_ink,anchor=None):
        d.text((x,y),str(txt),font=font(size,kind),fill=fill,anchor=anchor)
    def fit(txt,size,maxw,minsize=11):
        return _fitted_label(d,font,txt,size,maxw,minsize)

    d.rounded_rectangle((33,587,991,703),radius=15,fill=white,outline=color_border,width=2)
    # Small count + two deliberately wide, readable flight cards.
    d.line((205,600,205,691),fill=color_border,width=1)
    d.line((598,600,598,691),fill=color_border,width=1)
    t(51,598,'AIRSPACE',15,'bold',color_gray)
    state=data.get('status'); count=data.get('count')
    if state=='error':
        t(52,620,'—',38,'heavy')
        t(51,668,'BRON OFFLINE',10,'bold',color_gray)
    else:
        shown=f'{count:02d}' if isinstance(count,int) and 0<=count<100 else str(count if count is not None else '—')
        t(52,617,shown,40,'heavy')
        t(52,669,'VLIEGTUIGEN',11,'bold')
    t(52,686,'BINNEN 30 KM',10,'regular',color_gray)

    featured=data.get('featured')
    if featured is None:
        featured=pick_featured(data.get('flights') or [],_rotation_slot(dt.datetime.now(dt.timezone.utc)))
    for i,(x,right) in enumerate(((223,580),(615,972))):
        maxw=right-x-7
        if state=='error':
            if i==0:
                t(x,615,'GEEN ACTUELE DATA',17,'heavy')
                t(x,646,'Vliegtuigbron niet bereikbaar',13,'regular',color_gray)
            else:
                t(x,617,'GEEN VLIEGGEGEVENS',16,'bold',color_gray)
            continue
        if i>=len(featured):
            if i==0:
                t(x,616,'LUCHTRUIM RUSTIG',17,'heavy')
                t(x,644,'Geen vliegtuig binnen 30 km',13,'regular',color_gray)
            else:
                t(x,621,'GEEN TWEEDE VLIEGTUIG',14,'bold',color_gray)
            continue

        a=featured[i]
        cat=a.get('category') or 'OVERIG'
        category_label={'LIJNVLUCHT':'LIJNVLUCHT*','CARGO':'VRACHT*',
                        'MILITAIR':'MILITAIR','OVERIG':'VLIEGTUIG'}.get(cat,'VLIEGTUIG')
        t(x,597,f'{i+1:02d} · {category_label}',11,'bold',color_gray)
        cs=_sanitize_token(a.get('callsign')) or 'ONBEKEND'
        t(right-6,597,cs,11,'bold',color_gray,anchor='ra')

        kind=a.get('route_kind')
        origin=_clean_city(a.get('route_from')) if kind=='indicatie' else ''
        destination=_clean_city(a.get('route_to')) if kind=='indicatie' else ''
        typ=_sanitize_token(a.get('type'))
        friendly_type=TYPE_LABELS.get(typ,typ or 'TOESTEL ONBEKEND')
        if origin and destination:
            joined=f'{origin}  →  {destination}'
            joined_text,sz=fit(joined,23,maxw,15)
            if joined_text==joined:
                t(x,620,joined_text,sz,'bold')
                t(x,654,friendly_type,13,'regular')
                t(right-6,655,'ROUTE-INDICATIE',9,'regular',color_gray,anchor='ra')
            else:
                first,fs=fit(origin+'  →',21,maxw,13)
                second,ss=fit(destination,21,maxw,13)
                t(x,612,first,fs,'bold')
                t(x,639,second,ss,'bold')
                t(x,666,friendly_type,11,'regular',color_gray)
        else:
            title,ts=fit(friendly_type if typ else cs,20,maxw,14)
            t(x,619,title,ts,'bold')
            t(x,649,'ROUTE NIET BEKEND',12,'regular',color_gray)

        dist=a.get('distance_km'); alt=a.get('alt_ft'); spd=a.get('speed_kt')
        dist_txt=f'{dist:.1f} KM' if isinstance(dist,(int,float)) else '— KM'
        alt_txt=f'{alt:,} FT'.replace(',','.') if isinstance(alt,int) else '— FT'
        spd_txt=f'{spd} KT' if isinstance(spd,int) else '— KT'
        t(right-6,683,dist_txt+'  ·  '+alt_txt+'  ·  '+spd_txt,
          11,'regular',color_ink,anchor='ra')

    obs=data.get('observed_at_utc')
    if isinstance(obs,dt.datetime):
        if local_tz: obs=obs.astimezone(local_tz)
        stamp=f'SNAPSHOT {obs:%H:%M}'
    else: stamp='GEEN ACTUELE METING'
    return stamp
