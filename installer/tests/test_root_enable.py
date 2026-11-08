#!/usr/bin/env python3
"""The root-access ENABLE path, end to end, against the fake car (fake_enet.py) and a fake ssh
(fake_ssh.sh) - no car, no cable:

    python3 installer/tests/test_root_enable.py

  1. the bundled root tool's own `enable --persist` in-process: it must enroll five times, verify the
     login, make SSH persistent and turn debug mode off - and write the key bundle + connect.txt
  2. the installer's do_root('enable') as the window and the CLI run it (a subprocess), when this
     machine can bind port 22 (the tool probes that port, fixed); otherwise that part is skipped
Both prove the car was only ever sent the enrollment routines and reads - no flash, no writes.
"""
import os
import shutil
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SSHROOT = os.path.join(REPO, 'tools', 'idrive7-8-ssh-root')
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'installer'))
sys.path.insert(0, SSHROOT)

from fake_enet import FakeCar            # noqa: E402

import importlib.util
HAVE_CRYPTO = importlib.util.find_spec('cryptography') is not None

VIN = 'WBA00000000TEST01'
FLASH_SERVICES = {0x27, 0x28, 0x34, 0x36, 0x37, 0x2E, 0x3D, 0x85, 0x11, 0x14}


class FakeSsh(object):
    """fake_ssh.sh on the front of PATH, as `ssh`, for the duration of a test."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix='fake-ssh-')
        dst = os.path.join(self.dir, 'ssh')
        shutil.copyfile(os.path.join(HERE, 'fake_ssh.sh'), dst)
        os.chmod(dst, stat.S_IRWXU)
        self.old = os.environ.get('PATH', '')
        os.environ['PATH'] = self.dir + os.pathsep + self.old
        return self

    def __exit__(self, *a):
        os.environ['PATH'] = self.old
        shutil.rmtree(self.dir, ignore_errors=True)


def port_bindable(port):
    import socket
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
@unittest.skipIf(os.name == 'nt', 'the fake ssh is a shell script')
class EnableInProcess(unittest.TestCase):
    def test_enable_persist_writes_the_key_and_turns_debug_off(self):
        import ssh_enable
        from mgu7 import enroll, identity
        keys = tempfile.mkdtemp(prefix='enroll-')
        self.addCleanup(shutil.rmtree, keys, True)
        real_probe, real_dir = enroll.probe_port, identity.ENROLL_DIR
        with FakeCar(vin=VIN) as car, FakeSsh():
            # the tool probes port 22 of the head unit (a fixed port): answer for the fake car instead
            enroll.probe_port = lambda host, port=22, timeout=3.0: (host == '127.0.0.1', 1.0)
            identity.ENROLL_DIR = keys
            try:
                rc = ssh_enable.main(['enable', '--gateway', '127.0.0.1', '--mgu-ip', '127.0.0.1', '--vin', VIN,
                                      '--persist', '--yes', '--no-log'])
            finally:
                enroll.probe_port, identity.ENROLL_DIR = real_probe, real_dir
            services = car.services()
            enrolled, debug = car.enrolled, car.debug_on
        self.assertEqual(rc, 0)
        self.assertEqual(enrolled, 5, 'five enrollment cycles, as the proven sequence does')
        self.assertFalse(debug, 'persist turned debug mode off at the end (no B7F8E5 DTC)')
        self.assertTrue(services <= {0x22, 0x19, 0x31, 0x10, 0x3E}, 'enable sends reads and routines only: %s' % sorted(hex(s) for s in services))
        self.assertFalse(services & FLASH_SERVICES, 'nothing that flashes or writes')
        d = os.path.join(keys, VIN)
        self.assertTrue(os.path.isdir(d), os.listdir(keys))
        for n in ('id_rsa', 'id_rsa.pkcs1.pem', 'id_rsa.pkcs8.pem', 'id_rsa.ppk', 'connect.txt'):
            self.assertTrue(os.path.isfile(os.path.join(d, n)), n)
        with open(os.path.join(d, 'connect.txt')) as f:
            txt = f.read()
        self.assertIn('Host (IP):  127.0.0.1', txt)
        self.assertIn('VIN:        ' + VIN, txt)
        self.assertIn('ssh -i', txt)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, 'id_rsa')).st_mode) & 0o077, 0, 'the key is private to the user')


@unittest.skipUnless(HAVE_CRYPTO, "needs the 'cryptography' package")
@unittest.skipIf(os.name == 'nt', 'the fake ssh is a shell script')
@unittest.skipUnless(port_bindable(22), 'cannot bind port 22 here (the tool probes it): run as root to cover this')
class EnableThroughTheInstaller(unittest.TestCase):
    """do_root('enable') exactly as the window and `root-enable` run it: a subprocess of the real tool."""

    def setUp(self):
        self.enroll_dir = os.path.join(SSHROOT, 'data', 'enrollments', VIN)
        shutil.rmtree(self.enroll_dir, ignore_errors=True)

    def tearDown(self):
        shutil.rmtree(self.enroll_dir, ignore_errors=True)
        logs = os.path.join(SSHROOT, 'data', 'logs')
        for n in os.listdir(logs) if os.path.isdir(logs) else []:
            if n.startswith('session-'):
                os.remove(os.path.join(logs, n))

    def test_do_root_enable_returns_the_key_and_the_ip(self):
        import bimmhacks_installer as bi
        lines = []
        with FakeCar(vin=VIN, ssh_port=22) as car, FakeSsh():
            key = bi.do_root('enable', lines.append, vin=VIN, ip='127.0.0.1', gateway='127.0.0.1')
            services, connects, debug = car.services(), car.ssh_connects, car.debug_on
        self.assertEqual(key, os.path.join(self.enroll_dir, 'id_rsa'), '\n'.join(lines[-15:]))
        self.assertEqual(bi.ip_from_key(key), '127.0.0.1', 'the window fills the IP field from connect.txt')
        self.assertGreaterEqual(connects, 1, 'the tool probed the head unit\'s port 22')
        self.assertFalse(debug)
        self.assertFalse(services & FLASH_SERVICES)
        self.assertTrue(any('Root SSH is enabled' in l for l in lines))


if __name__ == '__main__':
    unittest.main(verbosity=2)
