'use strict';
/**
 * logger.js - writes a drive as a CSV file on the head unit, one row per sample.
 *
 *   const log = new DataLogger({ dir, columns: CHANNELS });
 *   log.start(columns);                // -> { name, path }   tuner-20261108-120000.csv; columns (optional) = the
 *                                      //    channels this log records ({ key, col }), fixed for the file
 *   log.add(values, tMs);              // values = { rpm: 3000, boost: 0.4, mhd3010: '00A1FF' ... }:
 *                                      //    a number, the hex text of a raw channel, or null / missing = blank cell
 *   const sum = log.stop();            // -> { name, path, rows, seconds, peaks, reason }
 *   log.list();                        // newest first: [{ name, size, mtime }]
 *
 * Safety nets: a file stops at maxBytes (default 4 MiB, a few hours of driving) and says so, and only
 * the newest maxFiles logs are kept, so the head unit's storage cannot fill up with logs.
 * Rows are written in small batches; a crash loses at most the last few rows.
 */
const nodeFs = require('fs');
const nodePath = require('path');

const FLUSH_ROWS = 10;
const HEX = /^[0-9A-F]{0,64}$/;
const NAME = /^tuner-\d{8}-\d{6}(_[b-z])?\.csv$/;     // _b.._z: more starts inside one second (sorts after the plain name)

const pad = (n) => String(n).padStart(2, '0');
function stamp(d) {
    return d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + '-' + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
}

class DataLogger {
    constructor(opts) {
        this.dir = opts.dir;
        this.columns = opts.columns;
        this.fs = opts.fs || nodeFs;
        this.now = opts.now || (() => new Date());
        this.maxBytes = opts.maxBytes || 4 * 1024 * 1024;
        this.maxFiles = opts.maxFiles || 20;
        this.log = opts.log || { info() {}, warn() {} };
        this._reset();
    }

    _reset() {
        this.file = null; this.path = null; this.t0 = null; this.rows = 0; this.bytes = 0;
        this.buf = []; this.peaks = {}; this.reason = null; this.lastT = 0;
    }

    get running() { return !!this.path; }
    get name() { return this.file; }
    get elapsedMs() { return this.running ? this.lastT : 0; }

    start(columns) {
        if (this.running) return { name: this.file, path: this.path };
        if (columns) this.columns = columns;
        // the header is written as it is: only plain names may be columns
        this.columns.forEach((c) => { if (!/^[A-Za-z0-9_]+$/.test(c.col)) throw new Error('bad column name: ' + c.col); });
        try { this.fs.mkdirSync(this.dir, { recursive: true }); } catch (e) { /* exists, or no permission: write tells */ }
        const name = this._nextName('tuner-' + stamp(this.now()));
        const header = 'time_s,' + this.columns.map((c) => c.col).join(',') + '\n';
        const path = nodePath.join(this.dir, name);
        this.fs.writeFileSync(path, header);
        this._reset();
        this.file = name; this.path = path; this.bytes = header.length;
        this._prune();
        this.log.info('datalog: started ' + name);
        return { name, path };
    }

    /**
     * The file name for a start in this second: the plain name, or _b, _c ... after the highest one there
     * already. Counting up from what exists (not from "is this name free") matters once old logs are pruned:
     * a freed name must not come back, or the newest log would sort as the oldest and be pruned next.
     */
    _nextName(base) {
        let names = [];
        try { names = this.fs.readdirSync(this.dir); } catch (e) { /* no folder yet */ }
        const re = new RegExp('^' + base + '_([b-z])\\.csv$');
        let top = -1;                                   // -1 none, 0 the plain name, 1 _b ... 25 _z
        names.forEach((n) => {
            if (n === base + '.csv') top = Math.max(top, 0);
            else { const m = re.exec(n); if (m) top = Math.max(top, m[1].charCodeAt(0) - 97); }
        });
        const next = Math.min(top + 1, 25);             // a 26th start in one second reuses _z
        return next === 0 ? base + '.csv' : base + '_' + String.fromCharCode(97 + next) + '.csv';
    }

    /** One sample. tMs = a clock in ms (the first sample is time 0). Returns false once the file is closed. */
    add(values, tMs) {
        if (!this.running) return false;
        if (this.t0 === null) this.t0 = tMs;
        const t = Math.max(0, (tMs - this.t0) / 1000);
        this.lastT = t * 1000;
        const cells = this.columns.map((c) => {
            const v = values ? values[c.key] : null;
            if (typeof v === 'string') return HEX.test(v) ? v : '';      // a raw channel: its bytes as hex text
            if (typeof v !== 'number' || !isFinite(v)) return '';
            const p = this.peaks[c.key] || (this.peaks[c.key] = { min: v, max: v });
            if (v < p.min) p.min = v;
            if (v > p.max) p.max = v;
            return String(Math.round(v * 1000) / 1000);
        });
        const line = t.toFixed(3) + ',' + cells.join(',') + '\n';
        this.buf.push(line);
        this.rows++;
        this.bytes += line.length;
        if (this.buf.length >= FLUSH_ROWS) this._flush();
        if (this.bytes >= this.maxBytes) { this.stop('size limit reached'); return false; }
        return true;
    }

    _flush() {
        if (!this.buf.length || !this.path) return;
        try { this.fs.appendFileSync(this.path, this.buf.join('')); } catch (e) { this.log.warn('datalog write: ' + e); }
        this.buf = [];
    }

    stop(reason) {
        if (!this.running) return null;
        this._flush();
        const sum = { name: this.file, path: this.path, rows: this.rows, seconds: Math.round(this.lastT / 100) / 10,
            peaks: this.peaks, reason: reason || 'stopped' };
        this.log.info('datalog: ' + sum.reason + ' - ' + sum.name + ', ' + sum.rows + ' rows, ' + sum.seconds + ' s');
        this._lastSummary = sum;
        this._reset();
        return sum;
    }

    /** What the last stop() produced (for the page), or null. */
    get last() { return this._lastSummary || null; }

    /** The logs on disk, newest first. */
    list() {
        let names = [];
        try { names = this.fs.readdirSync(this.dir).filter((n) => NAME.test(n)); } catch (e) { return []; }
        return names.sort().reverse().map((name) => {
            let st = null;
            try { st = this.fs.statSync(nodePath.join(this.dir, name)); } catch (e) { /* vanished */ }
            return { name, size: st ? st.size : 0, mtime: st ? st.mtimeMs || 0 : 0 };
        });
    }

    /** Keep only the newest maxFiles (the one just started counts). */
    _prune() {
        const all = this.list();
        all.slice(this.maxFiles).forEach((f) => {
            try { this.fs.unlinkSync(nodePath.join(this.dir, f.name)); this.log.info('datalog: removed old ' + f.name); } catch (e) { /* ignore */ }
        });
    }
}

module.exports = { DataLogger, stamp, NAME };
