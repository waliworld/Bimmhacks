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
# SVK records are (type, id, trip_a, trip_b, trip_c). types: 1 HWEL, 5 CAFD, 6 BTLD, 8 SWFL.
# A VERIFIED iDrive 7 car: supported bootloader AND the known-good MGU hardware (HWEL 00009537).
VERIFIED_SVK = [(1, 0x00009537, 1, 3, 2), (6, 0x00003F36, 30, 16, 6),
                (5, 0x00003E52, 8, 10, 124), (8, 0x000056E5, 30, 16, 2)]
# An early / base MGU (a 2020 F90 M5 in the field): SAME bootloader id, but different hardware
# (HWEL 00005F34) and an older software level - this is the real "base" case.
BASE_SVK = [(1, 0x00005F34, 1, 1, 4), (6, 0x00003F36, 16, 18, 10),
            (5, 0x00003E52, 6, 36, 28), (8, 0x000056E5, 16, 18, 4)]
# A unit whose bootloader id itself is different - outside the catalog entirely.
UNSUPPORTED_SVK = [(1, 0x00009537, 1, 3, 2), (6, 0x00009999, 30, 16, 6),
                   (5, 0x00003E52, 8, 10, 124), (8, 0x000056E5, 30, 16, 2)]


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

    def test_verified_hardware_is_recognised(self):
        loopback_discovery()
        with FakeCar(vin=VIN, svk=VERIFIED_SVK):
            info = cb.read_identity(gateway='127.0.0.1', out=self.out, log=quiet)
        self.assertEqual(info['btld'], '00003F36')
        self.assertEqual(info['hwel'], '00009537')
        self.assertTrue(info['is_supported_mgu'])
        self.assertTrue(info['hwel_matches_verified'])
        self.assertFalse(info['is_nbt'])

    def test_base_unit_shares_the_bootloader_but_differs_in_hardware(self):
        loopback_discovery()
        with FakeCar(vin=VIN, svk=BASE_SVK):
            info = cb.read_identity(gateway='127.0.0.1', out=self.out, log=quiet)
        # the key correction: the base unit's bootloader IS supported ...
        self.assertEqual(info['btld'], '00003F36')
        self.assertTrue(info['is_supported_mgu'])
        # ... but its hardware (HWEL) and software level are the real difference
        self.assertEqual(info['hwel'], '00005F34')
        self.assertFalse(info['hwel_matches_verified'])
        self.assertEqual(info['sw_level'], '016.018.010')
        self.assertTrue(os.path.isfile(info['profile']), 'a base unit must still save a profile')
        import json
        with open(info['profile']) as f:
            saved = json.load(f)
        self.assertEqual(saved['hwel'], '00005F34')

    def test_a_different_bootloader_id_is_unsupported(self):
        loopback_discovery()
        with FakeCar(vin=VIN, svk=UNSUPPORTED_SVK):
            info = cb.read_identity(gateway='127.0.0.1', out=self.out, log=quiet)
        self.assertEqual(info['btld'], '00009999')
        self.assertFalse(info['is_supported_mgu'])

    def test_identity_read_is_read_only(self):
        loopback_discovery()
        with FakeCar(vin=VIN, svk=BASE_SVK) as car:
            cb.read_identity(gateway='127.0.0.1', log=quiet)
            services = {p[0] for _, p in car.requests if p}
        self.assertTrue(services <= {0x22}, 'identity sent more than reads: %s' % sorted(hex(s) for s in services))
        self.assertNotIn(0x2E, services)        # never a write
        self.assertNotIn(0x34, services)        # never a RequestDownload (flash)

    def test_installer_verdicts(self):
        # do_base_identity drives coding_backup.py as a subprocess; the fake car answers on 127.0.0.1
        def verdict(svk):
            lines = []
            with FakeCar(vin=VIN, svk=svk):
                bi.do_base_identity(lines.append, ip=None, gateway='127.0.0.1', out_dir=self.out)
            return '\n'.join(lines)
        self.assertIn('VERIFIED iDrive 7 hardware', verdict(VERIFIED_SVK))
        self.assertIn('early / base MGU', verdict(BASE_SVK))
        self.assertIn('outside the root tool', verdict(UNSUPPORTED_SVK))


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
