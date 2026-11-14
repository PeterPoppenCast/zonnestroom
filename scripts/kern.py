"""Gedeelde rekenkern: opschonen, verschuiven naar de Aarde en per uur middelen.

Wordt gebruikt door zonnewind.py (elk uur, NOAA realtime) en toets_archief.py
(eenmalig, DSCOVR-archief tegen OMNI). Alleen standaard-Python.
"""
import datetime as dt
import json
import math
import os
import urllib.request
from collections import Counter
from statistics import median

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
UA = {'User-Agent': 'zonnestroom-kalender (github.com/PeterPoppenCast/zonnestroom)'}

L1_KM = 1.5e6            # gemiddelde afstand L1 tot de Aarde
BOEGSCHOK_KM = 14 * 6371  # gemiddelde afstand boegschok tot de Aarde, zoals OMNI
VELDEN = ['v', 'n', 't', 'b', 'by', 'bz', 'br']
GRENZEN = {               # fysisch mogelijke waarden in de zonnewind bij 1 AU
    'v': (200, 1500), 'n': (0.05, 150), 't': (1e3, 5e6), 'b': (0, 150),
    'by': (-150, 150), 'bz': (-150, 150), 'br': (-150, 150)}
PIEK = {'v': 0.25, 'n': 1.0, 't': 1.5}  # relatieve afwijking die als piek telt
MIN_MINUTEN = 20          # minimaal aantal minuten voor een geldig uur


def haal(url, timeout=90):
    """Download; met ZS_OFFLINE=map leest hij lokale bestanden (voor testen)."""
    off = os.environ.get('ZS_OFFLINE')
    if off:
        p = os.path.join(off, url.rstrip('/').split('/')[-1])
        with open(p, 'rb') as f:
            return f.read()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def nu_utc():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def lees(pad, standaard):
    try:
        with open(pad, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return standaard


def schrijf(pad, obj):
    os.makedirs(os.path.dirname(pad), exist_ok=True)
    tmp = pad + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, separators=(',', ':'), ensure_ascii=False)
    os.replace(tmp, pad)


def tijd(s):
    s = str(s).strip().replace(' ', 'T').replace('Z', '')
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo:
        d = d.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return d


def zet(o, veld, waarde):
    """Bewaar een waarde alleen als hij een getal is binnen de fysische grenzen."""
    try:
        x = float(waarde)
    except (TypeError, ValueError):
        return
    if math.isnan(x):
        return
    lo, hi = GRENZEN[veld]
    if lo <= x <= hi:
        o[veld] = x


def ontpiek(minuten):
    """Verwijder losse pieken: een minuut die sterk afwijkt van de 11 minuten eromheen
    en waarna de waarden niet op dat nieuwe niveau blijven. Echte schokfronten blijven staan."""
    weg = Counter()
    for veld, rel in PIEK.items():
        ks = sorted(k for k in minuten if veld in minuten[k])
        w = [minuten[k][veld] for k in ks]
        for i, k in enumerate(ks):
            med = median(w[max(0, i - 5):i + 6])
            if med and abs(w[i] - med) > rel * abs(med):
                na = w[i + 1:i + 4]
                if not na or abs(median(na) - med) <= rel * abs(med):
                    minuten[k].pop(veld, None)
                    weg[veld] += 1
    return dict(weg)


def verschuif(minuten):
    """Verschuif elke minuut van L1 naar aankomst bij de boegschok: (L1 - boegschok) / v.
    Zonder snelheid op die minuut wordt de laatste bekende snelheid gebruikt."""
    uit, v_laatst = {}, None
    for k in sorted(minuten):
        o = minuten[k]
        v = o.get('v', v_laatst)
        if 'v' in o:
            v_laatst = o['v']
        if not v:
            continue
        seconden = (L1_KM - BOEGSCHOK_KM) / v
        k2 = (k + dt.timedelta(seconds=seconden)).replace(second=0, microsecond=0)
        doel = uit.setdefault(k2, {})
        doel.update({f: o[f] for f in VELDEN if f in o})
        doel['loop'] = round(seconden / 60)
        if o.get('bron'):
            doel['bron'] = o['bron']
    return uit


def per_uur(minuten):
    """Uurgemiddelden van verschoven minuten; een veld telt vanaf MIN_MINUTEN metingen."""
    bak = {}
    for k, o in minuten.items():
        u = k.replace(minute=0)
        b = bak.setdefault(u, {'som': Counter(), 'n': Counter(), 'bron': Counter()})
        for f in VELDEN:
            if f in o:
                b['som'][f] += o[f]
                b['n'][f] += 1
        if o.get('bron'):
            b['bron'][o['bron']] += 1
    uit = {}
    for u, b in bak.items():
        r = {}
        for f in VELDEN:
            if b['n'][f] >= MIN_MINUTEN:
                r[f] = round(b['som'][f] / b['n'][f], 3 if f in ('n',) else 2)
        if r:
            r['nmin'] = max(b['n'].values())
            if b['bron']:
                r['bron'] = b['bron'].most_common(1)[0][0]
            uit[u] = r
    return uit


def uursleutel(u):
    return u.strftime('%Y-%m-%dT%H')


def lees_omni2(tekst):
    """OMNI2 uurwaarden: jaar, dag, uur, |B| (9), Bx GSE (13), By/Bz GSM (16, 17),
    T (23), n (24), v (25), Kp*10 (39), Dst (41). Opvulwaarden worden weggelaten."""
    uit = {}
    for regel in tekst.splitlines():
        w = regel.split()
        if len(w) < 41:
            continue
        try:
            u = dt.datetime(int(w[0]), 1, 1) + dt.timedelta(days=int(w[1]) - 1, hours=int(w[2]))
        except ValueError:
            continue
        r = {}
        def neem(veld, idx, fout, schaal=1.0):
            try:
                x = float(w[idx])
            except ValueError:
                return
            if x < fout:
                r[veld] = round(x * schaal, 3)
        neem('b', 8, 999)
        neem('by', 15, 999)
        neem('bz', 16, 999)
        neem('t', 22, 9999999)
        neem('n', 23, 999)
        neem('v', 24, 9999)
        neem('kp', 38, 99, 0.1)
        neem('dst', 40, 99999)
        try:
            bx = float(w[12])
            if bx < 999:
                r['br'] = round(-bx, 2)
        except ValueError:
            pass
        if r:
            uit[uursleutel(u)] = r
    return uit


def vergelijk(eigen, omni):
    """Per veld: aantal uren, gemiddelde afwijking (eigen - OMNI), RMS-fout, correlatie."""
    stats = {}
    for f in VELDEN:
        paren = [(eigen[k][f], omni[k][f]) for k in eigen
                 if k in omni and f in eigen[k] and f in omni[k]]
        if len(paren) < 3:
            continue
        a = [p[0] for p in paren]
        b = [p[1] for p in paren]
        n = len(paren)
        ma, mb = sum(a) / n, sum(b) / n
        d = [x - y for x, y in paren]
        sa = math.sqrt(sum((x - ma) ** 2 for x in a) / n)
        sb = math.sqrt(sum((y - mb) ** 2 for y in b) / n)
        cov = sum((x - ma) * (y - mb) for x, y in paren) / n
        stats[f] = {
            'uren': n,
            'gem_eigen': round(ma, 3), 'gem_omni': round(mb, 3),
            'afwijking': round(sum(d) / n, 3),
            'afwijking_pct': round(100 * (sum(d) / n) / mb, 1) if mb else None,
            'rms': round(math.sqrt(sum(x * x for x in d) / n), 3),
            'correlatie': round(cov / (sa * sb), 3) if sa and sb else None}
    return stats
