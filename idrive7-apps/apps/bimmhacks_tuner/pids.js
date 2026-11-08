'use strict';
/**
 * pids.js - the engine-unit values Bimmhacks Tuner can read and log, in packs the owner switches on and off.
 *
 * Every value is READ with UDS 0x22 (or OBD service 01) through the read-only diagnostic link. Where the
 * numbers come from, so nothing here is a guess:
 *
 *   tuning / engine / cylinders   BMW's own SGBD for the DME8FF_R engine unit (B48 / B58 ...): the catalogue
 *                                 apps/live_data/builtin/DME8FF_R.json. Each entry keeps the catalogue's DID,
 *                                 type, factor and offset in `from`, and kit/tools/test_bimmhacks_tuner.js
 *                                 checks every one against that file, so a typo cannot slip in.
 *   standard OBD-II               SAE J1979 PIDs (fuel trims, MAF, throttle, battery ...), each as two
 *                                 candidates: UDS 22 F4xx, then OBD service 01 (some engine units refuse one).
 *   fuel pressure                 M Vitals' lists (apps/car_vitals/app.json config.dme), reused as they are.
 *   mhd                           the eight identifiers an MHD capture was seen reading (22 3010 / 3020 / 3030 /
 *                                 3031 / 3032 / 37FE / 586A / 4098; apps/live_adjustments/README.md). Neither BMW's
 *                                 catalogue nor this repository says what they mean, so they are logged RAW:
 *                                 the exact bytes as hex, never a made-up name or scale.
 *   custom                        the owner's own custom_pids.json (custom.js).
 *
 * A car answers what it answers: a value the engine unit does not have is refused (NRC) or silent, and the
 * poller leaves it alone for a while. The Datalog / PIDs pages say which are answering.
 *
 * channel = { key, col (CSV column), label, unit, d (decimals), pack, rate: 'fast' | 'slow',
 *             cand: [DidReader candidates]  |  raw: { did: '3010', len: 16 } }
 * A candidate is kit/dids.js's: { did | obd, bytes, at, factor, offset, signed, min, max }; the value is
 * raw * factor + offset, and a value outside min..max counts as no answer.
 */
const TIMEOUT_MS = 1200;                     // a DME answers in tens of ms; DidReader's own default is 4 s
const SIZE = { u8: 1, s8: 1, u16: 2, s16: 2, u32: 4, s32: 4 };
const round = (x) => Number(x.toPrecision(12));

/** A BMW DME identifier from the SGBD catalogue: type, catalogue factor k and offset o; scale converts the unit. */
function bmw(did, type, k, o, scale, min, max) {
    const s = scale == null ? 1 : scale;
    return [{ did, bytes: SIZE[type], at: 0, factor: round((k == null ? 1 : k) * s), offset: round((o || 0) * s),
        signed: type[0] === 's', min, max, timeoutMs: TIMEOUT_MS, from: { did, type, k: k == null ? 1 : k, o: o || 0, scale: s } }];
}

/** A standard OBD-II PID as two candidates: UDS 22 F4xx, then OBD service 01. */
function std(pid, bytes, factor, offset, min, max) {
    const c = { bytes, at: 0, factor, offset, min, max, timeoutMs: TIMEOUT_MS };
    return [Object.assign({ did: 'F4' + pid }, c), Object.assign({ obd: pid }, c)];
}

const LAMBDA = 0.000244140625;               // BMW: raw 4096 = lambda 1.0 (1 / 4096)
const LAMBDA_5640 = 0.00024414;              // the catalogue's own (rounded) constant for 5640
const PCT255 = 100 / 255;                    // OBD: A * 100 / 255 %
const with_ = (list) => list.reduce((a, b) => a.concat(b), []);

/* ---- M Vitals' fuel pressure lists (apps/car_vitals/app.json config.dme.hpfp / lpfp), unchanged ---- */
const MV = (c) => Object.assign({ timeoutMs: TIMEOUT_MS }, c);
const HPFP = [
    MV({ did: 'F423', bytes: 2, at: 0, factor: 0.1, offset: 0, min: 0, max: 500 }),
    MV({ did: 'F459', bytes: 2, at: 0, factor: 0.1, offset: -1, min: 0, max: 500 }),
    MV({ obd: '23', bytes: 2, at: 0, factor: 0.1, offset: 0, min: 0, max: 500 }),
];
const LPFP = [
    MV({ did: 'F40A', bytes: 1, at: 0, factor: 0.03, offset: 0, min: 0.5, max: 8 }),
    MV({ did: 'F422', bytes: 2, at: 0, factor: 0.00079, offset: 0, min: 0.5, max: 15 }),
    MV({ obd: '0A', bytes: 1, at: 0, factor: 0.03, offset: 0, min: 0.5, max: 8 }),
    MV({ obd: '22', bytes: 2, at: 0, factor: 0.00079, offset: 0, min: 0.5, max: 15 }),
];

const ch = (pack, rate, key, col, label, unit, d, cand) => ({ key, col, label, unit, d, pack, rate, cand });

/* ------------------------------------------------------------------------------------------ tuning */
const TUNING = [
    ch('tuning', 'fast', 'lambda', 'lambda', 'Lambda', '', 3, with_([bmw('5889', 'u16', LAMBDA, 0, 1, 0.4, 2.5), bmw('5640', 'u16', LAMBDA_5640, 0, 1, 0.4, 2.5), std('24', 2, 2 / 65536, 0, 0.4, 2.5)])),
    ch('tuning', 'fast', 'lambdaTarget', 'lambda_target', 'Lambda target', '', 3, with_([bmw('5816', 'u16', LAMBDA, 0, 1, 0.4, 2.5), std('44', 2, 2 / 65536, 0, 0.4, 2.5)])),
    ch('tuning', 'fast', 'knock', 'knock_retard_deg', 'Knock retard (all cyl)', '°CA', 1, bmw('554C', 's8', 0.375, 0, 1, -48, 48)),
    ch('tuning', 'fast', 'ignition', 'ignition_deg', 'Ignition angle', '°CA', 1, bmw('59FA', 's8', 0.75, 0, 1, -96, 96)),
    ch('tuning', 'fast', 'charge', 'charge_kpa', 'Charge pressure (before throttle)', 'kPa', 0, with_([bmw('4205', 'u16', 0.078125, 0, 0.1, 20, 520), bmw('57C1', 'u16', 0.078125, 0, 0.1, 20, 520)])),
    ch('tuning', 'fast', 'boostDuty', 'boost_duty_pct', 'Boost actuator duty', '%', 1, bmw('4AAB', 'u16', 0.001525878906, 0, 1, 0, 100)),
    ch('tuning', 'fast', 'pedal', 'pedal_pct', 'Accelerator pedal', '%', 0, with_([bmw('480B', 's16', 0.01220703, 0, 1, -1, 101), bmw('5814', 's16', 0.01220703, 0, 1, -1, 101), std('49', 1, PCT255, 0, -1, 101)])),
    ch('tuning', 'slow', 'boostDev', 'boost_dev_kpa', 'Boost control deviation', 'kPa', 1, bmw('4536', 's16', 0.125, 0, 0.1, -300, 300)),
    ch('tuning', 'slow', 'mapMeas', 'map_kpa', 'Intake manifold pressure', 'kPa', 0, with_([bmw('4A2D', 'u16', 0.078125, 0, 0.1, 5, 520), std('0B', 1, 1, 0, 5, 255)])),
    ch('tuning', 'slow', 'wastegate', 'wastegate_pct', 'Wastegate position', '%', 0, bmw('5927', 'u8', 0.390625, 0, 1, 0, 100)),
    ch('tuning', 'slow', 'throttle', 'throttle_pct', 'Throttle', '%', 0, with_([bmw('4600', 's16', 0.0078125, 0, 1, -1, 101), std('11', 1, PCT255, 0, -1, 101)])),
    ch('tuning', 'slow', 'airMass', 'airmass_kgh', 'Air mass', 'kg/h', 0, with_([bmw('5812', 'u16', 0.1, 0, 1, 0, 3000), bmw('4ABC', 'u16', 0.1, 0, 1, 0, 3000), std('10', 2, 0.036, 0, 0, 3000)])),
    ch('tuning', 'slow', 'stft', 'stft_pct', 'Short-term fuel trim', '%', 1, std('06', 1, 100 / 128, -100, -100, 100)),
    ch('tuning', 'slow', 'ltft', 'ltft_pct', 'Long-term fuel trim', '%', 1, std('07', 1, 100 / 128, -100, -100, 100)),
    ch('tuning', 'slow', 'mixture', 'mixture_adapt', 'Mixture adaptation', 'x', 3, bmw('4A9D', 'u16', 3.0517578e-05, 0, 1, 0.5, 1.5)),
    ch('tuning', 'slow', 'knockAdapt', 'knock_adapt_deg', 'Knock adaptation', '°', 1, bmw('554A', 's16', 0.1, 0, 1, -30, 30)),
    ch('tuning', 'slow', 'rail', 'rail_bar', 'Rail pressure', 'bar', 0, with_([bmw('58EF', 'u16', 0.0005, 0, 10, 0, 400), bmw('5658', 'u16', 0.002, 0, 10, 0, 400), HPFP])),
    ch('tuning', 'slow', 'railTarget', 'rail_target_bar', 'Rail pressure target', 'bar', 0, with_([bmw('5837', 'u16', 0.0005, 0, 10, 0, 400), bmw('56C1', 'u16', 0.002, 0, 10, 0, 400)])),
    ch('tuning', 'slow', 'lpfp', 'lpfp_bar', 'Low-pressure fuel', 'bar', 1, LPFP),
    ch('tuning', 'slow', 'ethanol', 'ethanol_pct', 'Ethanol in fuel', '%', 0, bmw('550D', 'u8', 0.39215686, 0, 1, 0, 100)),
];

/* ------------------------------------------------------------------------------------------ engine */
const ENGINE = [
    ch('engine', 'slow', 'vanosInT', 'vanos_in_target_deg', 'VANOS intake target', '°CA', 1, bmw('452B', 's16', 0.1, 0, 1, -50, 150)),
    ch('engine', 'slow', 'vanosInA', 'vanos_in_actual_deg', 'VANOS intake actual', '°CA', 1, bmw('452E', 's16', 0.1, 0, 1, -50, 150)),
    ch('engine', 'slow', 'vanosExT', 'vanos_ex_target_deg', 'VANOS exhaust target', '°CA', 1, bmw('452A', 's16', 0.1, 0, 1, -50, 150)),
    ch('engine', 'slow', 'vanosExA', 'vanos_ex_actual_deg', 'VANOS exhaust actual', '°CA', 1, bmw('452C', 's16', 0.1, 0, 1, -50, 150)),
    ch('engine', 'slow', 'oilPressure', 'oil_pressure_bar', 'Oil pressure', 'bar', 1, with_([bmw('586F', 's16', 1, 0, 0.001, 0, 12), bmw('4436', 'u16', 1, 0, 0.001, 0, 12)])),
    ch('engine', 'slow', 'exhaustTemp', 'exhaust_model_c', 'Exhaust after catalyst (model)', '°C', 0, with_([bmw('5896', 's16', 0.1, -273.14, 1, -40, 1200), bmw('582F', 's16', 0.1, -273.14, 1, -40, 1200)])),
    ch('engine', 'slow', 'fuelTemp', 'fuel_temp_c', 'Fuel temperature', '°C', 0, bmw('4AD5', 'u8', 0.75, -48, 1, -40, 150)),
    ch('engine', 'slow', 'fuelFlow', 'fuel_flow_kgh', 'Fuel mass flow', 'kg/h', 1, bmw('4521', 'u16', 0.1, 0, 1, 0, 400)),
    ch('engine', 'slow', 'injAdapt', 'inj_adapt', 'Injector flow adaptation', 'x', 3, bmw('546E', 'u16', 3.052e-05, 0, 1, 0.5, 1.5)),
    ch('engine', 'slow', 'torqueDme', 'torque_dme_nm', 'Engine torque (DME)', 'Nm', 0, bmw('5891', 's16', 0.5, 0, 1, -200, 1500)),
    ch('engine', 'slow', 'voltage', 'battery_v', 'Battery voltage', 'V', 1, with_([bmw('5853', 'u16', 0.001, 0, 1, 6, 18), std('42', 2, 0.001, 0, 6, 18)])),
    ch('engine', 'slow', 'catTemp', 'cat_temp_c', 'Catalyst temperature', '°C', 0, std('3C', 2, 0.1, -40, -40, 1000)),
    ch('engine', 'slow', 'absLoad', 'abs_load_pct', 'Absolute load', '%', 0, std('43', 2, PCT255, 0, 0, 400)),
    ch('engine', 'slow', 'fuelLevel', 'fuel_level_pct', 'Fuel level', '%', 0, std('2F', 1, PCT255, 0, 0, 100.5)),
    ch('engine', 'slow', 'ambient', 'ambient_c', 'Outside temperature', '°C', 1, with_([bmw('5512', 'u8', 0.5, -40, 1, -40, 70), std('46', 1, 1, -40, -40, 70)])),
];

/* -------------------------------------------------------------------------------------- cylinders */
const CYL = [
    ['4A49', '4A4A', '4A4C', '4A4D', '4A4E', '4A4F'],          // ignition angle, cylinder 1..6
    ['4A30', '4A31', '4A32', '4A33', '4A34', '4A35'],          // rough running
    ['4B23', '4B24', '4B25', '4B30', '4B31', '4B32'],          // misfire counter
];
const CYLINDERS = [];
for (let i = 0; i < 6; i++) {
    const n = i + 1;
    CYLINDERS.push(ch('cylinders', 'slow', 'ign' + n, 'ign_cyl' + n + '_deg', 'Ignition angle, cyl ' + n, '°CA', 1, bmw(CYL[0][i], 's16', 0.1, 0, 1, -60, 100)));
}
for (let i = 0; i < 6; i++) {
    const n = i + 1;
    CYLINDERS.push(ch('cylinders', 'slow', 'rough' + n, 'rough_cyl' + n, 'Rough running, cyl ' + n, 'rad/s²', 2, bmw(CYL[1][i], 's16', 0.0625, 0, 1, -2048, 2048)));
}
for (let i = 0; i < 6; i++) {
    const n = i + 1;
    CYLINDERS.push(ch('cylinders', 'slow', 'misfire' + n, 'misfire_cyl' + n, 'Misfire count, cyl ' + n, '', 0, bmw(CYL[2][i], 'u16', null, 0, 1, 0, 65534)));
}

/* ------------------------------------------------------------------------------------- MHD (raw) */
const MHD_DIDS = ['3010', '3020', '3030', '3031', '3032', '37FE', '586A', '4098'];
const RAW_LEN = 16;                          // bytes logged per identifier (a longer reply is cut, never padded)
const MHD = MHD_DIDS.map((did) => ({
    key: 'mhd' + did, col: 'mhd_' + did.toLowerCase() + '_raw', label: 'MHD capture ' + did + ' (raw)', unit: 'hex', d: 0,
    pack: 'mhd', rate: 'slow', raw: { did, len: RAW_LEN },
}));

const PACKS = [
    { id: 'basic', label: 'Basic', setting: null, note: 'boost, load, timing, intake air (always on)' },
    { id: 'tuning', label: 'Tuning', setting: 'packTuning', note: 'lambda, knock, ignition, charge pressure, boost duty, trims, rail pressure ...' },
    { id: 'engine', label: 'Engine', setting: 'packEngine', note: 'VANOS, oil pressure, exhaust, fuel and battery values' },
    { id: 'cylinders', label: 'Cylinders', setting: 'packCylinders', note: 'per-cylinder ignition angle, rough running, misfires' },
    { id: 'mhd', label: 'MHD capture', setting: 'packMhd', note: 'the 8 identifiers an MHD capture reads, logged raw (hex)' },
    { id: 'custom', label: 'Custom', setting: 'packCustom', note: 'your own custom_pids.json' },
];

const BY_PACK = { tuning: TUNING, engine: ENGINE, cylinders: CYLINDERS, mhd: MHD };

module.exports = { TUNING, ENGINE, CYLINDERS, MHD, MHD_DIDS, RAW_LEN, PACKS, BY_PACK, TIMEOUT_MS, SIZE, bmw, std, HPFP, LPFP };
