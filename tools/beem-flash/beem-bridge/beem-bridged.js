#!/usr/bin/env node
'use strict';
/*
 * beem-bridged — the root helper behind BeeM-Flash's "OWN WI-FI" bridge mode.
 *
 * WHAT IT IS FOR
 *   The BeeM-Flash app already hosts the whole bridge itself: it listens on every
 *   interface for ENET/HSFZ (TCP 6801), DoIP (TCP 13400) and answers discovery
 *   (UDP 6811 / 13400), and it judges every request against the on-screen gate.
 *   What an app sandbox can NOT do without root is the network plumbing. This
 *   daemon does exactly that and nothing else:
 *
 *     1. brings up a separate Wi-Fi network ("BeeM-Flash") on a VIRTUAL access-
 *        point interface, so the car's own Wi-Fi / wireless CarPlay keep running;
 *     2. hands phones an address on it (DHCP, no default route, no DNS, so the
 *        phone keeps mobile data for the internet);
 *     3. opens the head unit's firewall for the bridge ports on that network
 *        only (and, if asked, on the car's own Wi-Fi interface);
 *     4. checks, as root, that the app's listener really answers on that
 *        network - so you know it works before you pick up a phone.
 *
 *     phone/tool --Wi-Fi (this AP)--> app listener 6801/13400 --TASU--> car
 *                                   ^ the app is the only thing that talks to the car
 *
 *   It never carries diagnostic traffic and never talks to the vehicle. It holds
 *   no policy: what may reach the car is decided by the app, on screen.
 *
 * LIFECYCLE
 *   Runs from boot (systemd), idle and harmless: no Wi-Fi is broadcast until the
 *   app asks for it (OWN WI-FI on the bridge page). The network comes down when
 *   the app turns it off, when the app has been gone for a minute, or after
 *   --idle seconds with nobody joined. The daemon itself keeps running.
 *
 * NOTHING PERSISTENT
 *   Configs, leases and the socket live under the runtime dir and are removed on
 *   exit; firewall rules it added are removed on exit; the virtual interface is
 *   deleted. Stopping the daemon leaves the unit as it was.
 *
 * Usage:
 *   beem-bridged --probe            report what this unit can do; changes nothing
 *   beem-bridged [--iface wlan0] [--ssid BeeM-Flash] [--channel N] [--country US]
 *                [--addr 192.168.44.1] [--idle 1800] [--open-iface wlan_bridge]
 *                [--take-iface] [--uid 12261] [--gid 12202]
 *   beem-bridged --status           ask the running daemon for its state
 */

var net = require('net');
var fs = require('fs');
var os = require('os');
var path = require('path');
var cp = require('child_process');
var crypto = require('crypto');

// ---- options ---------------------------------------------------------------
var argv = process.argv.slice(2);
function opt(name, dflt) {
  var i = argv.indexOf('--' + name);
  if (i < 0) return dflt;
  var v = argv[i + 1];
  return (v === undefined || v.slice(0, 2) === '--') ? true : v;
}
var OPT = {
  probe: argv.indexOf('--probe') >= 0,
  status: argv.indexOf('--status') >= 0,
  takeIface: argv.indexOf('--take-iface') >= 0,     // allow using the phys iface if no virtual AP
  iface: String(opt('iface', '') || ''),
  vif: String(opt('vif', 'beem0')),
  ssid: String(opt('ssid', 'BeeM-Flash')),
  channel: Number(opt('channel', 0)) || 0,          // 0 = follow the radio's current channel
  country: String(opt('country', 'US')).toUpperCase().slice(0, 2),
  addr: String(opt('addr', '192.168.44.1')),
  idleSec: Number(opt('idle', 1800)) || 1800,
  openIfaces: String(opt('open-iface', '') || '').split(',').filter(Boolean),
  appUid: opt('uid', null) === null ? null : Number(opt('uid', null)),
  appGid: Number(opt('gid', 12202)) || 12202,       // tm_oap_app: the app's group
  runDir: String(opt('rundir', '/run/beem-bridge')),
  socks: [String(opt('socket', '/run/beem-bridge/bridge.sock')), String(opt('socket2', '/dev/shm/beem-bridge/bridge.sock'))]
    .filter(function (s) { return s && s !== 'none'; })
};
// Values that reach shell commands are validated, never passed through raw.
if (!/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.1$/.test(OPT.addr)) OPT.addr = '192.168.44.1';
OPT.vif = cleanIface(OPT.vif) || 'beem0';
// The ports the app's own listener serves (bridgeserver.js / discovery.js).
var TCP_PORTS = [6801, 13400];
var UDP_PORTS = [6811, 13400];

function log() { process.stdout.write('[beem-bridged] ' + Array.prototype.slice.call(arguments).join(' ') + '\n'); }
function sh(cmd) {
  try { return { ok: true, out: String(cp.execSync(cmd, { stdio: ['ignore', 'pipe', 'pipe'], timeout: 8000 })) }; }
  catch (e) { return { ok: false, out: String((e && (e.stderr || e.stdout)) || (e && e.message) || e) }; }
}
function has(bin) { return sh('command -v ' + bin + ' 2>/dev/null').ok; }

// Anything that reaches a config file or a shell must be plain printable ASCII:
// a newline in an SSID could otherwise inject hostapd directives (e.g. wpa=0).
function cleanText(s, min, max) {
  s = String(s == null ? '' : s);
  if (s.length < min || s.length > max) return null;
  if (!/^[\x20-\x7e]+$/.test(s)) return null;
  return s;
}
function cleanIface(s) { return /^[A-Za-z0-9_.-]{1,15}$/.test(String(s)) ? String(s) : null; }

// ---- radio / interfaces ----------------------------------------------------
function ifaceList() {
  var out = [], ifs = os.networkInterfaces();
  Object.keys(ifs).forEach(function (name) {
    if (name === 'lo') return;
    var v4 = (ifs[name] || []).filter(function (a) { return a.family === 'IPv4' || a.family === 4; })[0];
    out.push({ name: name, address: v4 ? v4.address : null, mac: v4 ? v4.mac : null });
  });
  return out;
}
// `iw dev` -> [{ phy, iface, type, channel }]
function iwDevs() {
  var r = sh('iw dev 2>/dev/null');
  if (!r.ok) return [];
  var out = [], phy = null, cur = null;
  r.out.split('\n').forEach(function (ln) {
    var m;
    if ((m = /^phy#(\d+)/.exec(ln))) { phy = 'phy' + m[1]; return; }
    if ((m = /^\s+Interface\s+(\S+)/.exec(ln))) { cur = { phy: phy, iface: m[1], type: null, channel: null }; out.push(cur); return; }
    if (!cur) return;
    if ((m = /^\s+type\s+(\S+)/.exec(ln))) cur.type = m[1];
    if ((m = /^\s+channel\s+(\d+)/.exec(ln))) cur.channel = Number(m[1]);
  });
  return out;
}
// Can this radio run an extra AP next to what it already does? `iw list`
// "valid interface combinations" is the authority; "Supported interface modes"
// only says AP exists at all.
function apCapability() {
  var r = sh('iw list 2>/dev/null');
  if (!r.ok) return { known: false, ap: false, combos: null, why: 'iw not available' };
  var modes = /Supported interface modes:([\s\S]*?)\n\s*\n/.exec(r.out);
  var ap = modes ? /\*\s+AP\s*$/m.test(modes[1]) : false;
  var combos = /valid interface combinations:([\s\S]*?)(\n\s*\n|$)/i.exec(r.out);
  return { known: true, ap: ap, combos: combos ? combos[1].replace(/\s+/g, ' ').trim() : null, why: null };
}
function pickPhysIface() {
  if (OPT.iface) return cleanIface(OPT.iface);
  var devs = iwDevs().filter(function (d) { return d.iface !== OPT.vif; });
  var managed = devs.filter(function (d) { return d.type === 'managed'; })[0];
  if (managed) return managed.iface;
  if (devs[0]) return devs[0].iface;
  var wl = ifaceList().map(function (i) { return i.name; }).filter(function (n) { return /^wl|wlan/.test(n) && n !== OPT.vif; });
  return wl[0] || null;
}
// Follow the channel the radio is already on: a second AP on the SAME channel is
// the combination radios support best, and it keeps the car's own Wi-Fi intact.
function currentChannel(phys) {
  var devs = iwDevs(), me = devs.filter(function (d) { return d.iface === phys; })[0];
  var phy = me && me.phy;
  var on = devs.filter(function (d) { return d.channel && (!phy || d.phy === phy); })[0];
  return on ? on.channel : null;
}

// ---- passphrase --------------------------------------------------------------
// Readable (the car SHOWS it and you type it into the phone) but random from a
// cryptographic source: 3 words + 4 digits (~2^34), not Math.random.
var WORDS = ('amber anvil basalt cedar delta ember fjord granite harbor indigo jasper kettle lumen maple nickel onyx ' +
  'pebble quartz raven summit timber umber violet walnut yarrow zenith cobalt dune elm forge glacier ' +
  'heron iris juniper kestrel lagoon meadow nectar orchid prairie quill reef sierra tundra vista willow').split(' ');
function genPass() {
  var b = crypto.randomBytes(8);
  function pick(i) { return WORDS[((b[i] << 8) | b[i + 1]) % WORDS.length]; }
  var n = String(1000 + (((b[6] << 8) | b[7]) % 9000));
  return pick(0) + '-' + n + '-' + pick(2) + '-' + pick(4);
}

// ---- firewall ----------------------------------------------------------------
// BMW head units filter inbound traffic with nftables (table "ip filter", chain
// "input"; on the test car iptables is not even readable). The Wi-Fi interface
// there is an ALLOW-LIST - CarPlay 7000, Android Auto 7612, ... - so the bridge
// ports have to be added. Rules are INSERTED at the top of the input chain (an
// appended rule would sit behind a catch-all drop and never match), tagged with
// the comment "beem-bridge", and removed again by handle. nft is called without
// a shell (execFileSync), so no value is ever shell-interpreted. iptables is the
// fallback for units that use it.
var FW = { rules: [], note: null, kind: null, chain: null };
var FW_TAG = 'beem-bridge';

function nft(args) {
  try { return { ok: true, out: String(cp.execFileSync('nft', args, { stdio: ['ignore', 'pipe', 'pipe'], timeout: 8000 })) }; }
  catch (e) { return { ok: false, out: String((e && (e.stderr || e.message)) || e) }; }
}
// The chain that has "hook input": try the usual names, family ip first.
function nftInputChain() {
  if (FW.chain) return FW.chain;
  var tries = [['ip', 'filter', 'input'], ['inet', 'filter', 'input'], ['ip', 'filter', 'INPUT'], ['inet', 'fw4', 'input']];
  for (var i = 0; i < tries.length; i++) {
    var r = nft(['list', 'chain'].concat(tries[i]));
    if (r.ok && /hook input/.test(r.out)) { FW.chain = tries[i]; return FW.chain; }
  }
  return null;
}
function fwKind() {
  if (FW.kind) return FW.kind;
  if (has('nft') && nftInputChain()) FW.kind = 'nft';
  else if (has('iptables') && sh('iptables -S INPUT 2>/dev/null').ok) FW.kind = 'iptables';
  else FW.kind = 'none';
  return FW.kind;
}
function fwSpecs(iface, withDhcp) {
  var out = [
    { proto: 'tcp', ports: TCP_PORTS.slice(0) },
    { proto: 'udp', ports: UDP_PORTS.slice(0) }
  ];
  if (withDhcp) out.push({ proto: 'udp', ports: [67] });
  return out.map(function (x) { x.iface = iface; return x; });
}
function nftHandles(iface) {
  var ch = nftInputChain(); if (!ch) return [];
  var r = nft(['-a', 'list', 'chain'].concat(ch)); if (!r.ok) return [];
  var out = [];
  r.out.split('\n').forEach(function (ln) {
    if (ln.indexOf('"' + FW_TAG + '"') < 0) return;
    if (iface && ln.indexOf('"' + iface + '"') < 0 && ln.indexOf(' ' + iface + ' ') < 0) return;
    var m = /# handle (\d+)/.exec(ln); if (m) out.push(m[1]);
  });
  return out;
}
function fwOpen(iface, withDhcp) {
  var kind = fwKind();
  if (kind === 'none') { FW.note = 'no usable firewall tool (nothing opened)'; return; }
  if (kind === 'nft') {
    if (nftHandles(iface).length) { FW.note = null; return; }            // already open (idempotent)
    var ch = nftInputChain();
    fwSpecs(iface, withDhcp).forEach(function (sp) {
      var args = ['insert', 'rule'].concat(ch).concat(['iifname', '"' + iface + '"', sp.proto, 'dport']);
      if (sp.ports.length === 1) args.push(String(sp.ports[0]));
      else args = args.concat(['{', sp.ports.join(', '), '}']);
      args = args.concat(['accept', 'comment', '"' + FW_TAG + '"']);
      var r = nft(args);
      if (r.ok) FW.rules.push({ kind: 'nft', iface: iface });
      else FW.note = 'could not add an nft rule on ' + iface + ': ' + r.out.trim().slice(0, 120);
    });
    return;
  }
  fwSpecs(iface, withDhcp).forEach(function (sp) {
    sp.ports.forEach(function (port) {
      var rule = '-i ' + iface + ' -p ' + sp.proto + ' --dport ' + port + ' -j ACCEPT';
      if (sh('iptables -C INPUT ' + rule + ' 2>/dev/null').ok) return;
      if (sh('iptables -I INPUT ' + rule).ok) FW.rules.push({ kind: 'iptables', iface: iface, rule: rule });
      else FW.note = 'could not add a firewall rule (' + rule + ')';
    });
  });
}
function fwCloseIface(iface) {
  if (fwKind() === 'nft') {
    var ch = nftInputChain();
    nftHandles(iface).forEach(function (h) { nft(['delete', 'rule'].concat(ch).concat(['handle', h])); });
  }
  FW.rules = FW.rules.filter(function (r) {
    if (r.iface !== iface) return true;
    if (r.kind === 'iptables') sh('iptables -D INPUT ' + r.rule);
    return false;
  });
}
// Read-only: is inbound traffic on this interface accepted for the bridge
// ports? Looks for a catch-all drop / reject in the input chain (and the chains
// it jumps to) that would apply to the interface and is not narrowed by a port
// or address. A best-effort reading of the rules, shown as such.
function fwVerdict(iface) {
  if (fwKind() !== 'nft') return null;
  var ch = nftInputChain();
  var main = nft(['list', 'chain'].concat(ch)); if (!main.ok) return null;
  var text = main.out, jumps = [], m, re = /jump (\S+)/g;
  while ((m = re.exec(main.out))) jumps.push(m[1]);
  jumps.forEach(function (j) { var r = nft(['list', 'chain', ch[0], ch[1], j]); if (r.ok) text += '\n' + r.out; });
  var ours = text.indexOf('"' + FW_TAG + '"') >= 0;
  var policy = /policy (\w+)/.exec(main.out); policy = policy ? policy[1] : '?';
  var blockers = text.split('\n').map(function (l) { return l.trim(); }).filter(function (l) {
    if (!/\b(drop|reject)\b/.test(l) || /policy/.test(l)) return false;
    if (/dport|sport|saddr|daddr|limit|flags/.test(l)) return false;     // narrowed: not a catch-all
    var ifm = /iifname\s+"?([A-Za-z0-9_.*-]+)"?/.exec(l);
    if (!ifm) return true;                                              // applies to every interface
    var pat = ifm[1];
    if (pat.slice(-1) === '*') return iface.indexOf(pat.slice(0, -1)) === 0;
    return pat === iface;
  });
  return { iface: iface, policy: policy, blockers: blockers, opened: ours };
}

// ---- access point ------------------------------------------------------------
var AP = {
  state: 'down',          // down | starting | up | failed
  error: null,
  ssid: cleanText(OPT.ssid, 1, 32) || 'BeeM-Flash',
  pass: genPass(),
  security: 'WPA2',
  channel: null,
  address: OPT.addr,
  phys: null,
  iface: null,            // the interface the AP actually runs on
  virtual: false,
  hostapd: null,
  dnsmasq: null,
  since: 0,
  reach: null             // { 6801: true|errno, 13400: ... } from the root self-check
};

function apLeaseFile() { return path.join(OPT.runDir, 'dnsmasq.leases'); }

function killAndWait(proc, cb) {
  if (!proc || proc.exitCode !== null || proc.signalCode) return cb();
  var done = false;
  function fin() { if (!done) { done = true; cb(); } }
  proc.once('exit', fin);
  try { proc.kill('SIGTERM'); } catch (e) { return fin(); }
  setTimeout(function () { try { proc.kill('SIGKILL'); } catch (e) {} fin(); }, 3000);
}

function apStart(cb) {
  if (AP.state === 'up' || AP.state === 'starting') return cb(null);
  function fail(why) { AP.state = 'failed'; AP.error = why; log('access point not started: ' + why); apCleanup(function () { AP.state = 'failed'; cb(why); }); }
  if (!has('hostapd')) return fail('hostapd is not installed on this unit');
  if (!has('ip')) return fail('the ip tool is not installed on this unit');
  var phys = pickPhysIface();
  if (!phys) return fail('no wireless interface found');
  AP.state = 'starting'; AP.error = null; AP.phys = phys;

  // 1) a VIRTUAL AP interface next to the car's own Wi-Fi
  var iface = null;
  if (has('iw')) {
    sh('iw dev ' + OPT.vif + ' del 2>/dev/null');
    if (sh('iw dev ' + phys + ' interface add ' + OPT.vif + ' type __ap').ok) { iface = OPT.vif; AP.virtual = true; }
  }
  if (!iface) {
    if (!OPT.takeIface) return fail('this radio cannot add a separate access point next to the car\'s own Wi-Fi ' +
      '(see --probe). Not taking over ' + phys + ' because that would drop the car\'s Wi-Fi / wireless CarPlay; ' +
      'start the daemon with --take-iface to allow it anyway.');
    iface = phys; AP.virtual = false;
  }
  AP.iface = iface;
  AP.channel = OPT.channel || currentChannel(phys) || 6;

  // 2) address
  sh('ip addr flush dev ' + iface + ' 2>/dev/null');
  sh('ip addr add ' + AP.address + '/24 dev ' + iface);
  sh('ip link set ' + iface + ' up');

  // 3) hostapd (config under the runtime dir, sanitised values only)
  try { fs.mkdirSync(OPT.runDir, 0x1ed); } catch (e) { /* exists */ }
  var conf = path.join(OPT.runDir, 'hostapd.conf');
  var body = [
    'interface=' + iface, 'driver=nl80211', 'ssid=' + AP.ssid,
    'hw_mode=' + (AP.channel > 14 ? 'a' : 'g'), 'channel=' + AP.channel,
    'country_code=' + (/^[A-Z]{2}$/.test(OPT.country) ? OPT.country : 'US'), 'ieee80211d=1',
    'ieee80211n=1', 'wmm_enabled=1', 'auth_algs=1',
    'wpa=2', 'wpa_key_mgmt=WPA-PSK', 'rsn_pairwise=CCMP', 'wpa_passphrase=' + AP.pass,
    'ignore_broadcast_ssid=0'
  ].join('\n') + '\n';
  try { fs.writeFileSync(conf, body, { mode: 0x180 }); } catch (e) { return fail('cannot write ' + conf); }

  var hp = cp.spawn('hostapd', [conf], { stdio: ['ignore', 'pipe', 'pipe'] });
  AP.hostapd = hp;
  var settled = false, lastLines = [];
  function settle(err) { if (settled) return; settled = true; clearTimeout(t); if (err) fail(err); else afterUp(); }
  function onLine(d) {
    String(d).split('\n').forEach(function (ln) {
      ln = ln.trim(); if (!ln) return;
      lastLines.push(ln); if (lastLines.length > 6) lastLines.shift();
      if (/AP-ENABLED/.test(ln)) settle(null);
    });
  }
  hp.stdout.on('data', onLine);          // always drained: an unread pipe would block hostapd
  hp.stderr.on('data', onLine);
  hp.on('error', function (e) { settle('hostapd could not start: ' + e.message); });
  hp.on('exit', function (code) {
    if (!settled) return settle('hostapd exited (' + code + '): ' + lastLines.slice(-2).join(' | '));
    if (AP.hostapd === hp && AP.state === 'up') {           // died later: say so, do not pretend
      log('hostapd stopped (' + code + ')');
      AP.state = 'failed'; AP.error = 'hostapd stopped: ' + lastLines.slice(-1).join('');
      apCleanup(function () { AP.state = 'failed'; });
    }
  });
  var t = setTimeout(function () { settle('hostapd did not report AP-ENABLED within 10 s: ' + lastLines.slice(-2).join(' | ')); }, 10000);

  function afterUp() {
    // 4) DHCP for the phone: no router, no DNS (the phone keeps mobile data for
    //    the internet and uses this Wi-Fi only for the car), own lease file,
    //    never the unit's own resolver or /etc config.
    if (has('dnsmasq')) {
      var base = AP.address.replace(/\.\d+$/, '');
      var dm = cp.spawn('dnsmasq', ['--keep-in-foreground', '--port=0', '--bind-interfaces', '--conf-file=/dev/null',
        '--interface=' + iface, '--except-interface=lo', '--dhcp-range=' + base + '.50,' + base + '.90,255.255.255.0,1h',
        '--dhcp-option=3', '--dhcp-option=6', '--dhcp-leasefile=' + apLeaseFile(),
        '--pid-file=' + path.join(OPT.runDir, 'dnsmasq.pid')],
        { stdio: ['ignore', 'ignore', 'pipe'] });
      dm.stderr.on('data', function () {});
      dm.on('error', function (e) { log('dnsmasq failed: ' + e.message + ' - give the phone a static ' + base + '.50/24'); });
      AP.dnsmasq = dm;
    } else {
      log('dnsmasq not present: give the phone a static address ' + AP.address.replace(/\.\d+$/, '.50') + '/24');
    }
    // 5) firewall for the bridge ports on this network only
    fwOpen(iface, true);
    AP.state = 'up'; AP.since = Date.now();
    log('AP up: "' + AP.ssid + '" on ' + iface + (AP.virtual ? ' (virtual, next to ' + phys + ')' : ' (took over ' + phys + ')') +
      ' ch ' + AP.channel + ' at ' + AP.address);
    // 6) prove the app's listener answers on this network
    reachCheck(function () { cb(null); });
  }
}

function apCleanup(cb) {
  var hp = AP.hostapd, dm = AP.dnsmasq, iface = AP.iface;
  AP.hostapd = null; AP.dnsmasq = null;
  killAndWait(dm, function () {
    killAndWait(hp, function () {
      if (iface) {
        fwCloseIface(iface);
        if (AP.virtual) sh('iw dev ' + iface + ' del 2>/dev/null');
        else sh('ip addr del ' + AP.address + '/24 dev ' + iface + ' 2>/dev/null');
      }
      try { fs.unlinkSync(path.join(OPT.runDir, 'hostapd.conf')); } catch (e) {}
      AP.iface = null; AP.reach = null;
      cb();
    });
  });
}

function apStop(cb) {
  if (AP.state === 'down') return cb && cb();
  log('AP down');
  apCleanup(function () { AP.state = 'down'; AP.error = null; if (cb) cb(); });
}

// Root-side check that the APP's listener answers on the AP address. If it does,
// a phone on this network reaches the bridge; if not, the reason is shown.
function reachCheck(cb) {
  var res = {}, left = TCP_PORTS.length;
  TCP_PORTS.forEach(function (p) {
    var c, done = false;
    function fin(v) { if (done) return; done = true; res[p] = v; try { c && c.destroy(); } catch (e) {} if (--left === 0) { AP.reach = res; cb(res); } }
    try { c = net.connect(p, AP.address); } catch (e) { return fin((e && e.code) || 'error'); }
    c.setTimeout(1500, function () { fin('timeout'); });
    c.on('connect', function () { fin(true); });
    c.on('error', function (e) { fin((e && e.code) || 'error'); });
  });
}

function stations() {
  if (!AP.iface || AP.state !== 'up') return [];
  var r = sh('iw dev ' + AP.iface + ' station dump 2>/dev/null');
  if (!r.ok) return [];
  var out = [], re = /Station ([0-9a-f:]{17})/gi, m;
  while ((m = re.exec(r.out))) out.push({ mac: m[1], ip: null });
  try {
    fs.readFileSync(apLeaseFile(), 'utf8').split('\n').forEach(function (ln) {
      var f = ln.split(/\s+/);
      if (f.length >= 3) out.forEach(function (s) { if (s.mac.toLowerCase() === String(f[1]).toLowerCase()) s.ip = f[2]; });
    });
  } catch (e) { /* no leases yet */ }
  return out;
}

// ---- the app connection (UNIX socket) --------------------------------------
var appSock = null, appGoneAt = 0, lastJoined = Date.now();

function appSend(obj) {
  if (!appSock) return false;
  try { appSock.write(JSON.stringify(obj) + '\n'); return true; } catch (e) { return false; }
}
function stateMsg(id) {
  return {
    t: 'state', id: id, ok: true,
    ap: { up: AP.state === 'up', state: AP.state, error: AP.error, ssid: AP.ssid, pass: AP.pass, security: AP.security,
      channel: AP.channel, address: AP.address, iface: AP.iface, virtual: AP.virtual, reach: AP.reach },
    firewall: { opened: FW.rules.length, note: FW.note, kind: FW.kind, ifaces: OPT.openIfaces },
    stations: stations()
  };
}

function onAppLine(line) {
  var msg;
  try { msg = JSON.parse(line); } catch (e) { return; }
  if (!msg || typeof msg !== 'object') return;
  var id = msg.id;
  function reply() { appSend(stateMsg(id)); }
  function refuse(err) { appSend({ id: id, ok: false, error: err }); }
  switch (msg.cmd) {
    case 'status':
    case 'stations':
      return reply();
    case 'ap':
      if (msg.up) return apStart(function (err) { if (err) refuse(err); else reply(); });
      return apStop(reply);
    case 'check':
      if (AP.state !== 'up') return reply();
      return reachCheck(reply);
    case 'set-ssid': {
      var s = cleanText(msg.ssid, 1, 32);
      if (!s) return refuse('SSID must be 1-32 plain characters');
      AP.ssid = s;
      return restartIfUp(reply, refuse);
    }
    case 'set-pass': {
      var p = cleanText(msg.pass, 8, 63);
      if (!p) return refuse('password must be 8-63 plain characters');
      AP.pass = p;
      return restartIfUp(reply, refuse);
    }
    case 'new-pass':
      AP.pass = genPass();
      return restartIfUp(reply, refuse);
    default:
      if (id != null) refuse('unknown command');
  }
}
function restartIfUp(ok, bad) {
  if (AP.state !== 'up') return ok();
  apStop(function () { apStart(function (err) { if (err) bad(err); else ok(); }); });
}

function prepareSocketPath(p, cb) {
  var dir = path.dirname(p);
  try { fs.mkdirSync(dir, 0x1ed); } catch (e) { /* exists */ }
  try { fs.chmodSync(dir, 0x1ed); } catch (e) {}
  if (!fs.existsSync(p)) return cb(true);
  // A live daemon answers; a stale socket file from a crash does not.
  var c = net.connect(p), done = false;
  function fin(stale) { if (done) return; done = true; try { c.destroy(); } catch (e) {} if (stale) { try { fs.unlinkSync(p); } catch (e) {} } cb(stale); }
  c.on('connect', function () { fin(false); });
  c.on('error', function () { fin(true); });
  setTimeout(function () { fin(true); }, 800);
}

function serveApp(p) {
  var srv = net.createServer(function (c) {
    // A newer app connection replaces an older one (the app restarted).
    if (appSock && appSock !== c) { try { appSock.destroy(); } catch (e) {} }
    appSock = c; appGoneAt = 0;
    log('app attached (' + p + ')');
    var buf = '';
    c.on('data', function (d) {
      buf += String(d);
      if (buf.length > 256 * 1024) { buf = ''; try { c.destroy(); } catch (e) {} return; }
      var i;
      while ((i = buf.indexOf('\n')) >= 0) { var ln = buf.slice(0, i); buf = buf.slice(i + 1); if (ln) onAppLine(ln); }
    });
    function gone() { if (appSock === c) { appSock = null; appGoneAt = Date.now(); log('app detached'); } }
    c.on('close', gone);
    c.on('error', gone);
  });
  srv.on('error', function (e) { log('socket ' + p + ': ' + e.message); });
  srv.listen(p, function () {
    try { fs.chmodSync(p, 0x1b0); } catch (e) { log('chmod ' + p + ': ' + e.message); }
    var uid = (OPT.appUid !== null && isFinite(OPT.appUid)) ? OPT.appUid : 0;
    try { fs.chownSync(p, uid, OPT.appGid); } catch (e) { log('chown ' + p + ': ' + e.message); }
    log('control socket ' + p + ' (uid ' + uid + ', gid ' + OPT.appGid + ', mode 660)');
  });
  return srv;
}

// ---- probe / status ------------------------------------------------------------
function probe() {
  log('interfaces:');
  ifaceList().forEach(function (i) { log('   ' + i.name + '  ' + (i.address || '-') + '  ' + (i.mac || '')); });
  log('wireless (iw dev):');
  var devs = iwDevs();
  if (!devs.length) log('   (none reported)');
  devs.forEach(function (d) { log('   ' + d.phy + ' ' + d.iface + '  type ' + d.type + (d.channel ? '  channel ' + d.channel : '')); });
  var cap = apCapability();
  log('AP mode supported:       ' + (cap.known ? (cap.ap ? 'YES' : 'NO') : 'unknown (' + cap.why + ')'));
  log('interface combinations:  ' + (cap.combos || 'not reported'));
  log('  -> a separate AP next to the car\'s Wi-Fi needs a combination that allows AP alongside the current mode(s)');
  ['hostapd', 'dnsmasq', 'iw', 'ip', 'iptables', 'nft'].forEach(function (b) { log('tool ' + b + ': ' + (has(b) ? 'present' : 'MISSING')); });
  // Firewall, read-only: would a tool's traffic on each Wi-Fi interface (the
  // car's own Wi-Fi and a phone hotspot the car joined) reach the app?
  log('firewall (read-only): ' + fwKind() + (FW.chain ? ' chain ' + FW.chain.join(' ') : ''));
  var wifis = ifaceList().filter(function (i) { return /^wl/.test(i.name) && i.address; });
  if (fwKind() === 'nft') {
    wifis.forEach(function (w) {
      var v = fwVerdict(w.name);
      if (!v) return;
      var verdict = v.opened ? 'OPEN (beem-bridge rules present)'
        : (v.blockers.length ? 'BLOCKED by: ' + v.blockers[0] : (v.policy === 'accept' ? 'accepted (no catch-all drop found)' : 'policy ' + v.policy + ' - likely BLOCKED'));
      log('   ' + w.name + ' (' + w.address + ')  bridge ports 6801/13400/6811: ' + verdict);
    });
    log('   full input chain:');
    var full = nft(['list', 'chain'].concat(FW.chain));
    (full.ok ? full.out.split('\n') : []).forEach(function (l) { if (l.trim()) log('     ' + l.trim()); });
    log('   -> anything BLOCKED: install with  --open-iface ' + (wifis.map(function (w) { return w.name; }).join(',') || 'wlan_bridge'));
  } else if (fwKind() === 'iptables') {
    sh('iptables -S INPUT 2>/dev/null').out.split('\n').filter(Boolean).forEach(function (l) { log('     ' + l); });
  } else log('   no firewall tool readable');
  var other = sh('pgrep -a hostapd 2>/dev/null');
  log('hostapd already running: ' + (other.ok && other.out.trim() ? other.out.trim().replace(/\n/g, ' | ') : 'no'));
  log('node: ' + process.version + '   socket(s): ' + OPT.socks.join(', '));
  log('');
  log('Nothing was changed. If AP mode is NO or hostapd is MISSING this unit cannot host its own');
  log('network; the app\'s bridge still works on any network the car is already on.');
}
function askStatus() {
  // Try every socket path: a daemon that could only create /dev/shm still answers.
  var paths = OPT.socks.slice(0), errs = [];
  function next() {
    if (!paths.length) { log('daemon not reachable: ' + errs.join('; ')); process.exit(1); return; }
    var p = paths.shift(), c = net.connect(p), buf = '', done = false;
    c.on('connect', function () { c.write(JSON.stringify({ id: 's1', cmd: 'status' }) + '\n'); });
    c.on('data', function (d) {
      buf += String(d);
      if (!done && buf.indexOf('\n') >= 0) {
        done = true;
        var m = JSON.parse(buf.split('\n')[0]); m.socket = p;
        process.stdout.write(JSON.stringify(m, null, 2) + '\n'); c.destroy(); process.exit(0);
      }
    });
    c.on('error', function (e) { if (done) return; done = true; errs.push(p + ': ' + (e.code || e.message)); next(); });
  }
  next();
}

// ---- main ------------------------------------------------------------------
var servers = [], ticker = null;

function shutdown(code) {
  try { clearInterval(ticker); } catch (e) {}
  apStop(function () {
    OPT.openIfaces.forEach(function (i) { fwCloseIface(i); });
    servers.forEach(function (s) { try { s.close(); } catch (e) {} });
    OPT.socks.forEach(function (p) { try { fs.unlinkSync(p); } catch (e) {} });
    process.exit(code || 0);
  });
}

function main() {
  if (OPT.probe) { probe(); return; }
  if (OPT.status) { askStatus(); return; }
  log('starting (pid ' + process.pid + ', node ' + process.version + ') - no Wi-Fi is broadcast until the app asks');
  try { fs.mkdirSync(OPT.runDir, 0x1ed); } catch (e) {}
  try { fs.chmodSync(OPT.runDir, 0x1ed); } catch (e) {}

  var first = OPT.socks[0], pending = OPT.socks.length;
  OPT.socks.forEach(function (p) {
    prepareSocketPath(p, function (free) {
      if (!free) {
        if (p === first) { log('another beem-bridged is already running on ' + p + ' - exiting'); process.exit(0); }
        log('socket ' + p + ' is in use by another process - skipped');
      } else servers.push(serveApp(p));
      pending--;
    });
  });

  // Optional: open the bridge ports on the car's own Wi-Fi as well (the
  // DIRECT-BMW network), for when its firewall is what stops a phone.
  OPT.openIfaces.map(cleanIface).filter(Boolean).forEach(function (i) {
    fwOpen(i, false);
    log((FW.note ? 'firewall NOT opened on ' + i + ': ' + FW.note : 'firewall opened on ' + i + ' (' + fwKind() + ': tcp ' + TCP_PORTS.join('/') + ', udp ' + UDP_PORTS.join('/') + ')'));
  });

  // Bring the network down when nobody needs it; the daemon itself stays up.
  ticker = setInterval(function () {
    if (AP.state !== 'up') return;
    if (stations().length) lastJoined = Date.now();
    if (!appSock && appGoneAt && Date.now() - appGoneAt > 60000) { log('app gone for 60 s - taking the network down'); apStop(); return; }
    if (Date.now() - Math.max(lastJoined, AP.since) > OPT.idleSec * 1000) { log('nobody joined for ' + OPT.idleSec + ' s - taking the network down'); apStop(); }
  }, 15000);

  ['SIGINT', 'SIGTERM'].forEach(function (sig) { process.on(sig, function () { log('signal ' + sig); shutdown(0); }); });
  // SIGHUP arrives when the shell that started us closes (no setsid / nohup on
  // BusyBox units). Ignore it so a background start survives the SSH session.
  process.on('SIGHUP', function () { log('SIGHUP ignored'); });
  process.on('uncaughtException', function (e) { log('fatal: ' + ((e && e.stack) || e)); shutdown(1); });
}

if (require.main === module) main();

module.exports = {
  genPass: genPass, cleanText: cleanText, cleanIface: cleanIface, onAppLine: onAppLine, stateMsg: stateMsg,
  apCapability: apCapability, iwDevs: iwDevs, AP: AP, OPT: OPT, FW: FW, TCP_PORTS: TCP_PORTS, UDP_PORTS: UDP_PORTS,
  fwOpen: fwOpen, fwCloseIface: fwCloseIface, fwVerdict: fwVerdict,
  _setAppSock: function (s) { appSock = s; }
};
