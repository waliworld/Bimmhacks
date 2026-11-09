#!/usr/bin/env python3
"""Screenshots of the desktop window, taken by driving the real window through a session (developer tool,
not shipped):

    xvfb-run -a -s "-screen 0 1000x960x24" python3 installer/tests/desktop_shots.py

Needs Tk (python3-tk), Xvfb and ImageMagick (`import`; `convert` and `montage` optional), and a checkout of
mgu-store for the real Store updater (MGU_STORE_SRC, see installer/tests/test_store_updater.py).

Everything the window talks to is a stand-in from the test suite: the fake ENET car (fake_enet.py, found by
the window's own UDP broadcast discovery), a fake `ssh`, the Store's REAL updater (updater_harness.js) and a
synthetic capture. The dialogs are Tk's own, so they look plainer than the native ones Windows shows; the
text is the same. Writes installer/screenshots/NN-name.png (14 steps), 00-overview.png and refreshes
installer/screenshot.png (the README's picture). Environment: OUT (folder), DEMO_DIR (default
<tmp>/bimmhacks: it shows in the key field and the log; created and removed by the script).
"""
import json, os, shutil, subprocess, sys, tempfile, tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
OUT = os.environ.get('OUT') or os.path.join(REPO, 'installer', 'screenshots')
os.makedirs(OUT, exist_ok=True)
W, H = 980, 900
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(REPO, 'installer')); sys.path.insert(0, os.path.join(REPO, 'tools', 'idrive7-8-ssh-root'))
import bimmhacks_installer as bi
from tkinter import messagebox, filedialog
from fake_enet import FakeCar
from test_store_updater import RealUpdater
from test_pid_capture import scripted

VIN = 'WBA5R1C0XLFJ12345'                      # a made-up VIN
# a tidy, fixed path shows in the key field and the log; the folder is removed again at the end, and an
# existing one is only replaced if an earlier run made it (the marker file): never someone's own folder
demo = os.environ.get('DEMO_DIR') or os.path.join(tempfile.gettempdir(), 'bimmhacks')
MARKER = '.bimmhacks-screenshot-demo'
if os.path.exists(demo):
    if not os.path.isfile(os.path.join(demo, MARKER)):
        sys.exit('%s exists and was not made by this script: set DEMO_DIR to a folder that does not exist yet' % demo)
    shutil.rmtree(demo)
os.makedirs(demo)
open(os.path.join(demo, MARKER), 'w').close()
keydir = os.path.join(demo, 'enrollments', VIN)
os.makedirs(keydir)
key = os.path.join(keydir, 'id_rsa')
open(key, 'w').write('-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n')
open(os.path.join(keydir, 'connect.txt'), 'w').write('Host (IP):  169.254.36.47\nVIN:        %s\n' % VIN)
pcap = os.path.join(demo, 'drive-with-logger.pcap')
open(pcap, 'wb').write(scripted().pcap())

# ---- a stand-in `ssh` for this run only -----------------------------------------------------------------
stub_dir = os.path.join(demo, 'bin')
os.makedirs(stub_dir)
stub = os.path.join(stub_dir, 'ssh')
open(stub, 'w').write('''#!/bin/sh
for a; do cmd="$a"; done
cmd=${cmd#export LC_ALL=C; }
case "$cmd" in
    "echo CONNECTED"*) printf 'CONNECTED\\n0\\nMGU_OAP_OK\\n' ;;
    "test -f /var/opt/mgu-deploy/mgu-deploy.sh"*) echo STORE_PRESENT ;;
    "/usr/bin/node - "*) eval "node ${cmd#/usr/bin/node}" ;;
    *) : ;;
esac
exit 0
''')
os.chmod(stub, 0o755)
os.environ['PATH'] = stub_dir + os.pathsep + os.environ['PATH']

up = RealUpdater()
os.environ.update(MGU_UPDATER_SOCKET=up.sock, MGU_OAP_ROOT=up.oap, MGU_ADD_SOURCE_RETRIES='2', MGU_ADD_SOURCE_WAIT_MS='150')

bi.CONFIG = os.path.join(demo, 'config.json')
bi.CODING_BACKUP_DIR = os.path.join(demo, 'coding-backups')
json.dump({'vin': VIN, 'key': key, 'ip': '169.254.36.47', 'arch': 'id7'}, open(bi.CONFIG, 'w'))

# the window finds the car by UDP broadcast, as it does on a real ENET link: the fake car answers on every address

shown = []
messagebox.showinfo = lambda t, m, **k: shown.append(('info', m))
messagebox.showerror = lambda t, m, **k: shown.append(('error', m))
filedialog.askopenfilename = lambda **k: pcap
real_info, real_warn, real_askyn, real_askok = None, None, None, None


def all_of(w, pred, out=None):
    out = [] if out is None else out
    for c in w.winfo_children():
        try:
            if pred(c):
                out.append(c)
        except Exception:
            pass
        all_of(c, pred, out)
    return out


orig_mainloop = tk.Tk.mainloop


def mainloop(root, *a):
    root.geometry('%dx%d+0+0' % (W, H))
    shots = []

    def shot(name):
        root.update(); root.update_idletasks()
        path = '%s/%s.png' % (OUT, name)
        os.system('import -window root -crop %dx%d+0+0 %s' % (W, H, path))
        shots.append(path)

    tabs = lambda: all_of(root, lambda c: isinstance(c, tk.Label) and c.cget('cursor') == 'hand2')
    button = lambda text: [b for b in all_of(root, lambda c: c.winfo_class() == 'TButton' and c.cget('text') == text and c.winfo_ismapped())][0]
    logtext = lambda: all_of(root, lambda c: isinstance(c, tk.Text))[0].get('1.0', 'end')
    go_tab = lambda i: (tabs()[i].event_generate('<Button-1>'), root.update_idletasks())

    def wait_idle(timeout=90):
        """until no worker is running: the progress bar is gone"""
        import time
        end = time.time() + timeout
        while time.time() < end:
            root.update()
            bars = all_of(root, lambda c: c.winfo_class() == 'TProgressbar' and c.winfo_ismapped())
            if not bars:
                time.sleep(0.4); root.update()
                return
            time.sleep(0.15)

    def dialog(name, answer, buttons_text=None):
        """photograph the dialog that is about to open (invoked from inside it) and answer it"""
        def cap():
            shot(name)
            root.tk.call('set', '::tk::Priv(button)', answer)
        root.after(900, cap)

    def real_dialogs_on():
        messagebox.showinfo = real_info
        messagebox.showwarning = real_warn
        messagebox.askyesno = real_askyn
        messagebox.askokcancel = real_askok

    def real_dialogs_off():
        messagebox.showinfo = lambda t, m, **k: shown.append(('info', m))
        messagebox.showwarning = lambda t, m, **k: shown.append(('warn', m))
        messagebox.askyesno = messagebox.askokcancel = lambda *a, **k: True

    def entry_with(text):
        return [e for e in all_of(root, lambda c: c.winfo_class() == 'TEntry') if e.get() == text][0]

    def run():
        with FakeCar(vin=VIN, host='0.0.0.0'):
            # ---- 1. Root access ---------------------------------------------------------------------
            shot('01-root-access')

            # ---- 2. Detect VIN: clear the field first so the car's answer shows
            e = entry_with(VIN); e.delete(0, 'end')
            button('Detect VIN').invoke(); wait_idle()
            shot('02-detect-vin')

            # ---- 3. Back up coding (ticks the confirmation by itself)
            button('Back up coding').invoke(); wait_idle()
            shot('03-backup-coding')

            # ---- 4. Flash: the confirmation the window asks for
            real_dialogs_on()
            dialog('04-flash-confirmation', 'no')
            button('Flash + enable SSH').invoke()

            # ---- 5. Flash without the backup tick: refused with a reminder
            chk = all_of(root, lambda c: c.winfo_class() == 'TCheckbutton' and 'restorable coding backup' in str(c.cget('text')))[0]
            chk.invoke()
            dialog('05-flash-needs-backup', 'ok')
            button('Flash + enable SSH').invoke()
            real_dialogs_off()

            # ---- 6. Apps
            go_tab(1); root.update()
            shot('06-apps')
            button('Test connection').invoke(); wait_idle()
            shot('07-apps-connected')

            # ---- 8. App Store + RideTube
            go_tab(2); root.update()
            shot('08-app-store')
            real_dialogs_on()
            dialog('09-ridetube-confirm', 'ok')
            button('Make it available').invoke()
            real_dialogs_off()
            wait_idle()
            shot('10-ridetube-added')

            # ---- 11. More apps
            go_tab(3); root.update()
            shot('11-more-apps')

            # ---- 12. Bimmhacks apps + the capture analyser
            go_tab(4); root.update()
            shot('12-bimmhacks-apps')
            button('Analyse a capture').invoke(); wait_idle()
            shot('13-analyse-capture')

            # ---- 14. Credits
            go_tab(5); root.update()
            shot('14-credits')
        print('shots:', len(shots))
        print('log excerpt:'); print('\n'.join(l for l in logtext().splitlines() if l.strip())[-1600:])
        print('dialogs shown without capture:', shown)
        print('sources:', [(s['id'], s.get('channel')) for s in up.sources()])
        root.destroy()

    root.after(900, run)
    orig_mainloop(root, *a)


real_info, real_warn, real_askyn, real_askok = messagebox.showinfo, messagebox.showwarning, messagebox.askyesno, messagebox.askokcancel
# (the real implementations were replaced above for showinfo/showerror; keep Tk's own for the dialog shots)
import tkinter.messagebox as _mb
import importlib
importlib.reload(_mb)
real_info, real_warn, real_askyn, real_askok = _mb.showinfo, _mb.showwarning, _mb.askyesno, _mb.askokcancel
messagebox.showinfo = lambda t, m, **k: shown.append(('info', m))
messagebox.showwarning = lambda t, m, **k: shown.append(('warn', m))
messagebox.askyesno = messagebox.askokcancel = lambda *a, **k: True
tk.Tk.mainloop = mainloop
try:
    bi.run_gui()
finally:
    up.close()
    shutil.rmtree(demo, ignore_errors=True)


def finish():
    """256 colours (the window is flat colours: a third of the size, no visible change), an overview sheet of
    the six tabs, and the README's picture"""
    names = sorted(n for n in os.listdir(OUT) if n[:2].isdigit() and n.endswith('.png') and not n.startswith('00-'))
    if shutil.which('convert'):
        for n in names:
            p = os.path.join(OUT, n)
            subprocess.run(['convert', p, '-colors', '256', '-define', 'png:compression-level=9', p], check=True)
    tabs = ['01-root-access.png', '06-apps.png', '08-app-store.png', '11-more-apps.png', '12-bimmhacks-apps.png', '14-credits.png']
    if shutil.which('montage') and all(t in names for t in tabs):
        sheet = os.path.join(OUT, '00-overview.png')
        subprocess.run(['montage'] + [os.path.join(OUT, t) for t in tabs] + ['-tile', '3x2', '-geometry', '640x587+8+8',
                       '-background', '#d9dde3', sheet], check=True)
        if shutil.which('convert'):          # the resampling makes many greys: 256 colours again
            subprocess.run(['convert', sheet, '-depth', '8', '-colors', '256', '-define', 'png:compression-level=9', 'PNG8:' + sheet], check=True)
    if '01-root-access.png' in names:
        shutil.copyfile(os.path.join(OUT, '01-root-access.png'), os.path.join(REPO, 'installer', 'screenshot.png'))
    print('screenshots in', OUT, '-', len(names), 'steps')


finish()
