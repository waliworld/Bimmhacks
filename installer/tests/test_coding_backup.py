#!/usr/bin/env python3
"""Tests for the coding snapshot, VIN detection and root-tool status against a FAKE car
(installer/tests/fake_enet.py). No car, cable or network needed:

    python3 installer/tests/test_coding_backup.py

What it proves:
  - the coding snapshot reads the right things (CAFD versions, VIN, IP, faults), writes both files,
    and is STRICTLY READ-ONLY on the wire: only services 0x22 and 0x19 are ever sent
  - the real UDP discovery reply is parsed (VIN comes from the car, not from the command line)
  - `ssh_enable.py status` runs against the fake and sends nothing but reads
  - the installer's detect_vin() returns the VIN and does not pick up stray 17-character tokens
  - an unreachable car fails cleanly (non-zero exit, no snapshot written)
Needs the `cryptography` package (the root tool imports it).
"""
import json
import os
import subprocess
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'installer'))
SSHROOT = os.path.join(REPO, 'tools', 'idrive7-8-ssh-root')
sys.path.insert(0, SSHROOT)

from fake_enet import FakeCar            # noqa: E402
import coding_backup                      # noqa: E402
import bimmhacks_installer as bi          # noqa: E402

import importlib.util
HAVE_CRYPTO = importlib.util.find_spec('cryptography') is not None

VIN = 'WBA12345678901234'


def quiet(_m):
    pass


def broadcast_loops_back():
    """Does this machine receive its own UDP broadcast? (Real discovery relies on it; some sandboxes and CI
    runners do not.) Probed once, so the one test that needs it can skip instead of failing for the machine."""
    import socket
    rx = tx = None
    try:
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rx.bind(('0.0.0.0', 46812))
        rx.settimeout(0.5)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        tx.sendto(b'x', ('255.255.255.255', 46812))
        rx.recvfrom(8)
        return True
    except OSError:
        return False
    finally:
        for sock in (rx, tx):
            if sock:
                sock.close()


BROADCAST = broadcast_loops_back()


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
class CodingSnapshot(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix='coding-test-')
        self.addCleanup(shutil.rmtree, self.out, True)

    def test_snapshot_contents_and_files(self):
        with FakeCar(vin=VIN):
            snap = coding_backup.collect(gateway='127.0.0.1', vin=VIN, log=quiet)
            path = coding_backup.write(snap, self.out, log=quiet)
        self.assertEqual(snap['vin'], VIN)
        self.assertEqual([c['version'] for c in snap['cafd']], ['000000AB', '000000AC'])
        self.assertEqual(snap['cafd'][0]['trip'], '012.034.056')
        self.assertTrue(snap['is_supported_mgu'])
        self.assertEqual(snap['mgu_ip'][0], '169.254.36.47')
        self.assertIn('480A01', snap['dtcs'])
        self.assertIn('B7F8E5', snap['dtcs'])
        # the fake car hands over NCD, so the backup captured real coding and wrote .ncd files
        self.assertTrue(snap['ncd_captured'])
        self.assertEqual([b['cafd'] for b in snap['ncd_blocks']], ['000000AB', '000000AC'])
        self.assertTrue(all(b['ncd_len'] > 0 for b in snap['ncd_blocks']))
        # both files exist, the JSON round-trips, the text names every CAFD, and the .ncd folder has them
        base = path[:-4]
        with open(base + '.json') as f:
            data = json.load(f)
        self.assertEqual(data['vin'], VIN)
        self.assertEqual(sorted(data['ncd_files']), sorted(os.listdir(base + '.ncd')))
        self.assertTrue(all(n.endswith('.ncd') for n in data['ncd_files']))
        with open(base + '.txt') as f:
            text = f.read()
        self.assertIn('CAFD_000000AB', text)
        self.assertIn('CAFD_000000AC', text)
        self.assertIn('DID capture the coding data', text)

    def test_preflight_read_only_coding_backup_is_taken(self):
        # the read-only preflight backup is a SEPARATE, independent operation from the NCD backup: the
        # root tool's coding-store scan (0x3000-0x3FFF + identification, read twice and verified) + RESTORE.txt
        with FakeCar(vin=VIN):
            pf = coding_backup.preflight_backup(gateway='127.0.0.1', vin=VIN, out=self.out, log=quiet)
        self.assertTrue(pf['verified'], 'the coding read twice and matched')
        self.assertEqual(len(pf['coding']), len(FakeCar.CODING_STORE))
        folder = pf['folder']
        for name in ('RESTORE.txt', 'coding_MGU.txt', 'backup.json', 'SHA256SUMS'):
            self.assertTrue(os.path.isfile(os.path.join(folder, name)), name + ' written')
        with open(os.path.join(folder, 'RESTORE.txt')) as f:
            restore = f.read()
        self.assertIn('vehicle order', restore)          # the RESTORE note steers to VO / code-by-FA
        with open(os.path.join(folder, 'coding_MGU.txt')) as f:
            self.assertIn('3000: 112233', f.read())       # a coding block, byte for byte

    def test_snapshot_is_read_only_on_the_wire(self):
        with FakeCar(vin=VIN) as car:
            coding_backup.collect(gateway='127.0.0.1', vin=VIN, log=quiet)
            services = car.services()
            requests = list(car.requests)
        self.assertTrue(requests, 'the snapshot asked the car something')
        self.assertTrue(services <= {0x22, 0x19}, 'only ReadDataByIdentifier / ReadDTCInformation, saw %s'
                        % sorted(hex(s) for s in services))
        for _dst, payload in requests:      # and nothing that writes, clears, resets or reprograms
            self.assertNotIn(payload[0], (0x2E, 0x2F, 0x31, 0x14, 0x10, 0x11, 0x27, 0x34, 0x36, 0x37, 0x3D, 0x85))

    def test_vin_comes_from_discovery_not_the_command_line(self):
        from mgu7 import discovery
        real = discovery.discover
        with FakeCar(vin=VIN) as car:
            # probe the fake over loopback (the real code probes every interface's broadcast address)
            discovery.discover = lambda *a, **k: real(*a, broadcasts=['127.0.0.1'], **k)
            try:
                snap = coding_backup.collect(gateway='auto', log=quiet)
            finally:
                discovery.discover = real
            self.assertGreaterEqual(car.probes, 1)
        self.assertEqual(snap['vin'], VIN)
        self.assertEqual(snap['gateway'], '127.0.0.1')

    def test_unreachable_car_fails_cleanly(self):
        # nothing listens on this port
        r = subprocess.run([sys.executable, os.path.join(REPO, 'installer', 'coding_backup.py'), '--out', self.out,
                            '--gateway', '127.0.0.1'], cwd=SSHROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn('ERROR', r.stderr)
        self.assertEqual(os.listdir(self.out), [], 'no snapshot written for a car that never answered')
        self.assertNotIn('Traceback', r.stderr)


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
class RootToolAgainstFakeCar(unittest.TestCase):
    def test_status_runs_and_only_reads(self):
        with FakeCar(vin=VIN) as car:
            r = subprocess.run([sys.executable, os.path.join(SSHROOT, 'ssh_enable.py'), 'status', '--gateway',
                                '127.0.0.1', '--mgu-ip', '127.0.0.1', '--no-log'], cwd=SSHROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=90)
            services = car.services()
        self.assertEqual(r.returncode, 0, r.stdout[-600:])
        self.assertIn('B7F8E5', r.stdout)                                  # it reported the debug-state DTC
        self.assertTrue(services <= {0x22, 0x19, 0x31}, 'status sent %s' % sorted(hex(s) for s in services))
        self.assertNotIn(0x2E, services)                                   # status never writes

    def test_exit_prog_recovery_sends_only_routines_and_reset(self):
        # the new recovery command (for a unit stuck on the blue programming screen): it sends the
        # end-of-programming routines + ECU reset, never a flash write
        with FakeCar(vin=VIN) as car:
            r = subprocess.run([sys.executable, os.path.join(SSHROOT, 'ssh_enable.py'), 'exit-prog', '--gateway',
                                '127.0.0.1', '--mgu-ip', '127.0.0.1', '--no-log'], cwd=SSHROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=90)
            services = car.services()
        self.assertEqual(r.returncode, 0, r.stdout[-600:])
        self.assertTrue(services <= {0x22, 0x31, 0x11}, 'exit-prog sent %s' % sorted(hex(s) for s in services))
        self.assertNotIn(0x2E, services)                                   # never writes coding
        self.assertNotIn(0x34, services)                                   # never a RequestDownload (flash)

    @unittest.skipUnless(BROADCAST, 'this machine does not receive its own UDP broadcast')
    def test_detect_vin_reads_the_vin_the_car_reports(self):
        # detect_vin() runs `status` with automatic gateway discovery: drive it against the fake
        # through real broadcast discovery (the sandbox / CI loopback receives its own broadcasts)
        lines = []
        with FakeCar(vin=VIN, host='0.0.0.0'):
            vin = bi.detect_vin(lines.append, ip='127.0.0.1')
        self.assertEqual(vin, VIN, '\n'.join(lines[-12:]))


class VinParsing(unittest.TestCase):
    """detect_vin() must take the VIN from the line that reports it, not from any 17-character token."""

    def run_with(self, output_lines, **kw):
        real = bi.run_ssh_root
        bi.run_ssh_root = lambda action, log, **k: ([log(l) for l in output_lines], 0)[1]
        bi.ensure_cryptography = lambda log, install=True: None
        try:
            return bi.detect_vin(quiet, **kw)
        finally:
            bi.run_ssh_root = real

    def test_picks_the_vin_line(self):
        self.assertEqual(self.run_with(['Found 1 ENET gateway. | VIN: ' + VIN]), VIN)

    def test_ignores_other_long_tokens(self):
        noise = ['SVK: BTLD 00003F36_030.016.006', 'Full session log: ABCDEFGH123456789.log',
                 'cert 0123456789ABCDEFG', 'Found 1 ENET gateway. | VIN: ' + VIN]
        self.assertEqual(self.run_with(noise), VIN)

    def test_no_vin_line_means_none(self):
        noise = ['Full session log: ABCDEFGH123456789.log', 'token A1B2C3D4E5F6G7H8J']
        self.assertIsNone(self.run_with(noise))

    def test_not_reported_means_none(self):
        self.assertIsNone(self.run_with(['Found 1 ENET gateway. | VIN: not reported']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
