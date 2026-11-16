# Wireless bridge (phone / MHD / xHP → MGU → car) — design & status

**Status:** the app hosts the bridge itself (proven on the car: its listener binds
and passes its own self-test). An optional root helper adds a separate Wi-Fi
network for it. Still unproven on the car: a phone tool completing a session —
that is the next on-car test.

## The goal

Let an external tool (MHD, xHP, BimmerLink, E-Sys, ISTA…) connect to the head unit
over Wi-Fi and reach the car's modules **through the MGU**, with no ENET cable or
paid Wi-Fi adapter. The owner's own car; open source; consent-gated on screen.

## What is proven / what is not

| | |
|---|---|
| **The app can listen** | Yes — on the car (uid 12261, not root) it binds TCP 6801 and 13400 on every interface and its loopback self-test passes. Ports below 1024 are refused (EACCES), which the bridge does not need. |
| **The app reaches the modules** | Yes — TASU (`InternalTasu`) answers from the DME (P6: GO, VIN read). |
| **A gateway on the IP side** | Not found. P7's earlier "reachable 160.48.199.99" was the head unit's own address (fixed: P7 now ignores its own addresses). |
| **A phone reaching the app's listener** | **Not yet shown on the car.** If the car's Wi-Fi isolates clients or firewalls the ports, the root helper's own network is the fallback. |

## Architecture

```
                         ┌─────────────────────── head unit ───────────────────────┐
 phone / tool ──Wi-Fi──▶ │ BeeM-Flash app listener  ── TASU (SOME/IP) ──▶ gateway / DME / EGS
   (MHD, xHP, …)         │  TCP 6801 HSFZ · TCP 13400 DoIP · UDP 6811/13400 discovery │
                         │  every request judged on-screen (bridgegate + relaypolicy) │
                         │                                                           │
                         │ beem-bridged (root, optional): separate Wi-Fi AP on a     │
                         │  virtual interface + DHCP + firewall opened for the ports │
                         └───────────────────────────────────────────────────────────┘
```

* **The app is the bridge.** One protocol implementation (`bridgeserver.js`,
  `discovery.js`), behaving like a real BMW gateway: HSFZ ACKs, identification
  `0x0011` (`DIAGADR10BMWMAC…BMWVIN…`), alive check, DoIP routing activation and
  vehicle announcement. It listens on every interface, so it serves the car's own
  Wi-Fi, a phone hotspot the car joined, and the helper's network alike.
* **The helper only does what needs root** (`tools/beem-bridge`, see
  [INSTALL.md](../tools/beem-bridge/INSTALL.md)): create a separate Wi-Fi network
  next to the car's own (so CarPlay keeps working), give the phone an address,
  open the firewall for the bridge ports on that network, and check as root that
  the app answers there. It never carries diagnostic traffic and holds no policy.

## Security model (already built, with tests)

`bridgegate.js` + `relaypolicy.js` + `test/run.js` pin these invariants today:

- **Off by default; three deliberate on-HU tiers.** Acknowledge (persisted) →
  enable (never persisted) → arm a **time-boxed lease** (heartbeat; fails closed in
  seconds on loss, disarm, app exit, sleep, ignition-off, or motion). Flashing is a
  **separate** write-arm for one named ECU, reusing the local flasher's confirm chain.
- **Strict re-validation in the app.** Relayed frames may address only an ECU
  allow-list (default gateway `0x10`, DME `0x12`, EGS `0x18`; reads only); **functional/broadcast from the phone is
  refused**; `clearDTC` and all writes are separately armed; disallowed frames get a
  UDS negative, not silent passthrough. The phone's tester source is collapsed to the
  one TASU tester (`0xF2`).
- **One pinned peer per armed session.** The listener serves every interface (the
  phone's network cannot be known in advance), so the protection is the gate: nothing
  is relayed until the bridge is armed on the car's screen, and the first device in an
  armed session owns it — any other device is refused. The helper's firewall rules are
  scoped to its own network and removed when that network goes down.
- **No secrets, no content trust, plaintext disclosure.** The bridge bundles no keys
  or calibration and cannot validate a third-party tool's flash content; the Wi-Fi leg
  is plaintext (xHP presents no token), so safety rests on the WPA2 hotspot + in-car
  approval + source pinning. USB tether preferred where client-isolation is weak.
- **Protects what works.** The helper is optional: without it the app's bridge,
  live data, the flasher and the theme all work; it only adds a separate network.

## On-car probes (run these first — nothing depends on code until they pass)

See `tools/bridge-probes/`. Run as **root** on the head unit; read-only except a
temporary listener/firewall rule that are removed.

- **P1 — app↔daemon socket.** `node tools/bridge-probes/p1-daemon.js`, then set the
  app's `config.bridgeProbe=true`; the app reports `P1 OK …` on its error banner if it
  reaches the daemon. *Foundation of the whole design.*
- **P2 + P3 — inbound Wi-Fi reachability (make-or-break).**
  `sh tools/bridge-probes/probe-car.sh`; from the phone, `nc <MGU-wlan0-ip> 13400`.
  Text arriving = GO. Nothing either way = no wireless bridge (try USB tether).
- **P6 — TASU reads** (already in the app): tap SCAN on the CODES tab; fault codes
  appearing confirms the car-side leg.
- **P7 — TASU flash capacity** (bench only, a consented ECU): before any flash relay.

## Kill criteria (we stop and say so, rather than ship something that doesn't work)

- P1 fails (app can't reach a daemon socket) → architecture has no foundation.
- P3 fails **and** USB tether is unavailable → no wireless/tethered bridge.
- P6 fails (TASU doesn't answer reads) → the whole premise collapses.
- P7 shows TASU can't carry flash-grade UDS **and** no gateway route → the bridge can
  still ship **read/diagnostics only**; flashing-through-the-MGU is not deliverable.
- No root shell / Store won't install a third-party component → undeployable.

## Licensing

Our own `doip.js` (ISO 13400 codec) is MIT and seeds the daemon's codec. The deploy
and UNIX-socket-framing **patterns** from the MIT `idrive7-apps` repo are reusable
with attribution. **Not copyable:** `idrive7-apps/kit/renderd/src` (GPL-3.0) and
anything under `IterioDev/car-apps` (PolyForm Strict). The daemon is written clean.
