#!/usr/bin/env python3
"""Restarting the head unit after the Store install, against a fake ssh that behaves like one:

    python3 installer/tests/test_restart.py

The Store only starts after the head unit restarts, and its own restart button needs the Store running - so
the installer has to do it. What is proven here (no car, no network):
  - the request is the Store's own (nsm_control --requestRestart 7 --normal), sent as one quote-free command
  - it is refused while a Store install is running; an NSM refusal, a missing nsm_control and a cut-off ssh
    are told apart
  - the wait sees the unit go down and come back; a unit that never goes down, or never returns, is an
    error that says what to try
  - do_store_install(restart=True) restarts after installing (and only then); the source flow restarts a
    Store whose updater is not up, once, and keeps trying longer afterwards
  - the command line's restart-head-unit
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'installer'))

from test_root_enable import FakeSsh      # noqa: E402
import bimmhacks_installer as bi          # noqa: E402

CLI = os.path.join(REPO, 'installer', 'bimmhacks_installer.py')
FAST = dict(poll_s=0.01)
KNOBS = ('FAKE_SSH_LOG', 'FAKE_SSH_STATE', 'FAKE_SSH_RESTART', 'FAKE_SSH_DOWN_PROBES', 'FAKE_SSH_NEVER_DOWN',
         'FAKE_SSH_NEVER_BACK', 'FAKE_SSH_SOURCE', 'FAKE_SSH_SOURCE_FILE', 'FAKE_SSH_NO_STORE', 'FAKE_SSH_REAL_NODE')


@unittest.skipIf(os.name == 'nt', 'the fake ssh is a shell script')
class RestartTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='restart-test-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.key = os.path.join(self.dir, 'id_rsa')
        with open(self.key, 'w') as f:
            f.write('-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n')
        self.saved = {k: os.environ.get(k) for k in KNOBS}
        for k in KNOBS:
            os.environ.pop(k, None)
        self.addCleanup(self.restore)
        os.environ['FAKE_SSH_LOG'] = os.path.join(self.dir, 'ssh.log')
        os.environ['FAKE_SSH_STATE'] = os.path.join(self.dir, 'state')
        self.lines = []
        self.ssh = FakeSsh()
        self.ssh.__enter__()
        self.addCleanup(self.ssh.__exit__)
        self.car = bi.Car(shutil.which('ssh'), '127.0.0.1', self.key, log=self.lines.append)
        self.addCleanup(self.car.close)

    def restore(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def ssh_log(self):
        path = os.environ['FAKE_SSH_LOG']
        if not os.path.isfile(path):
            return []
        with open(path) as f:
            return f.read().splitlines()

    def restarts_sent(self):
        return [l for l in self.ssh_log() if l.startswith('L=/var/opt/mgu-deploy/lock;')]

    # ---- the request ---------------------------------------------------------------------------------
    def test_the_request_is_the_stores_own_and_quote_free(self):
        bi.restart_head_unit(self.car, self.lines.append)
        self.assertEqual(self.restarts_sent(), [bi.RESTART_CMD])
        self.assertIn('/usr/bin/nsm_control --requestRestart 7 --normal', bi.RESTART_CMD)
        self.assertNotIn('"', bi.RESTART_CMD)         # Windows passes the remote command through its own quoting
        self.assertNotIn('\\', bi.RESTART_CMD)
        self.assertIn('Restart requested.', self.lines)

    def test_refused_while_an_install_runs(self):
        os.environ['FAKE_SSH_RESTART'] = 'busy'
        with self.assertRaises(bi.InstallerError) as cm:
            bi.restart_head_unit(self.car, self.lines.append)
        self.assertIn('install is still running', str(cm.exception))

    def test_an_nsm_refusal_is_reported_with_the_way_out(self):
        os.environ['FAKE_SSH_RESTART'] = 'error'
        with self.assertRaises(bi.InstallerError) as cm:
            bi.restart_head_unit(self.car, self.lines.append)
        self.assertIn('ProxyLifecycleControl not available', str(cm.exception))
        self.assertIn('Settings > Restart head unit', str(cm.exception))

    def test_a_unit_without_nsm_control(self):
        os.environ['FAKE_SSH_RESTART'] = 'missing'
        with self.assertRaises(bi.InstallerError) as cm:
            bi.restart_head_unit(self.car, self.lines.append)
        self.assertIn('no restart command', str(cm.exception))

    # ---- the command that runs on the car, for real (in sh, against lock files) ----------------------------
    def run_restart_cmd(self, lock_text=None, lock_pid_alive=True, boot_id='same'):
        """RESTART_CMD in a real sh with the lock path and nsm_control pointed at stand-ins
        -> (exit status, output, whether nsm_control was called)"""
        lock = os.path.join(self.dir, 'lock')
        marker = os.path.join(self.dir, 'nsm-called')
        for f in (lock, marker):
            if os.path.exists(f):
                os.remove(f)
        stub = os.path.join(self.dir, 'nsm_control')
        with open(stub, 'w') as f:
            f.write('#!/bin/sh\necho "$@" > %s\n' % marker)
        os.chmod(stub, 0o755)
        if lock_text is not None:
            with open('/proc/sys/kernel/random/boot_id') as f:
                real_boot = f.read().strip()
            holder = subprocess.Popen(['sleep', '30']) if lock_pid_alive else None
            pid = holder.pid if holder else 999999
            boot = real_boot if boot_id == 'same' else 'a-different-boot'
            with open(lock, 'w') as f:
                f.write(lock_text.format(pid=pid, boot=boot))
            self.addCleanup(lambda: holder and holder.kill())
        cmd = bi.RESTART_CMD.replace('/var/opt/mgu-deploy/lock', lock).replace('/usr/bin/nsm_control', stub)
        r = subprocess.run(['sh', '-c', cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=30)
        return r.returncode, r.stdout, os.path.isfile(marker)

    @unittest.skipUnless(os.path.isfile('/proc/sys/kernel/random/boot_id'), 'needs Linux')
    def test_the_shell_command_on_the_car(self):
        # no install running: the restart is requested, with the Store's arguments
        rc, out, called = self.run_restart_cmd()
        self.assertEqual((rc, called), (0, True), out)
        with open(os.path.join(self.dir, 'nsm-called')) as f:
            self.assertEqual(f.read().strip(), '--requestRestart 7 --normal')
        # a lock held by a live process of this boot: refused, nothing requested
        rc, out, called = self.run_restart_cmd('{pid} {boot}\n')
        self.assertEqual((rc, called), (3, False), out)
        self.assertIn('INSTALL_RUNNING', out)
        # a lock left by an earlier boot, or by a process that is gone, or an empty one: not an install
        for kwargs in ({'lock_text': '{pid} {boot}\n', 'boot_id': 'other'},
                       {'lock_text': '{pid} {boot}\n', 'lock_pid_alive': False},
                       {'lock_text': ''},
                       {'lock_text': 'garbage'}):
            rc, out, called = self.run_restart_cmd(**kwargs)
            self.assertEqual((rc, called), (0, True), (kwargs, out))

    # ---- the wait ------------------------------------------------------------------------------------
    def test_the_wait_sees_it_go_down_and_come_back(self):
        os.environ['FAKE_SSH_DOWN_PROBES'] = '3'
        bi.restart_head_unit(self.car, self.lines.append)
        bi.wait_for_restart(self.car, self.lines.append, **FAST)
        text = '\n'.join(self.lines)
        self.assertIn('the head unit is down', text)
        self.assertIn('the head unit is back', text)
        self.assertLess(text.index('is down'), text.index('is back'))

    def test_a_unit_that_never_goes_down_is_an_error(self):
        os.environ['FAKE_SSH_NEVER_DOWN'] = '1'
        bi.restart_head_unit(self.car, self.lines.append)
        with self.assertRaises(bi.InstallerError) as cm:
            bi.wait_for_restart(self.car, self.lines.append, down_s=0.3, **FAST)
        self.assertIn('did not restart', str(cm.exception))
        self.assertIn('ignition off', str(cm.exception))

    def test_a_unit_that_never_comes_back_says_what_to_try(self):
        os.environ['FAKE_SSH_NEVER_BACK'] = '1'
        bi.restart_head_unit(self.car, self.lines.append)
        with self.assertRaises(bi.InstallerError) as cm:
            bi.wait_for_restart(self.car, self.lines.append, up_s=0.3, **FAST)
        self.assertIn('Enable (repair)', str(cm.exception))

    # ---- through the real entry points ---------------------------------------------------------------
    def test_do_restart_head_unit(self):
        bi.do_restart_head_unit('127.0.0.1', self.key, log=self.lines.append, **FAST)
        self.assertEqual(len(self.restarts_sent()), 1)
        self.assertTrue(any('is back' in l for l in self.lines))
        with self.assertRaises(bi.InstallerError):
            bi.do_restart_head_unit('127.0.0.1', os.path.join(self.dir, 'nokey'), log=self.lines.append)

    def fake_first_install(self):
        """the Store installer (node mgu-install.js) stood in for: records its arguments"""
        seen = {}

        class Proc(object):
            stdout = BytesIO(b'first-install: done\n')
            stdin = BytesIO()

            @staticmethod
            def wait():
                return 0
        real = bi.popen_noinput
        bi.popen_noinput = lambda argv, **k: (seen.setdefault('argv', argv), Proc)[1]
        self.addCleanup(setattr, bi, 'popen_noinput', real)
        return seen

    def test_store_install_restarts_only_when_asked(self):
        seen = self.fake_first_install()
        bi.do_store_install('node', '127.0.0.1', self.key, log=self.lines.append)
        self.assertEqual(self.restarts_sent(), [], 'no restart unless asked')
        self.assertTrue(any('press "Restart head unit"' in l for l in self.lines), self.lines[-3:])
        self.assertIn('--no-restart-prompt', seen['argv'])
        self.lines[:] = []
        real = bi.wait_for_restart
        bi.wait_for_restart = lambda car, log=print, **k: real(car, log, **dict(k, **FAST))
        self.addCleanup(setattr, bi, 'wait_for_restart', real)
        self.fake_first_install()
        bi.do_store_install('node', '127.0.0.1', self.key, log=self.lines.append, restart=True)
        self.assertEqual(len(self.restarts_sent()), 1)
        self.assertTrue(any('The head unit is back' in l for l in self.lines), self.lines[-4:])

    def patch_fast_wait(self):
        real = bi.wait_for_restart
        bi.wait_for_restart = lambda car, log=print, **k: real(car, log, **dict(k, **FAST))
        self.addCleanup(setattr, bi, 'wait_for_restart', real)

    @unittest.skipUnless(shutil.which('node'), 'needs node')
    def test_a_store_whose_updater_is_not_up_is_restarted_once_and_retried(self):
        self.patch_fast_wait()
        answers = os.path.join(self.dir, 'answers')
        with open(answers, 'w') as f:
            f.write('waiting\nadded\n')
        os.environ['FAKE_SSH_SOURCE_FILE'] = answers
        r = bi.do_install_apps_source('node', '127.0.0.1', self.key, bi.RIDETUBE_REPO, 'RideTube',
                                      log=self.lines.append, restart_store=True)
        self.assertEqual(r, 'added', '\n'.join(self.lines[-8:]))
        self.assertEqual(len(self.restarts_sent()), 1)
        adds = [l for l in self.ssh_log() if l.startswith('add-source ')]
        self.assertEqual(len(adds), 2)
        self.assertTrue(any('--wait=150' in l for l in self.ssh_log() if l.startswith('/usr/bin/node - ')),
                        'after a restart the updater gets two and a half minutes')

    @unittest.skipUnless(shutil.which('node'), 'needs node')
    def test_without_permission_to_restart_it_stays_pending(self):
        os.environ['FAKE_SSH_SOURCE'] = 'waiting'
        r = bi.do_install_apps_source('node', '127.0.0.1', self.key, bi.RIDETUBE_REPO, 'RideTube', log=self.lines.append)
        self.assertEqual(r, 'pending')
        self.assertEqual(self.restarts_sent(), [])
        self.assertTrue(any('Restart head unit' in l for l in self.lines), self.lines[-3:])

    @unittest.skipUnless(shutil.which('node'), 'needs node')
    def test_it_does_not_restart_twice(self):
        self.patch_fast_wait()
        os.environ['FAKE_SSH_SOURCE'] = 'waiting'        # the updater never comes up
        r = bi.do_install_apps_source('node', '127.0.0.1', self.key, bi.RIDETUBE_REPO, 'RideTube',
                                      log=self.lines.append, restart_store=True)
        self.assertEqual(r, 'slow', 'restarted, still not up: not "restart it" again')
        self.assertEqual(len(self.restarts_sent()), 1, 'one restart, then it says so instead of looping')
        self.assertIn('few more minutes', bi.source_done_message(r, 'RideTube'))
        self.assertNotIn('Restart the head unit', bi.source_done_message(r, 'RideTube'))

    @unittest.skipUnless(shutil.which('node'), 'needs node')
    def test_a_store_installed_here_is_restarted_by_the_install_not_again(self):
        self.patch_fast_wait()
        os.environ['FAKE_SSH_NO_STORE'] = '1'
        installs = []
        real = bi.do_store_install
        bi.do_store_install = lambda node, ip, key, **k: installs.append(k.get('restart'))
        self.addCleanup(setattr, bi, 'do_store_install', real)
        r = bi.do_install_apps_source('node', '127.0.0.1', self.key, bi.RIDETUBE_REPO, 'RideTube',
                                      log=self.lines.append, restart_store=True)
        self.assertEqual(installs, [True])
        self.assertEqual(r, 'added')
        self.assertEqual(self.restarts_sent(), [], 'the install does its own restart; this flow adds none')
        self.assertTrue(any('--wait=150' in l for l in self.ssh_log() if l.startswith('/usr/bin/node - ')))

    # ---- the command line ----------------------------------------------------------------------------
    def test_cli_restart_head_unit(self):
        args = [sys.executable, CLI, 'restart-head-unit', '--key', self.key, '--ip', '127.0.0.1']
        # one failed probe after the request, then the unit answers: no sleeping in the wait loop
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120,
                           env=dict(os.environ, FAKE_SSH_DOWN_PROBES='1'))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('the head unit is back', r.stdout)
        self.assertIn('The head unit restarted.', r.stdout)
        r = subprocess.run(args + ['--no-wait'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           timeout=60, env=dict(os.environ, FAKE_SSH_STATE=os.path.join(self.dir, 'state2')))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('Restart requested.', r.stdout)
        os.environ['FAKE_SSH_RESTART'] = 'busy'
        r = subprocess.run(args + ['--no-wait'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           timeout=60, env=dict(os.environ))
        self.assertEqual(r.returncode, 1)
        self.assertIn('install is still running', r.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
