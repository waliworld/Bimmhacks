'use strict';
/**
 * channels.js - what Bimmhacks Tuner shows and logs: the base channels, and the packs the owner switched on.
 *
 * Four kinds of channel, all READ-ONLY:
 *   car      the head unit's own driving signals (kit/car.js): engine speed, speed, torque, coolant, oil, g
 *   dme      a value asked from the engine unit with UDS 0x22 / OBD service 01 through kit/readonly-diag.js,
 *            from a list of candidates (the first one the car answers wins)
 *   raw      an identifier logged as its exact bytes in hex, with no meaning claimed (the MHD capture pack,
 *            and "type": "hex" entries of custom_pids.json)
 *   derived  computed from the others (gauge boost = absolute boost minus outside air pressure)
 *
 * The base set (CHANNELS) is always there; pids.js has the packs, custom.js the owner's own. activeChannels()
 * puts together what is on right now, in the order of the CSV columns.
 */
const PIDS = require('./pids');

const CHANNELS = [
    { key: 'rpm',         col: 'rpm',              label: 'Engine speed',       unit: 'rpm', d: 0, src: 'car', pack: 'base' },
    { key: 'speed',       col: 'speed_kmh',        label: 'Speed',              unit: 'km/h', d: 0, src: 'car', pack: 'base' },
    { key: 'torque',      col: 'torque_nm',        label: 'Torque',             unit: 'Nm',  d: 0, src: 'car', pack: 'base' },
    { key: 'coolant',     col: 'coolant_c',        label: 'Coolant',            unit: '°C', d: 0, src: 'car', pack: 'base' },
    { key: 'oil',         col: 'oil_c',            label: 'Oil',                unit: '°C', d: 0, src: 'car', pack: 'base' },
    { key: 'gLong',       col: 'g_long',           label: 'Longitudinal g',     unit: 'g',   d: 2, src: 'car', pack: 'base' },
    { key: 'gLat',        col: 'g_lat',            label: 'Lateral g',          unit: 'g',   d: 2, src: 'car', pack: 'base' },
    { key: 'boost',       col: 'boost_bar',        label: 'Boost',              unit: 'bar', d: 2, src: 'derived', pack: 'base' },
    // the basic engine-unit values: their candidate lists are in app.json config.dme (M Vitals' own)
    { key: 'boostAbs',    col: 'boost_abs_kpa',    label: 'Boost (absolute)',   unit: 'kPa', d: 0, src: 'dme', pack: 'basic', rate: 'fast', cfg: 'boostActual' },
    { key: 'boostTarget', col: 'boost_target_kpa', label: 'Boost target',       unit: 'kPa', d: 0, src: 'dme', pack: 'basic', rate: 'fast', cfg: 'boostTarget' },
    { key: 'load',        col: 'load_pct',         label: 'Engine load',        unit: '%',   d: 0, src: 'dme', pack: 'basic', rate: 'fast', cfg: 'loadActual' },
    { key: 'timing',      col: 'timing_deg',       label: 'Ignition timing',    unit: '°', d: 1, src: 'dme', pack: 'basic', rate: 'fast', cfg: 'timing' },
    { key: 'iat',         col: 'iat_c',            label: 'Intake air',         unit: '°C', d: 0, src: 'dme', pack: 'basic', rate: 'slow', cfg: 'iat' },
];

const BY_KEY = {};
CHANNELS.forEach((c) => { BY_KEY[c.key] = c; });

const STD_ATMOSPHERE_KPA = 101.3;      // used until the engine unit has told the outside air pressure

const isNum = (x) => typeof x === 'number' && isFinite(x);
const isRaw = (x) => typeof x === 'string' && /^[0-9A-F]{0,64}$/.test(x);
/** A logged value: a number, or the hex text of a raw channel. */
const isValue = (x) => isNum(x) || isRaw(x);

/** Gauge boost in bar from absolute boost and outside pressure (both kPa); null when there is no boost value. */
function gaugeBoost(absKpa, baroKpa) {
    if (!isNum(absKpa)) return null;
    const baro = isNum(baroKpa) ? baroKpa : STD_ATMOSPHERE_KPA;
    return (absKpa - baro) / 100;
}

/** The basic engine-unit channels (candidates in app.json config.dme). */
const dmeChannels = () => CHANNELS.filter((c) => c.src === 'dme');

/**
 * Give a channel its candidates: config.dme[<cfg or key>] from app.json if the owner (or the manifest) has a list,
 * else the channel's own. Every candidate gets the short wait (pids.TIMEOUT_MS) unless it names its own.
 */
function withCandidates(ch, dmeCfg) {
    if (ch.raw) return Object.assign({ src: 'raw' }, ch);
    const own = dmeCfg && Array.isArray(dmeCfg[ch.cfg || ch.key]) ? dmeCfg[ch.cfg || ch.key] : ch.cand;
    const cand = (own || []).map((c) => Object.assign({ timeoutMs: PIDS.TIMEOUT_MS }, c));
    return Object.assign({ src: 'dme' }, ch, { cand });
}

/**
 * The channels that are on, in CSV column order: the base ones, then the packs whose setting is on, then the
 * owner's custom ones.
 *   settings: { get(key) }   (dmeReads, packTuning, packEngine, packCylinders, packMhd, packCustom)
 *   custom:   channels from custom.js parseCustom()
 * With dmeReads off there are no engine-unit channels at all (the car's own signals only).
 */
function activeChannels(settings, custom, dmeCfg) {
    const on = (name) => !!settings.get(name);
    const out = CHANNELS.filter((c) => c.src !== 'dme').concat([]);
    const base = out.slice();
    if (!on('dmeReads')) return base;
    dmeChannels().forEach((c) => out.push(withCandidates(c, dmeCfg)));
    PIDS.PACKS.forEach((p) => {
        if (!p.setting || p.id === 'custom' || !on(p.setting)) return;
        (PIDS.BY_PACK[p.id] || []).forEach((c) => out.push(withCandidates(c, dmeCfg)));
    });
    if (on('packCustom')) (custom || []).forEach((c) => out.push(withCandidates(c, dmeCfg)));
    return out;
}

/**
 * One row of values {key: number | 'HEX' | null} for every channel in the list, from the car data and the
 * engine unit's values. Boost is worked out here. A value that is not a number (or the hex of a raw channel) is null.
 */
function collect(carData, dmeValues, baro, channels) {
    const car = carData || {}, dme = dmeValues || {};
    const out = {};
    (channels || CHANNELS).forEach((c) => {
        let v = null;
        if (c.src === 'car') v = car[c.key];
        else if (c.src === 'dme') v = dme[c.key];
        else if (c.src === 'raw') v = dme[c.key];
        out[c.key] = c.src === 'raw' ? (isRaw(v) ? v : null) : (isNum(v) ? v : null);
    });
    out.boost = gaugeBoost(out.boostAbs, baro);
    return out;
}

const fmt = (v, d) => (isNum(v) ? v.toFixed(d || 0) : '--');

module.exports = { CHANNELS, BY_KEY, STD_ATMOSPHERE_KPA, gaugeBoost, dmeChannels, withCandidates, activeChannels, collect, fmt, isNum, isRaw, isValue };
