"""Dagelijkse controle van de eigen reeks, los van welke satelliet gelijk heeft.

1. Kp van NOAA (snelle schatting) naast Kp van GFZ Potsdam (de officiële bron, eigen stationsnet).
2. Natuurkundige samenhang: de koppeling zonnewind-magnetosfeer (Newell) hoort met Kp mee te lopen.
   Valt dat verband weg, of zijn er uren met storm zonder aandrijving (of andersom), dan klopt er iets niet.
3. Overeenstemming tussen de satellieten: hoe vaak lopen ze verder uiteen dan normaal.
Uitvoer: data/controle.json
"""
import datetime as dt
import math
import os

from kern import DATA, haal, lees, schrijf, nu_utc

GFZ = 'https://kp.gfz.de/app/files/Kp_ap_nowcast.txt'
DAGEN = 30
DREMPEL_SPREIDING = {'v': 40, 'b': 1.5, 'bz': 2.0, 'n': 3.0}   # uurwaarden die als 'uiteen' tellen


def gfz_kp(log):
    tekst = haal(GFZ, timeout=60).decode('utf-8', 'replace')
    regels = [r for r in tekst.splitlines() if r.strip() and not r.startswith('#')]
    log['gfz_voorbeeld'] = regels[-1][:90] if regels else None
    uit = {}
    for r in regels:
        w = r.split()
        try:
            j, m, d, uur = int(w[0]), int(w[1]), int(w[2]), float(w[3])
            kp = float(w[7])
        except (ValueError, IndexError):
            continue
        if kp < 0:
            continue
        t = dt.datetime(j, m, d) + dt.timedelta(hours=uur)
        for h in range(3):
            uit[(t + dt.timedelta(hours=h)).strftime('%Y-%m-%dT%H')] = kp
    return uit


def newell(r):
    try:
        v, by, bz = r['v'], r['by'], r['bz']
    except KeyError:
        return None
    bt = math.hypot(by, bz)
    th = math.atan2(by, bz)
    return v ** (4 / 3) * bt ** (2 / 3) * abs(math.sin(th / 2)) ** (8 / 3)


def correlatie(paren):
    n = len(paren)
    if n < 10:
        return None
    a = [p[0] for p in paren]
    b = [p[1] for p in paren]
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return round(sum((x - ma) * (y - mb) for x, y in paren) / (sa * sb), 3) if sa and sb else None


def main(status=None):
    nu = nu_utc()
    begin = (nu - dt.timedelta(days=DAGEN)).strftime('%Y-%m-%dT%H')
    eigen = {}
    for jaar in sorted({nu.year, (nu - dt.timedelta(days=DAGEN)).year}):
        eigen.update(lees(os.path.join(DATA, 'eigen', f'{jaar}.json'), {}))
    eigen = {k: r for k, r in eigen.items() if k >= begin}
    log = {'run': nu.isoformat(timespec='seconds') + 'Z', 'periode': [begin, nu.strftime('%Y-%m-%dT%H')], 'fouten': []}

    # 1. NOAA-Kp tegen GFZ-Kp
    try:
        gfz = gfz_kp(log)
        paren = [(r['kp'], gfz[k]) for k, r in eigen.items() if 'kp' in r and k in gfz]
        d = [a - b for a, b in paren]
        log['kp_noaa_vs_gfz'] = {
            'uren': len(paren),
            'gem_verschil': round(sum(d) / len(d), 2) if d else None,
            'max_verschil': round(max(d, key=abs), 2) if d else None,
            'meer_dan_een_derde_kp': sum(1 for x in d if abs(x) > 0.34),
            'correlatie': correlatie(paren)}
    except Exception as e:
        log['fouten'].append(f'GFZ: {e}')

    # 2. Koppeling tegen Kp, per blok van 3 uur (Kp is een 3-uurs index)
    blok = {}
    for k, r in eigen.items():
        c = newell(r)
        if c is None or 'kp' not in r:
            continue
        t = dt.datetime.strptime(k, '%Y-%m-%dT%H')
        sleutel = t.replace(hour=t.hour - t.hour % 3).strftime('%Y-%m-%dT%H')
        blok.setdefault(sleutel, {'c': [], 'kp': r['kp']})['c'].append(c)
    rijen = sorted((k, sum(b['c']) / len(b['c']), b['kp']) for k, b in blok.items())
    if rijen:
        cs = sorted(c for _, c, _ in rijen)
        p20, p95 = cs[int(0.2 * (len(cs) - 1))], cs[int(0.95 * (len(cs) - 1))]
        log['koppeling_vs_kp'] = {
            'blokken': len(rijen),
            'correlatie': correlatie([(c ** 0.5, kp) for _, c, kp in rijen]),
            'storm_zonder_aandrijving': [k for k, c, kp in rijen if kp >= 5 and c <= p20],
            'aandrijving_zonder_reactie': [k for k, c, kp in rijen if c >= p95 and kp <= 1]}

    # 3. Satellieten onderling
    uiteen, met_spreiding = {}, 0
    for k, r in eigen.items():
        s = r.get('spreiding')
        if not s:
            continue
        met_spreiding += 1
        for f, grens in DREMPEL_SPREIDING.items():
            if s.get(f, 0) > grens:
                uiteen.setdefault(f, []).append(k)
    log['satellieten'] = {'uren_met_meerdere': met_spreiding,
                          'drempels': DREMPEL_SPREIDING,
                          'uiteen': {f: {'uren': len(v), 'laatste': v[-5:]} for f, v in uiteen.items()}}

    # samenvattend oordeel
    alarm = []
    kv = log.get('kp_noaa_vs_gfz', {})
    if kv.get('uren') and kv.get('meer_dan_een_derde_kp', 0) > 0.2 * kv['uren']:
        alarm.append('NOAA-Kp wijkt vaak af van GFZ')
    kk = log.get('koppeling_vs_kp', {})
    if kk.get('correlatie') is not None and kk['correlatie'] < 0.3:
        alarm.append('zonnewind en Kp lopen niet samen op')
    if kk.get('storm_zonder_aandrijving'):
        alarm.append('storm zonder aandrijving in de zonnewind')
    log['oordeel'] = 'in orde' if not alarm else 'nakijken: ' + '; '.join(alarm)
    schrijf(os.path.join(DATA, 'controle.json'), log)
    if status is not None:
        status['controle'] = log['oordeel']
    return log


if __name__ == '__main__':
    import json
    print(json.dumps(main(), indent=1, ensure_ascii=False))
