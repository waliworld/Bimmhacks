#!/usr/bin/env python3
"""pid_capture.py against synthetic captures: a pcap, the same traffic as pcapng, and the root tool's
journal format. No car, no Wireshark needed:

    python3 installer/tests/test_pid_capture.py
"""
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'installer'))
import pid_capture as pc  # noqa: E402

TESTER_IP, CAR_IP, TPORT = '169.254.1.10', '169.254.254.36', 51000
TESTER, DME = 0xF4, 0x12


def hsfz(src, dst, payload):
    body = bytes([src, dst]) + bytes(payload)
    return struct.pack('>IH', len(body), 1) + body


def packet(src_ip, dst_ip, sport, dport, seq, payload):
    eth = b'\x02' * 6 + b'\x04' * 6 + b'\x08\x00'
    total = 20 + 20 + len(payload)
    ip = struct.pack('>BBHHHBBH4s4s', 0x45, 0, total, 1, 0, 64, 6, 0, socket.inet_aton(src_ip), socket.inet_aton(dst_ip))
    tcp = struct.pack('>HHIIBBHHH', sport, dport, seq, 0, 5 << 4, 0x18, 65535, 0, 0)
    return eth + ip + tcp + bytes(payload)


class Conversation(object):
    """Builds a tester <-> car TCP conversation of HSFZ frames with real sequence numbers."""

    def __init__(self):
        self.pkts, self.t, self.tseq, self.cseq = [], 1.0, 1000, 5000

    def tx(self, payload, split=None, junk=b'', dup=False):
        data = junk + hsfz(TESTER, DME, payload)
        parts = [data] if split is None else [data[:split], data[split:]]
        for part in parts:
            self.pkts.append((self.t, packet(TESTER_IP, CAR_IP, TPORT, pc.HSFZ_PORT, self.tseq, part)))
            if dup:
                self.pkts.append((self.t + 0.001, packet(TESTER_IP, CAR_IP, TPORT, pc.HSFZ_PORT, self.tseq, part)))
            self.tseq += len(part)
            self.t += 0.01

    def rx(self, payload):
        data = hsfz(DME, TESTER, payload)
        self.pkts.append((self.t, packet(CAR_IP, TESTER_IP, pc.HSFZ_PORT, TPORT, self.cseq, data)))
        self.cseq += len(data)
        self.t += 0.01

    def pcap(self):
        out = struct.pack('<IHHiIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
        for ts, pkt in self.pkts:
            out += struct.pack('<IIII', int(ts), int((ts % 1) * 1e6), len(pkt), len(pkt)) + pkt
        return out

    def pcapng(self):
        def block(btype, body):
            body += b'\x00' * (-len(body) % 4)
            n = len(body) + 12
            return struct.pack('<II', btype, n) + body + struct.pack('<I', n)
        out = block(0x0A0D0D0A, struct.pack('<IHHq', 0x1A2B3C4D, 1, 0, -1))
        out += block(1, struct.pack('<HHI', 1, 0, 65535))
        for ts, pkt in self.pkts:
            us = int(ts * 1e6)
            out += block(6, struct.pack('<IIIII', 0, us >> 32, us & 0xFFFFFFFF, len(pkt), len(pkt)) + pkt)
        return out


def scripted():
    """The traffic every test uses: a logger reading the engine unit, with the awkward bits in."""
    c = Conversation()
    c.tx([0x22, 0x4A, 0xB0], junk=b'\xFF\xFF\xFF')              # the capture started mid-frame: 3 junk bytes first
    c.rx([0x62, 0x4A, 0xB0, 0x0C, 0x80])
    c.tx([0x22, 0x4A, 0xB0], dup=True)                           # a retransmitted segment: counted once
    c.rx([0x62, 0x4A, 0xB0, 0x0C, 0x90])
    c.tx([0x01, 0x0C])                                           # OBD-II PID
    c.rx([0x41, 0x0C, 0x1F, 0x40])
    c.tx([0x22, 0x30, 0x10])                                     # refused
    c.rx([0x7F, 0x22, 0x31])
    c.tx([0x22, 0x58, 0x6A])                                     # pending, then an 18-byte answer
    c.rx([0x7F, 0x22, 0x78])
    c.rx([0x62, 0x58, 0x6A] + list(range(1, 19)))
    c.tx([0x2F, 0x01, 0x01])                                     # not a read
    c.rx([0x6F, 0x01, 0x01])
    c.tx([0x22, 0x4A, 0xB0, 0x58, 0x0B])                         # two identifiers in one request
    c.rx([0x62, 0x4A, 0xB0, 0xAA, 0xBB, 0x58, 0x0B, 0xCC, 0xDD])
    c.tx([0x3E, 0x00])
    c.rx([0x7E, 0x00])
    c.tx([0x22, 0x58, 0x0B], split=4)                            # one frame over two TCP segments
    c.rx([0x62, 0x58, 0x0B, 0x00, 0x11])
    c.tx([0x22, 0xF1, 0x90])                                     # never answered
    return c


class Analysis(unittest.TestCase):
    def check(self, frames):
        r = pc.analyze(frames)
        s = r['summary']
        self.assertEqual((s['requests'], s['replies'], s['unmatched_replies'], s['pending']), (10, 9, 0, 1))
        self.assertEqual(list(r['units']), ['0x12'])
        u = r['units']['0x12']
        self.assertEqual(u['name'], 'DME engine')
        by = {e['id']: e for e in u['reads']}
        self.assertEqual(by['4AB0']['count'], 2, 'the retransmission is not a request')
        self.assertEqual((by['4AB0']['answered'], by['4AB0']['reply_len'], by['4AB0']['sample']), (2, 2, '0C80'))
        self.assertEqual((by['0C']['kind'], by['0C']['reply_len'], by['0C']['sample']), ('obd01', 2, '1F40'))
        self.assertEqual((by['3010']['refused'], by['3010']['answered']), (1, 0))
        self.assertIn('out of range', by['3010']['nrc'])
        self.assertEqual(by['586A']['reply_len'], 18, 'the answer after "pending" is the reply')
        self.assertEqual(by['586A']['sample'], ''.join('%02X' % i for i in range(1, 19)))
        self.assertIn('4AB0+580B', by)
        self.assertEqual((by['580B']['answered'], by['580B']['sample']), (1, '0011'), 'the split frame was reassembled')
        self.assertEqual(by['F190'].get('no_reply'), 1)
        self.assertEqual(u['reads'][0]['id'], '4AB0', 'most-requested first')
        other = {e['request'][:2]: e for e in u['other']}
        self.assertIn('2F', other)
        self.assertIn('3E', other)
        self.assertEqual(r['did_more_than_read'], ['InputOutputControlByIdentifier 2F0101'], 'tester-present is a read, I/O control is not')
        # the skeleton: what the car answered, singles only, raw
        sk = pc.skeleton(r)['pids']
        self.assertEqual([p['id'] for p in sk], ['did_4ab0', 'obd_0c', 'did_586a', 'did_580b'])
        self.assertEqual(sk[0], {'id': 'did_4ab0', 'label': 'DID 4AB0 (DME engine)', 'ecu': '0x12', 'did': '4AB0', 'type': 'hex', 'len': 2, 'rate': 'slow'})
        self.assertEqual((sk[1]['obd'], sk[1]['type'], sk[1]['factor']), ('0C', 'u16', 1))
        self.assertEqual(sk[2]['len'], 16, 'an 18-byte answer is logged as its first 16 bytes (the custom-PID limit)')
        return r

    def test_pcap(self):
        d = tempfile.mkdtemp(prefix='pidcap-')
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, 'drive.pcap')
        with open(p, 'wb') as f:
            f.write(scripted().pcap())
        frames = pc.frames_from_capture(p)
        self.assertEqual(len(frames), 20)
        self.check(frames)

    def test_pcapng(self):
        d = tempfile.mkdtemp(prefix='pidcap-')
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, 'drive.pcapng')
        with open(p, 'wb') as f:
            f.write(scripted().pcapng())
        self.check(pc.frames_from_capture(p))

    def test_journal(self):
        lines = ['[12:00:00] === session', '[12:00:01] TX F4>12 type 0001: 22 4a b0', '[12:00:01] RX 12>F4 type 0001: 62 4a b0 0c 80',
                 '[12:00:02] TX F4>12 type 0001: 22 30 10', '[12:00:02] RX 12>F4 type 0001: 7f 22 31',
                 '[12:00:03] TX F4>12 type 0001: 31 01 a0 fd 2d 2d 2d 2d (2400 bytes)', '[12:00:03] RX 12>F4 type 0001: 71 01 a0 fd 00']
        r = pc.analyze(pc.frames_from_journal('\n'.join(lines)))
        by = {e['id']: e for e in r['units']['0x12']['reads']}
        self.assertEqual(by['4AB0']['sample'], '0C80')
        self.assertEqual(by['3010']['refused'], 1)
        self.assertEqual(r['did_more_than_read'], ['RoutineControl 3101A0FD2D2D2D2D'])

    def test_cli_writes_both_files(self):
        d = tempfile.mkdtemp(prefix='pidcap-')
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, 'mhd-drive.pcap')
        with open(p, 'wb') as f:
            f.write(scripted().pcap())
        r = subprocess.run([sys.executable, os.path.join(REPO, 'installer', 'pid_capture.py'), p], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('DID     4AB0', r.stdout)
        self.assertIn('did more than read', r.stdout)
        with open(os.path.join(d, 'mhd-drive.custom_pids.json')) as f:
            sk = json.load(f)
        self.assertEqual(len(sk['pids']), 4)
        with open(os.path.join(d, 'mhd-drive.pids.json')) as f:
            self.assertEqual(json.load(f)['summary']['requests'], 10)

    def test_not_a_capture(self):
        d = tempfile.mkdtemp(prefix='pidcap-')
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, 'x.pcap')
        with open(p, 'wb') as f:
            f.write(b'hello')
        r = subprocess.run([sys.executable, os.path.join(REPO, 'installer', 'pid_capture.py'), p], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn('not a pcap', r.stderr)
        self.assertEqual(sorted(os.listdir(d)), ['x.pcap'], 'nothing written for a bad input')


if __name__ == '__main__':
    unittest.main(verbosity=2)
