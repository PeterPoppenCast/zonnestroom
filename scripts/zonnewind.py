"""Elk uur: NOAA-realtime ophalen, opschonen, naar de Aarde verschuiven, per uur middelen.
Eens per dag: officiële NASA OMNI ophalen en de eigen reeks ermee vergelijken.

Uitvoer in data/:
  eigen/JJJJ.json   eigen uurreeks (voorlopig), mediaan over alle satellieten, sleutel 'JJJJ-MM-DDTUU'
  bronnen/JJJJ.json dezelfde uren per satelliet apart (SOLAR1, IMAP, ACE, WIND), voor de toets per satelliet
  status-wind.json  wat de dagelijkse Wind-ophaal deed, met de gevonden variabelnamen
  omni/JJJJ.json    officiële OMNI uurreeks, zelfde vorm
  live/minuten.json laatste 7 dagen per minuut, al verschoven naar de Aarde
  toets.json        vergelijking eigen tegen OMNI waar beide bestaan
  status.json       wat er bij de laatste run gebeurde
"""
import datetime as dt
import os
import sys
import traceback
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kern import (nu_utc, DATA, VELDEN, haal, lees, schrijf, tijd, zet, ontpiek, verschuif,
                  per_uur, uursleutel, lees_omni2, vergelijk)
import json

NOAA = 'https://services.swpc.noaa.gov/'
SPDF = 'https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/'
LIVE_DAGEN = 7
WIND_DAGEN = 25   # zoveel dagen Wind k0 opnieuw ophalen; dekt het gat tot OMNI


def goed(r):
    """Kwaliteitsvlag 0 (normaal). Alle satellieten tellen mee, niet alleen de actieve."""
    return not (r.get('overall_quality') or 0)


def noaa_minuten(status):
    """Minuten per satelliet: {bron: {minuut: {veld: waarde}}}."""
    wind = json.loads(haal(NOAA + 'json/rtsw/rtsw_wind_1m.json'))
    mag = json.loads(haal(NOAA + 'json/rtsw/rtsw_mag_1m.json'))
    per_bron, actieve = {}, Counter()
    for r in wind:
        if not goed(r):
            continue
        b = r.get('source') or 'onbekend'
        k = tijd(r['time_tag']).replace(second=0, microsecond=0)
        o = per_bron.setdefault(b, {}).setdefault(k, {})
        zet(o, 'v', r.get('proton_speed'))
        zet(o, 'n', r.get('proton_density'))
        zet(o, 't', r.get('proton_temperature'))
        if r.get('active') in (True, 'true', 1):
            actieve[b] += 1
    for r in mag:
        if not goed(r):
            continue
        b = r.get('source') or 'onbekend'
        k = tijd(r['time_tag']).replace(second=0, microsecond=0)
        o = per_bron.setdefault(b, {}).setdefault(k, {})
        zet(o, 'b', r.get('bt'))
        zet(o, 'by', r.get('by_gsm'))
        zet(o, 'bz', r.get('bz_gsm'))
        if r.get('bx_gse') is not None:
            try:
                zet(o, 'br', -float(r['bx_gse']))
            except (TypeError, ValueError):
                pass
    for b in list(per_bron):
        per_bron[b] = {k: o for k, o in per_bron[b].items() if o}
        if not per_bron[b]:
            del per_bron[b]
    status['noaa_rijen'] = {'wind': len(wind), 'veld': len(mag),
                            'minuten_per_bron': {b: len(m) for b, m in per_bron.items()},
                            'actief': actieve.most_common(1)[0][0] if actieve else None}
    alle = [k for m in per_bron.values() for k in m]
    if alle:
        status['l1_laatste'] = max(alle).isoformat() + 'Z'
    return per_bron


def mediaan(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def combineer(per_bron_uren):
    """Per uur en per veld de mediaan over de satellieten; spreiding = hoogste - laagste."""
    uit = {}
    for u in sorted({u for uren in per_bron_uren.values() for u in uren}):
        r, spr, bronnen = {}, {}, []
        for f in VELDEN:
            w = [(b, uren[u][f]) for b, uren in per_bron_uren.items() if u in uren and f in uren[u]]
            if not w:
                continue
            xs = [x for _, x in w]
            r[f] = round(mediaan(xs), 3 if f == 'n' else 2)
            if len(xs) > 1:
                spr[f] = round(max(xs) - min(xs), 3 if f == 'n' else 2)
            bronnen += [b for b, _ in w]
        if r:
            r['nmin'] = max(uren[u].get('nmin', 0) for uren in per_bron_uren.values() if u in uren)
            r['bronnen'] = sorted(set(bronnen))
            if spr:
                r['spreiding'] = spr
            uit[u] = r
    return uit


def combineer_minuten(per_bron_verschoven):
    uit = {}
    for k in {k for m in per_bron_verschoven.values() for k in m}:
        o = {}
        for f in VELDEN:
            xs = [m[k][f] for m in per_bron_verschoven.values() if k in m and f in m[k]]
            if xs:
                o[f] = round(mediaan(xs), 3)
        lp = [m[k]['loop'] for m in per_bron_verschoven.values() if k in m and 'loop' in m[k]]
        if lp:
            o['loop'] = lp[0]
        uit[k] = o
    return uit


def noaa_kp():
    rows = json.loads(haal(NOAA + 'products/noaa-planetary-k-index.json'))
    if rows and isinstance(rows[0], list):
        kop = [str(x).lower() for x in rows[0]]
        rows = [dict(zip(kop, r)) for r in rows[1:]]
    uit = {}
    for r in rows:
        try:
            t = tijd(r.get('time_tag'))
            kp = float(r.get('Kp', r.get('kp')))
        except (TypeError, ValueError):
            continue
        for h in range(3):
            uit[uursleutel(t + dt.timedelta(hours=h))] = round(kp, 2)
    return uit


def bewaar_bronnen(per_bron_uren):
    per_jaar = {}
    for b, uren in per_bron_uren.items():
        for u, r in uren.items():
            per_jaar.setdefault(u.year, {}).setdefault(uursleutel(u), {})[b] = r
    for jaar, recs in per_jaar.items():
        pad = os.path.join(DATA, 'bronnen', f'{jaar}.json')
        oud = lees(pad, {})
        for k, per in recs.items():
            o = oud.get(k, {})
            for b, r in per.items():
                if r.get('nmin', 0) >= o.get(b, {}).get('nmin', 0):
                    o[b] = r
            oud[k] = o
        schrijf(pad, dict(sorted(oud.items())))
    return {jaar: set(recs) for jaar, recs in per_jaar.items()}


def herbereken_eigen(geraakt, kp):
    """Eigen reeks = per uur de mediaan over alle satellieten in data/bronnen. Kp erbij."""
    nieuw = 0
    for k, waarde in kp.items():
        geraakt.setdefault(int(k[:4]), set()).add(k)
    for jaar, sleutels in geraakt.items():
        bronnen = lees(os.path.join(DATA, 'bronnen', f'{jaar}.json'), {})
        pad = os.path.join(DATA, 'eigen', f'{jaar}.json')
        eigen = lees(pad, {})
        sub = {k: bronnen[k] for k in sleutels if k in bronnen}
        per_bron = {}
        for k, per in sub.items():
            for b, r in per.items():
                per_bron.setdefault(b, {})[k] = r
        gecombineerd = combineer(per_bron)
        for k in sleutels:
            oud = eigen.get(k, {})
            r = dict(gecombineerd.get(k, {}))
            if k in kp:
                r['kp'] = kp[k]
            elif 'kp' in oud:
                r['kp'] = oud['kp']
            if r:
                if k not in eigen:
                    nieuw += 1
                eigen[k] = r
        schrijf(pad, dict(sorted(eigen.items())))
    return nieuw


def bewaar_live(verschoven):
    pad = os.path.join(DATA, 'live', 'minuten.json')
    oud = lees(pad, {'velden': VELDEN, 'rijen': []})
    rijen = {r[0]: r for r in oud.get('rijen', [])}
    for k, o in verschoven.items():
        rijen[k.strftime('%Y-%m-%dT%H:%M')] = [k.strftime('%Y-%m-%dT%H:%M')] + [o.get(f) for f in VELDEN]
    grens = (nu_utc() - dt.timedelta(days=LIVE_DAGEN)).strftime('%Y-%m-%dT%H:%M')
    rijen = [rijen[k] for k in sorted(rijen) if k >= grens]
    schrijf(pad, {'velden': VELDEN, 'toelichting': 'tijden in UTC, verschoven naar aankomst bij de Aarde', 'rijen': rijen})
    return len(rijen)


def omni_bijwerken(status):
    nu = nu_utc()
    jaren = [nu.year] + ([nu.year - 1] if nu.month == 1 else [])
    for jaar in jaren:
        tekst = haal(SPDF + f'omni2_{jaar}.dat').decode('ascii', 'replace')
        recs = lees_omni2(tekst)
        schrijf(os.path.join(DATA, 'omni', f'{jaar}.json'), dict(sorted(recs.items())))
        met_v = [k for k, r in recs.items() if 'v' in r]
        status.setdefault('omni', {})[str(jaar)] = {'uren': len(recs), 'zonnewind_tot': max(met_v) if met_v else None}


def verken_wind(status):
    """Kijkt hoe vers de snelle Wind-data (Key Parameters, near real time) is. Alleen verkennen."""
    import re
    jaar = nu_utc().year
    uit = {}
    for naam, url, patroon in (
            ('swe_k0', f'https://cdaweb.gsfc.nasa.gov/pub/data/wind/swe/swe_k0/{jaar}/', r'wi_k0_swe_(\d{8})'),
            ('mfi_k0', f'https://cdaweb.gsfc.nasa.gov/pub/data/wind/mfi/mfi_k0/{jaar}/', r'wi_k0_mfi_(\d{8})'),
            ('swe_h1', f'https://cdaweb.gsfc.nasa.gov/pub/data/wind/swe/swe_h1/{jaar}/', r'wi_h1_swe_(\d{8})'),
            ('mfi_h0', f'https://cdaweb.gsfc.nasa.gov/pub/data/wind/mfi/mfi_h0/{jaar}/', r'wi_h0_mfi_(\d{8})')):
        try:
            datums = re.findall(patroon, haal(url, timeout=60).decode('utf-8', 'replace'))
            uit[naam] = {'url': url, 'laatste': max(datums) if datums else None, 'bestanden': len(set(datums))}
        except Exception as e:
            uit[naam] = {'url': url, 'fout': str(e)[:200]}
    try:
        html = haal('https://pwg.gsfc.nasa.gov/windnrt/', timeout=60).decode('utf-8', 'replace')
        links = re.findall(r'href="([^"]+)"', html)
        uit['windnrt'] = {'bytes': len(html), 'links': links[:40],
                          'titel': (re.findall(r'<title>(.*?)</title>', html, re.S) or [''])[0].strip()[:120]}
    except Exception as e:
        uit['windnrt'] = {'fout': str(e)[:200]}
    status['wind_verkenning'] = uit


def toets_bijwerken(status):
    stats, uren = {}, 0
    for naam in sorted(os.listdir(os.path.join(DATA, 'eigen'))) if os.path.isdir(os.path.join(DATA, 'eigen')) else []:
        jaar = naam.split('.')[0]
        eigen = lees(os.path.join(DATA, 'eigen', naam), {})
        omni = lees(os.path.join(DATA, 'omni', naam), {})
        s = vergelijk(eigen, omni)
        if s:
            stats[jaar] = s
            uren += max(x['uren'] for x in s.values())
    per_bron = {}
    if os.path.isdir(os.path.join(DATA, 'bronnen')):
        for naam in sorted(os.listdir(os.path.join(DATA, 'bronnen'))):
            jaar = naam.split('.')[0]
            recs = lees(os.path.join(DATA, 'bronnen', naam), {})
            omni = lees(os.path.join(DATA, 'omni', naam), {})
            for b in sorted({b for per in recs.values() for b in per}):
                s = vergelijk({k: per[b] for k, per in recs.items() if b in per}, omni)
                if s:
                    per_bron.setdefault(jaar, {})[b] = s
    schrijf(os.path.join(DATA, 'toets.json'), {
        'uitleg': 'eigen uurreeks (mediaan van de satellieten) en elke satelliet apart, tegen NASA OMNI, alleen uren die in beide staan. afwijking = eigen - OMNI.',
        'per_satelliet': per_bron,
        'bijgewerkt': nu_utc().isoformat(timespec='seconds') + 'Z',
        'per_jaar': stats})
    status['toets_uren'] = uren


def main():
    nu = nu_utc()
    status = {'run': nu.isoformat(timespec='seconds') + 'Z', 'fouten': []}
    try:
        per_bron = noaa_minuten(status)
        status['pieken_verwijderd'] = {b: ontpiek(m) for b, m in per_bron.items()}
        verschoven_per_bron = {b: verschuif(m) for b, m in per_bron.items()}
        uren_per_bron = {b: per_uur(v) for b, v in verschoven_per_bron.items()}
        verschoven = combineer_minuten(verschoven_per_bron)
        status['live_minuten'] = bewaar_live(verschoven)
        status['uren_per_bron'] = {b: len(u) for b, u in uren_per_bron.items()}
        if verschoven:
            status['aarde_tot'] = max(verschoven).isoformat() + 'Z'
            status['looptijd_min'] = verschoven[max(verschoven)].get('loop')
            status['bronnen'] = sorted(per_bron)
    except Exception as e:
        status['fouten'].append(f'NOAA: {e}')
        traceback.print_exc()

    if 'uren_per_bron' not in locals():
        uren_per_bron = {}
    dagelijks = os.environ.get('OMNI_NU') == 'ja' or nu.hour == 4
    if dagelijks or not os.path.exists(os.path.join(DATA, 'status-wind.json')):
        wlog = {'run': status['run']}
        try:
            import wind
            wm = wind.minuten(WIND_DAGEN, wlog)
            wlog['pieken_verwijderd'] = ontpiek(wm)
            uren_per_bron['WIND'] = per_uur(verschuif(wm))
            wlog['uren'] = len(uren_per_bron['WIND'])
        except Exception as e:
            wlog.setdefault('fouten', []).append(f'{type(e).__name__}: {e}')
            status['fouten'].append(f'Wind: {e}')
            traceback.print_exc()
        schrijf(os.path.join(DATA, 'status-wind.json'), wlog)
        status['wind'] = {k: wlog.get(k) for k in ('tot', 'uren', 'afstand_km', 'dagbestanden')}
    try:
        kp = noaa_kp()
    except Exception as e:  # Kp is bijzaak
        kp = {}
        status['fouten'].append(f'Kp: {e}')
    try:
        geraakt = bewaar_bronnen(uren_per_bron) if uren_per_bron else {}
        status['eigen_nieuwe_uren'] = herbereken_eigen(geraakt, kp)
    except Exception as e:
        status['fouten'].append(f'opslaan: {e}')
        traceback.print_exc()

    omni_pad = os.path.join(DATA, 'omni', f'{nu.year}.json')
    if os.environ.get('OMNI_NU') == 'ja' or nu.hour == 4 or not os.path.exists(omni_pad):
        try:
            omni_bijwerken(status)
        except Exception as e:
            status['fouten'].append(f'OMNI: {e}')
            traceback.print_exc()
        try:
            verken_wind(status)
        except Exception as e:
            status['fouten'].append(f'Wind-verkenning: {e}')
    try:
        toets_bijwerken(status)
    except Exception as e:
        status['fouten'].append(f'toets: {e}')

    vorige = lees(os.path.join(DATA, 'status.json'), {})
    for sleutel in ('omni', 'wind_verkenning', 'wind'):
        if sleutel not in status and sleutel in vorige:
            status[sleutel] = vorige[sleutel]
    schrijf(os.path.join(DATA, 'status.json'), status)
    print(json.dumps(status, indent=1, ensure_ascii=False))
    return 1 if status['fouten'] and 'l1_laatste' not in status else 0


if __name__ == '__main__':
    sys.exit(main())
