'use strict';
/* Bimmhacks: add a GitHub repository as a source of the on-car MGU App Store.
 *
 * Runs ON THE HEAD UNIT, sent there over ssh by the installer (`/usr/bin/node - <owner/repo> <channel>`,
 * this file on stdin). It asks the Store's updater over its socket - the "source-add" request every
 * Store client uses (mgu-store: native/mgu-updater/README.md, "The socket") - with the privileged secret
 * the updater leaves in the Store's data folder. The Store then reads the source and shows its apps.
 *
 *   node - <owner/repo> <releases|tags|branch|branch:NAME> [socket] [secret file]
 *
 * Prints one last line and exits:
 *   ADDED <repo> (<channel>)     exit 0
 *   ALREADY <repo>               exit 0   (the Store already has it: a built-in source, or added before)
 *   WAITING: <why>               exit 3   (the updater is not running / has left no secret yet: a Store that
 *                                          was just installed starts its updater at the next head unit start)
 *   ERROR: <why>                 exit 1   (refused, or bad input)
 * Written for the unit's Node (8.16): no modern syntax.
 */
var fs = require('fs');
var net = require('net');
var path = require('path');

/* `node - a b` puts '-' at argv[1]; `node unit_add_source.js a b` puts the file there */
var raw = process.argv.slice(1);
if (raw.length && (raw[0] === '-' || /unit_add_source\.js$/.test(raw[0]))) {
    raw = raw.slice(1);
}
var repo = raw[0] || '';
var channel = raw[1] || '';
var SOCK = raw[2] || process.env.MGU_UPDATER_SOCKET || '/dev/shm/mgu-updater/updater.sock';
var SECRET_ARG = raw[3] || null;
var OAP = process.env.MGU_OAP_ROOT || '/var/opt/public/oap';     // MainApp's app folders (updater.conf oap_root)
var MAINAPP_UID = process.env.MGU_MAINAPP_UID || '12201';          // updater.conf mainapp_uid
var STORE_APP = 'robertn_store';                                    // updater.conf store_app
var RETRIES = Number(process.env.MGU_ADD_SOURCE_RETRIES || 8);      // tries before "waiting" is reported
var WAIT_MS = Number(process.env.MGU_ADD_SOURCE_WAIT_MS || 3000);
var ANSWER_MS = Number(process.env.MGU_ADD_SOURCE_ANSWER_MS || 20000);

/* The last line, then exit with the code once stdout has been written (never process.exit(): a pipe can
 * still hold the line). A timer that does not keep the process alive is the safety net. */
function end(code, line) {
    console.log(line);
    process.exitCode = code;
    setTimeout(function () { process.exit(code); }, 3000).unref();
}

function fail(why) {
    end(1, 'ERROR: ' + why);
}

/* no top-level return: this runs as `node -` (a script on stdin), where it is a syntax error */
function badInput() {
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) {
        return 'repo must be <owner>/<repo>';
    }
    if (!/^(releases|tags|branch(:[^\s]+)?)$/.test(channel)) {
        return 'channel must be releases, tags, branch or branch:<name>';
    }
    return null;
}

/* The updater's privileged secret: in the Store's data folder, 0400, owner the Store's uid (root reads
 * it). The Store's uid is in MainApp's registry entry for it; failing that, the one app folder that
 * has the file. */
function secretFile() {
    if (SECRET_ARG) {
        return SECRET_ARG;
    }
    try {
        var entry = JSON.parse(fs.readFileSync(path.join(OAP, 'oap_' + MAINAPP_UID, 'data', 'apps', STORE_APP + '.json'), 'utf8'));
        var p = path.join(OAP, 'oap_' + entry.userId, 'data', 'updater-secret');
        if (fs.existsSync(p)) {
            return p;
        }
    } catch (e) { /* no registry entry: look for the file */ }
    var hits = [];
    try {
        fs.readdirSync(OAP).forEach(function (n) {
            var p2 = path.join(OAP, n, 'data', 'updater-secret');
            if (/^oap_\d+$/.test(n) && fs.existsSync(p2)) {
                hits.push(p2);
            }
        });
    } catch (e) { /* no app folders at all */ }
    return hits.length === 1 ? hits[0] : null;
}

function retry(n, why) {
    if (n + 1 >= RETRIES) {
        end(3, 'WAITING: ' + why);
        return;
    }
    console.log('waiting for the updater: ' + why);
    setTimeout(function () { attempt(n + 1); }, WAIT_MS);
}

function attempt(n) {
    var file = secretFile();
    if (!file) {
        retry(n, 'the Store\'s updater has not started yet (no updater secret)');
        return;
    }
    var secret;
    try {
        secret = fs.readFileSync(file, 'utf8').trim();
    } catch (e) {
        retry(n, 'cannot read the updater secret: ' + e.message);
        return;
    }
    var rid = 'bimmhacks-' + Date.now();
    var sock = net.connect(SOCK);
    var buf = '';
    var done = false;
    var timer = setTimeout(function () { finish(function () { retry(n, 'the updater gave no answer'); }); }, ANSWER_MS);
    function finish(fn) {
        if (done) {
            return;
        }
        done = true;
        clearTimeout(timer);
        sock.destroy();
        fn();
    }
    sock.setEncoding('utf8');
    sock.on('connect', function () {
        sock.write(JSON.stringify({ op: 'source-add', type: 'github', repo: repo, channel: channel, secret: secret, rid: rid }) + '\n');
    });
    sock.on('data', function (d) {
        buf += d;
        var nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
            var line = buf.slice(0, nl);
            buf = buf.slice(nl + 1);
            var msg;
            try {
                msg = JSON.parse(line);
            } catch (e) {
                continue;
            }
            if (!msg || msg.rid !== rid) {
                continue;                       // the state the updater pushes on connect carries no rid
            }
            if (msg.type === 'error') {
                var why = String(msg.error || 'refused');
                finish(function () {
                    if (/ is already a source$/.test(why)) {
                        end(0, 'ALREADY ' + repo);
                    } else if (why === 'forbidden') {
                        fail('the updater refused the secret (forbidden)');
                    } else {
                        fail(why);
                    }
                });
            } else {
                finish(function () { end(0, 'ADDED ' + repo + ' (' + channel + ')'); });
            }
            return;
        }
    });
    sock.on('error', function (e) {
        finish(function () { retry(n, 'the updater is not running (' + (e.code || e.message) + ')'); });
    });
    sock.on('close', function () {
        finish(function () { retry(n, 'the updater closed the connection'); });
    });
}

var problem = badInput();
if (problem) {
    fail(problem);
} else {
    attempt(0);
}
