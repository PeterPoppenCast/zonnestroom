"""Schumann-resonantie: de drie live beelden van het observatorium in Tomsk (Siberië).

shm.jpg  spectrogram (frequentie tegen tijd, kleur = sterkte)
srf.jpg  frequentie van de eerste vier tonen
sra.jpg  amplitude van de eerste vier tonen

Alles gaat naar de aparte tak 'beelden', zodat main schoon blijft:
  schumann/<naam>.jpg                  het nieuwste beeld, elk uur vervangen
  schumann/archief/<JJJJ-MM-DD>/...    één set per dag, blijft staan
Tomsk toont een paar dagen per beeld. Eén set per dag dekt dus alles af.
Uit dat archief leest digitaliseer.py later de cijferreeks voor de tijdlijn.
Wat er gebeurde staat in data/schumann.json.
"""
import os
import subprocess
import tempfile

from kern import DATA, ROOT, haal, lees, schrijf, nu_utc

BRON = 'http://sosrff.tsu.ru/new/'
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


def main(status=None):
    nu = nu_utc()
    dag = nu.strftime('%Y-%m-%d')
    log = {'run': nu.isoformat(timespec='seconds') + 'Z', 'bron': BRON, 'beelden': {}, 'fouten': []}
    vorige = lees(os.path.join(DATA, 'schumann.json'), {})
    archief = list(vorige.get('archief', []))
    nieuw = {}
    with tempfile.TemporaryDirectory() as tmp:
        for naam, wat in BEELDEN.items():
            try:
                data = haal(BRON + naam, timeout=60)
                if not data.startswith(b'\xff\xd8'):
                    raise ValueError('geen JPEG ontvangen')
                pad = os.path.join(tmp, naam)
                with open(pad, 'wb') as f:
                    f.write(data)
                nieuw[naam] = git('hash-object', '-w', pad)
                log['beelden'][naam] = {'wat': wat, 'bytes': len(data), 'opgehaald': log['run']}
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
    schrijf(os.path.join(DATA, 'schumann.json'), log)
    if status is not None:
        status['schumann'] = 'in orde' if nieuw and not log['fouten'] else ('deels' if nieuw else 'niet bereikbaar')
    return log


if __name__ == '__main__':
    import json
    print(json.dumps(main(), indent=1, ensure_ascii=False))
