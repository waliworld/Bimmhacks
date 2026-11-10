#!/usr/bin/env python3
"""Bimmhacks Pocket - the phone version.

A small box (a Raspberry Pi or any little Linux computer) sits on the ENET cable and runs this
server; the phone joins the box's Wi-Fi and opens the page. Root access and the on-car App Store
without a laptop: everything the desktop installer does on the wire runs here, on the box, and
the phone only presses the buttons.

    python3 installer/pocket_server.py                    # http://<box>:8080
    python3 installer/pocket_server.py --port 80 --pin 2468

The page is installer/pocket/index.html. The server is the same code path as the window and the
command line (bimmhacks_installer.py): the same safety gates apply - a flash is refused without the
backup confirmation and a valid VIN, before anything is sent to the car.

Set-up of the box itself: installer/pocket/setup-box.sh (docs/POCKET.md).
"""
import argparse
import io
import json
import os
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import bimmhacks_installer as bi  # noqa: E402

PAGE_DIR = os.path.join(HERE, 'pocket')
# the Store package, downloaded onto the box while it has internet (setup-box.sh, or the page's button):
# on the car the box is alone with the car, so first-install reads it from here (--from) instead of GitHub
POCKET_STORE = os.path.join(bi.REPO, 'pocket-store')
MAX_LOG = 4000                      # lines kept in memory
MAX_BODY = 64 * 1024
VIN_RE = re.compile(r'^[A-HJ-NPR-Z0-9]{17}$')
HOST_RE = re.compile(r'^[A-Za-z0-9.:\-\[\]%]+$')
KEY_FILES = ('id_rsa', 'id_rsa.pkcs1.pem', 'id_rsa.pkcs8.pem', 'id_rsa.ppk', 'connect.txt')

ACTIONS = ('detect-vin', 'backup-coding', 'verify-coding', 'restore-coding',
           'root-status', 'root-enable', 'root-flash', 'root-fix-key', 'root-disable',
           'test', 'store-install', 'restart', 'stage-store', 'add-source')
# the extra Store sources the page offers (idrive7-apps, cluster/HUD maps and car-apps are built in)
SOURCES = [
    {'id': 'ridetube', 'label': 'RideTube · unofficial YouTube client', 'repo': bi.RIDETUBE_REPO, 'channel': 'releases',
     'builtin': False, 'note': 'GPL-3, by JustAnotherPU; not affiliated with YouTube or Google. Video only while parked; iDrive 7.'},
    {'id': 'idrive-apps', 'label': 'Cluster & HUD maps (idrive-apps)', 'repo': bi.IDRIVE_APPS_REPO, 'channel': 'releases',
     'builtin': True, 'note': 'Robert Nisipeanu. Waze / CarPlay map on the cluster and HUD.'},
    {'id': 'car-apps', 'label': 'IterioDev car-apps (iDrive 8)', 'repo': bi.CARAPPS_REPO, 'channel': 'tags',
     'builtin': True, 'note': 'Car Data, Drag Timer, Drive Recorder, Overlays … mostly iDrive 8. PolyForm Strict licence.'},
]
ROOT_ACTIONS = {'root-status': 'status', 'root-enable': 'enable', 'root-flash': 'flash',
                'root-fix-key': 'fix-key', 'root-disable': 'disable'}
DONE = {
    'detect-vin': 'VIN read.',
    'backup-coding': 'Coding backup saved on the box - download it below. Keep your Autel / E-Sys backup too.',
    'verify-coding': 'Coding compared - see the activity log.',
    'restore-coding': 'Coding restore finished - see the activity log; verify the car.',
    'root-status': 'State read - see the activity log.',
    'root-enable': 'Root SSH is enabled - the key is saved on the box.',
    'root-flash': 'Flashed and root SSH enabled - the key is saved on the box. Download it to keep a copy.',
    'root-fix-key': 'Re-flashed with our key - root SSH is enabled.',
    'root-disable': 'Root SSH is disabled again.',
    'test': 'Connection OK.',
    'status': 'Status read - see the activity log.',
    'store-install': 'App Store installed. Press "Restart head unit" so the Store can start.',
    'restart': 'The head unit restarted.',
    'stage-store': 'Store package downloaded onto the box - the App Store now installs without internet on the box.',
    'add-source': 'Added to the car\'s Store - on the car open Apps > Store; the apps appear once it has read the source.',
}


def store_staged(folder=None):
    """the folder to give first-install --from, when the Store package was staged on this box"""
    folder = folder or POCKET_STORE
    return folder if os.path.isfile(os.path.join(folder, 'mgu-repo', 'mgu-store', 'catalog.json')) else None


def stage_store(log, folder=None):
    """Download the Store package (and the game data the Doom app needs) onto the box: needs internet HERE."""
    folder = folder or POCKET_STORE
    node = bi.find_node()
    if not node:
        raise bi.InstallerError('The box has no Node.js (apt install nodejs).')
    bi.do_usb_repo(node, folder, [], log=log)          # no inputs: the Store itself (github:robertnisipeanu/mgu-store)
    if not store_staged(folder):
        raise bi.InstallerError('The download finished but %s holds no Store catalog.' % folder)
    log('Staged in %s.' % folder)


class Busy(Exception):
    pass


class Refused(Exception):
    """a request that is wrong before anything runs (HTTP 400)"""


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def local_addresses():
    """IPv4 addresses of this box: [(interface, address)] (Linux `ip`; else the one the OS routes with)."""
    out = []
    try:
        r = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'up'], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) > 3 and parts[2] == 'inet' and parts[1] != 'lo':
                out.append((parts[1], parts[3].split('/')[0]))
    except (OSError, subprocess.SubprocessError):
        pass
    if not out:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.255.255.255', 1))
            out.append(('', s.getsockname()[0]))
        except OSError:
            out.append(('', '127.0.0.1'))
        finally:
            s.close()
    return out


class Pocket(object):
    """What the page sees: the car fields (saved in the installer's config), one worker at a time,
    the activity log, the last result."""

    def __init__(self, pin=None, backups=None, store=None):
        self.lock = threading.Lock()
        self.lines = []
        self.dropped = 0            # lines trimmed from the front (cursors are absolute)
        self.busy = None
        self.started = None
        self.last = None
        self.pin = pin or None
        self.backups = backups or bi.CODING_BACKUP_DIR
        self.store = store or POCKET_STORE
        self.chip = ['Not checked', 'neutral']

    # -- log --------------------------------------------------------------------------------------
    def log(self, msg):
        with self.lock:
            self.lines.append(str(msg))
            if len(self.lines) > MAX_LOG:
                cut = len(self.lines) - MAX_LOG
                del self.lines[:cut]
                self.dropped += cut

    def log_since(self, since):
        with self.lock:
            start = max(0, since - self.dropped)
            return self.lines[start:], self.dropped + len(self.lines)

    # -- car fields -------------------------------------------------------------------------------
    @staticmethod
    def car():
        cfg = bi.load_config()
        key = cfg.get('key', '') or ''
        arch = cfg.get('arch', 'id7')
        return {'vin': (cfg.get('vin') or '').upper(), 'arch': arch if arch in bi.ARCHES else 'id7',
                'ip': cfg.get('ip') or bi.DEFAULT_IP, 'key': key, 'key_ok': bool(key and os.path.isfile(key)),
                'gateway': cfg.get('gateway') or 'auto',
                # root access normally probes the head unit at the address the car itself reports;
                # this makes it use the IP field instead (a bridge or NAT between the box and the car)
                'root_ip_override': bool(cfg.get('root_ip_override')),
                'our_repo': cfg.get('our_repo') or bi.BIMMHACKS_APPS_REPO}

    @staticmethod
    def save_car(fields):
        """validate + save the fields the page sends (only the known ones)"""
        new = {}
        if 'vin' in fields:
            v = (fields.get('vin') or '').strip().upper()
            if v and not VIN_RE.match(v):
                raise Refused('A VIN is 17 characters: letters (no I, O, Q) and digits.')
            new['vin'] = v
        if 'arch' in fields:
            if fields['arch'] not in bi.ARCHES:
                raise Refused('Unknown head unit: pick one of %s.' % ', '.join(bi.ARCHES))
            new['arch'] = fields['arch']
        if 'ip' in fields:
            ip = (fields.get('ip') or '').strip() or bi.DEFAULT_IP
            if not HOST_RE.match(ip):
                raise Refused('Not a valid head unit address: %r' % ip)
            new['ip'] = ip
        if 'gateway' in fields:
            g = (fields.get('gateway') or '').strip() or 'auto'
            if g != 'auto' and not HOST_RE.match(g):
                raise Refused('Not a valid gateway address: %r' % g)
            new['gateway'] = g
        if 'key' in fields:
            k = (fields.get('key') or '').strip()
            if k and not os.path.isfile(k):
                raise Refused('No such key file on the box: %s' % k)
            new['key'] = k
        if 'root_ip_override' in fields:
            new['root_ip_override'] = bool(fields['root_ip_override'])
        if 'our_repo' in fields:
            r = (fields.get('our_repo') or '').strip()
            if r and not bi.SOURCE_RE.match(r):
                raise Refused('Enter your source as owner/repo, e.g. waliworld/Bimmhacks.')
            new['our_repo'] = r
        if new:
            bi.save_config(dict(bi.load_config(), **new))
        return Pocket.car()

    # -- state ------------------------------------------------------------------------------------
    def snapshot(self):
        with self.lock:
            busy, started, last, chip = self.busy, self.started, self.last, list(self.chip)
            total = self.dropped + len(self.lines)
        addrs = local_addresses()
        return {
            'version': bi.VERSION,
            'busy': busy, 'since': round(time.time() - started, 1) if started else None,
            'last': last, 'chip': chip, 'log_total': total,
            'car': self.car(), 'arches': list(bi.ARCHES), 'sources': SOURCES,
            'box': {'host': socket.gethostname(), 'addresses': addrs,
                    'enet': [a for _, a in addrs if a.startswith('169.254.')],
                    'node': bool(bi.find_node()), 'ssh': bool(bi.find_ssh()),
                    'store_staged': bool(store_staged(self.store))},
            'snapshots': self.snapshots(),
        }

    def snapshots(self):
        try:
            names = sorted(n for n in os.listdir(self.backups) if n.endswith(('.json', '.txt')))
        except OSError:
            return []
        return names[-20:]

    # -- the worker -------------------------------------------------------------------------------
    def start(self, action, fn):
        with self.lock:
            if self.busy:
                raise Busy(self.busy)
            self.busy, self.started = action, time.time()

        def work():
            ok, message = True, DONE.get(action, 'Done.')
            try:
                r = fn()
                if isinstance(r, str) and r:
                    message = r
            except bi.InstallerError as e:
                ok, message = False, str(e)
                self.log('ERROR: %s' % e)
            except Exception as e:   # the page must never be left "busy" by a surprise
                ok, message = False, 'Unexpected error: %s' % e
                self.log('ERROR: %s' % e)
                self.log(traceback.format_exc().rstrip())
            with self.lock:
                self.busy, self.started = None, None
                self.last = {'action': action, 'ok': ok, 'message': message, 'at': time.time()}
        threading.Thread(target=work, name='pocket-' + action, daemon=True).start()

    def set_chip(self, text, kind='neutral'):
        with self.lock:
            self.chip = [text, kind]

    # -- actions: validated here, BEFORE anything runs; the installer's own gates run again inside -
    def run(self, body):
        action = body.get('action')
        if action not in ACTIONS:
            raise Refused('Unknown action: %r' % (action,))
        car = self.save_car(body.get('car') or {})
        vin, arch, ip, key, gateway = car['vin'], car['arch'], car['ip'], car['key'], car['gateway']
        mgu_ip = ip if car['root_ip_override'] else None        # None: the tool asks the car for it
        log = self.log

        if action == 'detect-vin':
            def fn():
                v = bi.detect_vin(log, ip=mgu_ip, gateway=gateway, arch=arch)
                if not v:
                    raise bi.InstallerError('The car did not report a VIN. Is the ENET cable in and the car awake?')
                bi.save_config(dict(bi.load_config(), vin=v))
                log('VIN: ' + v)
                return 'VIN read: ' + v
            return self.start(action, fn)

        if action == 'backup-coding':
            def fn():
                bi.do_backup_coding(log, vin=vin or None, ip=mgu_ip, gateway=gateway, arch=arch, out_dir=self.backups)
            return self.start(action, fn)

        if action in ('verify-coding', 'restore-coding'):
            bk = (body.get('backup') or '').strip() or bi.latest_coding_backup(self.backups, vin or None)
            if not bk or not os.path.isfile(bk):
                raise Refused('No coding backup to use. Take one with "Back up coding" first.')
            if action == 'verify-coding':
                return self.start(action, lambda: bi.do_verify_coding(log, bk, ip=mgu_ip, gateway=gateway, arch=arch))
            dry = bool(body.get('dry_run'))
            if not dry and not body.get('confirm_restore'):
                raise Refused('Restoring coding is experimental and can leave the HMI partly-coded. Tick the '
                              'confirmation (or run a dry run first).')

            def fn():
                bi.do_restore_coding(log, bk, ip=mgu_ip, gateway=gateway, arch=arch, dry_run=dry, confirm=not dry)
            return self.start(action, fn)

        if action in ROOT_ACTIONS:
            act = ROOT_ACTIONS[action]
            confirmed = bool(body.get('confirm_backup'))
            try:
                bi.require_backup_confirmation(act, confirmed)       # the same gate as the window / CLI
            except bi.InstallerError as e:
                raise Refused(str(e))
            if act in bi.FLASHING_ACTIONS and not VIN_RE.match(vin or ''):
                raise Refused('A valid 17-character VIN is needed to flash. Use "Detect VIN", or type it in.')

            def fn():
                k = bi.do_root(act, log, vin=vin or None, ip=mgu_ip, gateway=gateway, arch=arch,
                               backup_confirmed=confirmed)
                if k:
                    got = bi.ip_from_key(k)
                    bi.save_config(dict(bi.load_config(), key=k, **({'ip': got} if got else {})))
                    self.set_chip('Rooted · key saved', 'good')
                elif act == 'disable':
                    self.set_chip('Root SSH disabled', 'warn')
            return self.start(action, fn)

        if action == 'stage-store':          # no car involved: the box downloads for itself
            return self.start(action, lambda: stage_store(log, self.store))

        # everything below talks to the head unit as root over ssh
        if not key or not os.path.isfile(key):
            raise Refused('The car SSH key is needed: do the root-access step first (or pick the key file).')
        ssh = bi.find_ssh()
        if not ssh:
            raise Refused('The box has no ssh client (apt install openssh-client).')
        if action == 'test':
            def fn():
                bi.do_test_connection(ssh, ip, key, log)
                self.set_chip('SSH OK · ' + ip, 'good')
            return self.start(action, fn)

        if action == 'restart':
            def fn():
                bi.do_restart_head_unit(ip, key, log=log)
                self.set_chip('Restarted · SSH OK', 'good')
            return self.start(action, fn)

        node = bi.find_node()
        if not node:
            raise Refused('The box has no Node.js (apt install nodejs).')
        if action == 'store-install':
            no_renderer, no_uplink = bool(body.get('no_renderer')), bool(body.get('no_uplink'))
            restart = bool(body.get('restart'))

            def fn():
                bi.do_store_install(node, ip, key, no_renderer=no_renderer, no_uplink=no_uplink, log=log,
                                    from_dir=store_staged(self.store), restart=restart)
                if restart:
                    self.set_chip('Store installed · restarted', 'good')
                    return 'App Store installed and the head unit restarted - open Apps > Store on the car.'
                self.set_chip('Store installed · restart the head unit', 'warn')
            return self.start(action, fn)

        if action == 'add-source':
            repo = (body.get('repo') or '').strip()
            channel = (body.get('channel') or 'releases').strip()
            label = (body.get('label') or repo).strip()[:60]
            if not bi.SOURCE_RE.match(repo):
                raise Refused('The source must be a GitHub owner/repo, e.g. JustAnotherPU/ridetube.')
            if not bi.CHANNEL_RE.match(channel):
                raise Refused('The channel must be releases, tags, branch or branch:<name>.')

            def fn():
                r = bi.do_install_apps_source(node, ip, key, repo, label, log=log, channel=channel,
                                              from_dir=store_staged(self.store), restart_store=True)
                if r == 'pending':
                    self.set_chip('Store installed · restart the head unit', 'warn')
                elif r == 'slow':
                    self.set_chip('Restarted · Store still starting', 'warn')
                return bi.source_done_message(r, label)
            return self.start(action, fn)
        raise Refused('Unknown action: %r' % (action,))      # not reached

    # -- downloads --------------------------------------------------------------------------------
    def snapshot_file(self, name):
        if not name or name != os.path.basename(name) or name not in self.snapshots():
            raise Refused('No such snapshot.')
        return os.path.join(self.backups, name)

    def key_zip(self):
        """the car's key bundle (the folder the root tool wrote), zipped for the phone's Files app"""
        key = self.car()['key']
        if not key or not os.path.isfile(key):
            raise Refused('No SSH key saved yet.')
        d = os.path.dirname(key)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for n in KEY_FILES:
                p = os.path.join(d, n)
                if os.path.isfile(p):
                    z.write(p, n)
        name = 'bimmhacks-%s-ssh-key.zip' % (os.path.basename(d) or 'car')
        return name, buf.getvalue()


# ------------------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = 'BimmhacksPocket/' + bi.VERSION
    pocket = None           # set by make_server
    verbose = False

    def log_message(self, fmt, *args):
        if self.verbose:
            sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    # -- helpers
    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, data, ctype, name=None, status=200):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if name:
            self.send_header('Content-Disposition', 'attachment; filename="%s"' % name)
        self.end_headers()
        self.wfile.write(data)

    def pin_ok(self, query):
        if not self.pocket.pin:
            return True
        given = self.headers.get('X-Pocket-Pin') or (query.get('pin') or [''])[0]
        return given == self.pocket.pin

    def body_json(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            raise Refused('Request too large.')
        raw = self.rfile.read(n) if n else b''
        if not raw:
            return {}
        try:
            obj = json.loads(raw.decode('utf-8'))
        except ValueError:
            raise Refused('The request is not JSON.')
        if not isinstance(obj, dict):
            raise Refused('The request is not a JSON object.')
        return obj

    # -- routes
    def do_GET(self):
        u = urlparse(self.path)
        query = parse_qs(u.query)
        path = u.path
        try:
            if path in ('/', '/index.html'):
                with open(os.path.join(PAGE_DIR, 'index.html'), 'rb') as f:
                    return self.send_file(f.read(), 'text/html; charset=utf-8')
            if path == '/manifest.webmanifest':
                with open(os.path.join(PAGE_DIR, 'manifest.webmanifest'), 'rb') as f:
                    return self.send_file(f.read(), 'application/manifest+json')
            if path == '/icon.svg':
                with open(os.path.join(PAGE_DIR, 'icon.svg'), 'rb') as f:
                    return self.send_file(f.read(), 'image/svg+xml')
            if not path.startswith('/api/'):
                return self.send_json({'error': 'Not found.'}, 404)
            if not self.pin_ok(query):
                return self.send_json({'error': 'PIN needed.', 'pin': True}, 401)
            if path == '/api/state':
                return self.send_json(self.pocket.snapshot())
            if path == '/api/log':
                try:
                    since = int((query.get('since') or ['0'])[0])
                except ValueError:
                    since = 0
                lines, nxt = self.pocket.log_since(since)
                return self.send_json({'lines': lines, 'next': nxt})
            if path == '/api/download':
                if query.get('key'):
                    name, data = self.pocket.key_zip()
                    return self.send_file(data, 'application/zip', name)
                name = (query.get('snapshot') or [''])[0]
                p = self.pocket.snapshot_file(name)
                with open(p, 'rb') as f:
                    return self.send_file(f.read(), 'application/json' if p.endswith('.json') else 'text/plain; charset=utf-8', name)
            return self.send_json({'error': 'Not found.'}, 404)
        except Refused as e:
            return self.send_json({'error': str(e)}, 400)
        except OSError as e:
            return self.send_json({'error': 'Cannot read that: %s' % e}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        query = parse_qs(u.query)
        try:
            if not u.path.startswith('/api/'):
                return self.send_json({'error': 'Not found.'}, 404)
            if not self.pin_ok(query):
                return self.send_json({'error': 'PIN needed.', 'pin': True}, 401)
            body = self.body_json()
            if u.path == '/api/car':
                return self.send_json({'car': self.pocket.save_car(body)})
            if u.path == '/api/run':
                self.pocket.run(body)
                return self.send_json({'started': body.get('action')}, 202)
            return self.send_json({'error': 'Not found.'}, 404)
        except Refused as e:
            return self.send_json({'error': str(e)}, 400)
        except Busy as e:
            return self.send_json({'error': 'Still busy with: %s. Wait for it to finish.' % e, 'busy': str(e)}, 409)


def make_server(host='0.0.0.0', port=8080, pin=None, backups=None, verbose=False, store=None):
    pocket = Pocket(pin=pin, backups=backups, store=store)
    handler = type('PocketHandler', (Handler,), {'pocket': pocket, 'verbose': verbose})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.pocket = pocket
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description='Bimmhacks Pocket: the phone page, served by the box on the ENET cable.')
    ap.add_argument('--host', default='0.0.0.0', help='address to listen on (default: every interface)')
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--pin', help='ask the phone for this PIN (on top of the Wi-Fi password)')
    ap.add_argument('--config', help='where the car fields are saved (default: the installer\'s own config file)')
    ap.add_argument('--backups', help='folder for coding snapshots (default: coding-backups/ in the package)')
    ap.add_argument('--verbose', action='store_true', help='log every request')
    ap.add_argument('--stage-store', action='store_true',
                    help='do not serve: download the Store package (and app data files) onto this box now, then exit')
    a = ap.parse_args(argv)
    if a.config:
        bi.CONFIG = a.config
    if a.stage_store:
        try:
            stage_store(print)
        except bi.InstallerError as e:
            sys.stderr.write('ERROR: %s\n' % e)
            return 1
        return 0
    if not os.path.isfile(os.path.join(PAGE_DIR, 'index.html')):
        sys.stderr.write('ERROR: the page is missing (expected %s)\n' % os.path.join(PAGE_DIR, 'index.html'))
        return 1
    try:
        srv = make_server(a.host, a.port, pin=a.pin, backups=a.backups, verbose=a.verbose)
    except OSError as e:
        sys.stderr.write('ERROR: cannot listen on %s:%d: %s\n' % (a.host, a.port, e))
        return 1
    print('Bimmhacks Pocket v%s' % bi.VERSION)
    for iface, addr in local_addresses():
        print('  open on the phone:  http://%s:%d/%s' % (addr, a.port, ('   (' + iface + ')') if iface else ''))
    print('  node: %s   ssh: %s   pin: %s   store package on the box: %s' % (
        'yes' if bi.find_node() else 'NO', 'yes' if bi.find_ssh() else 'NO', 'yes' if a.pin else 'no',
        'staged' if store_staged() else 'not yet (--stage-store while online)'))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
