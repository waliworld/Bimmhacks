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
                args = (['root-flash', '--gateway', '127.0.0.1', '--confirm-backup', '--arch', 'id7']
                        + (['--vin', bad] if bad else []))
                r = cli(*args)
                self.assertEqual(r.returncode, 1)
                self.assertIn('VIN', r.stderr)
                self.assertEqual(car.requests, [], 'a bad VIN must not reach the car (%r)' % bad)

    def test_flash_without_a_head_unit_is_refused_and_sends_nothing(self):
        # no --arch: the tool no longer assumes id7, and refuses before touching the car
        with FakeCar() as car:
            r = cli('root-flash', '--vin', VIN, '--gateway', '127.0.0.1', '--confirm-backup')
            self.assertEqual(r.returncode, 1)
            self.assertIn('head unit', r.stderr)
            self.assertEqual(car.requests, [], 'a missing head unit must not reach the car')

    def test_arch_mismatch_is_refused_before_any_write(self):
        # the FakeCar reports BTLD 030.016 (= iDrive 7); selecting an id8 variant must be refused after the
        # read-only software-level check and before any flash write (no security access, no download)
        with FakeCar() as car:
            r = cli('root-flash', '--vin', VIN, '--gateway', '127.0.0.1', '--ip', '127.0.0.1',
                    '--confirm-backup', '--arch', 'id8-bdc3')
            self.assertEqual(r.returncode, 1)
            self.assertIn('MISMATCH', r.stderr)
            self.assertNotIn(0x27, car.services(), 'no SecurityAccess on a refused arch mismatch')
            self.assertNotIn(0x34, car.services(), 'no download on a refused arch mismatch')

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
        # the gate opens with the flag AND a matching head unit (FakeCar is id7, so --arch id7): the car then
        # sees the flash sequence begin (the fake car does not implement flashing, so the run itself fails -
        # what matters is that it was allowed to start)
        with FakeCar() as car:
            cli('root-flash', '--vin', VIN, '--gateway', '127.0.0.1', '--confirm-backup', '--arch', 'id7')
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


class ArchDetection(unittest.TestCase):
    """The head-unit detection + arch guard, as pure logic (no car)."""

    def test_software_family_thresholds(self):
        self.assertEqual(bi.software_family(16), 'id7')    # base M5 016.018
        self.assertEqual(bi.software_family(24), 'id7')    # G30 024.010
        self.assertEqual(bi.software_family(30), 'id7')    # verified 030.016
        self.assertEqual(bi.software_family(36), 'id8')    # the stuck S58 036.010
        self.assertEqual(bi.software_family(40), 'id8')    # a future id8 level
        self.assertIsNone(bi.software_family(33))          # between the two known bands: unknown

    def test_arch_family(self):
        self.assertEqual(bi.arch_family('id7'), 'id7')
        self.assertEqual(bi.arch_family('id8-bdc3'), 'id8')
        self.assertEqual(bi.arch_family('id8-bcp'), 'id8')
        self.assertEqual(bi.arch_family(None), 'id7')

    def test_assert_refuses_id7_on_id8_but_allows_matching_id8(self):
        real = bi.detect_head_unit
        bi.detect_head_unit = lambda log, ip=None, gateway='auto': ('id8', '036.010.043')
        self.addCleanup(setattr, bi, 'detect_head_unit', real)
        with self.assertRaises(bi.InstallerError) as cm:
            bi.assert_arch_matches(lambda m: None, 'id7')
        self.assertIn('MISMATCH', str(cm.exception))
        self.assertIn('id8-bdc3', str(cm.exception))      # the guidance names the right family
        bi.assert_arch_matches(lambda m: None, 'id8-bdc3')  # a matching id8 variant must not raise

    def test_unreadable_level_is_a_note_not_a_block(self):
        real = bi.detect_head_unit
        bi.detect_head_unit = lambda log, ip=None, gateway='auto': (None, None)
        self.addCleanup(setattr, bi, 'detect_head_unit', real)
        msgs = []
        bi.assert_arch_matches(msgs.append, 'id7')         # must not raise when the level can't be read
        self.assertTrue(any('could not read' in m for m in msgs))

    def test_btld_level_regex_parses_a_status_line(self):
        line = ('SVK: HWEL 00007EC7_001.002.001, BTLD 00003F36_036.010.043, SWFL 00005CC5_036.010.004, '
                'SWFL 00005686_036.010.004')
        m = bi._BTLD_LEVEL.search(line)
        self.assertIsNotNone(m)
        self.assertEqual(m.groups(), ('036', '010', '043'))
        self.assertEqual(bi.software_family(int(m.group(1))), 'id8')


if __name__ == '__main__':
    unittest.main(verbosity=2)
