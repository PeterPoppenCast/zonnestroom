"""NASA Wind, snelle Key Parameter-bestanden (k0, 92 seconden), een paar dagen vertraging.

Plasma: wi_k0_swe_JJJJMMDD_vNN.cdf   Veld: wi_k0_mfi_JJJJMMDD_vNN.cdf
De variabelnamen van het plasma worden gezocht uit een lijst kandidaten en gelogd,
zodat een afwijkende naam in status.json zichtbaar wordt.
"""
import datetime as dt
import math
import os
import re
import subprocess
import sys
import tempfile

from kern import haal, zet

BASIS = 'https://cdaweb.gsfc.nasa.gov/pub/data/wind/'
RE_KM = 6371.0
KB, MP = 1.380649e-23, 1.67262192e-27


def cdflib_laden():
    try:
        import cdflib  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'cdflib'], check=True)
    import cdflib
    return cdflib


def lijst(soort, jaar):
    url = f'{BASIS}{soort[:3]}/{soort}/{jaar}/'
    namen = re.findall(r'(wi_k0_' + soort[:3] + r'_(\d{8})_v(\d+)\.cdf)', haal(url, timeout=60).decode('utf-8', 'replace'))
    beste = {}
    for naam, dag, versie in namen:
        if dag not in beste or int(versie) > beste[dag][1]:
            beste[dag] = (naam, int(versie))
    return url, {d: v[0] for d, v in beste.items()}


def zoek(namen, kandidaten):
    lager = {n.lower(): n for n in namen}
    for k in kandidaten:
        if k.lower() in lager:
            return lager[k.lower()]
    return None


def lees_cdf(cdflib, data):
    with tempfile.NamedTemporaryFile(suffix='.cdf', delete=False) as f:
        f.write(data)
        pad = f.name
    try:
        c = cdflib.CDF(pad)
        info = c.cdf_info()
        def veld(naam):
            x = getattr(info, naam, None)
            if x is None and isinstance(info, dict):
                x = info.get(naam)
            return list(x or [])
        namen = veld('zVariables') + veld('rVariables')
        tijden = cdflib.cdfepoch.to_datetime(c.varget('Epoch'))
        return c, namen, [t.astype('datetime64[s]').astype(dt.datetime) for t in tijden]
    finally:
        os.unlink(pad)


def goed(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or abs(x) > 1e30) else x


def minuten(dagen, log):
    """Haalt de laatste `dagen` dagen Wind k0 op. Geeft {minuut: {veld: waarde, 'x_km': afstand}}."""
    cdflib = cdflib_laden()
    nu = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    begin = (nu - dt.timedelta(days=dagen)).strftime('%Y%m%d')
    m, gebruikt = {}, {'swe': 0, 'mfi': 0}
    for soort in ('swe_k0', 'mfi_k0'):
        bestanden = {}
        for jaar in sorted({nu.year, (nu - dt.timedelta(days=dagen)).year}):
            url, b = lijst(soort, jaar)
            bestanden.update({d: url + n for d, n in b.items()})
        for dag in sorted(d for d in bestanden if d >= begin):
            try:
                c, namen, tijden = lees_cdf(cdflib, haal(bestanden[dag], timeout=120))
            except Exception as e:
                log.setdefault('fouten', []).append(f'{soort} {dag}: {e}')
                continue
            log.setdefault('variabelen', {})[soort] = sorted(namen)
            if soort == 'swe_k0':
                nv = zoek(namen, ['Np', 'N', 'Np_nonlin', 'DENSITY'])
                vv = zoek(namen, ['V_GSE', 'V_GSM', 'VGSE', 'V_GSE_p'])
                wv = zoek(namen, ['THERMAL_SPD', 'W', 'Vth', 'W_p'])
                pv = zoek(namen, ['SC_pos_gse', 'PGSE', 'SC_POS_GSE'])
                log['swe_gekozen'] = {'dichtheid': nv, 'snelheid': vv, 'thermisch': wv, 'positie': pv}
                N = c.varget(nv) if nv else None
                V = c.varget(vv) if vv else None
                W = c.varget(wv) if wv else None
                P = c.varget(pv) if pv else None
                for i, t in enumerate(tijden):
                    k = t.replace(second=0, microsecond=0)
                    o = m.setdefault(k, {})
                    if V is not None:
                        comp = [goed(x) for x in V[i]]
                        if None not in comp:
                            zet(o, 'v', math.sqrt(sum(x * x for x in comp)))
                    if N is not None:
                        zet(o, 'n', goed(N[i]))
                    if W is not None:
                        w = goed(W[i])
                        if w:
                            zet(o, 't', MP * (w * 1e3) ** 2 / (2 * KB))
                    if P is not None:
                        x = goed(P[i][0])
                        if x and x > 0:
                            o['x_km'] = x * (RE_KM if x < 1000 else 1)
                gebruikt['swe'] += 1
            else:
                bv = zoek(namen, ['BF1'])
                gv = zoek(namen, ['BGSMc'])
                ev = zoek(namen, ['BGSEc'])
                pv = zoek(namen, ['PGSE'])
                qv = zoek(namen, ['DQF'])
                log['mfi_gekozen'] = {'sterkte': bv, 'gsm': gv, 'gse': ev, 'positie': pv, 'kwaliteit': qv}
                B = c.varget(bv) if bv else None
                G = c.varget(gv) if gv else None
                E = c.varget(ev) if ev else None
                P = c.varget(pv) if pv else None
                Q = c.varget(qv) if qv else None
                for i, t in enumerate(tijden):
                    if Q is not None and goed(Q[i]) not in (0.0, None):
                        continue
                    k = t.replace(second=0, microsecond=0)
                    o = m.setdefault(k, {})
                    if B is not None:
                        zet(o, 'b', goed(B[i]))
                    if G is not None:
                        zet(o, 'by', goed(G[i][1]))
                        zet(o, 'bz', goed(G[i][2]))
                    if E is not None and goed(E[i][0]) is not None:
                        zet(o, 'br', -goed(E[i][0]))
                    if P is not None and 'x_km' not in o:
                        x = goed(P[i][0])
                        if x and x > 0:
                            o['x_km'] = x * RE_KM
                gebruikt['mfi'] += 1
    # minuten zonder positie krijgen die van de dichtstbijzijnde minuut met positie
    pos = sorted((k, o['x_km']) for k, o in m.items() if 'x_km' in o)
    if pos:
        j = 0
        for k in sorted(m):
            while j + 1 < len(pos) and abs((pos[j + 1][0] - k).total_seconds()) <= abs((pos[j][0] - k).total_seconds()):
                j += 1
            m[k].setdefault('x_km', pos[j][1])
        log['afstand_km'] = round(pos[-1][1])
    m = {k: o for k, o in m.items() if any(f in o for f in ('v', 'n', 't', 'b', 'by', 'bz', 'br'))}
    log['dagbestanden'] = gebruikt
    log['minuten'] = len(m)
    if m:
        log['tot'] = max(m).isoformat() + 'Z'
    return m
