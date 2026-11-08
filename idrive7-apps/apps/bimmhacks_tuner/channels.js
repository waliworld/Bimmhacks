'use strict';
/**
 * channels.js - what Bimmhacks Tuner can show and log.
 *
 * Three kinds, all READ-ONLY:
 *   car      the head unit's own driving signals (kit/car.js): engine speed, speed, torque, coolant, oil, g
 *   dme      values asked from the engine unit with UDS 0x22 through the read-only diagnostic link
 *            (kit/readonly-diag.js), using the candidate lists in app.json config.dme, which are the
 *            ones M Vitals ships: the first candidate the car answers wins
 *   derived  computed from the others (gauge boost = absolute boost minus outside air pressure)
 *
 * The CSV column names are fixed here so a log opens the same way in every spreadsheet / plotting tool.
 */

const CHANNELS = [
    { key: 'rpm',         col: 'rpm',              label: 'Engine speed',       unit: 'rpm', d: 0, src: 'car' },
    { key: 'speed',       col: 'speed_kmh',        label: 'Speed',              unit: 'km/h', d: 0, src: 'car' },
    { key: 'torque',      col: 'torque_nm',        label: 'Torque',             unit: 'Nm',  d: 0, src: 'car' },
    { key: 'coolant',     col: 'coolant_c',        label: 'Coolant',            unit: '°C', d: 0, src: 'car' },
    { key: 'oil',         col: 'oil_c',            label: 'Oil',                unit: '°C', d: 0, src: 'car' },
    { key: 'gLong',       col: 'g_long',           label: 'Longitudinal g',     unit: 'g',   d: 2, src: 'car' },
    { key: 'gLat',        col: 'g_lat',            label: 'Lateral g',          unit: 'g',   d: 2, src: 'car' },
    { key: 'boost',       col: 'boost_bar',        label: 'Boost',              unit: 'bar', d: 2, src: 'derived' },
    { key: 'boostAbs',    col: 'boost_abs_kpa',    label: 'Boost (absolute)',   unit: 'kPa', d: 0, src: 'dme', cfg: 'boostActual' },
    { key: 'boostTarget', col: 'boost_target_kpa', label: 'Boost target',       unit: 'kPa', d: 0, src: 'dme', cfg: 'boostTarget' },
    { key: 'load',        col: 'load_pct',         label: 'Engine load',        unit: '%',   d: 0, src: 'dme', cfg: 'loadActual' },
    { key: 'timing',      col: 'timing_deg',       label: 'Ignition timing',    unit: '°', d: 1, src: 'dme', cfg: 'timing' },
    { key: 'iat',         col: 'iat_c',            label: 'Intake air',         unit: '°C', d: 0, src: 'dme', cfg: 'iat' },
];

const BY_KEY = {};
CHANNELS.forEach((c) => { BY_KEY[c.key] = c; });

const STD_ATMOSPHERE_KPA = 101.3;      // used until the engine unit has told the outside air pressure

const isNum = (x) => typeof x === 'number' && isFinite(x);

/** Gauge boost in bar from absolute boost and outside pressure (both kPa); null when there is no boost value. */
function gaugeBoost(absKpa, baroKpa) {
    if (!isNum(absKpa)) return null;
    const baro = isNum(baroKpa) ? baroKpa : STD_ATMOSPHERE_KPA;
    return (absKpa - baro) / 100;
}

/** The channels the engine unit is asked for (src dme) that the owner has not switched off. */
const dmeChannels = () => CHANNELS.filter((c) => c.src === 'dme');

/** One row of values {key: number|null} for every channel from the car data and the engine unit's values. */
function collect(carData, dmeValues, baro) {
    const out = {};
    const car = carData || {};
    const dme = dmeValues || {};
    CHANNELS.forEach((c) => {
        let v = null;
        if (c.src === 'car') v = car[c.key];
        else if (c.src === 'dme') v = dme[c.key];
        out[c.key] = isNum(v) ? v : null;
    });
    out.boost = gaugeBoost(out.boostAbs, baro);
    return out;
}

const fmt = (v, d) => (isNum(v) ? v.toFixed(d || 0) : '--');

module.exports = { CHANNELS, BY_KEY, STD_ATMOSPHERE_KPA, gaugeBoost, dmeChannels, collect, fmt, isNum };
