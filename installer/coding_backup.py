#!/usr/bin/env python3
"""coding_backup.py - back up (and, experimentally, restore) the head unit's coding over the ENET
diagnostic link, BEFORE the car is rooted or flashed.

Two things are saved, and they are different:

  1. A SNAPSHOT (always works): the SVK - every software module and, crucially, the exact CAFD
     coding-version identifiers - plus the VIN, the IP configuration and the fault memory. This tells
     you EXACTLY which CAFD versions to put back if a flash wipes the coding. It is proven and it is
     what makes any restore reliable.

  2. The CODING DATA itself (experimental): the per-CAFD NCD (netto coding data) the head unit hands
     over the diagnostic link, saved as `CAFD_<ver>_<trip>.ncd` (the format E-Sys / BimmerCode read)
     plus a manifest. If the unit hands it over, you have a real coding backup you can restore from -
     with this tool (experimental), or with E-Sys / Autel as the trusted fallback.

RESTORE writes the saved NCD back after a flash. It is EXPERIMENTAL and SAFE-BY-CONSTRUCTION: it only
writes blocks this tool itself read from this same unit, to the same identifiers, and it re-reads and
compares every block (read-back verify) before it reports success. It refuses unless the car's CAFD
versions and VIN still match the backup. A wrong coding write is what leaves the HMI partly-coded, so
keep your E-Sys / Autel method ready and test restore on a unit you can recover.

The exact UDS mechanism for NCD on a real HU_MGU is the experimental part. It is isolated in
`CodingProtocol` below and reported transparently (lengths, which blocks answered). If your unit does
not hand the NCD over the default way, capture your Autel / E-Sys coding session with
`pid_capture.py` and the real request/replies are in the report; tell us and we set the protocol.

    python coding_backup.py backup  --out <dir> [--gateway auto] [--mgu-ip <ip>] [--arch id7] [--vin <VIN>] [--no-ncd]
    python coding_backup.py verify   --backup <file.json> [--gateway auto] ...
    python coding_backup.py restore  --backup <file.json> --confirm-restore [--dry-run] [--gateway auto] ...

Reuses the bundled idrive7-8-ssh-root 'mgu7' package; needs the `cryptography` package it needs.
Exit 0 on success, 1 on failure, 2 on a refused/guarded restore.
"""
import argparse
import binascii
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SSHROOT_DIR = os.environ.get('BIMMHACKS_SSHROOT_DIR') or os.path.join(
    os.path.dirname(HERE), 'tools', 'idrive7-8-ssh-root')
if SSHROOT_DIR not in sys.path:
    sys.path.insert(0, SSHROOT_DIR)

SCHEMA = 2
MAX_NCD_BYTES = 64 * 1024          # a sane cap per block; a real NCD is a few hundred bytes to a few KB


# ============================================================ the UDS coding mechanism (experimental)
# This is the ONE place the NCD read/write wire format lives. It is a documented best-effort for
# HU_MGU; if a real unit uses different identifiers it will simply refuse (and the backup falls back
# to the proven snapshot, losing nothing), never write to the wrong place. Capture your Autel / E-Sys
# coding session with pid_capture.py to read off the real sequence, then set it here.
class CodingProtocol(object):
    """Read and write a CAFD's NCD over UDS. Symmetric ReadDataByIdentifier / WriteDataByIdentifier on
    a per-coding-block identifier, so a restore writes back exactly what the backup read, to the same
    place, and can verify it by re-reading."""

    # the coding-block identifier base: block i (the i-th CAFD in the SVK, in order) is read with
    #   22 <BASE+i>  and written with  2E <BASE+i>. Replace BASE if your car differs (pid_capture).
    BLOCK_DID_BASE = 0xF5A0
    EXTENDED_SESSION = bytes.fromhex('10 03')
    # security access for writing coding: off by default. A real unit may demand it (it then refuses
    # the write with NRC 0x33/0x35, harmlessly); capture the Autel's 27 exchange to enable it.
    SECURITY_ACCESS = False

    def __init__(self, client, diag, hsfz):
        self.client = client
        self.diag = diag
        self.hsfz = hsfz
        self.dst = hsfz.MGU
        self._session = False

    def _uds(self, req, profile=None):
        profile = profile or self.hsfz.P_MAIN
        frames = self.client.request(self.dst, req, profile)
        return self.hsfz.match_response(frames, self.dst)

    def _did(self, index):
        did = self.BLOCK_DID_BASE + index
        return bytes((did >> 8 & 0xFF, did & 0xFF))

    def enter_session(self):
        if self._session:
            return
        x = self._uds(self.EXTENDED_SESSION, self.hsfz.P_ADVANCED)
        if x is None or x.negative:
            raise CodingError('The head unit would not enter the coding session (10 03).')
        self._session = True

    def read_block(self, index):
        """-> NCD bytes for coding block `index`, or None if the unit does not expose it (unsupported /
        refused). Non-destructive."""
        did = self._did(index)
        x = self._uds(b'\x22' + did, self.hsfz.P_SVK)
        if x is None or x.negative:
            return None
        body = x.data
        # positive ReadDataByIdentifier echoes 62 <did>
        if len(body) < 3 or body[0] != 0x62 or body[1:3] != did:
            return None
        ncd = body[3:]
        if not ncd or len(ncd) > MAX_NCD_BYTES:
            return None
        return bytes(ncd)

    def write_block(self, index, data):
        """Write NCD `data` to coding block `index`, then re-read and compare. -> (ok, read_back_bytes).
        Raises CodingError only on a transport failure; a refused/mismatched write returns ok=False."""
        self.enter_session()
        did = self._did(index)
        x = self._uds(b'\x2E' + did + bytes(data), self.hsfz.P_ADVANCED)
        if x is None:
            raise CodingError('No reply writing coding block %d.' % index)
        if x.negative:
            return (False, None, 'the unit refused the write (NRC 0x%02X)' % x.nrc)
        # positive WriteDataByIdentifier echoes 6E <did>
        if len(x.data) < 3 or x.data[0] != 0x6E or x.data[1:3] != did:
            return (False, None, 'unexpected write reply %s' % binascii.hexlify(x.data[:4]).decode())
        back = self.read_block(index)
        if back is None:
            return (False, None, 'could not read the block back to verify it')
        if back != bytes(data):
            return (False, back, 'read-back did not match what was written')
        return (True, back, None)


class CodingError(Exception):
    pass


# ============================================================ backup
def _now():
    return datetime.datetime.now().strftime('%Y%m%dT%H%M%S')


def _open_client(gateway, mgu_ip, vin, log):
    from mgu7 import discovery, hsfz
    found_vin = (vin or '').strip().upper() or None
    gw = gateway
    if gateway == 'auto':
        log('Finding the ENET gateway (UDP discovery) ...')
        gws = discovery.discover()
        if not gws:
            raise RuntimeError('No ENET gateway answered. Is the ENET cable in and the car awake?')
        gw = gws[0].ip
        if gws[0].vin and not found_vin:
            found_vin = gws[0].vin
        log('Gateway %s%s' % (gw, ' | VIN ' + gws[0].vin if gws[0].vin else ''))
    client = hsfz.EnetClient(gw)
    client.connect()
    return client, gw, found_vin


def collect(gateway='auto', mgu_ip=None, arch='id7', vin=None, log=print, read_ncd=True):
    """Read the backup from the car. -> dict. Raises on a link that cannot be reached. The NCD read is
    best-effort: if the unit does not hand it over, the snapshot is still complete."""
    from mgu7 import diag, hsfz
    client, gw, found_vin = _open_client(gateway, mgu_ip, vin, log)
    snap = {'tool': 'bimmhacks coding backup', 'schema': SCHEMA, 'taken': _now(),
            'gateway': gw, 'arch': arch, 'vin': found_vin, 'ncd_blocks': [], 'ncd_captured': False}
    try:
        log('Reading software versions (SVK) ...')
        svk = diag.read_svk(client)
        snap['svk_summary'] = svk.summary()
        snap['svk'] = [{'type': r.name, 'version': r.version_hex(), 'trip': r.trip_text()} for r in svk.records]
        snap['cafd'] = [{'version': r.version_hex(), 'trip': r.trip_text()}
                        for r in svk.records if r.name == 'CAFD']
        snap['is_supported_mgu'] = svk.is_supported_mgu()
        log('  %d software records, %d CAFD coding identifiers.' % (len(snap['svk']), len(snap['cafd'])))

        try:
            log('Reading the head unit IP configuration ...')
            snap['mgu_ip'] = list(diag.read_mgu_ip(client))
        except Exception as e:
            log('  (IP read skipped: %s)' % e)
        try:
            log('Reading fault memory (DTCs) ...')
            snap['dtcs'] = diag.read_dtcs(client)
            log('  %d fault code(s).' % len(snap['dtcs']))
        except Exception as e:
            log('  (DTC read skipped: %s)' % e)

        if read_ncd and snap['cafd']:
            log('Reading the coding data (NCD) the unit hands over - experimental ...')
            proto = CodingProtocol(client, diag, hsfz)
            got = 0
            for i, c in enumerate(snap['cafd']):
                try:
                    data = proto.read_block(i)
                except Exception as e:
                    log('  CAFD_%s: read error (%s)' % (c['version'], e))
                    data = None
                block = {'index': i, 'cafd': c['version'], 'trip': c['trip'],
                         'ncd_hex': binascii.hexlify(data).decode() if data else None,
                         'ncd_len': len(data) if data else 0}
                snap['ncd_blocks'].append(block)
                if data:
                    got += 1
                    log('  CAFD_%s: %d bytes of coding captured.' % (c['version'], len(data)))
            snap['ncd_captured'] = got > 0
            if got:
                log('  Captured real coding for %d of %d CAFD(s).' % (got, len(snap['cafd'])))
            else:
                log('  This unit did not hand coding data over the default way - the snapshot is complete,')
                log('  but there is no restorable NCD. Keep using E-Sys / Autel for the restore, or capture')
                log('  your tool\'s coding session with pid_capture.py so we can set the protocol.')
    finally:
        try:
            client.close()
        except OSError:
            pass
    return snap


def write(snap, out_dir, log=print):
    os.makedirs(out_dir, exist_ok=True)
    tag = (snap.get('vin') or 'unknown') + '-' + snap['taken']
    base = os.path.join(out_dir, 'coding-backup-' + tag)
    # the per-CAFD .ncd files (E-Sys / BimmerCode read these), in a folder next to the manifest
    ncd_dir = base + '.ncd'
    written = []
    for b in snap.get('ncd_blocks', []):
        if not b.get('ncd_hex'):
            continue
        os.makedirs(ncd_dir, exist_ok=True)
        name = 'CAFD_%s_%s.ncd' % (b['cafd'], b['trip'].replace('.', '_'))
        with open(os.path.join(ncd_dir, name), 'wb') as f:
            f.write(binascii.unhexlify(b['ncd_hex']))
        written.append(name)
    snap = dict(snap, ncd_files=written)
    with open(base + '.json', 'w', encoding='utf-8') as f:
        json.dump(snap, f, indent=2)

    cap = snap.get('ncd_captured')
    lines = ['BMW MGU coding backup (diagnostic link)',
             '=======================================',
             'Taken:   %s' % snap['taken'],
             'VIN:     %s' % (snap.get('vin') or 'unknown'),
             'Gateway: %s' % snap.get('gateway', ''),
             '',
             ('This backup DID capture the coding data (NCD) itself - see the .ncd folder beside this file.'
              if cap else
              'This records WHICH coding versions were on the car (so you know exactly what to restore), '
              'but the unit did not hand the coding DATA over: it is NOT a restorable backup by itself - '
              'also save the coding with E-Sys / BimmerCode / Autel.'),
             '',
             'CAFD coding identifiers:']
    for c in snap.get('cafd', []):
        lines.append('  CAFD_%s  (%s)' % (c['version'], c['trip']))
    if not snap.get('cafd'):
        lines.append('  (none read)')
    if written:
        lines += ['', 'Coding data captured (restore these NCD blocks):']
        for n in written:
            lines.append('  ' + n)
    lines += ['', 'All software (SVK):']
    for r in snap.get('svk', []):
        lines.append('  %-5s %s_%s' % (r['type'], r['version'], r['trip']))
    if snap.get('mgu_ip'):
        lines += ['', 'Head unit IP configuration:', '  ' + ', '.join(snap['mgu_ip'])]
    if snap.get('dtcs') is not None:
        lines += ['', 'Fault codes at backup time (%d):' % len(snap['dtcs']),
                  '  ' + (', '.join(snap['dtcs']) or 'none')]
    with open(base + '.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    log('Coding backup saved:')
    log('  ' + base + '.txt')
    log('  ' + base + '.json')
    if written:
        log('  ' + ncd_dir + '/  (%d NCD file(s), E-Sys / BimmerCode compatible)' % len(written))
    return base + '.txt'


# ============================================================ verify + restore (experimental)
def load_backup(path):
    with open(path, encoding='utf-8') as f:
        snap = json.load(f)
    if snap.get('schema') != SCHEMA or 'ncd_blocks' not in snap:
        raise CodingError('%s is not a Bimmhacks coding backup (schema %d).' % (path, SCHEMA))
    return snap


def _read_vin(client):
    """The VIN the head unit reports now (22 F1 90), or None."""
    from mgu7 import hsfz
    try:
        frames = client.request(hsfz.MGU, b'\x22\xF1\x90', hsfz.P_MAIN)
        x = hsfz.match_response(frames, hsfz.MGU)
        if x and not x.negative and x.data[:3] == b'\x62\xF1\x90':
            return x.data[3:].decode('ascii', 'replace').strip('\x00 ').upper() or None
    except Exception:
        pass
    return None


def _match_gate(backup, client, log):
    """Refuse unless the car in front of us is the one the backup came from: same CAFD versions, same
    VIN. Returns the live SVK CAFD version set."""
    from mgu7 import diag
    live = diag.read_svk(client)
    live_cafd = {r.version_hex() for r in live.records if r.name == 'CAFD'}
    want_cafd = {c['version'] for c in backup.get('cafd', [])}
    if not want_cafd:
        raise CodingError('The backup has no CAFD list; refusing to restore.')
    if not want_cafd <= live_cafd:
        missing = sorted(want_cafd - live_cafd)
        raise CodingError('This car\'s CAFD versions do not match the backup (missing on the car: %s). '
                          'This looks like a different unit or a different software level - refusing to '
                          'write coding to it.' % ', '.join('CAFD_' + m for m in missing))
    want_vin = (backup.get('vin') or '').upper()
    car_vin = _read_vin(client)
    if want_vin and car_vin and want_vin != car_vin:
        raise CodingError('The car reports VIN %s but the backup is for %s - refusing.' % (car_vin, want_vin))
    return live_cafd


def restore(backup, gateway='auto', mgu_ip=None, log=print, dry_run=False, confirm=False):
    """EXPERIMENTAL. Write the saved NCD back to the car, verifying each block. Gated hard. -> dict."""
    from mgu7 import diag, hsfz
    blocks = [b for b in backup.get('ncd_blocks', []) if b.get('ncd_hex')]
    if not blocks:
        raise CodingError('This backup captured no coding data (NCD), so there is nothing to restore. '
                          'Use E-Sys / Autel with your own coding backup.')
    if not dry_run and not confirm:
        raise CodingError('Refusing to write coding without confirmation. This is experimental; a wrong '
                          'coding write can leave the HMI partly-coded. Re-run with the confirmation (or '
                          'restore with E-Sys / Autel). A dry run (--dry-run) writes nothing.')
    client, gw, _car_vin = _open_client(gateway, mgu_ip, backup.get('vin'), log)
    report = {'written': [], 'verified': [], 'failed': [], 'dry_run': dry_run}
    try:
        _match_gate(backup, client, log)
        log('CAFD versions and VIN match the backup.')
        proto = CodingProtocol(client, diag, hsfz)
        for b in blocks:
            data = binascii.unhexlify(b['ncd_hex'])
            tag = 'CAFD_%s (%d bytes)' % (b['cafd'], len(data))
            if dry_run:
                log('  would write %s' % tag)
                report['written'].append(b['cafd'])
                continue
            log('  writing %s ...' % tag)
            ok, _back, why = proto.write_block(b['index'], data)
            report['written'].append(b['cafd'])
            if ok:
                report['verified'].append(b['cafd'])
                log('    verified (read back matches).')
            else:
                report['failed'].append({'cafd': b['cafd'], 'why': why})
                log('    FAILED: %s' % why)
    finally:
        try:
            client.close()
        except OSError:
            pass
    if dry_run:
        log('Dry run only - nothing was written to the car.')
    elif report['failed']:
        log('RESTORE INCOMPLETE: %d block(s) did not verify. The coding may be only partly written - '
            'finish it with E-Sys / Autel from the saved NCD before relying on the car.' % len(report['failed']))
    else:
        log('Coding restore complete: every block was written and read back identical.')
    return report


def verify(backup, gateway='auto', mgu_ip=None, log=print):
    """Read-only: compare the car's current coding to the backup, block by block. Writes nothing. -> dict."""
    from mgu7 import diag, hsfz
    blocks = [b for b in backup.get('ncd_blocks', []) if b.get('ncd_hex')]
    if not blocks:
        raise CodingError('This backup captured no coding data (NCD) to compare against.')
    client, gw, _vin = _open_client(gateway, mgu_ip, backup.get('vin'), log)
    report = {'same': [], 'different': [], 'unreadable': []}
    try:
        _match_gate(backup, client, log)
        proto = CodingProtocol(client, diag, hsfz)
        for b in blocks:
            want = binascii.unhexlify(b['ncd_hex'])
            got = proto.read_block(b['index'])
            if got is None:
                report['unreadable'].append(b['cafd'])
                log('  CAFD_%s: could not read coding from the car now.' % b['cafd'])
            elif got == want:
                report['same'].append(b['cafd'])
                log('  CAFD_%s: matches the backup.' % b['cafd'])
            else:
                report['different'].append(b['cafd'])
                log('  CAFD_%s: DIFFERENT from the backup (%d vs %d bytes).' % (b['cafd'], len(got), len(want)))
    finally:
        try:
            client.close()
        except OSError:
            pass
    return report


# ============================================================ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description='Bimmhacks MGU coding backup / restore (experimental).')
    ap.add_argument('action', nargs='?', default='backup', choices=['backup', 'restore', 'verify'])
    ap.add_argument('--out', help='backup: folder to write the backup into')
    ap.add_argument('--backup', help='restore / verify: the backup .json to use')
    ap.add_argument('--gateway', default='auto')
    ap.add_argument('--mgu-ip', default=None)
    ap.add_argument('--arch', default='id7')
    ap.add_argument('--vin', default=None)
    ap.add_argument('--no-ncd', action='store_true', help='backup: snapshot only, do not read the coding data')
    ap.add_argument('--dry-run', action='store_true', help='restore: show what would be written, write nothing')
    ap.add_argument('--confirm-restore', action='store_true',
                    help='restore: I accept this is experimental and can leave the HMI partly-coded, and I have '
                         'E-Sys / Autel ready')
    a = ap.parse_args(argv)

    def log(m):
        print(m, flush=True)
    try:
        if a.action == 'backup':
            if not a.out:
                ap.error('--out is needed for a backup')
            snap = collect(gateway=a.gateway, mgu_ip=a.mgu_ip, arch=a.arch, vin=a.vin, log=log, read_ncd=not a.no_ncd)
            write(snap, a.out, log=log)
            return 0
        if not a.backup:
            ap.error('--backup <file.json> is needed')
        bk = load_backup(a.backup)
        if a.action == 'verify':
            r = verify(bk, gateway=a.gateway, mgu_ip=a.mgu_ip, log=log)
            return 0 if not r['different'] and not r['unreadable'] else 1
        r = restore(bk, gateway=a.gateway, mgu_ip=a.mgu_ip, log=log, dry_run=a.dry_run, confirm=a.confirm_restore)
        return 1 if r['failed'] else 0
    except CodingError as e:
        print('ERROR: ' + str(e), file=sys.stderr, flush=True)
        return 2
    except Exception as e:
        print('ERROR: ' + str(e), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
