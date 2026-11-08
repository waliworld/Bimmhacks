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
import argparse, io, json, os, queue, re, shutil, stat, subprocess, sys, tarfile, tempfile, threading

VERSION = '1.3.0'
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROOT = os.environ.get('BIMMHACKS_APPS_ROOT') or os.path.join(REPO, 'idrive7-apps')
TOOLS = os.path.join(ROOT, 'kit', 'tools')
SDK_DIR = os.path.join(ROOT, 'oap-sdk')
DIST = os.path.join(ROOT, 'dist')
DEST = '/var/opt/mgu-custom-apps'          # where the kit lives on the head unit (same as upstream)
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

# apps that send control / write requests or are marked experimental upstream: off unless ticked
EXPERIMENTAL = {'live_adjustments': 'sends DME requests once a tune is picked',
                'zf8hp': 'read-only, verified EGS variants only'}
LANGUAGE_NAMES = {'en': 'English', 'ko': 'Korean', 'ru': 'Russian', 'zh-CN': 'Chinese (Simplified)'}
CREDIT = ('Apps: "MGU Custom Apps" by cauliflowermossgrape (MIT licence), free original at '
          'https://github.com/cauliflowermossgrape/idrive7-apps')

SDK_SOURCES = (
    # (shell command printing the bytes, kind)
    ('f=$(ls /usr/lib/oap-mainapp/preinstalled_apps/x_call@*.tgz 2>/dev/null | head -n 1); '
     '[ -n "$f" ] && cat "$f"', 'tgz'),
    ('cat /var/opt/public/oap/oap_12204/source/javascript/app.js', 'js'),
)


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


def tool_env(node):
    """environment for the build tools: node on PATH, no Docker build of the renderer"""
    env = dict(os.environ)
    env['PATH'] = os.path.dirname(node) + os.pathsep + env.get('PATH', '')
    env['MGU_NO_DOCKER'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def preflight(log):
    """-> (node, ssh) or raises with what to install"""
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
    if not ssh:
        problems.append('The OpenSSH client is missing. Windows: Settings > Apps > Optional features > '
                        'add "OpenSSH Client"')
    if not os.path.isfile(os.path.join(TOOLS, 'make_kit.py')):
        problems.append('The app sources are missing: expected them in %s' % ROOT)
    if problems:
        raise InstallerError('\n'.join(problems))
    log('Python %s, Node.js %s, ssh OK' % (sys.version.split()[0],
        subprocess.run([node, '--version'], stdout=subprocess.PIPE).stdout.decode().strip()))
    return node, ssh


def list_apps():
    """[(id, title, description, experimental_note or None)] from apps/*/app.json"""
    out = []
    apps_dir = os.path.join(ROOT, 'apps')
    for d in sorted(os.listdir(apps_dir)) if os.path.isdir(apps_dir) else []:
        p = os.path.join(apps_dir, d, 'app.json')
        if not os.path.isfile(p):
            continue
        with open(p, encoding='utf-8') as f:
            spec = json.load(f)
        out.append((d, spec.get('title', d), spec.get('description', ''), EXPERIMENTAL.get(d)))
    return out


def list_languages():
    locales = os.path.join(ROOT, 'kit', 'runtime', 'locales')
    langs = ['en']
    if os.path.isdir(locales):
        langs += sorted(n[:-5] for n in os.listdir(locales) if n.endswith('.json') and n != 'en.json')
    return langs


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
        p.wait()
        return p.returncode, '\n'.join(lines)

    def read(self, cmd):
        """bytes a command prints on the unit (b'' if it failed)"""
        p = subprocess.run(self.args() + ['export LC_ALL=C; ' + cmd], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return p.stdout if p.returncode == 0 else b''


# ----------------------------------------------------------------------------- the steps

def test_connection(car, log):
    log('Connecting to the head unit at %s ...' % car.ip)
    code, out = car.run('echo CONNECTED; id -u; '
                        '[ -d /var/opt/public/oap/oap_12201 ] && echo MGU_OAP_OK; '
                        '[ -f %s/deploy-car.sh ] && echo KIT_PRESENT; true' % DEST, echo=False)
    if 'CONNECTED' not in out:
        raise InstallerError('Could not log in to the head unit (%s).\n%s\n'
                             'Check: ignition on, the car is connected (Ethernet / Wi-Fi), the IP address, '
                             'the SSH key, and that SSH was enabled with the root tool.' % (car.ip, out.strip()))
    if '\n0\n' not in '\n' + out + '\n':
        raise InstallerError('Logged in, but not as root. The installer needs the root SSH key.')
    if 'MGU_OAP_OK' not in out:
        raise InstallerError('This does not look like an MGU (iDrive 7) head unit with the app platform.')
    log('Connected as root. MGU app platform found.')
    return 'KIT_PRESENT' in out


def fetch_sdk(car, node, log, workdir):
    """extract BMW's oap-sdk from THIS car's own x_call app into idrive7-apps/oap-sdk"""
    log("Reading BMW's app SDK from the car (it is never shipped with this installer) ...")
    app_js = None
    for cmd, kind in SDK_SOURCES:
        data = car.read(cmd)
        if not data:
            continue
        if kind == 'js':
            app_js = data
        else:
            try:
                with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as tf:
                    for m in tf.getmembers():
                        if m.isfile() and m.name.replace('\\', '/').endswith('javascript/app.js'):
                            app_js = tf.extractfile(m).read()
                            break
            except tarfile.TarError:
                app_js = None
        if app_js:
            break
    if not app_js:
        raise InstallerError('Could not read the x_call app from the head unit, so the SDK cannot be extracted.')
    src = os.path.join(workdir, 'x_call_app.js')
    with open(src, 'wb') as f:
        f.write(app_js)
    shutil.rmtree(SDK_DIR, ignore_errors=True)   # never mix one car's SDK into another's build
    p = subprocess.run([node, os.path.join(TOOLS, 'extract_sdk.js'), src, SDK_DIR],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in p.stdout.decode('utf-8', 'replace').splitlines():
        log('  sdk> ' + line)
    if p.returncode or not all(os.path.isfile(os.path.join(SDK_DIR, n)) for n in ('index.js', 'package.json')):
        raise InstallerError('Extracting the SDK failed (see the log above).')
    log('SDK extracted.')


def build_kit(node, apps, language, log):
    """-> path of the kit .tar.gz"""
    log('Building %d app(s) in %s: %s' % (len(apps), LANGUAGE_NAMES.get(language, language), ' '.join(apps)))
    if 'doom' in apps and not os.path.isfile(os.path.join(ROOT, 'apps', 'doom', 'freedoom1.wad')):
        log('  (Doom: the free game data, 24 MB, is downloaded once - needs internet)')
    cmd = [sys.executable, os.path.join(TOOLS, 'make_kit.py'), '--language', language] + list(apps)
    p = subprocess.Popen(cmd, cwd=ROOT, env=tool_env(node), stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(p.stdout.readline, b''):
        log('  build> ' + raw.decode('utf-8', 'replace').rstrip('\r\n'))
    if p.wait():
        raise InstallerError('Building the apps failed (see the log above).')
    name = 'mgu-custom-apps' + ('' if language == 'en' else '-' + language)
    archive = os.path.join(DIST, name + '.tar.gz')
    if not os.path.isfile(archive):
        raise InstallerError('The build finished but %s is missing.' % archive)
    log('Kit ready (%.1f MB).' % (os.path.getsize(archive) / 1e6))
    return archive


def push_kit(car, archive, log):
    log('Copying the kit to the head unit ...')
    with open(archive, 'rb') as f:
        data = f.read()
    # the archive's top folder is mgu-custom-apps -> /var/opt/mgu-custom-apps; apps/ is replaced, not
    # merged (old .mgu files left there would be installed too)
    code, out = car.run("mkdir -p '%s' && rm -rf '%s/apps' && tar xzf - -C /var/opt && cd '%s' && "
                        "md5sum -c KIT_SUMS >/dev/null && echo KIT_VERIFIED" % (DEST, DEST, DEST),
                        stdin_data=data, echo=False)
    if code or 'KIT_VERIFIED' not in out:
        raise InstallerError('Copying the kit failed:\n' + out.strip())
    log('Kit copied and verified on the car.')


def remote(car, command, env=None, log=print):
    envs = ' '.join('%s=%s' % (k, v) for k, v in (env or {}).items())
    return car.run("cd '%s' && %s sh deploy-car.sh %s" % (DEST, envs, command))


def cleanup(log):
    """the SDK and the .mgu files contain BMW's code: don't leave them on the shop PC"""
    shutil.rmtree(SDK_DIR, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    log("Removed the local copy of BMW's SDK and the build output.")


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
    p = subprocess.Popen([sys.executable, '-m', 'pip', 'install', '--user', 'cryptography>=41.0'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(p.stdout.readline, b''):
        log('  pip> ' + raw.decode('utf-8', 'replace').rstrip('\r\n'))
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
    p = subprocess.Popen(argv, cwd=SSHROOT_DIR, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(p.stdout.readline, b''):
        line = raw.decode('utf-8', 'replace').rstrip('\r\n')
        if 'DeprecationWarning' in line or 'utcnow()' in line:
            continue
        log('  root> ' + line)
    return p.wait()


def do_root(action, log, vin=None, ip=None, gateway='auto', arch='id7'):
    """Root-access actions. Returns the path of the SSH key when one was written, else None."""
    if IS_WIN:
        log('Note: on Windows, run this installer as Administrator so the ENET link-local address is used.')
    flashing = action in ('flash', 'fix-key')
    if flashing and not (vin and re.match(r'^[A-HJ-NPR-Z0-9]{17}$', vin.upper())):
        raise InstallerError('A valid 17-character VIN is needed to flash. Use "Detect VIN", or type it in.')
    if flashing:
        log('WARNING: flashing reprograms the head unit and wipes its coding and BMW Digital Key pairings.')
        log('Back up the coding first (BimmerCode / E-Sys). This is a one-time step per car.')
    names = {'status': 'Reading the head unit state (read-only) ...',
             'flash': 'Flashing the debug image and enabling root SSH (this reboots the unit; a few minutes) ...',
             'enable': 'Re-opening SSH on an already-flashed unit (no flashing) ...',
             'fix-key': 'Re-flashing to install our root key (repair for refused logins) ...',
             'disable': 'Reverting persistence and stopping debug/SSH ...'}
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
    code = run_ssh_root('status', grab, ip=ip, gateway=gateway, arch=arch)
    for m in lines:
        v = re.search(r'\b([A-HJ-NPR-Z0-9]{17})\b', m)
        if v:
            return v.group(1)
    return None


# ------------------------------------------------------- coding snapshot (before flashing)

CODING_BACKUP_DIR = os.path.join(REPO, 'coding-backups')


def do_backup_coding(log, vin=None, ip=None, gateway='auto', arch='id7', out_dir=None):
    """Save a diagnostic snapshot of the car's CAFD versions, VIN, IP and faults. -> file path.

    NOT a restorable coding backup (that needs BimmerCode / E-Sys); it records exactly what is on
    the car so a restore is reliable. Run it before any flash."""
    ensure_cryptography(log)
    out_dir = out_dir or CODING_BACKUP_DIR
    backup = os.path.join(HERE, 'coding_backup.py')
    if not os.path.isfile(backup):
        raise InstallerError('coding_backup.py is missing next to the installer.')
    log('Reading the head unit over ENET for a coding snapshot (read-only) ...')
    argv = [sys.executable, backup, '--out', out_dir]
    if gateway and gateway != 'auto':
        argv += ['--gateway', gateway]
    if ip:
        argv += ['--mgu-ip', ip]
    if arch and arch != 'id7':
        argv += ['--arch', arch]
    if vin:
        argv += ['--vin', vin]
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    p = subprocess.Popen(argv, cwd=SSHROOT_DIR, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    saved = None
    for raw in iter(p.stdout.readline, b''):
        line = raw.decode('utf-8', 'replace').rstrip('\r\n')
        if 'DeprecationWarning' in line or 'utcnow()' in line:
            continue
        log('  coding> ' + line)
        if line.strip().endswith('.txt'):
            saved = line.strip()
    if p.wait():
        raise InstallerError('The coding snapshot could not be read. Is the ENET cable in and the car awake?')
    log('Coding snapshot saved in: ' + out_dir)
    log('REMINDER: a snapshot is not a restore. Also back up the coding with BimmerCode or E-Sys.')
    return saved or out_dir


# -------------------------------------------------------------- stage C: the App Store (mgu-store)

def do_store_install(node, ip, key, no_renderer=False, no_uplink=False, log=print):
    """Install the on-car MGU App Store with the bundled mgu-install.js."""
    if not os.path.isfile(STORE_JS):
        raise InstallerError('The App Store installer is missing (expected it in %s).' % STORE_DIR)
    if not key or not os.path.isfile(key):
        raise InstallerError('The car SSH key is needed. Do the root-access step first, or pick the key.')
    log('Installing the MGU App Store on the head unit (needs internet at the car) ...')
    argv = [node, STORE_JS, 'first-install', '--identity', key, '--no-restart-prompt']
    if ip:
        argv += ['--host', ip]
    if no_renderer:
        argv.append('--no-renderer')
    if no_uplink:
        argv.append('--no-uplink')
    p = subprocess.Popen(argv, cwd=STORE_DIR, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(p.stdout.readline, b''):
        line = raw.decode('utf-8', 'replace').rstrip('\r\n')
        log('  store> ' + line)
    code = p.wait()
    if code == 2:
        raise InstallerError('The App Store installer refused the request or the usage was wrong (see above).')
    if code:
        raise InstallerError('The App Store install failed (exit %d; see above).' % code)
    log('App Store installed. Restart the head unit (Settings on the car, or power-cycle) to see its tile.')
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
    p = subprocess.Popen(argv, cwd=STORE_DIR, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(p.stdout.readline, b''):
        log('  store> ' + raw.decode('utf-8', 'replace').rstrip('\r\n'))
    code = p.wait()
    if code == 2:
        raise InstallerError('Staging was refused or the usage was wrong (see above).')
    if code:
        raise InstallerError('Staging the apps failed (exit %d; see above).' % code)
    repo = os.path.join(dest, 'mgu-repo')
    log('Staged. Put this stick in the car and open Apps > Store > USB to install.')
    return repo


def do_install_apps_source(node, ip, key, repo, label, stage_dir=None, log=print):
    """Make an extra app source available on the car. These apps are designed to be installed
    through the on-car Store, so there are two honest paths:

      - stage_dir given  -> write the repo to a USB folder (works offline); the car installs it
                            from Apps > Store > USB. No car internet needed.
      - no stage_dir     -> ensure the Store is installed so the source is available on the car
                            (needs car internet). idrive-apps is a built-in source; a custom repo
                            must be added once on the car (Settings > Add source) or staged to USB.
    """
    if not re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', repo or ''):
        raise InstallerError('The source must be a GitHub owner/repo, e.g. robertnisipeanu/idrive-apps.')
    if stage_dir:
        log('Staging %s to %s for offline install on the car ...' % (label, stage_dir))
        do_usb_repo(node, stage_dir, ['github:' + repo], log=log)
        log('%s staged. Put the stick in the car and open Apps > Store > USB to install.' % label)
        return
    if not key or not os.path.isfile(key):
        raise InstallerError('The car SSH key is needed (or stage to a USB folder instead).')
    log('Ensuring the on-car Store is present so %s is available ...' % label)
    do_store_install(node, ip, key, log=log)
    if repo == IDRIVE_APPS_REPO:
        log('%s is a built-in Store source: on the car open Apps > Store, find the apps and Install.' % label)
    else:
        log('%s: add the source on the car once (Settings > Add source > %s), then install it there,'
            % (label, repo))
        log('or re-run this with a USB stage folder to deliver it without on-car typing.')


def do_install(ssh, node, ip, key, apps, language, reboot=True, controller_gate=True, keep_build=False, log=print):
    if not apps:
        raise InstallerError('Pick at least one app.')
    car = Car(ssh, ip, key, log=log)
    work = tempfile.mkdtemp(prefix='bimmhacks-work-')
    try:
        test_connection(car, log)
        fetch_sdk(car, node, log, work)
        archive = build_kit(node, apps, language, log)
        push_kit(car, archive, log)
        log('Checking the head unit (read-only) ...')
        code, _ = remote(car, 'check', log=log)
        if code:
            raise InstallerError('The check found a problem (see above). Nothing was changed on the car.')
        log('Installing. Keep the ignition on. The screen reloads; this takes a few minutes ...')
        env = {}
        if not reboot:
            env['NO_REBOOT'] = '1'
        if not controller_gate:
            env['NO_INPUTGATE'] = '1'
        code, out = remote(car, 'install', env, log=log)
        if code and 'rebooting the head unit' in out and 'done.' in out:
            code = 0      # the unit rebooted itself at the end and dropped the connection: expected
        if code:
            raise InstallerError('The install stopped (see above). To undo, use "Restore to stock".')
        if 'rebooting the head unit' in out:
            log('The head unit is rebooting (screen black about a minute), then the apps are under Apps.')
        else:
            log('Done. After 1-2 minutes the new tiles are under Apps on the iDrive.')
        log(CREDIT)
    finally:
        car.close()
        shutil.rmtree(work, ignore_errors=True)
        if not keep_build:
            cleanup(log)


def do_simple(ssh, ip, key, what, log=print):
    car = Car(ssh, ip, key, log=log)
    try:
        present = test_connection(car, log)
        if what == 'test':
            log('The kit is already on this car (use Status to see what is installed).' if present
                else 'Nothing installed by this tool yet.')
            return
        if not present:
            raise InstallerError('Nothing installed by this tool on this car (no %s/deploy-car.sh).' % DEST)
        if what == 'status':
            remote(car, 'status', log=log)
        elif what == 'restore':
            log('Restoring the stock backup: removes the custom apps, puts every changed file back ...')
            code, _ = remote(car, 'restore', {'YES': '1'}, log=log)
            if code:
                raise InstallerError('Restore reported a problem (see above).')
            log('Back to stock.')
    finally:
        car.close()


# ----------------------------------------------------------------------------- window

# Visual identity (kept deliberately restrained: one ink, one accent, one surface).
INK = '#12151c'       # near-black header / console
SURFACE = '#f4f5f7'   # window body
PANEL = '#ffffff'     # cards
LINE = '#d9dce2'      # hairlines
ACCENT = '#0b63d6'    # single BMW-ish blue accent
ACCENT_DK = '#0a2a6b'
MUTED = '#6b7280'
GOOD = '#1f9d55'
WARN = '#b42318'

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


def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext

    cfg = load_config()
    apps = list_apps()
    langs = list_languages()
    q = queue.Queue()
    busy = {'on': False}
    buttons = []

    root = tk.Tk()
    root.title('Bimmhacks  -  MGU Flash & App Studio  (iDrive 7 & 8)')
    root.minsize(900, 760)
    root.configure(bg=SURFACE)

    st = ttk.Style()
    try:
        st.theme_use('clam')
    except tk.TclError:
        pass
    base_font = ('Segoe UI', 10) if IS_WIN else ('DejaVu Sans', 10)
    st.configure('.', background=SURFACE, foreground=INK, font=base_font)
    st.configure('TFrame', background=SURFACE)
    st.configure('Card.TFrame', background=PANEL, relief='flat')
    st.configure('TLabel', background=SURFACE, foreground=INK)
    st.configure('Card.TLabel', background=PANEL, foreground=INK)
    st.configure('Muted.TLabel', background=SURFACE, foreground=MUTED)
    st.configure('CardMuted.TLabel', background=PANEL, foreground=MUTED)
    st.configure('Warn.TLabel', background=PANEL, foreground=WARN)
    st.configure('Eyebrow.TLabel', background=PANEL, foreground=ACCENT, font=(base_font[0], 8, 'bold'))
    st.configure('H.TLabel', background=PANEL, foreground=INK, font=(base_font[0], 13, 'bold'))
    st.configure('TCheckbutton', background=PANEL, foreground=INK)
    st.map('TCheckbutton', background=[('active', PANEL)])
    st.configure('TEntry', fieldbackground='#ffffff', bordercolor=LINE)
    st.configure('TCombobox', fieldbackground='#ffffff')
    # buttons
    st.configure('TButton', background='#e8eaee', foreground=INK, borderwidth=0, focusthickness=0,
                 padding=(12, 7), font=(base_font[0], 10))
    st.map('TButton', background=[('active', '#dcdfe5'), ('disabled', '#eef0f3')],
           foreground=[('disabled', '#aab0ba')])
    st.configure('Accent.TButton', background=ACCENT, foreground='#ffffff', padding=(16, 9),
                 font=(base_font[0], 10, 'bold'))
    st.map('Accent.TButton', background=[('active', ACCENT_DK), ('disabled', '#9bb6e4')])
    st.configure('Danger.TButton', background='#fbeceb', foreground=WARN, padding=(14, 8),
                 font=(base_font[0], 10, 'bold'))
    st.map('Danger.TButton', background=[('active', '#f6d9d6'), ('disabled', '#f3eceb')])
    # notebook as a quiet left-to-right rail
    st.configure('TNotebook', background=SURFACE, borderwidth=0, tabmargins=(0, 6, 0, 0))
    st.configure('TNotebook.Tab', background=SURFACE, foreground=MUTED, padding=(16, 9),
                 borderwidth=0, font=(base_font[0], 10, 'bold'))
    st.map('TNotebook.Tab', background=[('selected', PANEL)], foreground=[('selected', ACCENT)],
           expand=[('selected', (0, 0, 0, 0))])

    # ---- header ---------------------------------------------------------------------------------
    header = tk.Frame(root, bg=INK)
    header.pack(fill='x')
    inner = tk.Frame(header, bg=INK)
    inner.pack(fill='x', padx=20, pady=14)
    tk.Label(inner, text='BIMMHACKS', bg=INK, fg='#ffffff',
             font=(base_font[0], 19, 'bold')).pack(side='left')
    tk.Label(inner, text='BMW MGU / iDrive 7 & 8  -  flash, root & app studio', bg=INK, fg='#aeb6c2',
             font=(base_font[0], 10)).pack(side='left', padx=(12, 0), pady=(7, 0))
    tk.Label(inner, text='by Mr. Wali', bg=INK, fg=ACCENT,
             font=(base_font[0], 10, 'bold')).pack(side='right', pady=(6, 0))
    tk.Frame(root, bg=ACCENT, height=3).pack(fill='x')

    body = tk.Frame(root, bg=SURFACE)
    body.pack(fill='both', expand=True)
    pad = {'padx': 8, 'pady': 5}

    # ---- shared connection card -----------------------------------------------------------------
    conn = tk.Frame(body, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
    conn.pack(fill='x', padx=16, pady=(14, 8))
    ttk.Label(conn, text='CAR CONNECTION', style='Eyebrow.TLabel').grid(row=0, column=0, columnspan=4,
              sticky='w', padx=12, pady=(10, 2))
    vin_var = tk.StringVar(value=cfg.get('vin', ''))
    key_var = tk.StringVar(value=cfg.get('key', ''))
    ip_var = tk.StringVar(value=cfg.get('ip', DEFAULT_IP))
    arch_var = tk.StringVar(value=cfg.get('arch', 'id7'))

    def cl(txt, r, c):
        ttk.Label(conn, text=txt, style='CardMuted.TLabel').grid(row=r, column=c, sticky='w', padx=12, pady=3)
    cl('VIN', 1, 0)
    ttk.Entry(conn, textvariable=vin_var, width=22).grid(row=1, column=1, sticky='w', padx=4, pady=3)
    cl('Variant', 1, 2)
    arch_box = ttk.Combobox(conn, state='readonly', width=10, values=list(ARCHES))
    arch_box.current(ARCHES.index(arch_var.get()) if arch_var.get() in ARCHES else 0)
    arch_box.grid(row=1, column=3, sticky='w', padx=4, pady=3)
    cl('Root SSH key', 2, 0)
    ttk.Entry(conn, textvariable=key_var).grid(row=2, column=1, columnspan=2, sticky='we', padx=4, pady=3)

    def browse():
        start_dir = SSHROOT_ENROLL if os.path.isdir(SSHROOT_ENROLL) else REPO
        p = filedialog.askopenfilename(title='The car SSH key (id_rsa)', initialdir=start_dir)
        if p:
            key_var.set(p)
            got = ip_from_key(p)
            if got:
                ip_var.set(got)
    ttk.Button(conn, text='Browse', command=browse).grid(row=2, column=3, sticky='we', padx=(4, 12), pady=3)
    cl('Head unit IP', 3, 0)
    ttk.Entry(conn, textvariable=ip_var, width=22).grid(row=3, column=1, sticky='w', padx=4, pady=(3, 10))
    ttk.Label(conn, text='ENET link-local default 169.254.36.47', style='CardMuted.TLabel').grid(
        row=3, column=2, columnspan=2, sticky='w', padx=4, pady=(3, 10))
    conn.columnconfigure(1, weight=1)

    nb = ttk.Notebook(body)
    nb.pack(fill='both', expand=True, padx=16, pady=(0, 6))

    def card(parent):
        c = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        c.pack(fill='both', expand=True, padx=2, pady=2)
        return c

    def eyebrow(parent, text):
        ttk.Label(parent, text=text, style='Eyebrow.TLabel').pack(anchor='w', padx=16, pady=(14, 0))

    def title(parent, text):
        ttk.Label(parent, text=text, style='H.TLabel').pack(anchor='w', padx=16, pady=(1, 4))

    def para(parent, text):
        ttk.Label(parent, text=text, style='Card.TLabel', justify='left', wraplength=800).pack(
            anchor='w', padx=16, pady=2)

    # ---- helpers --------------------------------------------------------------------------------
    def log(msg):
        q.put(('log', msg))

    def save():
        picked = [a for a, v in app_vars.items() if v.get()]
        save_config(dict(load_config(),
                         vin=vin_var.get().strip().upper(), key=key_var.get().strip().strip('"'),
                         ip=ip_var.get().strip(),
                         arch=ARCHES[arch_box.current()] if arch_box.current() >= 0 else 'id7',
                         apps=picked,
                         language=langs[lang_box.current()] if lang_box.current() >= 0 else 'en',
                         reboot=reboot_var.get()))

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
        progress.start(12)
        for b in buttons:
            b.state(['disabled'])

        def work():
            try:
                work_fn()
                q.put(('done', (True, done_msg)))
            except InstallerError as e:
                log('ERROR: ' + str(e))
                q.put(('done', (False, str(e))))
            except Exception as e:
                log('ERROR: %s: %s' % (type(e).__name__, e))
                q.put(('done', (False, 'Unexpected error: %s' % e)))
        threading.Thread(target=work, daemon=True).start()

    # ========================= Tab 1: Root access ===============================================
    t_root = ttk.Frame(nb)
    nb.add(t_root, text='Root access')
    c1 = card(t_root)
    eyebrow(c1, 'STEP 1  -  ONCE PER CAR')
    title(c1, 'Back up, then enable root over ENET')
    para(c1, 'Connect a wired ENET cable, ignition on. Back up the coding first, then flash. Already '
             'rooted and have the key? Skip to the app tabs and pick the key with Browse.')
    ttk.Label(c1, text='Flashing reprograms the head unit and wipes its coding and BMW Digital Key '
              'pairings. Back up first; re-pair keys after. Windows: run as Administrator.',
              style='Warn.TLabel', justify='left', wraplength=800).pack(anchor='w', padx=16, pady=(4, 2))

    backup_done = tk.BooleanVar(value=False)
    gate_row = tk.Frame(c1, bg=PANEL)
    gate_row.pack(fill='x', padx=16, pady=(8, 2))
    ttk.Checkbutton(gate_row, variable=backup_done,
                    text='I have saved a restorable coding backup (BimmerCode / E-Sys)').pack(side='left')

    def on_backup():
        vin, _, ip, arch = conn_values()
        def w():
            path = do_backup_coding(log, vin=vin or None, ip=ip or None, arch=arch)
            backup_done.set(True)
            log('Snapshot captured. Keep it with your BimmerCode/E-Sys backup: ' + str(path))
        start(w, 'Coding snapshot saved - see the log. Also keep a BimmerCode/E-Sys backup.')

    def on_detect_vin():
        _, _, ip, arch = conn_values()
        def w():
            v = detect_vin(log, ip=ip or None, arch=arch)
            if v:
                vin_var.set(v)
                log('Detected VIN: ' + v)
            else:
                log('VIN not detected. Is the ENET cable in and the car awake?')
        start(w, 'VIN read - see the log.')

    def on_root(action, confirm=None, need_backup=False):
        vin, _, ip, arch = conn_values()
        if need_backup and not backup_done.get():
            messagebox.showwarning('Back up first', 'Save a coding backup before flashing.\n\nClick '
                                   '"Back up coding (snapshot)" and take a BimmerCode / E-Sys backup, '
                                   'then tick the confirmation box.')
            return
        if confirm and not messagebox.askyesno('Root access', confirm):
            return
        def w():
            key = do_root(action, log, vin=vin or None, ip=ip or None, arch=arch)
            if key:
                key_var.set(key)
                got = ip_from_key(key)
                if got:
                    ip_var.set(got)
        start(w, 'Root-access step finished - see the log.')

    rb = tk.Frame(c1, bg=PANEL)
    rb.pack(fill='x', padx=14, pady=(8, 6))
    ttk.Button(rb, text='Detect VIN', command=on_detect_vin).pack(side='left', padx=4)
    ttk.Button(rb, text='Back up coding (snapshot)', command=on_backup).pack(side='left', padx=4)
    ttk.Button(rb, text='Check state', command=lambda: on_root('status')).pack(side='left', padx=4)

    rb2 = tk.Frame(c1, bg=PANEL)
    rb2.pack(fill='x', padx=14, pady=(0, 14))
    ttk.Button(rb2, text='Flash + Enable SSH (once)', style='Accent.TButton',
               command=lambda: on_root('flash',
                   'Flash the debug image and enable root SSH?\n\nThis REPROGRAMS the head unit and '
                   'WIPES its coding and Digital Key pairings. Do this only once per car.',
                   need_backup=True)).pack(side='left', padx=4)
    ttk.Button(rb2, text='Enable (repair)', command=lambda: on_root('enable')).pack(side='left', padx=4)
    ttk.Button(rb2, text='Fix refusing key', command=lambda: on_root('fix-key',
               'Re-flash to install our root key? Use only if SSH opens but login is refused. It '
               're-flashes (wipes coding again).', need_backup=True)).pack(side='left', padx=4)
    ttk.Button(rb2, text='Disable', style='Danger.TButton', command=lambda: on_root('disable',
               'Revert persistence and stop debug/SSH on this car?')).pack(side='left', padx=4)

    # ========================= Tab 2: Apps (idrive7-apps) =======================================
    t_apps = ttk.Frame(nb)
    nb.add(t_apps, text='Apps')
    c2 = card(t_apps)
    eyebrow(c2, 'STEP 2  -  THE CUSTOM APPS')
    title(c2, 'Install the MGU apps on the car')
    sel = tk.Frame(c2, bg=PANEL)
    sel.pack(fill='x', padx=12, pady=(2, 2))
    chosen = set(cfg.get('apps') or [a for a, _, _, exp in apps if not exp])
    app_vars = {}
    for i, (aid, t, desc, exp) in enumerate(apps):
        v = tk.BooleanVar(value=aid in chosen)
        app_vars[aid] = v
        ttk.Checkbutton(sel, text=t + ('  (experimental)' if exp else ''), variable=v).grid(
            row=i // 2, column=i % 2, sticky='w', padx=6, pady=1)
    r = (len(apps) + 1) // 2
    lf = tk.Frame(c2, bg=PANEL)
    lf.pack(fill='x', padx=16, pady=(6, 2))
    ttk.Label(lf, text='Language', style='CardMuted.TLabel').pack(side='left')
    lang_box = ttk.Combobox(lf, state='readonly', width=22,
                            values=['%s - %s' % (l, LANGUAGE_NAMES.get(l, l)) for l in langs])
    lang_box.current(langs.index(cfg.get('language', 'en')) if cfg.get('language', 'en') in langs else 0)
    lang_box.pack(side='left', padx=8)

    def set_all(on):
        for aid, v in app_vars.items():
            v.set(on and aid not in EXPERIMENTAL)
    ttk.Button(lf, text='Recommended', command=lambda: set_all(True)).pack(side='left', padx=6)
    ttk.Button(lf, text='None', command=lambda: set_all(False)).pack(side='left')
    reboot_var = tk.BooleanVar(value=cfg.get('reboot', True))
    ttk.Checkbutton(c2, text='Reboot the head unit at the end if needed (for the Doom controller gate)',
                    variable=reboot_var).pack(anchor='w', padx=16, pady=(4, 2))

    ab = tk.Frame(c2, bg=PANEL)
    ab.pack(fill='x', padx=14, pady=(8, 14))

    def on_test():
        _, key, ip, _ = conn_values()
        start(lambda: do_simple(need_ssh(), ip, key, 'test', log), 'Connection OK.')

    def on_install():
        _, key, ip, _ = conn_values()
        picked = [a for a, v in app_vars.items() if v.get()]
        lang = langs[lang_box.current()] if lang_box.current() >= 0 else 'en'
        if not picked:
            messagebox.showwarning('Bimmhacks', 'Pick at least one app.')
            return
        if any(a in EXPERIMENTAL for a in picked) and not messagebox.askyesno(
                'Experimental apps', 'You picked experimental apps (Engine Config / Transmission '
                'Config). Engine Config can send control requests to the DME once a tune is selected '
                'in its settings.\n\nInstall them anyway?'):
            return
        if not messagebox.askokcancel('Install', 'Install %d app(s) on the car at %s?\n\nEverything '
                                      'changed is backed up first and can be undone with Restore to '
                                      'stock.' % (len(picked), ip)):
            return
        reboot = reboot_var.get()
        def w():
            node, ssh = preflight(log)
            do_install(ssh, node, ip, key, picked, lang, reboot=reboot, log=log)
        start(w, 'Install finished. The apps appear under Apps on the iDrive.')

    def on_status():
        _, key, ip, _ = conn_values()
        start(lambda: do_simple(need_ssh(), ip, key, 'status', log), 'Status read - see the log.')

    def on_restore():
        _, key, ip, _ = conn_values()
        if not messagebox.askyesno('Restore to stock', 'Remove ALL custom apps and put every changed '
                                   'file back exactly as it was before the first install?'):
            return
        start(lambda: do_simple(need_ssh(), ip, key, 'restore', log), 'The car is back to stock.')

    ttk.Button(ab, text='Test connection', command=on_test).pack(side='left', padx=4)
    ttk.Button(ab, text='Install / Update apps', style='Accent.TButton', command=on_install).pack(side='left', padx=4)
    ttk.Button(ab, text='Status', command=on_status).pack(side='left', padx=4)
    ttk.Button(ab, text='Restore to stock', style='Danger.TButton', command=on_restore).pack(side='left', padx=4)

    # ========================= Tab 3: App Store =================================================
    t_store = ttk.Frame(nb)
    nb.add(t_store, text='App Store')
    c3 = card(t_store)
    eyebrow(c3, 'STEP 3  -  ON-CAR STORE')
    title(c3, 'Install the MGU App Store')
    para(c3, 'The on-car Store lets the client browse, install and update apps from the car screen '
             '(Apps > Store). It needs internet at the car (USB Ethernet, LTE stick, tethered phone '
             'or Wi-Fi). Restart the head unit once when it finishes.')
    no_renderer_var = tk.BooleanVar(value=cfg.get('no_renderer', False))
    no_uplink_var = tk.BooleanVar(value=cfg.get('no_uplink', False))
    ttk.Checkbutton(c3, text='Leave out the renderer (the Store draws in software)',
                    variable=no_renderer_var).pack(anchor='w', padx=16, pady=(4, 0))
    ttk.Checkbutton(c3, text='Leave out the internet uplink component',
                    variable=no_uplink_var).pack(anchor='w', padx=16)
    sbf = tk.Frame(c3, bg=PANEL)
    sbf.pack(fill='x', padx=14, pady=(10, 14))

    def on_store():
        _, key, ip, _ = conn_values()
        save_config(dict(load_config(), no_renderer=no_renderer_var.get(), no_uplink=no_uplink_var.get()))
        if not messagebox.askokcancel('Install App Store', 'Install the MGU App Store on the car at '
                                      '%s?\n\nThe car needs internet for this.' % ip):
            return
        def w():
            do_store_install(need_node(), ip, key, no_renderer=no_renderer_var.get(),
                             no_uplink=no_uplink_var.get(), log=log)
        start(w, 'App Store installed. Restart the head unit to see its tile.')
    ttk.Button(sbf, text='Install the App Store', style='Accent.TButton', command=on_store).pack(side='left', padx=4)

    ttk.Label(c3, text='POPULAR COMMUNITY SOURCE', style='Eyebrow.TLabel').pack(anchor='w', padx=16, pady=(14, 0))
    para(c3, 'RideTube - an unofficial YouTube client for the MGU (GPL-3, by JustAnotherPU). It plays '
             'on the car over the car\'s internet; the picture shows only while parked and drops to a '
             'podcast view (sound only) while driving. iDrive 7. Install the Store first.')
    stage_rt = tk.StringVar(value=cfg.get('stage_rt', ''))
    rrow = tk.Frame(c3, bg=PANEL)
    rrow.pack(fill='x', padx=16, pady=(4, 2))
    ttk.Label(rrow, text='Stage to (optional USB / folder)', style='CardMuted.TLabel').pack(side='left')
    ttk.Entry(rrow, textvariable=stage_rt, width=34).pack(side='left', padx=8)
    ttk.Button(rrow, text='Choose', command=lambda: pick_stage(stage_rt)).pack(side='left')
    rbf = tk.Frame(c3, bg=PANEL)
    rbf.pack(fill='x', padx=14, pady=(8, 14))

    def on_ridetube():
        _, key, ip, _ = conn_values()
        save_config(dict(load_config(), stage_rt=stage_rt.get().strip()))
        if not messagebox.askokcancel('RideTube', 'Make the RideTube YouTube client available on the car '
                                      'at %s?\n\nUnofficial, not affiliated with YouTube/Google. Video is '
                                      'parked-only. Installs the Store if needed.' % ip):
            return
        def w():
            do_install_apps_source(need_node(), ip, key, RIDETUBE_REPO, 'RideTube (YouTube, unofficial)',
                                   stage_dir=stage_rt.get().strip() or None, log=log)
        start(w, 'RideTube ready - add it from the car Store (JustAnotherPU/ridetube:releases).')
    ttk.Button(rbf, text='Add RideTube (YouTube)', command=on_ridetube).pack(side='left', padx=4)
    ttk.Button(rbf, text='Open repository', command=lambda: _open_url(
        'https://github.com/JustAnotherPU/ridetube')).pack(side='left', padx=4)

    # ========================= Tab 4: idrive-apps ===============================================
    t_idr = ttk.Frame(nb)
    nb.add(t_idr, text='idrive-apps')
    c4 = card(t_idr)
    eyebrow(c4, 'MORE APPS  -  ROBERTNISIPEANU/IDRIVE-APPS')
    title(c4, 'Cluster & HUD maps (Waze on cluster)')
    para(c4, 'The CarPlay / Android Auto map on the instrument cluster and the HUD, plus the cluster '
             'renderer it needs. These install through the on-car Store (install the App Store first); '
             'idrive-apps is a built-in Store source, so it appears there automatically.')
    para(c4, 'Offline car? Stage the apps to a USB stick here, then install from Apps > Store > USB.')
    stage_idr = tk.StringVar(value=cfg.get('stage_idr', ''))
    srow = tk.Frame(c4, bg=PANEL)
    srow.pack(fill='x', padx=16, pady=(6, 2))
    ttk.Label(srow, text='Stage to (optional USB / folder)', style='CardMuted.TLabel').pack(side='left')
    ttk.Entry(srow, textvariable=stage_idr, width=36).pack(side='left', padx=8)

    def pick_stage(var):
        d = filedialog.askdirectory(title='Folder or USB stick to stage the apps onto')
        if d:
            var.set(d)
    ttk.Button(srow, text='Choose', command=lambda: pick_stage(stage_idr)).pack(side='left')
    ib = tk.Frame(c4, bg=PANEL)
    ib.pack(fill='x', padx=14, pady=(10, 14))

    def on_idrive():
        _, key, ip, _ = conn_values()
        save_config(dict(load_config(), stage_idr=stage_idr.get().strip()))
        if not messagebox.askokcancel('idrive-apps', 'Make idrive-apps available on the car at %s?\n\n'
                                      'Installs the Store if needed; needs internet on this PC.' % ip):
            return
        def w():
            do_install_apps_source(need_node(), ip, key, IDRIVE_APPS_REPO, 'idrive-apps (robertnisipeanu)',
                                   stage_dir=stage_idr.get().strip() or None, log=log)
        start(w, 'idrive-apps ready - install Waze on cluster from the car Store.')
    ttk.Button(ib, text='Make idrive-apps available', style='Accent.TButton',
               command=on_idrive).pack(side='left', padx=4)
    ttk.Button(ib, text='Open repository', command=lambda: _open_url(
        'https://github.com/robertnisipeanu/idrive-apps')).pack(side='left', padx=4)

    # ========================= Tab 5: car-apps (iDrive 8) =======================================
    t_car = ttk.Frame(nb)
    nb.add(t_car, text='car-apps (iD8)')
    cC = card(t_car)
    eyebrow(cC, 'MORE APPS  -  ITERIODEV/CAR-APPS  -  IDRIVE 8')
    title(cC, 'IterioDev car-apps (iDrive 8 focused)')
    para(cC, 'Car Data, Drag Timer, Drive Recorder, eDrive, Fault Codes, Live Adjustments, Overlays, '
             'Service and Tailscale by IterioDev. Most are iDrive 8 only (Drive Recorder also runs on '
             'iDrive 7). They install through the on-car Store - car-apps is a built-in source, so it '
             'appears there automatically; set the Variant above to id8 for an iDrive 8 car.')
    para(cC, 'Licence: PolyForm Strict 1.0.0 (IterioDev). Offline car? Stage to a USB stick, then '
             'install from Apps > Store > USB.')
    stage_car = tk.StringVar(value=cfg.get('stage_car', ''))
    crow = tk.Frame(cC, bg=PANEL)
    crow.pack(fill='x', padx=16, pady=(6, 2))
    ttk.Label(crow, text='Stage to (optional USB / folder)', style='CardMuted.TLabel').pack(side='left')
    ttk.Entry(crow, textvariable=stage_car, width=36).pack(side='left', padx=8)
    ttk.Button(crow, text='Choose', command=lambda: pick_stage(stage_car)).pack(side='left')
    cb2 = tk.Frame(cC, bg=PANEL)
    cb2.pack(fill='x', padx=14, pady=(10, 14))

    def on_carapps():
        _, key, ip, _ = conn_values()
        save_config(dict(load_config(), stage_car=stage_car.get().strip()))
        if not messagebox.askokcancel('car-apps', 'Make IterioDev car-apps available on the car at %s?\n\n'
                                      'Mostly iDrive 8 apps. Installs the Store if needed; needs internet '
                                      'on this PC.' % ip):
            return
        def w():
            do_install_apps_source(need_node(), ip, key, CARAPPS_REPO, 'car-apps (IterioDev, iDrive 8)',
                                   stage_dir=stage_car.get().strip() or None, log=log)
        start(w, 'car-apps ready - install them from the car Store.')
    ttk.Button(cb2, text='Make car-apps available', style='Accent.TButton',
               command=on_carapps).pack(side='left', padx=4)
    ttk.Button(cb2, text='Open repository', command=lambda: _open_url(
        'https://github.com/IterioDev/car-apps')).pack(side='left', padx=4)

    # ========================= Tab 6: Bimmhacks Apps (ours) =====================================
    t_our = ttk.Frame(nb)
    nb.add(t_our, text='Bimmhacks Apps')
    c5 = card(t_our)
    eyebrow(c5, 'IN-HOUSE  -  DEVELOPED BY US')
    title(c5, 'Bimmhacks Apps')
    para(c5, 'Apps developed in-house by the Bimmhacks team (Mr. Wali). A starter app, '
             '"Bimmhacks Hello", ships in the Apps tab already - copy it to build your own. Publish '
             'your apps to the GitHub repo below and they install through the on-car Store, the same '
             'way the other sources do. The guide below walks through building and publishing an app.')
    our_repo = tk.StringVar(value=cfg.get('our_repo', BIMMHACKS_APPS_REPO))
    orow = tk.Frame(c5, bg=PANEL)
    orow.pack(fill='x', padx=16, pady=(6, 2))
    ttk.Label(orow, text='Source repo (owner/repo)', style='CardMuted.TLabel').pack(side='left')
    ttk.Entry(orow, textvariable=our_repo, width=30).pack(side='left', padx=8)
    stage_our = tk.StringVar(value=cfg.get('stage_our', ''))
    orow2 = tk.Frame(c5, bg=PANEL)
    orow2.pack(fill='x', padx=16, pady=(2, 2))
    ttk.Label(orow2, text='Stage to (optional USB / folder)', style='CardMuted.TLabel').pack(side='left')
    ttk.Entry(orow2, textvariable=stage_our, width=36).pack(side='left', padx=8)
    ttk.Button(orow2, text='Choose', command=lambda: pick_stage(stage_our)).pack(side='left')
    obf = tk.Frame(c5, bg=PANEL)
    obf.pack(fill='x', padx=14, pady=(10, 14))

    def on_our():
        _, key, ip, _ = conn_values()
        repo = our_repo.get().strip()
        save_config(dict(load_config(), our_repo=repo, stage_our=stage_our.get().strip()))
        if not repo:
            messagebox.showinfo('Bimmhacks Apps', 'No repository set yet.\n\nEnter your own GitHub '
                                '"owner/repo" of packaged apps once it is published, then install.')
            return
        if not re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', repo):
            messagebox.showwarning('Bimmhacks Apps', 'Enter the source as owner/repo, e.g. bimmhacks/apps.')
            return
        if not messagebox.askokcancel('Bimmhacks Apps', 'Make %s available on the car at %s?' % (repo, ip)):
            return
        def w():
            do_install_apps_source(need_node(), ip, key, repo, 'Bimmhacks Apps',
                                   stage_dir=stage_our.get().strip() or None, log=log)
        start(w, 'Bimmhacks Apps source ready - install from the car Store.')
    ttk.Button(obf, text='Make our apps available', style='Accent.TButton', command=on_our).pack(side='left', padx=4)
    ttk.Button(obf, text='How to make an app', command=lambda: _open_path(APP_GUIDE)).pack(side='left', padx=4)
    ttk.Button(obf, text='Open our repo', command=lambda: _open_url(
        'https://github.com/' + (our_repo.get().strip() or BIMMHACKS_APPS_REPO))).pack(side='left', padx=4)

    # ========================= Tab 7: Credits ===================================================
    t_cred = ttk.Frame(nb)
    nb.add(t_cred, text='Credits')
    c6 = card(t_cred)
    eyebrow(c6, 'CREDITS & LICENCES')
    title(c6, 'Built on open-source work')
    for what, who in CREDITS:
        row = tk.Frame(c6, bg=PANEL)
        row.pack(fill='x', padx=16, pady=2)
        ttk.Label(row, text=what, style='Card.TLabel', width=34, anchor='w').pack(side='left')
        ttk.Label(row, text=who, style='CardMuted.TLabel', anchor='w').pack(side='left')
    para(c6, '')
    para(c6, 'These apps are free and open source. Charge for installation and support, credit the '
             'original authors, and tell clients the apps themselves are free. BMW\'s app SDK is never '
             'shipped - it is read from the client\'s own car and deleted from the PC afterwards.')
    cbf = tk.Frame(c6, bg=PANEL)
    cbf.pack(fill='x', padx=14, pady=(10, 14))
    ttk.Button(cbf, text='idrive7-apps', command=lambda: _open_url(
        'https://github.com/cauliflowermossgrape/idrive7-apps')).pack(side='left', padx=4)
    ttk.Button(cbf, text='ssh-root tool', command=lambda: _open_url(
        'https://github.com/cauliflowermossgrape/idrive7-8-ssh-root')).pack(side='left', padx=4)
    ttk.Button(cbf, text='mgu-store', command=lambda: _open_url(
        'https://github.com/robertnisipeanu/mgu-store')).pack(side='left', padx=4)

    # ---- console + progress ---------------------------------------------------------------------
    progress = ttk.Progressbar(body, mode='indeterminate')
    progress.pack(fill='x', padx=16, pady=(2, 2))
    console = tk.Frame(body, bg=INK)
    console.pack(fill='both', expand=True, padx=16, pady=(0, 6))
    logbox = scrolledtext.ScrolledText(console, height=12, bg=INK, fg='#d6dbe4', insertbackground='#d6dbe4',
                                       relief='flat', borderwidth=0,
                                       font=('Consolas' if IS_WIN else 'DejaVu Sans Mono', 9))
    logbox.pack(fill='both', expand=True, padx=8, pady=8)
    foot = tk.Frame(body, bg=SURFACE)
    foot.pack(fill='x', padx=16, pady=(0, 10))
    tk.Label(foot, text='Bimmhacks by Mr. Wali', bg=SURFACE, fg=INK,
             font=(base_font[0], 9, 'bold')).pack(side='left')
    tk.Label(foot, text='  -  apps by cauliflowermossgrape (MIT), Store by Robert Nisipeanu & IterioDev '
             '(GPL-3.0)', bg=SURFACE, fg=MUTED, font=(base_font[0], 9)).pack(side='left')

    def pump():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == 'log':
                    logbox.insert('end', val + '\n')
                    logbox.see('end')
                elif kind == 'done':
                    busy['on'] = False
                    progress.stop()
                    for b2 in buttons:
                        b2.state(['!disabled'])
                    ok, msg = val
                    (messagebox.showinfo if ok else messagebox.showerror)('Bimmhacks', msg)
        except queue.Empty:
            pass
        root.after(100, pump)

    # collect buttons for enable/disable during work
    def collect(w):
        for ch in w.winfo_children():
            if isinstance(ch, ttk.Button):
                buttons.append(ch)
            collect(ch)
    collect(body)

    log('Bimmhacks MGU Flash & App Studio (iDrive 7 & 8)  v%s' % VERSION)
    log('Flashing app developed by Mr. Wali. Apps: %d. Languages: %s.' % (len(apps), ', '.join(langs)))
    log('Work left to right: Root access -> Apps -> App Store -> more sources.')
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
                'root-fix-key': 'fix-key', 'root-disable': 'disable'}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Bimmhacks MGU Flash & App Studio (iDrive 7 & 8)')
    ap.add_argument('action', nargs='?', default='gui',
                    choices=['gui', 'install', 'test', 'status', 'restore', 'apps',
                             'root-status', 'root-flash', 'root-enable', 'root-fix-key', 'root-disable',
                             'detect-vin', 'backup-coding', 'store-install',
                             'install-idrive-apps', 'install-car-apps', 'install-ridetube',
                             'install-our-apps', 'usb-repo'])
    ap.add_argument('apps', nargs='*', help='app ids for install (default: all non-experimental apps)')
    ap.add_argument('--key', help='root SSH private key (id_rsa from the root-access step)')
    ap.add_argument('--ip', help='head unit address (default: from connect.txt next to the key, else %s)' % DEFAULT_IP)
    ap.add_argument('--vin', help='17-char VIN (required for root-flash / root-fix-key)')
    ap.add_argument('--arch', default='id7', choices=ARCHES, help='head-unit variant (default: id7)')
    ap.add_argument('--gateway', default='auto', help="ENET gateway address, or 'auto' (default)")
    ap.add_argument('--language', default='en')
    ap.add_argument('--out', help='backup-coding: folder to write the coding snapshot into')
    ap.add_argument('--repo', help='install-our-apps / usb-repo: a GitHub owner/repo source')
    ap.add_argument('--stage', help='install-*-apps: also stage the apps to this folder / USB for offline install')
    ap.add_argument('--no-reboot', action='store_true', help='do not reboot the unit at the end (controller gate loads at the next start)')
    ap.add_argument('--no-controller-gate', action='store_true', help='do not install the controller gate (Doom)')
    ap.add_argument('--no-renderer', action='store_true', help='store-install: leave out the renderer')
    ap.add_argument('--no-uplink', action='store_true', help='store-install: leave out the internet uplink')
    ap.add_argument('--keep-build', action='store_true', help="keep oap-sdk/ and dist/ after installing (contain BMW's SDK)")
    a = ap.parse_args(argv)

    def log(m):
        print(m, flush=True)

    if a.action == 'gui':
        try:
            import tkinter  # noqa: F401
        except ImportError:
            sys.exit('No Tk (window toolkit) in this Python. Use the command line, e.g.:\n'
                     '  python bimmhacks_installer.py install --key path/to/id_rsa')
        return run_gui()
    if a.action == 'apps':
        for aid, title, desc, exp in list_apps():
            print('%-18s %-22s %s' % (aid, title, '(experimental: %s)' % exp if exp else ''))
        print('languages: ' + ', '.join(list_languages()))
        return

    try:
        # root-access actions and detect-vin don't need Node or an existing key
        if a.action in ROOT_ACTIONS:
            do_root(ROOT_ACTIONS[a.action], log, vin=a.vin, ip=a.ip, gateway=a.gateway, arch=a.arch)
            return
        if a.action == 'detect-vin':
            vin = detect_vin(log, ip=a.ip, gateway=a.gateway, arch=a.arch)
            print('VIN: ' + vin if vin else 'VIN not detected (is the ENET cable in and the car awake?)')
            return
        if a.action == 'backup-coding':
            do_backup_coding(log, vin=a.vin, ip=a.ip, gateway=a.gateway, arch=a.arch, out_dir=a.out)
            return

        if a.action == 'usb-repo':
            # staging to a folder needs only Node (no car, no ssh)
            node = find_node()
            if not node:
                raise InstallerError('Node.js is not installed: get the LTS version from https://nodejs.org/')
            if not a.stage:
                ap.error('--stage <folder> is needed')
            do_usb_repo(node, a.stage, (['github:' + a.repo] if a.repo else []), log=log)
            return

        node, ssh = preflight(log)
        if a.action == 'store-install':
            if not a.key:
                ap.error('--key is needed')
            do_store_install(node, a.ip or ip_from_key(a.key) or DEFAULT_IP, a.key,
                             no_renderer=a.no_renderer, no_uplink=a.no_uplink, log=log)
            return
        if a.action in ('install-idrive-apps', 'install-car-apps', 'install-ridetube', 'install-our-apps'):
            if not a.key and not a.stage:
                ap.error('--key is needed (or --stage <folder> for an offline USB)')
            ip2 = a.ip or (ip_from_key(a.key) if a.key else None) or DEFAULT_IP
            if a.action == 'install-idrive-apps':
                do_install_apps_source(node, ip2, a.key, IDRIVE_APPS_REPO, 'idrive-apps (robertnisipeanu)',
                                       stage_dir=a.stage, log=log)
            elif a.action == 'install-car-apps':
                do_install_apps_source(node, ip2, a.key, CARAPPS_REPO, 'car-apps (IterioDev, iDrive 8)',
                                       stage_dir=a.stage, log=log)
            elif a.action == 'install-ridetube':
                do_install_apps_source(node, ip2, a.key, RIDETUBE_REPO, 'RideTube (YouTube, unofficial)',
                                       stage_dir=a.stage, log=log)
            else:
                repo = a.repo or BIMMHACKS_APPS_REPO
                if not repo:
                    raise InstallerError('No "our apps" repository set. Pass --repo owner/repo, or set '
                                         'BIMMHACKS_APPS_REPO.')
                do_install_apps_source(node, ip2, a.key, repo, 'Bimmhacks Apps', stage_dir=a.stage, log=log)
            return

        if not a.key:
            ap.error('--key is needed')
        ip = a.ip or ip_from_key(a.key) or DEFAULT_IP
        if a.action == 'install':
            known = [x[0] for x in list_apps()]
            apps = a.apps or [x for x in known if x not in EXPERIMENTAL]
            bad = [x for x in apps if x not in known]
            if bad:
                raise InstallerError('Unknown app(s): %s. Known: %s' % (' '.join(bad), ' '.join(known)))
            if a.language not in list_languages():
                raise InstallerError('Unknown language %s. Known: %s' % (a.language, ' '.join(list_languages())))
            do_install(ssh, node, ip, a.key, apps, a.language, reboot=not a.no_reboot,
                       controller_gate=not a.no_controller_gate, keep_build=a.keep_build, log=log)
        else:
            do_simple(ssh, ip, a.key, a.action, log)
    except InstallerError as e:
        print('ERROR: ' + str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
