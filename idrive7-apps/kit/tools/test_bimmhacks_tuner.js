'use strict';

// Bimmhacks Tuner: the datalogger, the engine-unit fault read / two-press clear, the gated map switch and
// the channel table, against in-memory fake diagnostic links. No SDK, vehicle or capture needed:
//   node kit/tools/test_bimmhacks_tuner.js

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const Module = require('module');

const ROOT = path.join(__dirname, '..', '..');
// the app asks for './kit/<name>'; in the repo that is kit/runtime/<name>
const load = Module._load;
Module._load = function (req, parent, isMain) {
    if (/^\.\/kit\//.test(req)) return load.call(this, path.join(ROOT, 'kit/runtime', req.slice(6)), parent, isMain);
    return load.apply(this, arguments);
};
const APP = path.join(ROOT, 'apps/bimmhacks_tuner');
const { CHANNELS, collect, gaugeBoost, dmeChannels, BY_KEY } = require(path.join(APP, 'channels'));
const { DataLogger, NAME } = require(path.join(APP, 'logger'));
const { FaultControl, CONFIRM_MS } = require(path.join(APP, 'faults'));
const { MapControl, MIN_COOLING_RPM } = require(path.join(APP, 'maps'));

const quiet = { info() {}, warn() {}, error() {} };
const hex = (a) => a.map((b) => b.toString(16).padStart(2, '0')).join(' ');
let passed = 0;
const ok = (name) => { passed++; console.log('  ok  ' + name); };

/* ---------------------------------------------------------------- channels */
(function channels() {
    assert.strictEqual(gaugeBoost(null, 100), null);
    assert.ok(Math.abs(gaugeBoost(201.3, 101.3) - 1.0) < 1e-9, '1 bar over ambient');
    assert.ok(Math.abs(gaugeBoost(101.3, undefined)) < 1e-9, 'standard atmosphere when baro is unknown');
    assert.ok(gaugeBoost(81.3, 101.3) < 0, 'vacuum is negative boost');
    const row = collect({ rpm: 3000, speed: 80, coolant: 90, demo: false }, { boostAbs: 181.3, load: 55 }, 101.3);
    assert.strictEqual(row.rpm, 3000);
    assert.strictEqual(row.oil, null, 'a channel nobody supplied is null, not undefined or 0');
    assert.strictEqual(row.timing, null);
    assert.ok(Math.abs(row.boost - 0.8) < 1e-9);
    assert.strictEqual(collect({ rpm: NaN }, {}, 101.3).rpm, null, 'NaN is no value');
    // every column name is unique and CSV-safe
    const cols = CHANNELS.map((c) => c.col);
    assert.strictEqual(new Set(cols).size, cols.length);
    cols.forEach((c) => assert.ok(/^[a-z0-9_]+$/.test(c), c));
    // every engine-unit channel has a candidate list in the manifest, and the manifest's lists exist
    const spec = JSON.parse(fs.readFileSync(path.join(APP, 'app.json'), 'utf8'));
    dmeChannels().forEach((c) => assert.ok(Array.isArray(spec.config.dme[c.cfg]) && spec.config.dme[c.cfg].length, 'config.dme.' + c.cfg));
    assert.ok(Array.isArray(spec.config.dme.baro), 'config.dme.baro');
    assert.strictEqual(BY_KEY.boost.src, 'derived');
    ok('channels: boost maths, null handling, unique CSV columns, manifest candidates');
})();

/* ---------------------------------------------------------------- logger */
(function logger() {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tuner-log-'));
    try {
        let clock = new Date(2026, 10, 8, 12, 0, 0);
        const cols = [{ key: 'rpm', col: 'rpm' }, { key: 'boost', col: 'boost_bar' }, { key: 'oil', col: 'oil_c' }];
        const lg = new DataLogger({ dir, columns: cols, now: () => clock, log: quiet });
        assert.strictEqual(lg.add({ rpm: 1 }, 0), false, 'nothing is written before start');
        const s = lg.start();
        assert.ok(NAME.test(s.name), s.name);
        assert.strictEqual(s.name, 'tuner-20261108-120000.csv');
        assert.strictEqual(lg.start().name, s.name, 'start while running keeps the same file');
        lg.add({ rpm: 800, boost: -0.2, oil: null }, 1000);
        lg.add({ rpm: 6500, boost: 1.234567, oil: 95 }, 1500);
        lg.add({ rpm: 3000, boost: 0.5, oil: 96 }, 2000);
        const sum = lg.stop();
        assert.strictEqual(sum.rows, 3);
        assert.strictEqual(sum.seconds, 1);
        assert.deepStrictEqual(sum.peaks.rpm, { min: 800, max: 6500 });
        assert.strictEqual(sum.peaks.oil.min, 95, 'a blank cell is not a minimum of 0');
        const lines = fs.readFileSync(s.path, 'utf8').trim().split('\n');
        assert.strictEqual(lines[0], 'time_s,rpm,boost_bar,oil_c');
        assert.strictEqual(lines[1], '0.000,800,-0.2,', 'a null is an empty cell, time starts at 0');
        assert.strictEqual(lines[2], '0.500,6500,1.235,95', 'rounded to 3 decimals');
        assert.strictEqual(lines.length, 4);
        assert.strictEqual(lg.stop(), null, 'stop twice is harmless');
        assert.strictEqual(lg.last.name, s.name);

        // two starts in one second get two files
        const a = lg.start(); lg.stop();
        assert.strictEqual(a.name, 'tuner-20261108-120000_b.csv');
        const b = lg.start(); lg.stop();
        assert.strictEqual(b.name, 'tuner-20261108-120000_c.csv');
        assert.deepStrictEqual(lg.list().map((f) => f.name),
            ['tuner-20261108-120000_c.csv', 'tuner-20261108-120000_b.csv', 'tuner-20261108-120000.csv'], 'newest first');

        // size limit closes the file by itself and says why
        const small = new DataLogger({ dir: path.join(dir, 'small'), columns: cols, now: () => clock, maxBytes: 200, log: quiet });
        small.start();
        let stopped = false;
        for (let i = 0; i < 100 && !stopped; i++) stopped = small.add({ rpm: 1000 + i, boost: 0.1, oil: 90 }, i * 100) === false;
        assert.ok(stopped, 'the size cap stopped the log');
        assert.ok(!small.running);
        assert.strictEqual(small.last.reason, 'size limit reached');
        assert.ok(fs.statSync(small.last.path).size < 400, 'and the file stayed small');

        // only the newest maxFiles are kept
        const pr = new DataLogger({ dir: path.join(dir, 'prune'), columns: cols, now: () => clock, maxFiles: 3, log: quiet });
        for (let i = 0; i < 6; i++) { clock = new Date(2026, 10, 8, 13, 0, i); pr.start(); pr.stop(); }
        const kept = pr.list().map((f) => f.name);
        assert.strictEqual(kept.length, 3);
        assert.strictEqual(kept[0], 'tuner-20261108-130005.csv', 'newest first, and the newest survived');
        assert.ok(!kept.includes('tuner-20261108-130000.csv'), 'the oldest went');

        // starts inside one second are pruned like any other
        clock = new Date(2026, 10, 8, 14, 0, 0);
        const same = new DataLogger({ dir: path.join(dir, 'same'), columns: cols, now: () => clock, maxFiles: 2, log: quiet });
        for (let i = 0; i < 5; i++) { same.start(); same.stop(); }
        assert.strictEqual(same.list().length, 2, 'same-second logs are listed and pruned');
        assert.strictEqual(same.list()[0].name, 'tuner-20261108-140000_e.csv', 'the newest survived');

        // unrelated files are never listed or pruned
        fs.writeFileSync(path.join(dir, 'prune', 'notes.txt'), 'keep me');
        pr.start(); pr.stop();
        assert.ok(fs.existsSync(path.join(dir, 'prune', 'notes.txt')));
        ok('logger: CSV layout, blanks, peaks, size cap, pruning, unrelated files untouched');
    } finally { fs.rmSync(dir, { recursive: true, force: true }); }
})();

/* ---------------------------------------------------------------- faults */
function fakeDiag(handler) {
    const calls = [];
    const diag = {
        available: true, calls,
        async physical(target, sid, data, timeout, accept) {
            calls.push({ target, req: [sid].concat(data) });
            const r = await handler(target, sid, data, calls);
            return r === undefined ? null : r;
        },
    };
    return diag;
}

(async function faults() {
    // read: two records; the second is active
    const dtcReply = [0x59, 0x02, 0xFF, 0x48, 0x0A, 0x01, 0x08, 0x29, 0x1C, 0x02, 0x01];
    let diag = fakeDiag((t, sid) => (sid === 0x19 ? dtcReply : [0x7F, sid, 0x11]));
    let fc = new FaultControl({ diag, log: quiet });
    const f = await fc.read();
    assert.strictEqual(diag.calls[0].target, 0x12, 'only the engine unit is asked');
    assert.deepStrictEqual(diag.calls[0].req, [0x19, 0x02, 0x0C]);
    assert.strictEqual(f.length, 2);
    assert.strictEqual(f[0].code, '480A01');
    assert.strictEqual(f[0].label, 'STORED');
    assert.strictEqual(f[1].code, '291C02');
    assert.strictEqual(f[1].label, 'ACTIVE');
    assert.strictEqual(fc.note, '2 faults');

    // empty memory, a refusal, a silent unit, and a down link
    fc = new FaultControl({ diag: fakeDiag(() => [0x59, 0x02, 0xFF]), log: quiet });
    assert.deepStrictEqual(await fc.read(), []);
    assert.strictEqual(fc.note, 'No faults stored');
    fc = new FaultControl({ diag: fakeDiag((t, sid) => [0x7F, sid, 0x22]), log: quiet });
    assert.strictEqual(await fc.read(), null);
    assert.ok(/Refused/.test(fc.note), fc.note);
    fc = new FaultControl({ diag: fakeDiag(() => null), log: quiet });
    await fc.read();
    assert.ok(/did not answer/.test(fc.note));
    const down = fakeDiag(() => dtcReply); down.available = false;
    fc = new FaultControl({ diag: down, log: quiet });
    await fc.read();
    assert.strictEqual(down.calls.length, 0, 'no request on a down link');

    // clear: first press only arms, nothing is sent
    let clock = 1000;
    diag = fakeDiag((t, sid) => (sid === 0x14 ? [0x54] : [0x7F, sid, 0x11]));
    fc = new FaultControl({ diag, log: quiet, now: () => clock });
    let r = await fc.clear();
    assert.deepStrictEqual([r.sent, r.ok], [false, false]);
    assert.strictEqual(diag.calls.length, 0, 'the first press sends nothing');
    assert.ok(fc.armed);
    // second press inside the window sends 14 FF FF FF to the engine unit
    clock += CONFIRM_MS - 1;
    r = await fc.clear();
    assert.deepStrictEqual([r.sent, r.ok], [true, true]);
    assert.strictEqual(hex(diag.calls[0].req), '14 ff ff ff');
    assert.strictEqual(diag.calls[0].target, 0x12);
    assert.ok(!fc.armed, 'the arm is used up');
    // a third press has to arm again
    r = await fc.clear();
    assert.strictEqual(r.sent, false);
    // the window runs out: the second press only re-arms
    clock += 10 * CONFIRM_MS;
    diag.calls.length = 0;
    r = await fc.clear();
    assert.strictEqual(r.sent, false);
    clock += CONFIRM_MS + 1;
    r = await fc.clear();
    assert.strictEqual(r.sent, false, 'too late: it armed again, nothing sent');
    assert.strictEqual(diag.calls.length, 0);

    // a refusal for the session: asked again in the extended session, then the default one is restored
    clock = 0;
    const seq = [];
    diag = fakeDiag((t, sid, data) => {
        seq.push(hex([sid].concat(data)));
        if (sid === 0x14) return seq.filter((s) => s.startsWith('14')).length === 1 ? [0x7F, 0x14, 0x7F] : [0x54];
        if (sid === 0x10) return [0x50, data[0]];
        return null;
    });
    fc = new FaultControl({ diag, log: quiet, now: () => clock });
    await fc.clear(); r = await fc.clear();
    assert.strictEqual(r.ok, true);
    assert.deepStrictEqual(seq, ['14 ff ff ff', '10 03', '14 ff ff ff', '10 01']);

    // a hard refusal says why, and the memory is still shown as read
    diag = fakeDiag((t, sid) => (sid === 0x14 ? [0x7F, 0x14, 0x22] : [0x50, 0x03]));
    fc = new FaultControl({ diag, log: quiet, now: () => clock });
    fc.faults = [{ code: '480A01' }];
    await fc.clear(); r = await fc.clear();
    assert.strictEqual(r.ok, false);
    assert.ok(/Not cleared/.test(fc.note), fc.note);
    assert.strictEqual(fc.faults.length, 1, 'faults stay listed when the clear failed');

    // no answer at all: tried twice, then reported
    let n = 0;
    diag = fakeDiag((t, sid) => { if (sid === 0x14) n++; return null; });
    fc = new FaultControl({ diag, log: quiet, now: () => clock });
    await fc.clear(); r = await fc.clear();
    assert.strictEqual(n, 2);
    assert.ok(/no answer/.test(fc.note));
    ok('faults: engine-unit-only read, two-press clear window, session retry, refusals, down link');
})().then(maps).catch((e) => { console.log('FAIL ' + (e && e.stack || e)); process.exit(1); });

/* ---------------------------------------------------------------- maps */
async function maps() {
    const make = (kind, setting, rpm, up) => {
        const calls = [];
        const diag = {
            available: up !== false, calls,
            async physical(target, sid, data) {
                calls.push({ target, sid, data: data.slice() });
                await new Promise(setImmediate);
                if (kind === 'echo') return [0x6F].concat(data);
                if (kind === 'refuse') return [0x7F, sid, 0x31];
                if (kind === 'wrong') return [0x6F, 0x01, 0x03];
                return null;
            },
        };
        const mc = new MapControl({ diag, log: quiet, settings: { get: (k) => (k === 'maps' ? setting : undefined) },
            engine: { rpm: () => (rpm === undefined ? 800 : rpm) } });
        return { mc, diag, calls };
    };

    // OFF (the shipped default): not a single byte, for every command
    let { mc, calls } = make('echo', 'off');
    assert.strictEqual(await mc.setMap(2), false);
    assert.strictEqual(await mc.setCooling(true), false);
    assert.strictEqual(await mc.setCooling(false), false);
    assert.strictEqual(calls.length, 0, 'disarmed sends nothing');
    assert.ok(/Disarmed/.test(mc.note));
    ({ mc, calls } = make('echo', undefined));
    await mc.setMap(1);
    assert.strictEqual(calls.length, 0, 'an unset setting is off');

    // armed + echo: the exact MHD bytes to the engine unit
    ({ mc, calls } = make('echo', 'mhd'));
    assert.strictEqual(await mc.setMap(3), true);
    assert.deepStrictEqual([calls[0].target, calls[0].sid, calls[0].data], [0x12, 0x2F, [0x01, 0x02]]);
    assert.strictEqual(mc.map, 3);
    assert.ok(/accepted/.test(mc.note) && !/confirmed/.test(mc.note), 'never claims confirmed');
    assert.strictEqual(await mc.setMap(1), true);
    assert.deepStrictEqual(calls[1].data, [0x01, 0x00]);
    assert.strictEqual(await mc.setMap(4), true);
    assert.deepStrictEqual(calls[2].data, [0x01, 0x03]);

    // only maps 1..4
    const before = calls.length;
    for (const bad of [0, 5, -1, 1.5, '2', null, undefined, NaN]) assert.strictEqual(await mc.setMap(bad), false);
    assert.strictEqual(calls.length, before, 'an out-of-range map sends nothing');

    // an untuned DME refuses: says so, does not record a map
    ({ mc, calls } = make('refuse', 'mhd'));
    assert.strictEqual(await mc.setMap(2), false);
    assert.strictEqual(mc.map, null);
    assert.strictEqual(mc.note, 'Not available on this car');
    // a reply that does not echo the request is not accepted
    ({ mc } = make('wrong', 'mhd'));
    assert.strictEqual(await mc.setMap(2), false);
    assert.strictEqual(mc.map, null);
    // silence is not success
    ({ mc } = make('silent', 'mhd'));
    assert.strictEqual(await mc.setMap(2), false);
    assert.strictEqual(mc.note, 'No answer from the DME');

    // a down link: nothing sent
    ({ mc, calls } = make('echo', 'mhd', 800, false));
    assert.strictEqual(await mc.setMap(2), false);
    assert.strictEqual(calls.length, 0);

    // Max Cooling ON only with a live running engine; OFF always
    ({ mc, calls } = make('echo', 'mhd', null));
    assert.strictEqual(await mc.setCooling(true), false, 'no live rpm (or demo): not on');
    assert.strictEqual(calls.length, 0);
    ({ mc, calls } = make('echo', 'mhd', MIN_COOLING_RPM));
    assert.strictEqual(await mc.setCooling(true), false, 'exactly the limit is not running');
    ({ mc, calls } = make('echo', 'mhd', MIN_COOLING_RPM + 1));
    assert.strictEqual(await mc.setCooling(true), true);
    assert.deepStrictEqual(calls[0].data, [0x03, 0x01]);
    assert.strictEqual(mc.cooling, 'ON');
    ({ mc, calls } = make('echo', 'mhd', null));
    assert.strictEqual(await mc.setCooling(false), true, 'off needs no engine');
    assert.deepStrictEqual(calls[0].data, [0x03, 0x00]);

    // one command at a time: a second press while one is in flight is refused, not queued
    ({ mc, calls } = make('echo', 'mhd'));
    const first = mc.setMap(2);
    const second = await mc.setMap(3);
    assert.strictEqual(second, false);
    assert.ok(/Wait/.test(mc.note));
    assert.strictEqual(await first, true);
    assert.strictEqual(calls.length, 1, 'only the first reached the car');
    assert.strictEqual(mc.busy, false, 'busy is released');
    ok('maps: off by default, exact bytes, 1..4 only, echo required, cooling needs a running engine, one at a time');

    // a throwing link releases busy and reports
    const bad = new MapControl({ diag: { available: true, physical: () => { throw new Error('boom'); } }, log: quiet,
        settings: { get: () => 'mhd' } });
    assert.strictEqual(await bad.setMap(2), false);
    assert.strictEqual(bad.busy, false);
    assert.ok(/boom/.test(bad.note));
    ok('maps: a failing link releases the busy flag');

    console.log('\nBimmhacks Tuner checks passed (' + passed + ' groups).');
}
