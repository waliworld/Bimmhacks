#!/usr/bin/env python3
"""Bimmhacks Pocket (the phone page served by the box) against the FAKE car (fake_enet.py):

    python3 installer/tests/test_pocket.py

What it proves:
  - the page and the state come up; the car fields are validated and saved
  - a flash request without the backup confirmation, or with a bad VIN, is refused with HTTP 400 and
    the car is not sent a single request
  - "Check state" runs through the server and only ever reads (0x22 / 0x19 / 0x31)
  - the coding snapshot is written on the box and downloadable; path tricks are refused
  - one job at a time (409 while busy); the PIN gate; Store install needs the key first
  - the whole root ENABLE path through the server (when port 22 can be bound here): the key is saved,
    the IP filled in from the car, the key bundle downloadable
Needs the `cryptography` package (the root tool imports it).
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SSHROOT = os.path.join(REPO, 'tools', 'idrive7-8-ssh-root')
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'installer'))
sys.path.insert(0, SSHROOT)

from fake_enet import FakeCar            # noqa: E402
from test_root_enable import FakeSsh, port_bindable   # noqa: E402
import bimmhacks_installer as bi          # noqa: E402
import pocket_server                      # noqa: E402

import importlib.util
HAVE_CRYPTO = importlib.util.find_spec('cryptography') is not None

VIN = 'WBA0000000P0CKET1'      # 17 chars, no I/O/Q


class Box(object):
    """a Pocket server on a free port with its own config + snapshot folder, for one test"""

    def __init__(self, pin=None):
        self.dir = tempfile.mkdtemp(prefix='pocket-')
        self.old_config = bi.CONFIG
        bi.CONFIG = os.path.join(self.dir, 'config.json')
        self.srv = pocket_server.make_server('127.0.0.1', 0, pin=pin, backups=os.path.join(self.dir, 'snapshots'),
                                             store=os.path.join(self.dir, 'store'))
        self.port = self.srv.server_address[1]
        self.pin = pin
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        bi.CONFIG = self.old_config
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -- http
    def call(self, path, body=None, pin=None, raw=False):
        headers = {'X-Pocket-Pin': pin if pin is not None else (self.pin or '')}
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = Request('http://127.0.0.1:%d%s' % (self.port, path), data=data, headers=headers)
        try:
            with urlopen(req, timeout=30) as r:
                payload = r.read()
                return r.status, (payload if raw else json.loads(payload.decode('utf-8')))
        except HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload.decode('utf-8'))
            except ValueError:
                return e.code, payload

    def run(self, action, **extra):
        body = dict(extra, action=action)
        return self.call('/api/run', body)

    def wait(self, timeout=120):
        """until the worker is done -> the final state"""
        end = time.time() + timeout
        while time.time() < end:
            st, s = self.call('/api/state')
            if st == 200 and not s['busy']:
                return s
            time.sleep(0.2)
        raise AssertionError('the box stayed busy for %ds' % timeout)

    def log(self):
        return self.call('/api/log?since=0')[1]['lines']


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
class PageAndFields(unittest.TestCase):
    def test_page_and_state(self):
        with Box() as box:
            st, html = box.call('/', raw=True)
            self.assertEqual(st, 200)
            self.assertIn(b'Bimmhacks', html)
            self.assertIn(b'Flash + enable SSH', html)
            st, s = box.call('/api/state')
            self.assertEqual(st, 200)
            self.assertEqual(s['version'], bi.VERSION)
            self.assertIsNone(s['busy'])
            self.assertEqual(s['car']['arch'], 'id7')
            self.assertEqual(s['car']['ip'], bi.DEFAULT_IP)
            self.assertEqual(s['arches'], list(bi.ARCHES))
            self.assertIn('en', s['languages'])
            for p in ('/manifest.webmanifest', '/icon.svg'):
                self.assertEqual(box.call(p, raw=True)[0], 200, p)
            self.assertEqual(box.call('/nope', raw=True)[0], 404)

    def test_car_fields_are_validated_and_saved(self):
        with Box() as box:
            st, r = box.call('/api/car', {'vin': VIN.lower(), 'arch': 'id8-bcp', 'ip': '192.168.4.9', 'gateway': '169.254.1.2'})
            self.assertEqual(st, 200, r)
            self.assertEqual(r['car']['vin'], VIN)
            self.assertEqual(r['car']['arch'], 'id8-bcp')
            self.assertEqual(r['car']['gateway'], '169.254.1.2')
            # survives a reload of the state (it is the installer's own config file)
            self.assertEqual(box.call('/api/state')[1]['car']['ip'], '192.168.4.9')
            for bad in ({'vin': 'SHORT'}, {'vin': 'WBA1234567890123I'}, {'arch': 'id9'}, {'ip': '1.2.3.4;reboot'},
                        {'gateway': 'x y'}, {'key': '/no/such/key'}, {'language': 'xx'}):
                st, r = box.call('/api/car', bad)
                self.assertEqual(st, 400, bad)
                self.assertIn('error', r)
            self.assertEqual(box.call('/api/state')[1]['car']['vin'], VIN, 'a refused field changed nothing')

    def test_pin_gate(self):
        with Box(pin='2468') as box:
            self.assertEqual(box.call('/', raw=True)[0], 200, 'the page itself needs no PIN')
            st, r = box.call('/api/state', pin='')
            self.assertEqual(st, 401)
            self.assertTrue(r.get('pin'))
            self.assertEqual(box.call('/api/state', pin='0000')[0], 401)
            self.assertEqual(box.call('/api/state', pin='2468')[0], 200)
            self.assertEqual(box.call('/api/state?pin=2468', pin='')[0], 200, 'download links carry it as a query')

    def test_bad_requests(self):
        with Box() as box:
            self.assertEqual(box.run('no-such-action')[0], 400)
            req = Request('http://127.0.0.1:%d/api/run' % box.port, data=b'not json', headers={'Content-Type': 'application/json'})
            try:
                urlopen(req, timeout=10)
                self.fail('accepted junk')
            except HTTPError as e:
                self.assertEqual(e.code, 400)


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
class Guards(unittest.TestCase):
    def test_flash_without_confirmation_sends_nothing(self):
        with Box() as box, FakeCar(vin=VIN) as car:
            box.call('/api/car', {'vin': VIN, 'gateway': '127.0.0.1', 'ip': '127.0.0.1'})
            for action in ('root-flash', 'root-fix-key'):
                st, r = box.run(action)
                self.assertEqual(st, 400, (action, r))
                self.assertIn('Refusing to flash', r['error'])
                self.assertIn('WIPES', r['error'])
            self.assertIsNone(box.call('/api/state')[1]['busy'])
            self.assertEqual(car.requests, [], 'the car must not be touched without the confirmation')

    def test_confirmation_with_a_bad_vin_is_refused(self):
        with Box() as box, FakeCar(vin=VIN) as car:
            box.call('/api/car', {'gateway': '127.0.0.1', 'ip': '127.0.0.1'})
            st, r = box.run('root-flash', confirm_backup=True)           # no VIN at all
            self.assertEqual(st, 400)
            self.assertIn('VIN', r['error'])
            st, r = box.run('root-flash', confirm_backup=True, car={'vin': 'WBA1234567890123Q'})
            self.assertEqual(st, 400)
            self.assertEqual(car.requests, [])

    def test_store_install_needs_the_key_first(self):
        with Box() as box, FakeCar(vin=VIN) as car:
            st, r = box.run('store-install')
            self.assertEqual(st, 400)
            self.assertIn('SSH key', r['error'])
            st, r = box.run('install-apps')
            self.assertEqual(st, 400)
            self.assertEqual(car.requests, [])

    def test_add_source_needs_the_key_and_a_real_repo(self):
        with Box() as box, FakeCar(vin=VIN) as car:
            st, r = box.run('add-source', repo='JustAnotherPU/ridetube')
            self.assertEqual(st, 400)
            self.assertIn('SSH key', r['error'])
            st, s = box.call('/api/state')
            self.assertEqual([x['id'] for x in s['sources']], ['ridetube', 'idrive-apps', 'car-apps'])
            self.assertEqual([x['builtin'] for x in s['sources']], [False, True, True])
            self.assertEqual(car.requests, [])

    @unittest.skipIf(os.name == 'nt', 'the fake ssh is a shell script')
    def test_add_source_runs_the_installer_path(self):
        import shutil as _sh
        real_wait = bi.wait_for_restart
        bi.wait_for_restart = lambda car, log=print, **k: real_wait(car, log, **dict(k, poll_s=0.01))

        def real_wait_for_restart_restore():
            bi.wait_for_restart = real_wait
        log_file = None
        with Box() as box, FakeSsh():
            key = os.path.join(box.dir, 'id_rsa')
            with open(key, 'w') as f:
                f.write('fake key\n')
            log_file = os.path.join(box.dir, 'ssh.log')
            os.environ['FAKE_SSH_LOG'] = log_file
            try:
                box.call('/api/car', {'key': key, 'ip': '127.0.0.1'})
                st, r = box.run('add-source', repo='bad repo')
                self.assertEqual(st, 400)
                st, r = box.run('add-source', repo='JustAnotherPU/ridetube', channel='nightly')
                self.assertEqual(st, 400)
                if not _sh.which('node'):
                    return                       # the box would refuse without Node, which is right
                st, r = box.run('add-source', repo='JustAnotherPU/ridetube', label='RideTube')
                self.assertEqual(st, 202, r)
                s = box.wait()
                self.assertTrue(s['last']['ok'], '\n'.join(box.log()[-10:]))
                self.assertIn('Store', s['last']['message'])
                st, r = box.run('add-source', repo=bi.CARAPPS_REPO, label='car-apps')
                s = box.wait()
                self.assertIn('built-in', s['last']['message'])
                # a Store whose updater is not up yet: the box restarts the head unit once and waits for it, and
                # when the updater is still not up it says so (not an error, and not "restart it" again)
                os.environ.update(FAKE_SSH_SOURCE='waiting', FAKE_SSH_STATE=os.path.join(box.dir, 'state'), FAKE_SSH_DOWN_PROBES='1')
                st, r = box.run('add-source', repo='someone/else', label='Someone')
                s = box.wait()
                self.assertTrue(s['last']['ok'], s['last'])
                self.assertIn('few more minutes', s['last']['message'])
                self.assertEqual(s['chip'], ['Restarted · Store still starting', 'warn'])
            finally:
                real_wait_for_restart_restore()
                for k in ('FAKE_SSH_LOG', 'FAKE_SSH_SOURCE', 'FAKE_SSH_STATE', 'FAKE_SSH_DOWN_PROBES'):
                    os.environ.pop(k, None)
            with open(log_file) as f:
                sent = [l for l in f.read().splitlines() if l.startswith('add-source ')]
        self.assertEqual(len(sent), 3, sent)          # RideTube; then the one that waited, again after the restart; car-apps is built in
        self.assertTrue(sent[0].startswith('add-source JustAnotherPU/ridetube releases'), sent)
        self.assertTrue(all(l.startswith('add-source someone/else releases') for l in sent[1:]), sent)

    @unittest.skipIf(os.name == 'nt', 'the fake ssh is a shell script')
    @unittest.skipUnless(__import__('shutil').which('node'), 'needs node')
    def test_restart_and_the_store_options(self):
        saved = {k: os.environ.get(k) for k in ('FAKE_SSH_LOG', 'FAKE_SSH_STATE', 'FAKE_SSH_DOWN_PROBES')}
        real_store, real_wait = bi.do_store_install, bi.wait_for_restart
        calls = []
        bi.do_store_install = lambda node, ip, key, **k: calls.append({x: k[x] for x in ('no_renderer', 'no_uplink', 'restart')})
        bi.wait_for_restart = lambda car, log=print, **k: real_wait(car, log, **dict(k, poll_s=0.01))
        try:
            with Box() as box, FakeSsh():
                st, r = box.run('restart')
                self.assertEqual(st, 400, 'no key yet')
                self.assertIn('SSH key', r['error'])
                key = os.path.join(box.dir, 'id_rsa')
                with open(key, 'w') as f:
                    f.write('fake key\n')
                box.call('/api/car', {'key': key, 'ip': '127.0.0.1'})
                log_file = os.path.join(box.dir, 'ssh.log')
                os.environ.update(FAKE_SSH_LOG=log_file, FAKE_SSH_STATE=os.path.join(box.dir, 'state'), FAKE_SSH_DOWN_PROBES='1')
                st, r = box.run('restart')
                self.assertEqual(st, 202, r)
                s = box.wait()
                self.assertTrue(s['last']['ok'], '\n'.join(box.log()[-8:]))
                self.assertEqual(s['last']['message'], 'The head unit restarted.')
                with open(log_file) as f:
                    self.assertEqual(len([l for l in f.read().splitlines() if l.startswith('L=/var/opt/mgu-deploy/lock;')]), 1)
                # the page's three options -> the install call: ticked boxes are "include", the server's flags are "leave out"
                st, r = box.run('store-install', no_renderer=True, no_uplink=False, restart=True)
                self.assertEqual(st, 202, r)
                s = box.wait()
                self.assertIn('restarted', s['last']['message'])
                self.assertEqual(s['chip'], ['Store installed · restarted', 'good'])
                st, r = box.run('store-install')
                s = box.wait()
                self.assertIn('Restart head unit', s['last']['message'])
                self.assertEqual(s['chip'][1], 'warn')
        finally:
            bi.do_store_install, bi.wait_for_restart = real_store, real_wait
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(calls, [{'no_renderer': True, 'no_uplink': False, 'restart': True},
                                 {'no_renderer': False, 'no_uplink': False, 'restart': False}])

    def test_store_package_staged_on_the_box_is_used_offline(self):
        with Box() as box:
            self.assertFalse(box.call('/api/state')[1]['box']['store_staged'])
            self.assertIsNone(pocket_server.store_staged(box.srv.pocket.store))
            d = os.path.join(box.srv.pocket.store, 'mgu-repo', 'mgu-store')
            os.makedirs(d)
            with open(os.path.join(d, 'catalog.json'), 'w') as f:
                f.write('{}')
            self.assertEqual(pocket_server.store_staged(box.srv.pocket.store), box.srv.pocket.store)
            self.assertTrue(box.call('/api/state')[1]['box']['store_staged'])
            # and first-install is then told to read it (--from) instead of GitHub
            seen = {}
            real = bi.subprocess.Popen

            class FakeProc(object):
                stdout = BytesIO(b'')
                stdin = BytesIO()

                @staticmethod
                def wait():
                    return 0
            bi.subprocess.Popen = lambda argv, **k: (seen.setdefault('argv', argv), FakeProc)[1]
            try:
                bi.do_store_install('node', '127.0.0.1', __file__, log=lambda m: None,
                                    from_dir=pocket_server.store_staged(box.srv.pocket.store))
            finally:
                bi.subprocess.Popen = real
            self.assertIn('--from', seen['argv'])
            self.assertEqual(seen['argv'][seen['argv'].index('--from') + 1], box.srv.pocket.store)


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
class ReadOnlyActions(unittest.TestCase):
    def test_check_state_only_reads(self):
        with Box() as box, FakeCar(vin=VIN) as car:
            box.call('/api/car', {'gateway': '127.0.0.1', 'ip': '127.0.0.1'})
            st, r = box.run('root-status')
            self.assertEqual(st, 202, r)
            self.assertEqual(r['started'], 'root-status')
            st, r2 = box.run('root-status')
            self.assertEqual(st, 409, 'one job at a time')
            s = box.wait()
            services = car.services()
        self.assertTrue(s['last']['ok'], s['last'])
        self.assertEqual(s['last']['action'], 'root-status')
        self.assertTrue(services <= {0x22, 0x19, 0x31}, 'status sent %s' % sorted(hex(x) for x in services))
        self.assertNotIn(0x2E, services)

    def test_log_is_served_in_pages(self):
        with Box() as box, FakeCar(vin=VIN):
            box.call('/api/car', {'gateway': '127.0.0.1', 'ip': '127.0.0.1'})
            box.run('root-status')
            box.wait()
            st, first = box.call('/api/log?since=0')
            self.assertEqual(st, 200)
            self.assertTrue(any('B7F8E5' in l for l in first['lines']), first['lines'][-5:])
            st, rest = box.call('/api/log?since=%d' % first['next'])
            self.assertEqual(rest['lines'], [])
            self.assertEqual(rest['next'], first['next'])

    def test_coding_snapshot_is_written_and_downloadable(self):
        with Box() as box, FakeCar(vin=VIN) as car:
            box.call('/api/car', {'vin': VIN, 'gateway': '127.0.0.1', 'ip': '127.0.0.1'})
            st, r = box.run('backup-coding')
            self.assertEqual(st, 202, r)
            s = box.wait()
            self.assertTrue(s['last']['ok'], s['last'])
            self.assertTrue(car.services() <= {0x22, 0x19})
            names = s['snapshots']
            self.assertEqual(len(names), 2, names)
            self.assertTrue(any(n.endswith('.json') for n in names) and any(n.endswith('.txt') for n in names))
            js = [n for n in names if n.endswith('.json')][0]
            st, data = box.call('/api/download?snapshot=' + js, raw=True)
            self.assertEqual(st, 200)
            self.assertEqual(json.loads(data.decode('utf-8'))['vin'], VIN)
            for bad in ('../config.json', 'config.json', '', 'x/y.json'):
                st, _ = box.call('/api/download?snapshot=' + bad, raw=True)
                self.assertIn(st, (400, 404), bad)
            st, _ = box.call('/api/download?key=1', raw=True)
            self.assertEqual(st, 400, 'no key yet')


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
@unittest.skipIf(os.name == 'nt', 'the fake ssh is a shell script')
@unittest.skipUnless(port_bindable(22), 'cannot bind port 22 here (the tool probes it): run as root to cover this')
class EnableThroughThePhone(unittest.TestCase):
    """the whole root ENABLE path, pressed from the phone page: the box runs the real tool"""

    def setUp(self):
        self.enroll_dir = os.path.join(SSHROOT, 'data', 'enrollments', VIN)
        shutil.rmtree(self.enroll_dir, ignore_errors=True)

    def tearDown(self):
        shutil.rmtree(self.enroll_dir, ignore_errors=True)
        logs = os.path.join(SSHROOT, 'data', 'logs')
        for n in os.listdir(logs) if os.path.isdir(logs) else []:
            if n.startswith('session-'):
                os.remove(os.path.join(logs, n))

    def test_enable_saves_the_key_and_fills_the_ip(self):
        with Box() as box, FakeCar(vin=VIN, ssh_port=22) as car, FakeSsh():
            # the fake car reports 169.254.36.47 as its head unit address (like a real one); its port 22
            # is on 127.0.0.1 here, so this is what the "use the IP field" option is for
            box.call('/api/car', {'vin': VIN, 'gateway': '127.0.0.1', 'ip': '127.0.0.1', 'root_ip_override': True})
            st, r = box.run('root-enable')
            self.assertEqual(st, 202, r)
            s = box.wait(timeout=180)
            services, debug = car.services(), car.debug_on
            self.assertTrue(s['last']['ok'], '\n'.join(box.log()[-15:]))
            self.assertEqual(s['chip'][0], 'Rooted · key saved')
            self.assertEqual(s['car']['key'], os.path.join(self.enroll_dir, 'id_rsa'))
            self.assertTrue(s['car']['key_ok'])
            self.assertEqual(s['car']['ip'], '127.0.0.1', 'the IP comes from the car (connect.txt), not the field')
            st, data = box.call('/api/download?key=1', raw=True)
            self.assertEqual(st, 200)
            names = zipfile.ZipFile(BytesIO(data)).namelist()
            self.assertIn('id_rsa', names)
            self.assertIn('connect.txt', names)
        self.assertFalse(debug, 'persist turned debug mode off')
        self.assertFalse(services & {0x27, 0x34, 0x36, 0x37, 0x2E, 0x3D, 0x85, 0x11, 0x14}, 'nothing that flashes or writes')


if __name__ == '__main__':
    unittest.main(verbosity=2)
