#!/usr/bin/env python3
"""pid_capture.py - which diagnostic identifiers a logging tool reads from YOUR car.

Run a logging app (MHD, BM3, ISTA, anything) on your own car over the ENET cable while Wireshark
records the link, then point this at the capture. It lists every value the tool asked the car for,
and writes a custom_pids.json for Bimmhacks Tuner so you can log the same identifiers yourself.

    python3 installer/pid_capture.py <capture.pcap | capture.pcapng | session.log> [--out <folder>]

Input:
  - a Wireshark / tcpdump capture (.pcap or .pcapng) of the ENET link (HSFZ on TCP port 6801)
  - or a session log of the bundled root tool (its TX / RX frame lines)

Output (next to the capture, or in --out):
  <name>.pids.json          every request per control unit: identifier, how often, reply size,
                            a sample reply, refusals; plus anything that was not a read (so you
                            know whether the tool did more than log)
  <name>.custom_pids.json   a Bimmhacks Tuner custom-PID file with the identifiers the car
                            ANSWERED, as raw hex entries (and OBD-II PIDs as numbers with no
                            scale), ready to drop into the app's data folder and refine

What it is and is not: it reads the traffic between a tool and your own car, which tells you which
BMW identifiers the tool reads and what the car answers. It does not know what those identifiers
mean or how the tool scales them - that you work out by lining the raw log up with the tool's own
log of the same drive. It never touches the car and never decompiles anything.

Pure Python 3.6+, standard library only.
"""
import argparse
import bisect
import json
import os
import re
import struct
import sys
from collections import OrderedDict, defaultdict, deque

HSFZ_PORT = 6801
TYPE_DIAG = 1
MAX_SKELETON = 24          # custom.js MAX
RAW_LEN_MAX = 16           # custom.js hex len limit
SAMPLE_BYTES = 32

# control unit names by diagnostic address (kit/runtime/ecus.js of idrive7-apps, MIT)
ECU = {
    0x01: 'ACSM airbag', 0x06: 'ICAM cameras', 0x07: 'SME HV battery', 0x08: 'SRR corner radar',
    0x0D: 'HKFM tailgate', 0x10: 'BDC gateway', 0x12: 'DME engine', 0x13: 'DME 2', 0x14: 'OBC charger',
    0x18: 'EGS gearbox', 0x1D: 'TFM fuel tank', 0x20: 'SRR corner radar', 0x21: 'FRR front radar',
    0x23: 'SAS assist sensors', 0x28: 'SRR corner radar', 0x29: 'DSC brakes', 0x2A: 'SRR corner radar',
    0x2C: 'USS park sensors', 0x30: 'EPS steering', 0x37: 'RAM audio', 0x38: 'VTG xDrive',
    0x39: 'DSC brake booster', 0x3A: 'EME e-motor', 0x40: 'BDC body', 0x43: 'FLM headlight',
    0x44: 'FLM headlight', 0x45: 'DCS driver camera', 0x56: 'FZD roof', 0x5D: 'KAFAS camera',
    0x5E: 'GWS gear selector', 0x60: 'KOMBI cluster', 0x61: 'ATM telematics', 0x63: 'Head unit',
    0x64: 'PDC parking', 0x67: 'ZBE controller', 0x6D: 'SM seat module', 0x76: 'VDP dampers',
    0x78: 'IHKA climate', 0xDF: 'all units (functional)',
}
SERVICE = {
    0x10: 'DiagnosticSessionControl', 0x11: 'ECUReset', 0x14: 'ClearDiagnosticInformation',
    0x19: 'ReadDTCInformation', 0x22: 'ReadDataByIdentifier', 0x23: 'ReadMemoryByAddress',
    0x27: 'SecurityAccess', 0x28: 'CommunicationControl', 0x2C: 'DynamicallyDefineDataIdentifier',
    0x2E: 'WriteDataByIdentifier', 0x2F: 'InputOutputControlByIdentifier', 0x31: 'RoutineControl',
    0x34: 'RequestDownload', 0x36: 'TransferData', 0x37: 'RequestTransferExit', 0x3D: 'WriteMemoryByAddress',
    0x3E: 'TesterPresent', 0x85: 'ControlDTCSetting',
    0x01: 'OBD current data', 0x02: 'OBD freeze frame', 0x03: 'OBD stored DTCs', 0x04: 'OBD clear DTCs',
    0x06: 'OBD test results', 0x07: 'OBD pending DTCs', 0x09: 'OBD vehicle information', 0x0A: 'OBD permanent DTCs',
}
READS = (0x22, 0x01, 0x09, 0x19, 0x06, 0x03, 0x07, 0x0A, 0x02, 0x23, 0x3E)
NRC = {0x10: 'general reject', 0x11: 'service not supported', 0x12: 'sub-function not supported', 0x13: 'bad length',
       0x21: 'busy', 0x22: 'conditions not correct', 0x31: 'out of range', 0x33: 'security access denied',
       0x78: 'pending', 0x7E: 'not supported in session', 0x7F: 'not supported in session'}

hx = lambda b: ''.join('%02X' % x for x in b)


class Frame(object):
    __slots__ = ('ts', 'src', 'dst', 'payload', 'where')

    def __init__(self, ts, src, dst, payload, where=''):
        self.ts, self.src, self.dst, self.payload, self.where = ts, src, dst, bytes(payload), where


# ---------------------------------------------------------------------------------- capture files

def read_capture(path):
    """-> (linktype, [(ts, bytes)]) from a pcap or pcapng file."""
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] == b'\x0A\x0D\x0D\x0A':
        return read_pcapng(data)
    magics = {b'\xd4\xc3\xb2\xa1': ('<', 1e-6), b'\xa1\xb2\xc3\xd4': ('>', 1e-6),
              b'\x4d\x3c\xb2\xa1': ('<', 1e-9), b'\xa1\xb2\x3c\x4d': ('>', 1e-9)}
    if data[:4] not in magics:
        raise ValueError('not a pcap or pcapng file')
    endian, frac = magics[data[:4]]
    linktype = struct.unpack(endian + 'I', data[20:24])[0]
    out, pos = [], 24
    while pos + 16 <= len(data):
        sec, sub, incl, _orig = struct.unpack(endian + 'IIII', data[pos:pos + 16])
        pos += 16
        out.append((sec + sub * frac, data[pos:pos + incl]))
        pos += incl
    return linktype, out


def read_pcapng(data):
    pos, endian, linktypes, out = 0, '<', [], []
    while pos + 12 <= len(data):
        btype = struct.unpack(endian + 'I', data[pos:pos + 4])[0]
        if btype == 0x0A0D0D0A:                                   # section header: it sets the byte order
            endian = '<' if data[pos + 8:pos + 12] == b'\x4d\x3c\x2b\x1a' else '>'
            btype = struct.unpack(endian + 'I', data[pos:pos + 4])[0]
        blen = struct.unpack(endian + 'I', data[pos + 4:pos + 8])[0]
        if blen < 12 or pos + blen > len(data):
            break
        body = data[pos + 8:pos + blen - 4]
        if btype == 1:                                            # interface description
            linktypes.append(struct.unpack(endian + 'H', body[:2])[0])
        elif btype == 6:                                          # enhanced packet
            iface, hi, lo, caplen, _orig = struct.unpack(endian + 'IIIII', body[:20])
            ts = ((hi << 32) | lo) * 1e-6                         # the default if_tsresol (microseconds)
            out.append((iface, ts, body[20:20 + caplen]))
        elif btype == 3:                                          # simple packet
            orig = struct.unpack(endian + 'I', body[:4])[0]
            out.append((0, len(out) * 1e-3, body[4:4 + orig]))
        pos += blen
    if not linktypes:
        raise ValueError('pcapng without an interface block')
    # one link type is the normal case; a mixed capture is read with the first interface's
    return linktypes[0], [(ts, pkt) for _iface, ts, pkt in out]


def ip_packet(linktype, pkt):
    """The IPv4 packet inside a link-layer frame, or None."""
    if linktype == 1:                                             # Ethernet
        if len(pkt) < 14:
            return None
        et, off = struct.unpack('>H', pkt[12:14])[0], 14
        while et == 0x8100 or et == 0x88A8:                       # VLAN tags
            et, off = struct.unpack('>H', pkt[off + 2:off + 4])[0], off + 4
        return pkt[off:] if et == 0x0800 else None
    if linktype == 113:                                           # Linux cooked
        return pkt[16:] if struct.unpack('>H', pkt[14:16])[0] == 0x0800 else None
    if linktype == 276:                                           # Linux cooked v2
        return pkt[20:] if struct.unpack('>H', pkt[0:2])[0] == 0x0800 else None
    if linktype == 0:                                             # BSD loopback: 4-byte family
        return pkt[4:]
    if linktype in (12, 14, 101, 228):                            # raw IP
        return pkt
    return None


def tcp_segments(linktype, packets):
    """-> [(ts, (src, sport, dst, dport), seq, payload)] for every TCP segment with data."""
    out = []
    for ts, pkt in packets:
        ip = ip_packet(linktype, pkt)
        if not ip or len(ip) < 20 or ip[0] >> 4 != 4:
            continue
        ihl = (ip[0] & 15) * 4
        total = struct.unpack('>H', ip[2:4])[0]
        if ip[9] != 6 or len(ip) < ihl + 20:
            continue
        src, dst = '%d.%d.%d.%d' % tuple(ip[12:16]), '%d.%d.%d.%d' % tuple(ip[16:20])
        tcp = ip[ihl:total if 20 < total <= len(ip) else len(ip)]
        sport, dport, seq = struct.unpack('>HHI', tcp[:8])
        doff = (tcp[12] >> 4) * 4
        payload = tcp[doff:]
        if payload:
            out.append((ts, (src, sport, dst, dport), seq, payload))
    return out


def reassemble(segments):
    """Per direction: the byte stream, with (offset -> ts) marks. Retransmissions are dropped,
    overlaps trimmed; a hole in the capture is just skipped (the frames across it are lost)."""
    streams = defaultdict(list)
    for ts, key, seq, payload in segments:
        streams[key].append((seq, ts, payload))
    out = {}
    for key, segs in streams.items():
        segs.sort(key=lambda s: s[0])
        buf, marks, nxt = bytearray(), [], None
        for seq, ts, payload in segs:
            if nxt is not None:
                if seq + len(payload) <= nxt:
                    continue                                      # a retransmission
                if seq < nxt:
                    payload = payload[nxt - seq:]                 # an overlap
                    seq = nxt
            marks.append((len(buf), ts))
            buf += payload
            nxt = seq + len(payload)
        out[key] = (bytes(buf), marks)
    return out


def hsfz_frames(stream, marks, src_key):
    """HSFZ frames in a byte stream: [Frame]. A capture that starts in the middle of a frame is
    resynchronised on the first header that is followed by another plausible header."""
    offs = [m[0] for m in marks]
    ts_at = lambda off: marks[max(0, bisect.bisect_right(offs, off) - 1)][1] if marks else 0

    def plausible(at):
        if at + 6 > len(stream):
            return False
        n, t = struct.unpack('>IH', stream[at:at + 6])
        return 2 <= n <= 1048576 and t in (1, 2, 0xFF) and at + 6 + n <= len(stream)

    pos, out = 0, []
    while pos + 6 <= len(stream):
        if not plausible(pos):
            pos += 1                                              # resync
            continue
        n, t = struct.unpack('>IH', stream[pos:pos + 6])
        end = pos + 6 + n
        if end < len(stream) and not plausible(end) and n > 2:
            pos += 1
            continue
        if t == TYPE_DIAG and n >= 3:
            out.append(Frame(ts_at(pos), stream[pos + 6], stream[pos + 7], stream[pos + 8:end], src_key))
        pos = end
    return out


def frames_from_capture(path):
    linktype, packets = read_capture(path)
    segs = tcp_segments(linktype, packets)
    frames = []
    for key, (stream, marks) in reassemble(segs).items():
        if HSFZ_PORT not in (key[1], key[3]):
            continue
        frames += hsfz_frames(stream, marks, key)
    frames.sort(key=lambda f: f.ts)
    return frames


# ------------------------------------------------------------------------------ the root tool's log

JOURNAL = re.compile(r'(TX|RX)\s+([0-9A-Fa-f]{2})>([0-9A-Fa-f]{2})\s+type\s+([0-9A-Fa-f]{4}):\s*([0-9A-Fa-f ]+)')


def frames_from_journal(text):
    out = []
    for n, line in enumerate(text.splitlines()):
        m = JOURNAL.search(line)
        if not m or int(m.group(4), 16) != TYPE_DIAG:
            continue
        payload = bytes.fromhex(m.group(5).replace(' ', ''))
        out.append(Frame(float(n), int(m.group(2), 16), int(m.group(3), 16), payload, 'journal'))
    return out


# -------------------------------------------------------------------------------------- analysis

def analyze(frames):
    """Pair every request with its reply and count per unit. -> report dict."""
    pending = defaultdict(deque)                                  # (tester, unit) -> requests waiting
    reads, others = OrderedDict(), OrderedDict()
    stats = {'frames': len(frames), 'requests': 0, 'replies': 0, 'unmatched_replies': 0, 'pending': 0}

    def key_of(p):
        sid = p[0]
        if sid == 0x22 and len(p) >= 3:
            return 'did', '+'.join(hx(p[i:i + 2]) for i in range(1, len(p) - 1, 2))
        if sid in (0x01, 0x09) and len(p) >= 2:
            return 'obd%02X' % sid, '+'.join('%02X' % b for b in p[1:])
        return 'other', hx(p[:min(len(p), 5)])

    for f in frames:
        p = f.payload
        if not p:
            continue
        sid = p[0]
        if sid == 0x7F or (0x41 <= sid <= 0x7E and sid - 0x40 in SERVICE) or (sid - 0x40 in (0x01, 0x02, 0x03, 0x04, 0x06, 0x07, 0x09, 0x0A)):
            # a reply from unit f.src to tester f.dst
            if sid == 0x7F and len(p) >= 3 and p[2] == 0x78:
                stats['pending'] += 1
                continue
            q = pending[(f.dst, f.src)]
            want = p[1] if sid == 0x7F else sid - 0x40
            req = None
            for i, r in enumerate(q):
                if r.payload[0] == want:
                    req = r
                    del q[i]
                    break
            if req is None:
                stats['unmatched_replies'] += 1
                continue
            stats['replies'] += 1
            kind, ident = key_of(req.payload)
            table = reads if kind != 'other' else others
            k = (req.dst, kind, ident)
            e = table.get(k)
            if e is None:
                e = table[k] = {'unit': '0x%02X' % req.dst, 'unit_name': ECU.get(req.dst, 'ECU 0x%02X' % req.dst),
                                'kind': kind, 'id': ident, 'service': SERVICE.get(req.payload[0], 'service 0x%02X' % req.payload[0]),
                                'count': 0, 'answered': 0, 'refused': 0, 'nrc': None, 'reply_len': None, 'sample': None,
                                'request': hx(req.payload[:min(len(req.payload), 8)])}
            e['count'] += 1
            if sid == 0x7F:
                e['refused'] += 1
                e['nrc'] = '0x%02X %s' % (p[2], NRC.get(p[2], '')) if len(p) >= 3 else '?'
            else:
                e['answered'] += 1
                head = 3 if sid == 0x62 else 2 if sid in (0x41, 0x49) else 1
                data = p[head:]
                if e['sample'] is None:
                    e['reply_len'] = len(data)
                    e['sample'] = hx(data[:SAMPLE_BYTES]) + ('...' if len(data) > SAMPLE_BYTES else '')
        elif sid in SERVICE:
            stats['requests'] += 1
            pending[(f.src, f.dst)].append(f)
    for q in pending.values():                                    # requests that never got a reply
        for r in q:
            kind, ident = key_of(r.payload)
            table = reads if kind != 'other' else others
            k = (r.dst, kind, ident)
            e = table.setdefault(k, {'unit': '0x%02X' % r.dst, 'unit_name': ECU.get(r.dst, 'ECU 0x%02X' % r.dst), 'kind': kind,
                                     'id': ident, 'service': SERVICE.get(r.payload[0], ''), 'count': 0, 'answered': 0, 'refused': 0,
                                     'nrc': None, 'reply_len': None, 'sample': None, 'request': hx(r.payload[:8])})
            e['count'] += 1
            e['no_reply'] = e.get('no_reply', 0) + 1
    by_unit = OrderedDict()
    for table, name in ((reads, 'reads'), (others, 'other')):
        for (unit, _kind, _ident), e in table.items():
            u = by_unit.setdefault('0x%02X' % unit, {'name': ECU.get(unit, 'ECU 0x%02X' % unit), 'reads': [], 'other': []})
            u[name].append(e)
    for u in by_unit.values():
        u['reads'].sort(key=lambda e: -e['count'])
    writes = [e for u in by_unit.values() for e in u['other'] if int(e['request'][:2], 16) not in READS]
    return {'summary': stats, 'units': by_unit,
            'did_more_than_read': [e['service'] + ' ' + e['request'] for e in writes]}


def skeleton(report):
    """A custom_pids.json for Bimmhacks Tuner from the identifiers the car answered."""
    pids, seen = [], set()
    cands = [e for u in report['units'].values() for e in u['reads'] if e['answered'] and e['kind'] in ('did', 'obd01')]
    cands.sort(key=lambda e: -e['count'])
    for e in cands:
        if '+' in e['id']:
            continue                                              # several identifiers in one request: list only
        if e['kind'] == 'did':
            pid = {'id': 'did_%s' % e['id'].lower(), 'label': 'DID %s (%s)' % (e['id'], e['unit_name']),
                   'ecu': e['unit'], 'did': e['id'], 'type': 'hex', 'len': min(RAW_LEN_MAX, max(1, e['reply_len'] or 1)),
                   'rate': 'fast' if e['count'] >= 10 else 'slow'}
        else:
            n = e['reply_len'] or 0
            t = {1: 'u8', 2: 'u16', 4: 'u32'}.get(n)
            if not t:
                continue
            pid = {'id': 'obd_%s' % e['id'].lower(), 'label': 'OBD %s raw' % e['id'], 'ecu': e['unit'], 'obd': e['id'],
                   'type': t, 'factor': 1, 'offset': 0, 'decimals': 0, 'rate': 'fast' if e['count'] >= 10 else 'slow'}
        if pid['id'] in seen:
            continue
        seen.add(pid['id'])
        pids.append(pid)
        if len(pids) >= MAX_SKELETON:
            break
    return {'comment': 'Made by pid_capture.py from a capture of your own car. Each entry is raw: give it a type, '
                       'factor and offset once you know what it is. hex entries are logged as bytes.', 'pids': pids}


def summary_lines(report):
    s = report['summary']
    out = ['%d frames: %d requests, %d replies (%d unmatched, %d "pending")' % (s['frames'], s['requests'], s['replies'], s['unmatched_replies'], s['pending'])]
    for unit, u in report['units'].items():
        out.append('')
        out.append('%s %s: %d identifiers read%s' % (unit, u['name'], len(u['reads']), ', %d other requests' % len(u['other']) if u['other'] else ''))
        for e in u['reads']:
            state = ('%d answered' % e['answered']) if e['answered'] else ('refused: ' + (e['nrc'] or '?')) if e['refused'] else 'no reply'
            out.append('  %-7s %-26s x%-5d %s%s' % (e['service'][:7] if e['kind'] != 'did' else 'DID', e['id'], e['count'], state,
                                                  ('  reply %d bytes  e.g. %s' % (e['reply_len'], e['sample'])) if e['sample'] else ''))
        for e in u['other']:
            out.append('  %-7s %-26s x%-5d %s' % ('other', e['service'] + ' ' + e['request'], e['count'], 'refused ' + (e['nrc'] or '') if e['refused'] and not e['answered'] else ''))
    if report['did_more_than_read']:
        out.append('')
        out.append('NOTE: the tool did more than read: ' + ', '.join(report['did_more_than_read'][:8]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='Which diagnostic identifiers a logging tool reads from your car (from a capture).')
    ap.add_argument('capture', help='a .pcap / .pcapng of the ENET link, or a session log of the root tool')
    ap.add_argument('--out', help='folder for the two output files (default: next to the capture)')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    try:
        if a.capture.lower().endswith(('.pcap', '.pcapng', '.cap')):
            frames = frames_from_capture(a.capture)
        else:
            with open(a.capture, 'r', encoding='utf-8', errors='replace') as f:
                frames = frames_from_journal(f.read())
    except (OSError, ValueError, struct.error) as e:
        print('ERROR: ' + str(e), file=sys.stderr)
        return 1
    if not frames:
        print('ERROR: no HSFZ diagnostic frames found (is this a capture of the ENET link, TCP port 6801?)', file=sys.stderr)
        return 1
    report = analyze(frames)
    out_dir = a.out or os.path.dirname(os.path.abspath(a.capture))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, os.path.splitext(os.path.basename(a.capture))[0])
    with open(base + '.pids.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    sk = skeleton(report)
    with open(base + '.custom_pids.json', 'w', encoding='utf-8') as f:
        json.dump(sk, f, indent=2)
    if not a.quiet:
        for line in summary_lines(report):
            print(line)
        print('')
        print('Report:           ' + base + '.pids.json')
        print('Custom PID file:  ' + base + '.custom_pids.json  (%d entries - copy it to the Tuner\'s data folder as custom_pids.json)' % len(sk['pids']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
