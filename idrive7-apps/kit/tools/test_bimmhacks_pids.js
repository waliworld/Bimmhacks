'use strict';

// Bimmhacks Tuner's PID packs, the custom-PID file, the channel list, the datalogger's raw / dynamic columns and
// the poller - the poller against the REAL kit/readonly-diag.js and kit/dids.js, with only the TASU transport under
// them faked. No SDK, vehicle or capture needed:
//   node kit/tools/test_bimmhacks_pids.js

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const Module = require('module');

const ROOT = path.join(__dirname, '..', '..');
const APP = path.join(ROOT, 'apps/bimmhacks_tuner');
const DIAG = path.join(ROOT, 'kit/runtime/diag.js');

// ---- the transport under ReadOnlyDiag: scripted per test ---------------------------------------------------
const wire = { handler: null, sent: [] };
class FakeDiag {
    constructor() { this.available = true; this.p = {}; }
    async init() { return this; }
    on() {}
    async physical(target, sid, data, timeout, accept) {
        wire.sent.push({ target, sid, data: data.slice(), timeout });
        return wire.handler ? wire.handler(target, sid, data) : null;
    }
}
const load = Module._load;
Module._load = function (req, parent) {
    if (/^\.\/kit\//.test(req)) req = path.join(ROOT, 'kit/runtime', req.slice(6));
    const out = load.call(this, req, parent);
    try { if (Module._resolveFilename(req, parent) === DIAG) return Object.assign({}, out, { Diag: FakeDiag }); } catch (e) { /* not a file */ }
    return out;
};

const PIDS = require(path.join(APP, 'pids'));
const CH = require(path.join(APP, 'channels'));
const { parseCustom, MAX } = require(path.join(APP, 'custom'));
const { Poller, REFUSED_MS, SILENT_MS, MIN_AGE_MS } = require(path.join(APP, 'poller'));
const { DataLogger } = require(path.join(APP, 'logger'));
const { ReadOnlyDiag } = require(path.join(ROOT, 'kit/runtime/readonly-diag'));
const { DidReader } = require(path.join(ROOT, 'kit/runtime/dids'));

const quiet = { info() {}, warn() {}, error() {} };
const near = (a, b, eps) => Math.abs(a - b) <= (eps || 1e-6);
let groups = 0;
const ok = (name) => { groups++; console.log('  ok  ' + name); };
const ALL = [].concat(PIDS.TUNING, PIDS.ENGINE, PIDS.CYLINDERS, PIDS.MHD);

/* ------------------------------------------------------------------ the tables match BMW's catalogue */
(function catalogue() {
    const cat = JSON.parse(fs.readFileSync(path.join(ROOT, 'apps/live_data/builtin/DME8FF_R.json'), 'utf8'));
    // catalogue unit -> { my unit: how many of mine are one of theirs }
    const UNITS = {
        hPa: { kPa: 0.1, bar: 0.001, hPa: 1 }, MPa: { bar: 10, MPa: 1 }, 'kg/h': { 'kg/h': 1 }, '°C': { '°C': 1, '°': 1 },
        '°CA': { '°CA': 1 }, '%': { '%': 1 }, V: { V: 1 }, Nm: { Nm: 1 }, 'rad/s²': { 'rad/s²': 1 },
    };
    let checked = 0;
    ALL.forEach((c) => (c.cand || []).forEach((cd) => {
        if (!cd.from) return;
        const e = cat[cd.from.did];
        assert.ok(e, c.key + ': ' + cd.from.did + ' is not in the DME catalogue');
        assert.strictEqual(e.f.length, 1, cd.from.did + ': one field expected');
        const f = e.f[0];
        assert.strictEqual(f.at, 0, cd.from.did + ': field offset');
        assert.strictEqual(f.t, cd.from.type, cd.from.did + ': type');
        assert.strictEqual(cd.from.k, f.k == null ? 1 : f.k, cd.from.did + ': catalogue factor');
        assert.strictEqual(cd.from.o, f.o || 0, cd.from.did + ': catalogue offset');
        assert.strictEqual(cd.bytes, PIDS.SIZE[f.t], cd.from.did + ': bytes');
        assert.strictEqual(cd.signed, f.t[0] === 's', cd.from.did + ': signedness');
        assert.strictEqual(cd.did, cd.from.did);
        // the factor / offset I use = theirs times the unit conversion, and that conversion is the right one
        assert.ok(near(cd.factor, cd.from.k * cd.from.scale, Math.abs(cd.factor) * 1e-9), cd.from.did + ': factor ' + cd.factor);
        assert.ok(near(cd.offset, cd.from.o * cd.from.scale, 1e-9), cd.from.did + ': offset');
        const table = UNITS[f.u];
        if (f.u === undefined) assert.ok(['', 'x'].indexOf(c.unit) >= 0 && cd.from.scale === 1, cd.from.did + ': a unit-less value kept as it is');
        else {
            assert.ok(table && table[c.unit] !== undefined, cd.from.did + ': catalogue unit ' + f.u + ' shown as ' + c.unit);
            assert.ok(near(cd.from.scale, table[c.unit], 1e-12), cd.from.did + ': ' + f.u + ' -> ' + c.unit + ' is x' + table[c.unit] + ', not x' + cd.from.scale);
        }
        assert.ok(cd.min < cd.max, cd.from.did + ': plausible range');
        checked++;
    }));
    assert.strictEqual(checked, 55, 'catalogue-sourced identifiers: 23 tuning + 14 engine + 18 cylinder');
    // a deliberately wrong table entry must be caught (the check can fail)
    const wrong = PIDS.bmw('4205', 'u16', 0.078125, 0, 1, 20, 520)[0];       // forgot hPa -> kPa
    assert.ok(!near(wrong.factor, 0.078125 * 0.1, 1e-9));
    ok('catalogue: ' + checked + ' BMW identifiers match the DME SGBD catalogue (type, offset, factor, unit conversion)');
})();

/* ------------------------------------------------------------------ pack table sanity */
(function tables() {
    const keys = ALL.map((c) => c.key), cols = ALL.map((c) => c.col);
    assert.strictEqual(new Set(keys).size, keys.length, 'unique keys');
    assert.strictEqual(new Set(cols).size, cols.length, 'unique CSV columns');
    cols.forEach((c) => assert.ok(/^[a-z0-9_]+$/.test(c), c));
    ALL.forEach((c) => {
        assert.ok(c.rate === 'fast' || c.rate === 'slow', c.key);
        assert.ok(c.cand ? c.cand.length > 0 : c.raw, c.key + ' reads something');
        (c.cand || []).forEach((cd) => {
            assert.ok(!!cd.did !== !!cd.obd, c.key + ': a did or an obd');
            assert.ok(/^[0-9A-F]{4}$/.test(cd.did || 'ABCD') && /^[0-9A-F]{2}$/.test(cd.obd || 'AB'), c.key);
            assert.ok(cd.timeoutMs <= 1200, c.key + ': a short wait, not DidReader\'s 4 s');
        });
    });
    // every standard PID is offered both as UDS 22 F4xx and as OBD service 01
    ALL.forEach((c) => (c.cand || []).filter((cd) => cd.obd).forEach((cd) => assert.ok(c.cand.some((x) => x.did === 'F4' + cd.obd) || /^(22|23|0A)$/.test(cd.obd), c.key + ' ' + cd.obd)));
    // the MHD pack: exactly the eight identifiers from the capture, raw, never named or scaled
    assert.deepStrictEqual(PIDS.MHD.map((c) => c.raw.did), ['3010', '3020', '3030', '3031', '3032', '37FE', '586A', '4098']);
    PIDS.MHD.forEach((c) => { assert.ok(!c.cand && c.unit === 'hex' && /_raw$/.test(c.col)); assert.ok(!/boost|knock|lambda|timing/i.test(c.label)); });
    // only fast values are the ones tuning cares about every round
    assert.ok(ALL.filter((c) => c.rate === 'fast').length <= 8, 'few fast values, so a round stays short');
    ok('pids: unique keys and CSV columns, short waits, every standard PID both ways, MHD pack raw');
})();

/* ------------------------------------------------------------------ decoding: known vectors through the real DidReader */
async function decoding() {
    const reader = (answer) => new DidReader({ async physical(t, sid, data, to, accept) { return answer(sid, data); } }, { ecu: 0x12 });
    const find = (key) => ALL.find((c) => c.key === key);
    // a vector: channel key, the data bytes the car sends, the value expected
    const standard = [
        ['lambda', '24', [0x80, 0x00, 0, 0], 1.0], ['lambda', '24', [0x40, 0x00, 0, 0], 0.5], ['lambdaTarget', '44', [0x80, 0x00], 1.0],
        ['stft', '06', [128], 0], ['stft', '06', [0], -100], ['stft', '06', [255], 99.21875], ['ltft', '07', [64], -50],
        ['mapMeas', '0B', [101], 101], ['airMass', '10', [0x0F, 0xA0], 144], ['throttle', '11', [255], 100], ['throttle', '11', [128], 50.196078],
        ['pedal', '49', [255], 100], ['rail', '23', [0x0D, 0xAC], 350], ['voltage', '42', [0x36, 0xB0], 14.0], ['catTemp', '3C', [0x0F, 0xA0], 360],
        ['absLoad', '43', [0x00, 0xFF], 100], ['fuelLevel', '2F', [255], 100], ['ambient', '46', [65], 25], ['lpfp', '0A', [100], 3.0],
    ];
    for (const [key, pid, bytes, want] of standard) {
        const ch = find(key);
        for (const form of ['did', 'obd']) {
            const cd = ch.cand.find((c) => (form === 'did' ? c.did === 'F4' + pid : c.obd === pid));
            assert.ok(cd, key + ' has the ' + form + ' form of PID ' + pid);
            const rd = reader((sid, data) => (form === 'did' ? [0x62, 0xF4, parseInt(pid, 16)].concat(bytes) : [0x41, parseInt(pid, 16)].concat(bytes)));
            const got = await rd.read(cd);
            assert.ok(got !== null && near(got, want, Math.max(1e-4, Math.abs(want) * 1e-6)), key + ' ' + form + ' ' + pid + ': ' + got + ' vs ' + want);
        }
    }
    // BMW identifiers: signed, offset and unit-converted values
    const bmw = [
        ['lambda', '5889', [0x10, 0x00], 1.0], ['knock', '554C', [0xFC], -1.5], ['ignition', '59FA', [0x20], 24.0], ['charge', '4205', [0x4E, 0x20], 156.25],
        ['exhaustTemp', '5896', [0x0E, 0x10], 86.86], ['ambient', '5512', [130], 25.0], ['oilPressure', '586F', [0x11, 0x94], 4.5],
        ['rail', '58EF', [0x9C, 0x40], 200], ['boostDuty', '4AAB', [0x80, 0x00], 50.0], ['boostDev', '4536', [0xFF, 0x38], -2.5],
        ['ethanol', '550D', [255], 100], ['fuelTemp', '4AD5', [100], 27.0], ['vanosInA', '452E', [0x01, 0x2C], 30.0], ['wastegate', '5927', [0x80], 50.0],
    ];
    for (const [key, did, bytes, want] of bmw) {
        const cd = find(key).cand.find((c) => c.did === did);
        assert.ok(cd, key + ' ' + did);
        const rd = reader(() => [0x62, parseInt(did.slice(0, 2), 16), parseInt(did.slice(2), 16)].concat(bytes));
        const got = await rd.read(cd);
        assert.ok(got !== null && near(got, want, Math.max(0.01, Math.abs(want) * 0.002)), key + ' ' + did + ': ' + got + ' vs ' + want);
    }
    // a value outside the plausible range is "no answer", not a number: lambda 16 (the engine off), 0xFFFF everywhere
    const lam = find('lambda').cand.find((c) => c.did === '5889');
    assert.strictEqual(await reader(() => [0x62, 0x58, 0x89, 0xFF, 0xFF]).read(lam), null);
    assert.strictEqual(await reader(() => [0x7F, 0x22, 0x31]).read(lam), null, 'a refusal is not a value');
    assert.strictEqual(await reader(() => [0x62, 0x58, 0x89, 0x10]).read(lam), null, 'a short reply is not a value');
    ok('decoding: ' + standard.length + ' standard-PID vectors both ways (UDS and OBD), ' + bmw.length + ' BMW values, bad replies');
}

/* ------------------------------------------------------------------ custom_pids.json */
(function custom() {
    const good = { pids: [
        { id: 'boost_dev', label: 'Boost deviation', unit: 'kPa', decimals: 1, ecu: '0x12', did: '4536', type: 's16', factor: 0.0125, min: -300, max: 300, rate: 'fast' },
        { id: 'mystery', ecu: 19, did: '37fe', type: 'hex', len: 6 },
        { id: 'rpm_obd', obd: '0c', type: 'u16', factor: 0.25, ecu: '19' },
        { id: 'big', did: 'ABCD', type: 's32', at: 4, offset: 10 },
    ] };
    const r = parseCustom(JSON.stringify(good));
    assert.deepStrictEqual(r.errors, []);
    assert.strictEqual(r.channels.length, 4);
    const [a, b, c, d] = r.channels;
    assert.deepStrictEqual([a.key, a.col, a.pack, a.rate, a.d, a.unit, a.label], ['c_boost_dev', 'c_boost_dev', 'custom', 'fast', 1, 'kPa', 'Boost deviation']);
    assert.deepStrictEqual([a.cand[0].did, a.cand[0].bytes, a.cand[0].signed, a.cand[0].ecu, a.cand[0].factor, a.cand[0].min, a.cand[0].max],
        ['4536', 2, true, 0x12, 0.0125, -300, 300]);
    assert.ok(a.cand[0].timeoutMs <= 1200);
    assert.deepStrictEqual([b.raw.did, b.raw.len, b.ecu, b.unit, b.rate], ['37FE', 6, 19, 'hex', 'slow'], 'hex: upper-case DID, ecu, default rate');
    assert.deepStrictEqual([c.cand[0].obd, c.cand[0].did, c.cand[0].ecu, c.cand[0].signed], ['0C', undefined, 19, false]);
    assert.deepStrictEqual([d.cand[0].bytes, d.cand[0].signed, d.cand[0].at, d.cand[0].offset], [4, true, 4, 10]);

    // everything that must be refused is, with a message naming the entry; the good ones around it still load
    const bad = [
        [{ id: 'Upper', did: '4536', type: 'u8' }, 'id must be'], [{ id: '1abc', did: '4536', type: 'u8' }, 'id must be'],
        [{ id: 'a'.repeat(25), did: '4536', type: 'u8' }, 'id must be'], [{ did: '4536', type: 'u8' }, 'id must be'],
        [{ id: 'x', did: '4536', type: 'float' }, 'type must be'], [{ id: 'x', did: '453', type: 'u8' }, 'did must be 4 hex'],
        [{ id: 'x', did: '45G6', type: 'u8' }, 'did must be 4 hex'], [{ id: 'x', obd: '0', type: 'u8' }, 'obd must be 2 hex'],
        [{ id: 'x', did: '4536', obd: '0C', type: 'u8' }, 'exactly one'], [{ id: 'x', type: 'u8' }, 'exactly one'],
        [{ id: 'x', did: '4536', type: 'u8', ecu: 0xDF }, '0xDF'], [{ id: 'x', did: '4536', type: 'u8', ecu: 300 }, 'ecu must be'],
        [{ id: 'x', did: '4536', type: 'u8', ecu: '0xZZ' }, 'ecu must be'], [{ id: 'x', did: '4536', type: 'u8', ecu: 1.5 }, 'ecu must be'],
        [{ id: 'x', did: '4536', type: 'u8', factor: 0 }, 'factor'], [{ id: 'x', did: '4536', type: 'u8', factor: 'a' }, 'factor'],
        [{ id: 'x', did: '4536', type: 'u8', offset: NaN }, 'offset'], [{ id: 'x', did: '4536', type: 'u8', min: 5, max: 1 }, 'min is above max'],
        [{ id: 'x', did: '4536', type: 'u8', decimals: 7 }, 'decimals'], [{ id: 'x', did: '4536', type: 'u8', rate: 'now' }, 'rate'],
        [{ id: 'x', did: '4536', type: 'u8', at: 33 }, 'at must be'], [{ id: 'x', did: '4536', type: 'u8', at: -1 }, 'at must be'],
        [{ id: 'x', obd: '0C', type: 'hex' }, 'hex needs a did'], [{ id: 'x', did: '4536', type: 'hex', len: 17 }, 'len must be'],
        [{ id: 'x', did: '4536', type: 'hex', len: 0 }, 'len must be'], [{ id: 'x', did: '4536', type: 'u8', label: '' }, 'label'],
        [{ id: 'x', did: '4536', type: 'u8', label: 'a\nb' }, 'label'], [{ id: 'x', did: '4536', type: 'u8', label: 'x'.repeat(41) }, 'label'],
        [{ id: 'x', did: '4536', type: 'u8', unit: 'waytoolong' }, 'unit'], [42, 'not an object'], [null, 'not an object'], [[1], 'not an object'],
    ];
    bad.forEach(([entry, why]) => {
        const out = parseCustom(JSON.stringify({ pids: [{ id: 'fine', did: '4536', type: 'u8' }, entry, { id: 'fine2', obd: '05', type: 'u8' }] }));
        assert.strictEqual(out.channels.length, 2, JSON.stringify(entry) + ' is refused, the good ones load');
        assert.strictEqual(out.errors.length, 1, JSON.stringify(entry));
        assert.ok(out.errors[0].indexOf(why) >= 0, JSON.stringify(entry) + ' -> ' + out.errors[0] + ' (wanted: ' + why + ')');
        assert.ok(/^pids\[1\]/.test(out.errors[0]), 'the message says which entry');
    });
    // duplicates, the limit, the file itself
    const dup = parseCustom(JSON.stringify({ pids: [{ id: 'a', did: '4536', type: 'u8' }, { id: 'a', did: '4537', type: 'u8' }] }));
    assert.strictEqual(dup.channels.length, 1); assert.ok(/used twice/.test(dup.errors[0]));
    const many = parseCustom(JSON.stringify({ pids: Array.from({ length: MAX + 5 }, (_, i) => ({ id: 'p' + i, did: '4536', type: 'u8' })) }));
    assert.strictEqual(many.channels.length, MAX); assert.ok(/only the first/.test(many.errors[0])); assert.strictEqual(many.errors.length, 1);
    assert.deepStrictEqual(parseCustom(''), { channels: [], errors: [] }); assert.deepStrictEqual(parseCustom(null), { channels: [], errors: [] });
    assert.ok(/not valid JSON/.test(parseCustom('{ nope').errors[0]));
    assert.ok(/needs a "pids" list/.test(parseCustom('{"a":1}').errors[0]));
    assert.strictEqual(parseCustom(JSON.stringify([{ id: 'x', did: '4536', type: 'u8' }])).channels.length, 1, 'a bare list is accepted');
    assert.strictEqual(parseCustom('﻿' + JSON.stringify(good)).channels.length, 4, 'a BOM from a Windows editor is fine');
    assert.ok(/larger than/.test(parseCustom(' '.repeat(70000)).errors[0]));
    // a custom id can never collide with a built-in key (they are all c_<id>)
    ALL.concat(CH.CHANNELS).forEach((c) => assert.ok(!/^c_/.test(c.key), c.key));
    ok('custom: ' + bad.length + ' malformed entries each refused by name, good ones kept, limits, BOM, duplicates');
})();

/* ------------------------------------------------------------------ the channel list */
(function channelList() {
    const settings = (o) => ({ get: (k) => o[k] });
    const DME = JSON.parse(fs.readFileSync(path.join(APP, 'app.json'), 'utf8')).config.dme;      // the shipped candidate lists
    const dflt = { dmeReads: true, packTuning: true, packCustom: true };
    const cust = parseCustom(JSON.stringify({ pids: [{ id: 'one', did: '4536', type: 's16' }] })).channels;
    const keys = (l) => l.map((c) => c.key);
    let l = CH.activeChannels(settings(dflt), cust, DME);
    assert.deepStrictEqual(keys(l).slice(0, 8), ['rpm', 'speed', 'torque', 'coolant', 'oil', 'gLong', 'gLat', 'boost'], 'car channels, then boost');
    assert.deepStrictEqual(keys(l).slice(8, 13), ['boostAbs', 'boostTarget', 'load', 'timing', 'iat'], 'then the basic engine-unit values');
    assert.strictEqual(l.length, 13 + PIDS.TUNING.length + 1, 'default: base + basic + Tuning + the custom file');
    assert.ok(!l.some((c) => c.pack === 'engine' || c.pack === 'cylinders' || c.pack === 'mhd'), 'the other packs are off by default');
    l = CH.activeChannels(settings({ dmeReads: true, packTuning: true, packEngine: true, packCylinders: true, packMhd: true, packCustom: true }), cust, DME);
    assert.strictEqual(l.length, 13 + 20 + 15 + 18 + 8 + 1);
    const cols = l.map((c) => c.col);
    assert.strictEqual(new Set(cols).size, cols.length, 'unique columns with everything on');
    assert.deepStrictEqual(keys(l).slice(-9, -1), PIDS.MHD.map((c) => c.key), 'packs in order, custom last');
    l.forEach((c) => { if (c.src === 'dme') assert.ok(c.cand.length && c.cand.every((x) => x.timeoutMs <= 1200), c.key); if (c.src === 'raw') assert.ok(c.raw.did, c.key); });
    // the five basic values read the manifest's lists: none may be empty (an empty list would never ask the car anything)
    ['boostAbs', 'boostTarget', 'load', 'timing', 'iat'].forEach((k) => assert.ok(l.find((c) => c.key === k).cand.length > 0, k + ' has candidates from app.json'));
    assert.deepStrictEqual(CH.activeChannels(settings({ dmeReads: false, packTuning: true }), cust, DME).map((c) => c.key), keys(CH.CHANNELS.filter((c) => c.src !== 'dme')),
        'engine unit reads off: the car\'s own signals only');
    assert.ok(!keys(CH.activeChannels(settings({ dmeReads: true, packCustom: false }), cust, DME)).includes('c_one'), 'the custom pack switches off');
    // app.json config.dme overrides a channel's candidates, by key (or cfg)
    const override = { boostActual: [{ did: 'ABCD', bytes: 1 }], lambda: [{ obd: '24', bytes: 2, factor: 1 }] };
    l = CH.activeChannels(settings(dflt), [], Object.assign({}, DME, override));
    assert.strictEqual(l.find((c) => c.key === 'boostAbs').cand[0].did, 'ABCD');
    assert.strictEqual(l.find((c) => c.key === 'lambda').cand.length, 1);
    assert.strictEqual(l.find((c) => c.key === 'boostAbs').cand[0].timeoutMs, 1200, 'and still gets the short wait');
    // collect: numbers, raw hex, nulls
    const ch = CH.activeChannels(settings({ dmeReads: true, packMhd: true }), [], DME);
    const row = CH.collect({ rpm: 3000 }, { boostAbs: 181.3, mhd3010: '00A1FF', mhd3020: 'zz', lambda: 'nope' }, 101.3, ch);
    assert.strictEqual(row.rpm, 3000); assert.ok(near(row.boost, 0.8));
    assert.strictEqual(row.mhd3010, '00A1FF'); assert.strictEqual(row.mhd3020, null, 'a raw value must be hex'); assert.strictEqual(row.mhd3030, null);
    assert.strictEqual(row.lambda, undefined, 'a channel that is not on is not in the row');
    ok('channels: defaults, every pack, order, off switches, config overrides, raw values in a row');
})();

/* ------------------------------------------------------------------ the datalogger: raw values, columns per log */
(function loggerColumns() {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tuner-cols-'));
    try {
        const clock = new Date(2026, 10, 8, 15, 0, 0);
        const lg = new DataLogger({ dir, columns: [{ key: 'rpm', col: 'rpm' }], now: () => clock, log: quiet });
        const cols = [{ key: 'rpm', col: 'rpm' }, { key: 'lambda', col: 'lambda' }, { key: 'mhd3010', col: 'mhd_3010_raw' }];
        const s = lg.start(cols);
        lg.add({ rpm: 3000, lambda: 0.98, mhd3010: '00A1FF' }, 0);
        lg.add({ rpm: 3100, lambda: null, mhd3010: 'not hex!' }, 500);            // a raw value that is not hex is blank
        lg.add({ rpm: 3200, lambda: 1.01, mhd3010: '' }, 1000);
        lg.add({ rpm: 3200, lambda: 1.01, mhd3010: 'a1b2' }, 1500);               // lower case is not hex bytes the app made
        const sum = lg.stop();
        const lines = fs.readFileSync(s.path, 'utf8').trim().split('\n');
        assert.strictEqual(lines[0], 'time_s,rpm,lambda,mhd_3010_raw');
        assert.strictEqual(lines[1], '0.000,3000,0.98,00A1FF');
        assert.strictEqual(lines[2], '0.500,3100,,');
        assert.strictEqual(lines[3], '1.000,3200,1.01,');
        assert.strictEqual(lines[4], '1.500,3200,1.01,');
        assert.ok(!('mhd3010' in sum.peaks), 'a raw channel has no min / max');
        assert.deepStrictEqual(sum.peaks.lambda, { min: 0.98, max: 1.01 });
        // the next log can have other columns; the header names only the columns of its own log
        clock.setSeconds(5);
        const s2 = lg.start([{ key: 'rpm', col: 'rpm' }]);
        lg.add({ rpm: 1 }, 0); lg.stop();
        assert.strictEqual(fs.readFileSync(s2.path, 'utf8').split('\n')[0], 'time_s,rpm');
        // a column name that could break the CSV is refused before a file exists
        assert.throws(() => lg.start([{ key: 'x', col: 'a,b' }]), /bad column name/);
        assert.throws(() => lg.start([{ key: 'x', col: 'a\nb' }]), /bad column name/);
        assert.strictEqual(lg.list().length, 2, 'and no file was made for it');
        ok('logger: raw hex cells, per-log columns, bad column names refused');
    } finally { fs.rmSync(dir, { recursive: true, force: true }); }
})();

/* ------------------------------------------------------------------ the poller, against the real link */
function rig(opts) {
    wire.sent = []; wire.handler = null;
    const clock = { t: 1000000 };
    const link = new ReadOnlyDiag({}, quiet, { gapMs: 0 });
    const reader = new DidReader(link, { ecu: 0x12 });
    const poller = new Poller(Object.assign({ reader, link, log: quiet, now: () => clock.t, wakeMs: 10000 }, opts || {}));
    const step = async (n, dt) => { for (let i = 0; i < (n || 1); i++) { await poller.step(); clock.t += dt === undefined ? 20 : dt; } };
    return { clock, link, reader, poller, step };
}
const chan = (key, did, rate, extra) => Object.assign({ key, col: key, label: key, unit: '', d: 0, pack: 'tuning', rate: rate || 'fast',
    cand: [{ did, bytes: 2, at: 0, factor: 1, offset: 0, timeoutMs: 1200 }] }, extra || {});
const dids = (list) => list.filter((s) => s.sid === 0x22).map((s) => s.data[0].toString(16).toUpperCase().padStart(2, '0') + s.data[1].toString(16).toUpperCase().padStart(2, '0'));
const answer22 = (target, sid, data) => (sid === 0x22 ? [0x62, data[0], data[1], 0x01, 0x00] : null);            // every value reads 256

async function poller() {
    await (async function silentUnitRecovers() {
        const r = rig(); wire.handler = () => null;                                  // the DME is silent
        r.poller.setChannels([chan('a', '1001'), chan('b', '1002'), chan('c', '1003')]);
        await r.step(3);                                                              // 3 misses; each value now backs off 5 s
        await r.step(3);
        assert.ok(!r.link.silent(0x12), 'the backoff spares the unit: no more requests yet');
        r.clock.t += 5001;
        await r.step(3);                                                              // 6 misses: the link gives the unit up
        assert.ok(r.link.silent(0x12), 'the link has given up on the unit');
        assert.deepStrictEqual(r.poller.values(), {});
        wire.handler = answer22;                                                      // the car wakes up
        const before = wire.sent.length;
        await r.step(8);                                                              // less than wakeMs later: it waits, sends nothing
        assert.strictEqual(wire.sent.length, before, 'while silent it does not hammer the unit');
        assert.strictEqual(r.poller.stats.wakes, 0, 'and it does not wake it at once either');
        r.clock.t += 10000;
        await r.step(6);
        assert.ok(!r.link.silent(0x12), 'woken');
        assert.strictEqual(r.poller.stats.wakes, 1);
        assert.deepStrictEqual(r.poller.values(), { a: 256, b: 256, c: 256 }, 'and the values are back without a restart');
        // silent again later: it recovers again, the same way
        wire.handler = () => null;
        await r.step(3); r.clock.t += 5001; await r.step(3);
        assert.ok(r.link.silent(0x12), 'silent a second time');
        wire.handler = answer22;
        await r.step(2); r.clock.t += 10000; await r.step(8);
        assert.strictEqual(r.poller.stats.wakes, 2);
        assert.ok(Object.keys(r.poller.values()).length === 3);
    })();

    await (async function fastAndSlow() {
        const r = rig({ slowPerCycle: 3 }); wire.handler = answer22;
        const fast = [chan('f1', '2001'), chan('f2', '2002')], slow = ['3001', '3002', '3003', '3004', '3005', '3006', '3007'].map((d, i) => chan('s' + i, d, 'slow'));
        r.poller.setChannels(fast.concat(slow));
        await r.step(15);                                                             // three rounds of 2 fast + 3 slow
        const seen = dids(wire.sent), n = (d) => seen.filter((x) => x === d).length;
        assert.strictEqual(n('2001'), 3); assert.strictEqual(n('2002'), 3);
        const slowReads = ['3001', '3002', '3003', '3004', '3005', '3006', '3007'].map(n);
        assert.strictEqual(slowReads.reduce((a, b) => a + b, 0), 9, 'three slow values per round');
        assert.ok(slowReads.every((c) => c >= 1), 'every slow value comes round: ' + slowReads);
        // with fewer slow values than the allowance, each is read once per round (not twice)
        const r2 = rig({ slowPerCycle: 3 }); wire.handler = answer22;
        r2.poller.setChannels([chan('f', '2001')].concat(['3001', '3002'].map((d, i) => chan('t' + i, d, 'slow'))));
        await r2.step(9);
        const seen2 = dids(wire.sent); assert.strictEqual(seen2.filter((x) => x === '3001').length, 3);
    })();

    await (async function refusalAndBackoff() {
        const r = rig(); const asked = {};
        wire.handler = (t, sid, data) => {
            const d = data[0].toString(16) + data[1].toString(16).padStart(2, '0'); asked[d] = (asked[d] || 0) + 1;
            if (d === '4001') return [0x7F, 0x22, 0x31];                              // a value this car does not have
            if (d === '4002') return null;                                            // one that never answers
            return [0x62, data[0], data[1], 0x00, 0x07];
        };
        r.poller.setChannels([chan('ok', '4003'), chan('gone', '4001'), chan('dead', '4002')]);
        await r.step(3);                                                              // first round
        assert.strictEqual(r.poller.status().find((s) => s.key === 'gone').state, 'refused');
        assert.strictEqual(r.poller.status().find((s) => s.key === 'dead').state, 'no reply');
        assert.strictEqual(r.poller.status().find((s) => s.key === 'ok').state, 'ok');
        await r.step(30);                                                             // ten more rounds, 20 ms apart
        assert.strictEqual(asked['4001'], 1, 'a refused value is not asked again for ' + REFUSED_MS / 1000 + ' s');
        assert.strictEqual(asked['4002'], 1, 'a silent value backs off');
        assert.ok(asked['4003'] >= 11, 'and the others carry on every round');
        r.clock.t += SILENT_MS[0] + 1; await r.step(3);
        assert.strictEqual(asked['4002'], 2, 'asked again after 5 s'); assert.strictEqual(asked['4001'], 1);
        r.clock.t += SILENT_MS[0] + 1; await r.step(3);
        assert.strictEqual(asked['4002'], 2, 'the second wait is longer (10 s)');
        r.clock.t += SILENT_MS[1]; await r.step(3);
        assert.strictEqual(asked['4002'], 3);
        r.clock.t += REFUSED_MS; await r.step(3);
        assert.strictEqual(asked['4001'], 2, 'a refused value is retried after two minutes');
        // a list where every value is refused: rounds go by with nothing read, and no rate is claimed
        const r3 = rig(); wire.handler = (t, sid) => [0x7F, sid, 0x31];
        r3.poller.setChannels([chan('x', '4101'), chan('y', '4102')]);
        await r3.step(2); await r3.step(40, 300);
        assert.strictEqual(r3.poller.rateHz, 0, 'nothing was read, so there is no measured rate');
        assert.ok(wire.sent.every((s) => s.timeout <= 1200), 'every request waited at most 1.2 s');
    })();

    await (async function outOfRange() {
        // a car that ANSWERS with an implausible number is not a car that lacks the value: it is told apart,
        // and (unlike a refusal) asked again soon, since the engine being off or a transient can do it
        const r = rig(); const asked = { odd: 0 };
        wire.handler = (t, sid, data) => {
            if (data[0] === 0x40 && data[1] === 0x04) { asked.odd++; return [0x62, 0x40, 0x04, 0xFF, 0xFF]; }
            if (data[0] === 0x40 && data[1] === 0x05) return [0x7F, 0x22, 0x31];
            return [0x62, data[0], data[1], 0x00, 0x07];
        };
        const bounded = chan('odd', '4004', 'fast', { cand: [{ did: '4004', bytes: 2, at: 0, factor: 1, offset: 0, min: 0, max: 100, timeoutMs: 1200 }] });
        // two candidates, one answering implausibly and one refusing: the answer wins the explanation
        const mixed = chan('mixed', '4004', 'fast', { cand: [bounded.cand[0], { did: '4005', bytes: 2, at: 0, factor: 1, offset: 0, timeoutMs: 1200 }] });
        r.poller.setChannels([bounded, chan('gone', '4005', 'fast'), mixed]);
        await r.step(3);
        const st = Object.fromEntries(r.poller.status().map((s) => [s.key, s.state]));
        assert.deepStrictEqual(st, { odd: 'range', gone: 'refused', mixed: 'range' });
        await r.step(12);
        assert.strictEqual(asked.odd, 2, 'within the 5 s wait it is not asked again (once for itself, once inside "mixed")');
        r.clock.t += SILENT_MS[0] + 1; await r.step(3);
        assert.ok(asked.odd > 2, 'but it is asked again after 5 s, not after the two minutes a refusal waits');
    })();

    await (async function staleness() {
        const r = rig(); wire.handler = answer22;
        r.poller.setChannels([chan('a', '5001')]);
        await r.step(2);
        assert.deepStrictEqual(r.poller.values(), { a: 256 });
        r.clock.t += MIN_AGE_MS - 100; assert.ok('a' in r.poller.values(), 'still fresh');
        r.clock.t += 200; assert.ok(!('a' in r.poller.values()), 'older than 4 s is no value');
        assert.strictEqual(r.poller.status()[0].state, 'stale');
    })();

    await (async function rawChannels() {
        const r = rig(); const reply = {};
        wire.handler = (t, sid, data) => reply[data[0].toString(16) + data[1].toString(16)];
        const raw = (key, did, len) => ({ key, col: key, label: key, unit: 'hex', d: 0, pack: 'mhd', rate: 'slow', raw: { did, len } });
        r.poller.setChannels([raw('r1', '3010', 16), raw('r2', '3020', 4), raw('r3', '3030', 4), raw('r4', '3031', 4)]);
        reply['3010'] = [0x62, 0x30, 0x10].concat(Array.from({ length: 20 }, (_, i) => i));        // longer than len: cut
        reply['3020'] = [0x62, 0x30, 0x20, 0x0A, 0xFF];                                            // shorter than len: as it is
        reply['3030'] = [0x7F, 0x22, 0x31];
        reply['3031'] = undefined;
        await r.step(4);
        const v = r.poller.values();
        assert.strictEqual(v.r1, '000102030405060708090A0B0C0D0E0F', 'the first 16 bytes, upper-case hex');
        assert.strictEqual(v.r2, '0AFF');
        assert.ok(!('r3' in v) && !('r4' in v));
        const st = Object.fromEntries(r.poller.status().map((s) => [s.key, s.state]));
        assert.deepStrictEqual(st, { r1: 'ok', r2: 'ok', r3: 'refused', r4: 'no reply' });
        assert.ok(wire.sent.every((s) => s.sid === 0x22 && s.data.length === 2));
        assert.strictEqual(r.poller.summary().mhd.answering, 2);
    })();

    await (async function readOnlyAndOtherUnits() {
        const r = rig(); wire.handler = answer22;
        const custom = parseCustom(JSON.stringify({ pids: [{ id: 'other', ecu: '0x40', did: '1234', type: 'u16', rate: 'fast' }, { id: 'o2', ecu: 0x40, obd: '0C', type: 'u16' }] })).channels;
        r.poller.setChannels([chan('a', '6001'), chan('b', '6002', 'slow'), { key: 'baro', hidden: true, pack: 'basic', rate: 'slow', cand: [{ obd: '33', bytes: 1, at: 0, factor: 1, offset: 0, min: 50, max: 115, timeoutMs: 1200 }] }].concat(custom));
        wire.handler = (t, sid, data) => (sid === 0x22 ? [0x62, data[0], data[1], 0x00, 0x64] : sid === 0x01 ? [0x41, data[0], 0x64, 0x64] : null);
        await r.step(40);
        assert.strictEqual(wire.sent.length, 40, 'every value answers, so every step sent one request');
        assert.ok(wire.sent.every((s) => s.sid === 0x22 || s.sid === 0x01), 'nothing but reads: ' + [...new Set(wire.sent.map((s) => s.sid))]);
        assert.ok(wire.sent.some((s) => s.target === 0x40) && wire.sent.some((s) => s.target === 0x12), 'a custom PID can name another unit');
        assert.ok(r.poller.status().every((s) => s.key !== 'baro'), 'the outside-pressure channel stays out of the lists');
        assert.strictEqual(r.poller.values().baro, 100, 'the outside pressure is read (and kept out of the lists)');
        // one unit going silent does not blind the others
        wire.handler = (t, sid, data) => (t === 0x40 ? null : (sid === 0x22 ? [0x62, data[0], data[1], 0x00, 0x64] : [0x41, data[0], 0x64, 0x64]));
        for (let i = 0; i < 4; i++) { await r.step(40); r.clock.t += 5001; }               // past each backoff, a few times
        await r.step(12);                                                                  // and a last round, so what is read is fresh
        assert.ok(r.link.silent(0x40) && !r.link.silent(0x12));
        assert.ok('a' in r.poller.values(), 'the engine unit is still read while 0x40 is silent');
    })();

    await (async function timingAndState() {
        const r = rig(); wire.handler = answer22;
        r.poller.setChannels([chan('a', '7001'), chan('b', '7002')]);
        assert.strictEqual(r.poller.rateHz, 0);
        await r.step(12, 25);                                                         // 25 ms per request: a round of 2 = 50 ms
        assert.ok(near(r.poller.cycleMs, 50, 5), 'round length measured: ' + r.poller.cycleMs);
        assert.ok(r.poller.rateHz >= 15 && r.poller.rateHz <= 25, 'about 20 rounds a second: ' + r.poller.rateHz);
        // changing the list keeps what is known about a value that stays
        const before = r.poller.values().a;
        r.poller.setChannels([chan('a', '7001'), chan('z', '7009')]);
        assert.strictEqual(r.poller.values().a, before); assert.ok(!('b' in r.poller.values()));
        assert.strictEqual(r.poller.status().find((s) => s.key === 'z').state, 'waiting');
        // no channels: nothing is sent and the caller is told to wait
        r.poller.setChannels([]); wire.sent = [];
        assert.strictEqual(await r.poller.step(), 500); assert.strictEqual(wire.sent.length, 0);
    })();

    ok('poller: wakes a silent unit, fast/slow rounds, refusal and no-reply backoff, 4 s staleness, raw reads, read-only, other units, timing');
}

decoding().then(poller).then(() => { console.log('\nBimmhacks PID checks passed (' + groups + ' groups).'); })
    .catch((e) => { console.log('FAIL ' + (e && e.stack || e)); process.exit(1); });
