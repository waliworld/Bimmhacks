#!/usr/bin/env python3
"""The add-source step against the Store's REAL updater (no car, no network):

    MGU_STORE_SRC=/path/to/mgu-store-checkout python3 installer/tests/test_store_updater.py

installer/unit_add_source.js runs on the head unit and asks the Store's updater to add a source. Here the
updater is the real thing - the daemon from https://github.com/robertnisipeanu/mgu-store, pinned in
installer/tests/MGU_STORE_COMMIT - running in-process (installer/tests/updater_harness.js) on a throw-away
oap folder, sources.json seeded exactly as first-install seeds it, its GitHub addresses pointing at a
closed port so nothing leaves the machine. What it proves:
  - the script, run the way the installer runs it (`node -`, the script on stdin), adds RideTube and the
    real updater writes it into sources.json, which the real parser then loads
  - a repeat, a different spelling, and the built-in sources (idrive7-apps, idrive-apps, car-apps) are
    "already there"; a wrong secret is refused; an id collision still gives a valid file
  - an updater that is not running yet (a Store installed a minute ago) is reported as WAITING, not as a failure
  - installer.add_store_source() / do_install_apps_source() work end to end through a fake ssh that runs the
    command with the local node
Skipped when node or a mgu-store checkout is missing (the checkout is not part of this package).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'installer'))

from test_root_enable import FakeSsh      # noqa: E402
import bimmhacks_installer as bi          # noqa: E402

NODE = shutil.which('node')
SCRIPT = os.path.join(REPO, 'installer', 'unit_add_source.js')
HARNESS = os.path.join(HERE, 'updater_harness.js')


def find_store_checkout():
    for c in (os.environ.get('MGU_STORE_SRC'), os.path.expanduser('~/cauliflowermossgrape/mgu-store'),
              '/tmp/mgu-store-src'):
        if c and os.path.isfile(os.path.join(c, 'native', 'mgu-updater', 'js', 'updater.js')):
            return c
    return None


STORE_SRC = find_store_checkout()
WHY_SKIP = 'needs node, a mgu-store checkout (MGU_STORE_SRC) and a Unix machine'
CAN_RUN = bool(NODE and STORE_SRC and os.name != 'nt')


class RealUpdater(object):
    """the real mgu-updater on a throw-away head unit layout; the work dir is short: a unix socket's path is
    limited to ~100 characters"""

    def __init__(self):
        self.work = tempfile.mkdtemp(prefix='hx-', dir='/tmp' if os.path.isdir('/tmp') else None)
        self.log = open(os.path.join(self.work, 'harness.log'), 'w')
        self.proc = subprocess.Popen([NODE, HARNESS, STORE_SRC, self.work], stdout=self.log, stderr=subprocess.STDOUT)
        ready = os.path.join(self.work, 'ready.json')
        end = time.time() + 30
        while not os.path.isfile(ready):
            if self.proc.poll() is not None or time.time() > end:
                self.close()
                raise AssertionError('the real updater did not start:\n' + open(os.path.join(self.work, 'harness.log')).read()[-800:]
                                     if os.path.isdir(self.work) else 'the real updater did not start')
            time.sleep(0.1)
        with open(ready) as f:
            self.info = json.load(f)

    @property
    def sock(self):
        return self.info['socket']

    @property
    def oap(self):
        return self.info['oap']

    @property
    def sources_file(self):
        return self.info['sources']

    def sources(self):
        with open(self.sources_file) as f:
            return json.load(f)['sources']

    def parses_with_the_real_parser(self):
        """the updater's own sources.js reads the file back (its schema checks included) -> the list"""
        js = ("const s = require(%r); const l = s.load(%r); console.log(JSON.stringify(l.map(x => x.id)));"
              % (os.path.join(STORE_SRC, 'native', 'mgu-updater', 'js', 'sources.js'), self.sources_file))
        r = subprocess.run([NODE, '-e', js], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def env(self, **extra):
        e = dict(os.environ, MGU_OAP_ROOT=self.oap, MGU_UPDATER_SOCKET=self.sock, MGU_ADD_SOURCE_RETRIES='2',
                 MGU_ADD_SOURCE_WAIT_MS='150', MGU_ADD_SOURCE_ANSWER_MS='5000')
        e.update(extra)
        return e

    def run_script(self, repo, channel, *args, **extra_env):
        """the script exactly as the installer sends it: `node - <repo> <channel>`, the file on stdin"""
        with open(SCRIPT, 'rb') as f:
            script = f.read()
        r = subprocess.run([NODE, '-', repo, channel] + list(args), input=script, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=60, env=self.env(**extra_env))
        return r.returncode, r.stdout.decode('utf-8', 'replace').strip().splitlines()

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()
        shutil.rmtree(self.work, ignore_errors=True)


@unittest.skipUnless(CAN_RUN, WHY_SKIP)
class ScriptAgainstTheRealUpdater(unittest.TestCase):
    def setUp(self):
        self.up = RealUpdater()
        self.addCleanup(self.up.close)

    def test_the_seed_is_what_the_real_updater_accepts(self):
        self.assertEqual(self.up.info['stored'], ['mgu-store', 'idrive7-apps', 'idrive-apps', 'car-apps'])

    def test_adds_ridetube_and_the_real_updater_writes_it(self):
        rc, out = self.up.run_script('JustAnotherPU/ridetube', 'releases')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out[-1], 'ADDED JustAnotherPU/ridetube (releases)')
        srcs = self.up.sources()
        self.assertEqual([s['id'] for s in srcs], ['mgu-store', 'idrive7-apps', 'idrive-apps', 'car-apps', 'ridetube'])
        self.assertEqual(srcs[-1], {'id': 'ridetube', 'type': 'github', 'repo': 'JustAnotherPU/ridetube', 'channel': 'releases'})
        self.assertEqual(self.up.parses_with_the_real_parser(), [s['id'] for s in srcs])

    def test_a_repeat_or_another_spelling_is_already_there(self):
        self.up.run_script('JustAnotherPU/ridetube', 'releases')
        for spelling in ('JustAnotherPU/ridetube', 'justanotherpu/RideTube'):
            rc, out = self.up.run_script(spelling, 'releases')
            self.assertEqual(rc, 0, out)
            self.assertEqual(out[-1], 'ALREADY ' + spelling)
        self.assertEqual(len(self.up.sources()), 5)

    def test_the_built_in_sources_are_already_in_the_store(self):
        # what the installer treats as built in must be what first-install really seeds
        for repo in bi.BUILTIN_SOURCES:
            rc, out = self.up.run_script(repo, 'releases')
            self.assertEqual((rc, out[-1]), (0, 'ALREADY ' + repo), out)
        self.assertEqual(len(self.up.sources()), 4)
        seeded = {s['repo'].lower() for s in self.up.sources() if not s.get('builtin')}
        self.assertEqual(seeded, set(bi.BUILTIN_SOURCES))

    def test_a_wrong_secret_is_refused_and_nothing_changes(self):
        wrong = os.path.join(self.up.work, 'wrong-secret')
        with open(wrong, 'w') as f:
            f.write('f' * 64)
        rc, out = self.up.run_script('JustAnotherPU/ridetube', 'releases', self.up.sock, wrong)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out[-1], 'ERROR: the updater refused the secret (forbidden)')
        self.assertEqual(len(self.up.sources()), 4)

    def test_channels_and_an_id_collision(self):
        self.assertEqual(self.up.run_script('JustAnotherPU/ridetube', 'releases')[0], 0)
        rc, out = self.up.run_script('someone-else/ridetube', 'branch:main')
        self.assertEqual((rc, out[-1]), (0, 'ADDED someone-else/ridetube (branch:main)'), out)
        rc, out = self.up.run_script('waliworld/Bimmhacks', 'tags')
        self.assertEqual((rc, out[-1]), (0, 'ADDED waliworld/Bimmhacks (tags)'), out)
        srcs = self.up.sources()
        ids = [s['id'] for s in srcs]
        self.assertEqual(len(ids), len(set(ids)), 'every id is unique: %s' % ids)
        self.assertEqual([s['channel'] for s in srcs[-3:]], ['releases', 'branch:main', 'tags'])
        self.assertEqual(self.up.parses_with_the_real_parser(), ids)

    def test_an_updater_that_is_not_running_is_waiting_not_failed(self):
        rc, out = self.up.run_script('JustAnotherPU/ridetube', 'releases', os.path.join(self.up.work, 'nothing.sock'))
        self.assertEqual(rc, 3, out)
        self.assertTrue(out[-1].startswith('WAITING: the updater is not running'), out)
        self.assertEqual(len(self.up.sources()), 4)

    def test_a_store_without_its_secret_yet_is_waiting(self):
        empty = tempfile.mkdtemp(prefix='oap-', dir='/tmp')
        self.addCleanup(shutil.rmtree, empty, True)
        rc, out = self.up.run_script('JustAnotherPU/ridetube', 'releases', MGU_OAP_ROOT=empty)
        self.assertEqual(rc, 3, out)
        self.assertIn('WAITING: ', out[-1])
        self.assertIn('no updater secret', out[-1])

    def test_the_secret_is_found_without_the_registry_entry(self):
        os.remove(os.path.join(self.up.oap, 'oap_12201', 'data', 'apps', 'robertn_store.json'))
        rc, out = self.up.run_script('JustAnotherPU/ridetube', 'releases')
        self.assertEqual((rc, out[-1]), (0, 'ADDED JustAnotherPU/ridetube (releases)'), out)


@unittest.skipUnless(CAN_RUN, WHY_SKIP)
class InstallerThroughFakeSsh(unittest.TestCase):
    """add_store_source() / do_install_apps_source(): Python -> Car.run -> (fake) ssh -> the script with the
    local node -> the real updater"""

    def setUp(self):
        self.up = RealUpdater()
        self.addCleanup(self.up.close)
        self.key = os.path.join(self.up.work, 'id_rsa')
        with open(self.key, 'w') as f:
            f.write('-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n')
        self.saved = {k: os.environ.get(k) for k in ('FAKE_SSH_REAL_NODE', 'MGU_UPDATER_SOCKET', 'MGU_OAP_ROOT',
                                                     'MGU_ADD_SOURCE_RETRIES', 'MGU_ADD_SOURCE_WAIT_MS', 'FAKE_SSH_NO_STORE')}
        os.environ.update(FAKE_SSH_REAL_NODE='1', MGU_UPDATER_SOCKET=self.up.sock, MGU_OAP_ROOT=self.up.oap,
                          MGU_ADD_SOURCE_RETRIES='2', MGU_ADD_SOURCE_WAIT_MS='150')
        os.environ.pop('FAKE_SSH_NO_STORE', None)
        self.addCleanup(self.restore_env)
        self.lines = []

    def restore_env(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_do_install_apps_source_adds_ridetube(self):
        with FakeSsh():
            r = bi.do_install_apps_source('node', '127.0.0.1', self.key, bi.RIDETUBE_REPO, 'RideTube', log=self.lines.append)
        self.assertEqual(r, 'added', '\n'.join(self.lines))
        self.assertEqual(self.up.sources()[-1]['repo'], bi.RIDETUBE_REPO)
        self.assertEqual(self.up.parses_with_the_real_parser()[-1], 'ridetube')
        with FakeSsh():
            self.assertEqual(bi.do_install_apps_source('node', '127.0.0.1', self.key, bi.RIDETUBE_REPO, 'RideTube',
                                                       log=self.lines.append), 'already')

    def test_a_store_whose_updater_is_not_up_yet_is_pending_not_an_error(self):
        os.environ['MGU_UPDATER_SOCKET'] = os.path.join(self.up.work, 'nothing.sock')
        with FakeSsh():
            r = bi.do_install_apps_source('node', '127.0.0.1', self.key, bi.RIDETUBE_REPO, 'RideTube', log=self.lines.append)
        self.assertEqual(r, 'pending')
        self.assertTrue(any('restart the head unit' in l.lower() for l in self.lines), self.lines[-4:])
        self.assertEqual(len(self.up.sources()), 4)

    def test_a_just_installed_store_is_installed_first_then_waited_for(self):
        os.environ['FAKE_SSH_NO_STORE'] = '1'
        installs = []
        real = bi.do_store_install
        bi.do_store_install = lambda node, ip, key, **k: installs.append(k.get('from_dir'))
        os.environ['MGU_UPDATER_SOCKET'] = os.path.join(self.up.work, 'nothing.sock')      # not started yet
        try:
            with FakeSsh():
                r = bi.do_install_apps_source('node', '127.0.0.1', self.key, bi.RIDETUBE_REPO, 'RideTube',
                                              log=self.lines.append, from_dir='/staged')
        finally:
            bi.do_store_install = real
        self.assertEqual(installs, ['/staged'])
        self.assertEqual(r, 'pending')

    def test_a_refusal_is_an_error_with_the_updaters_reason(self):
        with open(os.path.join(self.up.oap, 'oap_13050', 'data', 'updater-secret'), 'w') as f:
            f.write('0' * 64)
        with FakeSsh():
            with self.assertRaises(bi.InstallerError) as cm:
                bi.do_install_apps_source('node', '127.0.0.1', self.key, bi.RIDETUBE_REPO, 'RideTube', log=self.lines.append)
        self.assertIn('forbidden', str(cm.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
