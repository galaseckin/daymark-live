#!/usr/bin/env python3
"""DAYMARK v3 • live-rendered Kindle Paperwhite 2 landscape dashboard.

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
    "origin_lat":None,
    "origin_lon":None,
    "ericsson_breda_lat":None,
    "ericsson_breda_lon":None,
    "ericsson_maastricht_lat":None,
    "ericsson_maastricht_lon":None,
    "notes":"Routetijden vereisen vertrekpunt, exacte kantoorcoordinaten en TOMTOM_API_KEY in je Mac-omgeving. Geen adressen vereist voor de demo."
}


def get_config():
    p=BASE/'config.json'
    if not p.exists():
        p.write_text(json.dumps(DEFAULT_CONFIG,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    config={**DEFAULT_CONFIG, **json.loads(p.read_text(encoding='utf-8'))}
    env_keys={'origin_lat':'DAYMARK_ORIGIN_LAT', 'origin_lon':'DAYMARK_ORIGIN_LON',
              'ericsson_breda_lat':'DAYMARK_BREDA_LAT', 'ericsson_breda_lon':'DAYMARK_BREDA_LON',
              'ericsson_maastricht_lat':'DAYMARK_MAASTRICHT_LAT', 'ericsson_maastricht_lon':'DAYMARK_MAASTRICHT_LON'}
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


def weather(cfg):
    params={'latitude':cfg['weather_lat'],'longitude':cfg['weather_lon'],
            'current':'temperature_2m,weather_code,apparent_temperature,wind_speed_10m',
            'daily':'temperature_2m_max,temperature_2m_min,precipitation_probability_max',
            'timezone':'Europe/Amsterdam','forecast_days':1}
    obj=get_json('https://api.open-meteo.com/v1/forecast?'+urllib.parse.urlencode(params))
    cur=obj.get('current',{}); daily=obj.get('daily',{})
    code=cur.get('weather_code')
    if code==0: desc='Zonnig'
    elif code in (1,2): desc='Licht bewolkt'
    elif code==3: desc='Bewolkt'
    elif code in (45,48): desc='Mist'
    elif code in (51,53,55,56,57): desc='Motregen'
    elif code in (61,63,65,66,67,80,81,82): desc='Regen'
    elif code in (71,73,75,77,85,86): desc='Sneeuw'
    elif code in (95,96,99): desc='Onweer'
    else: desc='Wisselvallig'
    def first(key):
        x=daily.get(key,[])
        return x[0] if x else None
    return {'temp':cur.get('temperature_2m'), 'feels':cur.get('apparent_temperature'),
            'desc':desc,'code':code,'high':first('temperature_2m_max'),
            'low':first('temperature_2m_min'),'rain':first('precipitation_probability_max'),
            'wind':cur.get('wind_speed_10m')}


def football(now):
    """ESPN's undocumented schedule endpoint; missing fields stay missing."""
    events={}
    for league,league_name in [('tur.1','Super Lig'),('uefa.champions','Champions League')]:
        try:
            feed=get_json(f'https://site.web.api.espn.com/apis/site/v2/sports/soccer/{league}/teams/432/schedule')
        except Exception:
            try:feed=get_json(f'https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams/432/schedule')
            except Exception as exc:
                print('Wedstrijdfeed niet bereikbaar voor '+league+': '+str(exc));continue
        for event in feed.get('events',[]):
            if event.get('id'):events[event['id']]=(event,league_name)
    past=[]; future=[]
    for ev, league in events.values():
        date=local_date(ev.get('date'))
        if not date:continue
        comp=(ev.get('competitions') or [{}])[0]
        sides=comp.get('competitors') or []
        if len(sides)!=2:continue
        def is_gal(side):
            team=side.get('team') or {}
            return str(team.get('id'))=='432' or 'galatasaray' in str(team.get('displayName','')).lower()
        us=next((side for side in sides if is_gal(side)),None)
        them=next((side for side in sides if not is_gal(side)),None)
        if not us or not them:continue
        opponent=str((them.get('team') or {}).get('displayName','Tegenstander'))
        status=(ev.get('status') or {}).get('type') or (comp.get('status') or {}).get('type') or {}
        completed=status.get('completed') is True or status.get('state')=='post'
        def scr(side):
            v=side.get('score');return str(v.get('displayValue',v.get('value','?'))) if isinstance(v,dict) else str(v) if v is not None else '?'
        score=f'{scr(us)} - {scr(them)}'
        def logo_for(side):
            team=(side.get('team') or {})
            logos=team.get('logos') or []
            return (logos[0].get('href') if logos and isinstance(logos[0],dict) else None) or team.get('logo')
        item={'opponent':opponent,'date':date,'league':league,'score':score,
              'home':us.get('homeAway')=='home',
              'gs_logo':logo_for(us),
              'opponent_logo':logo_for(them),
              'opponent_id':str((them.get('team') or {}).get('id',''))}
        if completed and date <=now+dt.timedelta(hours=6): past.append(item)
        elif date>=now-dt.timedelta(hours=2):future.append(item)
    past.sort(key=lambda it:it['date'],reverse=True)
    future.sort(key=lambda it:it['date'])
    return {'last':past[0] if past else None,'next':future[0] if future else None}


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


def commute(cfg,name):
    origin=[cfg.get('origin_lat'),cfg.get('origin_lon')]
    dest=[cfg.get(f'ericsson_{name}_lat'),cfg.get(f'ericsson_{name}_lon')]
    key=os.environ.get('TOMTOM_API_KEY')
    if not key or any(v is None for v in origin+dest):return None
    coords=f'{origin[0]},{origin[1]}:{dest[0]},{dest[1]}'
    params=urllib.parse.urlencode({'traffic':'true','departAt':'now','travelMode':'car','key':key})
    url=f'https://api.tomtom.com/routing/1/calculateRoute/{coords}/json?{params}'
    data=get_json(url)
    summary=(data.get('routes') or [{}])[0].get('summary') or {}
    sec=summary.get('travelTimeInSeconds')
    return round(sec/60) if sec is not None else None


def demo_data():
    now=dt.datetime.now(TZ)
    return {'now':now,'demo':True,'weather':{'temp':17,'desc':'Licht bewolkt','high':19,'low':10,'rain':20,'wind':9,'feels':16},
            'football':{'last':{'opponent':'Fenerbahce (demo)','opponent_id':'436','opponent_logo':'https://a.espncdn.com/i/teamlogos/soccer/500/436.png','gs_logo':GS_LOGO_URL,'score':'2 - 1','league':'Demo-wedstrijd','date':now-dt.timedelta(days=2),'home':True},
                        'next':{'opponent':'Konyaspor (demo)','opponent_id':'7648','opponent_logo':'https://a.espncdn.com/i/teamlogos/soccer/500/7648.png','gs_logo':GS_LOGO_URL,'date':now+dt.timedelta(days=3),'league':'Demo-wedstrijd','home':True}},
            'stocks':{'NVDA':{'price':210.48,'change':1.2,'points':[2,3,4,4,6,6,5,7,8]},
                      'PLTR':{'price':153.60,'change':-0.4,'points':[4,5,6,5,5,4,7,6,5]},
                      'AAPL':{'price':284.13,'change':0.8,'points':[2,4,3,5,7,6,8,8,9]}},
            'gold':{'price':128300.0,'change':0.3},
            'routes':{'breda':43,'maastricht':66},
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
            'routes':{name:attempt(name,lambda n=name:commute(cfg,n),None) for name in ('breda','maastricht')},
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
    if not url or (url != GS_LOGO_URL and not url.startswith('https://a.espncdn.com/')):
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
    # WEATHER
    x=51
    label(x,133,'WEER  /  '+str(cfg['city']).upper())
    temp=we.get('temp')
    t(x,169,f'{num(temp)}°',102,'heavy')
    # clean weather symbol, not weather-coded illustration
    cx,cy=281,197
    d.ellipse((cx-23,cy-24,cx+15,cy+14),outline=INK,width=4)
    for angle in [0,45,90,135,180,225,270,315]:
        a=math.radians(angle)
        d.line((cx-4+math.cos(a)*27,cy-5+math.sin(a)*27,cx-4+math.cos(a)*35,cy-5+math.sin(a)*35),fill=INK,width=3)
    d.ellipse((cx-48,cy+13,cx+5,cy+56),fill=WHITE,outline=INK,width=4)
    d.ellipse((cx-13,cy+21,cx+44,cy+59),fill=WHITE,outline=INK,width=4)
    rule(cx-47,cy+60,cx+43,cy+60,INK,4)
    t(x,293,fit(we.get('desc') or 'Geen weergegevens',23,260,'bold'),23,'bold',INK)
    rule(51,339,326,339)
    label(x,352,'VANDAAG')
    t(x,385,f"MAX  {num(we.get('high'))}°",23,'bold')
    t(188,385,f"MIN  {num(we.get('low'))}°",23,'bold')
    rule(51,433,326,433)
    label(x,446,'NEERSLAGKANS')
    t(51,476, f"{num(we.get('rain'))}%",34,'heavy')
    t(178,488,f"WIND {num(we.get('wind'))} km/u",16,'bold',GRAY)
    t(x,535,'Weerbron: Open-Meteo',12,'regular',GRAY)
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
        t(514,534,fit(upcoming.get('league',''),12,266),12,'regular',GRAY,anchor='mm')
    else:
        draw_crest(im,d,377,395,73,90,gs_logo,'GALATASARAY')
        t(475,435,'Nog geen fixture',16,'bold')
        t(475,455,'Bron niet beschikbaar',12,'regular',GRAY)
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
    # COMMUTE LOWER BAR
    panel(33,587,991,703)
    label(52,599,'COMMUTE  /  AUTO  ·  VANAF JOUW VERTREKPUNT')
    rule(515,631,515,688)
    for i,(name,title) in enumerate([('breda','ERICSSON BREDA'),('maastricht','ERICSSON MAASTRICHT')]):
        left=52 if i==0 else 534
        t(left,641,title,18,'bold',GRAY)
        m=routes.get(name)
        t(478 if i==0 else 958,634,f'{m} MIN' if m is not None else '— MIN',32,'heavy',INK,anchor='ra')
        if m is None:t(left,674,'Route nog niet ingesteld',13,'regular',GRAY)
        else:t(left,674,'Rijtijd, actueel verkeer' if not data['demo'] else 'VOORBEELD-RIJTIJD',13,'regular',GRAY)
    # footer
    rule(33,718,991,718,INK,2)
    t(34,730,'DAYMARK V3  •  PAPERWHITE 2  •  LANDSCAPE',12,'bold',GRAY)
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
        shutil.copy2(BASE/'ATTRIBUTION.md',dest/'ATTRIBUTION.md')
        status={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                'mode':'demo' if args.demo else 'live',
                'sources_missing':data.get('errors',[]),
                'routes_configured':all(data['routes'].get(n) is not None for n in ('breda','maastricht'))}
        (dest/'status.json').write_text(json.dumps(status,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print('Web feed:',dest/'daymark.png')
    if args.install:install(args.kindle,preview,right,left)

if __name__=='__main__':main()
