'use strict';
/**
 * custom.js - the owner's own log PIDs: custom_pids.json in the app's data folder.
 *
 *   { "pids": [
 *       { "id": "boost_dev", "label": "Boost deviation", "unit": "kPa", "decimals": 1,
 *         "ecu": "0x12", "did": "4536", "type": "s16", "at": 0, "factor": 0.0125, "offset": 0,
 *         "min": -300, "max": 300, "rate": "fast" },
 *       { "id": "mystery", "label": "Mystery value", "ecu": 18, "did": "37FE", "type": "hex", "len": 4 },
 *       { "id": "rpm_obd", "label": "RPM (OBD)", "obd": "0C", "type": "u16", "factor": 0.25 } ] }
 *
 *   id        a-z, 0-9, _ ; starts with a letter, up to 24; the CSV column is c_<id>
 *   label     shown on the PIDs page (default: the id), unit (default none), decimals 0..6 (default 1)
 *   ecu       the control unit's diagnostic address: 18, "18" or "0x12" (default 0x12, the engine unit);
 *             not 0xDF (that is "all units": a read goes to one unit)
 *   did       4 hex digits, read with UDS 22  -or-  obd: 2 hex digits, read with OBD service 01
 *   type      u8 s8 u16 s16 u32 s32 (a number: value = raw * factor + offset) or hex (the raw bytes, no meaning
 *             given: len 1..16, default 4; did only)
 *   at        byte offset into the answer's data (0..32, default 0), factor (default 1), offset (default 0)
 *   min / max an answer outside is "no value" (optional)
 *   rate      "slow" (default) or "fast": fast ones are read on every round, slow ones a few per round
 *
 * This only ever describes READS: everything goes through kit/readonly-diag.js, which cannot send a write.
 * A bad entry is skipped with a message that names it; the good ones still load. The file is limited to
 * 64 KiB and MAX entries.
 */
const { TIMEOUT_MS, SIZE } = require('./pids');

const MAX = 24;
const MAX_BYTES = 64 * 1024;
const TYPES = Object.keys(SIZE).concat(['hex']);
const ID = /^[a-z][a-z0-9_]{0,23}$/;
const HEX4 = /^[0-9A-Fa-f]{4}$/;
const HEX2 = /^[0-9A-Fa-f]{2}$/;
const ALL_UNITS = 0xDF;

const isNum = (x) => typeof x === 'number' && isFinite(x);
const isInt = (x, lo, hi) => Number.isInteger(x) && x >= lo && x <= hi;

function ecuOf(v) {
    if (v === undefined) return 0x12;
    if (typeof v === 'number') return Number.isInteger(v) ? v : NaN;
    if (typeof v === 'string' && /^0x[0-9a-f]{1,2}$/i.test(v)) return parseInt(v, 16);
    if (typeof v === 'string' && /^[0-9]{1,3}$/.test(v)) return parseInt(v, 10);
    return NaN;
}

/** One entry -> { channel } or { error: 'why' }. */
function entry(e, n) {
    const where = 'pids[' + n + ']' + (e && typeof e.id === 'string' ? ' (' + e.id + ')' : '');
    const bad = (why) => ({ error: where + ': ' + why });
    if (!e || typeof e !== 'object' || Array.isArray(e)) return bad('not an object');
    if (typeof e.id !== 'string' || !ID.test(e.id)) return bad('id must be a-z, 0-9, _ (a letter first, up to 24 characters)');
    if (TYPES.indexOf(e.type) < 0) return bad('type must be one of ' + TYPES.join(' '));
    const ecu = ecuOf(e.ecu);
    if (!isInt(ecu, 0, 255)) return bad('ecu must be a number 0-255 or "0x12"');
    if (ecu === ALL_UNITS) return bad('ecu 0xDF is "all units"; a read goes to one unit');
    const hasDid = e.did !== undefined, hasObd = e.obd !== undefined;
    if (hasDid === hasObd) return bad('give exactly one of did (4 hex digits) or obd (2 hex digits)');
    if (hasDid && !(typeof e.did === 'string' && HEX4.test(e.did))) return bad('did must be 4 hex digits, e.g. "4536"');
    if (hasObd && !(typeof e.obd === 'string' && HEX2.test(e.obd))) return bad('obd must be 2 hex digits, e.g. "0C"');
    const label = e.label === undefined ? e.id : e.label;
    if (typeof label !== 'string' || !label.length || label.length > 40 || /[^\x20-\x7e°²µ]/.test(label)) return bad('label must be 1-40 printable characters');
    const unit = e.unit === undefined ? '' : e.unit;
    if (typeof unit !== 'string' || unit.length > 8 || /[^\x20-\x7e°²µ]/.test(unit)) return bad('unit must be up to 8 printable characters');
    const d = e.decimals === undefined ? 1 : e.decimals;
    if (!isInt(d, 0, 6)) return bad('decimals must be 0 to 6');
    const rate = e.rate === undefined ? 'slow' : e.rate;
    if (rate !== 'slow' && rate !== 'fast') return bad('rate must be "slow" or "fast"');
    const at = e.at === undefined ? 0 : e.at;
    if (!isInt(at, 0, 32)) return bad('at must be 0 to 32');
    const base = { key: 'c_' + e.id, col: 'c_' + e.id, label, unit, d, pack: 'custom', rate };

    if (e.type === 'hex') {
        if (!hasDid) return bad('type hex needs a did (obd is for numbers)');
        const len = e.len === undefined ? 4 : e.len;
        if (!isInt(len, 1, 16)) return bad('len must be 1 to 16');
        return { channel: Object.assign(base, { unit: unit || 'hex', ecu, raw: { did: e.did.toUpperCase(), len, at } }) };
    }
    const factor = e.factor === undefined ? 1 : e.factor, offset = e.offset === undefined ? 0 : e.offset;
    if (!isNum(factor) || factor === 0) return bad('factor must be a non-zero number');
    if (!isNum(offset)) return bad('offset must be a number');
    if (e.min !== undefined && !isNum(e.min)) return bad('min must be a number');
    if (e.max !== undefined && !isNum(e.max)) return bad('max must be a number');
    if (e.min !== undefined && e.max !== undefined && e.min > e.max) return bad('min is above max');
    const c = { ecu, bytes: SIZE[e.type], at, factor, offset, signed: e.type[0] === 's', timeoutMs: TIMEOUT_MS };
    if (e.min !== undefined) c.min = e.min;
    if (e.max !== undefined) c.max = e.max;
    if (hasDid) c.did = e.did.toUpperCase(); else c.obd = e.obd.toUpperCase();
    return { channel: Object.assign(base, { cand: [c] }) };
}

/** The file's text -> { channels: [...], errors: [...] }. Never throws. */
function parseCustom(text) {
    const out = { channels: [], errors: [] };
    if (text === null || text === undefined || text === '') return out;
    if (typeof text !== 'string' || text.length > MAX_BYTES) { out.errors.push('custom_pids.json is larger than ' + MAX_BYTES / 1024 + ' KiB'); return out; }
    let doc;
    try { doc = JSON.parse(text.replace(/^﻿/, '')); } catch (e) { out.errors.push('custom_pids.json is not valid JSON: ' + e.message); return out; }
    const list = Array.isArray(doc) ? doc : doc && Array.isArray(doc.pids) ? doc.pids : null;
    if (!list) { out.errors.push('custom_pids.json needs a "pids" list'); return out; }
    const seen = {};
    list.forEach((e, n) => {
        if (out.channels.length >= MAX) { if (n === MAX) out.errors.push('only the first ' + MAX + ' pids are used (' + (list.length - MAX) + ' ignored)'); return; }
        const r = entry(e, n);
        if (r.error) { out.errors.push(r.error); return; }
        if (seen[r.channel.key]) { out.errors.push('pids[' + n + ']: id "' + e.id + '" is used twice'); return; }
        seen[r.channel.key] = true;
        out.channels.push(r.channel);
    });
    return out;
}

module.exports = { parseCustom, MAX, MAX_BYTES };
