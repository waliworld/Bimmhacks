'use strict';
/**
 * maps.js - switch the engine map of a car whose DME ALREADY runs a multi-map tune.
 *
 * This does not flash, write or change a tune. It only sends the two switch commands the tuner's own
 * code in the DME answers (the same ones Engine Config sends; captured on an MHD multi-map car by
 * a1-mapping, PR #23 of idrive7-apps, confirmed on a car), to DME 0x12:
 *
 *   map 1..4    2F 01 <map - 1>     -> 6F 01 <map - 1>      (00..03)
 *   Max Cooling 2F 03 01 | 00       -> 6F 03 01 | 00        (on | off)
 *
 * Guards, all of which must hold before a byte leaves the head unit:
 *   - the "Map switching" setting is on. It ships OFF, and then nothing is ever sent.
 *   - the diagnostic link is up.
 *   - one command at a time.
 *   - Max Cooling ON only with the engine really running (live engine speed over MIN_COOLING_RPM;
 *     demo data never counts). OFF is always allowed.
 * A press only counts when the DME echoes the request back. A DME that is not tuned refuses it, and
 * the page says "Not available on this car". There is no read-back of the active map, so the screen
 * says "accepted", never "confirmed".
 */
const { NRC, hex, replyTo } = require('./kit/diag');

const DME = 0x12;
const MIN_COOLING_RPM = 500;
const MAPS = [1, 2, 3, 4];

class MapControl {
    /**
     * diag: kit/diag.js Diag. settings: { get(key) }. engine: { rpm() -> live rpm or null }.
     */
    constructor(opts) {
        this.diag = opts.diag;
        this.settings = opts.settings;
        this.engine = opts.engine || { rpm: () => null };
        this.log = opts.log || { info() {}, warn() {} };
        this.map = null;               // last accepted map 1..4
        this.cooling = null;           // 'ON' | 'OFF' | null
        this.note = '';
        this.noteType = 'dim';         // dim | go | warn
        this.busy = false;
    }

    get armed() { return this.settings.get('maps') === 'mhd'; }
    get linkUp() { return !!(this.diag && this.diag.available); }

    rpm() {
        const r = this.engine.rpm();
        return typeof r === 'number' && isFinite(r) ? r : null;
    }
    engineRunning() { const r = this.rpm(); return r !== null && r > MIN_COOLING_RPM; }

    _say(text, type) { this.note = text; this.noteType = type || 'dim'; }

    /** Why a command can't go right now, or null when it can. */
    _blocked() {
        if (!this.armed) return 'Disarmed in Settings - nothing is sent';
        if (!this.linkUp) return 'No diagnostic link - ignition on?';
        if (this.busy) return 'Wait for the last command';
        return null;
    }

    async setMap(n) {
        if (MAPS.indexOf(n) < 0) { this._say('Map must be 1 to 4', 'warn'); return false; }
        const why = this._blocked();
        if (why) { this._say(why, 'warn'); return false; }
        const ok = await this._send([0x01, n - 1], 'map ' + n);
        if (ok) { this.map = n; this._say('Map ' + n + ' · accepted by the DME', 'go'); }
        return ok;
    }

    async setCooling(on) {
        const why = this._blocked();
        if (why) { this._say(why, 'warn'); return false; }
        if (on && !this.engineRunning()) {
            this._say('Start the engine first · Max Cooling needs over ' + MIN_COOLING_RPM + ' rpm', 'warn');
            return false;
        }
        const ok = await this._send([0x03, on ? 0x01 : 0x00], 'cooling ' + (on ? 'on' : 'off'));
        if (ok) { this.cooling = on ? 'ON' : 'OFF'; this._say('Max Cooling ' + this.cooling.toLowerCase() + ' · accepted by the DME', 'go'); }
        return ok;
    }

    async _send(req, label) {
        this.busy = true;
        this.log.info('maps: ' + label + ' -> DME 2F ' + req.map((b) => hex(b)).join(' '));
        try {
            const r = await this.diag.physical(DME, 0x2F, req, 3000, replyTo(0x2F, req));
            if (!r || !r.length) { this._say('No answer from the DME', 'warn'); return false; }
            if (r[0] === 0x7F) {
                this.log.info('maps: DME refused (' + (NRC[r[2]] || hex(r[2])) + ')');
                this._say('Not available on this car', 'warn');
                return false;
            }
            const echoed = r[0] === 0x6F && req.every((b, i) => r[i + 1] === b);
            if (!echoed) { this._say('Not available on this car', 'warn'); return false; }
            return true;
        } catch (e) {
            this.log.warn('maps: ' + e);
            this._say('Failed: ' + (e && e.message || e), 'warn');
            return false;
        } finally {
            this.busy = false;
        }
    }
}

module.exports = { MapControl, DME, MIN_COOLING_RPM, MAPS };
