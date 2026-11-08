'use strict';
/**
 * faults.js - the engine unit's (DME, 0x12) fault memory: read it, and clear it after two presses.
 *
 * Reuses the Fault Scanner's own requests, restricted to ONE unit so a tuner can look at the engine
 * without touching the rest of the car:
 *   read   19 02 0C               ReadDTCInformation, reportDTCByStatusMask (the Fault Scanner's default mask)
 *   clear  14 FF FF FF            ClearDiagnosticInformation; a unit that refuses for its session or
 *                                 conditions is asked again in the extended session (10 03), then the
 *                                 default session is restored (10 01), as the Fault Scanner does
 *
 * Only codes are shown (six hex digits and their state). Texts for them are in the Fault Scanner app.
 *
 * Clearing needs two presses within CONFIRM_MS: the first only arms it (nothing is sent), the second
 * sends. Reading is always allowed; nothing here writes anything but the clear.
 */
const { parseDtcReply, statusInfo, hex, NRC } = require('./kit/diag');

const DME = 0x12;
const READ_PARAMS = [0x02, 0x0C];
const CONFIRM_MS = 8000;
const RETRY_NRC = [0x22, 0x31, 0x33, 0x7E, 0x7F];      // refusals worth one more try in the extended session

const acceptRead = (u) => (u[0] === 0x59 && u[1] === READ_PARAMS[0]) || (u[0] === 0x7F && u[1] === 0x19);
const acceptClear = (u) => u[0] === 0x54 || (u[0] === 0x7F && u[1] === 0x14);
const acceptSession = (n) => (u) => (u[0] === 0x50 && u[1] === n) || (u[0] === 0x7F && u[1] === 0x10);

class FaultControl {
    /** diag: kit/diag.js Diag (physical(), available). now: a clock, for the two-press window. */
    constructor(opts) {
        this.diag = opts.diag;
        this.log = opts.log || { info() {}, warn() {} };
        this.ecu = opts.ecu == null ? DME : opts.ecu;
        this.now = opts.now || Date.now;
        this.faults = null;            // null = not read yet, else [{ code, status, label, sev }]
        this.readAt = 0;
        this.note = '';
        this.busy = false;
        this.armedUntil = 0;
    }

    get linkUp() { return !!(this.diag && this.diag.available); }
    get armed() { return this.now() < this.armedUntil; }

    async read() {
        if (this.busy) return this.faults;
        if (!this.linkUp) { this.note = 'No diagnostic link - ignition on?'; return this.faults; }
        this.busy = true; this.armedUntil = 0; this.note = 'Reading...';
        try {
            const uds = await this.diag.physical(this.ecu, 0x19, READ_PARAMS, 6000, acceptRead);
            const p = parseDtcReply(uds, READ_PARAMS);
            if (!uds) { this.note = 'The engine unit did not answer'; }
            else if (!p.ok) { this.note = 'Refused: ' + (p.nrc != null ? (NRC[p.nrc] || 'NRC ' + hex(p.nrc)) : 'unexpected reply'); }
            else {
                this.faults = p.dtcs.map((d) => Object.assign({ code: d.code, status: d.status }, statusInfo(d.status)));
                this.readAt = this.now();
                this.note = this.faults.length ? this.faults.length + ' fault' + (this.faults.length === 1 ? '' : 's') : 'No faults stored';
            }
        } catch (e) {
            this.note = 'Read failed: ' + (e && e.message || e);
            this.log.warn('faults read: ' + e);
        }
        this.busy = false;
        return this.faults;
    }

    /**
     * First call: arms the clear and says so (nothing is sent). Second call inside CONFIRM_MS: clears.
     * Resolves with { sent, ok, note }.
     */
    async clear() {
        if (this.busy) return { sent: false, ok: false, note: this.note };
        if (!this.linkUp) { this.note = 'No diagnostic link - ignition on?'; return { sent: false, ok: false, note: this.note }; }
        if (!this.armed) {
            this.armedUntil = this.now() + CONFIRM_MS;
            this.note = 'Press Clear again within ' + Math.round(CONFIRM_MS / 1000) + ' s to erase the engine unit\'s faults';
            return { sent: false, ok: false, note: this.note };
        }
        this.armedUntil = 0;
        this.busy = true; this.note = 'Clearing...';
        let res;
        try { res = await this._clearOnce(); } catch (e) { res = { ok: false, why: String(e && e.message || e) }; this.log.warn('faults clear: ' + e); }
        this.busy = false;
        if (res.ok) { this.faults = null; this.note = 'Cleared - read again to see what comes back'; }
        else this.note = 'Not cleared: ' + res.why;
        return { sent: true, ok: res.ok, note: this.note };
    }

    async _clearOnce() {
        const d = this.diag, ecu = this.ecu, req = [0xFF, 0xFF, 0xFF];
        const ask = () => d.physical(ecu, 0x14, req, 8000, acceptClear);
        const session = (n) => d.physical(ecu, 0x10, [n], 3000, acceptSession(n));
        let r = await ask();
        if (!r) r = await ask();                                   // no answer: once more
        if (r && r[0] === 0x7F && RETRY_NRC.indexOf(r[2]) >= 0) {
            const first = r;
            const ext = await session(0x03);
            if (ext && ext[0] === 0x50) {
                r = (await ask()) || first;
                await session(0x01);                               // back to the default session
            }
        }
        if (r && r[0] === 0x54) return { ok: true };
        const why = !r ? 'no answer'
            : r[0] === 0x7F ? (r[2] === 0x22 ? 'refused now (ignition on, engine off?)' : NRC[r[2]] || 'NRC ' + hex(r[2]))
                : 'unexpected reply';
        return { ok: false, why };
    }
}

module.exports = { FaultControl, DME, READ_PARAMS, CONFIRM_MS, acceptRead, acceptClear };
