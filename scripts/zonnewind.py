"""Elk uur: NOAA-realtime ophalen, opschonen, naar de Aarde verschuiven, per uur middelen.
Eens per dag: officiële NASA OMNI ophalen en de eigen reeks ermee vergelijken.

Uitvoer in data/:
  eigen/JJJJ.json   eigen uurreeks (voorlopig), sleutel 'JJJJ-MM-DDTUU'
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


def actief(r):
    return r.get('active') in (True, 'true', 1) and not (r.get('overall_quality') or 0)


def noaa_minuten(status):
    wind = json.loads(haal(NOAA + 'json/rtsw/rtsw_wind_1m.json'))
    mag = json.loads(haal(NOAA + 'json/rtsw/rtsw_mag_1m.json'))
    m = {}
    for r in wind:
        if not actief(r):
            continue
        k = tijd(r['time_tag']).replace(second=0, microsecond=0)
        o = m.setdefault(k, {})
        zet(o, 'v', r.get('proton_speed'))
        zet(o, 'n', r.get('proton_density'))
        zet(o, 't', r.get('proton_temperature'))
        if r.get('source'):
            o['bron'] = r['source']
    for r in mag:
        if not actief(r):
            continue
        k = tijd(r['time_tag']).replace(second=0, microsecond=0)
        o = m.setdefault(k, {})
        zet(o, 'b', r.get('bt'))
        zet(o, 'by', r.get('by_gsm'))
        zet(o, 'bz', r.get('bz_gsm'))
        if r.get('bx_gse') is not None:
            try:
                zet(o, 'br', -float(r['bx_gse']))
            except (TypeError, ValueError):
                pass
    status['noaa_rijen'] = {'wind': len(wind), 'veld': len(mag), 'minuten_actief': len(m)}
    if m:
        status['l1_laatste'] = max(m).isoformat() + 'Z'
    return m


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


def bewaar_eigen(uren, kp):
    per_jaar = {}
    for u, r in uren.items():
        per_jaar.setdefault(u.year, {})[uursleutel(u)] = r
    for k, waarde in kp.items():
        per_jaar.setdefault(int(k[:4]), {}).setdefault(k, {})['kp'] = waarde
    nieuw = 0
    for jaar, recs in per_jaar.items():
        pad = os.path.join(DATA, 'eigen', f'{jaar}.json')
        oud = lees(pad, {})
        for k, r in recs.items():
            o = oud.get(k, {})
            # vollediger uur wint; Kp wordt altijd bijgewerkt
            if r.get('nmin', 0) >= o.get('nmin', 0):
                kp_oud = o.get('kp')
                o = {**o, **r}
                if 'kp' not in r and kp_oud is not None:
                    o['kp'] = kp_oud
            elif 'kp' in r:
                o['kp'] = r['kp']
            if k not in oud:
                nieuw += 1
            oud[k] = o
        schrijf(pad, dict(sorted(oud.items())))
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
    schrijf(os.path.join(DATA, 'toets.json'), {
        'uitleg': 'eigen uurreeks (NOAA realtime, eigen bewerking) tegen NASA OMNI, alleen uren die in beide staan. afwijking = eigen - OMNI.',
        'bijgewerkt': nu_utc().isoformat(timespec='seconds') + 'Z',
        'per_jaar': stats})
    status['toets_uren'] = uren


def main():
    nu = nu_utc()
    status = {'run': nu.isoformat(timespec='seconds') + 'Z', 'fouten': []}
    try:
        m = noaa_minuten(status)
        status['pieken_verwijderd'] = ontpiek(m)
        verschoven = verschuif(m)
        uren = per_uur(verschoven)
        try:
            kp = noaa_kp()
        except Exception as e:  # Kp is bijzaak
            kp = {}
            status['fouten'].append(f'Kp: {e}')
        status['eigen_nieuwe_uren'] = bewaar_eigen(uren, kp)
        status['live_minuten'] = bewaar_live(verschoven)
        if verschoven:
            status['aarde_tot'] = max(verschoven).isoformat() + 'Z'
            looptijden = [o['loop'] for o in verschoven.values() if 'loop' in o]
            status['looptijd_min'] = looptijden[-1] if looptijden else None
            status['bron'] = Counter(o.get('bron') for o in verschoven.values() if o.get('bron')).most_common(1)[0][0] if any(o.get('bron') for o in verschoven.values()) else None
    except Exception as e:
        status['fouten'].append(f'NOAA: {e}')
        traceback.print_exc()

    omni_pad = os.path.join(DATA, 'omni', f'{nu.year}.json')
    if os.environ.get('OMNI_NU') == 'ja' or nu.hour == 4 or not os.path.exists(omni_pad):
        try:
            omni_bijwerken(status)
        except Exception as e:
            status['fouten'].append(f'OMNI: {e}')
            traceback.print_exc()
    try:
        toets_bijwerken(status)
    except Exception as e:
        status['fouten'].append(f'toets: {e}')

    vorige = lees(os.path.join(DATA, 'status.json'), {})
    if 'omni' not in status and 'omni' in vorige:
        status['omni'] = vorige['omni']
    schrijf(os.path.join(DATA, 'status.json'), status)
    print(json.dumps(status, indent=1, ensure_ascii=False))
    return 1 if status['fouten'] and 'l1_laatste' not in status else 0


if __name__ == '__main__':
    sys.exit(main())
