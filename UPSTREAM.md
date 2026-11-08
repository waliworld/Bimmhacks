# Upstream sources

This project bundles three upstream repositories, each copied unchanged except where noted.

## 1. `idrive7-apps/` — the custom apps

- **Repository:** https://github.com/cauliflowermossgrape/idrive7-apps
- **Commit:** `8213223b039a3757e3559e08913140148a1f753b` (2026-11-08, "Merge PR #110: Redline 1.15.0 ...")
- **Licence:** MIT (`idrive7-apps/LICENSE`); Doom app GPL-2.0 (`idrive7-apps/apps/doom/LICENSE`,
  source in `idrive7-apps/third_party/doomgeneric`); fonts SIL OFL 1.1.
- **Change:** `kit/tools/make_kit.py` honours `MGU_NO_DOCKER=1` (set by the installer) to skip the
  Docker build of the GPU renderer and ship the committed prebuilt one.

## 2. `tools/idrive7-8-ssh-root/` — root access over ENET

- **Repository:** https://github.com/cauliflowermossgrape/idrive7-8-ssh-root
- **Commit:** `1d3ba60a9aa8a79fe16e1c26488a89918ec1d928`
- **Licence:** The Unlicense (public domain) — `tools/idrive7-8-ssh-root/LICENSE`.
- Copied unchanged, minus `build/`, `dist/`, `data/enrollments/`, `data/logs/` and caches. The
  shared flash assets in `data/assets/` are kept (the tool needs them). Per-car SSH keys the tool
  writes to `data/enrollments/<VIN>/` are never committed or shipped.
- The installer calls its `ssh_enable.py` as a subprocess; it needs the `cryptography` Python
  package, which the installer installs on demand (`pip install --user cryptography`).

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
  (offline). The installer's "idrive-apps" and "Bimmhacks Apps" tabs use that path, so nothing from
  the repo needs to ship in the client package.

## 5. car-apps (IterioDev/car-apps) — not vendored

- **Repository:** https://github.com/IterioDev/car-apps (**PolyForm Strict 1.0.0** — NOT open
  source; do not copy or redistribute it). It is a built-in **mgu-store** source, installed through
  the on-car Store or staged to USB at the car owner's choice. Nothing from it is copied into this
  repository; the installer only points the Store at it.

## Bimmhacks additions (not upstream)

- `installer/coding_backup.py` reads a diagnostic coding snapshot (CAFD versions / VIN / IP / faults)
  over ENET by reusing the vendored `mgu7` package. It is a record of what to restore, not a
  restorable coding backup.
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
- `installer/bimmhacks_installer.py` is the window + CLI that drives all of the above. The flashing
  app, installer and Bimmhacks apps were developed by **Mr. Wali**.

## Updating to newer upstream versions

```sh
# apps
git clone --depth 1 https://github.com/cauliflowermossgrape/idrive7-apps /tmp/apps
rm -rf idrive7-apps && mkdir idrive7-apps
(cd /tmp/apps && tar cf - --exclude=./.git .) | tar xf - -C idrive7-apps
# re-apply the MGU_NO_DOCKER line in idrive7-apps/kit/tools/make_kit.py (pick_renderer)

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
python3 installer/make_client_package.py
```

Not included (and never to be committed or shipped): BMW's `oap-sdk` and any built `.mgu`. They
contain BMW's copyrighted code; the installer extracts the SDK from each client's own car.
