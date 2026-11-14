"""Schumann-resonantie: de drie live beelden van het observatorium in Tomsk (Siberië).

shm.jpg  spectrogram (frequentie tegen tijd, kleur = sterkte)
srf.jpg  frequentie van de eerste vier tonen
sra.jpg  amplitude van de eerste vier tonen

Alles gaat naar de aparte tak 'beelden', zodat main schoon blijft:
  schumann/<naam>.jpg                  het nieuwste beeld, elk uur vervangen
  schumann/archief/<JJJJ-MM-DD>/...    één set per dag, blijft staan
Tomsk toont een paar dagen per beeld. Eén set per dag dekt dus alles af.
digitaliseer.py leest srf.jpg en sra.jpg uit tot cijfers per uur:
data/schumann/reeks.json, de Schumann-baan op de tijdlijn.
Bron: sos70.ru (de huidige plek van Tomsk). Het oude adres sosrff.tsu.ru/new/
staat sinds 1 september 2025 stil en is alleen nog reserve.
Wat er gebeurde staat in data/schumann.json.
"""
import os
import subprocess
import tempfile

from kern import DATA, ROOT, haal, lees, schrijf, nu_utc

BRONNEN = ['https://sos70.ru/provider.php?file=', 'http://sos70.ru/provider.php?file=', 'http://sosrff.tsu.ru/new/']
BEELDEN = {'shm.jpg': 'spectrogram', 'srf.jpg': 'frequentie', 'sra.jpg': 'amplitude'}
TAK = 'beelden'


def git(*args, invoer=None, env_extra=None, check=True):
    env = dict(os.environ, GIT_AUTHOR_NAME='zonnestroom-bot', GIT_COMMITTER_NAME='zonnestroom-bot',
               GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com',
               GIT_COMMITTER_EMAIL='41898282+github-actions[bot]@users.noreply.github.com')
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(['git', *args], cwd=ROOT, input=invoer, capture_output=True, text=True, env=env, check=check)
    return r.stdout.strip()


def vorige_tip():
    """Laatste commit van de beeldentak, of None als die nog niet bestaat."""
    r = subprocess.run(['git', 'fetch', '-q', '--depth=1', 'origin', f'+refs/heads/{TAK}:refs/remotes/origin/{TAK}'],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        return None
    return git('rev-parse', f'refs/remotes/origin/{TAK}', check=False) or None


def leesuit(ruw, log, nu):
    """srf.jpg en sra.jpg omzetten naar cijfers en bij de reeks voegen."""
    try:
        import digitaliseer
    except Exception as e:
        log['fouten'].append(f'uitlezen: {e}')
        return
    pad = os.path.join(DATA, 'schumann', 'reeks.json')
    reeks = lees(pad, {})
    uren, info = {}, {}
    for naam, soort in (('srf.jpg', 'f'), ('sra.jpg', 'a')):
        if naam not in ruw:
            continue
        try:
            r = digitaliseer.lees(ruw[naam], soort)
            for u, rij in r['uren'].items():
                uren.setdefault(u, {}).update(rij)
            info[naam] = {'datums': r['datums'], 'assen': r['assen'], 'uren': len(r['uren'])}
        except Exception as e:
            log['fouten'].append(f'uitlezen {naam}: {e}')
    if not uren:
        return
    laatste = max(uren)
    leeftijd = nu.timestamp() / 3600 - laatste
    info['laatste_uur_utc'] = __import__('datetime').datetime.utcfromtimestamp(laatste * 3600).isoformat() + 'Z'
    info['achterstand_uren'] = round(leeftijd, 1)
    if leeftijd > 48:
        info['bevroren'] = True
        log['fouten'].append(f'beelden zijn oud: laatste meting {info["laatste_uur_utc"]}')
    log['uitgelezen'] = info
    schrijf(pad, digitaliseer.voeg_toe(reeks, uren))


def main(status=None):
    nu = nu_utc()
    dag = nu.strftime('%Y-%m-%d')
    log = {'run': nu.isoformat(timespec='seconds') + 'Z', 'beelden': {}, 'fouten': []}
    vorige = lees(os.path.join(DATA, 'schumann.json'), {})
    archief = list(vorige.get('archief', []))
    nieuw, ruw = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        for naam, wat in BEELDEN.items():
            try:
                data, fout = None, []
                for bron in BRONNEN:
                    try:
                        d = haal(bron + naam, timeout=60)
                        if not d.startswith(b'\xff\xd8'):
                            raise ValueError('geen JPEG')
                        data = d
                        log['beelden'].setdefault(naam, {})['bron'] = bron + naam
                        break
                    except Exception as e:
                        fout.append(f'{bron}: {e}')
                if data is None:
                    raise ValueError('; '.join(fout))
                ruw[naam] = data
                pad = os.path.join(tmp, naam)
                with open(pad, 'wb') as f:
                    f.write(data)
                nieuw[naam] = git('hash-object', '-w', pad)
                log['beelden'][naam].update({'wat': wat, 'bytes': len(data), 'opgehaald': log['run']})
            except Exception as e:
                log['fouten'].append(f'{naam}: {e}')
                if naam in vorige.get('beelden', {}):
                    log['beelden'][naam] = {**vorige['beelden'][naam], 'verouderd': True}
        if nieuw:
            try:
                tip = vorige_tip()
                index = {'GIT_INDEX_FILE': os.path.join(tmp, 'index')}
                if tip:
                    git('read-tree', tip, env_extra=index)
                for naam, blob in nieuw.items():
                    git('update-index', '--add', '--cacheinfo', f'100644,{blob},schumann/{naam}', env_extra=index)
                if dag not in archief:
                    for naam, blob in nieuw.items():
                        git('update-index', '--add', '--cacheinfo', f'100644,{blob},schumann/archief/{dag}/{naam}', env_extra=index)
                    archief.append(dag)
                boom = git('write-tree', env_extra=index)
                ouder = ['-p', tip] if tip else []
                commit = git('commit-tree', boom, *ouder, '-m', f'Schumann {log["run"]}')
                git('push', '-q', 'origin', f'{commit}:refs/heads/{TAK}')
                log['tak'] = TAK
            except subprocess.CalledProcessError as e:
                log['fouten'].append(f'git: {(e.stderr or str(e))[:300]}')
                archief = list(vorige.get('archief', []))
    log['archief'] = sorted(archief)
    leesuit(ruw, log, nu)
    schrijf(os.path.join(DATA, 'schumann.json'), log)
    if status is not None:
        status['schumann'] = 'in orde' if nieuw and not log['fouten'] else ('deels' if nieuw else 'niet bereikbaar')
    return log


if __name__ == '__main__':
    import json
    print(json.dumps(main(), indent=1, ensure_ascii=False))
