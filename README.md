# zonnestroom

Zonnewind en heliosfeerdata voor de Zonnestroom-kalender.

Deze repository draait zichzelf. Elk uur haalt een GitHub-taak de realtime metingen van NOAA op
(zonnewind en interplanetair magneetveld bij L1, plus Kp), haalt er storingen uit,
verschuift de tijden naar aankomst bij de Aarde en slaat per uur een gemiddelde op.
Eens per dag haalt hij de officiële NASA OMNI-reeks op en vergelijkt die met de eigen reeks.

## Wat waar staat

| Bestand | Inhoud |
| --- | --- |
| `data/eigen/JJJJ.json` | eigen uurreeks, voorlopig. Sleutel `JJJJ-MM-DDTUU` (UTC) |
| `data/omni/JJJJ.json` | officiële NASA OMNI, zelfde vorm |
| `data/live/minuten.json` | laatste 7 dagen per minuut, al verschoven naar de Aarde |
| `data/toets.json` | eigen reeks tegen OMNI, waar beide bestaan |
| `data/toets-archief-JJJJ-MM.json` | eenmalige toets op archiefdata (handmatig starten) |
| `data/status.json` | wat er bij de laatste run gebeurde |

Velden: `v` km/s, `n` cm⁻³, `t` K, `b` nT (|B|), `by` en `bz` nT (GSM), `br` nT (≈ −Bx GSE),
`kp`, `dst` nT (alleen OMNI), `nmin` aantal minuten in het uur, `bron` satelliet.

## Bewerking

1. Alleen de actieve satelliet met kwaliteitsvlag 0.
2. Waarden buiten fysische grenzen eruit.
3. Losse pieken eruit: een minuut die sterk afwijkt van de 11 minuten eromheen en waarna de waarden niet op dat niveau blijven. Schokfronten blijven staan.
4. Tijd verschoven met (1,5 miljoen km − 14 aardstralen) / v.
5. Uurgemiddelde vanaf 20 minuten aan metingen.

OMNI doet meer (meerdere satellieten, schuin front, onderlinge ijking). Daarom houden we
OMNI leidend en vergelijken we continu.

## Bronnen

- NOAA SWPC Real Time Solar Wind: `services.swpc.noaa.gov/json/rtsw/`
- NOAA planetaire Kp: `services.swpc.noaa.gov/products/noaa-planetary-k-index.json`
- NASA OMNI2: `spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/` (King en Papitashvili)
- DSCOVR-archief voor de toets: `www.ngdc.noaa.gov/dscovr/data/`

Gegevens van NOAA en NASA zijn publiek domein.
