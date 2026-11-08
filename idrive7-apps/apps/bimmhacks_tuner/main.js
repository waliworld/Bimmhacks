'use strict';
/**
 * Bimmhacks Tuner - gauges, a datalogger, the engine unit's faults and (for a car whose DME already runs
 * a multi-map tune) map switching, in one app. By Bimmhacks (Mr. Wali).
 *
 * It does NOT flash, write or change a tune. Reads by default:
 *   Gauges   live engine speed, boost, load, ignition timing, temperatures and torque, with session peaks
 *   Datalog  record the drive to a CSV file on the head unit (tuner-YYYYMMDD-HHMMSS.csv in the app's data folder)
 *   Faults   the engine unit's (DME) fault codes; Clear needs two presses
 *   Maps     engine map 1-4 and Max Cooling for an already-tuned car; ships OFF (Settings > Map switching)
 *
 * Modules (each is unit-tested by kit/tools/test_bimmhacks_tuner.js):
 *   channels.js  what is shown and logged        logger.js  the CSV datalogger
 *   faults.js    engine-unit fault read / clear  maps.js    the gated map switch
 *
 * Engine-unit values (boost, load, timing, intake air) are read through kit/readonly-diag.js, which can only
 * send reads. Faults and maps use the Fault Scanner's / Engine Config's own requests (see those modules).
 */
const path = require('path');
const mgu = require('./kit/mgu');
const G = require('./kit/gauges');
const Car = require('./kit/car');
const { Diag } = require('./kit/diag');
const { ReadOnlyDiag } = require('./kit/readonly-diag');
const { DidReader } = require('./kit/dids');
const CH = require('./channels');
const { DataLogger } = require('./logger');
const { FaultControl } = require('./faults');
const { MapControl } = require('./maps');

const W = 1600, H = 520;
const MPH = 1.609344;
const PAGES = ['Gauges', 'Datalog', 'Faults', 'Maps'];
const STALE_MS = 3000;           // an engine-unit value older than this is "no value", not a fresh one
const RETRY_MS = 30000;          // a value the engine unit refused is asked again after this
const HISTORY_S = 60;

mgu.run({
    async start(ctx) {
        const { ui, settings, log } = ctx;
        const car = new Car(ctx, ['rpm', 'speed', 'accel', 'temps', 'torque']);
        // state the loops below read from their first run: declared first
        const logger = new DataLogger({ dir: path.join(ctx.dataDir, 'logs'), columns: CH.CHANNELS, log });
        let page = 0, selMap = 1, logNote = '';
        let row = {}, peaks = {}, hist = [], nextHist = 0, nextLog = 0, demo = false;
        const dmeCfg = Object.assign({ ecu: 18 }, (ctx.spec.config && ctx.spec.config.dme) || {});

        /* ---------- engine unit values: read-only link, polled while they matter ---------- */
        const link = new ReadOnlyDiag(ctx.signals, log);
        link.init();                                       // in the background: the screen must not wait for it
        const reader = new DidReader(link, { ecu: dmeCfg.ecu });
        const dme = {};                                    // key -> { v, t }
        const failUntil = {};
        let baro = CH.STD_ATMOSPHERE_KPA, baroAt = 0, baroTry = 0;
        let pollIdx = 0, polling = false;

        const wantDme = () => settings.get('dmeReads') && !car.data().demo && (logger.running || (ui.isVisible('MAIN') && page === 0));
        async function pollOne() {
            const list = CH.dmeChannels();
            const c = list[pollIdx++ % list.length];
            if ((failUntil[c.key] || 0) > Date.now()) return;
            const v = await reader.value(c.key, dmeCfg[c.cfg]);
            if (typeof v === 'number' && isFinite(v)) dme[c.key] = { v, t: Date.now() };
            else failUntil[c.key] = Date.now() + RETRY_MS;
        }
        async function pollBaro() {
            if (Date.now() - baroTry < 60000) return;
            baroTry = Date.now();
            const v = await reader.value('baro', dmeCfg.baro);
            if (typeof v === 'number' && isFinite(v)) { baro = v; baroAt = Date.now(); }
        }
        (async function pollLoop() {
            for (;;) {
                if (wantDme() && link.available && !polling) {
                    polling = true;
                    try { await pollBaro(); await pollOne(); } catch (e) { log.warn('dme poll: ' + e); }
                    polling = false;
                }
                await new Promise((r) => setTimeout(r, wantDme() ? 120 : 1000));
            }
        })();

        /** The demo drive has no engine unit: plausible values from the demo torque / rpm, flagged DEMO on screen. */
        function demoDme(d) {
            const tq = G.isNum(d.torque) ? d.torque : 40, rpm = G.isNum(d.rpm) ? d.rpm : 850;
            const load = Math.max(8, Math.min(100, tq / 6));
            const boostBar = Math.max(-0.4, (tq - 120) / 420 * 1.3);
            return { boostAbs: CH.STD_ATMOSPHERE_KPA + boostBar * 100, boostTarget: CH.STD_ATMOSPHERE_KPA + boostBar * 100 + 4,
                load, timing: 38 - rpm / 7000 * 26 - load / 12, iat: 24 + load * 0.2 };
        }
        function dmeValues(d) {
            if (d.demo) return demoDme(d);
            const out = {}, now = Date.now();
            Object.keys(dme).forEach((k) => { if (now - dme[k].t < STALE_MS) out[k] = dme[k].v; });
            return out;
        }

        /* ---------- one sample row (10 Hz): gauges, peaks, history, datalog ---------- */
        const resetPeaks = () => { peaks = {}; hist = []; };
        function tick() {
            const d = car.data();
            demo = !!d.demo;
            row = CH.collect(d, dmeValues(d), baro);
            const now = Date.now();
            CH.CHANNELS.forEach((c) => {
                const v = row[c.key];
                if (!G.isNum(v)) return;
                const p = peaks[c.key] || (peaks[c.key] = { min: v, max: v });
                if (v < p.min) p.min = v;
                if (v > p.max) p.max = v;
            });
            if (now >= nextHist) { nextHist = now + 250; hist.push([now, row.boost, row.rpm]); while (hist.length && now - hist[0][0] > HISTORY_S * 1000) hist.shift(); }
            if (logger.running && now >= nextLog) {
                nextLog = now + 1000 / Math.max(1, Math.min(10, Number(settings.get('logRate')) || 2));
                // never log the demo drive as if it were the car: stop, and say so
                if (demo) { logger.stop('demo data'); logNote = 'Stopped: no live car data'; actions(); }
                else if (logger.add(row, now) === false) { logNote = 'Log stopped: size limit reached'; actions(); }
            }
        }
        setInterval(tick, 100);
        tick();

        /* ---------- engine-unit link for faults and maps (can write: used only by those two) ---------- */
        const wlink = new Diag(ctx.signals, log);
        wlink.quiet = true;
        wlink.init();
        const faults = new FaultControl({ diag: wlink, log });
        const maps = new MapControl({ diag: wlink, log, settings,
            engine: { rpm: () => { const d = car.data(); return d.demo ? null : d.rpm; } } });

        /* ---------- pages ---------- */
        const mph = () => settings.get('speedUnit') === 'mph';
        const speed = (kmh) => (G.isNum(kmh) ? (mph() ? kmh / MPH : kmh) : null);

        /**
         * A tile that is always drawn (the page must show the state in bar-button mode too); it is tappable only
         * where the kit runs the screen (cv.own), like G.button. o: { on, selected, busy }.
         */
        function softButton(cv, t, x, y, w, h, title, sub, run, key, o) {
            const c = t.sel || t.accent, r = Math.min(18, h / 2);
            cv.roundRect(x, y, w, h, r, o.on ? G.withAlpha(c, o.busy ? 0.4 : 0.85) : G.withAlpha('#ffffff', 0.07));
            cv.strokeRoundRect(x, y, w, h, r, o.selected ? 3 : 1.5, o.selected ? c : G.withAlpha('#ffffff', o.busy ? 0.15 : 0.3));
            const ink = o.on ? '#ffffff' : o.busy ? t.dim : t.text;
            cv.text(title, x + w / 2, y + h / 2 + (sub ? -2 : 12), sub ? 34 : 30, ink, { align: 'center', font: 'bold', spacing: 1.5 });
            if (sub) cv.text(sub, x + w / 2, y + h / 2 + 30, 20, o.on ? '#ffffff' : t.dim, { align: 'center', spacing: 1 });
            if (cv.own && !o.busy && run) cv.target(x, y, w, h, run, key);
        }

        function drawGauges(cv, t) {
            const tile = (i, label, key, o) => {
                const col = i % 4, r = Math.floor(i / 4);
                const w = 380, h = 150, x = 24 + col * (w + 10), y = 52 + r * (h + 10);
                const p = peaks[key];
                G.stat(cv, t, x, y, w, h, Object.assign({ label, value: row[key], decimals: 0, size: 64,
                    sub: p ? 'peak ' + CH.fmt(p.max, o && o.decimals != null ? o.decimals : 0) + ' · min ' + CH.fmt(p.min, o && o.decimals != null ? o.decimals : 0) : '' }, o));
            };
            tile(0, 'Engine speed  rpm', 'rpm', { zones: [{ from: 6500, to: 99999, color: 'crit' }] });
            tile(1, 'Boost  bar', 'boost', { decimals: 2, size: 64 });
            tile(2, 'Engine load  %', 'load');
            tile(3, 'Ignition timing  °', 'timing', { decimals: 1 });
            tile(4, 'Coolant  °C', 'coolant', { zones: [{ from: 108, to: 999, color: 'crit' }] });
            tile(5, 'Oil  °C', 'oil', { zones: [{ from: 125, to: 999, color: 'crit' }] });
            tile(6, 'Intake air  °C', 'iat');
            tile(7, 'Torque  Nm', 'torque');
            // the last minute of boost under the tiles
            const pts = hist.filter((h) => G.isNum(h[1])).map((h) => [(h[0] - Date.now()) / 1000, h[1]]);
            const missing = !settings.get('dmeReads') ? 'Engine unit reads are off (Settings)' : (!demo && !link.available ? 'Waiting for the diagnostic link' : 'No boost value from this car yet');
            G.chart(cv, t, 24, 376, 1552, 136, { xMin: -HISTORY_S, xMax: 0, yMin: -0.5, yMax: 2, yStep: 0.5, xStep: 15, xUnit: 's',
                yFmt: (v) => v.toFixed(1), padLeft: 50, series: pts.length > 2 ? [{ pts, color: t.accent2, width: 3, fill: true }] : [], empty: missing });
            cv.text('BOOST  last ' + HISTORY_S + ' s', 104, 399, 16, t.dim, { font: 'bold', spacing: 2 });
        }

        function drawLog(cv, t) {
            const sh = G.panel(cv, t, 24, 52, 760, 220, 'Datalog');
            const run = logger.running;
            const state = run ? 'RECORDING' : 'STOPPED';
            G.pill(cv, t, 48, 100, 190, 34, state, { color: run ? 'crit' : 'dim', filled: run ? 0.35 : 0.15 });
            const sum = logger.last;
            cv.text(run ? logger.name : (sum ? sum.name : 'No log yet this session'), 256, 126, 24, t.text, { font: 'bold' });
            const secs = run ? Math.round(logger.elapsedMs / 100) / 10 : (sum ? sum.seconds : 0);
            const rows = run ? logger.rows : (sum ? sum.rows : 0);
            cv.text(CH.fmt(secs, 0) + ' s', 48, 200, 56, t.text, { font: 'num' });
            cv.text(rows + ' rows  ·  ' + CH.CHANNELS.length + ' channels  ·  ' + (Number(settings.get('logRate')) || 2) + ' Hz', 48, 240, 22, t.dim);
            if (logNote || (sum && sum.reason && !run && sum.reason !== 'stopped')) cv.text(logNote || sum.reason, 48, 262, 20, t.warn);
            G.panel(cv, t, 800, 52, 776, 220, 'What it records');
            const names = CH.CHANNELS.map((c) => c.label);
            names.forEach((n, i) => cv.text(n, 824 + (i % 4) * 190, 140 + Math.floor(i / 4) * 28, 20, i < 7 ? t.text : t.accent2));
            cv.text('Blue = engine unit values (when the car answers).', 824, 262, 18, t.dim);
            G.panel(cv, t, 24, 284, 1552, 228, 'Saved logs  ·  newest first');
            const files = logger.list().slice(0, 4);
            if (!files.length) cv.text('Nothing recorded yet. Press Start log, drive, press Stop log.', 48, 378, 22, t.dim);
            files.forEach((f, i) => {
                cv.text(f.name, 48, 378 + i * 30, 22, i === 0 ? t.text : t.dim, { font: 'bold' });
                cv.text(Math.max(1, Math.round(f.size / 1024)) + ' KB', 560, 378 + i * 30, 22, t.dim);
            });
            cv.text('On the head unit: ' + path.join(ctx.dataDir, 'logs'), 48, 504, 16, t.dim);
        }

        function drawFaults(cv, t) {
            const sh = G.panel(cv, t, 24, 52, 1552, 460, 'Engine unit (DME) faults');
            if (faults.faults === null) {
                cv.text(faults.note || 'Press Read faults', 48, 140, 34, faults.armed ? t.warn : t.dim, { font: 'bold' });
                cv.text('Ignition on. Codes only; texts are in the Fault Scanner app.', 48, 182, 22, t.dim);
            } else if (!faults.faults.length) {
                cv.text('No faults stored', 48, 140, 44, t.ok, { font: 'bold' });
            } else {
                const rows = faults.faults.slice(0, 9).map((f) => [f.code, { text: f.label, color: f.sev === 'dim' ? 'dim' : f.sev }]);
                G.table(cv, t, 48, 110, 700, [{ label: 'Code', w: 260, font: 'bold' }, { label: 'State', w: 400 }], rows, { rowH: 40, size: 24 });
                if (faults.faults.length > 9) cv.text('+ ' + (faults.faults.length - 9) + ' more', 48, 504, 20, t.dim);
            }
            if (faults.armed) cv.text(faults.note, 800, 130, 26, t.warn, { font: 'bold' });
            else if (faults.faults !== null) cv.text(faults.note, 800, 130, 24, t.dim);
            cv.text('Read, then Clear (press twice). Clearing does not fix the cause - a fault that is still there returns.', 800, 496, 18, t.dim);
        }

        function drawMaps(cv, t) {
            const armed = maps.armed;
            G.panel(cv, t, 24, 52, 1552, 460, 'Engine map  ·  car already running a multi-map tune');
            G.pill(cv, t, 48, 96, 230, 34, armed ? 'ARMED (MHD)' : 'DISARMED', { color: armed ? 'warn' : 'dim', filled: armed ? 0.3 : 0.15 });
            const lead = armed ? 'Sends the map switch to the engine unit. A press counts when the DME confirms it.'
                : 'Off by default: nothing is sent. Turn on in Settings > Map switching, only if your car already runs an MHD multi-map tune.';
            cv.text(lead, 300, 122, 20, armed ? t.text : t.warn);
            const busy = !armed || maps.busy;
            for (let n = 1; n <= 4; n++) {
                const on = maps.map === n;
                softButton(cv, t, 48 + (n - 1) * 380, 160, 350, 100, 'MAP ' + n, on ? 'accepted' : selMap === n ? 'selected' : '', () => applyMap(n),
                    'map' + n, { on, selected: selMap === n, busy });
            }
            softButton(cv, t, 48, 290, 350, 76, 'MAX COOLING ON', maps.cooling === 'ON' ? 'accepted' : '', () => applyCooling(true),
                'cool-on', { on: maps.cooling === 'ON', busy });
            softButton(cv, t, 428, 290, 350, 76, 'MAX COOLING OFF', maps.cooling === 'OFF' ? 'accepted' : '', () => applyCooling(false),
                'cool-off', { on: maps.cooling === 'OFF', busy });
            cv.text(maps.note || (armed ? 'Pick a map' : ''), 48, 408, 28, maps.noteType === 'go' ? t.ok : maps.noteType === 'warn' ? t.warn : t.dim, { font: 'bold' });
            cv.text('Max Cooling needs the engine running. This app never flashes or writes a tune.', 48, 440, 20, t.dim);
            cv.text('Bar buttons: "Next map" picks, "Send map" sends it.', 48, 466, 20, t.dim);
            const rpm = maps.rpm();
            cv.text('Engine ' + (rpm === null ? 'speed unknown' : Math.round(rpm) + ' rpm'), 1576 - 24, 504, 20, t.dim, { align: 'right' });
        }

        async function render() {
            const t = ctx.theme(), cv = ctx.canvas(W, H).clear(t.bg);
            [drawGauges, drawLog, drawFaults, drawMaps][page](cv, t);
            G.frame(cv, t, PAGES, page, [demo && { text: 'DEMO' }]);
            await ui.image('MAIN_GFX', cv);
        }

        /* ---------- actions (the bar's two buttons, and the page's own) ---------- */
        const label = (id, text) => ui.text(id, text);
        function actions() {
            if (page === 0) { label('BTN_ACTION', 'Reset peaks'); ui.visible('BTN_ACTION2', false); }
            else if (page === 1) { label('BTN_ACTION', logger.running ? 'Stop log' : 'Start log'); ui.visible('BTN_ACTION2', false); }
            else if (page === 2) { label('BTN_ACTION', 'Read faults'); label('BTN_ACTION2', faults.armed ? 'Yes, clear' : 'Clear'); ui.visible('BTN_ACTION2', true); }
            else { label('BTN_ACTION', 'Next map (' + selMap + ')'); label('BTN_ACTION2', 'Send map ' + selMap); ui.visible('BTN_ACTION2', true); }
        }
        const goto = (p) => { page = (p + PAGES.length) % PAGES.length; if (page !== 1) logNote = ''; actions(); return render(); };
        const applyMap = async (n) => { selMap = n; actions(); await maps.setMap(n); return render(); };
        const applyCooling = async (on) => { await maps.setCooling(on); return render(); };

        function toggleLog() {
            if (logger.running) { logNote = ''; logger.stop('stopped'); }
            else if (car.data().demo) { logNote = 'No live car data - start the car (demo data is never logged)'; }
            else { logNote = ''; nextLog = 0; try { logger.start(); } catch (e) { logNote = 'Could not start: ' + (e.message || e); log.warn('datalog: ' + e); } }
            actions(); return render();
        }
        async function readFaults() { faults.note = 'Reading...'; await render(); await faults.read(); actions(); return render(); }
        async function clearFaults() {
            const r = await faults.clear();
            actions(); await render();
            if (r.sent) { await faults.read(); actions(); await render(); }
            else setTimeout(() => { actions(); render(); }, 8100);           // the arm runs out: the label goes back
        }

        ui.onClick('BTN_PAGE', () => goto(page + 1));
        ui.onClick('BTN_PREV', () => goto(page - 1));
        ctx.onTab((i) => i !== page && goto(i));
        ui.onClick('BTN_ACTION', () => {
            if (page === 0) { resetPeaks(); return render(); }
            if (page === 1) return toggleLog();
            if (page === 2) return readFaults();
            selMap = selMap % 4 + 1; actions(); return render();
        });
        ui.onClick('BTN_ACTION2', () => {
            if (page === 2) return clearFaults();
            if (page === 3) return applyMap(selMap);
            return null;
        });
        process.on('exit', () => { if (logger.running) logger.stop('app closed'); });
        actions();
        ctx.loop('MAIN', 5, render);
        render().then(() => log.info('first frame done')).catch((e) => log.warn('first render: ' + e));
    },
});
