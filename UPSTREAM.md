# Upstream sources

This project bundles three upstream repositories, each copied unchanged except where noted.

## 1. `idrive7-apps/` — the custom apps

- **Repository:** https://github.com/cauliflowermossgrape/idrive7-apps
- **Commit:** `8213223b039a3757e3559e08913140148a1f753b` (2026-11-08, "Merge PR #110: Redline 1.15.0 ...")
- **Licence:** MIT (`idrive7-apps/LICENSE`); Doom app GPL-2.0 (`idrive7-apps/apps/doom/LICENSE`,
  source in `idrive7-apps/third_party/doomgeneric`); fonts SIL OFL 1.1.
- **Change:** `kit/tools/make_kit.py` honours `MGU_NO_DOCKER=1` (set by the installer) to skip the
  Docker build of the GPU renderer and ship the committed prebuilt one, and under it never asks
  "Continue anyway? [y/N]" (`ask()`; upstream's own `prebuilt/SOURCE_SHA256` is older than its source,
  so the question comes up for everyone without Docker). Without that, a Windows PC died with an
  `EOFError` there: it passes the NUL device off as a terminal, so `isatty()` said "ask" and nobody
  could answer. The installer also starts such tools with a closed pipe for stdin instead of NUL.

## 2. `tools/idrive7-8-ssh-root/` — root access over ENET

- **Repository:** https://github.com/cauliflowermossgrape/idrive7-8-ssh-root
- **Commit:** `6d4928e6dca9812e7882318afdd7cba9840c2d62` (2026-11-13). Previously `1d3ba60`; this re-sync
  brings their new work: `mgu7/preflight.py` (a read-only coding backup before flashing — scans the
  coding store 0x3000-0x3FFF + identification over ENET, reads twice and verifies, writes
  `coding_MGU.txt` + a `RESTORE.txt` that steers restore to VO / code-by-FA re-coding, issue #10),
  the `enter-prog` / `exit-prog` recovery commands (`mgu7/diag.exit_programming`,
  `flash.run(stop_after=)`) for a unit stuck on the blue programming screen, and the removal of the
  interactive y/n prompts (the tool is driven non-interactively here). Their GUI redesign is vendored
  but unused — the Bimmhacks installer is the front end.
- **Licence:** The Unlicense (public domain) — `tools/idrive7-8-ssh-root/LICENSE`.
- Copied unchanged, minus `build/`, `dist/`, `data/enrollments/`, `data/logs/` and caches. The
  shared flash assets in `data/assets/` are kept (the tool needs them). Per-car SSH keys the tool
  writes to `data/enrollments/<VIN>/` are never committed or shipped.
- The installer calls its `ssh_enable.py` as a subprocess; it needs the `cryptography` Python
  package, which the installer installs on demand (`pip install --user cryptography`).
- `installer/coding_backup.py` keeps our NCD backup / restore exactly as it was, and adds a **separate,
  independent** `preflight` command (`preflight_backup()`, reusing `mgu7.preflight.backup`). The two are
  not coupled: "Back up coding" writes only the NCD backup, "Read-only backup" writes only the preflight
  record. The vendored `tools/idrive7-8-ssh-root/tests/test_preflight.py` validates preflight in CI.

## 3. `tools/mgu-store/` — the on-car App Store

- **Repository (canonical):** https://github.com/robertnisipeanu/mgu-store
  (mirrored at https://github.com/cauliflowermossgrape/mgu-store, from which this copy was taken)
- **Commit:** `648c7874b40c51375e6647f936f85ae501498bd1`
- **Licence:** GPL-3.0-or-later — `tools/mgu-store/LICENSE`, notices in `THIRD_PARTY_NOTICES.md`.
- Only the single-file installer `deploy/mgu-install.js` is vendored (plus its licence and
  `sources.json`). It is a self-contained Node 18+ script with no dependencies; the installer runs
  it as `node mgu-install.js first-install …`, and it downloads the Store's components from the
  mgu-store GitHub releases at install time. The vendored file keeps its GPL header and is invoked
  as a separate process (arm's-length aggregation), not linked into the Python installer.

## 4. idrive-apps (robertnisipeanu/idrive-apps) — not vendored

- **Repository:** https://github.com/robertnisipeanu/idrive-apps (GPL-3.0)
- Not copied into this repo: it is a built-in **mgu-store** source and is installed through the
  on-car Store (online) or staged to USB with `mgu-install.js usb-repo github:robertnisipeanu/idrive-apps`
  (offline). The installer points the on-car Store at it (built-in source, or `install-idrive-apps` /
  `usb-repo`), so nothing from the repo needs to ship in the client package.

## 5. car-apps (IterioDev/car-apps) — not vendored

- **Repository:** https://github.com/IterioDev/car-apps (**PolyForm Strict 1.0.0** — NOT open
  source; do not copy or redistribute it). It is a built-in **mgu-store** source, installed through
  the on-car Store or staged to USB at the car owner's choice. Nothing from it is copied into this
  repository; the installer only points the Store at it.

## Bimmhacks additions (not upstream)

- `installer/coding_backup.py` is the coding engine: over ENET (reusing the vendored `mgu7` package) it
  takes a snapshot (CAFD versions / VIN / IP / faults) and, where the unit hands it over, backs up the
  coding data (NCD) as E-Sys-compatible `.ncd` files, then can **verify** and **restore** it. Restore is
  experimental and safe-by-construction: it writes only the NCD it read, only to the identifiers it read,
  gates on a CAFD-versions-and-VIN match, requires a typed confirmation and reads every block back.
  Tests: `installer/tests/test_coding_restore.py` (round-trip, the gates, dry-run writes nothing, a
  refused or non-sticking write is reported).
- `idrive7-apps/apps/bimmhacks_hello/` is a small original example app (MIT, part of this project)
  that builds with the idrive7-apps kit; it is the starter for `docs/MAKE-YOUR-OWN-APP.md`.
- `idrive7-apps/apps/bimmhacks_tuner/` is an original app (MIT, part of this project): gauges, CSV
  datalogger, engine-unit fault read/clear and an off-by-default map switch for a car that already
  runs a multi-map tune. It reuses, with attribution, M Vitals' engine-unit identifier lists
  (`config.dme`) and Engine Config's two map-switch commands (captured by a1-mapping, PR #23 of
  idrive7-apps). Tests: `idrive7-apps/kit/tools/test_bimmhacks_tuner.js`. It does not flash.
- `installer/pid_capture.py` reads a Wireshark capture (pcap / pcapng) of the ENET link, or a root-tool
  session log, and reports which diagnostic identifiers a logging tool asked the car for, writing a
  `custom_pids.json` skeleton for Bimmhacks Tuner. Offline; it never talks to the car.
- `installer/tests/` holds a fake ENET gateway (`fake_enet.py`, which also answers the root tool's
  enrollment routines) and a fake `ssh` (`fake_ssh.sh`), with tests that drive the real coding-snapshot,
  VIN-detection and root-tool `status` code against them (`test_coding_backup.py`), the installer's safety
  guards — a refused flash must send the car nothing (`test_guards.py`) —, the root-access *enable* path end
  to end through the real tool and through the installer (`test_root_enable.py`), and the capture analyser
  on synthetic pcap/pcapng files (`test_pid_capture.py`).
- `installer/unit_add_source.js` is an original script the installer sends to the head unit over ssh
  (`node -`, the unit's own Node 8): it asks the Store's updater to add a GitHub repo as a Store source
  with the `source-add` request every Store client uses (mgu-store `native/mgu-updater/README.md`, "The
  socket"), with the secret the updater leaves for the Store. It is how RideTube and a repo of your own
  get into the Store. `installer/tests/test_store_updater.py` runs it against the **real** mgu-updater
  from a checkout of mgu-store pinned in `installer/tests/MGU_STORE_COMMIT` (`updater_harness.js` starts
  it on a throw-away layout; nothing is vendored or shipped).
- `installer/screenshots/` holds screenshots of the window's three tabs (plus an overview), made by
  `installer/tests/desktop_shots.py`, which opens the real window under Xvfb and captures each tab
  (developer tool; not shipped).
- `installer/pocket_server.py` + `installer/pocket/` is **Bimmhacks Pocket**: the same installer
  functions behind a small HTTP/JSON server and a phone-sized page, for a box (a Raspberry Pi) on
  the ENET cable; `setup-box.sh` turns a Pi into that box. It can stage the Store package on the box
  (`usb-repo` + `first-install --from`) so the App Store installs with no internet on the box.
  Tests: `installer/tests/test_pocket.py` (the gates, the read-only actions, the snapshot download
  and the root ENABLE path through the server, all against the fake car).
- `installer/bimmhacks_installer.py` is the window + CLI that drives all of the above. The flashing
  app, installer and Bimmhacks apps were developed by **Mr. Wali**.

## Updating to newer upstream versions

```sh
# apps
git clone --depth 1 https://github.com/cauliflowermossgrape/idrive7-apps /tmp/apps
rm -rf idrive7-apps && mkdir idrive7-apps
(cd /tmp/apps && tar cf - --exclude=./.git .) | tar xf - -C idrive7-apps
# re-apply the MGU_NO_DOCKER lines in idrive7-apps/kit/tools/make_kit.py (pick_renderer, and ask(): no prompt under it)

# root tool
git clone --depth 1 https://github.com/cauliflowermossgrape/idrive7-8-ssh-root /tmp/sshroot
rm -rf tools/idrive7-8-ssh-root && mkdir tools/idrive7-8-ssh-root
(cd /tmp/sshroot && tar cf - --exclude=./.git --exclude=./build --exclude=./dist \
   --exclude='./data/enrollments' --exclude='./data/logs' .) | tar xf - -C tools/idrive7-8-ssh-root

# store installer
git clone --depth 1 https://github.com/cauliflowermossgrape/mgu-store /tmp/store
cp /tmp/store/deploy/mgu-install.js /tmp/store/LICENSE /tmp/store/THIRD_PARTY_NOTICES.md \
   /tmp/store/deploy/sources.json tools/mgu-store/

# then update the commit hashes above and bump VERSION in installer/bimmhacks_installer.py
python3 installer/make_client_package.py          # builds two zips: PC installer + Pocket box
```

`make_client_package.py` builds **two independent zips** so the PC and Pocket releases are separate:
`Bimmhacks-MGU-Installer-<version>.zip` (the desktop app, with no Pocket code) and
`Bimmhacks-Pocket-<version>.zip` (the box: the Pocket server, its page and the engine it drives). Pass
`--pc` or `--pocket` to build just one. Neither ships the `idrive7-apps` tree (apps install from the
on-car Store), the tests, screenshots, or client keys.

Not included (and never to be committed or shipped): BMW's `oap-sdk` and any built `.mgu`. They
contain BMW's copyrighted code.
