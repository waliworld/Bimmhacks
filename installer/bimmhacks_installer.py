#!/usr/bin/env python3
"""Bimmhacks MGU Flash & App Studio (BMW iDrive 7 and iDrive 8 head units).

One tool that takes a client from a stock car to installed apps, in three stages:

  A. ROOT ACCESS - enable root SSH on the head unit over an ENET cable, with the bundled
     idrive7-8-ssh-root tool. One flash per unit; it writes out the car's SSH key for every
     later step. (Skip this if the car is already rooted and you have its key.)
  B. APPS - the MGU custom apps, against the client's own car:
       1. connects to the head unit over SSH (the root key from stage A)
       2. copies BMW's x_call app off THAT car and extracts the oap-sdk from it
          (BMW's SDK is copyrighted and is never shipped with this installer)
       3. builds the chosen apps in the chosen language and packs the car kit
       4. copies the kit to the car, verifies it, runs the read-only check, then installs
       5. deletes the local SDK copy and build output again (they contain BMW's code)
  C. APP STORE - installs the on-car MGU App Store (mgu-store), so the client browses,
     installs and updates apps from the car's own screen afterwards.

Status and "back to stock" (restore) work the same way.

    python bimmhacks_installer.py                 # the window (default)
    python bimmhacks_installer.py install --key id_rsa [--ip 169.254.36.47] [--language en] [apps ...]
    python bimmhacks_installer.py test|status|restore --key id_rsa [--ip ...]
    python bimmhacks_installer.py root-status|root-flash|root-enable|root-fix-key [--vin ...] [--ip ...]
    python bimmhacks_installer.py store-install --key id_rsa [--ip ...]
    python bimmhacks_installer.py apps            # list the apps

Needs Python 3.6+, Node.js 14+ and the OpenSSH client (built into Windows 10/11, macOS, Linux).
Stage A (root access) also needs the `cryptography` Python package; the installer offers to add
it. Stage C (App Store) needs internet at the car (or a prepared USB stick).
"""
import argparse, json, os, queue, re, shutil, stat, subprocess, sys, tempfile, threading, time

VERSION = '1.10.1'
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_IP = '169.254.36.47'                # the unit's address with the idrive7-8-ssh-root tool
CONFIG = os.path.join(os.path.expanduser('~'), '.bimmhacks-installer.json')
IS_WIN = os.name == 'nt'
NULL = 'NUL' if IS_WIN else '/dev/null'

# bundled tools (see UPSTREAM.md / tools/*/LICENSE)
SSHROOT_DIR = os.environ.get('BIMMHACKS_SSHROOT_DIR') or os.path.join(REPO, 'tools', 'idrive7-8-ssh-root')
SSHROOT_PY = os.path.join(SSHROOT_DIR, 'ssh_enable.py')
SSHROOT_ENROLL = os.path.join(SSHROOT_DIR, 'data', 'enrollments')
STORE_DIR = os.environ.get('BIMMHACKS_STORE_DIR') or os.path.join(REPO, 'tools', 'mgu-store')
STORE_JS = os.path.join(STORE_DIR, 'mgu-install.js')
ARCHES = ('id7', 'id8-bdc3', 'id8-bcp')
CODING_BACKUP_PY = os.path.join(HERE, 'coding_backup.py')


class Done(str):
    """a worker's return value that replaces the window's fixed completion message"""


class InstallerError(Exception):
    pass


# ----------------------------------------------------------------------------- environment

def find_node():
    for c in (os.environ.get('BIMMHACKS_NODE'), shutil.which('node')):
        if c and os.path.isfile(c):
            return c
    if IS_WIN:
        for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'),
                     os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs')):
            if base and os.path.isfile(os.path.join(base, 'nodejs', 'node.exe')):
                return os.path.join(base, 'nodejs', 'node.exe')
    else:
        for p in ('/usr/local/bin/node', '/opt/homebrew/bin/node', '/usr/bin/node'):
            if os.path.isfile(p):
                return p
    return None


def find_ssh():
    p = shutil.which('ssh')
    if p:
        return p
    if IS_WIN:
        p = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'OpenSSH', 'ssh.exe')
        if os.path.isfile(p):
            return p
    return None


def popen_noinput(argv, **kw):
    """Start a child that is never asked anything. Its stdin is CLOSED (an empty pipe) rather than the NUL
    device: on Windows Python reports NUL as a terminal (os.isatty is True there), so a tool that asks
    "continue? [y/N]" whenever stdin is a terminal asked, got end-of-input and crashed - the apps build did,
    on a real PC. A closed pipe is not a terminal anywhere."""
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, **kw)
    p.stdin.close()
    return p


def preflight(log, need_ssh=True):
    """Node (for the App Store installer) and, when need_ssh, the OpenSSH client. -> (node, ssh)."""
    problems = []
    if sys.version_info < (3, 6):
        problems.append('Python 3.6 or newer is needed (this is %s)' % sys.version.split()[0])
    node = find_node()
    if not node:
        problems.append('Node.js is not installed: get the LTS version from https://nodejs.org/')
    else:
        out = subprocess.run([node, '--version'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        m = re.match(r'v(\d+)', out.stdout.decode('ascii', 'replace').strip())
        if not m or int(m.group(1)) < 14:
            problems.append('Node.js 14 or newer is needed (found %s): https://nodejs.org/'
                            % out.stdout.decode('ascii', 'replace').strip())
    ssh = find_ssh()
    if need_ssh and not ssh:
        problems.append('The OpenSSH client is missing. Windows: Settings > Apps > Optional features > '
                        'add "OpenSSH Client"')
    if problems:
        raise InstallerError('\n'.join(problems))
    return node, ssh


def ip_from_key(key):
    """the idrive7-8-ssh-root tool writes connect.txt (with the unit's address) next to id_rsa"""
    try:
        with open(os.path.join(os.path.dirname(key), 'connect.txt'), encoding='utf-8', errors='replace') as f:
            m = re.search(r'\b(\d{1,3}(?:\.\d{1,3}){3})\b', f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def load_config():
    try:
        with open(CONFIG, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(CONFIG, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=1)
    except OSError:
        pass


# ----------------------------------------------------------------------------- the car

class Car:
    """SSH to the head unit as root. The key is copied to a private temp file first: OpenSSH refuses
    keys other users can read, and keys from a USB stick or a download often are."""

    def __init__(self, ssh, ip, key, port=22, log=print):
        if not key or not os.path.isfile(key):
            raise InstallerError('SSH key not found: %s' % key)
        if not re.match(r'^[A-Za-z0-9.:\-\[\]%]+$', ip or ''):
            raise InstallerError('Not a valid head unit address: %r' % ip)
        self.ssh, self.ip, self.port, self.log = ssh, ip, int(port), log
        self.tmp = tempfile.mkdtemp(prefix='bimmhacks-')
        self.key = os.path.join(self.tmp, 'id_key')
        with open(key, 'rb') as f:
            data = f.read().replace(b'\r\n', b'\n')
        with open(self.key, 'wb') as f:
            f.write(data)
        if IS_WIN:
            user = os.environ.get('USERNAME', '')
            subprocess.run(['icacls', self.key, '/inheritance:r', '/grant:r', '%s:F' % user],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.chmod(self.key, stat.S_IRUSR | stat.S_IWUSR)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def args(self):
        # the same options upstream's push-to-car.ps1 uses: the unit's host key changes with every
        # flash, so it is never pinned
        return [self.ssh, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=' + NULL, '-o', 'GlobalKnownHostsFile=' + NULL,
                '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=15', '-o', 'LogLevel=ERROR',
                '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4',
                '-p', str(self.port), '-i', self.key, 'root@' + self.ip]

    def run(self, cmd, stdin_data=None, echo=True):
        """run a shell command on the unit, streaming its output to the log -> (exit code, text)"""
        p = subprocess.Popen(self.args() + ['export LC_ALL=C; ' + cmd],
                             stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if stdin_data is not None:
            def feed():
                try:
                    p.stdin.write(stdin_data)
                except OSError:
                    pass
                finally:
                    try:
                        p.stdin.close()
                    except OSError:
                        pass
            threading.Thread(target=feed, daemon=True).start()
        lines = []
        for raw in iter(p.stdout.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip('\r\n')
            lines.append(line)
            if echo:
                self.log('  car> ' + line)
        p.stdout.close()
        p.wait()
        return p.returncode, '\n'.join(lines)

    def read(self, cmd):
        """bytes a command prints on the unit (b'' if it failed)"""
        p = subprocess.run(self.args() + ['export LC_ALL=C; ' + cmd], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return p.stdout if p.returncode == 0 else b''


# ----------------------------------------------------------------------------- the steps

def test_connection(car, log):
    """Check we can log in to the head unit as root and it is an MGU with the app platform."""
    log('Connecting to the head unit at %s ...' % car.ip)
    code, out = car.run('echo CONNECTED; id -u; '
                        '[ -d /var/opt/public/oap/oap_12201 ] && echo MGU_OAP_OK; true', echo=False)
    if 'CONNECTED' not in out:
        raise InstallerError('Could not log in to the head unit (%s).\n%s\n'
                             'Check: ignition on, the car is connected (Ethernet / Wi-Fi), the IP address, '
                             'the SSH key, and that SSH was enabled with the root tool.' % (car.ip, out.strip()))
    if '\n0\n' not in '\n' + out + '\n':
        raise InstallerError('Logged in, but not as root. The installer needs the root SSH key.')
    if 'MGU_OAP_OK' not in out:
        raise InstallerError('This does not look like an MGU (iDrive 7 / 8) head unit with the app platform.')
    log('Connected as root. MGU app platform found.')


def do_test_connection(ssh, ip, key, log=print):
    car = Car(ssh, ip, key, log=log)
    try:
        test_connection(car, log)
    finally:
        car.close()


# ------------------------------------------------------- stage A: root access (idrive7-8-ssh-root)

def have_cryptography():
    return subprocess.run([sys.executable, '-c', 'import cryptography'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def ensure_cryptography(log, install=True):
    """The SSH-root tool needs the `cryptography` package. Install it into the user site if missing."""
    if not os.path.isfile(SSHROOT_PY):
        raise InstallerError('The root-access tool is missing (expected it in %s).' % SSHROOT_DIR)
    if have_cryptography():
        return
    if not install:
        raise InstallerError("The 'cryptography' Python package is needed for root access. "
                             "Install it with:  %s -m pip install --user cryptography" % os.path.basename(sys.executable))
    log("Installing the 'cryptography' package (one-time, for root access) ...")
    p = popen_noinput([sys.executable, '-m', 'pip', 'install', '--user', 'cryptography>=41.0'],
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(p.stdout.readline, b''):
        log('  pip> ' + raw.decode('utf-8', 'replace').rstrip('\r\n'))
    p.stdout.close()
    p.wait()
    if not have_cryptography():
        raise InstallerError("Could not install 'cryptography' automatically. Install it by hand:\n"
                             "  %s -m pip install --user cryptography" % os.path.basename(sys.executable))
    log("'cryptography' installed.")


def newest_enrolled_key(vin=None):
    """Newest data/enrollments/<VIN>/id_rsa written by the SSH-root tool (preferring a given VIN)."""
    best, best_mtime = None, -1
    if not os.path.isdir(SSHROOT_ENROLL):
        return None
    for d in os.listdir(SSHROOT_ENROLL):
        key = os.path.join(SSHROOT_ENROLL, d, 'id_rsa')
        if not os.path.isfile(key):
            continue
        if vin and d.upper() != vin.upper():
            continue
        m = os.path.getmtime(key)
        if m > best_mtime:
            best, best_mtime = key, m
    if best is None and vin:            # asked for a VIN with no key: fall back to any
        return newest_enrolled_key(None)
    return best


def run_ssh_root(action, log, vin=None, ip=None, gateway='auto', arch='id7', persist=False,
                 keep_debug=False, unique_key=False, verbose=False):
    """Run the bundled ssh_enable.py as a subprocess, streaming its output. -> exit code."""
    ensure_cryptography(log)
    argv = [sys.executable, SSHROOT_PY, action, '--yes']
    if vin:
        argv += ['--vin', vin]
    if ip:
        argv += ['--mgu-ip', ip]
    if gateway and gateway != 'auto':
        argv += ['--gateway', gateway]
    if arch and arch != 'id7':
        argv += ['--arch', arch]
    if persist:
        argv.append('--persist')
    if keep_debug:
        argv.append('--keep-debug')
    if unique_key:
        argv.append('--unique-key')
    if verbose:
        argv.append('--verbose')
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    p = popen_noinput(argv, cwd=SSHROOT_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(p.stdout.readline, b''):
        line = raw.decode('utf-8', 'replace').rstrip('\r\n')
        if 'DeprecationWarning' in line or 'utcnow()' in line:
            continue
        log('  root> ' + line)
    p.stdout.close()
    return p.wait()


FLASHING_ACTIONS = ('flash', 'fix-key')


def require_backup_confirmation(action, confirmed):
    """Flashing reprograms the head unit (it wipes the coding and the Digital Key pairings). The window asks
    for a backup confirmation before it enables the button; the command line must say the same thing
    explicitly, and this runs BEFORE anything is sent to the car."""
    if action in FLASHING_ACTIONS and not confirmed:
        raise InstallerError(
            'Refusing to flash: this reprograms the head unit and WIPES its coding and BMW Digital Key pairings.\n'
            'First save the coding (run "backup-coding", and take a BimmerCode / E-Sys backup), then repeat the '
            'command with --confirm-backup to say you have done it.')


def do_root(action, log, vin=None, ip=None, gateway='auto', arch='id7', backup_confirmed=False):
    """Root-access actions. Returns the path of the SSH key when one was written, else None."""
    if IS_WIN:
        log('Note: on Windows, run this installer as Administrator so the ENET link-local address is used.')
    require_backup_confirmation(action, backup_confirmed)
    flashing = action in FLASHING_ACTIONS
    if flashing and not (vin and re.match(r'^[A-HJ-NPR-Z0-9]{17}$', vin.upper())):
        raise InstallerError('A valid 17-character VIN is needed to flash. Use "Detect VIN", or type it in.')
    if flashing:
        log('WARNING: flashing reprograms the head unit and wipes its coding and BMW Digital Key pairings.')
        log('Back up the coding first (BimmerCode / E-Sys). This is a one-time step per car.')
    names = {'status': 'Reading the head unit state (read-only) ...',
             'flash': 'Flashing the debug image and enabling root SSH (this reboots the unit; a few minutes) ...',
             'enable': 'Re-opening SSH on an already-flashed unit (no flashing) ...',
             'fix-key': 'Re-flashing to install our root key (repair for refused logins) ...',
             'disable': 'Reverting persistence and stopping debug/SSH ...',
             'enter-prog': 'Putting the head unit into the programming session (no flash, no write) ...',
             'exit-prog': 'Leaving programming mode and rebooting to normal (recovery for a stuck blue screen) ...'}
    log(names.get(action, 'Running root-access action: %s' % action))
    code = run_ssh_root(action, log, vin=vin, ip=ip, gateway=gateway, arch=arch,
                        persist=(action in ('flash', 'enable', 'fix-key')))
    if code:
        raise InstallerError('The root-access step reported a problem (exit %d; see the log above).' % code)
    if action in ('flash', 'enable', 'fix-key'):
        key = newest_enrolled_key(vin)
        if key:
            log('Root SSH is enabled. The car SSH key is: %s' % key)
            return key
        log('Done, but no SSH key file was found under %s.' % SSHROOT_ENROLL)
    return None


def detect_vin(log, ip=None, gateway='auto', arch='id7'):
    """Read the VIN from the car over ENET via the SSH-root tool's status. -> VIN or None."""
    ensure_cryptography(log)
    lines = []
    def grab(m):
        lines.append(m)
        log(m)
    run_ssh_root('status', grab, ip=ip, gateway=gateway, arch=arch)
    return vin_from_output(lines)


# The root tool reports the VIN the car sends in its discovery reply as "... | VIN: <17 chars>" (or
# "VIN: not reported"). Only that marker counts: any other 17-character token in the output (a log
# file name, a hash, a certificate id) is not a VIN, and this value ends up in the field that a flash
# writes into the head unit.
_VIN_LINE = re.compile(r'\bVIN:\s*([A-HJ-NPR-Z0-9]{17})(?![A-Za-z0-9])')


def vin_from_output(lines):
    for m in lines:
        found = _VIN_LINE.search(m)
        if found:
            return found.group(1)
    return None


# ------------------------------------------------------- coding snapshot (before flashing)

CODING_BACKUP_DIR = os.path.join(REPO, 'coding-backups')


def _run_coding(args, log, gateway='auto', ip=None, arch='id7'):
    """Run coding_backup.py (a subprocess, like the root tool) and stream it. -> (exit code, lines)."""
    ensure_cryptography(log)
    if not os.path.isfile(CODING_BACKUP_PY):
        raise InstallerError('coding_backup.py is missing next to the installer.')
    argv = [sys.executable, CODING_BACKUP_PY] + list(args)
    if gateway and gateway != 'auto':
        argv += ['--gateway', gateway]
    if ip:
        argv += ['--mgu-ip', ip]
    if arch and arch != 'id7':
        argv += ['--arch', arch]
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    p = popen_noinput(argv, cwd=SSHROOT_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = []
    for raw in iter(p.stdout.readline, b''):
        line = raw.decode('utf-8', 'replace').rstrip('\r\n')
        if 'DeprecationWarning' in line or 'utcnow()' in line:
            continue
        lines.append(line)
        log('  coding> ' + line)
    p.stdout.close()
    return p.wait(), lines


def do_backup_coding(log, vin=None, ip=None, gateway='auto', arch='id7', out_dir=None, no_ncd=False):
    """Back up the car's coding over ENET: the proven snapshot (CAFD versions / VIN / IP / faults) and,
    where the unit hands it over, the coding data (NCD) itself. -> backup .txt path. Run before any flash."""
    out_dir = out_dir or CODING_BACKUP_DIR
    log('Reading the head unit over ENET for a coding backup ...')
    args = ['backup', '--out', out_dir]
    if vin:
        args += ['--vin', vin]
    if no_ncd:
        args.append('--no-ncd')
    code, lines = _run_coding(args, log, gateway=gateway, ip=ip, arch=arch)
    if code:
        raise InstallerError('The coding backup could not be read. Is the ENET cable in and the car awake?')
    saved = next((l.strip() for l in lines if l.strip().endswith('.txt')), None)
    log('Coding backup saved in: ' + out_dir)
    return saved or out_dir


def do_preflight_backup(log, vin=None, ip=None, gateway='auto', arch='id7', out_dir=None):
    """The root tool's READ-ONLY coding backup - a separate, independent option from "Back up coding".
    Scans the coding store over ENET, reads it twice and verifies, and writes coding_MGU.txt + RESTORE.txt
    (VO / code-by-FA re-coding). It never writes to the car. -> the folder it wrote into."""
    out_dir = out_dir or CODING_BACKUP_DIR
    log('Reading the head unit over ENET for the read-only coding backup ...')
    args = ['preflight', '--out', out_dir]
    if vin:
        args += ['--vin', vin]
    code, lines = _run_coding(args, log, gateway=gateway, ip=ip, arch=arch)
    if code:
        log('The read-only coding backup did not fully verify - keep a BimmerCode / E-Sys / Autel backup too.')
    folder = next((l.split(': ', 1)[1].strip() for l in lines if l.startswith('Read-only coding backup written to')), None)
    log('Read-only coding backup saved in: ' + (folder or out_dir))
    return folder or out_dir


def latest_coding_backup(out_dir=None, vin=None):
    """The newest coding-backup-*.json in the folder (optionally for a VIN), or None."""
    out_dir = out_dir or CODING_BACKUP_DIR
    if not os.path.isdir(out_dir):
        return None
    hits = [n for n in os.listdir(out_dir) if n.startswith('coding-backup-') and n.endswith('.json')
            and (not vin or vin.upper() in n)]
    if not hits:
        return None
    return os.path.join(out_dir, sorted(hits)[-1])


def do_verify_coding(log, backup_json, ip=None, gateway='auto', arch='id7'):
    """Read-only: compare the car's current coding to a backup. -> True if all blocks matched."""
    if not backup_json or not os.path.isfile(backup_json):
        raise InstallerError('Pick a coding backup (.json) to verify against.')
    log('Comparing the car\'s coding to %s ...' % os.path.basename(backup_json))
    code, _ = _run_coding(['verify', '--backup', backup_json], log, gateway=gateway, ip=ip, arch=arch)
    return code == 0


def do_restore_coding(log, backup_json, ip=None, gateway='auto', arch='id7', dry_run=False, confirm=False):
    """EXPERIMENTAL. Write the saved coding (NCD) back to the car with read-back verify. -> exit code."""
    if not backup_json or not os.path.isfile(backup_json):
        raise InstallerError('Pick a coding backup (.json) to restore from.')
    args = ['restore', '--backup', backup_json]
    if dry_run:
        args.append('--dry-run')
    if confirm:
        args.append('--confirm-restore')
    code, _ = _run_coding(args, log, gateway=gateway, ip=ip, arch=arch)
    if code == 2:
        raise InstallerError('The coding restore was refused (see the log above).')
    if code:
        raise InstallerError('The coding restore did not complete cleanly (see the log above). Finish it with '
                             'E-Sys / Autel from the saved .ncd files before relying on the car.')
    return code


# -------------------------------------------------------------- restarting the head unit
# What the Store's own "Restart head unit" does: the Node State Manager's normal restart, the request the car's
# own restart sends (mgu-store, native/mgu-updater/README.md, "Restarting the head unit"). It is refused while a
# Store install runs, so a restart never cuts one short. nsm_control exits 0 whether or not it took the request;
# when it cannot, it prints why as "[Error. <why>]". The command has no quotes or backslashes: on Windows ssh
# passes the remote command through its own quoting.
RESTART_CMD = ('L=/var/opt/mgu-deploy/lock; if [ -e $L ]; then read P B < $L; '
               'if [ x$B = x$(cat /proc/sys/kernel/random/boot_id) ] && kill -0 $P 2>/dev/null; '
               'then echo INSTALL_RUNNING; exit 3; fi; fi; /usr/bin/nsm_control --requestRestart 7 --normal 2>&1')
RESTART_BY_HAND = ('Restart it from the car instead: the Store\'s Settings > Restart head unit, or switch the '
                   'ignition off, wait until the screens are dark, and on again - or, on most cars, hold the volume '
                   'knob for about 30 seconds until the screens restart.')


def restart_head_unit(car, log=print):
    """Ask the head unit to restart, as the Store's own button does. Returns once the request is made: the
    unit goes down a few seconds later (wait_for_restart waits for that and for it to come back)."""
    log('Asking the head unit to restart ...')
    code, out = car.run(RESTART_CMD, echo=False)
    text = out.strip()
    for line in text.splitlines():
        if line.strip():
            log('  car> ' + line.strip())
    if code == 3 or 'INSTALL_RUNNING' in text:
        raise InstallerError('An install is still running on the head unit, so it was not restarted. Wait for it '
                             'to finish (a minute or two) and restart again.')
    refused = re.search(r'\[Error\.\s*([^\]]*)\]', text)
    if refused:
        raise InstallerError('The head unit did not take the restart request (%s). %s' % (refused.group(1).strip(), RESTART_BY_HAND))
    if code == 127 or 'nsm_control: not found' in text or 'No such file' in text:
        raise InstallerError('This head unit has no restart command to call. ' + RESTART_BY_HAND)
    if code not in (0, 255):          # 255: ssh was cut off as the unit went down - that is the restart working
        raise InstallerError('The restart request failed (exit %d): %s' % (code, text[-200:]))
    log('Restart requested.')


def head_unit_answers(car):
    code, out = car.run('echo HU_UP', echo=False)
    return code == 0 and 'HU_UP' in out


def wait_for_restart(car, log=print, poll_s=5.0, down_s=180.0, up_s=420.0, sleep=time.sleep, clock=time.monotonic):
    """After restart_head_unit: wait for the unit to go down, then for it to answer on SSH again. A unit that
    never goes down, or never comes back, is an error that says what to try."""
    log('Waiting for the head unit to restart: it goes dark, then comes back (usually one to two minutes) ...')
    t0 = clock()
    went_down = False
    while clock() - t0 < down_s:
        if not head_unit_answers(car):
            went_down = True
            break
        sleep(poll_s)
    if not went_down:
        raise InstallerError('The head unit is still answering %d seconds after the restart request, so it did not '
                             'restart. %s' % (down_s, RESTART_BY_HAND))
    log('  the head unit is down ...')
    t1 = clock()
    last_note = t1
    while clock() - t1 < up_s:
        if head_unit_answers(car):
            log('  the head unit is back (%d s).' % (clock() - t0))
            return
        sleep(poll_s)
        if clock() - last_note >= 30:
            last_note = clock()
            log('  still starting ... %d s' % (clock() - t0))
    raise InstallerError('The head unit did not answer on SSH again within %d minutes. Give it a few more minutes; if '
                         'it is up on the car but SSH stays closed, use Root access > "Enable (repair)".' % (up_s // 60))


def do_restart_head_unit(ip, key, log=print, wait=True, **timing):
    """Restart the head unit at ip (root key) and, with wait, until it answers on SSH again."""
    if not key or not os.path.isfile(key):
        raise InstallerError('The car SSH key is needed. Do the root-access step first, or pick the key.')
    ssh = find_ssh()
    if not ssh:
        raise InstallerError('The OpenSSH client is missing. Windows: Settings > Apps > Optional features > '
                             'add "OpenSSH Client"')
    car = Car(ssh, ip or DEFAULT_IP, key, log=log)
    try:
        restart_head_unit(car, log)
        if wait:
            wait_for_restart(car, log, **timing)
    finally:
        car.close()


# -------------------------------------------------------------- stage C: the App Store (mgu-store)

def do_store_install(node, ip, key, no_renderer=False, no_uplink=False, log=print, from_dir=None, restart=False):
    """Install the on-car MGU App Store with the bundled mgu-install.js.

    from_dir: a folder `usb-repo` staged earlier (it holds mgu-repo/mgu-store): the Store package is
    taken from there instead of GitHub, so this machine needs no internet (the Pocket box).
    restart: then restart the head unit and wait for it - the Store only starts after a restart."""
    if not os.path.isfile(STORE_JS):
        raise InstallerError('The App Store installer is missing (expected it in %s).' % STORE_DIR)
    if not key or not os.path.isfile(key):
        raise InstallerError('The car SSH key is needed. Do the root-access step first, or pick the key.')
    if from_dir:
        log('Installing the MGU App Store on the head unit from the package staged in %s ...' % from_dir)
    else:
        log('Installing the MGU App Store on the head unit (downloads the Store package; needs internet here) ...')
    argv = [node, STORE_JS, 'first-install', '--identity', key, '--no-restart-prompt']
    if from_dir:
        argv += ['--from', from_dir]
    if ip:
        argv += ['--host', ip]
    if no_renderer:
        argv.append('--no-renderer')
    if no_uplink:
        argv.append('--no-uplink')
    p = popen_noinput(argv, cwd=STORE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(p.stdout.readline, b''):
        line = raw.decode('utf-8', 'replace').rstrip('\r\n')
        log('  store> ' + line)
    p.stdout.close()
    code = p.wait()
    if code == 2:
        raise InstallerError('The App Store installer refused the request or the usage was wrong (see above).')
    if code:
        raise InstallerError('The App Store install failed (exit %d; see above).' % code)
    if restart:
        log('App Store installed. Restarting the head unit so the Store can start ...')
        do_restart_head_unit(ip, key, log=log)
        log('The head unit is back. The Store\'s tile is under Apps; give it a minute to finish starting.')
    else:
        log('App Store installed. It starts after the head unit restarts: press "Restart head unit" here, or '
            'restart it from the car (Settings, or ignition off and on).')
    log('Afterwards the client installs and updates apps from the car screen - Apps > Store.')


# ---- extra app sources installed through the Store (idrive-apps, and our own apps) --------------

# the Store's built-in sources ship with first-install; these two tabs are about these repos
IDRIVE_APPS_REPO = 'robertnisipeanu/idrive-apps'
CARAPPS_REPO = 'IterioDev/car-apps'          # iDrive 8 focused apps and tools (PolyForm Strict licence)
RIDETUBE_REPO = 'JustAnotherPU/ridetube'     # RideTube: an unofficial YouTube client (GPL-3), parked-only video
# our own apps: the repo that holds the Bimmhacks apps (defaults to this project's own repo)
BIMMHACKS_APPS_REPO = os.environ.get('BIMMHACKS_APPS_REPO', 'waliworld/Bimmhacks')
APP_GUIDE = os.path.join(REPO, 'docs', 'MAKE-YOUR-OWN-APP.md')


def do_usb_repo(node, dest, inputs, log=print):
    """Stage a repository (apps) onto a folder / USB stick with the Store's mgu-install.js usb-repo,
    so a car installs it from the USB page without the car needing internet. -> the mgu-repo folder."""
    if not os.path.isfile(STORE_JS):
        raise InstallerError('The App Store installer is missing (expected it in %s).' % STORE_DIR)
    if not dest:
        raise InstallerError('Pick a folder or USB stick to stage onto.')
    os.makedirs(dest, exist_ok=True)
    log('Staging %s onto %s (downloads the apps once; needs internet on this PC) ...'
        % (', '.join(inputs) if inputs else 'the default repository', dest))
    argv = [node, STORE_JS, 'usb-repo', dest] + list(inputs)
    p = popen_noinput(argv, cwd=STORE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(p.stdout.readline, b''):
        log('  store> ' + raw.decode('utf-8', 'replace').rstrip('\r\n'))
    p.stdout.close()
    code = p.wait()
    if code == 2:
        raise InstallerError('Staging was refused or the usage was wrong (see above).')
    if code:
        raise InstallerError('Staging the apps failed (exit %d; see above).' % code)
    repo = os.path.join(dest, 'mgu-repo')
    log('Staged. Put this stick in the car and open Apps > Store > USB to install.')
    return repo


# ---- adding a source to the on-car Store ---------------------------------------------------------
# The Store's updater takes a "source-add" request over its socket on the unit (mgu-store,
# native/mgu-updater/README.md "The socket"); unit_add_source.js does that, sent over ssh and run
# with the unit's own Node. These repos are the updater's default sources: nothing to add for them.
STORE_SOURCE_JS = os.path.join(HERE, 'unit_add_source.js')
DEPLOY_TOOL = '/var/opt/mgu-deploy/mgu-deploy.sh'      # the Store's deploy tool: present = Store installed
BUILTIN_SOURCES = {'cauliflowermossgrape/idrive7-apps': 'idrive7-apps', IDRIVE_APPS_REPO.lower(): 'idrive-apps',
                   CARAPPS_REPO.lower(): 'car-apps'}
SOURCE_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
CHANNEL_RE = re.compile(r'^(releases|tags|branch(:\S+)?)$')


class StoreNotReady(InstallerError):
    """the Store is on the car but its updater is not running yet (it starts with the head unit): not a failure"""


def store_present(car):
    """is the on-car Store (its deploy tool) installed on this head unit?"""
    _code, out = car.run('test -f %s && echo STORE_PRESENT; true' % DEPLOY_TOOL, echo=False)
    return 'STORE_PRESENT' in out


def add_store_source(car, repo, channel='releases', log=print, wait_s=None):
    """Add a GitHub repo as a source of the car's Store. -> 'added' or 'already'; raises with the
    updater's reason otherwise. Read-only for the car apart from the Store's own sources.json.
    wait_s: how long to keep trying for the updater to be up (default about 24 s; after a head-unit
    restart it needs longer)."""
    if not SOURCE_RE.match(repo or ''):
        raise InstallerError('The source must be a GitHub owner/repo, e.g. JustAnotherPU/ridetube.')
    if not CHANNEL_RE.match(channel or ''):
        raise InstallerError('The channel must be releases, tags, branch or branch:<name>.')
    if not os.path.isfile(STORE_SOURCE_JS):
        raise InstallerError('unit_add_source.js is missing next to the installer.')
    with open(STORE_SOURCE_JS, 'rb') as f:
        script = f.read()
    log('Adding %s (%s) as a source of the car\'s Store ...' % (repo, channel))
    import shlex
    wait_arg = ' --wait=%d' % int(wait_s) if wait_s else ''
    code, out = car.run('/usr/bin/node - %s %s%s' % (shlex.quote(repo), shlex.quote(channel), wait_arg),
                        stdin_data=script, echo=False)
    verdict = ''
    for line in out.splitlines():
        if line.strip():
            log('  store> ' + line)
        if line.startswith(('ADDED ', 'ALREADY ', 'WAITING:', 'ERROR:')):
            verdict = line.strip()
    if code == 0 and verdict.startswith('ADDED '):
        return 'added'
    if code == 0 and verdict.startswith('ALREADY '):
        return 'already'
    if code == 3 and verdict.startswith('WAITING:'):
        raise StoreNotReady(verdict[len('WAITING:'):].strip())
    why = verdict[len('ERROR:'):].strip() if verdict.startswith('ERROR:') else (out.strip()[-300:] or 'no answer from the car')
    raise InstallerError('Could not add %s to the car\'s Store: %s' % (repo, why))


def do_install_apps_source(node, ip, key, repo, label, stage_dir=None, log=print, channel='releases', from_dir=None,
                           restart_store=False):
    """Make an extra app source available on the car. These apps install through the on-car Store:

      - stage_dir given  -> write the repo to a USB folder (works offline); the car installs it
                            from Apps > Store > USB. No car internet needed.
      - no stage_dir     -> make sure the Store is installed (installing it if not; from_dir: the
                            package staged on this machine), then ADD the repo to the Store's sources
                            over ssh - the Store shows its apps once it has read them (car internet).
                            A built-in source (idrive-apps, car-apps, idrive7-apps) is already there.
      - restart_store    -> a Store installed here, or one whose updater is not running yet, needs the head
                            unit restarted to start: do that (and wait for it) instead of asking the user to.
    -> 'staged' | 'builtin' | 'added' | 'already'
       | 'pending' (the Store's updater is not up and the unit was not restarted: restart it, then retry)
       | 'slow'    (restarted, but the updater is still not up: give it a few minutes, then retry)
    """
    if not SOURCE_RE.match(repo or ''):
        raise InstallerError('The source must be a GitHub owner/repo, e.g. robertnisipeanu/idrive-apps.')
    if stage_dir:
        log('Staging %s to %s for offline install on the car ...' % (label, stage_dir))
        do_usb_repo(node, stage_dir, ['github:' + repo], log=log)
        log('%s staged. Put the stick in the car and open Apps > Store > USB to install.' % label)
        return 'staged'
    if not key or not os.path.isfile(key):
        raise InstallerError('The car SSH key is needed (or stage to a USB folder instead).')
    ssh = find_ssh()
    if not ssh:
        raise InstallerError('The OpenSSH client is missing. Windows: Settings > Apps > Optional features > '
                             'add "OpenSSH Client"')
    car = Car(ssh, ip, key, log=log)
    restarted = False
    try:
        if not store_present(car):
            log('The on-car Store is not installed yet - installing it first ...')
            do_store_install(node, ip, key, log=log, from_dir=from_dir, restart=restart_store)
            restarted = restart_store
        if repo.lower() in BUILTIN_SOURCES:
            log('%s is a built-in Store source (%s): it is already in the car\'s Store. On the car open '
                'Apps > Store, find the apps and Install.' % (label, BUILTIN_SOURCES[repo.lower()]))
            return 'builtin'
        result = None
        for attempt in (1, 2):
            try:
                # after a restart the updater takes a while to come up: keep trying longer
                result = add_store_source(car, repo, channel, log=log, wait_s=150 if restarted else None)
                break
            except StoreNotReady as e:
                if restart_store and not restarted and attempt == 1:
                    log('The Store\'s updater is not running (%s): restarting the head unit so it starts ...' % e)
                    restart_head_unit(car, log)
                    wait_for_restart(car, log)
                    restarted = True
                    continue
                if restarted:
                    log('The head unit restarted, but the Store\'s updater is still not up (%s).' % e)
                    log('Give it a few more minutes, then add %s again.' % label)
                    return 'slow'
                log('The Store\'s updater is not running yet (%s).' % e)
                log('It starts with the head unit: restart the head unit (the "Restart head unit" button, or the '
                    'car\'s Settings), wait a minute, then add %s again.' % label)
                return 'pending'
        if result == 'already':
            log('%s was already a source of the car\'s Store.' % label)
        else:
            log('%s added as a source of the car\'s Store.' % label)
        log('On the car: Apps > Store - its apps appear once the Store has read the source (the car needs internet).')
        log('(A source with no published Release, catalog or mgu-repo.json lists nothing: '
            'docs/MAKE-YOUR-OWN-APP.md, section 5.)')
        return result
    finally:
        car.close()


def source_done_message(result, label):
    """what to tell the user once do_install_apps_source has finished"""
    return {
        'staged': '%s staged - put the stick in the car and open Apps > Store > USB to install.' % label,
        'builtin': '%s is a built-in source of the car\'s Store: open Apps > Store on the car and install.' % label,
        'added': '%s was added to the car\'s Store: open Apps > Store on the car and install (the car needs internet to read it).' % label,
        'already': '%s was already in the car\'s Store: open Apps > Store on the car.' % label,
        'pending': 'The Store is installed, but its updater only starts with the head unit. Restart the head unit, '
                   'wait a minute, then add %s again.' % label,
        'slow': 'The head unit restarted, but the Store has not finished starting. Give it a few more minutes, '
                'then add %s again.' % label,
    }.get(result, 'Done.')


# ------------------------------------------------------------ PIDs from a capture (installer/pid_capture.py)

def do_pid_capture(path, log=print, out_dir=None):
    """Which identifiers a logging tool read from the car: analyse a Wireshark capture of the ENET link (or a
    root-tool session log) and write the report + a custom_pids.json for Bimmhacks Tuner next to it."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import pid_capture as pc
    if not os.path.isfile(path):
        raise InstallerError('Capture not found: %s' % path)
    log('Reading %s ...' % path)
    try:
        if path.lower().endswith(('.pcap', '.pcapng', '.cap')):
            frames = pc.frames_from_capture(path)
        else:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                frames = pc.frames_from_journal(f.read())
    except (OSError, ValueError) as e:
        raise InstallerError('Could not read the capture: %s' % e)
    if not frames:
        raise InstallerError('No diagnostic frames in it. Capture the ENET link (HSFZ on TCP port 6801) while the tool logs.')
    report = pc.analyze(frames)
    sk = pc.skeleton(report)
    out_dir = out_dir or os.path.dirname(os.path.abspath(path))
    base = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0])
    with open(base + '.pids.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    with open(base + '.custom_pids.json', 'w', encoding='utf-8') as f:
        json.dump(sk, f, indent=2)
    for line in pc.summary_lines(report):
        log('  ' + line)
    log('Report: ' + base + '.pids.json')
    log('Custom PID file (%d entries): %s - copy it into the Tuner\'s data folder as custom_pids.json' % (len(sk['pids']), base + '.custom_pids.json'))
    return base + '.custom_pids.json'


# ----------------------------------------------------------------------------- window

# One light surface, one ink, one accent. No dark bars: the window reads like a well-made form.
BG = '#f3f4f6'            # window
PANEL = '#ffffff'         # cards, header, console
LINE = '#e2e5ea'          # hairlines
INK = '#1b1f27'           # text
MUTED = '#6b7280'         # secondary text
ACCENT = '#1c5fd6'        # the one accent (buttons, active tab, brand mark)
ACCENT_DK = '#154aa8'
ACCENT_SOFT = '#e9f0fd'
DANGER = '#b42318'
DANGER_SOFT = '#fdecea'
DANGER_LINE = '#f2c4bd'
GOOD = '#1f7a4d'
GOOD_SOFT = '#e6f4ec'
WARN = '#8a4b00'
WARN_SOFT = '#fff4e5'
CHIP_BG = '#eef0f3'

CREDITS = [
    ('Flashing app, installer & Bimmhacks apps', 'Developed by Mr. Wali'),
    ('MGU custom apps (idrive7-apps)', 'cauliflowermossgrape - MIT'),
    ('Root over ENET (idrive7-8-ssh-root)', 'cauliflowermossgrape - public domain'),
    ('MGU App Store (mgu-store)', 'Robert Nisipeanu & IterioDev - GPL-3.0'),
    ('idrive-apps (cluster / HUD maps)', 'Robert Nisipeanu & contributors - GPL-3.0'),
    ('car-apps (iDrive 8 apps & tools)', 'IterioDev - PolyForm Strict 1.0.0'),
    ('RideTube (unofficial YouTube client)', 'JustAnotherPU - GPL-3.0'),
    ('Doom engine (doomgeneric) + Freedoom', 'id Software lineage / Freedoom - GPL-2.0'),
]


RESTART_NOTE = ('\n\nIf the Store is not on the car yet, or has not started, the head unit restarts once '
                '(about two minutes, the screens go dark) so it can. Park the car and keep the ignition on.')


def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    cfg = load_config()
    q = queue.Queue()
    busy = {'on': False}
    buttons = []
    F = 'Segoe UI' if IS_WIN else 'DejaVu Sans'
    MONO = 'Consolas' if IS_WIN else 'DejaVu Sans Mono'
    font = lambda size=10, weight='normal': (F, size, weight)

    root = tk.Tk()
    root.title('Bimmhacks MGU Tool  -  BMW iDrive 7 & 8')
    root.minsize(940, 800)
    root.configure(bg=BG)

    # ---- ttk: flat, bordered, light ---------------------------------------------------------------
    st = ttk.Style()
    try:
        st.theme_use('clam')
    except tk.TclError:
        pass
    st.configure('.', background=PANEL, foreground=INK, font=font(), borderwidth=0)
    st.configure('TFrame', background=PANEL)
    st.configure('TLabel', background=PANEL, foreground=INK)
    st.configure('Muted.TLabel', background=PANEL, foreground=MUTED)
    st.configure('Small.TLabel', background=PANEL, foreground=MUTED, font=font(9))
    st.configure('Title.TLabel', background=PANEL, foreground=INK, font=font(13, 'bold'))
    st.configure('Kicker.TLabel', background=PANEL, foreground=MUTED, font=font(8, 'bold'))
    st.configure('Warn.TLabel', background=WARN_SOFT, foreground=WARN)
    st.configure('TCheckbutton', background=PANEL, foreground=INK, focuscolor=PANEL)
    st.map('TCheckbutton', background=[('active', PANEL)])
    st.configure('TEntry', fieldbackground=PANEL, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, padding=4)
    st.map('TEntry', bordercolor=[('focus', ACCENT)], lightcolor=[('focus', ACCENT)], darkcolor=[('focus', ACCENT)])
    st.configure('TCombobox', fieldbackground=PANEL, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, arrowcolor=MUTED, padding=3)
    st.map('TCombobox', fieldbackground=[('readonly', PANEL)], bordercolor=[('focus', ACCENT)])
    root.option_add('*TCombobox*Listbox.font', font())
    st.configure('TButton', background=PANEL, foreground=INK, bordercolor=LINE, lightcolor=PANEL, darkcolor=PANEL,
                 borderwidth=1, focusthickness=0, padding=(12, 6), font=font())
    st.map('TButton', background=[('active', '#f6f7f9'), ('disabled', PANEL)], bordercolor=[('active', '#c9ced6')],
           foreground=[('disabled', '#b3b8c2')])
    st.configure('Accent.TButton', background=ACCENT, foreground='#ffffff', bordercolor=ACCENT, lightcolor=ACCENT,
                 darkcolor=ACCENT, padding=(14, 7), font=font(10, 'bold'))
    st.map('Accent.TButton', background=[('active', ACCENT_DK), ('disabled', '#a9c0ea')],
           bordercolor=[('active', ACCENT_DK), ('disabled', '#a9c0ea')], foreground=[('disabled', '#ffffff')])
    st.configure('Danger.TButton', background=DANGER_SOFT, foreground=DANGER, bordercolor=DANGER_LINE,
                 lightcolor=DANGER_SOFT, darkcolor=DANGER_SOFT, padding=(12, 6), font=font(10, 'bold'))
    st.map('Danger.TButton', background=[('active', '#f9dcd8'), ('disabled', '#f8f1f0')], foreground=[('disabled', '#d9a39c')])
    st.configure('TProgressbar', background=ACCENT, troughcolor=LINE, borderwidth=0, thickness=3)

    # ---- header: a white band with the brand mark, no dark bar -------------------------------------
    head = tk.Frame(root, bg=PANEL)
    head.pack(fill='x')
    hi = tk.Frame(head, bg=PANEL)
    hi.pack(fill='x', padx=22, pady=(14, 12))
    tk.Frame(hi, bg=ACCENT, width=10, height=10).pack(side='left', pady=(5, 0))
    tk.Label(hi, text='Bimmhacks', bg=PANEL, fg=INK, font=font(15, 'bold')).pack(side='left', padx=(10, 12))
    tk.Frame(hi, bg=LINE, width=1, height=20).pack(side='left', pady=(3, 0))
    tk.Label(hi, text='Root access  ·  Coding backup  ·  App Store  ·  BMW iDrive 7 and 8', bg=PANEL, fg=MUTED, font=font(10)).pack(side='left', padx=(12, 0), pady=(3, 0))
    tk.Label(hi, text='by Mr. Wali', bg=PANEL, fg=ACCENT, font=font(10, 'bold')).pack(side='right', pady=(3, 0))
    tk.Frame(root, bg=LINE, height=1).pack(fill='x')

    body = tk.Frame(root, bg=BG)
    body.pack(fill='both', expand=True)

    # ---- the pieces every section is built from ---------------------------------------------------
    def card(parent, fill='x', expand=False, pady=(0, 10), padx=22):
        c = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1, bd=0)
        c.pack(fill=fill, expand=expand, padx=padx, pady=pady)
        return c

    def two_up(page, weights=(1, 1)):
        """Two columns of cards side by side (the pages that would otherwise be twice as tall); equal unless
        weights says otherwise."""
        cols = tk.Frame(page, bg=BG)
        cols.pack(fill='x', pady=(10, 0))
        left, right = tk.Frame(cols, bg=BG), tk.Frame(cols, bg=BG)
        left.grid(row=0, column=0, sticky='nsew')
        right.grid(row=0, column=1, sticky='nsew')
        cols.columnconfigure(0, weight=weights[0], uniform='two')
        cols.columnconfigure(1, weight=weights[1], uniform='two')
        return left, right

    def kicker(parent, text):
        ttk.Label(parent, text=text.upper(), style='Kicker.TLabel').pack(anchor='w', padx=18, pady=(12, 0))

    def title(parent, text):
        ttk.Label(parent, text=text, style='Title.TLabel').pack(anchor='w', padx=18, pady=(2, 2))

    def para(parent, text, muted=True, wrap=860):
        ttk.Label(parent, text=text, style='Muted.TLabel' if muted else 'TLabel', justify='left', wraplength=wrap).pack(anchor='w', padx=18, pady=(2, 2))

    def notice(parent, text, wrap=840):
        f = tk.Frame(parent, bg=WARN_SOFT)
        f.pack(fill='x', padx=18, pady=(8, 2))
        ttk.Label(f, text=text, style='Warn.TLabel', justify='left', wraplength=wrap).pack(anchor='w', padx=12, pady=8)

    def row(parent, pady=(8, 12)):
        f = tk.Frame(parent, bg=PANEL)
        f.pack(fill='x', padx=14, pady=pady)
        return f

    def button(parent, text, cmd, style=None, side='left'):
        b = ttk.Button(parent, text=text, command=cmd, style=style) if style else ttk.Button(parent, text=text, command=cmd)
        b.pack(side=side, padx=4)
        buttons.append(b)
        return b

    def field(parent, label, var, width=None, r=0, c=0, colspan=1, sticky='w'):
        ttk.Label(parent, text=label, style='Muted.TLabel').grid(row=r, column=c, sticky='w', padx=(18, 8), pady=5)
        e = ttk.Entry(parent, textvariable=var, width=width) if width else ttk.Entry(parent, textvariable=var)
        e.grid(row=r, column=c + 1, columnspan=colspan, sticky=sticky, padx=(0, 10), pady=5)
        return e

    # ---- thread-safe UI updates: the worker threads only ever put messages on the queue -----------
    def log(msg):
        q.put(('log', msg))

    def ui_set(var, value):
        q.put(('set', var, value))

    def set_chip(text, kind='neutral'):
        q.put(('chip', text, kind))

    # ---- car connection (shared by every section) -------------------------------------------------
    conn = card(body, pady=(16, 10))
    ch = tk.Frame(conn, bg=PANEL)
    ch.pack(fill='x', padx=18, pady=(12, 0))
    ttk.Label(ch, text='CAR', style='Kicker.TLabel').pack(side='left')
    chip = tk.Label(ch, text='Not checked', bg=CHIP_BG, fg=MUTED, font=font(9), padx=9, pady=2)
    chip.pack(side='right')
    grid = tk.Frame(conn, bg=PANEL)
    grid.pack(fill='x', pady=(4, 8))
    vin_var = tk.StringVar(value=cfg.get('vin', ''))
    key_var = tk.StringVar(value=cfg.get('key', ''))
    ip_var = tk.StringVar(value=cfg.get('ip', DEFAULT_IP))
    field(grid, 'VIN', vin_var, width=22, r=0, c=0)
    ttk.Label(grid, text='Head unit', style='Muted.TLabel').grid(row=0, column=2, sticky='w', padx=(18, 8))
    arch_box = ttk.Combobox(grid, state='readonly', width=11, values=list(ARCHES))
    arch_box.current(ARCHES.index(cfg.get('arch', 'id7')) if cfg.get('arch', 'id7') in ARCHES else 0)
    arch_box.grid(row=0, column=3, sticky='w')
    field(grid, 'Root SSH key', key_var, r=1, c=0, colspan=3, sticky='we')

    def browse():
        start_dir = SSHROOT_ENROLL if os.path.isdir(SSHROOT_ENROLL) else REPO
        p = filedialog.askopenfilename(title='The car SSH key (id_rsa)', initialdir=start_dir)
        if p:
            key_var.set(p)
            got = ip_from_key(p)
            if got:
                ip_var.set(got)
    bb = ttk.Button(grid, text='Browse', command=browse)
    bb.grid(row=1, column=4, sticky='w', padx=(0, 18))
    buttons.append(bb)
    field(grid, 'IP address', ip_var, width=22, r=2, c=0)
    ttk.Label(grid, text='ENET default 169.254.36.47  ·  root access fills this in', style='Small.TLabel').grid(
        row=2, column=2, columnspan=3, sticky='w', padx=(18, 0))
    grid.columnconfigure(1, weight=1)

    # ---- sections: a flat tab strip and one content frame ------------------------------------------
    strip = tk.Frame(body, bg=BG)
    strip.pack(fill='x', padx=(28, 22))
    content = tk.Frame(body, bg=BG)
    content.pack(fill='x')
    pages, tabs = [], []

    def show(i):
        # only the chosen page stays gridded, so the content is exactly as tall as that page and the
        # activity log below gets the rest of the window (a hidden tall page must not reserve the space)
        for k, (lab, line) in enumerate(tabs):
            lab.configure(fg=INK if k == i else MUTED)
            line.configure(bg=ACCENT if k == i else BG)
            if k == i:
                pages[k].grid()
            else:
                pages[k].grid_remove()

    def section(name):
        cell = tk.Frame(strip, bg=BG)
        cell.pack(side='left')
        lab = tk.Label(cell, text=name, bg=BG, fg=MUTED, font=font(10, 'bold'), padx=12, pady=8, cursor='hand2')
        lab.pack()
        line = tk.Frame(cell, bg=BG, height=2)
        line.pack(fill='x')
        i = len(tabs)
        lab.bind('<Button-1>', lambda e, i=i: show(i))
        tabs.append((lab, line))
        page = tk.Frame(content, bg=BG)
        page.grid(row=0, column=0, sticky='nsew')
        pages.append(page)
        return page
    content.rowconfigure(0, weight=1)
    content.columnconfigure(0, weight=1)

    def save():
        save_config(dict(load_config(),
                         vin=vin_var.get().strip().upper(), key=key_var.get().strip().strip('"'),
                         ip=ip_var.get().strip(),
                         arch=ARCHES[arch_box.current()] if arch_box.current() >= 0 else 'id7'))

    def conn_values():
        save()
        return (vin_var.get().strip().upper(), key_var.get().strip().strip('"'),
                ip_var.get().strip(), ARCHES[arch_box.current()] if arch_box.current() >= 0 else 'id7')

    def need_ssh():
        s = find_ssh()
        if not s:
            raise InstallerError('The OpenSSH client is missing. Windows: Settings > Apps > Optional '
                                 'features > add "OpenSSH Client".')
        return s

    def need_node():
        n = find_node()
        if not n:
            raise InstallerError('Node.js is not installed: get the LTS version from https://nodejs.org/')
        return n

    def start(work_fn, done_msg):
        if busy['on']:
            return
        busy['on'] = True
        progress.pack(fill='x', padx=22, pady=(0, 6), before=act)
        progress.start(12)
        for b in buttons:
            b.state(['disabled'])

        def work():
            try:
                r = work_fn()
                q.put(('done', (True, str(r) if isinstance(r, Done) else done_msg)))
            except InstallerError as e:
                log('ERROR: ' + str(e))
                q.put(('done', (False, str(e))))
            except Exception as e:
                log('ERROR: %s: %s' % (type(e).__name__, e))
                q.put(('done', (False, 'Unexpected error: %s' % e)))
        threading.Thread(target=work, daemon=True).start()

    # ======================================================= 1. Root access (+ coding backup / restore)
    p1 = section('Root access')
    l1, r1c = two_up(p1, (3, 2))
    c1 = card(l1, pady=(0, 10), padx=(22, 6))
    kicker(c1, 'Step 1  ·  once per car')
    title(c1, 'Back up the coding, then root over ENET')
    para(c1, 'Wired ENET cable, ignition on. Already rooted and holding the key? Skip this and pick the key '
             'with Browse above - the tool reads the head unit\'s address from the car, and fills the IP in.', wrap=400)
    notice(c1, 'Flashing reprograms the head unit and wipes its coding and BMW Digital Key pairings. Flash + enable '
               'SSH takes a coding backup first (or you confirm you have one from another tool); re-pair keys after. '
               'Windows: run as Administrator.', wrap=500)
    backup_done = tk.BooleanVar(value=False)
    gchk = row(c1, pady=(8, 0))
    ttk.Checkbutton(gchk, variable=backup_done,
                    text='I have a coding backup (this tool, BimmerCode / E-Sys or Autel)').pack(side='left', padx=4)
    restore_var = tk.StringVar(value=latest_coding_backup() or '')

    def on_backup():
        vin, _, _, arch = conn_values()
        def w():
            path = do_backup_coding(log, vin=vin or None, arch=arch)
            ui_set(backup_done, True)
            if path and path.endswith('.txt'):
                ui_set(restore_var, path[:-4] + '.json')
            log('Backup done. Keep it safe; also keep your BimmerCode / E-Sys / Autel backup.')
        start(w, 'Coding backup saved - see the activity log.')

    def on_preflight():
        vin, _, _, arch = conn_values()
        def w():
            do_preflight_backup(log, vin=vin or None, arch=arch)
            ui_set(backup_done, True)
            log('Read-only coding backup done. It never writes to the car; restore with E-Sys (code by FA).')
        start(w, 'Read-only coding backup saved - see the activity log.')

    def on_detect_vin():
        _, _, _, arch = conn_values()
        def w():
            v = detect_vin(log, arch=arch)
            if v:
                ui_set(vin_var, v)
                log('Detected VIN: ' + v)
            else:
                log('VIN not detected. Is the ENET cable in and the car awake?')
        start(w, 'VIN read - see the activity log.')

    def on_root(action, confirm=None, need_backup=False):
        vin, _, _, arch = conn_values()
        backup_first = False
        confirmed = backup_done.get()
        if need_backup and not confirmed:
            # force a coding backup, or make the client explicitly accept flashing without one
            ans = messagebox.askyesnocancel(
                'Coding backup required',
                'Flashing WIPES the head unit\'s coding and BMW Digital Key pairings.\n\n'
                'Back up the coding now, before flashing?\n\n'
                '  Yes  -  back it up now with this tool (recommended)\n'
                '  No   -  flash WITHOUT a Bimmhacks backup\n'
                '  Cancel  -  do not flash')
            if ans is None:
                return
            if ans:
                backup_first = True
                confirmed = True
            else:
                if not messagebox.askokcancel(
                        'Flash without a backup?',
                        'You are about to flash WITHOUT a coding backup from this tool.\n\n'
                        'You MUST already have a coding backup made with another tool - BimmerCode, E-Sys or '
                        'Autel. The flash wipes the coding and it CANNOT be restored without one; without it the '
                        'head unit can be left partly-coded.\n\nFlash anyway?', icon='warning'):
                    return
                confirmed = True
        if confirm and not messagebox.askyesno('Root access', confirm):
            return
        def w():
            if backup_first:
                log('Backing up the coding before the flash ...')
                path = do_backup_coding(log, vin=vin or None, arch=arch)
                ui_set(backup_done, True)
                if path and path.endswith('.txt'):
                    ui_set(restore_var, path[:-4] + '.json')
                log('Backup done. Proceeding to flash.')
            key = do_root(action, log, vin=vin or None, arch=arch, backup_confirmed=confirmed)
            if key:
                ui_set(key_var, key)
                got = ip_from_key(key)
                if got:
                    ui_set(ip_var, got)
                set_chip('Rooted  ·  key saved', 'good')
        start(w, 'Root-access step finished - see the activity log.')

    rr1 = row(c1, pady=(6, 4))
    button(rr1, 'Detect VIN', on_detect_vin)
    button(rr1, 'Check state', lambda: on_root('status'))
    rrb = row(c1, pady=(0, 4))
    button(rrb, 'Back up coding', on_backup)
    button(rrb, 'Read-only backup', on_preflight)
    rr2 = row(c1, pady=(0, 14))
    button(rr2, 'Flash + enable SSH', lambda: on_root('flash',
           'Flash the debug image and enable root SSH?\n\nThis REPROGRAMS the head unit and WIPES its coding '
           'and Digital Key pairings. Do this only once per car.', need_backup=True), style='Accent.TButton')
    button(rr2, 'Enable (repair)', lambda: on_root('enable'))
    rr3 = row(c1, pady=(0, 14))
    button(rr3, 'Fix refusing key', lambda: on_root('fix-key',
           'Re-flash to install our root key? Use only if SSH opens but login is refused. It re-flashes '
           '(wipes coding again).', need_backup=True))
    button(rr3, 'Disable', lambda: on_root('disable', 'Revert persistence and stop debug/SSH on this car?'), style='Danger.TButton')
    rr4 = row(c1, pady=(0, 6))
    button(rr4, 'Fix blue screen', lambda: on_root('exit-prog',
           'Head unit stuck on the blue programming screen? Leave programming mode and reboot it to normal.\n\n'
           'No flash and no write - it just sends the end-of-programming routines and restarts the unit.'))

    # ---- right: experimental coding restore -------------------------------------------------------
    c1b = card(r1c, pady=(0, 10), padx=(6, 22))
    kicker(c1b, 'Coding  ·  experimental')
    title(c1b, 'Restore the coding after a flash')
    para(c1b, 'A flash wipes the head unit\'s coding. "Back up coding" saves the CAFD versions and, where the '
              'unit hands it over, the coding data (NCD) itself. This can write that NCD back and read it back '
              'to verify it.', wrap=320)
    notice(c1b, 'Experimental: a wrong coding write can leave the HMI partly-coded. Keep E-Sys / Autel ready and '
                'try it on a car you can recover. "Verify" and "Dry run" never write to the car.', wrap=300)
    bf = row(c1b, pady=(6, 2))
    ttk.Label(bf, text='Backup', style='Muted.TLabel').pack(side='left', padx=(4, 8))
    button(bf, 'Browse', lambda: _pick_backup(), side='right')
    ttk.Entry(bf, textvariable=restore_var, width=6).pack(side='left', fill='x', expand=True, padx=(0, 6))

    def _pick_backup():
        p = filedialog.askopenfilename(title='A coding backup (.json)', initialdir=CODING_BACKUP_DIR,
                                       filetypes=[('Coding backup', '*.json'), ('All files', '*.*')])
        if p:
            restore_var.set(p)

    def on_verify():
        _, _, _, arch = conn_values()
        bk = restore_var.get().strip()
        start(lambda: do_verify_coding(log, bk, arch=arch), 'Coding compared - see the activity log.')

    def on_restore(dry):
        _, _, _, arch = conn_values()
        bk = restore_var.get().strip()
        if not dry and not messagebox.askyesno(
                'Restore coding (experimental)',
                'Write the saved coding (NCD) back to the car?\n\nThis is EXPERIMENTAL. A wrong coding write can '
                'leave the HMI partly-coded. Only run it on a car you can recover with E-Sys / Autel, and only '
                'with a backup this tool took from THIS car.\n\nEvery block is read back and checked. Continue?'):
            return
        def w():
            do_restore_coding(log, bk, arch=arch, dry_run=dry, confirm=not dry)
        start(w, 'Coding dry-run done - see the activity log.' if dry else
              'Coding restore finished - see the activity log. Verify the car, and finish with E-Sys / Autel if any block failed.')

    vr = row(c1b, pady=(6, 2))
    button(vr, 'Verify coding', on_verify)
    button(vr, 'Dry run', lambda: on_restore(True))
    vr2 = row(c1b, pady=(0, 12))
    button(vr2, 'Restore coding (experimental)', lambda: on_restore(False), style='Danger.TButton')

    # ======================================================= 2. App Store
    p2 = section('App Store')
    l2, r2c = two_up(p2, (3, 2))
    c2 = card(l2, pady=(0, 10), padx=(22, 6))
    kicker(c2, 'Step 2  ·  on-car store')
    title(c2, 'Install the MGU App Store')
    para(c2, 'The car then browses, installs and updates apps from its own screen (Apps > Store). It needs '
             'internet for that, and the Store only starts after the head unit has restarted.', wrap=470)

    def option(parent, var, text, explain):
        ttk.Checkbutton(parent, text=text, variable=var).pack(anchor='w', padx=20, pady=(8, 0))
        ttk.Label(parent, text=explain, style='Small.TLabel', justify='left', wraplength=440).pack(anchor='w', padx=42)
    renderer_var = tk.BooleanVar(value=not cfg.get('no_renderer', False))
    uplink_var = tk.BooleanVar(value=not cfg.get('no_uplink', False))
    restart_var = tk.BooleanVar(value=cfg.get('store_restart', True))
    option(c2, renderer_var, 'Renderer',
           'Draws the Store and other apps with the head unit\'s graphics chip: smooth, with the on-screen '
           'keyboard. Without it the Store falls back to simpler screens.')
    option(c2, uplink_var, 'Internet uplink',
           'Gives the car internet through a USB Ethernet adapter, an LTE stick, phone tethering or Wi-Fi, so '
           'the Store can download apps. Untick only if you will install from a USB stick.')
    option(c2, restart_var, 'Restart the head unit when done',
           'About two minutes; the installer waits until the car is back.')

    def on_test():
        _, key, ip, _ = conn_values()
        def w():
            do_test_connection(need_ssh(), ip, key, log)
            set_chip('SSH OK  ·  ' + ip, 'good')
        start(w, 'Connection OK.')

    def on_store():
        _, key, ip, _ = conn_values()
        no_r, no_u, rs = not renderer_var.get(), not uplink_var.get(), restart_var.get()
        save_config(dict(load_config(), no_renderer=no_r, no_uplink=no_u, store_restart=rs))
        note = ('\n\nWhen it is done the head unit restarts (about two minutes, the screens go dark). Park the car '
                'and keep the ignition on.' if rs else '\n\nThe Store starts after the head unit restarts: use '
                '"Restart head unit" afterwards.')
        if not messagebox.askokcancel('Install App Store', 'Install the MGU App Store on the car at %s?\n\n'
                                      'The car needs internet for this.%s' % (ip, note)):
            return
        def w():
            do_store_install(need_node(), ip, key, no_renderer=no_r, no_uplink=no_u, log=log, restart=rs)
        start(w, 'App Store installed and the head unit restarted - open Apps > Store on the car.' if rs else
              'App Store installed. Press "Restart head unit" (or restart the car) so the Store can start.')

    def on_restart():
        _, key, ip, _ = conn_values()
        if not messagebox.askokcancel('Restart head unit', 'Restart the head unit at %s now?\n\nThe screens go dark '
                                      'for about two minutes. Do not do this while an install is running, or while '
                                      'driving.' % ip):
            return
        start(lambda: do_restart_head_unit(ip, key, log=log), 'The head unit restarted.')
    sr = row(c2, pady=(10, 4))
    button(sr, 'Test connection', on_test)
    button(sr, 'Install the App Store', on_store, style='Accent.TButton')
    sr2 = row(c2, pady=(0, 12))
    button(sr2, 'Restart head unit', on_restart)

    # ---- right: add sources to the car's Store ----------------------------------------------------
    c2b = card(r2c, pady=(0, 10), padx=(6, 22))
    kicker(c2b, 'Sources  ·  on the car')
    title(c2b, 'Add apps to the Store')
    para(c2b, 'idrive7-apps, the cluster / HUD maps and IterioDev\'s car-apps are built in - they appear in the '
              'car\'s Store as soon as it is installed. Add RideTube or your own GitHub repo here, or on the car '
              '(Apps > Store > Add source).', wrap=320)

    def add_source(repo, label, confirm):
        _, key, ip, _ = conn_values()
        if not re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', repo or ''):
            messagebox.showwarning('Add source', 'Enter the source as owner/repo, e.g. JustAnotherPU/ridetube.')
            return
        if not messagebox.askokcancel('Add source', confirm % ip + RESTART_NOTE):
            return
        def w():
            r = do_install_apps_source(need_node(), ip, key, repo, label, log=log, restart_store=True)
            return Done(source_done_message(r, label))
        start(w, 'Source added - open Apps > Store on the car.')

    rt = row(c2b, pady=(4, 2))
    button(rt, 'Add RideTube (YouTube, unofficial)', lambda: add_source(
        RIDETUBE_REPO, 'RideTube (YouTube, unofficial)',
        'Make the RideTube YouTube client available on the car at %s?\n\nUnofficial, GPL-3; video is parked-only.'),
        style='Accent.TButton')
    our_repo = tk.StringVar(value=cfg.get('our_repo', BIMMHACKS_APPS_REPO))
    orr = row(c2b, pady=(8, 2))
    ttk.Label(orr, text='Your repo', style='Muted.TLabel').pack(side='left', padx=(4, 8))
    ttk.Entry(orr, textvariable=our_repo, width=6).pack(side='left', fill='x', expand=True, padx=(0, 6))

    def on_our():
        repo = our_repo.get().strip()
        save_config(dict(load_config(), our_repo=repo))
        add_source(repo, 'your apps (%s)' % repo, 'Make %s available on the car at ' % repo + '%s?')
    orr2 = row(c2b, pady=(0, 2))
    button(orr2, 'Add your repo to the Store', on_our)
    orr3 = row(c2b, pady=(0, 12))
    button(orr3, 'How to publish an app', lambda: _open_path(APP_GUIDE))

    # ======================================================= 3. Credits
    p3 = section('Credits')
    c3 = card(p3, pady=(10, 10))
    kicker(c3, 'Credits & licences')
    title(c3, 'Built on open-source work')
    tbl = tk.Frame(c3, bg=PANEL)
    tbl.pack(fill='x', padx=18, pady=(4, 6))
    for i, (what, who) in enumerate(CREDITS):
        ttk.Label(tbl, text=what).grid(row=i, column=0, sticky='w', pady=2)
        ttk.Label(tbl, text=who, style='Muted.TLabel').grid(row=i, column=1, sticky='w', padx=(24, 0), pady=2)
    para(c3, 'The apps and tools are free and open source. Charge for the install and support, credit the '
             'original authors, and tell clients the apps themselves are free. This tool installs the on-car '
             'Store and enables root; the apps come from the Store, from their authors\' own repositories.')
    cb = row(c3)
    button(cb, 'idrive7-apps', lambda: _open_url('https://github.com/cauliflowermossgrape/idrive7-apps'))
    button(cb, 'ssh-root tool', lambda: _open_url('https://github.com/cauliflowermossgrape/idrive7-8-ssh-root'))
    button(cb, 'mgu-store', lambda: _open_url('https://github.com/robertnisipeanu/mgu-store'))

    # ---- activity ---------------------------------------------------------------------------------
    progress = ttk.Progressbar(body, mode='indeterminate')
    act = card(body, fill='both', expand=True, pady=(6, 8))
    ah = tk.Frame(act, bg=PANEL)
    ah.pack(fill='x', padx=18, pady=(10, 4))
    ttk.Label(ah, text='ACTIVITY', style='Kicker.TLabel').pack(side='left')
    logbox = tk.Text(act, height=8, bg=PANEL, fg=INK, insertbackground=INK, relief='flat', bd=0, wrap='word',
                     font=(MONO, 9), padx=4, pady=2, highlightthickness=0)
    sb = ttk.Scrollbar(act, command=logbox.yview)
    logbox.configure(yscrollcommand=sb.set)
    logbox.tag_configure('err', foreground=DANGER)
    logbox.tag_configure('ok', foreground=GOOD)
    clr = ttk.Button(ah, text='Clear', command=lambda: logbox.delete('1.0', 'end'))
    clr.pack(side='right')
    sb.pack(side='right', fill='y', padx=(0, 6), pady=(0, 10))
    logbox.pack(fill='both', expand=True, padx=(18, 0), pady=(0, 10))
    foot = tk.Frame(body, bg=BG)
    foot.pack(fill='x', padx=22, pady=(0, 10))
    tk.Label(foot, text='Bimmhacks by Mr. Wali  ·  root tool & apps: cauliflowermossgrape  ·  Store by Robert Nisipeanu & IterioDev (GPL-3.0)',
             bg=BG, fg=MUTED, font=font(9)).pack(side='left')

    chip_colors = {'neutral': (CHIP_BG, MUTED), 'good': (GOOD_SOFT, GOOD), 'warn': (WARN_SOFT, WARN)}

    def pump():
        try:
            while True:
                m = q.get_nowait()
                kind = m[0]
                if kind == 'log':
                    text = m[1]
                    tag = 'err' if text.startswith('ERROR') else 'ok' if text.startswith(('Done', 'DONE', 'Root SSH is enabled', 'Connected')) else None
                    logbox.insert('end', text + '\n', tag)
                    logbox.see('end')
                elif kind == 'set':
                    m[1].set(m[2])
                elif kind == 'chip':
                    bg, fg = chip_colors.get(m[2], chip_colors['neutral'])
                    chip.configure(text=m[1], bg=bg, fg=fg)
                elif kind == 'done':
                    busy['on'] = False
                    progress.stop()
                    progress.pack_forget()
                    for b2 in buttons:
                        b2.state(['!disabled'])
                    ok, msg = m[1]
                    (messagebox.showinfo if ok else messagebox.showerror)('Bimmhacks', msg)
        except queue.Empty:
            pass
        root.after(100, pump)

    show(0)
    log('Bimmhacks MGU Tool  v%s  ·  root access, coding backup & the on-car App Store for BMW iDrive 7 / 8' % VERSION)
    log('Root access once per car (back up the coding first), then install the on-car Store; apps install from the Store.')
    root.after(100, pump)
    root.mainloop()


def _open_url(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def _open_path(path):
    """Open a local file with the OS default app (the app-making guide)."""
    try:
        if not os.path.isfile(path):
            _open_url('https://github.com/' + BIMMHACKS_APPS_REPO + '/blob/main/docs/MAKE-YOUR-OWN-APP.md')
            return
        if IS_WIN:
            os.startfile(path)  # noqa
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
    except Exception:
        _open_url('file://' + path)


# ----------------------------------------------------------------------------- command line

ROOT_ACTIONS = {'root-status': 'status', 'root-flash': 'flash', 'root-enable': 'enable',
                'root-fix-key': 'fix-key', 'root-disable': 'disable',
                'root-enter-prog': 'enter-prog', 'root-exit-prog': 'exit-prog'}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Bimmhacks MGU Tool - root access, coding backup, App Store (iDrive 7 & 8)')
    ap.add_argument('action', nargs='?', default='gui',
                    choices=['gui',
                             'root-status', 'root-flash', 'root-enable', 'root-fix-key', 'root-disable',
                             'root-enter-prog', 'root-exit-prog',
                             'detect-vin', 'backup-coding', 'preflight-coding', 'verify-coding', 'restore-coding',
                             'test', 'store-install', 'restart-head-unit',
                             'install-ridetube', 'install-idrive-apps', 'install-car-apps', 'install-our-apps',
                             'usb-repo', 'pid-capture'])
    ap.add_argument('--key', help='root SSH private key (id_rsa from the root-access step)')
    ap.add_argument('--ip', help='head unit address (default: from connect.txt next to the key, else %s)' % DEFAULT_IP)
    ap.add_argument('--vin', help='17-char VIN (required for root-flash / root-fix-key)')
    ap.add_argument('--arch', default='id7', choices=ARCHES, help='head-unit variant (default: id7)')
    ap.add_argument('--gateway', default='auto', help="ENET gateway address, or 'auto' (default)")
    ap.add_argument('--out', help='backup-coding / pid-capture: folder to write into')
    ap.add_argument('--backup', help='verify-coding / restore-coding: the coding backup .json to use')
    ap.add_argument('--capture', help='pid-capture: a Wireshark .pcap/.pcapng of the ENET link, or a root-tool log')
    ap.add_argument('--confirm-backup', action='store_true',
                    help='root-flash / root-fix-key: I have saved the coding and accept that a flash wipes it')
    ap.add_argument('--no-ncd', action='store_true', help='backup-coding: snapshot only, do not read the coding data')
    ap.add_argument('--dry-run', action='store_true', help='restore-coding: show what would be written, write nothing')
    ap.add_argument('--confirm-restore', action='store_true',
                    help='restore-coding: I accept this is experimental and can leave the HMI partly-coded, E-Sys/Autel ready')
    ap.add_argument('--repo', help='install-our-apps / usb-repo: a GitHub owner/repo source')
    ap.add_argument('--stage', help='install-*-apps / usb-repo: stage the apps to this folder / USB for offline install')
    ap.add_argument('--no-renderer', action='store_true', help='store-install: leave out the renderer')
    ap.add_argument('--no-uplink', action='store_true', help='store-install: leave out the internet uplink')
    ap.add_argument('--restart', action='store_true',
                    help='store-install / install-*: restart the head unit when the Store needs it (and wait for it)')
    ap.add_argument('--no-wait', action='store_true', help='restart-head-unit: do not wait for the unit to come back')
    a = ap.parse_args(argv)

    def log(m):
        print(m, flush=True)

    if a.action == 'gui':
        import importlib.util
        if importlib.util.find_spec('tkinter') is None:
            sys.exit('No Tk (window toolkit) in this Python. Use the command line, e.g.:\n'
                     '  python bimmhacks_installer.py store-install --key path/to/id_rsa')
        return run_gui()

    try:
        # root access and the coding tools reach the car over ENET; they need neither Node nor an existing key
        if a.action in ROOT_ACTIONS:
            do_root(ROOT_ACTIONS[a.action], log, vin=a.vin, ip=a.ip, gateway=a.gateway, arch=a.arch,
                    backup_confirmed=a.confirm_backup)
            return
        if a.action == 'detect-vin':
            vin = detect_vin(log, ip=a.ip, gateway=a.gateway, arch=a.arch)
            print('VIN: ' + vin if vin else 'VIN not detected (is the ENET cable in and the car awake?)')
            return
        if a.action == 'backup-coding':
            do_backup_coding(log, vin=a.vin, ip=a.ip, gateway=a.gateway, arch=a.arch, out_dir=a.out, no_ncd=a.no_ncd)
            return
        if a.action == 'preflight-coding':
            do_preflight_backup(log, vin=a.vin, ip=a.ip, gateway=a.gateway, arch=a.arch, out_dir=a.out)
            return
        if a.action == 'verify-coding':
            bk = a.backup or latest_coding_backup(a.out, a.vin)
            ok = do_verify_coding(log, bk, ip=a.ip, gateway=a.gateway, arch=a.arch)
            sys.exit(0 if ok else 1)
        if a.action == 'restore-coding':
            bk = a.backup or latest_coding_backup(a.out, a.vin)
            do_restore_coding(log, bk, ip=a.ip, gateway=a.gateway, arch=a.arch,
                              dry_run=a.dry_run, confirm=a.confirm_restore)
            return
        if a.action == 'pid-capture':
            cap = a.capture or a.repo
            if not cap:
                ap.error('--capture <file> is needed')
            do_pid_capture(cap, log=log, out_dir=a.out)
            return

        if a.action == 'usb-repo':
            node = find_node()
            if not node:
                raise InstallerError('Node.js is not installed: get the LTS version from https://nodejs.org/')
            if not a.stage:
                ap.error('--stage <folder> is needed')
            do_usb_repo(node, a.stage, (['github:' + a.repo] if a.repo else []), log=log)
            return

        node, ssh = preflight(log, need_ssh=(a.action in ('test', 'store-install', 'restart-head-unit')))
        if a.action == 'test':
            if not a.key:
                ap.error('--key is needed')
            do_test_connection(ssh, a.ip or ip_from_key(a.key) or DEFAULT_IP, a.key, log=log)
            return
        if a.action == 'store-install':
            if not a.key:
                ap.error('--key is needed')
            do_store_install(node, a.ip or ip_from_key(a.key) or DEFAULT_IP, a.key,
                             no_renderer=a.no_renderer, no_uplink=a.no_uplink, log=log, restart=a.restart)
            return
        if a.action == 'restart-head-unit':
            if not a.key:
                ap.error('--key is needed')
            do_restart_head_unit(a.ip or ip_from_key(a.key) or DEFAULT_IP, a.key, log=log, wait=not a.no_wait)
            log('The head unit restarted.' if not a.no_wait else 'Restart requested.')
            return
        if a.action in ('install-idrive-apps', 'install-car-apps', 'install-ridetube', 'install-our-apps'):
            if not a.key and not a.stage:
                ap.error('--key is needed (or --stage <folder> for an offline USB)')
            ip2 = a.ip or (ip_from_key(a.key) if a.key else None) or DEFAULT_IP
            if a.action == 'install-idrive-apps':
                repo, label = IDRIVE_APPS_REPO, 'idrive-apps (robertnisipeanu)'
            elif a.action == 'install-car-apps':
                repo, label = CARAPPS_REPO, 'car-apps (IterioDev, iDrive 8)'
            elif a.action == 'install-ridetube':
                repo, label = RIDETUBE_REPO, 'RideTube (YouTube, unofficial)'
            else:
                repo, label = a.repo or BIMMHACKS_APPS_REPO, 'your apps'
                if not repo:
                    raise InstallerError('No "our apps" repository set. Pass --repo owner/repo.')
            result = do_install_apps_source(node, ip2, a.key, repo, label, stage_dir=a.stage, log=log,
                                            restart_store=a.restart)
            log(source_done_message(result, label))
            if result in ('pending', 'slow'):
                sys.exit(3)
            return
        ap.error('unknown action %s' % a.action)
    except InstallerError as e:
        print('ERROR: ' + str(e), file=sys.stderr)
        sys.exit(1)



if __name__ == '__main__':
    main()
