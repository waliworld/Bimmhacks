#!/usr/bin/env python3
"""make_client_package.py - pack the installer and the app sources into ONE zip for clients.

    python3 installer/make_client_package.py            # -> dist/Bimmhacks-iDrive7-Installer-<version>.zip

The client unzips it and double-clicks "Install Bimmhacks Apps.cmd" (Windows) or
"Install Bimmhacks Apps.command" (macOS). Left out: BMW's SDK and any built .mgu (never shipped,
the installer makes them from each client's own car), the screenshots / GIFs, the renderer's
test captures and the upstream CI files. The Doom engine's GPL source (third_party/) stays in.
"""
import os, re, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT_DIR = os.path.join(REPO, 'dist')

TOP_FILES = ['Install Bimmhacks Apps.cmd', 'Install Bimmhacks Apps.command', 'install.sh',
             'README.md', 'CLIENT-GUIDE.md', 'UPSTREAM.md']
TREES = ['installer', 'idrive7-apps', 'tools']
SKIP_DIRS = {'.git', '.github', '__pycache__', 'node_modules', 'oap-sdk', 'dist', 'preview', 'backups',
             'work', 'captures', '.font-cache', 'build'}
SKIP_PATHS = {'idrive7-apps/docs/media', 'idrive7-apps/docs/screens', 'idrive7-apps/kit/renderd/test',
              'idrive7-apps/kit/renderd/build',
              # the SSH-root tool keeps the shared flash assets (data/assets, needed) but never
              # client-specific keys or logs
              'tools/idrive7-8-ssh-root/data/enrollments', 'tools/idrive7-8-ssh-root/data/logs'}
SKIP_EXT = ('.mgu', '.tgz', '.pyc', '.log', '.wad')
# never ship a client's generated SSH key, whatever folder it is in
SKIP_NAMES = {'id_rsa', 'id_rsa.pub', 'id_rsa.ppk', 'id_rsa.pkcs1.pem', 'id_rsa.pkcs8.pem'}
EXECUTABLE = ('.sh', '.command', 'mgu-renderd')


def version():
    src = open(os.path.join(HERE, 'bimmhacks_installer.py'), encoding='utf-8').read()
    return re.search(r"^VERSION = '([^']+)'", src, re.M).group(1)


def files():
    for name in TOP_FILES:
        if os.path.isfile(os.path.join(REPO, name)):
            yield name
    for tree in TREES:
        for d, dirs, names in os.walk(os.path.join(REPO, tree)):
            rel_d = os.path.relpath(d, REPO).replace(os.sep, '/')
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and rel_d + '/' + x not in SKIP_PATHS)
            for n in sorted(names):
                if not n.endswith(SKIP_EXT) and n not in SKIP_NAMES:
                    yield rel_d + '/' + n


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    top = 'Bimmhacks-iDrive7-Installer-' + version()
    out = os.path.join(OUT_DIR, top + '.zip')
    count = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files():
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


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
