'use strict';
/*
 * mgu-check.js - a read-only report on the cluster: MGU Styles, the phone's
 * map (Maps on cluster) and the cluster renderer, for the first drives of a
 * new release. Run on the head unit (Node 8), as root or as any user in
 * tm_oap_app:
 *
 *   node mgu-check.js            the report, as text
 *   node mgu-check.js --json     the same, as one JSON object
 *
 * It changes nothing: it reads files, and asks the cluster renderer for its
 * status over its socket as a client that draws nothing (no layer, no asset,
 * no host). It never writes a file. It prints no VIN, no phone or network
 * names, and only the few MGU Styles settings that decide where the map goes.
 * Every step has its own time limit and its own error, so one missing part
 * never stops the rest of the report.
 *
 * Exit code: 0 the report was made (whatever it says), 2 bad arguments.
 */

var fs = require('fs');
var path = require('path');
var net = require('net');

var VERSION = '1.0.0';
var RENDER_SOCK = process.env.MGU_CHECK_SOCK || '/dev/shm/cluster-render/render.sock';
var WAZE_STATUS = process.env.MGU_CHECK_WAZE || '/run/waze-on-cluster/status.json';
var OAP_ROOTS = (process.env.MGU_CHECK_OAP || '/var/opt/public/oap,/var/opt').split(',');
var TOOLS_ROOT = process.env.MGU_CHECK_TOOLS || '/var/opt';
var MGU_ID = 'mgustyles_toro';
var MGU_CLIENT = 'wali_mgu_styles';
var RENDER_MS = 5000;
var LOG_LINES = 400;            /* how much of the app's log is looked through */
var LOG_KEEP = 40;              /* how many of its lines about the map and the cluster are shown */
/* the settings that decide what is on the cluster and where the map goes - nothing else is read out */
var SETTING_KEYS = ['enabled', 'style', 'mapFit', 'startWait', 'lower', 'profile',
                    'tCentre', 'tCentreSize', 'showGMeter', 'mCentre', 'mCentreSize',
                    'pCentre', 'pCentreSize', 'kCentre', 'kWing', 'refreshHz', 'smoothing'];

function readJson(f) { return JSON.parse(fs.readFileSync(f, 'utf8')); }
function tail(f, n) {
    var st = fs.statSync(f), len = Math.min(st.size, 256 * 1024), buf = Buffer.alloc(len);
    var fd = fs.openSync(f, 'r');
    try { fs.readSync(fd, buf, 0, len, st.size - len); } finally { fs.closeSync(fd); }
    var lines = buf.toString('utf8').split('\n');
    if (len < st.size) lines.shift();                         /* a cut first line */
    return lines.filter(function (l) { return l !== ''; }).slice(-n);
}
function errText(e) { return (e && (e.code || e.message)) || String(e); }

/* ---------------------------------------------------- installed tools */
function tools() {
    var out = {};
    ['cluster-render', 'waze-on-cluster', 'mgu-deploy', 'mgu-render'].forEach(function (id) {
        var dir = path.join(TOOLS_ROOT, id), v = null;
        ['current/component.json', 'component.json'].some(function (rel) {
            try { v = readJson(path.join(dir, rel)).version || null; return !!v; } catch (e) { return false; }
        });
        if (!v) { try { v = fs.readlinkSync(path.join(dir, 'current')).split('/').pop(); } catch (e) { /* none */ } }
        var there = false;
        try { there = fs.statSync(dir).isDirectory(); } catch (e) { /* none */ }
        out[id] = there ? (v || 'installed, version unknown') : 'not installed';
    });
    return out;
}

/* ---------------------------------------------------- MGU Styles */
function findMgu() {
    for (var r = 0; r < OAP_ROOTS.length; r++) {
        var names = [];
        try { names = fs.readdirSync(OAP_ROOTS[r]); } catch (e) { continue; }
        for (var i = 0; i < names.length; i++) {
            if (!/^oap_\d+$/.test(names[i])) continue;
            var home = path.join(OAP_ROOTS[r], names[i]);
            try {
                var pkg = readJson(path.join(home, 'source', 'package.json'));
                if (pkg.name === MGU_ID) return { home: home, version: pkg.version || null };
            } catch (e) { /* not an app, or not this one */ }
        }
    }
    return null;
}

function mgu() {
    var f = findMgu();
    if (!f) return { installed: false, looked: OAP_ROOTS };
    var out = { installed: true, home: f.home, version: f.version, settings: {}, log: [], problems: [] };
    /* the settings: the spec's defaults, then what was saved, as the app reads them */
    var all = {};
    try {
        var spec = readJson(path.join(f.home, 'source', 'javascript', 'kit', 'app-spec.json'));
        (spec.settings || []).forEach(function (s) { all[s.key] = s.default; });
        if (spec.version) out.version = spec.version;
    } catch (e) { out.problems.push('app-spec.json: ' + errText(e)); }
    try {
        var saved = readJson(path.join(f.home, 'data', 'settings.json'));
        Object.keys(saved).forEach(function (k) { all[k] = saved[k]; });
    } catch (e) { out.problems.push(e.code === 'ENOENT' ? 'no settings saved yet (all as they come)' : 'settings.json: ' + errText(e)); }
    SETTING_KEYS.forEach(function (k) { if (all[k] !== undefined) out.settings[k] = all[k]; });
    var style = all.style || 'toro';
    var centreKey = { toro: 'tCentre', track: 'mCentre', mplus: 'pCentre', mtrack: 'kCentre' }[style] || 'tCentre';
    out.centre = all[centreKey];
    /* the app's own log: its lines about the map, the cluster and its status, and any warning */
    try {
        var lines = tail(path.join(f.home, 'data', MGU_ID + '.log'), LOG_LINES);
        out.log = lines.filter(function (l) {
            return /map:|cluster|status:|centre|WARN|ERROR|FATAL|settings applied|exit code/i.test(l);
        }).slice(-LOG_KEEP);
    } catch (e) { out.problems.push('log: ' + errText(e)); }
    return out;
}

/* ---------------------------------------------------- the phone's map */
function waze() {
    try {
        var st = fs.statSync(WAZE_STATUS), j = readJson(WAZE_STATUS);
        var age = Math.round((Date.now() - st.mtime.getTime()) / 1000);
        return { running: age < 10, age_s: age, state: j.state || null, target: j.target == null ? null : j.target,
                 cluster: !(j.cluster === 0 || j.cluster === false), hud: j.hud === 1 || j.hud === true,
                 reason: typeof j.reason === 'string' ? j.reason.slice(0, 80) : '' };
    } catch (e) { return { running: false, error: errText(e) }; }
}

/* ---------------------------------------------------- the cluster renderer */
/* Its protocol (cluster-render PROTOCOL.md): u32 LE length, u8 type, body; 'J' is a flat JSON object. */
function frame(obj) {
    var b = Buffer.from(JSON.stringify(obj)), m = Buffer.alloc(5 + b.length);
    m.writeUInt32LE(b.length + 1, 0); m[4] = 0x4a; b.copy(m, 5);
    return m;
}

function renderer(cb) {
    var out = { reachable: false }, buf = Buffer.alloc(0), done = false, sock;
    function finish(err) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        if (err) out.error = err;
        try { sock.destroy(); } catch (e) { /* closed */ }
        cb(out);
    }
    var timer = setTimeout(function () { finish(out.reachable ? 'no status within ' + RENDER_MS + ' ms' : 'no answer within ' + RENDER_MS + ' ms'); }, RENDER_MS);
    try { sock = net.connect(RENDER_SOCK); } catch (e) { return finish(errText(e)); }
    sock.on('error', function (e) { finish(errText(e)); });
    sock.on('close', function () { finish(out.status ? null : 'the renderer closed the connection'); });
    sock.on('connect', function () {
        out.reachable = true;
        sock.write(frame({ op: 'hello', app: 'mgu-check', proto: 1 }));
        sock.write(frame({ op: 'status' }));
    });
    sock.on('data', function (d) {
        buf = Buffer.concat([buf, d]);
        while (buf.length >= 5) {
            var len = buf.readUInt32LE(0);
            if (len > 8 * 1024 * 1024) return finish('a message too large to be the renderer\'s');
            if (buf.length < 4 + len) return;
            var type = buf[4], body = buf.slice(5, 4 + len);
            buf = buf.slice(4 + len);
            if (type !== 0x4a) continue;
            var ev;
            try { ev = JSON.parse(body.toString('utf8')); } catch (e) { continue; }
            if (ev.ev === 'hello') {
                out.backend = ev.backend; out.targets = ev.targets;
                out.host_layers = ev.host_layers === 1;
            } else if (ev.ev === 'status') {
                out.status = String(ev.text || '');
                return finish(null);
            } else if (ev.ev === 'error' && /not supported/.test(String(ev.reason))) {
                out.unsupported = String(ev.reason);
            }
        }
    });
}

/* What the status text says about MGU Styles and the map (cluster-render server.cpp status_text). */
function readStatus(text) {
    var r = { targets: {}, mgu_on_panel: false, hosts: [], upload: null };
    String(text || '').split('\n').forEach(function (l) {
        var m = /^target ([a-z0-9_-]+): slot \S+ scene \S+ \d+x\d+: (.*)$/.exec(l);
        if (m) { r.targets[m[1]] = m[2].trim(); return; }
        m = /^asset \d+ \S+ \d+x\d+(?: \(\d+ mip levels\))? of (.+) on ([a-z0-9_-]+): /.exec(l);
        if (m && m[1] === MGU_CLIENT && m[2] === 'panel') { r.mgu_on_panel = true; return; }
        m = /^(.+) hosts (\S+) from its layers on (\S+) at (\d+)x(\d+)\+(-?\d+)\+(-?\d+) from (\d+)x(\d+)\+(\d+)\+(\d+)(.*)$/.exec(l);
        if (m) {
            var rest = m[12], live = /; live/.test(rest), wait = /; waiting: (.*)$/.exec(rest);
            r.hosts.push({ app: m[1], target: m[2], into: m[3],
                           rect: { x: +m[6], y: +m[7], w: +m[4], h: +m[5] },
                           part: { x: +m[10], y: +m[11], w: +m[8], h: +m[9] },
                           live: live, waiting: wait ? wait[1] : null });
            return;
        }
        if (/^upload: /.test(l)) r.upload = l.slice(8);
    });
    return r;
}

/* ---------------------------------------------------- the verdict */
function verdict(rep) {
    var v = [], m = rep.mgu, w = rep.waze, r = rep.renderer, s = r.parsed || {};
    var mapHost = (s.hosts || []).filter(function (h) { return h.app === MGU_CLIENT && h.target === 'map'; })[0];
    function line(ok, text) { v.push((ok === true ? 'OK   ' : ok === false ? 'NO   ' : '--   ') + text); }

    line(r.reachable && !!r.status, r.reachable ? (r.status ? 'cluster renderer answering' + (r.backend ? ' (' + r.backend + ')' : '')
        : 'cluster renderer reached, but no status: ' + r.error) : 'cluster renderer not reachable: ' + (r.error || '?'));
    if (r.reachable) line(r.host_layers, r.host_layers ? 'renderer can place the phone map in a style\'s opening (0.8.2 or later)'
        : 'renderer too old to place the map in the opening (needs cluster-render 0.8.2): the map stays at its own place');
    line(m.installed, m.installed ? 'MGU Styles installed, ' + (m.version || 'version unknown') : 'MGU Styles not found under ' + OAP_ROOTS.join(', '));
    if (m.installed) {
        line(m.settings.enabled === true, 'MGU Styles turned ' + (m.settings.enabled === true ? 'on' : 'off') + ' in its settings');
        line(null, 'style ' + (m.settings.style || 'toro') + ', its middle set to "' + (m.centre || '?') + '", map sizing ' +
            (m.settings.mapFit === false ? 'OFF (as the car places it)' : 'on'));
    }
    if (r.status) {
        line(s.mgu_on_panel, s.mgu_on_panel ? 'MGU Styles is drawing on the cluster now' : 'MGU Styles is not drawing on the cluster now (car off, turned off, or waiting)');
        line(null, 'panel: ' + (s.targets.panel || '?') + ' | map slot: ' + (s.targets.map || '?'));
    }
    line(w.running, w.running ? 'Maps on cluster running: phone map ' + (w.state || '?') + (w.cluster ? '' : ' (cluster map switched off)') +
        (w.target ? ', drawn on ' + w.target : '') : 'Maps on cluster not running' + (w.error ? ' (' + w.error + ')' : ''));
    if (mapHost) {
        line(mapHost.live, 'phone map placed in the opening at ' + mapHost.rect.w + 'x' + mapHost.rect.h + ' at ' + mapHost.rect.x + ',' + mapHost.rect.y +
            ' (the map\'s ' + mapHost.part.w + 'x' + mapHost.part.h + ' at ' + mapHost.part.x + ',' + mapHost.part.y + ')' +
            (mapHost.live ? ', live' : ', waiting: ' + (mapHost.waiting || '?')));
        if (mapHost.live && w.running && w.target && w.target !== 'panel' && w.state === 'showing') {
            line(false, 'but Maps on cluster says it draws on "' + w.target + '", not the panel: it may be older than 0.14.5');
        }
    } else if (r.status && s.mgu_on_panel) {
        var should = m.installed && m.settings.mapFit !== false && (m.centre === 'waze' || m.centre === 'map') && r.host_layers;
        line(should ? false : null, should ? 'phone map NOT placed in the opening, though it should be (see the app\'s log below)'
            : 'phone map not placed in the opening (as expected for these settings)');
    }
    return v;
}

function report(json) {
    var rep = { tool: 'mgu-check ' + VERSION, at: new Date().toISOString(), tools: null, mgu: null, waze: null, renderer: null };
    try { rep.tools = tools(); } catch (e) { rep.tools = { error: errText(e) }; }
    try { rep.mgu = mgu(); } catch (e) { rep.mgu = { installed: false, error: errText(e) }; }
    try { rep.waze = waze(); } catch (e) { rep.waze = { running: false, error: errText(e) }; }
    renderer(function (r) {
        rep.renderer = r;
        try { r.parsed = readStatus(r.status); } catch (e) { r.parsed = { error: errText(e) }; }
        try { rep.verdict = verdict(rep); } catch (e) { rep.verdict = ['--   could not judge: ' + errText(e)]; }
        if (json) { process.stdout.write(JSON.stringify(rep, null, 1) + '\n'); return; }
        var o = [];
        o.push('== mgu-check ' + VERSION + ' - ' + rep.at);
        o.push('');
        rep.verdict.forEach(function (l) { o.push(l); });
        o.push('');
        o.push('== installed tools');
        Object.keys(rep.tools || {}).forEach(function (k) { o.push('  ' + k + ': ' + rep.tools[k]); });
        if (rep.mgu.installed) {
            o.push('== MGU Styles ' + (rep.mgu.version || '') + ' (' + rep.mgu.home + ')');
            o.push('  settings: ' + JSON.stringify(rep.mgu.settings));
            rep.mgu.problems.forEach(function (p) { o.push('  note: ' + p); });
            o.push('== its log, the lines about the map and the cluster (last ' + rep.mgu.log.length + ')');
            rep.mgu.log.forEach(function (l) { o.push('  ' + l); });
        }
        o.push('== Maps on cluster: ' + JSON.stringify(rep.waze));
        o.push('== cluster renderer status');
        String(r.status || r.error || '').split('\n').forEach(function (l) { if (l) o.push('  ' + l); });
        process.stdout.write(o.join('\n') + '\n');
    });
}

var args = process.argv.slice(2);
if (args.some(function (a) { return a !== '--json'; })) {
    process.stderr.write('usage: node mgu-check.js [--json]\n');
    process.exitCode = 2;
} else {
    report(args.indexOf('--json') >= 0);
}
