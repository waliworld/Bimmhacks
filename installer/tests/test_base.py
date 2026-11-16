#!/usr/bin/env python3
"""iDrive 7 Base: the read-only identity diagnostic and the (gated) force-root path, against the FAKE
car. No real vehicle, no writes.

    python3 installer/tests/test_base.py

What it proves:
  - the identity readout reports the unit's BTLD and whether it is the supported MGU type (00003F36);
  - an early / base MGU (a different BTLD) is reported as unsupported, and a profile is saved;
  - the identity read is READ-ONLY (it only ever sends 0x22 reads);
  - the experimental force-root refuses without a coding-backup confirmation and without a valid VIN,
    i.e. it can never touch the car by accident.
"""
import os
import sys
import tempfile
import shutil
import unittest
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SSHROOT = os.path.join(REPO, 'tools', 'idrive7-8-ssh-root')
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'installer'))
sys.path.insert(0, SSHROOT)

from fake_enet import FakeCar            # noqa: E402
import coding_backup as cb               # noqa: E402
import bimmhacks_installer as bi         # noqa: E402

HAVE_CRYPTO = importlib.util.find_spec('cryptography') is not None
VIN = 'WBA5R1C0XLFJ12345'
# a base / early MGU: same shape, but the bootloader id is not 00003F36
BASE_SVK = [(1, 0x00000001, 1, 2, 3), (6, 0x00009999, 30, 16, 6),
            (5, 0x000000AB, 12, 34, 56), (8, 0x000056E5, 30, 16, 2)]


def quiet(_m):
    pass


def loopback_discovery():
    from mgu7 import discovery
    real = discovery.discover
    discovery.discover = lambda *a, **k: real(*a, broadcasts=['127.0.0.1'], **k)
    return real


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
class Identity(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix='base-test-')
        self.addCleanup(shutil.rmtree, self.out, True)
        from mgu7 import discovery
        self._real = discovery.discover
        self.addCleanup(setattr, discovery, 'discover', self._real)

    def test_supported_unit_is_recognised(self):
        loopback_discovery()
        with FakeCar(vin=VIN):
            info = cb.read_identity(gateway='127.0.0.1', out=self.out, log=quiet)
        self.assertEqual(info['btld'], '00003F36')
        self.assertTrue(info['is_supported_mgu'])
        self.assertFalse(info['is_nbt'])
        self.assertTrue(info['profile'].endswith('.json'))

    def test_base_unit_is_flagged_unsupported_and_profiled(self):
        loopback_discovery()
        with FakeCar(vin=VIN, svk=BASE_SVK):
            info = cb.read_identity(gateway='127.0.0.1', out=self.out, log=quiet)
        self.assertEqual(info['btld'], '00009999')
        self.assertFalse(info['is_supported_mgu'])
        self.assertTrue(os.path.isfile(info['profile']), 'a base unit must still save a profile')
        import json
        with open(info['profile']) as f:
            saved = json.load(f)
        self.assertEqual(saved['btld'], '00009999')
        self.assertIn('BTLD 00009999_030.016.006', saved['svk'])

    def test_identity_read_is_read_only(self):
        loopback_discovery()
        with FakeCar(vin=VIN, svk=BASE_SVK) as car:
            cb.read_identity(gateway='127.0.0.1', log=quiet)
            services = {p[0] for _, p in car.requests if p}
        self.assertTrue(services <= {0x22}, 'identity sent more than reads: %s' % sorted(hex(s) for s in services))
        self.assertNotIn(0x2E, services)        # never a write
        self.assertNotIn(0x34, services)        # never a RequestDownload (flash)

    def test_installer_verdict_exit_codes(self):
        # do_base_identity drives coding_backup.py as a subprocess; the fake car answers on 127.0.0.1
        lines = []
        with FakeCar(vin=VIN, svk=BASE_SVK):
            bi.do_base_identity(lines.append, ip=None, gateway='127.0.0.1', out_dir=self.out)
        self.assertTrue(any('early / base MGU' in l for l in lines), 'base verdict missing: %s' % lines[-3:])
        lines = []
        with FakeCar(vin=VIN):
            bi.do_base_identity(lines.append, ip=None, gateway='127.0.0.1', out_dir=self.out)
        self.assertTrue(any('SUPPORTED MGU' in l for l in lines), 'supported verdict missing: %s' % lines[-3:])


class ForceRootGate(unittest.TestCase):
    """The experimental force-root must never reach the car without the two gates."""

    def test_refuses_without_a_backup_confirmation(self):
        with self.assertRaises(bi.InstallerError):
            bi.do_base_root_force(quiet, vin=VIN, ip='127.0.0.1', gateway='127.0.0.1', backup_confirmed=False)

    def test_refuses_without_a_valid_vin(self):
        with self.assertRaises(bi.InstallerError):
            bi.do_base_root_force(quiet, vin='NOPE', ip='127.0.0.1', gateway='127.0.0.1', backup_confirmed=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
