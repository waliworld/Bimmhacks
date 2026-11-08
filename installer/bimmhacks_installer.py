#!/usr/bin/env python3
"""Bimmhacks iDrive 7 App Installer.

One-click installer for the MGU (iDrive 7) custom apps in ../idrive7-apps. It does every step of
the upstream guide for the client, against the client's own car:

  1. connects to the head unit over SSH (root key from the idrive7-8-ssh-root tool)
  2. copies BMW's x_call app off THAT car and extracts the oap-sdk from it
     (BMW's SDK is copyrighted and is never shipped with this installer)
  3. builds the chosen apps in the chosen language and packs the car kit
  4. copies the kit to the car, verifies it, runs the read-only check, then installs
  5. deletes the local SDK copy and build output again (they contain BMW's code)

Status and "back to stock" (restore) work the same way.

    python bimmhacks_installer.py                 # the window (default)
    python bimmhacks_installer.py install --key id_rsa [--ip 169.254.36.47] [--language en] [apps ...]
    python bimmhacks_installer.py test|status|restore --key id_rsa [--ip ...]
    python bimmhacks_installer.py apps            # list the apps

Needs Python 3.6+, Node.js 14+ and the OpenSSH client (built into Windows 10/11, macOS, Linux).
No pip packages.
"""
import argparse, io, json, os, queue, re, shutil, stat, subprocess, sys, tarfile, tempfile, threading

VERSION = '1.0.0'
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('BIMMHACKS_APPS_ROOT') or os.path.join(os.path.dirname(HERE), 'idrive7-apps')
TOOLS = os.path.join(ROOT, 'kit', 'tools')
SDK_DIR = os.path.join(ROOT, 'oap-sdk')
DIST = os.path.join(ROOT, 'dist')
DEST = '/var/opt/mgu-custom-apps'          # where the kit lives on the head unit (same as upstream)
DEFAULT_IP = '169.254.36.47'                # the unit's address with the idrive7-8-ssh-root tool
CONFIG = os.path.join(os.path.expanduser('~'), '.bimmhacks-installer.json')
IS_WIN = os.name == 'nt'
NULL = 'NUL' if IS_WIN else '/dev/null'

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

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext

    cfg = load_config()
    apps = list_apps()
    langs = list_languages()
    q = queue.Queue()
    busy = {'on': False}

    root = tk.Tk()
    root.title('Bimmhacks iDrive 7 App Installer  v' + VERSION)
    root.minsize(780, 640)
    try:
        ttk.Style().theme_use('vista' if IS_WIN else 'clam')
    except tk.TclError:
        pass

    pad = {'padx': 8, 'pady': 4}
    head = ttk.Frame(root)
    head.pack(fill='x', **pad)
    ttk.Label(head, text='Bimmhacks iDrive 7 Apps', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
    ttk.Label(head, text='Car parked, ignition on, battery charger connected. Connect the PC to the car, '
                         'then fill in the two fields below.').pack(anchor='w')

    conn = ttk.LabelFrame(root, text='1. Car connection')
    conn.pack(fill='x', **pad)
    key_var = tk.StringVar(value=cfg.get('key', ''))
    ip_var = tk.StringVar(value=cfg.get('ip', DEFAULT_IP))
    ttk.Label(conn, text='Root SSH key (id_rsa):').grid(row=0, column=0, sticky='w', **pad)
    ttk.Entry(conn, textvariable=key_var, width=48).grid(row=0, column=1, sticky='we', **pad)

    def browse():
        p = filedialog.askopenfilename(title='The id_rsa from the root tool (data\\enrollments\\<VIN>\\id_rsa)')
        if p:
            key_var.set(p)
            ip = ip_from_key(p)
            if ip:
                ip_var.set(ip)
    ttk.Button(conn, text='Browse...', command=browse).grid(row=0, column=2, **pad)
    ttk.Label(conn, text='Head unit IP address:').grid(row=1, column=0, sticky='w', **pad)
    ttk.Entry(conn, textvariable=ip_var, width=20).grid(row=1, column=1, sticky='w', **pad)
    conn.columnconfigure(1, weight=1)

    sel = ttk.LabelFrame(root, text='2. Apps and language')
    sel.pack(fill='x', **pad)
    chosen = set(cfg.get('apps') or [a for a, _, _, exp in apps if not exp])
    app_vars = {}
    for i, (aid, title, desc, exp) in enumerate(apps):
        v = tk.BooleanVar(value=aid in chosen)
        app_vars[aid] = v
        label = title + ('  (experimental)' if exp else '')
        cb = ttk.Checkbutton(sel, text=label, variable=v)
        cb.grid(row=i // 2, column=i % 2, sticky='w', padx=8, pady=1)
    r = (len(apps) + 1) // 2
    lang_var = tk.StringVar(value=cfg.get('language', 'en'))
    lf = ttk.Frame(sel)
    lf.grid(row=r, column=0, columnspan=2, sticky='w', **pad)
    ttk.Label(lf, text='Language:').pack(side='left')
    lang_box = ttk.Combobox(lf, state='readonly', width=24,
                            values=['%s - %s' % (l, LANGUAGE_NAMES.get(l, l)) for l in langs])
    lang_box.current(langs.index(lang_var.get()) if lang_var.get() in langs else 0)
    lang_box.pack(side='left', padx=6)

    def set_all(on):
        for aid, v in app_vars.items():
            v.set(on and aid not in EXPERIMENTAL)
    ttk.Button(lf, text='Select recommended', command=lambda: set_all(True)).pack(side='left', padx=6)
    ttk.Button(lf, text='Select none', command=lambda: set_all(False)).pack(side='left')
    reboot_var = tk.BooleanVar(value=cfg.get('reboot', True))
    ttk.Checkbutton(sel, text='Reboot the head unit at the end if needed (for the Doom controller gate)',
                    variable=reboot_var).grid(row=r + 1, column=0, columnspan=2, sticky='w', **pad)

    acts = ttk.LabelFrame(root, text='3. Go')
    acts.pack(fill='x', **pad)
    buttons = []
    progress = ttk.Progressbar(root, mode='indeterminate')
    progress.pack(fill='x', **pad)
    logbox = scrolledtext.ScrolledText(root, height=16, font=('Consolas' if IS_WIN else 'Courier', 9))
    logbox.pack(fill='both', expand=True, **pad)
    ttk.Label(root, text=CREDIT, foreground='#666', wraplength=740).pack(anchor='w', padx=8, pady=(0, 8))

    def log(msg):
        q.put(('log', msg))

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
                    for b in buttons:
                        b.state(['!disabled'])
                    ok, msg = val
                    (messagebox.showinfo if ok else messagebox.showerror)('Bimmhacks', msg)
        except queue.Empty:
            pass
        root.after(100, pump)

    def current():
        key, ip = key_var.get().strip().strip('"'), ip_var.get().strip()
        picked = [a for a, v in app_vars.items() if v.get()]
        lang = langs[lang_box.current()] if lang_box.current() >= 0 else 'en'
        save_config({'key': key, 'ip': ip, 'apps': picked, 'language': lang, 'reboot': reboot_var.get()})
        return key, ip, picked, lang

    def start(fn, done_msg):
        if busy['on']:
            return
        busy['on'] = True
        progress.start(12)
        for b in buttons:
            b.state(['disabled'])

        def work():
            try:
                node, ssh = preflight(log)
                fn(node, ssh)
                q.put(('done', (True, done_msg)))
            except InstallerError as e:
                log('ERROR: ' + str(e))
                q.put(('done', (False, str(e))))
            except Exception as e:  # keep the window alive, show what happened
                log('ERROR: %s: %s' % (type(e).__name__, e))
                q.put(('done', (False, 'Unexpected error: %s' % e)))
        threading.Thread(target=work, daemon=True).start()

    def on_test():
        key, ip, _, _ = current()
        start(lambda node, ssh: do_simple(ssh, ip, key, 'test', log), 'Connection OK.')

    def on_install():
        key, ip, picked, lang = current()
        if not picked:
            messagebox.showwarning('Bimmhacks', 'Pick at least one app.')
            return
        if any(a in EXPERIMENTAL for a in picked) and not messagebox.askyesno(
                'Experimental apps', 'You picked experimental apps (Engine Config / Transmission Config). '
                'Engine Config can send control requests to the DME once a tune is selected in its '
                'settings.\n\nInstall them anyway?'):
            return
        if not messagebox.askokcancel('Install', 'Install %d app(s) on the car at %s?\n\nEverything that is '
                                      'changed is backed up first and can be undone with "Restore to stock".'
                                      % (len(picked), ip)):
            return
        reboot = reboot_var.get()
        start(lambda node, ssh: do_install(ssh, node, ip, key, picked, lang, reboot=reboot, log=log),
              'Install finished. The apps appear under Apps on the iDrive.')

    def on_status():
        key, ip, _, _ = current()
        start(lambda node, ssh: do_simple(ssh, ip, key, 'status', log), 'Status read - see the log.')

    def on_restore():
        key, ip, _, _ = current()
        if not messagebox.askyesno('Restore to stock', 'Remove ALL custom apps and put every changed file '
                                   'back exactly as it was before the first install?'):
            return
        start(lambda node, ssh: do_simple(ssh, ip, key, 'restore', log), 'The car is back to stock.')

    for text, fn in (('Test connection', on_test), ('Install / Update apps', on_install),
                     ('Status', on_status), ('Restore to stock', on_restore)):
        b = ttk.Button(acts, text=text, command=fn)
        b.pack(side='left', padx=8, pady=8)
        buttons.append(b)

    log('Bimmhacks iDrive 7 App Installer v%s. Apps found: %d. Languages: %s'
        % (VERSION, len(apps), ', '.join(langs)))
    root.after(100, pump)
    root.mainloop()


# ----------------------------------------------------------------------------- command line

def main(argv=None):
    ap = argparse.ArgumentParser(description='Bimmhacks iDrive 7 App Installer')
    ap.add_argument('action', nargs='?', default='gui', choices=['gui', 'install', 'test', 'status', 'restore', 'apps'])
    ap.add_argument('apps', nargs='*', help='app ids for install (default: all non-experimental apps)')
    ap.add_argument('--key', help='root SSH private key (id_rsa from the idrive7-8-ssh-root tool)')
    ap.add_argument('--ip', help='head unit address (default: from connect.txt next to the key, else %s)' % DEFAULT_IP)
    ap.add_argument('--language', default='en')
    ap.add_argument('--no-reboot', action='store_true', help='do not reboot the unit at the end (controller gate loads at the next start)')
    ap.add_argument('--no-controller-gate', action='store_true', help='do not install the controller gate (Doom)')
    ap.add_argument('--keep-build', action='store_true', help="keep oap-sdk/ and dist/ after installing (contain BMW's SDK)")
    a = ap.parse_args(argv)

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
    if not a.key:
        ap.error('--key is needed')
    ip = a.ip or ip_from_key(a.key) or DEFAULT_IP

    def log(m):
        print(m, flush=True)
    try:
        node, ssh = preflight(log)
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
