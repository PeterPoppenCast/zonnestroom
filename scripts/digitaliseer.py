"""Leest de Schumann-grafieken uit Tomsk uit tot cijfers per uur.

srf.jpg: frequentie F1 (wit), F2 (geel), F3 (rood), F4 (groen), elk met een eigen as
sra.jpg: amplitude A1 tot A4, zelfde kleuren
Opbouw van beide beelden (1006 x 340 px):
  plotvlak x 71 tot 935: drie dagen van 288 px, dus 12 px per uur
  rasterlijnen y 30, 50 ... 310; elke as heeft zes labels op een rasterlijn
  links: as 1 op y 30-130, as 3 op y 150-250; rechts: as 2 op y 90-190, as 4 op y 210-310
  bovenaan per dag de datum, DD.MM.JJJJ
De assen schalen per beeld mee. Daarom leest dit script de labels zelf
(sjablonen van het pixelfont) en ook de datums. De tijd staat in Tomsk-tijd, UTC+7.
Alleen numpy en Pillow nodig.
"""
import io
import datetime as dt

import numpy as np
from PIL import Image

X0, DAG_PX, UUR_PX = 71, 288, 12
TOMSK_UTC = 7

SJABLONEN = {
    '8': [".###./#...#/#...#/#...#/.###./#...#/#...#/#...#/.###."],
    '0': [".###./#...#/#...#/#...#/#...#/#...#/#...#/#...#/.###."],
    '5': ["#####/#..../#..../####./#...#/....#/....#/#...#/.###."],
    '7': ["#####/....#/...#./...#./..#../..#../.#.../.#.../.#..."],
    '9': [".###./#...#/#...#/#...#/.####/....#/....#/#...#/.###."],
    '4': ["...#./..##./..##./.#.#./.#.#./#..#./#####/...#./...#."],
    '2': [".###./#...#/....#/....#/...#./..#../.#.../#..../#####"],
    '1': ["..#/###/..#/..#/..#/..#/..#/..#/..#", ".#/##/.#/.#/.#/.#/.#/.#/.#"],
    '6': [".###./#...#/#..../#..../####./#...#/#...#/#...#/.###."],
    '3': [".###./#...#/....#/....#/..##./....#/....#/#...#/.###.", "###./...#/...#/...#/.##./...#/...#/...#/###."],
}
_SJ = [(c, np.array([[ch == '#' for ch in r] for r in s.split('/')])) for c, ls in SJABLONEN.items() for s in ls]

# (naam, kant, rasterrijen van de labels, kleur)
ASSEN = {
    1: ('L', [30, 50, 70, 90, 110, 130], 'wit'),
    3: ('L', [150, 170, 190, 210, 230, 250], 'rood'),
    2: ('R', [90, 110, 130, 150, 170, 190], 'geel'),
    4: ('R', [210, 230, 250, 270, 290, 310], 'groen'),
}


def _masker(a):
    return a.max(2) > 110


def _glyphs(m, y0, y1, x0, x1):
    sub = m[y0:y1, x0:x1]
    kol = sub.any(0)
    uit, i, w = [], 0, sub.shape[1]
    while i < w:
        if kol[i]:
            j = i
            while j < w and kol[j]:
                j += 1
            g = sub[:, i:j]
            rr = np.where(g.any(1))[0]
            uit.append((x0 + i, g[rr.min():rr.max() + 1]))
            i = j
        else:
            i += 1
    return uit


def _herken(g):
    if g.shape[0] < 7:
        return '.'
    beste, score = None, 1e9
    for c, s in _SJ:
        h, w = max(s.shape[0], g.shape[0]), max(s.shape[1], g.shape[1])
        A = np.zeros((h, w), bool); A[:g.shape[0], :g.shape[1]] = g
        B = np.zeros((h, w), bool); B[:s.shape[0], :s.shape[1]] = s
        d = (A ^ B).sum() + 3 * abs(s.shape[1] - g.shape[1])
        if d < score:
            beste, score = c, d
    return beste if score <= 8 else '?'


def _getal(m, y, kant):
    x0, x1 = (20, 68) if kant == 'L' else (938, 1000)
    cijfers = ''.join(_herken(g) for _, g in _glyphs(m, y - 5, y + 9, x0, x1) if g.shape[0] >= 7)
    if len(cijfers) < 3 or '?' in cijfers:
        return None
    return float(cijfers[:-2] + '.' + cijfers[-2:])  # labels hebben altijd twee decimalen


def _as(m, nr):
    """Lineaire as: waarde = a + b*y, gefit op de leesbare labels."""
    kant, rijen, _ = ASSEN[nr]
    pts = [(y, v) for y in rijen if (v := _getal(m, y, kant)) is not None]
    if len(pts) < 3:
        return None
    ys, vs = np.array(pts).T
    b, a = np.polyfit(ys, vs, 1)
    rest = np.abs(a + b * ys - vs).max()
    stap = abs(b * 20)
    if b >= 0 or rest > 0.2 * stap:  # labels moeten netjes oplopen naar boven
        return None
    return a, b


def _datums(m):
    """Datum per dag-kolom uit de kop van het beeld."""
    uit = []
    for d in range(3):
        xs = X0 + d * DAG_PX
        cijfers = ''.join(_herken(g) for _, g in _glyphs(m, 2, 22, xs + 40, xs + DAG_PX - 40) if g.shape[0] >= 7)
        try:
            uit.append(dt.date(int(cijfers[4:8]), int(cijfers[2:4]), int(cijfers[0:2])))
        except Exception:
            uit.append(None)
    # ontbrekende datum invullen vanuit een buur
    for i in range(3):
        if uit[i] is None:
            for j in range(3):
                if uit[j] is not None:
                    uit[i] = uit[j] + dt.timedelta(days=i - j)
                    break
    return uit


def _kleur(a, naam):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    if naam == 'wit':
        return (r > 170) & (g > 170) & (b > 170)
    if naam == 'geel':
        return (r > 150) & (g > 150) & (b < np.minimum(r, g) - 50)
    if naam == 'rood':
        return (r > 95) & (r > 2 * g) & (r > 2 * b)
    return (g > 165) & (g - r > 70) & (g - b > 70)  # groen, feller dan het raster


def lees(jpeg_bytes, soort):
    """soort 'f' of 'a'. Geeft {'datums': [...], 'uren': {unix_uur: {'f1': .., ...}}}."""
    a = np.asarray(Image.open(io.BytesIO(jpeg_bytes)).convert('RGB')).astype(int)
    if a.shape[:2] != (340, 1006):
        raise ValueError(f'onverwachte beeldmaat {a.shape[1]}x{a.shape[0]}')
    m = _masker(a)
    datums = _datums(m)
    if any(d is None for d in datums):
        raise ValueError('datums niet leesbaar')
    plot = a[28:313, X0:X0 + 3 * DAG_PX]
    uren = {}
    assen = {}
    for nr, (_, _, kleur) in ASSEN.items():
        ax = _as(m, nr)
        assen[nr] = ax is not None
        if ax is None:
            continue
        msk = _kleur(plot, kleur)
        msk[:105 - 28, 900 - X0:] = False  # logo rechtsboven
        for d in range(3):
            for h in range(24):
                ys = []
                for c in range(d * DAG_PX + h * UUR_PX, d * DAG_PX + (h + 1) * UUR_PX):
                    r = np.where(msk[:, c])[0]
                    if len(r):
                        ys.append(r.mean() + 28)
                if len(ys) < UUR_PX // 2:
                    continue
                v = ax[0] + ax[1] * float(np.median(ys))
                t = dt.datetime.combine(datums[d], dt.time(h)) - dt.timedelta(hours=TOMSK_UTC)
                u = int(t.replace(tzinfo=dt.timezone.utc).timestamp() // 3600)
                uren.setdefault(u, {})[f'{soort}{nr}'] = round(float(v), 3 if soort == 'f' else 2)
    return {'datums': [d.isoformat() for d in datums], 'assen': assen, 'uren': uren}


VELDEN = ['f1', 'f2', 'f3', 'f4', 'a1', 'a2', 'a3', 'a4']


def voeg_toe(reeks, uren):
    """Voeg uurwaarden toe aan de reeks {t0, velden...}. Nieuwe waarden winnen."""
    oud = {}
    if reeks and reeks.get('t0') is not None:
        for i in range(len(reeks.get('f1', []))):
            rij = {k: reeks[k][i] for k in VELDEN if k in reeks and reeks[k][i] is not None}
            if rij:
                oud[reeks['t0'] + i] = rij
    for u, rij in uren.items():
        oud.setdefault(u, {}).update(rij)
    if not oud:
        return reeks or {}
    t0, t1 = min(oud), max(oud)
    uit = {'t0': t0, 'eenheid': 'uur sinds 1970 (UTC)', 'bron': 'Tomsk, uitgelezen uit srf.jpg en sra.jpg'}
    for k in VELDEN:
        uit[k] = [oud.get(t0 + i, {}).get(k) for i in range(t1 - t0 + 1)]
    return uit
