# Bimmhacks MGU Apps: Client Guide (iDrive 7 & 8)

Installs extra apps on BMWs with the **MGU head unit (iDrive 7)**: M Vitals, Redline, Service,
eDrive, Live Data, G-Force, Warm-Up, Virtual Dyno, Brake Test, Drag Timer, Fault Scanner, Doom
and more — and sets up an **on-car App Store** so you can add and update apps from the car screen
later. Everything the installer changes is backed up first, and one button puts the car back to stock.

The installer window has sections: **Root access → Apps → App Store → More apps → Bimmhacks apps → Credits.**
The **Car** panel at the top (VIN, head unit, root SSH key, IP address) is shared by all of them, and the
**Activity** log at the bottom shows what every button did.

> Flashing app and installer developed by **Mr. Wali**. Apps, root tool and Store are open source —
> see the **Credits** tab.

## Before you start

- **The PC connected to the car.** For root access this must be a **wired ENET cable** (a BMW
  ENET-to-Ethernet cable). For an already-rooted car you can also use the car's Wi-Fi hotspot.
- **Car parked, ignition on, a battery charger connected.** The screen reloads during installs and
  the head unit may reboot.
- **Windows: run as Administrator** for the root-access step (the launcher offers to relaunch
  elevated). This is needed so Windows uses the car's `169.254.x.x` ENET address.
- **Internet on the PC** the first time (Python/Node.js setup, Doom's game data). The **App Store**
  step also needs internet **at the car** (USB Ethernet adapter, LTE stick, tethered phone or Wi-Fi).

## Windows

1. Unzip `Bimmhacks-MGU-Installer-x.y.z.zip` (right-click → *Extract All*).
2. Double-click **`Install Bimmhacks Apps.cmd`**.
   - If Python or Node.js is missing, it offers to install them. Press **Y** and accept.
   - It offers to relaunch as Administrator — say **Y** for the root-access step.
   - If Windows SmartScreen warns, click *More info* → *Run anyway*.

### Root access (once per car)

Only needed if the car is **not already rooted**. Already have root SSH and the `id_rsa` key? Skip
to the **Apps** section and pick the key with *Browse* (the IP address is filled in from the key's
`connect.txt`).

> ⚠️ **Flashing reprograms the head unit and wipes its coding and BMW Digital Key pairings.**
> Back up the coding first, and plan to re-pair digital keys. This is a one-time step; do not repeat it.

**Backing up the coding — two things, both needed:**

1. **Coding snapshot (this installer).** Click **Back up coding**. It reads the car over
   ENET and saves, under `coding-backups/`, exactly which **CAFD coding versions**, VIN, IP and
   faults are on the car. This records *what to restore* — it is **not** a restorable backup by
   itself.
2. **Restorable backup (BimmerCode or E-Sys).** Also save the coding with BimmerCode or E-Sys —
   this is the only thing that can actually re-code the car. Then tick **"I have saved a restorable
   coding backup"**. **Flash** refuses, with a reminder, until you do.

Then:

1. Connect the ENET cable, wake the car.
2. Click **Detect VIN** (or type the 17-char VIN). Pick the **Head unit** (id7 for iDrive 7).
3. Take both backups above and tick the confirmation box.
4. Click **Flash + enable SSH** and confirm. The unit reboots; this takes a few minutes.
5. When it finishes, the car's SSH key **and** its IP address are filled into the Car panel
   automatically (the root tool reads the head unit's address from the car itself — the IP field is
   not used for this step).
   - **Enable (repair)**: re-opens SSH on an already-flashed car whose SSH stopped — no flashing.
   - **Fix refusing key**: only if SSH opens but login is refused (also needs the backup confirmation).
   - **Check state**: read-only; changes nothing.
   - **Disable**: closes root SSH again.

If coding is wiped, use the snapshot in `coding-backups/` to see which CAFD versions to restore,
then restore them with BimmerCode or E-Sys.

### Apps

1. Make sure the **Root SSH key** and **IP address** are set (filled in by Root access, or *Browse*
   to the key).
2. Click **Test connection** — it must say *Connection OK*.
3. Tick the apps you want and pick the language, then click **Install / Update apps**.
4. On the iDrive: **Apps** → the new tiles (after 1-2 minutes, or after the reboot).
   - **Status** shows what is installed; **Restore to stock** removes everything.

### App Store

Installs the on-car **MGU App Store**, so you browse, install and update apps from the car screen
afterwards (**Apps → Store**). The car needs internet for this (see above). Three options sit above the
button, all ticked by default:

- **Renderer** — the graphics part. It draws the Store and other apps with the head unit's graphics
  chip, so they are smooth and get the on-screen keyboard. Without it the Store falls back to simpler
  screens. Leave it ticked.
- **Internet uplink** — gives the car its own internet through a USB Ethernet adapter, an LTE stick,
  phone tethering (an Android phone on USB, or an iPhone's Personal Hotspot) or Wi-Fi, so the Store can
  download apps. Untick it only if you will install from a USB stick and never need the car online.
- **Restart the head unit when done** — the Store only starts after a restart (about two minutes: the
  screens go dark, and the installer waits until the car is back). Leave it ticked.

1. Park the car, ignition on, then click **Install the App Store** and confirm.
2. The installer installs the Store, restarts the head unit and waits for it to come back. Then open
   **Apps → Store** on the car.

Unticked the restart box? Click **Restart head unit** next to the install button when you are ready.
It asks the car to restart exactly as the Store's own *Restart head unit* does, and refuses while a
Store install is still running. (The Store's own button cannot help here: on a freshly installed Store
it is the thing that has not started yet.)

**RideTube (YouTube)** sits next to it: click **Make it available**. The installer makes sure the
Store is on the car and then **adds RideTube to the Store's sources for you**, so it is a normal
download under **Apps → Store** on the car. RideTube is an unofficial YouTube client for the MGU
(GPL-3, by JustAnotherPU): it plays on the car over the car's internet, shows the picture **only
while parked**, and drops to a sound-only podcast view while driving. It is not affiliated with
YouTube/Google. iDrive 7. If the Store has not started yet, the head unit is restarted once for you
(about two minutes) and the installer waits for the Store's updater before it adds RideTube.

> **First time on a car?** A Store that was installed a minute ago only starts its updater with the
> head unit. If the installer says *the Store's updater is not running yet*, restart the head unit
> (car **Settings**, or power-cycle), wait a minute, and click the button again.

### More apps

Two more Store sources, each with its own card. Both are **built-in sources of the Store**: the
Store lists them as soon as it is installed (the buttons only make sure the Store is on the car, or
stage the apps to a USB stick for a car without internet).

- **Cluster & HUD maps (idrive-apps)** — Robert Nisipeanu's Waze / CarPlay map on the instrument
  cluster and HUD. Online car: click **Make it available**, then on the car open **Apps → Store**
  and install *Waze on cluster*. Offline car: set a **Stage to** folder (a USB stick), click the
  button to download the apps onto it, then on the car use **Apps → Store → USB**.
- **IterioDev car-apps (iDrive 8)** — Car Data, Drag Timer, Drive Recorder, eDrive, Fault Codes,
  Live Adjustments, Overlays, Service, Tailscale. Most are **iDrive 8 only** (Drive Recorder also
  runs on iDrive 7). Set **Head unit** to `id8-…` for an iDrive 8 car. Online or staged to USB the
  same way. Licence: PolyForm Strict 1.0.0 (IterioDev); nothing from it is copied into this package.

### Bimmhacks Tuner (Apps section, experimental)

Our own tuner companion, in the **Apps** section (unticked by default; it asks for confirmation). On the car:

- **Gauges** — engine speed, boost, load, ignition timing, coolant, oil, intake air and torque, with
  session peaks and the last minute of boost.
- **PIDs** — every value that is switched on, live, with whether your car answers it. *More PIDs* pages
  through them.
- **Datalog** — *Start log* / *Stop log* records the drive to a CSV on the head unit (newest 20 are
  kept). It will not log the demo drive: with no live car data it says so. In **Settings → Log pack**
  switch on what your car has: **Tuning** (lambda, knock, ignition, trims, rail pressure …, on by
  default), **Engine**, **Cylinders**, **MHD capture** (eight identifiers logged as raw hex) and
  **Custom** (your own `custom_pids.json`; see the app's README for the format). The Datalog page shows
  how many values of each pack your car is actually answering.
- **Faults** — the engine unit's fault codes. *Clear* needs two presses.
- **Maps** — map 1–4 and Max Cooling **for a car that already runs an MHD multi-map tune**. It ships
  **off** (*Settings → Map switching*); off means nothing is ever sent.

**It never flashes or writes a tune.** Flashing the head unit (root access) is a separate, deliberate step that
needs a coding backup first. The tuner is new and has not been tested on a real car yet — tell the
developer what your car does. Values the car does not answer show as `--` (and blank in the log).

### Bimmhacks apps

Apps developed in-house (Mr. Wali). A starter app, **Bimmhacks Hello**, already appears in the
**Apps** section — install it to see a Bimmhacks app on the car, or copy it to build your own. To
distribute your apps through the Store, publish them to your GitHub repo (default
`waliworld/Bimmhacks`) and use this section. The **How to make an app** button opens the full guide
(`docs/MAKE-YOUR-OWN-APP.md`): building an app, previewing it, and adding it to the App Store.

**Analyse a capture** (the second card) answers "which identifiers does a logging tool read from
*my* car?". Record the ENET link with Wireshark while a logger (MHD, BM3, ISTA …) runs on your own
car, then pick the `.pcap` / `.pcapng` here. You get a report of every identifier it asked the car
for — which control unit, how often, what the car answered — plus a ready `custom_pids.json` for
Bimmhacks Tuner that logs the same identifiers raw. The root tool's own session logs work too. It
reads the capture only; nothing is sent to the car. (Same thing without a window:
`python3 installer/pid_capture.py drive.pcapng`.)

## Phone instead of a laptop: Bimmhacks Pocket

If you were given a **Pocket box** (a small computer with an ENET cable), you need no laptop at all:

1. Plug the cable into the car's OBD port, power the box from the car's USB port, ignition on.
2. On your phone, join the Wi-Fi named on the box (e.g. **Bimmhacks-Pocket**) and open
   **http://10.42.0.1:8080/** (Safari: *Share → Add to Home Screen* keeps it as an icon).
3. The page has the same three steps: **Root access** (with the same backup confirmation before a
   flash), **App Store**, and optionally the apps. The activity log at the bottom shows what the box
   is doing; it keeps working if the phone's screen goes off.
4. When root access finishes, press **Download key bundle** to keep a copy of the car's SSH key on the
   phone; coding snapshots can be downloaded from their list.

Building such a box from a Raspberry Pi is described in `docs/POCKET.md`.

## macOS / Linux

Install Python 3 and Node.js (macOS: `brew install python node python-tk`; Ubuntu:
`sudo apt install python3 python3-tk nodejs openssh-client`), then double-click
**`Install Bimmhacks Apps.command`** (macOS) or run `./install.sh` (Linux).

Without a window (any OS):

```sh
# root access (ENET cable; once per car)
python3 installer/bimmhacks_installer.py root-status                 # read-only check
python3 installer/bimmhacks_installer.py backup-coding --out coding-backups   # coding snapshot
python3 installer/bimmhacks_installer.py root-flash --vin <17-CHAR-VIN> --confirm-backup   # flash + enable SSH
python3 installer/bimmhacks_installer.py root-enable                 # repair, no flashing

# apps
python3 installer/bimmhacks_installer.py install --key <id_rsa> --ip 169.254.36.47
python3 installer/bimmhacks_installer.py install --key <id_rsa> --language ko redline service
python3 installer/bimmhacks_installer.py status  --key <id_rsa>
python3 installer/bimmhacks_installer.py restore --key <id_rsa>

# app store + more sources
python3 installer/bimmhacks_installer.py store-install --key <id_rsa> --restart     # --restart: restart the head unit when done
python3 installer/bimmhacks_installer.py restart-head-unit --key <id_rsa>             # the Store only starts after a restart
python3 installer/bimmhacks_installer.py install-idrive-apps --key <id_rsa>
python3 installer/bimmhacks_installer.py install-car-apps --key <id_rsa>
python3 installer/bimmhacks_installer.py install-ridetube --key <id_rsa>   # YouTube client (unofficial)
python3 installer/bimmhacks_installer.py install-our-apps --key <id_rsa> --repo owner/repo
python3 installer/bimmhacks_installer.py usb-repo --stage /path/to/usb --repo robertnisipeanu/idrive-apps

# which identifiers a logger read from your car (from a Wireshark capture of the ENET link)
python3 installer/pid_capture.py drive.pcapng          # -> drive.pids.json + drive.custom_pids.json
```

The root key is written to `tools/idrive7-8-ssh-root/data/enrollments/<VIN>/id_rsa`.

## Updating

Run the installer again with the new version and click **Install / Update apps**, or use the on-car
App Store. A new backup is made each time; the first (stock) backup is always kept.

**After a BMW firmware update** the apps are hidden (the car runs stock). Run **Install / Update
apps** again to bring them back.

## Good to know

- **Engine Config** and **Transmission Config** are experimental and off by default. Engine Config
  can send control requests to the engine (DME) once a tune is selected in its settings.
- Driving apps show a **DEMO** badge when there is no live data from the car.
- If an app crashes repeatedly, the car removes it by itself. Run the install again.

## Troubleshooting

| Message | What to do |
|---|---|
| Root access can't reach the car | Use the **ENET cable** (not Wi-Fi), ignition on. Windows: run as Administrator. |
| *A valid 17-character VIN is needed* | Click **Detect VIN**, or type the VIN from the windshield. |
| *MainApp … a version the patch does not know* | This firmware isn't supported yet. Nothing was changed. Contact us. |
| *Could not log in to the head unit* | Do **Root access** first, or *Browse* to the right `id_rsa`. Check the IP and that the car is awake. |
| App Store step fails to download | The **car** needs internet (USB Ethernet / LTE / tethered phone / Wi-Fi). |
| *'cryptography' could not be installed* | Run `python -m pip install --user cryptography`, then retry. |
| *Node.js / OpenSSH missing* | Install Node LTS from nodejs.org; Windows OpenSSH: Settings → Apps → Optional features. |
| The Store opens on the car but asks you to restart the MGU, and cannot | A fresh Store only starts after a restart, and its own restart button needs it running. In the installer open **App Store** and click **Restart head unit** (about two minutes). Still no luck: switch the ignition off and on, or hold the volume knob for about 30 seconds until the screens restart. |
| *An install is still running on the head unit* | The restart was refused on purpose so it cannot cut an install short. Wait a minute or two and try again. |
| *The head unit did not answer on SSH again* | Give it a few more minutes. If it is up on the car but SSH stays closed, use **Root access → Enable (repair)** (no flashing). |
| *Building the apps failed* with `Continue anyway? [y/N]` and `EOFError` in the log | Fixed in 1.8.2 (Windows made the build ask a question nobody could answer). Update the installer. The two `!!` lines about the renderer being older than its source are normal on a PC without Docker: the kit ships the prebuilt renderer. |

## Credits

The apps are **MGU Custom Apps** by cauliflowermossgrape, MIT licence, free original at
<https://github.com/cauliflowermossgrape/idrive7-apps>. Root access uses
[idrive7-8-ssh-root](https://github.com/cauliflowermossgrape/idrive7-8-ssh-root) (public domain).
The App Store is [mgu-store](https://github.com/robertnisipeanu/mgu-store) by Robert Nisipeanu and
IterioDev (GPL-3.0). Doom: doomgeneric (GPL-2.0) with Freedoom game data. BMW's app SDK is never
included: the installer reads it from your own car and deletes it from the PC afterwards.
