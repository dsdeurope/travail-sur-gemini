"""Local browser interface. Google credentials are handled exclusively by gcloud OAuth."""
import argparse
import base64
import ctypes
from contextlib import contextmanager
from ctypes import wintypes
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT.parent
RUNTIME = OUTPUTS.parent / 'work' / 'google-runtime'
EMAIL = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._%+\-]*@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def safe_error(message):
    message = re.sub(r'(AQ\.[A-Za-z0-9_.\-]+|AIza[A-Za-z0-9_\-]+|ya29\.[A-Za-z0-9_.\-]+)', '[secret masque]', str(message))
    message = re.sub(r'https://accounts\.google\.com/\S+', '[connexion Google]', message)
    return message[-1600:]


class GoogleError(Exception):
    pass


class WindowsVault:
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    def convert(self, value, decrypt=False):
        data = ctypes.create_string_buffer(value)
        incoming = self.Blob(len(value), ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte)))
        outgoing = self.Blob()
        api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
        if not api(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
            raise RuntimeError('Le coffre Windows est indisponible. Relance l’application dans ta session Windows habituelle.')
        try:
            return ctypes.string_at(outgoing.data, outgoing.size)
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))

    def seal(self, value):
        return base64.b64encode(self.convert(value.encode())).decode()

    def open(self, value):
        return self.convert(base64.b64decode(value), True).decode()


class Google:
    def __init__(self):
        self.python = RUNTIME / 'google-cloud-sdk/platform/bundledpython/python.exe'
        self.script = RUNTIME / 'google-cloud-sdk/lib/gcloud.py'
        self.env = dict(os.environ, CLOUDSDK_CONFIG=str(RUNTIME / 'connexion'),
                        CLOUDSDK_CORE_DISABLE_USAGE_REPORTING='true',
                        CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK='true',
                        PYTHONIOENCODING='utf-8')

    def run(self, args, timeout=300):
        if not self.python.exists():
            raise GoogleError('Outil Google absent. Relance Lancer-Gemini.cmd pour le préparer.')
        try:
            result = subprocess.run([str(self.python), str(self.script), *args],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', timeout=timeout,
                env=self.env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            raise GoogleError('Délai dépassé. Clique sur Reprendre ; les ressources existantes seront conservées.') from None
        if result.returncode:
            raise GoogleError(safe_error(result.stderr or 'Google a refusé cette opération.'))
        return result.stdout.strip()

    def data(self, args):
        return json.loads(self.run([*args, '--format=json']) or '[]')


class Manager:
    def __init__(self, db, export, google=None, vault=None):
        self.db, self.export = Path(db), Path(export)
        self.google, self.vault = google or Google(), vault or WindowsVault()
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        with self.connect() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, project TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', phase TEXT NOT NULL DEFAULT 'En attente',
                error TEXT NOT NULL DEFAULT '', secret TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL)''')
            con.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT)')
            con.execute("INSERT OR IGNORE INTO settings VALUES ('paused','0')")
            con.execute("UPDATE accounts SET state='queued',phase='Reprise après interruption' WHERE state='running'")
        self.thread = None

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA synchronous=FULL')
        try:
            with con:
                yield con
        finally:
            con.close()

    def update(self, id, **values):
        allowed = {'state', 'phase', 'error', 'secret'}
        if not set(values) <= allowed:
            raise ValueError('Champ invalide')
        with self.connect() as con:
            con.execute('UPDATE accounts SET ' + ','.join(k + '=?' for k in values) + ',updated=? WHERE id=?',
                        [*values.values(), time.time(), id])

    def add(self, emails):
        if not isinstance(emails, list) or not 1 <= len(emails) <= 100:
            raise ValueError('Ajoute entre 1 et 100 adresses à la fois.')
        normalized = [e.strip().lower() for e in emails if isinstance(e, str)]
        if len(normalized) != len(emails) or any(len(e) > 254 or not EMAIL.fullmatch(e) for e in normalized):
            raise ValueError('Chaque ligne doit contenir uniquement une adresse email valide, sans mot de passe.')
        with self.connect() as con:
            for email in dict.fromkeys(normalized):
                id = uuid.uuid4().hex
                con.execute('INSERT OR IGNORE INTO accounts(id,email,project,created,updated) VALUES(?,?,?,?,?)',
                            (id, email, 'gemini-' + id[:20], time.time(), time.time()))
        self.wake.set()

    def snapshot(self):
        with self.connect() as con:
            rows = [dict(r) for r in con.execute('SELECT id,email,project,state,phase,error,created FROM accounts ORDER BY created')]
            paused = con.execute("SELECT value FROM settings WHERE name='paused'").fetchone()[0] == '1'
        return {'accounts': rows, 'paused': paused, 'ready': sum(r['state'] == 'done' for r in rows)}

    def pause(self, value):
        with self.connect() as con:
            con.execute("UPDATE settings SET value=? WHERE name='paused'", ('1' if value else '0',))
        self.wake.set()

    def retry(self, id):
        with self.connect() as con:
            row = con.execute('SELECT state FROM accounts WHERE id=?', (id,)).fetchone()
            if not row:
                raise ValueError('Compte introuvable')
            if row['state'] != 'error':
                return
            con.execute("UPDATE accounts SET state='queued',error='',phase='Reprise demandée' WHERE id=?", (id,))
        self.wake.set()

    def export_keys(self):
        with self.lock:
            with self.connect() as con:
                encrypted = [r[0] for r in con.execute("SELECT secret FROM accounts WHERE secret!='' ORDER BY created")]
            keys = [self.vault.open(item) for item in encrypted]
            if any(not k.startswith('AQ') or '\n' in k or '\r' in k for k in keys):
                raise RuntimeError('Une clé ne correspond pas au format AQ attendu.')
            data = ''.join(k + '\n' for k in keys).encode()
            temporary = self.export.with_suffix('.tmp')
            with open(temporary, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.export)
            return data

    def process(self, row):
        id, email, project = row['id'], row['email'], row['project']
        def phase(label):
            self.update(id, phase=label)
        common = [f'--account={email}', f'--project={project}', '--quiet']
        g = self.google
        # Ensure local protection works before any remote mutation.
        self.vault.open(self.vault.seal('verification'))
        if row['secret']:
            phase('Restauration du document')
            self.export_keys()
            self.update(id, state='done', phase='Clé enregistrée', error='')
            return
        phase('Connexion Google : valide la fenêtre ouverte')
        g.run(['auth', 'login', email, '--no-activate', '--quiet'], timeout=900)
        phase('Vérification du compte')
        token = g.run(['auth', 'print-access-token', f'--account={email}', '--quiet'])
        if not token:
            raise GoogleError('La connexion de ce compte n’est pas validée.')
        del token
        phase('Projet Google Cloud')
        projects = g.data(['projects', 'list', f'--account={email}', f'--filter=projectId={project}', '--quiet'])
        if not any(p.get('projectId') == project for p in projects):
            g.run(['projects', 'create', project, '--name=Gemini personnel', *common])
        phase('Activation de Gemini')
        g.run(['services', 'enable', 'apikeys.googleapis.com', 'generativelanguage.googleapis.com', 'iam.googleapis.com', *common])
        phase('Préparation de la clé AQ')
        service = f'gemini-local@{project}.iam.gserviceaccount.com'
        services = g.data(['iam', 'service-accounts', 'list', *common])
        if not any(s.get('email') == service for s in services):
            g.run(['iam', 'service-accounts', 'create', 'gemini-local', '--display-name=Gemini personnel', *common])
        keyid = 'gemini-local'
        keys = g.data(['services', 'api-keys', 'list', *common])
        if not any(k.get('name', '').split('/')[-1] == keyid for k in keys):
            g.run(['services', 'api-keys', 'create', f'--key-id={keyid}', f'--service-account={service}',
                   '--api-target=service=generativelanguage.googleapis.com', '--display-name=Gemini personnel', *common])
        phase('Récupération de la clé')
        key = g.run(['services', 'api-keys', 'get-key-string', keyid, '--location=global', '--format=value(keyString)', *common])
        if not key.startswith('AQ') or any(c.isspace() for c in key):
            raise GoogleError('Google n’a pas renvoyé une clé AQ. Vérifie le type de clé dans Google AI Studio.')
        self.update(id, secret=self.vault.seal(key), phase='Mise à jour du document')
        del key
        self.export_keys()
        self.update(id, state='done', phase='Clé enregistrée', error='')

    def tick(self):
        with self.connect() as con:
            if con.execute("SELECT value FROM settings WHERE name='paused'").fetchone()[0] == '1':
                return False
            row = con.execute("SELECT * FROM accounts WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                return False
            con.execute("UPDATE accounts SET state='running',error='' WHERE id=?", (row['id'],))
        try:
            self.process(dict(row))
        except Exception as exc:
            self.update(row['id'], state='error', error=safe_error(exc), phase='Intervention nécessaire')
        return True

    def start(self):
        def worker():
            while not self.stop.is_set():
                if not self.tick():
                    self.wake.wait(2)
                    self.wake.clear()
        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()


def make_handler(manager, token, origin):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log the initial session URL or keys.

        def send(self, code, data, content='application/json; charset=utf-8', cookie=False, download=False):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie:
                self.send_header('Set-Cookie', f'gemini_session={token}; HttpOnly; SameSite=Strict; Path=/')
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="cles-gemini-AQ.txt"')
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if self.headers.get('Host') != urlparse(origin).netloc:
                return False
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get('Cookie', ''))
                value = jar['gemini_session'].value if 'gemini_session' in jar else ''
                return secrets.compare_digest(value, token)
            except Exception:
                return False

        def do_GET(self):
            path = urlparse(self.path)
            supplied = parse_qs(path.query).get('session', [''])[0]
            initial = path.path == '/' and self.headers.get('Host') == urlparse(origin).netloc and secrets.compare_digest(supplied, token)
            if not initial and not self.authorized():
                return self.send(403, {'error': 'Ouvre l’application avec Lancer-Gemini.cmd.'})
            if path.path == '/':
                return self.send(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8', cookie=initial)
            if path.path == '/api/state':
                return self.send(200, manager.snapshot())
            if path.path == '/keys.txt':
                try:
                    return self.send(200, manager.export_keys(), 'text/plain; charset=utf-8', download=True)
                except Exception as exc:
                    return self.send(500, {'error': safe_error(exc)})
            self.send(404, {'error': 'Page introuvable'})

        def do_POST(self):
            if not self.authorized() or self.headers.get('Origin') != origin or self.headers.get('X-Gemini-Action') != '1':
                return self.send(403, {'error': 'Requête non autorisée'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 40000:
                    raise ValueError('Requête trop volumineuse')
                data = json.loads(self.rfile.read(length))
                if self.path == '/api/add':
                    manager.add(data.get('emails'))
                elif self.path == '/api/retry':
                    manager.retry(data.get('id'))
                elif self.path == '/api/pause':
                    manager.pause(bool(data.get('paused')))
                else:
                    return self.send(404, {'error': 'Action inconnue'})
                self.send(200, manager.snapshot())
            except (ValueError, TypeError, AttributeError) as exc:
                self.send(400, {'error': safe_error(exc)})
            except Exception as exc:
                self.send(500, {'error': safe_error(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    port = 8765
    origin = f'http://127.0.0.1:{port}'
    info = RUNTIME / 'browser-session.json'
    # Binding before recovery prevents a second worker from replaying active jobs.
    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), BaseHTTPRequestHandler)
    except OSError:
        raise SystemExit('Le port 8765 est déjà utilisé. Utilise la fenêtre Gemini déjà ouverte.')
    manager = Manager(ROOT / 'comptes.sqlite3', OUTPUTS / 'cles-gemini-AQ.txt')
    server.RequestHandlerClass = make_handler(manager, token, origin)
    info.write_text(json.dumps({'url': origin + '/?session=' + token}), encoding='utf-8')
    manager.start()
    if not args.no_browser:
        webbrowser.open(origin + '/?session=' + token)
    print('Application Gemini disponible dans le navigateur.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager.stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
