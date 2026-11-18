#!/usr/bin/env python3
"""BeeM-Flash wireless bridge deploy (do_beem), against a FAKE ssh - no car, no network.

    python3 installer/tests/test_bridge.py

A stub `ssh` runs each command in a sandbox: it answers the root/MGU connection check, writes the files
the installer streams with `cat >`, and records every command. The tests prove do_beem copies the right
bridge files to the unit and runs the right script - the read-only probes never run a destructive command.
"""
import os
import re
import stat
import subprocess
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
            echo "   -> anything BLOCKED: install with  --open-iface wlan_bridge,wlan1"
            echo "NOTE: this head unit has no hostapd/iw, so it cannot host its own Wi-Fi network:"
        fi
        if [ -n "${NEVER_REACHED:-}" ]; then
            echo "Phase A (45 s): the firewall as it is now."
            echo "Phase B (45 s): a TEMPORARY accept rule for port 47001 is in place."
            echo "(temporary rule removed)"
        fi
        if [ -n "${REACHED:-}" ]; then
            echo "Phase A (45 s): the firewall as it is now."
            echo "  >>> REACHED via wlan1 from 172.20.10.1"
            echo "Phase B (45 s): a TEMPORARY accept rule for port 47001 is in place."
        fi
        if [ -n "${FW_DROPS:-}" ]; then
            echo "!! The car's firewall DROPS the bridge ports on Wi-Fi - MHD / ENET tools cannot connect."
            echo "!! Re-run the install with:   sh install.sh --open-iface wifi"
        fi
        if [ -n "${NO_6801:-}" ]; then
            echo "  tcp 6801  NOT listening - HSFZ/ENET (MHD, E-Sys, ISTA)"
            echo "  tcp 13400  listening   - DoIP"
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
        self.assertIn('NOT start again after a head-unit restart', msg)
        self.assertIn('Bridge log', msg)

    def test_a_probe_without_hostapd_says_the_unit_cannot_host_an_ap(self):
        os.environ['NO_AP'] = '1'
        self.addCleanup(os.environ.pop, 'NO_AP', None)
        _, _, lines = self.run_beem('probe')
        text = '\n'.join(lines)
        self.assertIn('cannot host its OWN Wi-Fi network', text)
        self.assertIn('open car-Wi-Fi iface', text)

    def test_probe_reports_the_interfaces_to_open(self):
        os.environ['NO_AP'] = '1'
        self.addCleanup(os.environ.pop, 'NO_AP', None)
        _, _, _ = self.run_beem('probe')
        self.assertEqual(bi.beem_wifi_ifaces('-> install with  --open-iface wlan_bridge,wlan1'),
                         'wlan_bridge,wlan1')
        self.assertEqual(bi.beem_wifi_ifaces('no recommendation here'), '')

    def test_car_probe_flags_the_app_not_listening_on_6801(self):
        os.environ['NO_6801'] = '1'
        self.addCleanup(os.environ.pop, 'NO_6801', None)
        _, _, lines = self.run_beem('car-probe')
        text = '\n'.join(lines)
        self.assertIn('not listening on TCP 6801', text)
        self.assertIn('Apps > Store', text)

    def test_install_without_open_iface_on_a_no_ap_unit_warns(self):
        os.environ['NO_AP'] = '1'
        self.addCleanup(os.environ.pop, 'NO_AP', None)
        _, _, lines = self.run_beem('install')            # no open_iface passed
        text = '\n'.join(lines)
        self.assertIn('not doing anything here yet', text)
        # and it does NOT warn when the field was filled in
        lines2 = self.run_beem('install', open_iface='wlan_bridge')[2]
        self.assertNotIn('not doing anything here yet', '\n'.join(lines2))

    def test_car_probe_calls_it_when_the_phone_never_reached(self):
        os.environ['NEVER_REACHED'] = '1'
        self.addCleanup(os.environ.pop, 'NEVER_REACHED', None)
        _, _, lines = self.run_beem('car-probe')
        text = '\n'.join(lines)
        self.assertIn('never reached the head unit', text)
        self.assertIn('firewall is NOT what is stopping it', text)
        self.assertIn('isolation turned OFF', text)

    def test_car_probe_stays_quiet_when_the_phone_did_reach(self):
        os.environ['REACHED'] = '1'
        self.addCleanup(os.environ.pop, 'REACHED', None)
        _, _, lines = self.run_beem('car-probe')
        self.assertNotIn('never reached the head unit', '\n'.join(lines))

    def test_install_surfaces_the_firewall_drop_check(self):
        os.environ['FW_DROPS'] = '1'
        self.addCleanup(os.environ.pop, 'FW_DROPS', None)
        _, _, lines = self.run_beem('install', open_iface='wlan_bridge')
        text = '\n'.join(lines)
        self.assertIn('DROPPING the bridge ports', text)
        self.assertIn('"wifi"', text)

    def test_wifi_keyword_is_accepted_as_an_open_iface(self):
        self.assertEqual(bi._beem_install_args(open_iface='wifi'), ' --open-iface wifi')

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


class InstallScript(unittest.TestCase):
    """The on-car install.sh must never assume setsid. On a real MGU with a read-only /etc it fell back to a
    detached start, busybox had no setsid, and the daemon never ran at all while a pid was still recorded.
    Fixed upstream in BeeM-Flash v2.29.0: systemd unit -> systemd-run transient -> nohup/background."""

    INSTALL = os.path.join(bi.BEEM_DIR, 'beem-bridge', 'install.sh')

    def test_install_script_is_valid_posix_sh(self):
        r = subprocess.run(['/bin/sh', '-n', self.INSTALL], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 0, r.stdout.decode())

    def test_setsid_is_never_assumed(self):
        with open(self.INSTALL) as f:
            src = f.read()
        # the original bug: a bare `setsid ...` line on a busybox unit that has none
        self.assertIsNone(re.search(r'^\s*setsid\s', src, re.M), 'setsid must never be called unguarded')
        self.assertIn('command -v nohup', src, 'needs a nohup fallback for units without setsid')
        self.assertIn('systemd-run', src, 'needs the transient-unit tier for a read-only /etc')

    def test_detach_still_starts_the_daemon_without_setsid_or_nohup(self):
        # run the SHIPPED start_detached() with an empty PATH, so neither setsid nor nohup exists
        with open(self.INSTALL) as f:
            src = f.read()
        m = re.search(r'^if \[ -z "\$STARTED" \]; then\n.*?^fi$', src, re.S | re.M)
        self.assertIsNotNone(m, 'the background-start block was not found in install.sh')
        d = tempfile.mkdtemp(prefix='detach-')
        self.addCleanup(shutil.rmtree, d, True)
        fake_node = os.path.join(d, 'node')
        with open(fake_node, 'w') as f:
            f.write('#!/bin/sh\nsleep 30\n')
        os.chmod(fake_node, 0o755)
        script = 'NODE=%s\nDIR=%s\nARGS=""\nSTARTED=""\n%s\n' % (fake_node, d, m.group(0))
        r = subprocess.run(['/bin/sh', '-c', script], env={'PATH': ''},
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        pidfile = os.path.join(d, 'bridge.pid')
        self.assertTrue(os.path.isfile(pidfile), 'no pid was recorded')
        with open(pidfile) as f:
            pid = int(f.read().strip())
        self.addCleanup(lambda: os.kill(pid, 9))
        os.kill(pid, 0)          # raises ProcessLookupError if the daemon never started


class ConnectionGate(unittest.TestCase):
    """test_connection must accept any MGU MainApp uid, not only oap_12201 (a base unit can differ)."""

    class FakeCar:
        def __init__(self, resp):
            self.ip = '169.254.14.173'
            self.resp = resp
            self.cmd = None

        def run(self, cmd, **k):
            self.cmd = cmd
            return 0, self.resp

    def test_oap_check_is_version_agnostic(self):
        car = self.FakeCar('CONNECTED\n0\nMGU_OAP_OK\n')
        bi.test_connection(car, lambda m: None)             # must not raise
        self.assertIn('oap_[0-9]*', car.cmd, 'the platform check must match any oap_<uid>, not a fixed one')
        self.assertNotIn('oap_12201', car.cmd, 'must not hard-code the 12201 MainApp uid')

    def test_missing_platform_is_rejected(self):
        car = self.FakeCar('CONNECTED\n0\n')               # no MGU_OAP_OK line
        with self.assertRaises(bi.InstallerError):
            bi.test_connection(car, lambda m: None)

    def test_non_root_is_rejected(self):
        car = self.FakeCar('CONNECTED\n1000\nMGU_OAP_OK\n')
        with self.assertRaises(bi.InstallerError):
            bi.test_connection(car, lambda m: None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
