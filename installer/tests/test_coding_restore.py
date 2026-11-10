#!/usr/bin/env python3
"""The experimental coding backup / verify / restore engine, against the FAKE car (fake_enet.py), which
models the per-CAFD NCD store:

    python3 installer/tests/test_coding_restore.py

What it proves (no car, no network):
  - backup reads the NCD the unit hands over and writes E-Sys-compatible .ncd files + a manifest
  - verify is read-only and spots a match, a difference and an unreadable block
  - a dry run writes NOTHING to the car
  - restore refuses without the confirmation; with it, it writes every block back and read-back verifies
  - restore is SAFE-BY-CONSTRUCTION: it only ever sends 0x22 / 0x2E / 0x10, only writes to the NCD
    identifiers it read, and a no-NCD backup writes nothing at all
  - the gates hold: a car whose CAFD versions or VIN differ is refused; a unit that refuses the write
    (securityAccessDenied) is reported as FAILED, never as a silent success
Needs the `cryptography` package (the root tool imports it).
"""
import os
import subprocess
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SSHROOT = os.path.join(REPO, 'tools', 'idrive7-8-ssh-root')
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'installer'))
sys.path.insert(0, SSHROOT)

from fake_enet import FakeCar            # noqa: E402
import coding_backup as cb               # noqa: E402

import importlib.util
HAVE_CRYPTO = importlib.util.find_spec('cryptography') is not None
VIN = 'WBA12345678901234'


def quiet(_m):
    pass


def loopback_discovery():
    from mgu7 import discovery
    real = discovery.discover
    discovery.discover = lambda *a, **k: real(*a, broadcasts=['127.0.0.1'], **k)
    return real


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
class CodingEngine(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix='coding-restore-')
        self.addCleanup(shutil.rmtree, self.out, True)
        from mgu7 import discovery
        self._real_discover = discovery.discover
        self.addCleanup(setattr, discovery, 'discover', self._real_discover)

    def backup(self, car):
        loopback_discovery()
        snap = cb.collect(gateway='127.0.0.1', vin=VIN, log=quiet)
        path = cb.write(snap, self.out, log=quiet)
        return cb.load_backup(path[:-4] + '.json')

    def test_backup_captures_ncd_and_writes_ncd_files(self):
        with FakeCar(vin=VIN):
            bk = self.backup(None)
        self.assertTrue(bk['ncd_captured'])
        self.assertEqual([b['cafd'] for b in bk['ncd_blocks']], ['000000AB', '000000AC'])
        folder = [n for n in os.listdir(self.out) if n.endswith('.ncd')][0]
        files = os.listdir(os.path.join(self.out, folder))
        self.assertEqual(len(files), 2)
        self.assertTrue(all(f.startswith('CAFD_') and f.endswith('.ncd') for f in files))

    def test_verify_spots_match_difference_and_unreadable(self):
        with FakeCar(vin=VIN) as car:
            bk = self.backup(None)
            r = cb.verify(bk, gateway='127.0.0.1', log=quiet)
            self.assertEqual((r['same'], r['different'], r['unreadable']), (['000000AB', '000000AC'], [], []))
            car.ncd[0] = b'\x99\x99\x99\x99'          # coding changed on the car
            del car.ncd[1]                             # and one block now unreadable
            r = cb.verify(bk, gateway='127.0.0.1', log=quiet)
            self.assertEqual(r['different'], ['000000AB'])
            self.assertEqual(r['unreadable'], ['000000AC'])

    def test_dry_run_writes_nothing(self):
        with FakeCar(vin=VIN) as car:
            bk = self.backup(None)
            before = dict(car.ncd)
            n_before = len(car.requests)
            r = cb.restore(bk, gateway='127.0.0.1', log=quiet, dry_run=True)
            self.assertEqual(r['written'], ['000000AB', '000000AC'])
            self.assertEqual(r['verified'], [])
            self.assertEqual(car.ncd, before)
            self.assertNotIn(0x2E, {p[0] for _, p in car.requests[n_before:]}, 'a dry run sent no write')

    def test_restore_refuses_without_confirmation(self):
        with FakeCar(vin=VIN) as car:
            bk = self.backup(None)
            car.ncd[0] = b'\xDE\xAD'
            n = len(car.requests)
            with self.assertRaises(cb.CodingError):
                cb.restore(bk, gateway='127.0.0.1', log=quiet)
            self.assertNotIn(0x2E, {p[0] for _, p in car.requests[n:]}, 'a refused restore wrote nothing')

    def test_restore_round_trips_and_verifies(self):
        with FakeCar(vin=VIN) as car:
            bk = self.backup(None)
            original = dict(car.ncd)
            car.ncd[0] = b'\x00\x00\x00\x00'           # a flash wiped the coding
            car.ncd[1] = b'\x00\x00\x00\x00'
            r = cb.restore(bk, gateway='127.0.0.1', log=quiet, confirm=True)
            self.assertEqual(r['verified'], ['000000AB', '000000AC'])
            self.assertEqual(r['failed'], [])
            self.assertEqual(car.ncd, original, 'the coding is back exactly as it was')

    def test_restore_is_safe_on_the_wire(self):
        with FakeCar(vin=VIN) as car:
            bk = self.backup(None)
            n = len(car.requests)
            cb.restore(bk, gateway='127.0.0.1', log=quiet, confirm=True)
            services = {p[0] for _, p in car.requests[n:]}
            self.assertTrue(services <= {0x22, 0x2E, 0x10}, 'restore sends only reads, coding writes and session: %s'
                            % sorted(hex(s) for s in services))
            # every write went to an NCD identifier (>= NCD_BASE), never anything else
            for _dst, p in car.requests[n:]:
                if p[0] == 0x2E:
                    self.assertGreaterEqual(p[1] << 8 | p[2], FakeCar.NCD_BASE)

    def test_no_ncd_backup_restores_nothing(self):
        with FakeCar(vin=VIN):
            loopback_discovery()
            snap = cb.collect(gateway='127.0.0.1', vin=VIN, log=quiet, read_ncd=False)
            path = cb.write(snap, self.out, log=quiet)
            bk = cb.load_backup(path[:-4] + '.json')
        self.assertFalse(bk['ncd_captured'])
        with FakeCar(vin=VIN) as car:
            n = len(car.requests)
            with self.assertRaises(cb.CodingError):
                cb.restore(bk, gateway='127.0.0.1', log=quiet, confirm=True)
            self.assertEqual(car.requests[n:], [], 'nothing captured -> the car is never touched')

    def test_a_unit_that_was_not_handing_over_ncd_still_backs_up(self):
        # a real unit that does not expose NCD the default way: backup still succeeds as the snapshot
        with FakeCar(vin=VIN, ncd={}):
            loopback_discovery()
            snap = cb.collect(gateway='127.0.0.1', vin=VIN, log=quiet)
        self.assertFalse(snap['ncd_captured'])
        self.assertEqual([c['version'] for c in snap['cafd']], ['000000AB', '000000AC'])

    def test_gate_refuses_a_different_unit(self):
        with FakeCar(vin=VIN):
            bk = self.backup(None)
        # a car with different CAFD versions
        other_svk = [(1, 0x00000001, 1, 2, 3), (6, 0x00003F36, 30, 16, 6), (5, 0x000000FF, 1, 1, 1)]
        with FakeCar(vin=VIN, svk=other_svk) as car:
            n = len(car.requests)
            with self.assertRaises(cb.CodingError) as e:
                cb.restore(bk, gateway='127.0.0.1', log=quiet, confirm=True)
            self.assertIn('do not match', str(e.exception))
            self.assertNotIn(0x2E, {p[0] for _, p in car.requests[n:]}, 'a mismatched unit is never written to')

    def test_gate_refuses_a_different_vin(self):
        with FakeCar(vin=VIN):
            bk = self.backup(None)
        with FakeCar(vin='WBA99999999999999'):
            with self.assertRaises(cb.CodingError) as e:
                cb.restore(bk, gateway='127.0.0.1', log=quiet, confirm=True)
            self.assertIn('refusing', str(e.exception).lower())

    def test_a_refused_write_is_reported_not_hidden(self):
        with FakeCar(vin=VIN, coding_write=False):
            bk = self.backup(None)
            r = cb.restore(bk, gateway='127.0.0.1', log=quiet, confirm=True)
            self.assertEqual(r['verified'], [])
            self.assertEqual(len(r['failed']), 2)
            self.assertTrue(all('NRC 0x33' in f['why'] for f in r['failed']))

    def test_a_write_that_does_not_stick_is_caught_by_read_back(self):
        # the unit says the write succeeded but the value did not change: read-back verify must catch it
        with FakeCar(vin=VIN, coding_write='lie') as car:
            bk = self.backup(None)
            car.ncd[0] = b'\x00\x00\x00\x00'           # a flash wiped it; the "write" will not put it back
            car.ncd[1] = b'\x00\x00\x00\x00'
            r = cb.restore(bk, gateway='127.0.0.1', log=quiet, confirm=True)
            self.assertEqual(r['verified'], [])
            self.assertEqual(len(r['failed']), 2)
            self.assertTrue(all('read-back' in f['why'] for f in r['failed']))


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
class CodingCli(unittest.TestCase):
    """coding_backup.py restore/verify as a subprocess (the installer runs it this way)."""

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix='coding-cli-')
        self.addCleanup(shutil.rmtree, self.out, True)

    def run_cli(self, *args, **kw):
        env = dict(os.environ, PYTHONPATH=SSHROOT)
        return subprocess.run([sys.executable, os.path.join(REPO, 'installer', 'coding_backup.py')] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=90, env=env, **kw)

    def test_backup_then_restore_refused_without_confirm(self):
        from mgu7 import discovery
        real = discovery.discover
        self.addCleanup(setattr, discovery, 'discover', real)
        with FakeCar(vin=VIN):
            r = self.run_cli('backup', '--out', self.out, '--gateway', '127.0.0.1', '--vin', VIN)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            bk = [n for n in os.listdir(self.out) if n.endswith('.json')][0]
            path = os.path.join(self.out, bk)
            # restore without the confirmation: exit 2 (guarded), nothing written
            r = self.run_cli('restore', '--backup', path, '--gateway', '127.0.0.1')
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn('without confirmation', (r.stdout + r.stderr))
            # dry run: exit 0, writes nothing
            r = self.run_cli('restore', '--backup', path, '--gateway', '127.0.0.1', '--dry-run')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('nothing was written', r.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
