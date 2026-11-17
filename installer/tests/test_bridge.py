#!/usr/bin/env python3
"""BeeM-Flash wireless bridge deploy (do_beem), against a FAKE ssh - no car, no network.

    python3 installer/tests/test_bridge.py

A stub `ssh` runs each command in a sandbox: it answers the root/MGU connection check, writes the files
the installer streams with `cat >`, and records every command. The tests prove do_beem copies the right
bridge files to the unit and runs the right script - the read-only probes never run a destructive command.
"""
import os
import stat
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'installer'))
import bimmhacks_installer as bi  # noqa: E402

STUB = '''#!/bin/sh
for a; do cmd="$a"; done
cmd=${cmd#export LC_ALL=C; }
printf '%s\\n' "$cmd" >> "$SSH_LOG"
case "$cmd" in
    "echo CONNECTED"*) printf 'CONNECTED\\n0\\nMGU_OAP_OK\\n' ;;
    "mkdir -p "*) d=${cmd#mkdir -p }; mkdir -p "$SANDBOX$d" ;;
    "cat > "*) f=${cmd#cat > }; mkdir -p "$(dirname "$SANDBOX$f")"; cat > "$SANDBOX$f" ;;
    *)
        if [ -n "${FAIL_INSTALL:-}" ]; then
            echo "install.sh: line 89: can't create /etc/systemd/system/beem-bridged.service: Read-only file system"
            echo "warning: cannot write /etc/systemd/system/beem-bridged.service (read-only /etc?) - starting detached instead"
            echo " beem-bridged was installed but is NOT answering yet."
            exit 1
        fi
        if [ -n "${NO_AP:-}" ]; then
            echo "tool hostapd: MISSING"
            echo "tool iw: MISSING"
        fi
        printf 'RAN %s\\n' "$cmd" ;;
esac
exit 0
'''


class BridgeDeploy(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='bridge-test-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.sandbox = os.path.join(self.dir, 'unit')
        self.log_file = os.path.join(self.dir, 'ssh.log')
        self.stub = os.path.join(self.dir, 'ssh')
        with open(self.stub, 'w') as f:
            f.write(STUB)
        os.chmod(self.stub, 0o755)
        self.key = os.path.join(self.dir, 'id_rsa')
        with open(self.key, 'w') as f:
            f.write('-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n')
        os.chmod(self.key, stat.S_IRUSR | stat.S_IWUSR)
        os.environ['SSH_LOG'] = self.log_file
        os.environ['SANDBOX'] = self.sandbox

    def run_beem(self, action, **opts):
        lines = []
        code, out = bi.do_beem(action, self.stub, '127.0.0.1', self.key, log=lines.append, **opts)
        cmds = []
        if os.path.isfile(self.log_file):
            with open(self.log_file) as f:
                cmds = f.read().splitlines()
        return code, cmds, lines

    def remote_path(self, subdir, name):
        return self.sandbox + bi.BEEM_REMOTE + '/' + subdir + '/' + name

    def assert_copied(self, subdir, name):
        p = self.remote_path(subdir, name)
        self.assertTrue(os.path.isfile(p), 'did not copy %s to the unit' % name)
        with open(p, 'rb') as a, open(os.path.join(bi.BEEM_DIR, subdir, name), 'rb') as b:
            self.assertEqual(a.read(), b.read(), '%s copied wrong' % name)

    def test_probe_is_read_only_and_copies_the_service_files(self):
        code, cmds, _ = self.run_beem('probe')
        self.assertEqual(code, 0)
        for f in ('beem-bridged.js', 'beem-bridged.service', 'install.sh'):
            self.assert_copied('beem-bridge', f)
        self.assertTrue(any(c.endswith('sh install.sh --probe') for c in cmds), 'did not run the --probe')
        # a probe must not install anything: no bare install.sh, no systemctl, no uninstall
        self.assertFalse(any(c.endswith('sh install.sh') for c in cmds), 'probe must not install')
        self.assertFalse(any('uninstall' in c for c in cmds))

    def test_install_runs_install_sh(self):
        code, cmds, _ = self.run_beem('install')
        self.assertEqual(code, 0)
        self.assert_copied('beem-bridge', 'install.sh')
        self.assertTrue(any(c.endswith('&& sh install.sh') for c in cmds), 'did not run install.sh')
        self.assertFalse(any('--probe' in c for c in cmds))

    def test_install_passes_the_app_uid_and_switches(self):
        code, cmds, _ = self.run_beem('install', app_uid='777', take_iface=True, channel='36')
        self.assertEqual(code, 0)
        self.assertTrue(any(c.endswith('sh install.sh --app-uid 777 --channel 36 --take-iface') for c in cmds),
                        'install did not carry the app uid / switches: %s' % cmds[-1:])

    def test_install_refuses_a_non_numeric_app_uid(self):
        with self.assertRaises(bi.InstallerError):
            self.run_beem('install', app_uid='12261; reboot')

    def test_status_queries_the_helper_read_only(self):
        code, cmds, _ = self.run_beem('status')
        self.assertEqual(code, 0)
        self.assert_copied('beem-bridge', 'install.sh')
        self.assertTrue(any(c.endswith('sh install.sh --status') for c in cmds), 'did not run --status')
        # status is read-only: it never pushes the daemon or runs a bare install
        self.assertFalse(any(c.endswith('beem-bridged.js') for c in cmds if c.startswith('cat >')))
        self.assertFalse(any(c.endswith('&& sh install.sh') for c in cmds))

    def test_log_is_read_only_and_copies_nothing(self):
        code, cmds, _ = self.run_beem('log')
        self.assertEqual(code, 0)
        self.assertFalse(any(c.startswith('cat >') for c in cmds), 'the log action must copy no files')
        last = cmds[-1]
        for expect in ('bridge.log', 'beem-bridged processes', 'control sockets', 'is /run writable'):
            self.assertIn(expect, last, 'the log diagnostic is missing %r' % expect)
        self.assertNotIn('install.sh', last)
        self.assertNotIn('uninstall.sh', last)

    def test_a_failed_install_explains_readonly_etc_and_points_at_the_log(self):
        # replays the real failure seen on a car: read-only /etc, helper not answering
        os.environ['FAIL_INSTALL'] = '1'
        self.addCleanup(os.environ.pop, 'FAIL_INSTALL', None)
        with self.assertRaises(bi.InstallerError) as e:
            self.run_beem('install')
        msg = str(e.exception)
        self.assertIn('/etc is read-only', msg)
        self.assertIn('NOT come back after a head-unit restart', msg)
        self.assertIn('Bridge log', msg)

    def test_a_probe_without_hostapd_says_the_unit_cannot_host_an_ap(self):
        os.environ['NO_AP'] = '1'
        self.addCleanup(os.environ.pop, 'NO_AP', None)
        _, _, lines = self.run_beem('probe')
        text = '\n'.join(lines)
        self.assertIn('cannot host its OWN Wi-Fi network', text)
        self.assertIn('open car-Wi-Fi iface', text)

    def test_uninstall_only_sends_uninstall(self):
        code, cmds, _ = self.run_beem('uninstall')
        self.assertEqual(code, 0)
        self.assert_copied('beem-bridge', 'uninstall.sh')
        self.assertTrue(any(c.endswith('sh uninstall.sh') for c in cmds))
        # it must not push the daemon or the service unit
        self.assertFalse(any(c.endswith('beem-bridged.js') for c in cmds if c.startswith('cat >')))

    def test_car_probe_runs_the_read_only_probe(self):
        code, cmds, _ = self.run_beem('car-probe')
        self.assertEqual(code, 0)
        self.assert_copied('bridge-probes', 'probe-car.sh')
        self.assertTrue(any(c.endswith('sh probe-car.sh') for c in cmds))

    def test_a_connection_check_runs_before_any_deploy(self):
        _, cmds, _ = self.run_beem('probe')
        self.assertTrue(cmds and cmds[0].startswith('echo CONNECTED'), 'must verify root MGU first')

    def test_missing_key_is_refused(self):
        with self.assertRaises(bi.InstallerError):
            bi.do_beem('probe', self.stub, '127.0.0.1', os.path.join(self.dir, 'nope'), log=lambda m: None)


class BridgeAccessCode(unittest.TestCase):
    """The Wireless Bridge tab is gated by an access code (compared by SHA-256, never stored plain)."""

    def tearDown(self):
        os.environ.pop('BIMMHACKS_BRIDGE_CODE_HASH', None)

    def test_only_the_right_code_unlocks(self):
        import hashlib
        os.environ['BIMMHACKS_BRIDGE_CODE_HASH'] = hashlib.sha256(b'secret-123').hexdigest()
        self.assertTrue(bi.bridge_code_ok('secret-123'))
        self.assertTrue(bi.bridge_code_ok('  secret-123  '))     # trimmed
        self.assertFalse(bi.bridge_code_ok('secret-124'))
        self.assertFalse(bi.bridge_code_ok(''))
        self.assertFalse(bi.bridge_code_ok(None))

    def test_the_source_stores_only_a_hash_not_the_code(self):
        src = open(os.path.join(REPO, 'installer', 'bimmhacks_installer.py'), encoding='utf-8').read()
        import re
        m = re.search(r"^BRIDGE_CODE_HASH = '([0-9a-f]{64})'", src, re.M)
        self.assertIsNotNone(m, 'the hash constant must exist')
        # whatever the default code is, the plaintext must not sit next to the hash
        self.assertNotIn('BRIDGE_CODE_PLAIN', src)


if __name__ == '__main__':
    unittest.main(verbosity=2)
