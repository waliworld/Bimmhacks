'use strict';
/* Starts the REAL mgu-updater (the daemon behind the on-car Store) in this process, with a throw-away
 * configuration, so the installer's add-source script can be tried against the real protocol:
 *
 *   node updater_harness.js <mgu-store checkout> <work dir>
 *
 * It lays out what a head unit has - the oap folder with the Store's registry entry and its data folder,
 * the Store's `updater-secret`, a sources.json seeded like first-install seeds it (tools/mgu-store/
 * sources.json) - starts the updater's socket and writes <work dir>/ready.json when it listens.
 * Nothing leaves this machine: the updater's GitHub addresses point at a closed local port, so a source
 * that is added is simply reported unreachable. Stops on SIGTERM.
 *
 * Needs a checkout of https://github.com/robertnisipeanu/mgu-store (GPL-3.0; not part of this package -
 * installer/tests/test_store_updater.py finds it through MGU_STORE_SRC and skips without it).
 */
const fs = require('fs');
const path = require('path');

const storeSrc = process.argv[2];
const work = process.argv[3];
if (!storeSrc || !work) {
    console.error('usage: node updater_harness.js <mgu-store checkout> <work dir>');
    process.exit(2);
}
const { Updater, loadConf } = require(path.join(storeSrc, 'native', 'mgu-updater', 'js', 'updater'));

const mk = (p) => fs.mkdirSync(p, { recursive: true });
const oap = path.join(work, 'oap');
const STORE_UID = '13050';
mk(path.join(oap, 'oap_12201', 'data', 'apps'));
fs.writeFileSync(path.join(oap, 'oap_12201', 'data', 'apps', 'robertn_store.json'), JSON.stringify({ userId: Number(STORE_UID) }));
mk(path.join(oap, `oap_${STORE_UID}`, 'data'));
['state', 'opt', 'media', 'run', 'shm', 'cred'].forEach((d) => mk(path.join(work, d)));
fs.writeFileSync(path.join(work, 'mounts'), 'proc /proc proc rw 0 0\n');

const seed = path.join(__dirname, '..', '..', 'tools', 'mgu-store', 'sources.json');
const sourcesFile = path.join(work, 'sources.json');
fs.copyFileSync(seed, sourcesFile);

const closed = 'https://127.0.0.1:9';
const conf = Object.assign(loadConf(), {
    sources: sourcesFile, credentials_dir: path.join(work, 'cred'), state_dir: path.join(work, 'state'),
    tool_root: path.join(work, 'opt'), oap_root: oap, mainapp_uid: '12201', store_app: 'robertn_store',
    socket: path.join(work, 'shm', 'updater.sock'), socket_group: '', web_secret: '',
    fetch_uid: '12990', fetch_gid: '12990', build_uid: '12995', build_gid: '12995', web_uid: '12996', web_gid: '12202',
    github_api: closed, github_web: closed, github_raw: closed, github_codeload: closed,
    usb_roots: path.join(work, 'media'), mounts: path.join(work, 'mounts'), lease: path.join(work, 'lease.json'),
    privacy_tool: '', reboot: ''
});
const u = new Updater(conf);
const secretPath = path.join(oap, `oap_${STORE_UID}`, 'data', 'updater-secret');

u.serve().then(() => {
    fs.writeFileSync(secretPath, u.secret, { mode: 0o400 });       // what writeSecrets() leaves for the Store
    fs.writeFileSync(path.join(work, 'ready.json'), JSON.stringify({
        socket: conf.socket, oap, sources: sourcesFile, secret: secretPath, secretText: u.secret,
        stored: u.stored.map((s) => s.id)
    }));
}).catch((e) => {
    console.error('harness: ' + (e && e.stack || e));
    process.exit(1);
});
process.on('SIGTERM', () => process.exit(0));
