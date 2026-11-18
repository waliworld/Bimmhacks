# Installing the BeeM-Flash bridge helper — full guide

This guide installs **`beem-bridged`**, the small root helper that gives the
BeeM-Flash bridge **its own Wi-Fi network** on the head unit. It takes about ten
minutes with your root tool.

---

## 0. Do you need it? (read this first)

The BeeM-Flash **app is the bridge**. With no helper at all, the app already
listens for tools on **every network the car is on**:

| Port | What it serves | Used by |
|---|---|---|
| TCP **6801** | ENET / HSFZ | MHD, E-Sys, ISTA, BimmerCode, Boostline |
| TCP **13400** | DoIP (ISO 13400) | DoIP tools |
| UDP **6811** and **13400** | "where is the car?" discovery | tools that search instead of asking for an address |

So **try without the helper first**: phone on the car's Wi-Fi (or the car joined to
your phone's hotspot) → BRIDGE: ON → connect the tool.

**Install the helper when** the tool can't reach the car that way. Usual reasons:
the car's own Wi-Fi isolates devices from each other, or its firewall drops the
bridge ports. The helper fixes both by giving the bridge a separate network that
it controls. It also checks, as root, that the app answers on that network, so you
know it works **before** you pick up the phone.

What the helper does **not** do: it never talks to the car and never carries
diagnostic data. The app does that, and every request is still approved on the
car's screen.

---

## 1. What you need

* **Root access to the head unit** — your SSH-root / root-shell tool.
* **These four files**, from this repository's `tools/beem-bridge/` folder:
  `beem-bridged.js`, `beem-bridged.service`, `install.sh`, `uninstall.sh`.
* **BeeM-Flash v2.28.0 or newer** on the head unit (it has the WI-FI: OWN button).
* A way to copy files to the head unit (scp, or your root tool's file transfer).

---

## 2. Copy the files to the head unit

Put the four files in one folder on the head unit, for example `/tmp/beem-bridge`.
With scp from a computer on the same network:

```bash
scp beem-bridged.js beem-bridged.service install.sh uninstall.sh root@<head-unit-ip>:/tmp/beem-bridge/
```

Then open a root shell on the head unit and go to that folder:

```bash
cd /tmp/beem-bridge
```

---

## 3. Probe first — this changes nothing

```bash
sh install.sh --probe
```

It prints what this head unit can do. Look for these lines:

| Line | What you want | If not |
|---|---|---|
| `AP mode supported:` | `YES` | The Wi-Fi chip can't host a network → the helper can't help; see §8. |
| `interface combinations:` | lists `AP` together with the mode already in use (`managed` / `P2P` / `AP`) | The chip can't run a second network next to the car's. The helper refuses to take over the car's Wi-Fi unless you allow it (§5, `--take-iface`). |
| `tool hostapd:` | `present` | Required. Without it there's no network → see §8. |
| `tool dnsmasq:` | `present` | Optional. Without it, give the phone a static address (§6). |
| `tool iw:` / `tool ip:` | `present` | Required. |
| `tool iptables:` | `present` | Needed only if a firewall blocks the ports. |
| `wireless (iw dev):` | your interfaces and their channel | Note the channel: the helper uses the same one. |
| `firewall (read-only):` | one line per Wi-Fi interface: `accepted` or `OPEN` | `BLOCKED by: …` means the firewall drops tool traffic on that interface. Install with `--open-iface <that interface>` (§5). BMW units use **nftables**; the helper adds its rules there (tagged `beem-bridge`, removed when it stops). |

**Send me a photo of this output if anything is MISSING or NO.**

---

### 3b. Can the phone reach the head unit at all? (2 minutes, recommended)

`tools/bridge-probes/probe-car.sh` answers that directly. Copy it to the head unit and
run it as root with the phone on the same Wi-Fi as the car:

```bash
sh probe-car.sh
```

It prints the complete firewall input chain, whether the app's bridge is listening on
**6801 / 13400 / 6811** (turn BRIDGE: ON first), and an address for each Wi-Fi
interface (e.g. `http://172.16.222.97:47001/`). Open that address in **Safari** on
the phone twice: once in phase A (the firewall as it is), then reload in phase B (with a
temporary allow rule, removed afterwards). The page says **"BeeM-Flash probe: reached"**
when that path works:

| Result | Meaning |
|---|---|
| reached in phase A | Nothing blocks that path. |
| reached only in phase B | The firewall blocks it → install with `--open-iface <interface>`. |
| never reached | The Wi-Fi keeps devices apart, or the phone is on another network → try the other path (phone hotspot ↔ car Wi-Fi). |

## 4. Find the app's user id (one number)

On the car: **BeeM-Flash → SETUP → DIAG → RUN**. Line **P8 PRIVILEGE** shows
`uid NNNNN`. On the test car it is **12261**, the installer's default. If yours
differs, pass it in step 5 with `--app-uid NNNNN`.

---

## 5. Install

```bash
sh install.sh
```

or, with a different app uid:

```bash
sh install.sh --app-uid 12261
```

The installer:

1. stops any earlier copy;
2. copies the helper to `/var/opt/beem-bridge/`;
3. checks that the helper loads under the head unit's own Node;
4. starts it, using the best option this head unit allows:
   * `/etc` writable → a **systemd service that starts at every boot**;
   * `/etc` **read-only** (seen on the test car) → a **transient systemd service**
     (`systemd-run`): supervised and restarted if it stops, but **not started at boot**.
     Run `sh install.sh` again after a head-unit restart.
   * no systemd → a background start (works without `setsid` / `nohup`, and survives
     the SSH session closing);
5. **checks the helper answers** on its control socket, and says clearly if not.

You should see `beem-bridged is installed and answering (systemd).`

**Optional switches** (only if you need them):

| Switch | When |
|---|---|
| `--open-iface wlan_bridge` | You want to keep using the **car's own Wi-Fi** and the problem is its firewall. Opens the bridge ports on that interface while the helper runs. |
| `--take-iface` | The probe showed the chip **can't** run a second network. This lets the helper take over the Wi-Fi radio. **The car's own Wi-Fi and wireless CarPlay drop while the bridge network is on.** |
| `--channel N` | Fix the network's Wi-Fi channel (default: the same channel the car's Wi-Fi uses). |
| `--no-systemd` | Force a detached start (it won't start at boot). |

Nothing is broadcast after installing. The helper waits until you turn its network
on from the app.

---

## 6. Use it

1. On the car: **BeeM-Flash → CONNECT → WIRELESS BRIDGE → BRIDGE: ON.**
2. Tap **WI-FI: CAR** so it changes to **WI-FI: OWN**.
3. The screen shows:
   * **NETWORK** `BeeM-Flash` and a **PASSWORD** (new each time unless you keep the helper running),
   * **TOOL** `192.168.44.1 : 13400 / 6801`,
   * **✓ app answers on it.** This is the helper's root-side check. If it shows
     **✗**, read the top-right message and §7.
4. On the phone: join **BeeM-Flash** with that password. The phone stays on mobile
   data for the internet; this network is only for the car. If iOS says
   "No Internet Connection", that's expected; stay connected.
5. Open the tool:
   * **MHD / ENET-style tools**: let the tool search for the car (the app answers
     the search), or set the address `192.168.44.1` port `6801`.
   * **DoIP tools**: `192.168.44.1` port `13400`.
6. The CONNECT page shows the device and counts every request
   (**Relayed / refused**). Live data and diagnostics work in safe mode. Writes and
   flashing need **FULL GW** (two taps, on screen).

No DHCP (`dnsmasq` missing)? Give the phone a static address: IP `192.168.44.50`,
mask `255.255.255.0`, no router.

Turning **BRIDGE: OFF**, tapping **WI-FI: OWN** again, leaving the app for a minute,
or 30 minutes with nobody joined all take the network down. The helper keeps
running, idle.

---

## 7. Check and troubleshoot

```bash
sh install.sh --status            # the helper's live state (network, errors, devices)
journalctl -u beem-bridged -e     # its log (systemd install)
```

| You see | Meaning | Fix |
|---|---|---|
| App: *"The bridge helper is not installed or not running (ENOENT …)"* | The app can't find the helper's socket. | `sh install.sh --status`. If that fails, `systemctl restart beem-bridged`, then read the log. |
| App: *"… (EACCES …)"* | The socket exists but the app isn't allowed in. | Reinstall with the right `--app-uid` (§4). |
| *"this radio cannot add a separate access point …"* | The chip can't run a second network. | Use the car's Wi-Fi with `--open-iface wlan_bridge`, or accept `--take-iface` (CarPlay drops while on). |
| *"hostapd is not installed on this unit"* | No access-point software. | See §8. |
| *"hostapd exited / did not report AP-ENABLED"* | The chip refused the settings, often the channel. | Reinstall with a fixed channel: `sh install.sh --channel 6` (or `--channel 36`), or send me the log. |
| **✗ app not answering on it** | Network up, but the app's listener isn't reachable on it. | Make sure BRIDGE: ON shows "listener verified". Send me `sh install.sh --status`. |
| Phone joins but the tool finds nothing | Search not reaching the app. | Type the address instead: `192.168.44.1` port `6801` (MHD) or `13400`. |

---

## 8. If the head unit can't host a network (the test car: no `hostapd`, no `iw`)

If the probe shows `AP mode supported: NO` or `hostapd: MISSING`, the helper can't
create a network on this unit. The bridge still works on any network the car is
already on:

* **Car joined to your phone's hotspot**: the app lists that address (often
  `172.20.10.x`) on the bridge page; point the tool there.
* **Car's own Wi-Fi with its firewall opened**:
  `sh install.sh --open-iface wlan_bridge`, then the phone on the car's Wi-Fi uses
  the `172.16.222.97` address shown. The helper keeps those ports open while it runs
  and removes the rules when it stops.

Either way, open **CONNECT → WIRELESS BRIDGE** while the tool tries to connect. The
**TOOL ACTIVITY** list shows exactly what reached the car:

| TOOL ACTIVITY shows | Meaning |
|---|---|
| nothing at all | The tool's packets never reached the head unit: wrong network, Wi-Fi client isolation, or a firewall. |
| `UDP 6811 HSFZ ident from … → answered` only | The tool found the car but never connected. Note which port it should have used. |
| `TCP 6801 connect …` then `closed … after 0 frames` | Connected but sent nothing we recognised; the first bytes are shown. |
| `request 0x22 → ECU 0x10: refused (…)` | It got through; the reason is shown. FULL GW may be needed. |

**Send me a photo of that list** after a tool attempt: it shows exactly where to look next.

---

## 9. What it changes, and removing it

While running, the helper creates **nothing permanent**:

* a virtual Wi-Fi interface `beem0`, only while its network is on;
* firewall rules for ports 6801/13400/6811 (and DHCP) on that interface only,
  removed when the network goes down;
* `/run/beem-bridge/` (socket, hostapd config, leases), removed when it stops.

Installed files: `/var/opt/beem-bridge/` and `/etc/systemd/system/beem-bridged.service`.

To remove everything:

```bash
sh uninstall.sh
```

It stops the helper cleanly (so the helper removes its own network, firewall
rules and interface), cleans up anything a crash left behind, and deletes the
files. The car's own Wi-Fi isn't touched.
