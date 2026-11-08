#!/usr/bin/env python3
"""coding_backup.py - read and save the head unit's identity and software state over the ENET
diagnostic link, BEFORE the car is rooted or flashed.

This is a SNAPSHOT / RECORD, not a restorable coding backup. Over the diagnostic link (before
root SSH exists) the car will hand out:

  - the SVK: every software module and, crucially, the exact CAFD coding-version identifiers;
  - the VIN;
  - the head unit's IP configuration;
  - the current fault memory (DTCs).

That tells you EXACTLY which CAFD versions to put back if a flash wipes the coding. It does NOT
contain the coding data itself (the NCD/FA blocks), so it cannot by itself re-code the car - a
real restorable backup must still be taken with BimmerCode or E-Sys. This file makes that restore
reliable by recording precisely what was on the car.

    python coding_backup.py --out <dir> [--gateway auto] [--mgu-ip <ip>] [--arch id7] [--vin <VIN>]

Reuses the bundled idrive7-8-ssh-root 'mgu7' package; needs the `cryptography` package it needs.
Exit 0 on success, 1 on failure.
"""
import argparse, datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SSHROOT_DIR = os.environ.get('BIMMHACKS_SSHROOT_DIR') or os.path.join(
    os.path.dirname(HERE), 'tools', 'idrive7-8-ssh-root')
if SSHROOT_DIR not in sys.path:
    sys.path.insert(0, SSHROOT_DIR)


def _now():
    return datetime.datetime.now().strftime('%Y%m%dT%H%M%S')


def collect(gateway='auto', mgu_ip=None, arch='id7', vin=None, log=print):
    """Read the snapshot from the car. -> dict. Raises on a link that cannot be reached."""
    from mgu7 import diag, discovery, hsfz

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
    snap = {'tool': 'bimmhacks coding snapshot', 'schema': 1, 'taken': _now(),
            'gateway': gw, 'arch': arch, 'vin': found_vin,
            'note': 'Diagnostic snapshot of software/CAFD versions, VIN, IP and faults. NOT a '
                    'restorable coding backup - take that with BimmerCode or E-Sys as well.'}
    try:
        log('Reading software versions (SVK) ...')
        svk = diag.read_svk(client)
        snap['svk_summary'] = svk.summary()
        snap['svk'] = [{'type': r.name, 'version': r.version_hex(), 'trip': r.trip_text()}
                       for r in svk.records]
        snap['cafd'] = [{'version': r.version_hex(), 'trip': r.trip_text()}
                        for r in svk.records if r.name == 'CAFD']
        snap['is_supported_mgu'] = svk.is_supported_mgu()
        log('  %d software records, %d CAFD coding identifiers.'
            % (len(snap['svk']), len(snap['cafd'])))

        try:
            log('Reading the head unit IP configuration ...')
            ips = diag.read_mgu_ip(client)
            snap['mgu_ip'] = list(ips)
        except Exception as e:  # non-fatal
            log('  (IP read skipped: %s)' % e)

        try:
            log('Reading fault memory (DTCs) ...')
            snap['dtcs'] = diag.read_dtcs(client)
            log('  %d fault code(s).' % len(snap['dtcs']))
        except Exception as e:
            log('  (DTC read skipped: %s)' % e)
    finally:
        try:
            client.close()
        except OSError:
            pass
    return snap


def write(snap, out_dir, log=print):
    os.makedirs(out_dir, exist_ok=True)
    tag = (snap.get('vin') or 'unknown') + '-' + snap['taken']
    base = os.path.join(out_dir, 'coding-snapshot-' + tag)
    with open(base + '.json', 'w', encoding='utf-8') as f:
        json.dump(snap, f, indent=2)
    lines = ['BMW MGU coding snapshot (diagnostic link)',
             '==========================================',
             'Taken:   %s' % snap['taken'],
             'VIN:     %s' % (snap.get('vin') or 'unknown'),
             'Gateway: %s' % snap.get('gateway', ''),
             '',
             'IMPORTANT: this records WHICH coding versions were on the car, so you know exactly',
             'what to restore. It is NOT a restorable backup - also save the coding with BimmerCode',
             'or E-Sys before flashing.',
             '',
             'CAFD coding identifiers (restore these versions if coding is wiped):']
    for c in snap.get('cafd', []):
        lines.append('  CAFD_%s  (%s)' % (c['version'], c['trip']))
    if not snap.get('cafd'):
        lines.append('  (none read)')
    lines += ['', 'All software (SVK):']
    for r in snap.get('svk', []):
        lines.append('  %-5s %s_%s' % (r['type'], r['version'], r['trip']))
    if snap.get('mgu_ip'):
        lines += ['', 'Head unit IP configuration:', '  ' + ', '.join(snap['mgu_ip'])]
    if snap.get('dtcs') is not None:
        lines += ['', 'Fault codes at snapshot time (%d):' % len(snap['dtcs']),
                  '  ' + (', '.join(snap['dtcs']) or 'none')]
    with open(base + '.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    log('Coding snapshot saved:')
    log('  ' + base + '.txt')
    log('  ' + base + '.json')
    return base + '.txt'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', required=True, help='folder to write the snapshot into')
    ap.add_argument('--gateway', default='auto')
    ap.add_argument('--mgu-ip', default=None)
    ap.add_argument('--arch', default='id7')
    ap.add_argument('--vin', default=None)
    a = ap.parse_args(argv)

    def log(m):
        print(m, flush=True)
    try:
        snap = collect(gateway=a.gateway, mgu_ip=a.mgu_ip, arch=a.arch, vin=a.vin, log=log)
        write(snap, a.out, log=log)
        return 0
    except Exception as e:
        print('ERROR: ' + str(e), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
