"""DAYMARK AIRSPACE: optional, read-only aviation snapshot for Meerhoven.

Only public neighborhood coordinates are used. No home address or personal
identifier is sent to OpenSky. All network I/O runs on the server, never Kindle.

The lower bar draws on the caller's existing Pillow image at 1024x758.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import os
import urllib.parse
import urllib.request

# Public *neighborhood* point in Meerhoven, NOT a private address.
CENTER_LAT = 51.44148
CENTER_LON = 5.40563
RADIUS_KM = 30.0


def _finite(x):
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


def haversine_km(lat1, lon1, lat2, lon2):
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dr = rlat2-rlat1
    dl = math.radians(lon2-lon1)
    a = math.sin(dr/2)**2 + math.cos(rlat1)*math.cos(rlat2)*math.sin(dl/2)**2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(max(0.0,a))))


def _get_json(url, timeout=8):
    req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'DAYMARK-Airspace/1.0 personal dashboard'})
    with urllib.request.urlopen(req,timeout=timeout) as response:
        raw=response.read(3_000_001)
    if len(raw)>3_000_000: raise ValueError('API-antwoord te groot')
    return json.loads(raw)


def _enrich_adsbdb(item):
    """Optional enrichment; OFF by default due to route redistribution uncertainty.

    Only enable when you have established that the data usage rights permit
    publishing the resulting public image. Never persist a route database.
    """
    if os.getenv('DAYMARK_AIRSPACE_ENRICH','0')!='1': return
    icao=item.get('icao24') or ''
    if len(icao)!=6 or any(c not in '0123456789abcdef' for c in icao): return
    cs=''.join(c for c in item.get('callsign','') if c.isalnum()).upper()
    url='https://api.adsbdb.com/v0/aircraft/'+icao
    if cs: url+='?'+urllib.parse.urlencode({'callsign':cs})
    try:
        data=_get_json(url,timeout=4).get('response')
        if not isinstance(data,dict): return
        a=data.get('aircraft') or {}
        if isinstance(a,dict):
            item['type']=str(a.get('icao_type') or '')[:8]
        fl=data.get('flightroute') or {}
        if isinstance(fl,dict):
            origin=fl.get('origin') or {}; destination=fl.get('destination') or {}
            if isinstance(origin,dict) and isinstance(destination,dict):
                a=str(origin.get('iata_code') or origin.get('icao_code') or '').upper()
                b=str(destination.get('iata_code') or destination.get('icao_code') or '').upper()
                if 3<=len(a)<=4 and 3<=len(b)<=4 and a.isalnum() and b.isalnum():
                    item['route']=a+' → '+b
    except Exception:
        return


def parse_snapshot(payload, *, now_utc=None, radius_km=RADIUS_KM):
    """Parse and validate state vectors, rejecting stale or implausible positions."""
    now_utc=now_utc or dt.datetime.now(dt.timezone.utc)
    now_ts=now_utc.timestamp()
    if not isinstance(payload,dict) or not isinstance(payload.get('states'),list):
        raise ValueError('OpenSky antwoord zonder states')
    source_ts=_finite(payload.get('time'))
    if source_ts is None or abs(now_ts-source_ts)>300:
        raise ValueError('Verouderde OpenSky-snapshot')
    result=[]
    for row in payload['states']:
        if not isinstance(row,list) or len(row)<11: continue
        # Fields documented by OpenSky: 3=time_position, 5=lon, 6=lat,
        # 7=baro_alt, 8=on_ground, 9=velocity, 10=true_track.
        pos_ts=_finite(row[3]); lat=_finite(row[6]); lon=_finite(row[5]);
        if pos_ts is None or lat is None or lon is None: continue
        if not -90<=lat<=90 or not -180<=lon<=180: continue
        if row[8] is True or abs(now_ts-pos_ts)>180: continue
        distance=haversine_km(CENTER_LAT,CENTER_LON,lat,lon)
        if distance>radius_km: continue
        icao=str(row[0] or '').strip().lower()
        callsign=str(row[1] or '').strip().upper()[:10]
        alt_m=_finite(row[7]); alt_ft=round(alt_m*3.28084) if alt_m is not None else None
        speed_ms=_finite(row[9]); speed_kt=round(speed_ms*1.94384) if speed_ms is not None and speed_ms>=0 else None
        track=_finite(row[10]); track=track%360 if track is not None else None
        result.append({'icao24':icao,'callsign':callsign or icao.upper() or 'ONBEKEND',
          'lat':lat,'lon':lon,'distance_km':round(distance,1),
          'alt_ft':alt_ft,'speed_kt':speed_kt,'track':track,'type':'','route':'',
          'position_age_s':max(0,int(now_ts-pos_ts))})
    result.sort(key=lambda a:(a['distance_km'],a['callsign']))
    return {'status':'ok','source':'OpenSky','flights':result,
            'count':len(result),'observed_at_utc':dt.datetime.fromtimestamp(source_ts,dt.timezone.utc),
            'error':None}


def fetch(now_utc=None):
    """Fetch one rate-limited public snapshot. Fail safe: never invent traffic."""
    now_utc=now_utc or dt.datetime.now(dt.timezone.utc)
    lat_delta=RADIUS_KM/110.574
    lon_delta=RADIUS_KM/(111.320*math.cos(math.radians(CENTER_LAT)))
    params={'lamin':round(CENTER_LAT-lat_delta,5),'lamax':round(CENTER_LAT+lat_delta,5),
            'lomin':round(CENTER_LON-lon_delta,5),'lomax':round(CENTER_LON+lon_delta,5)}
    url='https://opensky-network.org/api/states/all?'+urllib.parse.urlencode(params)
    try:
        result=parse_snapshot(_get_json(url),now_utc=now_utc)
        for item in result['flights'][:3]: _enrich_adsbdb(item)
        return result
    except Exception as exc:
        return {'status':'error','source':'OpenSky','flights':[], 'count':None,
                'observed_at_utc':None,'error':type(exc).__name__+': '+str(exc)[:160]}


def demo(now):
    """Only used by explicit --demo; no fabricated records in live mode."""
    positions=[
      (51.493,5.498,'DEMO101','B738','EIN → PMI',8700,145,285),
      (51.345,5.290,'DEMO202','A320','AMS → BCN',16800,222,330),
      (51.522,5.204,'DEMO303','E190','EIN → LHR',11200,180,95),
    ]
    flights=[]
    for lat,lon,cs,typ,route,alt,speed,track in positions:
        flights.append({'icao24':'000000','callsign':cs,'type':typ,'route':route,
          'lat':lat,'lon':lon,'distance_km':round(haversine_km(CENTER_LAT,CENTER_LON,lat,lon),1),
          'alt_ft':alt,'speed_kt':speed,'track':track,'position_age_s':3})
    flights.sort(key=lambda x:x['distance_km'])
    return {'status':'demo','source':'DEMO','flights':flights,'count':len(flights),
      'observed_at_utc':now.astimezone(dt.timezone.utc),'error':None}


def render_bar(im, d, data, *, font, color_ink=31, color_gray=95, color_border=207,
               pale=246, white=255, local_tz=None):
    """Draw the real available bottom band (x33–991, y587–703)."""
    t=lambda x,y,s,size=12,kind='regular',fill=color_ink,anchor=None: d.text((x,y),str(s),font=font(size,kind),fill=fill,anchor=anchor)
    d.rounded_rectangle((33,587,991,703),radius=15,fill=white,outline=color_border,width=2)
    d.line((214,600,214,690),fill=color_border,width=1)
    d.line((455,600,455,690),fill=color_border,width=1)
    # Left area: oversized count with accurate failure vs zero distinction.
    t(52,599,'AIRSPACE',15,'bold',color_gray)
    state=data.get('status')
    count=data.get('count')
    if state=='error':
        t(52,624,'—',43,'heavy')
        t(52,674,'BRON OFFLINE',11,'bold',color_gray)
    else:
        t(52,619,str(count if count is not None else '—'),47,'heavy')
        t(52,675,'BINNEN 30 KM',11,'bold',color_gray)
    t(52,691,'MEERHOVEN',10,'bold',color_gray)
    # Radar radius constrained by the actual y587..703 band.
    cx,cy=334,647
    for r in (23,46):
        d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=color_border,width=1)
    d.line((cx-49,cy,cx+49,cy),fill=207,width=1)
    d.line((cx,cy-49,cx,cy+49),fill=207,width=1)
    d.ellipse((cx-3,cy-3,cx+3,cy+3),fill=color_ink)
    t(cx+4,594,'N',9,'bold',color_gray,anchor='mm')
    flights=data.get('flights') or []
    for i,a in enumerate(flights[:8]):
        lat=_finite(a.get('lat')); lon=_finite(a.get('lon'))
        if lat is None or lon is None: continue
        east=(lon-CENTER_LON)*111.320*math.cos(math.radians(CENTER_LAT))
        north=(lat-CENTER_LAT)*110.574
        x=cx+east/RADIUS_KM*46
        y=cy-north/RADIUS_KM*46
        # Restrict marker fully to radar circle.
        dx,dy=x-cx,y-cy
        dist=math.hypot(dx,dy)
        if dist>43:
            x=cx+dx/dist*43; y=cy+dy/dist*43
        track=_finite(a.get('track'))
        if track is not None:
            # Tiny arrow rotated to actual course (north = top).
            angle=math.radians(track)
            fx,fy=math.sin(angle),-math.cos(angle)
            sx,sy=-fy,fx
            tip=(x+fx*7,y+fy*7)
            l=(x-fx*4+sx*3,y-fy*4+sy*3)
            r=(x-fx*4-sx*3,y-fy*4-sy*3)
            d.polygon([tip,l,r],fill=color_ink)
        else:
            d.ellipse((x-3,y-3,x+3,y+3),fill=color_ink)
        if i<3:
            tx=x+9 if x<cx+28 else x-9
            ty=y-10 if y>cy-29 else y+9
            t(tx,ty,str(i+1),9,'heavy',color_ink,'lm' if tx>x else 'rm')
    # Right: 3 columns, not 3 stacked rows (too little vertical space).
    for x in (637,809): d.line((x,600,x,690),fill=color_border,width=1)
    if state=='error':
        t(478,618,'GEEN ACTUELE RADARGEGEVENS',17,'bold')
        t(478,651,'OpenSky tijdelijk niet bereikbaar',13,'regular',color_gray)
    elif not flights:
        t(478,618,'GEEN VLIEGTUIGEN GEDETECTEERD',16,'bold')
        t(478,651,'Binnen 30 km · posities van deze meting',12,'regular',color_gray)
    else:
        for i,a in enumerate(flights[:3]):
            x=(474,648,819)[i]
            maxw=(152,150,151)[i]
            t(x,599,f'{i+1:02d}  ·  VLIEGTUIG',10,'bold',color_gray)
            cs=(a.get('callsign') or 'ONBEKEND')[:11]
            t(x,615,cs,16 if len(cs)<=8 else 14,'heavy')
            route=(a.get('route') or '')
            typ=(a.get('type') or '')
            course=_finite(a.get('track'))
            desc=(typ+' · '+route) if typ and route else (route or typ or
                    (f'KOERS {course:.0f}°' if course is not None else 'TYPE ONBEKEND'))
            while desc and d.textbbox((0,0),desc,font=font(12,'bold'))[2]>156:
                desc=desc[:-2]+'…'
            t(x,642,desc,12,'bold',color_ink)
            alt=a.get('alt_ft')
            t(x,663, (f'{alt:,} FT'.replace(',','.') if isinstance(alt,int) else 'HOOGTE —'),14,'bold')
            dist=a.get('distance_km')
            speed=a.get('speed_kt')
            sub=(f'{dist:.1f} KM' if isinstance(dist,(float,int)) else '— KM')
            if isinstance(speed,int): sub+=f'  ·  {speed} KT'
            t(x,686,sub[:24],11,'regular',color_gray)
    obs=data.get('observed_at_utc')
    if isinstance(obs,dt.datetime):
        if local_tz: obs=obs.astimezone(local_tz)
        stamp=f'SNAPSHOT {obs:%H:%M}'
    else: stamp='GEEN ACTUELE METING'
    # Subtitle in external footer rather than inside the cramped strip.
    return stamp
