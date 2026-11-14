"""Fase 1: toets van de eigen methode op archiefdata.

Haalt per dag de DSCOVR-bestanden (f1m = zonnewind, m1m = veld) uit het NOAA-archief,
haalt ze door precies dezelfde bewerking als de uurlijkse taak, en vergelijkt het
resultaat per uur met NASA OMNI. Uitvoer: data/toets-archief-JJJJ-MM.json
Gebruik: python scripts/toets_archief.py 2025-06
"""
import datetime as dt
import gzip
import io
import os
import re
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kern import (DATA, haal, schrijf, zet, ontpiek, verschuif, per_uur, uursleutel,
                  lees_omni2, vergelijk, nu_utc)

ARCHIEF = 'https://www.ngdc.noaa.gov/dscovr/data/{j}/{m:02d}/'
SPDF = 'https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/'
PATROON = re.compile(r'(oe_(f1m|m1m)_dscovr_s(\d{8})\d{6}_e\d{14}_p(\d{14})_pub\.nc\.gz)')


def bestanden(jaar, maand, log):
    url = ARCHIEF.format(j=jaar, m=maand)
    html = haal(url).decode('utf-8', 'replace')
    gekozen = {}
    for naam, soort, dag, proc in PATROON.findall(html):
        sleutel = (soort, dag)
        if sleutel not in gekozen or proc > gekozen[sleutel][1]:
            gekozen[sleutel] = (naam, proc)
    log['archief_url'] = url
    log['bestanden_gevonden'] = len(gekozen)
    return url, {k: v[0] for k, v in gekozen.items()}


def lees_nc(data, velden, log):
    import netCDF4
    import numpy as np
    ds = netCDF4.Dataset('mem', memory=gzip.decompress(data))
    try:
        log.setdefault('variabelen', sorted(ds.variables.keys()))
        tv = ds.variables['time']
        eenheid = getattr(tv, 'units', 'milliseconds since 1970-01-01')
        t = np.asarray(tv[:], dtype='float64')
        deler = 1000.0 if 'milli' in eenheid else 1.0
        basis = dt.datetime(1970, 1, 1)
        kol = {}
        for doel, bron in velden.items():
            if bron in ds.variables:
                x = ds.variables[bron][:]
                kol[doel] = np.ma.filled(np.ma.masked_invalid(np.ma.asarray(x, dtype='float64')), np.nan)
        kw = None
        if 'overall_quality' in ds.variables:
            kw = np.ma.filled(np.ma.asarray(ds.variables['overall_quality'][:], dtype='float64'), 0)
        rijen = []
        for i in range(len(t)):
            if kw is not None and kw[i] != 0:
                continue
            k = (basis + dt.timedelta(seconds=float(t[i]) / deler)).replace(second=0, microsecond=0)
            rijen.append((k, {d: float(kol[d][i]) for d in kol}))
        return rijen
    finally:
        ds.close()


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else '2025-06'
    jaar, maand = int(arg[:4]), int(arg[5:7])
    log = {'maand': arg, 'run': nu_utc().isoformat(timespec='seconds') + 'Z', 'fouten': []}
    uit_pad = os.path.join(DATA, f'toets-archief-{arg}.json')
    try:
        url, lijst = bestanden(jaar, maand, log)
        minuten = {}
        dagen = sorted({d for (_, d) in lijst})
        log['dagen'] = len(dagen)
        for (soort, dag), naam in sorted(lijst.items()):
            try:
                ruw = haal(url + naam, timeout=180)
                if soort == 'f1m':
                    rijen = lees_nc(ruw, {'v': 'proton_speed', 'n': 'proton_density', 't': 'proton_temperature'}, log)
                    for k, r in rijen:
                        o = minuten.setdefault(k, {})
                        for f in ('v', 'n', 't'):
                            if f in r:
                                zet(o, f, r[f])
                else:
                    rijen = lees_nc(ruw, {'b': 'bt', 'by': 'by_gsm', 'bz': 'bz_gsm', 'bx': 'bx_gse'}, log)
                    for k, r in rijen:
                        o = minuten.setdefault(k, {})
                        for f in ('b', 'by', 'bz'):
                            if f in r:
                                zet(o, f, r[f])
                        if 'bx' in r:
                            zet(o, 'br', -r['bx'])
            except Exception as e:
                log['fouten'].append(f'{naam}: {e}')
        log['minuten'] = len(minuten)
        log['pieken_verwijderd'] = ontpiek(minuten)
        eigen = {uursleutel(u): r for u, r in per_uur(verschuif(minuten)).items()
                 if u.year == jaar and u.month == maand}
        omni = lees_omni2(haal(SPDF + f'omni2_{jaar}.dat').decode('ascii', 'replace'))
        omni = {k: r for k, r in omni.items() if k.startswith(f'{jaar}-{maand:02d}')}
        log['uren_eigen'] = len(eigen)
        log['uren_omni'] = len(omni)
        log['vergelijking'] = vergelijk(eigen, omni)
        # reeksen voor een grafiek: snelheid en Bz per uur, eigen naast OMNI
        log['reeks'] = {k: {'eigen': {f: eigen[k].get(f) for f in ('v', 'n', 'bz')},
                            'omni': {f: omni.get(k, {}).get(f) for f in ('v', 'n', 'bz')}}
                        for k in sorted(eigen)}
    except Exception as e:
        log['fouten'].append(f'{type(e).__name__}: {e}')
        traceback.print_exc()
    schrijf(uit_pad, log)
    samen = {k: v for k, v in log.items() if k not in ('reeks', 'variabelen')}
    import json
    print(json.dumps(samen, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
