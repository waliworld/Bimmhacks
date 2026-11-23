#!/usr/bin/env python3
"""The on-car cluster / MGU Styles check (do_mgu_check), against a fake head unit:

    python3 installer/tests/test_mgu_check.py

What is proven here (no car, no network):
  - the script mgu-check.js exists, is valid ES5/Node, runs stand-alone (exit 0 with a report, 2 on a bad arg)
  - do_mgu_check logs in (test_connection), stages the script to /var/opt/bimmhacks-check and runs it with node
  - it only reads the car: the commands it sends are mkdir, cat (stage our own script) and node - nothing that
    changes head-unit state
  - a non-zero run, a bad-argument exit 2, and a missing script are each a clear error
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'installer'))

import bimmhacks_installer as bi          # noqa: E402

REPORT = ('== mgu-check 1.0.0 - 2026-01-01T00:00:00.000Z\n\n'
          'OK   cluster renderer answering (id7)\n'
          'OK   MGU Styles installed, 0.10.3\n')


class ScriptedCar:
    """A fake Car: test_connection sees a rooted MGU, the node run returns a report. Records every command."""

    def __init__(self, ssh=None, ip='169.254.36.47', key=None, log=print, node_code=0, node_out=REPORT):
        self.ip = ip
        self.cmds = []
        self.node_code, self.node_out = node_code, node_out

    def run(self, cmd, stdin_data=None, echo=True):
        self.cmds.append(cmd)
        if 'echo CONNECTED' in cmd:                      # test_connection probe
            return 0, 'CONNECTED\n0\nMGU_OAP_OK\n'
        if cmd.startswith('mkdir'):
            return 0, ''
        if cmd.startswith('cat >'):
            assert stdin_data and b'mgu-check' in stdin_data, 'the script bytes must be piped to the car'
            return 0, ''
        if 'node mgu-check.js' in cmd:
            return self.node_code, self.node_out
        raise AssertionError('unexpected command: %r' % cmd)

    def close(self):
        pass


class MguCheckScript(unittest.TestCase):
    def test_script_runs_standalone(self):
        js = os.path.join(REPO, 'tools', 'mgu-check', 'mgu-check.js')
        self.assertTrue(os.path.isfile(js), 'mgu-check.js must ship under tools/mgu-check/')
        node = bi.find_node()
        if not node:
            self.skipTest('node not installed')
        r = subprocess.run([node, js], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 0, 'a report must always be made (exit 0) even with nothing to read')
        self.assertIn(b'mgu-check', r.stdout)
        bad = subprocess.run([node, js, '--nope'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(bad.returncode, 2, 'a bad argument must exit 2')


class DoMguCheck(unittest.TestCase):
    def setUp(self):
        self.real_car = bi.Car
        self.key = os.path.join(HERE, 'fake_key')
        with open(self.key, 'w') as f:
            f.write('-----BEGIN KEY-----\nx\n-----END KEY-----\n')
        self.addCleanup(lambda: os.path.exists(self.key) and os.remove(self.key))
        self.addCleanup(lambda: setattr(bi, 'Car', self.real_car))

    def use(self, **kw):
        car = {}

        def make(ssh, ip, key, log=print):
            car['car'] = ScriptedCar(ssh, ip, key, log, **kw)
            return car['car']
        bi.Car = make
        return car

    def test_stages_and_runs_readonly(self):
        car = self.use()
        lines = []
        code, out = bi.do_mgu_check('ssh', '169.254.36.47', self.key, log=lines.append)
        self.assertEqual(code, 0)
        self.assertIn('MGU Styles installed', out)
        cmds = car['car'].cmds
        self.assertTrue(any('node mgu-check.js' in c for c in cmds), 'it must run the check with node')
        self.assertTrue(any(c.startswith('cat >') and 'bimmhacks-check' in c for c in cmds), 'it must stage the script')
        # read-only: nothing that changes car state (no rm/systemctl/install/write beyond staging our script)
        joined = ' ; '.join(cmds)
        for bad in (' rm ', 'systemctl', 'install.sh', 'nsm_control', '> /etc', '> /var/opt/public'):
            self.assertNotIn(bad, joined, 'the check must not change the head unit')

    def test_bad_argument_exit2_is_flagged(self):
        self.use(node_code=2, node_out='usage: node mgu-check.js [--json]\n')
        with self.assertRaises(bi.InstallerError) as cm:
            bi.do_mgu_check('ssh', '169.254.36.47', self.key, log=lambda m: None)
        self.assertIn('exit 2', str(cm.exception))

    def test_run_failure_is_flagged(self):
        self.use(node_code=1, node_out='node: not found\n')
        with self.assertRaises(bi.InstallerError) as cm:
            bi.do_mgu_check('ssh', '169.254.36.47', self.key, log=lambda m: None)
        self.assertIn('could not run', str(cm.exception))

    def test_missing_script_is_flagged(self):
        self.use()
        real = bi.MGUCHECK_JS
        bi.MGUCHECK_JS = os.path.join(HERE, 'no-such-mgu-check.js')
        self.addCleanup(lambda: setattr(bi, 'MGUCHECK_JS', real))
        with self.assertRaises(bi.InstallerError) as cm:
            bi.do_mgu_check('ssh', '169.254.36.47', self.key, log=lambda m: None)
        self.assertIn('missing', str(cm.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
