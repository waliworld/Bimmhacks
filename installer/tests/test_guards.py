#!/usr/bin/env python3
"""Safety guards of the installer, proven against a FAKE car (installer/tests/fake_enet.py): a refused
action must not send the car a single request.

    python3 installer/tests/test_guards.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'installer'))
sys.path.insert(0, os.path.join(REPO, 'tools', 'idrive7-8-ssh-root'))

from fake_enet import FakeCar            # noqa: E402
import bimmhacks_installer as bi          # noqa: E402

import importlib.util
HAVE_CRYPTO = importlib.util.find_spec('cryptography') is not None

CLI = os.path.join(REPO, 'installer', 'bimmhacks_installer.py')
VIN = 'WBA12345678901234'


_STUB = None


def setUpModule():
    """The installer checks for an ssh client before anything else. These tests are about what happens next,
    so where a machine has none (a bare container), put a do-nothing one on PATH. A real ssh is never replaced."""
    global _STUB
    if shutil.which('ssh'):
        return
    _STUB = tempfile.mkdtemp(prefix='ssh-stub-')
    path = os.path.join(_STUB, 'ssh')
    with open(path, 'w') as f:
        f.write('#!/bin/sh\nexit 255\n')
    os.chmod(path, 0o755)


def tearDownModule():
    if _STUB:
        shutil.rmtree(_STUB, ignore_errors=True)


def cli(*args, timeout=120):
    env = dict(os.environ)
    if _STUB:
        env['PATH'] = _STUB + os.pathsep + env.get('PATH', '')
    return subprocess.run([sys.executable, CLI] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=timeout, env=env)


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
class FlashNeedsBackupConfirmation(unittest.TestCase):
    def test_cli_flash_without_confirmation_sends_nothing(self):
        for action in ('root-flash', 'root-fix-key'):
            with self.subTest(action=action), FakeCar() as car:
                r = cli(action, '--vin', VIN, '--gateway', '127.0.0.1')
                self.assertEqual(r.returncode, 1)
                self.assertIn('--confirm-backup', r.stderr)
                self.assertIn('WIPES', r.stderr)
                self.assertEqual(car.requests, [], 'the car must not be touched without the confirmation')

    def test_flag_opens_the_gate_but_a_bad_vin_still_stops_it(self):
        for bad in (None, 'SHORT', 'WBA1234567890123I', 'WBA1234567890123O', 'WBA1234567890123Q', 'WBA123456789012345'):
            with self.subTest(vin=bad), FakeCar() as car:
                args = ['root-flash', '--gateway', '127.0.0.1', '--confirm-backup'] + (['--vin', bad] if bad else [])
                r = cli(*args)
                self.assertEqual(r.returncode, 1)
                self.assertIn('VIN', r.stderr)
                self.assertEqual(car.requests, [], 'a bad VIN must not reach the car (%r)' % bad)

    def test_the_gate_is_in_do_root_itself(self):
        # not only the CLI: any caller of do_root() is held to it
        with FakeCar() as car:
            for action in ('flash', 'fix-key'):
                with self.assertRaises(bi.InstallerError):
                    bi.do_root(action, lambda m: None, vin=VIN, gateway='127.0.0.1')
            self.assertEqual(car.requests, [])

    def test_read_only_actions_need_no_confirmation(self):
        with FakeCar() as car:
            r = cli('root-status', '--gateway', '127.0.0.1', '--ip', '127.0.0.1')
            self.assertEqual(r.returncode, 0, r.stdout[-400:] + r.stderr[-400:])
            self.assertTrue(car.services() <= {0x22, 0x19, 0x31})
            self.assertNotIn(0x27, car.services(), 'no security access in a status read')

    def test_confirmed_flash_does_start(self):
        # the gate opens only with the flag: the car then sees the flash sequence begin (the fake car does not
        # implement flashing, so the run itself fails - what matters is that it was allowed to start)
        with FakeCar() as car:
            cli('root-flash', '--vin', VIN, '--gateway', '127.0.0.1', '--confirm-backup')
            self.assertIn(0x10, car.services())      # DiagnosticSessionControl: the flash sequence started


class OtherGuards(unittest.TestCase):
    def test_store_actions_need_a_key_or_a_stage_folder(self):
        for action in ('store-install', 'install-idrive-apps', 'install-car-apps', 'install-ridetube'):
            r = cli(action)
            self.assertNotEqual(r.returncode, 0, action)

    def test_custom_source_must_look_like_owner_repo(self):
        r = cli('install-our-apps', '--key', os.path.join(HERE, 'fake_enet.py'), '--repo', 'not a repo; rm -rf /')
        self.assertEqual(r.returncode, 1)
        self.assertIn('owner/repo', r.stderr)

    def test_bad_head_unit_address_is_refused(self):
        r = cli('test', '--key', os.path.join(HERE, 'fake_enet.py'), '--ip', '1.2.3.4;reboot')
        self.assertEqual(r.returncode, 1)
        self.assertIn('valid head unit address', r.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
