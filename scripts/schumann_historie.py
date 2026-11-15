"""Schumann-resonantie in het verleden, twee bronnen.

1. Nagycenk (Hongarije), wetenschappelijke reeksen op Zenodo, CC BY 4.0
   Sátori, Bozóki e.a., record 13220677: f1 1994-2014, f2 1994-2009, f3 2000-2009.
   Vorm: per maand de gemiddelde dagkromme (24 uurwaarden, UT), geen losse dagen.
   Eenmalig opgehaald. De ruwe bestanden gaan ook naar data/schumann/bron/, zodat de opbouw te controleren is.
2. Tomsk via het Internet Archive: oude kopieën van srf.jpg en sra.jpg, uitgelezen met digitaliseer.py.
   Elk beeld dekt bijna drie dagen. We pakken ongeveer één kopie per twee dagen.
   Dit gaat in porties: elke uurlijkse run werkt een paar minuten aan de stapel, tot hij leeg is.

Uitvoer:
  data/schumann/archief.json   Tomsk per uur uit het archief: t0 (Unix-uur), n, velden als base64 int16
                               (frequentie x1000, amplitude x100; -32768 = leeg)
  data/schumann/nagycenk.json  Nagycenk zoals de bron hem geeft
  data/schumann/historie.json  voortgang en fouten
"""
import base64
import gzip
import datetime as dt
import json
import os
import re
import struct
import time
import urllib.error
import urllib.parse

from kern import DATA, haal, lees, schrijf, nu_utc

MAP = os.path.join(DATA, 'schumann')
ZENODO = 'https://zenodo.org/records/13220677/files/{}?download=1'
NCK = {'f1': 'NCK_f1_1994-2014.txt', 'f2': 'NCK_f2_1994-2009.txt', 'f3': 'NCK_f3_2000-2009.txt'}
CDX = 'https://web.archive.org/cdx/search/cdx?url={}&output=json&fl=timestamp,digest&filter=statuscode:200&collapse=digest'
RUW = 'https://web.archive.org/web/{}id_/{}'
ADRESSEN = ['http://sosrff.tsu.ru/new/{}', 'https://sos70.ru/provider.php?file={}']
BUDGET_S = 300
VERSIE = 3
BEVROREN = '20250905'  # het oude adres staat sindsdien stil; latere kopieën zijn steeds hetzelfde oude beeld
VELDEN = ['f1', 'f2', 'f3', 'f4', 'a1', 'a2', 'a3', 'a4']
LEEG = -32768


# ---------- Nagycenk ----------

def lees_nck(tekst, veld):
    """Bestand: kop 'year/month 0 UT ... 23 UT', daarna per maand 'JJJJ/MM' en 24 uurwaarden (UT).
    Elke regel is de gemiddelde dagkromme van die maand. Uitvoer: {'JJJJ-MM': [24 waarden]}."""
    lo, hi = {'f1': (6, 10), 'f2': (11, 17), 'f3': (17, 23)}[veld]
    uit = {}
    for r in tekst.splitlines():
        p = r.split()
        if len(p) < 25 or '/' not in p[0]:
            continue
        try:
            j, m = (int(x) for x in p[0].split('/'))
            w = [float(x) for x in p[1:25]]
        except ValueError:
            continue
        uit[f'{j}-{m:02d}'] = [round(v, 4) if lo <= v <= hi else None for v in w]
    # sommige maanden zijn exact gelijk aan een eerdere maand: opvulling in de bron, geen meting
    gezien, dubbel = {}, []
    for k in sorted(uit):
        sleutel = tuple(uit[k])
        if sleutel in gezien:
            dubbel.append([k, gezien[sleutel]])
        else:
            gezien[sleutel] = k
    for k, _ in dubbel:
        del uit[k]
    if dubbel:
        uit['_dubbel'] = dubbel
    return uit


def nagycenk(log):
    pad = os.path.join(MAP, 'nagycenk.json')
    if os.path.exists(pad):
        return
    reeks = {'bron': 'Nagycenk Geophysical Observatory (NCK), Ez-veld; Sátori, Bozóki e.a., Zenodo 10.5281/zenodo.13220677, CC BY 4.0',
             'vorm': 'per maand de gemiddelde dagkromme: 24 uurwaarden in UT'}
    for veld, naam in NCK.items():
        try:
            data = haal(ZENODO.format(naam), timeout=120).decode('latin-1')
            os.makedirs(os.path.join(MAP, 'bron'), exist_ok=True)
            with open(os.path.join(MAP, 'bron', naam), 'w') as f:
                f.write(data)
            waarden = lees_nck(data, veld)
            reeks[veld] = waarden
            ks = sorted(k for k in waarden if not k.startswith('_'))
            log['nagycenk'][veld] = {'regels': len(data.splitlines()), 'waarden': len(waarden),
                                     'van': ks[0] if ks else None, 'tot': ks[-1] if ks else None,
                                     'voorbeeld': data.splitlines()[:6]}
        except Exception as e:
            log['fouten'].append(f'Nagycenk {veld}: {type(e).__name__}: {e}')
    if any(isinstance(reeks.get(v), dict) and reeks[v] for v in NCK):
        schrijf(pad, reeks)


# ---------- Tomsk uit het Internet Archive ----------

def ts_dt(ts):
    return dt.datetime.strptime(ts[:14], '%Y%m%d%H%M%S')


def kopieen(naam):
    uit = []
    for a in ADRESSEN:
        url = a.format(naam)
        try:
            rijen = json.loads(haal(CDX.format(urllib.parse.quote(url, safe='')), timeout=120))
            uit += [(r[0], url) for r in rijen[1:]]
        except Exception:
            continue
    return sorted(set(uit))


def bruikbaar(t, u):
    return not ('sosrff' in u and t[:8] > BEVROREN)


def kies(srf, sra):
    """Nieuwste eerst, telkens minstens twee dagen ertussen; sra binnen zes uur van srf."""
    srf = [(t, u) for t, u in srf if bruikbaar(t, u)]
    sra = [(t, u) for t, u in sra if bruikbaar(t, u)]
    gekozen, vorige = [], None
    sra_t = [(ts_dt(t), t, u) for t, u in sra]
    for t, u in sorted(srf, reverse=True):
        d = ts_dt(t)
        if vorige and (vorige - d).total_seconds() < 2 * 86400:
            continue
        maat = min(sra_t, key=lambda x: abs((x[0] - d).total_seconds()), default=None)
        paar = (maat[1], maat[2]) if maat and abs((maat[0] - d).total_seconds()) < 6 * 3600 else None
        gekozen.append({'srf': [t, u], 'sra': list(paar) if paar else None})
        vorige = d
    return gekozen


def lees_archief(pad):
    a = lees(pad, {})
    if not a.get('n'):
        return {}
    uren = {}
    kol = {k: struct.unpack(f'<{a["n"]}h', base64.b64decode(a[k])) for k in VELDEN if k in a}
    for i in range(a['n']):
        rij = {k: (v[i] / (1000 if k[0] == 'f' else 100)) for k, v in kol.items() if v[i] != LEEG}
        if rij:
            uren[a['t0'] + i] = rij
    return uren


def schrijf_archief(pad, uren):
    if not uren:
        return
    t0, t1 = min(uren), max(uren)
    n = t1 - t0 + 1
    uit = {'t0': t0, 'n': n, 'eenheid': 'uur sinds 1970 (UTC); f x1000 Hz, a x100; -32768 leeg',
           'bron': 'Tomsk (sosrff.tsu.ru / sos70.ru) via het Internet Archive, uitgelezen uit srf.jpg en sra.jpg'}
    for k in VELDEN:
        s = 1000 if k[0] == 'f' else 100
        rij = [LEEG] * n
        for u, r in uren.items():
            if k in r:
                rij[u - t0] = max(-32767, min(32767, round(r[k] * s)))
        uit[k] = base64.b64encode(struct.pack(f'<{n}h', *rij)).decode()
    schrijf(pad, uit)


def tomsk(log, staat):
    import digitaliseer
    if staat.get('versie') != VERSIE:
        staat.update({'versie': VERSIE, 'stapel': [], 'mislukt': {}, 'lijst_op': ''})
    if staat.get('pauze_tot', '') > nu_utc().isoformat():
        log['tomsk'] = {'pauze_tot': staat['pauze_tot'], 'nog_te_doen': len(staat.get('stapel', []))}
        return
    if not staat.get('stapel') or staat.get('lijst_op', '') < (nu_utc() - dt.timedelta(days=30)).strftime('%Y-%m-%d'):
        srf, sra = kopieen('srf.jpg'), kopieen('sra.jpg')
        gedaan = set(staat.get('gedaan', []))
        staat['stapel'] = [k for k in kies(srf, sra) if k['srf'][0] not in gedaan]
        staat['lijst_op'] = nu_utc().strftime('%Y-%m-%d')
        log['kopieen'] = {'srf': len(srf), 'sra': len(sra), 'te_doen': len(staat['stapel'])}
    pad = os.path.join(MAP, 'archief.json')
    uren = lees_archief(pad)
    start, klaar = time.monotonic(), 0
    while staat['stapel'] and time.monotonic() - start < BUDGET_S:
        k = staat['stapel'].pop(0)
        t = k['srf'][0]
        try:
            for soort, sleutel in (('f', 'srf'), ('a', 'sra')):
                if not k.get(sleutel):
                    continue
                ts, url = k[sleutel]
                data = haal(RUW.format(ts, url), timeout=60)
                if data[:2] == b'\x1f\x8b':  # het archief geeft sommige kopieën gezipt terug
                    data = gzip.decompress(data)
                if not data.startswith(b'\xff\xd8'):
                    raise ValueError(f'geen beeld in het archief: {data[:40]!r}')
                r = digitaliseer.lees(data, soort)
                # het archief vult alleen aan; wat er al staat (live) blijft staan
                for u, rij in r['uren'].items():
                    bestaand = uren.setdefault(u, {})
                    for veld, v in rij.items():
                        bestaand.setdefault(veld, v)
            staat.setdefault('gedaan', []).append(t)
            klaar += 1
        except Exception as e:
            tekst = f'{type(e).__name__}: {str(e)[:120]}'
            # het archief remt af (weigert, time-out, 429, 5xx): terug op de stapel en een uur pauze
            if isinstance(e, OSError) and not isinstance(e, urllib.error.HTTPError) or \
                    (isinstance(e, urllib.error.HTTPError) and (e.code == 429 or e.code >= 500)):
                staat['stapel'].insert(0, k)
                staat['pauze_tot'] = (nu_utc() + dt.timedelta(hours=1)).isoformat()
                log.setdefault('afgeremd', tekst)
                break
            staat.setdefault('mislukt', {})[t] = tekst
        time.sleep(5)  # het archief wil rustig aan: ruim onder 15 verzoeken per minuut
    schrijf_archief(pad, uren)
    log['tomsk'] = {'verwerkt_deze_run': klaar, 'nog_te_doen': len(staat['stapel']),
                    'gedaan_totaal': len(staat.get('gedaan', [])), 'mislukt_totaal': len(staat.get('mislukt', {})),
                    'uren_in_archief': len(uren),
                    'van': dt.datetime.utcfromtimestamp(min(uren) * 3600).isoformat() if uren else None}


def main(status=None):
    pad = os.path.join(MAP, 'historie.json')
    staat = lees(pad, {})
    if staat.get('klaar') and staat.get('lijst_op', '') >= (nu_utc() - dt.timedelta(days=30)).strftime('%Y-%m-%d'):
        return staat.get('log', {})
    log = {'run': nu_utc().isoformat(timespec='seconds') + 'Z', 'nagycenk': {}, 'fouten': []}
    try:
        nagycenk(log)
    except Exception as e:
        log['fouten'].append(f'Nagycenk: {e}')
    try:
        tomsk(log, staat)
    except Exception as e:
        log['fouten'].append(f'Tomsk-archief: {type(e).__name__}: {e}')
    staat['klaar'] = not staat.get('stapel')
    staat['log'] = log
    schrijf(pad, staat)
    if status is not None:
        status['schumann_historie'] = log.get('tomsk', {}).get('nog_te_doen', 'fout')
    return log


if __name__ == '__main__':
    print(json.dumps(main(), indent=1, ensure_ascii=False, default=str))
