#!/usr/bin/env python3
"""Screenshots of the Bimmhacks MGU Tool window (developer tool, not shipped):

    xvfb-run -a -s "-screen 0 1040x980x24" python3 installer/tests/desktop_shots.py

Needs Tk (python3-tk), Xvfb and ImageMagick (`import`; `convert`/`montage` optional). Nothing else -
it captures the three tabs as they open (static views), so there is no car, no network and no Store
checkout to set up. Writes installer/screenshots/NN-name.png + a one-page overview, and refreshes
installer/screenshot.png (the README's picture). Only you see the result.
"""
import json, os, shutil, tempfile, subprocess, sys, tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
OUT = os.environ.get('OUT') or os.path.join(REPO, 'installer', 'screenshots')
os.makedirs(OUT, exist_ok=True)
W, H = 1020, 940
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(REPO, 'installer'))
sys.path.insert(0, os.path.join(REPO, 'tools', 'idrive7-8-ssh-root'))
import bimmhacks_installer as bi  # noqa: E402
from tkinter import messagebox    # noqa: E402

VIN = 'WBA5R1C0XLFJ12345'
demo = os.environ.get('DEMO_DIR') or os.path.join(tempfile.gettempdir(), 'bimmhacks')
MARKER = '.bimmhacks-screenshot-demo'
if os.path.exists(demo):
    if not os.path.isfile(os.path.join(demo, MARKER)):
        sys.exit('%s exists and was not made by this script: set DEMO_DIR' % demo)
    shutil.rmtree(demo)
os.makedirs(demo)
open(os.path.join(demo, MARKER), 'w').close()
keydir = os.path.join(demo, 'enrollments', VIN)
os.makedirs(keydir)
key = os.path.join(keydir, 'id_rsa')
open(key, 'w').write('-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n')

bi.CONFIG = os.path.join(demo, 'config.json')
bi.CODING_BACKUP_DIR = os.path.join(demo, 'coding-backups')
json.dump({'vin': VIN, 'key': key, 'ip': '169.254.36.47', 'arch': 'id7'}, open(bi.CONFIG, 'w'))

# the window never talks to a car or shows a blocking dialog in this static tour
messagebox.showinfo = messagebox.showerror = messagebox.showwarning = lambda *a, **k: None
messagebox.askyesno = messagebox.askokcancel = lambda *a, **k: True


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
        os.system('import -window root -crop %dx%d+0+0 %s/%s.png' % (W, H, OUT, name))
        shots.append(name)

    tabs = lambda: all_of(root, lambda c: isinstance(c, tk.Label) and c.cget('cursor') == 'hand2')

    def go_tab(i):
        tabs()[i].event_generate('<Button-1>')
        root.update(); root.update_idletasks()

    def run():
        shot('01-root-access')          # root access + the experimental coding-restore card
        go_tab(1); shot('02-app-store')  # the on-car App Store installer
        go_tab(2); shot('03-credits')
        print('shots:', len(shots))
        root.destroy()

    root.after(600, run)
    orig_mainloop(root, *a)


tk.Tk.mainloop = mainloop
try:
    bi.run_gui()
finally:
    shutil.rmtree(demo, ignore_errors=True)


def finish():
    names = sorted(n for n in os.listdir(OUT) if n[:2].isdigit() and n.endswith('.png') and not n.startswith('00-'))
    if shutil.which('convert'):
        for n in names:
            p = os.path.join(OUT, n)
            subprocess.run(['convert', p, '-colors', '256', '-define', 'png:compression-level=9', p], check=True)
    sheet = os.path.join(OUT, '00-overview.png')
    tiles = ['01-root-access.png', '02-app-store.png', '03-credits.png']
    if shutil.which('montage') and all(t in names for t in tiles):
        subprocess.run(['montage'] + [os.path.join(OUT, t) for t in tiles] + ['-tile', '3x1', '-geometry',
                       '680x626+8+8', '-background', '#d9dde3', sheet], check=True)
        if shutil.which('convert'):
            subprocess.run(['convert', sheet, '-depth', '8', '-colors', '256', '-define',
                            'png:compression-level=9', 'PNG8:' + sheet], check=True)
    if '01-root-access.png' in names:
        shutil.copyfile(os.path.join(OUT, '01-root-access.png'), os.path.join(REPO, 'installer', 'screenshot.png'))
    print('screenshots in', OUT, '-', len(names), 'steps')


finish()
