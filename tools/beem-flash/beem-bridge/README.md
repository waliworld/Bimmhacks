# beem-bridge — optional root helper for the BeeM-Flash bridge

**Install guide: [INSTALL.md](INSTALL.md).**

The BeeM-Flash app **is** the bridge. Its own listener serves ENET/HSFZ on
TCP 6801, DoIP on TCP 13400 and tool discovery on UDP 6811 / 13400, on every
network the car is on. It judges every request against the gate on the car's
screen.

This helper adds only what an app sandbox cannot do without root:

1. a **separate Wi-Fi network** for the bridge, on a virtual interface next to the
   car's own Wi-Fi (CarPlay keeps working);
2. **DHCP** for the phone (no default route, no DNS: the phone keeps mobile data);
3. the **firewall opened** for the bridge ports on that network only;
4. a **root-side check** that the app's listener answers on it.

```
phone/tool --Wi-Fi (helper's network)--> app listener 6801/13400 --TASU--> car
```

It never talks to the car, never carries diagnostic traffic, and holds no
policy. It runs idle from boot and broadcasts nothing until the app turns
**WI-FI: OWN** on.

| File | |
|---|---|
| `beem-bridged.js` | the helper (Node 8 / ES5, runs on the head unit's own Node) |
| `beem-bridged.service` | systemd unit (starts at boot, restarts on any exit) |
| `install.sh` | `--probe` (changes nothing), install + verify, `--status` |
| `uninstall.sh` | clean shutdown and removal |

Control socket: `/run/beem-bridge/bridge.sock` (also `/dev/shm/beem-bridge/bridge.sock`),
newline-delimited JSON. Commands: `status`, `ap {up}`, `check`, `set-ssid`,
`set-pass`, `new-pass`. SSID and password are restricted to plain printable
characters before they reach hostapd. The passphrase is random from a
cryptographic source.

**Tested:** the control protocol, input validation and the whole bring-up
sequence are tested against stand-in `hostapd` / `iw` / `ip`: virtual interface,
`AP-ENABLED` detection, root-side reach check, crash detection, firewall add and
remove, and teardown. **Not yet run on an MGU:** whether its Wi-Fi chip allows a
second access point is hardware-specific. `install.sh --probe` answers that
before anything is changed.
