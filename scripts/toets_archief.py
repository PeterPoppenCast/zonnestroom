"""Fase 1: toets van de eigen methode op archiefdata.

Haalt per dag de DSCOVR-bestanden (f1m = zonnewind, m1m = veld) uit het NOAA NCEI-archief
(archive.data.noaa.gov, S3-lijst),
haalt ze door precies dezelfde bewerking als de uurlijkse taak, en vergelijkt het
resultaat per uur met NASA OMNI. Uitvoer: data/toets-archief-JJJJ-MM.json
Gebruik: python scripts/toets_archief.py 2025-06
"""
import datetime as dt
import gzip
import os
import re
import sys
import traceback
import warnings
import json
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kern import (DATA, haal, schrijf, zet, ontpiek, verschuif, per_uur, uursleutel,
                  lees_omni2, vergelijk, nu_utc)

ARCHIEF = 'https://archive.data.noaa.gov/satellite-spaceweather'   # S3, NOAA NCEI (sinds 2026)
PREFIX = {'f1m': 'DSCOVR/DSCOVR/FC/f1m/{j}/{m:02d}/', 'm1m': 'DSCOVR/DSCOVR/MAG/m1m/{j}/{m:02d}/'}
SPDF = 'https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/'
DAG = re.compile(r'_s(\d{8})')


def s3_lijst(prefix):
    import xml.etree.ElementTree as ET
    sleutels, token = [], ''
    while True:
        q = {'list-type': '2', 'prefix': prefix}
        if token:
            q['continuation-token'] = token
        xml = haal(ARCHIEF + '?' + urllib.parse.urlencode(q))
        root = ET.fromstring(xml)
        ns = root.tag.split('}')[0] + '}' if root.tag.startswith('{') else ''
        for c in root.findall(ns + 'Contents'):
            k = c.findtext(ns + 'Key')
            if k and k.endswith('.nc.gz'):
                sleutels.append(k)
        if (root.findtext(ns + 'IsTruncated') or '').lower() != 'true':
            return sleutels
        token = root.findtext(ns + 'NextContinuationToken') or ''
        if not token:
            return sleutels


def bestanden(jaar, maand, log):
    gekozen = {}
    for soort, pf in PREFIX.items():
        prefix = pf.format(j=jaar, m=maand)
        sleutels = s3_lijst(prefix)
        log.setdefault('archief', {})[soort] = {'prefix': prefix, 'objecten': len(sleutels),
                                                'voorbeeld': sleutels[0] if sleutels else None}
        for k in sleutels:
            m = DAG.search(k)
            dag = m.group(1) if m else k
            # bij meerdere versies van dezelfde dag wint de laatst verwerkte (hoogste naam)
            if (soort, dag) not in gekozen or k > gekozen[(soort, dag)]:
                gekozen[(soort, dag)] = k
    log['bestanden_gevonden'] = len(gekozen)
    return gekozen


def lees_nc(data, velden, log):
    """Leest een dagbestand; geeft per minuut de waarden en de kwaliteit (0 normaal, 1 twijfel, 2 fout)."""
    import netCDF4
    import numpy as np
    ds = netCDF4.Dataset('mem', memory=gzip.decompress(data))
    try:
        log.setdefault('variabelen', {})[velden.get('_soort', '?')] = sorted(ds.variables.keys())
        tv = ds.variables['time']
        eenheid = getattr(tv, 'units', 'milliseconds since 1970-01-01')
        t = np.asarray(tv[:], dtype='float64')
        deler = 1000.0 if 'milli' in eenheid else 1.0
        basis = dt.datetime(1970, 1, 1)
        kol = {}
        for doel, bron in velden.items():
            if doel != '_soort' and bron in ds.variables:
                x = np.ma.asarray(ds.variables[bron][:], dtype='float64')
                kol[doel] = np.ma.filled(np.ma.masked_invalid(x), np.nan)
        kw = np.zeros(len(t))
        if 'overall_quality' in ds.variables:
            kw = np.ma.filled(np.ma.asarray(ds.variables['overall_quality'][:], dtype='float64'), 2)
        rijen = []
        for i in range(len(t)):
            k = (basis + dt.timedelta(seconds=float(t[i]) / deler)).replace(second=0, microsecond=0)
            rijen.append((k, int(kw[i]), {d: float(kol[d][i]) for d in kol}))
        return rijen
    finally:
        ds.close()


def bouw(ruw, max_kwaliteit):
    """Minuten uit de archiefrijen, alleen met kwaliteit <= max_kwaliteit."""
    minuten = {}
    for soort, rijen in ruw:
        for k, q, r in rijen:
            if q > max_kwaliteit:
                continue
            o = minuten.setdefault(k, {})
            for f in ('v', 'n', 't', 'b', 'by', 'bz'):
                if f in r:
                    zet(o, f, r[f])
            if 'bx' in r:
                zet(o, 'br', -r['bx'])
    return minuten


def main():
    warnings.filterwarnings('ignore', message='.*valid_m.*')
    arg = sys.argv[1] if len(sys.argv) > 1 else '2025-06'
    jaar, maand = int(arg[:4]), int(arg[5:7])
    log = {'maand': arg, 'run': nu_utc().isoformat(timespec='seconds') + 'Z', 'fouten': []}
    uit_pad = os.path.join(DATA, f'toets-archief-{arg}.json')
    try:
        lijst = bestanden(jaar, maand, log)
        ruw = []
        for (soort, dag), sleutel in sorted(lijst.items()):
            try:
                data = haal(ARCHIEF + '/' + urllib.parse.quote(sleutel), timeout=180)
                if soort == 'f1m':
                    velden = {'_soort': 'f1m', 'v': 'proton_speed', 'n': 'proton_density', 't': 'proton_temperature'}
                else:
                    velden = {'_soort': 'm1m', 'b': 'bt', 'by': 'by_gsm', 'bz': 'bz_gsm', 'bx': 'bx_gse'}
                ruw.append((soort, lees_nc(data, velden, log)))
            except Exception as e:
                log['fouten'].append(f'{sleutel}: {e}')
        log['dagbestanden_gelezen'] = len(ruw)
        kw = {}
        for soort, rijen in ruw:
            for _, q, _ in rijen:
                kw[f'{soort}_{q}'] = kw.get(f'{soort}_{q}', 0) + 1
        log['kwaliteit_minuten'] = kw

        omni = lees_omni2(haal(SPDF + f'omni2_{jaar}.dat').decode('ascii', 'replace'))
        omni = {k: r for k, r in omni.items() if k.startswith(f'{jaar}-{maand:02d}')}
        log['uren_omni'] = len(omni)

        # twee varianten: alleen 'normaal' (zoals de uurlijkse taak), en ook 'twijfel' toelaten
        log['varianten'] = {}
        reeks = {}
        for naam, maxq in (('alleen_normaal', 0), ('ook_twijfel', 1)):
            minuten = bouw(ruw, maxq)
            weg = ontpiek(minuten)
            eigen = {uursleutel(u): r for u, r in per_uur(verschuif(minuten)).items()
                     if u.year == jaar and u.month == maand}
            log['varianten'][naam] = {'minuten': len(minuten), 'pieken_verwijderd': weg,
                                      'uren_eigen': len(eigen), 'vergelijking': vergelijk(eigen, omni)}
            if naam == 'alleen_normaal':
                reeks = eigen
        log['reeks'] = {k: {'eigen': {f: reeks.get(k, {}).get(f) for f in ('v', 'n', 'bz')},
                            'omni': {f: omni.get(k, {}).get(f) for f in ('v', 'n', 'bz')}}
                        for k in sorted(set(reeks) | set(omni))}
    except Exception as e:
        log['fouten'].append(f'{type(e).__name__}: {e}')
        traceback.print_exc()
    schrijf(uit_pad, log)
    samen = {k: v for k, v in log.items() if k not in ('reeks', 'variabelen')}
    print(json.dumps(samen, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
