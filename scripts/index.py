"""Lange reeksen van zonne- en aardactiviteit, eens per dag bijgewerkt.

GFZ Potsdam   Kp, Ap, zonnevlekgetal en F10.7 per dag, 1932 tot gisteren
SILSO Brussel zonnevlekgetal per dag vanaf 1818, per maand vanaf 1749
SILSO         groepsgetal per jaar (Hoyt & Schatten), vanaf 1610
NOAA NCEI     aa-index per dag, 1868 tot 2010 (Ap-achtig, voor de jaren voor 1932)

Uitvoer:
  data/index/dag.json   per dag vanaf 1818: kpmax, kpsum (x10), ap, isn, f107 (x10), aa
                        als base64 uint16, 65535 = geen meting (zelfde vorm als de pagina kent)
  data/index/lang.json  maand- en jaarreeks van het zonnevlekgetal
  data/index/log.json   wat er gebeurde
Een bron die faalt laat de vorige uitvoer staan.
"""
import base64
import datetime as dt
import os
import struct

from kern import DATA, haal, lees, schrijf, nu_utc

GFZ = 'https://kp.gfz.de/app/files/Kp_ap_Ap_SN_F107_since_1932.txt'
SN_DAG = 'https://www.sidc.be/SILSO/DATA/SN_d_tot_V2.0.txt'
SN_MAAND = 'https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt'
GN_JAAR = 'https://www.sidc.be/SILSO/DATA/GroupNumber/GNhs_y.txt'
AA = 'https://www.ngdc.noaa.gov/stp/space-weather/geomagnetic-data/AA_INDEX/aaindex'

START = dt.date(1818, 1, 1)
LEEG = 65535
MAP = os.path.join(DATA, 'index')


def regels(data):
    for r in data.decode('latin-1').splitlines():
        r = r.strip()
        if r and not r.startswith(('#', '%', ';')):
            yield r


def voorbeeld(data, n=4):
    return list(regels(data))[:n]


def gfz(data):
    """YYYY MM DD days days_m Bsr dB Kp1..8 ap1..8 Ap SN F10.7obs F10.7adj D"""
    uit = {}
    for r in regels(data):
        p = r.split()
        if len(p) < 27:
            continue
        d = dt.date(int(p[0]), int(p[1]), int(p[2]))
        kp = [float(x) for x in p[7:15]]
        ok = [k for k in kp if k >= 0]
        ap, sn, f = int(float(p[23])), int(float(p[24])), float(p[25])
        uit[d] = {
            'kpmax': round(max(ok) * 10) if ok else LEEG,
            'kpsum': round(sum(ok) * 10) if len(ok) == 8 else LEEG,
            'ap': ap if ap >= 0 else LEEG,
            'isn': sn if sn >= 0 else LEEG,
            'f107': round(f * 10) if f > 0 else LEEG,
        }
    if len(uit) < 30000:
        raise ValueError(f'te weinig regels uit GFZ ({len(uit)})')
    return uit


def silso_dag(data):
    """YYYY MM DD decjaar SN std nobs def"""
    uit = {}
    for r in regels(data):
        p = r.replace(';', ' ').split()
        if len(p) < 5:
            continue
        sn = float(p[4])
        if sn >= 0:
            uit[dt.date(int(p[0]), int(p[1]), int(p[2]))] = round(sn)
    if len(uit) < 50000:
        raise ValueError(f'te weinig dagen uit SILSO ({len(uit)})')
    return uit


def silso_maand(data):
    """YYYY MM decjaar SN std nobs def"""
    uit = {}
    for r in regels(data):
        p = r.replace(';', ' ').split()
        if len(p) >= 4 and float(p[3]) >= 0:
            uit[(int(p[0]), int(p[1]))] = round(float(p[3]), 1)
    if len(uit) < 3000:
        raise ValueError(f'te weinig maanden uit SILSO ({len(uit)})')
    return uit


def groepsgetal(data):
    """Jaar (soms met .5) en groepsgetal; eerste twee getallen per regel."""
    uit = {}
    for r in regels(data):
        p = r.replace(';', ' ').replace(',', ' ').split()
        try:
            j, g = float(p[0]), float(p[1])
        except (ValueError, IndexError):
            continue
        if 1500 < j < 2100 and g >= 0:
            uit[int(j)] = g
    if len(uit) < 300:
        raise ValueError(f'te weinig jaren groepsgetal ({len(uit)})')
    return uit


def aa_dag(data):
    """NGDC aaindex, vaste kolommen: 1-4 jaar, 5-6 maand, 7-8 dag, 9-32 acht 3-uurswaarden (I3),
    33-37 daggemiddelde (F5.2, loopt over boven 99,99). Daarom zelf middelen over de acht 3-uurswaarden."""
    uit = {}
    for r in data.decode('latin-1').splitlines():
        if len(r) < 32 or not r[:8].isdigit():
            continue
        try:
            d = dt.date(int(r[0:4]), int(r[4:6]), int(r[6:8]))
            v = [int(r[8 + 3 * k:11 + 3 * k]) for k in range(8)]
        except ValueError:
            continue
        if all(0 <= x < 1000 for x in v):
            uit[d] = round(sum(v) / 8)
    if len(uit) < 20000:
        raise ValueError(f'te weinig dagen aa ({len(uit)})')
    return uit


def b64(waarden):
    return base64.b64encode(struct.pack(f'<{len(waarden)}H', *[min(max(int(v), 0), LEEG) for v in waarden])).decode()


def main(status=None):
    nu = nu_utc()
    log = {'run': nu.isoformat(timespec='seconds') + 'Z', 'bronnen': {}, 'fouten': []}
    bron = {}
    for naam, url, lezer in (('gfz', GFZ, gfz), ('sn_dag', SN_DAG, silso_dag), ('sn_maand', SN_MAAND, silso_maand),
                             ('gn_jaar', GN_JAAR, groepsgetal), ('aa', AA, aa_dag)):
        data = None
        try:
            data = haal(url, timeout=120)
            bron[naam] = lezer(data)
            ks = sorted(bron[naam])
            log['bronnen'][naam] = {'url': url, 'van': str(ks[0]), 'tot': str(ks[-1]), 'aantal': len(ks)}
        except Exception as e:
            log['fouten'].append(f'{naam}: {type(e).__name__}: {e}')
            log['bronnen'][naam] = {'url': url, 'fout': str(e)[:200], 'voorbeeld': voorbeeld(data) if data else None}

    vorige = lees(os.path.join(MAP, 'dag.json'), {})
    if 'gfz' in bron or 'sn_dag' in bron:
        g, s, a = bron.get('gfz', {}), bron.get('sn_dag', {}), bron.get('aa', {})
        eind = max([*g, *s]) if (g or s) else START
        n = (eind - START).days + 1
        kol = {k: [LEEG] * n for k in ('kpmax', 'kpsum', 'ap', 'isn', 'f107', 'aa')}
        for i in range(n):
            d = START + dt.timedelta(days=i)
            if d in g:
                for k in ('kpmax', 'kpsum', 'ap', 'f107'):
                    kol[k][i] = g[d][k]
            # SILSO is de bron; GFZ draagt de voorlopige waarden van de laatste weken
            kol['isn'][i] = s.get(d, g[d]['isn'] if d in g else LEEG)
            if d in a:
                kol['aa'][i] = a[d]
        uit = {'start': str(START), 'n': n, 'end': str(eind), 'bron': 'GFZ Potsdam (Kp, Ap, F10.7), SILSO (zonnevlekken), NOAA NCEI (aa)'}
        paren = [(kol['ap'][i], kol['aa'][i]) for i in range(n) if kol['ap'][i] != LEEG and kol['aa'][i] != LEEG]
        if len(paren) > 1000:
            uit['aa_naar_ap'] = round(sum(p for p, _ in paren) / sum(q for _, q in paren), 3)
        uit.update({k: b64(v) for k, v in kol.items()})
        if not a and vorige.get('aa') and vorige.get('start') == uit['start']:
            # aa verandert niet meer (eindigt 2010); oude kolom hergebruiken, aangevuld met lege waarden
            oud = list(struct.unpack(f'<{vorige["n"]}H', base64.b64decode(vorige['aa'])))
            uit['aa'] = b64((oud + [LEEG] * n)[:n])
        schrijf(os.path.join(MAP, 'dag.json'), uit)
        log['dag'] = {'van': uit['start'], 'tot': uit['end'], 'dagen': n}

    lang = lees(os.path.join(MAP, 'lang.json'), {})
    if 'sn_maand' in bron:
        m = bron['sn_maand']
        ks = sorted(m)
        j0, m0 = ks[0]
        n = (ks[-1][0] - j0) * 12 + ks[-1][1] - m0 + 1
        rij = [None] * n
        for (j, mm), v in m.items():
            rij[(j - j0) * 12 + mm - m0] = v
        lang['maand'] = {'start': f'{j0}-{m0:02d}', 'sn': rij, 'bron': 'SILSO, maandgemiddelde, versie 2'}
    if 'gn_jaar' in bron and 'sn_maand' in bron:
        gn = bron['gn_jaar']
        jaarsn = {}
        for (j, _), v in bron['sn_maand'].items():
            jaarsn.setdefault(j, []).append(v)
        overlap = [(gn[j], sum(v) / len(v)) for j, v in jaarsn.items() if j in gn and len(v) == 12 and 1850 <= j <= 1990]
        som_g = sum(g for g, _ in overlap)
        factor = sum(s for _, s in overlap) / som_g if som_g else None
        if factor:
            j0 = min(gn)
            j1 = min(jaarsn) - 1
            lang['jaar'] = {'start': j0, 'sn': [round(gn[j] * factor, 1) if j in gn else None for j in range(j0, j1 + 1)],
                            'factor': round(factor, 3),
                            'bron': f'groepsgetal Hoyt & Schatten, geschaald naar SILSO v2 (x{factor:.2f}, gefit 1850-1990)'}
            log['jaar'] = {'van': j0, 'tot': j1, 'factor': round(factor, 3), 'jaren_overlap': len(overlap)}
    if lang:
        schrijf(os.path.join(MAP, 'lang.json'), lang)
    schrijf(os.path.join(MAP, 'log.json'), log)
    if status is not None:
        status['index'] = 'in orde' if not log['fouten'] else f'{len(log["fouten"])} bron(nen) mislukt'
    return log


if __name__ == '__main__':
    import json
    print(json.dumps(main(), indent=1, ensure_ascii=False))
