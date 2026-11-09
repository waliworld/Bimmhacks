#!/usr/bin/env python3
"""Adding a source (RideTube, your own repo) to the on-car Store, without a car:

    python3 installer/tests/test_store_source.py

  1. installer/unit_add_source.js - the script the installer runs ON THE HEAD UNIT with the unit's Node -
     against a FAKE updater socket that speaks the real protocol (one JSON object per line, the state on
     connect, the privileged secret, replies tagged with the request's rid): added, already a source,
     wrong secret, updater not running, bad channel
  2. add_store_source() / do_install_apps_source() through the installer's Car over the fake ssh:
     the Store is installed first when missing, built-in sources are not added twice, the result is
     read from the script's last line, refusals become InstallerError
Needs node (the script is JavaScript).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'installer'))

from test_root_enable import FakeSsh      # noqa: E402
import bimmhacks_installer as bi          # noqa: E402

NODE = shutil.which('node')
SCRIPT = os.path.join(REPO, 'installer', 'unit_add_source.js')
SECRET = 'a' * 64


class FakeUpdater(object):
    """the mgu-updater socket as the real daemon serves it: state on connect, one JSON request per line,
    privileged requests need the secret, the answer carries the request's rid"""

    def __init__(self, secret=SECRET, sources=()):
        self.dir = tempfile.mkdtemp(prefix='fake-updater-')
        self.path = os.path.join(self.dir, 'updater.sock')
        self.secret_file = os.path.join(self.dir, 'updater-secret')
        with open(self.secret_file, 'w') as f:
            f.write(secret + '\n')
        self.secret = secret
        self.sources = [dict(s) for s in sources]
        self.requests = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(4)
        self.alive = True
        threading.Thread(target=self._accept, daemon=True).start()

    def state(self):
        return {'type': 'state', 'sources': self.sources}

    def _accept(self):
        while self.alive:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            conn.sendall((json.dumps(self.state()) + '\n').encode())
            buf = b''
            while True:
                d = conn.recv(65536)
                if not d:
                    return
                buf += d
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    req = json.loads(line.decode())
                    self.requests.append(req)
                    rid = {'rid': req['rid']} if 'rid' in req else {}
                    err = self.handle(req)
                    reply = dict({'type': 'error', 'op': req.get('op'), 'error': err}, **rid) if err else dict(self.state(), **rid)
                    conn.sendall((json.dumps(reply) + '\n').encode())
        except OSError:
            pass
        finally:
            conn.close()

    def handle(self, req):
        if req.get('op') != 'source-add':
            return 'unknown op'
        if req.get('secret') != self.secret:
            return 'forbidden'
        if req.get('type') != 'github':
            return 'type must be github or https'
        if any(s['repo'].lower() == req['repo'].lower() for s in self.sources):
            return '%s is already a source' % req['repo']
        self.sources.append({'id': req['repo'].split('/')[1].lower(), 'type': 'github', 'repo': req['repo'],
                             'channel': req.get('channel')})
        return None

    def close(self):
        self.alive = False
        self.srv.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def run_script(updater, repo, channel, sock=None, secret_file=None, retries='2'):
    """the script exactly as the installer sends it to the unit: `node - <repo> <channel> ...`, file on stdin"""
    env = dict(os.environ, MGU_ADD_SOURCE_RETRIES=retries, MGU_ADD_SOURCE_WAIT_MS='150', MGU_ADD_SOURCE_ANSWER_MS='3000')
    with open(SCRIPT, 'rb') as f:
        script = f.read()
    r = subprocess.run([NODE, '-', repo, channel, sock or updater.path, secret_file or updater.secret_file], input=script,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30, env=env)
    return r.returncode, r.stdout.decode('utf-8', 'replace').strip().splitlines()


@unittest.skipUnless(NODE, 'needs node')
class ScriptAgainstFakeUpdater(unittest.TestCase):
    def setUp(self):
        self.up = FakeUpdater()
        self.addCleanup(self.up.close)

    def test_adds_a_source_with_the_secret_and_the_channel(self):
        rc, out = run_script(self.up, 'JustAnotherPU/ridetube', 'releases')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out[-1], 'ADDED JustAnotherPU/ridetube (releases)')
        self.assertEqual(len(self.up.requests), 1)
        req = self.up.requests[0]
        self.assertEqual((req['op'], req['type'], req['repo'], req['channel'], req['secret']),
                         ('source-add', 'github', 'JustAnotherPU/ridetube', 'releases', SECRET))
        self.assertTrue(req['rid'].startswith('bimmhacks-'))
        self.assertEqual(self.up.sources[0]['id'], 'ridetube')

    def test_already_a_source_is_fine(self):
        run_script(self.up, 'JustAnotherPU/ridetube', 'releases')
        rc, out = run_script(self.up, 'justanotherpu/RideTube', 'releases')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out[-1], 'ALREADY justanotherpu/RideTube')
        self.assertEqual(len(self.up.sources), 1)

    def test_wrong_secret_is_an_error(self):
        wrong = os.path.join(self.up.dir, 'wrong')
        with open(wrong, 'w') as f:
            f.write('b' * 64)
        rc, out = run_script(self.up, 'o/r', 'releases', secret_file=wrong)
        self.assertEqual(rc, 1)
        self.assertEqual(out[-1], 'ERROR: the updater refused the secret (forbidden)')
        self.assertEqual(self.up.sources, [])

    def test_updater_not_running_retries_then_reports_waiting(self):
        rc, out = run_script(self.up, 'o/r', 'releases', sock=os.path.join(self.up.dir, 'nothing.sock'), retries='3')
        self.assertEqual(rc, 3, 'waiting is its own status: the Store is there, its updater starts with the head unit')
        self.assertEqual(sum(1 for l in out if l.startswith('waiting for the updater')), 2)
        self.assertTrue(out[-1].startswith('WAITING: the updater is not running'), out)
        self.assertEqual(self.up.requests, [])

    def test_bad_input_never_connects(self):
        for repo, chan in (('no-slash', 'releases'), ('o/r', 'nightly'), ('o/r;x', 'releases')):
            rc, out = run_script(self.up, repo, chan)
            self.assertEqual(rc, 1, (repo, chan))
            self.assertTrue(out[-1].startswith('ERROR: '), out)
        self.assertEqual(self.up.requests, [])

    def test_branch_channel_with_a_name(self):
        rc, out = run_script(self.up, 'waliworld/Bimmhacks', 'branch:main')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.up.sources[0]['channel'], 'branch:main')


@unittest.skipIf(os.name == 'nt', 'the fake ssh is a shell script')
class ThroughTheInstaller(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='source-test-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.key = os.path.join(self.dir, 'id_rsa')
        with open(self.key, 'w') as f:
            f.write('-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n')
        self.log_file = os.path.join(self.dir, 'ssh.log')
        os.environ['FAKE_SSH_LOG'] = self.log_file
        self.addCleanup(os.environ.pop, 'FAKE_SSH_LOG', None)
        for k in ('FAKE_SSH_NO_STORE', 'FAKE_SSH_SOURCE'):
            os.environ.pop(k, None)
        self.lines = []

    def ssh_log(self):
        if not os.path.isfile(self.log_file):
            return []
        with open(self.log_file) as f:
            return f.read().splitlines()

    def test_add_store_source_reads_the_verdict(self):
        with FakeSsh():
            car = bi.Car(shutil.which('ssh'), '127.0.0.1', self.key, log=self.lines.append)
            try:
                self.assertEqual(bi.add_store_source(car, 'JustAnotherPU/ridetube', 'releases', log=self.lines.append), 'added')
                os.environ['FAKE_SSH_SOURCE'] = 'already'
                self.assertEqual(bi.add_store_source(car, 'JustAnotherPU/ridetube', log=self.lines.append), 'already')
                os.environ['FAKE_SSH_SOURCE'] = 'error'
                with self.assertRaises(bi.InstallerError) as cm:
                    bi.add_store_source(car, 'JustAnotherPU/ridetube', log=self.lines.append)
                self.assertIn('forbidden', str(cm.exception))
            finally:
                car.close()
        sent = [l for l in self.ssh_log() if l.startswith('add-source ')]
        self.assertEqual(len(sent), 3)
        self.assertTrue(sent[0].startswith('add-source JustAnotherPU/ridetube releases script-bytes='))
        self.assertGreater(int(sent[0].split('script-bytes=')[1]), 1000, 'the whole script went over on stdin')

    def test_waiting_is_store_not_ready_not_a_failure(self):
        os.environ['FAKE_SSH_SOURCE'] = 'waiting'
        with FakeSsh():
            car = bi.Car(shutil.which('ssh'), '127.0.0.1', self.key, log=self.lines.append)
            try:
                with self.assertRaises(bi.StoreNotReady):
                    bi.add_store_source(car, 'JustAnotherPU/ridetube', log=self.lines.append)
            finally:
                car.close()
            r = bi.do_install_apps_source('node', '127.0.0.1', self.key, 'JustAnotherPU/ridetube', 'RideTube',
                                          log=self.lines.append)
        self.assertEqual(r, 'pending')
        self.assertTrue(any('restart the head unit' in l.lower() for l in self.lines), self.lines[-4:])

    def test_the_final_message_says_what_happened(self):
        for result, must in (('staged', 'USB'), ('builtin', 'built-in'), ('added', 'was added'),
                             ('already', 'already'), ('pending', 'Restart the head unit')):
            msg = bi.source_done_message(result, 'RideTube')
            self.assertIn('RideTube', msg)
            self.assertIn(must, msg, result)
            self.assertIn('RideTube', bi.source_done_message(result, 'RideTube'))

    def test_the_command_line_adds_ridetube_and_pending_has_its_own_exit_status(self):
        cli = os.path.join(REPO, 'installer', 'bimmhacks_installer.py')
        if not NODE:
            self.skipTest('needs node')
        args = [sys.executable, cli, 'install-ridetube', '--key', self.key, '--ip', '127.0.0.1']
        with FakeSsh():
            r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout[-300:] + r.stderr[-300:])
            self.assertIn('was added to the car', r.stdout)
            os.environ['FAKE_SSH_SOURCE'] = 'waiting'
            r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
            self.assertEqual(r.returncode, 3, r.stdout[-300:] + r.stderr[-300:])
            self.assertIn('Restart the head unit', r.stdout)
            os.environ['FAKE_SSH_SOURCE'] = 'error'
            r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
            self.assertEqual(r.returncode, 1)
            self.assertIn('forbidden', r.stderr)

    def test_bad_repo_or_channel_is_refused_before_ssh(self):
        with FakeSsh():
            car = bi.Car(shutil.which('ssh'), '127.0.0.1', self.key, log=self.lines.append)
            try:
                for repo, chan in (('rm -rf /', 'releases'), ('o/r', 'weekly')):
                    with self.assertRaises(bi.InstallerError):
                        bi.add_store_source(car, repo, chan, log=self.lines.append)
            finally:
                car.close()
        self.assertEqual(self.ssh_log(), [])

    def test_source_is_added_after_the_store_when_the_store_is_missing(self):
        os.environ['FAKE_SSH_NO_STORE'] = '1'
        installs = []
        real = bi.do_store_install
        bi.do_store_install = lambda node, ip, key, **k: installs.append((ip, k.get('from_dir')))
        try:
            with FakeSsh():
                r = bi.do_install_apps_source('node', '127.0.0.1', self.key, 'JustAnotherPU/ridetube', 'RideTube',
                                              log=self.lines.append, from_dir='/staged/store')
        finally:
            bi.do_store_install = real
        self.assertEqual(r, 'added')
        self.assertEqual(installs, [('127.0.0.1', '/staged/store')], 'the Store was installed first, from the staged package')
        log = self.ssh_log()
        self.assertTrue(any(l.startswith('test -f /var/opt/mgu-deploy') for l in log))
        self.assertTrue(any(l.startswith('add-source JustAnotherPU/ridetube releases') for l in log))
        self.assertTrue(any('added as a source' in l for l in self.lines), self.lines[-3:])

    def test_store_present_means_no_reinstall_and_builtin_sources_are_left_alone(self):
        real = bi.do_store_install
        bi.do_store_install = lambda *a, **k: self.fail('the Store must not be installed again')
        try:
            with FakeSsh():
                for repo in (bi.IDRIVE_APPS_REPO, bi.CARAPPS_REPO, 'IterioDev/Car-Apps'):
                    self.assertEqual(bi.do_install_apps_source('node', '127.0.0.1', self.key, repo, 'x', log=self.lines.append), 'builtin')
                self.assertEqual(bi.do_install_apps_source('node', '127.0.0.1', self.key, 'waliworld/Bimmhacks', 'ours',
                                                           log=self.lines.append), 'added')
        finally:
            bi.do_store_install = real
        sent = [l for l in self.ssh_log() if l.startswith('add-source ')]
        self.assertEqual(sent and sent[0].split()[1], 'waliworld/Bimmhacks', sent)
        self.assertEqual(len(sent), 1, 'built-in sources are never sent to the updater')

    def test_staging_to_usb_does_not_touch_the_car(self):
        staged = []
        real = bi.do_usb_repo
        bi.do_usb_repo = lambda node, dest, inputs, log=print: staged.append((dest, inputs))
        try:
            with FakeSsh():
                r = bi.do_install_apps_source('node', '127.0.0.1', self.key, 'JustAnotherPU/ridetube', 'RideTube',
                                              stage_dir=os.path.join(self.dir, 'stick'), log=self.lines.append)
        finally:
            bi.do_usb_repo = real
        self.assertEqual(r, 'staged')
        self.assertEqual(staged, [(os.path.join(self.dir, 'stick'), ['github:JustAnotherPU/ridetube'])])
        self.assertEqual(self.ssh_log(), [])

    def test_no_key_is_refused(self):
        with self.assertRaises(bi.InstallerError):
            bi.do_install_apps_source('node', '127.0.0.1', None, 'JustAnotherPU/ridetube', 'RideTube', log=self.lines.append)


if __name__ == '__main__':
    unittest.main(verbosity=2)
