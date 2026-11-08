"""fake_enet.py - an in-memory BMW ENET gateway + head unit, for testing the root-access tooling
without a car.

It speaks just enough of the real wire protocol that the bundled idrive7-8-ssh-root code
(tools/idrive7-8-ssh-root/mgu7) talks to it exactly as it talks to a car:

  UDP 6811   discovery: any probe is answered with a datagram containing "BMWVIN" + the 17-char VIN
  TCP 6801   HSFZ diagnostics: 4-byte length, 2-byte type, source, destination, UDS payload

and answers the read-only requests the coding snapshot and `status` use:

  22 F1 01   software identification (SVK): HWEL / CAFD / BTLD / SWFL records
  22 17 2A   head unit IP configuration
  19 02 0C   fault memory (B7F8E5, the debug-mode DTC, while debug mode is on)
  22 F1 90   VIN
and, for the root tool's `enable` / `persist` flow (never a flash):
  31 01 A6 71      the enrollment challenge (64 hex characters)
  31 01 A0 FD ...  the certificate install: status 0x00 (debug mode activated), counted in .enrolled
  31 02 A0 FD      stop debug mode: B7F8E5 goes away (.debug_on = False)
  31 03 A0 FD      the routine's results
  10 xx / 3E 00    session control / tester present
With ssh_port set it also listens there (and accepts, then closes), so the tool's "is port 22
open" probe succeeds; the ssh command itself is faked by fake_ssh.sh on PATH.

Everything else gets a negative reply (7F <sid> 11). Every request received is kept in
FakeCar.requests as (destination address, payload bytes), so a test can assert what was - and,
more to the point, was NOT - sent to the "car".
"""
import socket
import struct
import threading

TESTER = 0xF4
MGU = 0x63
TYPE_DIAG = 1

DEFAULT_SVK = [          # (record type, version, trip a, b, c)   types: 1 HWEL, 5 CAFD, 6 BTLD, 8 SWFL, 13 SWFK
    (1, 0x00000001, 1, 2, 3),
    (6, 0x00003F36, 30, 16, 6),      # the MGU bootloader the tool requires
    (5, 0x000000AB, 12, 34, 56),
    (5, 0x000000AC, 12, 34, 57),
    (8, 0x000056E5, 30, 16, 2),
]


def svk_record(t, version, a, b, c):
    return struct.pack('>BIBBB', t, version, a, b, c)


class FakeCar:
    CHALLENGE = '00112233445566778899AABBCCDDEEFF' * 2

    def __init__(self, vin='WBA12345678901234', svk=None, dtcs=None, mgu_ip='169.254.36.47',
                 host='127.0.0.1', tcp_port=6801, udp_port=6811, ssh_port=None, debug_on=True):
        self.vin = vin
        self.svk = DEFAULT_SVK if svk is None else svk
        self.dtcs = [('480A01', 0x09)] if dtcs is None else list(dtcs)
        self.debug_on = debug_on                  # the B7F8E5 DTC is reported while this is on
        self.mgu_ip = mgu_ip
        self.host, self.tcp_port, self.udp_port, self.ssh_port = host, tcp_port, udp_port, ssh_port
        self.requests = []
        self.probes = 0
        self.enrolled = 0                        # A0 FD installs accepted
        self.ssh_connects = 0                    # connections to the fake port 22
        self._stop = threading.Event()
        self._threads = []
        self._socks = []

    # ---- lifecycle ----------------------------------------------------------------------------
    def start(self):
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp.bind((self.host, self.tcp_port))
        tcp.listen(4)
        tcp.settimeout(0.2)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind((self.host, self.udp_port))
        udp.settimeout(0.2)
        self._socks = [tcp, udp]
        if self.ssh_port is not None:
            sshs = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sshs.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sshs.bind((self.host, self.ssh_port))
            sshs.listen(4)
            sshs.settimeout(0.2)
            self._socks.append(sshs)
            t = threading.Thread(target=self._serve_ssh_port, args=(sshs,), daemon=True)
            t.start()
            self._threads.append(t)
        for fn, s in ((self._serve_tcp, tcp), (self._serve_udp, udp)):
            t = threading.Thread(target=fn, args=(s,), daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self):
        self._stop.set()
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=2)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    # ---- what the car says --------------------------------------------------------------------
    def answer(self, dst, payload):
        """UDS reply bytes for a request, or None for no reply."""
        sid = payload[0]
        if payload[:3] == b'\x22\xF1\x01':
            body = b''.join(svk_record(*r) for r in self.svk)
            return b'\x62\xF1\x01' + body
        if payload[:3] == b'\x22\x17\x2A':
            octets = b''.join(bytes(int(x) for x in ip.split('.')) for ip in
                              (self.mgu_ip, '169.254.254.36', '169.254.0.1'))
            return b'\x62\x17\x2A\x00' + octets
        if payload[:3] == b'\x22\xF1\x90':
            return b'\x62\xF1\x90' + self.vin.encode('ascii')
        if payload[:3] == b'\x19\x02\x0C':
            dtcs = ([('B7F8E5', 0x08)] if self.debug_on else []) + self.dtcs
            recs = b''.join(bytes.fromhex(code) + bytes([status]) for code, status in dtcs)
            return b'\x59\x02\xFF' + recs
        if payload[:4] == b'\x31\x01\xA6\x71':                 # enrollment challenge
            return b'\x71\x01\xA6\x71' + self.CHALLENGE.encode('ascii')
        if payload[:4] == b'\x31\x01\xA0\xFD':                 # certificate install -> debug mode on
            self.enrolled += 1
            self.debug_on = True
            return b'\x71\x01\xA0\xFD\x00'
        if payload[:4] == b'\x31\x02\xA0\xFD':                 # stop debug mode
            self.debug_on = False
            return b'\x71\x02\xA0\xFD'
        if payload[:4] == b'\x31\x03\xA0\xFD':
            return b'\x71\x03\xA0\xFD' + (b'\x00' if self.debug_on else b'\x01')
        if sid == 0x10 and len(payload) >= 2:
            return bytes((0x50, payload[1], 0x00, 0x32, 0x01, 0xF4))
        if sid == 0x3E:
            return b'\x7E\x00'
        return bytes((0x7F, sid, 0x11))                       # service not supported

    # ---- wire ---------------------------------------------------------------------------------
    def _serve_udp(self, sock):
        while not self._stop.is_set():
            try:
                _data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            self.probes += 1
            try:
                sock.sendto(b'\x00\x00\x00\x00BMWVIN' + self.vin.encode('ascii') + b'\x00', addr)
            except OSError:
                pass

    def _serve_ssh_port(self, srv):
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.ssh_connects += 1
            try:
                conn.close()
            except OSError:
                pass

    def _serve_tcp(self, srv):
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            t = threading.Thread(target=self._client, args=(conn,), daemon=True)
            t.start()
            self._threads.append(t)

    def _client(self, conn):
        try:
            self._client_loop(conn)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _client_loop(self, conn):
        conn.settimeout(0.2)
        buf = b''
        while not self._stop.is_set():
            try:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
            except socket.timeout:
                pass
            except OSError:
                return
            while len(buf) >= 6:
                length, ftype = struct.unpack('>IH', buf[:6])
                if len(buf) < 6 + length:
                    break
                body, buf = buf[6:6 + length], buf[6 + length:]
                if ftype != TYPE_DIAG or len(body) < 3:
                    continue
                src, dst, payload = body[0], body[1], body[2:]
                self.requests.append((dst, bytes(payload)))
                reply = self.answer(dst, payload)
                if reply is None:
                    continue
                out = bytes((dst, src)) + reply
                try:
                    conn.sendall(struct.pack('>IH', len(out), TYPE_DIAG) + out)
                except OSError:
                    return

    # ---- helpers for assertions ---------------------------------------------------------------
    def services(self):
        """The set of UDS service ids the tool sent (e.g. {0x22, 0x19})."""
        return {p[0] for _, p in self.requests if p}
