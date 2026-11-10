#!/usr/bin/env python3
"""make_client_package.py - pack the Bimmhacks MGU tool into small client zips.

    python3 installer/make_client_package.py            # both zips, into dist/
    python3 installer/make_client_package.py --pc       # only the PC installer zip
    python3 installer/make_client_package.py --pocket   # only the Pocket (box) zip

It builds TWO independent packages so the PC and the Pocket releases are separate - a problem with
one never holds up the other:

  dist/Bimmhacks-MGU-Installer-<version>.zip   the desktop app. The client unzips it and double-clicks
      "Install Bimmhacks Apps.cmd" (Windows) or "Install Bimmhacks Apps.command" (macOS). Root access,
      the coding backup / restore, and the on-car App Store installer. The Pocket box software is left
      out entirely.

  dist/Bimmhacks-Pocket-<version>.zip          the phone-on-a-box build. Copied onto a Raspberry Pi and
      set up with installer/pocket/setup-box.sh (see docs/POCKET.md). It carries the same installer and
      tools the box drives, plus the Pocket server and its phone page.

Neither zip ships the idrive7-apps app sources (apps install from the on-car Store, not built on the
computer), the test suite, screenshots, or any client-specific key / log.
"""
import argparse, os, re, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT_DIR = os.path.join(REPO, 'dist')

TREES = ['installer', 'tools']

# the desktop app: PC launchers + the general docs, but NOT the Pocket guide.
PC_TOP_FILES = ['Install Bimmhacks Apps.cmd', 'Install Bimmhacks Apps.command', 'install.sh',
                'README.md', 'CLIENT-GUIDE.md', 'UPSTREAM.md', 'docs/MAKE-YOUR-OWN-APP.md']
# the box: no desktop double-click launchers (it is headless and set up over SSH); the Pocket guide leads.
POCKET_TOP_FILES = ['README.md', 'UPSTREAM.md', 'docs/POCKET.md', 'docs/MAKE-YOUR-OWN-APP.md']

# files that belong to the Pocket box only. Kept OUT of the PC zip; included in the Pocket zip.
POCKET_ONLY = {'installer/pocket', 'installer/pocket_server.py', 'installer/pocket-screenshot.png',
               'docs/POCKET.md'}

SKIP_DIRS = {'.git', '.github', '__pycache__', 'node_modules', 'dist', 'coding-backups',
             # the Store package a Pocket box staged for itself (downloaded on the box, not shipped)
             'pocket-store', 'screenshots'}
SKIP_PATHS = {# tests and their fake car are for developers, not clients
              'installer/tests',
              # the SSH-root tool keeps the shared flash assets (data/assets, needed) but never
              # client-specific keys or logs
              'tools/idrive7-8-ssh-root/data/enrollments', 'tools/idrive7-8-ssh-root/data/logs'}
SKIP_EXT = ('.mgu', '.tgz', '.pyc', '.log')
# never ship a client's generated SSH key, whatever folder it is in
SKIP_NAMES = {'id_rsa', 'id_rsa.pub', 'id_rsa.ppk', 'id_rsa.pkcs1.pem', 'id_rsa.pkcs8.pem'}
EXECUTABLE = ('.sh', '.command', 'mgu-renderd')


def version():
    src = open(os.path.join(HERE, 'bimmhacks_installer.py'), encoding='utf-8').read()
    return re.search(r"^VERSION = '([^']+)'", src, re.M).group(1)


def _pocket_only(rel):
    """True if rel is (or is inside) a Pocket-only path."""
    return any(rel == p or rel.startswith(p + '/') for p in POCKET_ONLY)


def files(kind):
    top = PC_TOP_FILES if kind == 'pc' else POCKET_TOP_FILES
    for name in top:
        if os.path.isfile(os.path.join(REPO, name)):
            yield name
    for tree in TREES:
        for d, dirs, names in os.walk(os.path.join(REPO, tree)):
            rel_d = os.path.relpath(d, REPO).replace(os.sep, '/')
            keep = []
            for x in sorted(dirs):
                sub = rel_d + '/' + x
                if x in SKIP_DIRS or sub in SKIP_PATHS:
                    continue
                if kind == 'pc' and _pocket_only(sub):      # the PC zip omits the Pocket box code
                    continue
                keep.append(x)
            dirs[:] = keep
            for n in sorted(names):
                rel = rel_d + '/' + n
                if n.endswith(SKIP_EXT) or n in SKIP_NAMES:
                    continue
                if kind == 'pc' and _pocket_only(rel):
                    continue
                yield rel


def build(kind):
    os.makedirs(OUT_DIR, exist_ok=True)
    stem = 'Bimmhacks-MGU-Installer-' if kind == 'pc' else 'Bimmhacks-Pocket-'
    top = stem + version()
    out = os.path.join(OUT_DIR, top + '.zip')
    count = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files(kind):
            src = os.path.join(REPO, rel)
            zi = zipfile.ZipInfo.from_file(src, top + '/' + rel)
            zi.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if rel.endswith(EXECUTABLE) else 0o644
            zi.external_attr = (0o100000 | mode) << 16
            with open(src, 'rb') as f:
                data = f.read()
            if rel.endswith(('.cmd', '.ps1')):   # Windows scripts: CRLF, whatever the checkout has
                data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            z.writestr(zi, data)
            count += 1
    print('%s  (%d files, %.1f MB)' % (out, count, os.path.getsize(out) / 1e6))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='Pack the Bimmhacks PC installer and/or Pocket box zips.')
    ap.add_argument('--pc', action='store_true', help='build only the PC installer zip')
    ap.add_argument('--pocket', action='store_true', help='build only the Pocket (box) zip')
    args = ap.parse_args(argv)
    kinds = []
    if args.pc:
        kinds.append('pc')
    if args.pocket:
        kinds.append('pocket')
    if not kinds:
        kinds = ['pc', 'pocket']
    for k in kinds:
        build(k)
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
